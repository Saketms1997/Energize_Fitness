-- Energize Fitness: cloud sync core (Supabase / PostgreSQL 15+)
--
-- Model: the device is the source of truth while offline. Every device pushes
-- its outbox operations; the server stores the latest version of each record
-- as JSON in `sync_records` and hands out a monotonically increasing
-- `server_seq` so other devices can pull "everything after N".
--
-- Security: row level security is ON for every table. A signed-in user may only
-- touch gyms listed in `gym_access`. Password hashes are never synced
-- (gym_owners is not in the allow-list).


-- ---------------------------------------------------------------- access

create table if not exists public.gym_access (
  user_id    uuid not null references auth.users (id) on delete cascade,
  gym_id     uuid not null,
  role       text not null default 'owner' check (role in ('owner')),
  created_at timestamptz not null default now(),
  primary key (user_id, gym_id)
);
-- A gym has exactly one owner account.
create unique index if not exists gym_access_one_owner
  on public.gym_access (gym_id);

alter table public.gym_access enable row level security;

drop policy if exists gym_access_select_own on public.gym_access;
create policy gym_access_select_own on public.gym_access
  for select to authenticated using (user_id = auth.uid());
-- No insert/update/delete policies: rows are created only by claim_gym().

create or replace function public.has_gym_access(p_gym uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.gym_access
    where gym_id = p_gym and user_id = auth.uid()
  );
$$;

-- First signed-in user to present a (random, unguessable) gym id becomes its
-- owner. Idempotent for the same user; refused for anyone else.
create or replace function public.claim_gym(p_gym uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  existing uuid;
begin
  if auth.uid() is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  select user_id into existing from public.gym_access where gym_id = p_gym;
  if existing is null then
    insert into public.gym_access (user_id, gym_id) values (auth.uid(), p_gym);
  elsif existing <> auth.uid() then
    raise exception 'gym already claimed' using errcode = '42501';
  end if;
end;
$$;

-- ---------------------------------------------------------------- records

create sequence if not exists public.sync_seq;

create table if not exists public.sync_records (
  gym_id       uuid   not null,
  entity_table text   not null check (entity_table in (
                 'gyms', 'membership_plans', 'members', 'memberships',
                 'payments', 'membership_freezes', 'trainers',
                 'attendance_records', 'whatsapp_messages')),
  entity_id    uuid   not null,
  version      int    not null check (version >= 1),
  deleted      boolean not null default false,
  payload      jsonb  not null,
  server_seq   bigint not null default nextval('public.sync_seq'),
  updated_at   timestamptz not null default now(),
  primary key (gym_id, entity_table, entity_id)
);
create index if not exists sync_records_pull
  on public.sync_records (gym_id, server_seq);

alter table public.sync_records enable row level security;

drop policy if exists sync_records_select on public.sync_records;
create policy sync_records_select on public.sync_records
  for select to authenticated using (public.has_gym_access(gym_id));
-- Writes only through apply_sync_ops() so version rules cannot be bypassed.

-- Idempotency ledger: a retried operation returns its original outcome.
create table if not exists public.sync_applied (
  gym_id          uuid not null,
  idempotency_key text not null,
  outcome         text not null check (outcome in ('applied', 'conflict')),
  applied_at      timestamptz not null default now(),
  primary key (gym_id, idempotency_key)
);
alter table public.sync_applied enable row level security;
-- no policies: only security-definer functions touch it.

-- ---------------------------------------------------------------- apply

-- p_ops: [{idempotency_key, entity_table, entity_id, operation, payload}]
-- Returns one row per op: {idempotency_key, outcome, server_version}
--   applied  : stored (or already stored identically)
--   conflict : server holds an equal-or-newer, different version; the client
--              must pull and reconcile. Nothing is overwritten.
create or replace function public.apply_sync_ops(p_gym uuid, p_ops jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  op        jsonb;
  results   jsonb := '[]'::jsonb;
  key       text;
  tbl       text;
  eid       uuid;
  incoming  int;
  pay       jsonb;
  is_delete boolean;
  cur       public.sync_records%rowtype;
  prior     text;
  res       text;
begin
  if not public.has_gym_access(p_gym) then
    raise exception 'no access to gym' using errcode = '42501';
  end if;
  if jsonb_typeof(p_ops) <> 'array' or jsonb_array_length(p_ops) > 200 then
    raise exception 'ops must be an array of at most 200' using errcode = '22023';
  end if;

  for op in select * from jsonb_array_elements(p_ops) loop
    key := op ->> 'idempotency_key';
    tbl := op ->> 'entity_table';
    eid := (op ->> 'entity_id')::uuid;
    pay := op -> 'payload';
    is_delete := (op ->> 'operation') = 'delete';
    incoming := coalesce((pay ->> 'version')::int, 0);

    if key is null or tbl is null or pay is null or incoming < 1 then
      raise exception 'malformed operation' using errcode = '22023';
    end if;
    -- a client may only write rows that belong to the gym it claims
    if tbl = 'gyms' then
      if eid <> p_gym then
        raise exception 'gym id mismatch' using errcode = '42501';
      end if;
    elsif (pay ->> 'gymId')::uuid is distinct from p_gym then
      raise exception 'row belongs to another gym' using errcode = '42501';
    end if;

    select outcome into prior from public.sync_applied
      where gym_id = p_gym and idempotency_key = key;
    if prior is not null then
      results := results || jsonb_build_object(
        'idempotency_key', key, 'outcome', prior, 'server_version', null);
      continue;
    end if;

    select * into cur from public.sync_records
      where gym_id = p_gym and entity_table = tbl and entity_id = eid
      for update;

    if not found then
      insert into public.sync_records
        (gym_id, entity_table, entity_id, version, deleted, payload)
      values (p_gym, tbl, eid, incoming, is_delete, pay);
      res := 'applied';
    elsif incoming > cur.version then
      update public.sync_records
         set version = incoming, deleted = is_delete, payload = pay,
             server_seq = nextval('public.sync_seq'), updated_at = now()
       where gym_id = p_gym and entity_table = tbl and entity_id = eid;
      res := 'applied';
    elsif incoming = cur.version and cur.payload = pay then
      res := 'applied';            -- identical replay
    else
      res := 'conflict';
    end if;

    insert into public.sync_applied (gym_id, idempotency_key, outcome)
      values (p_gym, key, res)
      on conflict do nothing;
    results := results || jsonb_build_object(
      'idempotency_key', key, 'outcome', res,
      'server_version', coalesce(cur.version, incoming));
  end loop;
  return results;
end;
$$;

revoke all on function public.apply_sync_ops(uuid, jsonb) from public;
revoke all on function public.claim_gym(uuid) from public;
revoke all on function public.has_gym_access(uuid) from public;
grant execute on function public.apply_sync_ops(uuid, jsonb) to authenticated;
grant execute on function public.claim_gym(uuid) to authenticated;
grant execute on function public.has_gym_access(uuid) to authenticated;

-- ---------------------------------------------------------------- whatsapp

-- Called by the whatsapp-webhook edge function (service role) when Meta
-- reports a delivery status. Only moves a message forward, never backward, and
-- bumps version/server_seq so devices pick the change up on their next pull.
create or replace function public.apply_whatsapp_status(
  p_provider_id text, p_status text, p_error text default null)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  cur_rank int;
  new_rank int;
  rec public.sync_records%rowtype;
begin
  new_rank := case p_status
    when 'sent' then 1 when 'delivered' then 2 when 'read' then 3
    when 'failed' then 4 else null end;
  if new_rank is null then return false; end if;
  select * into rec from public.sync_records
    where entity_table = 'whatsapp_messages'
      and payload ->> 'providerMessageId' = p_provider_id
    for update;
  if not found then return false; end if;
  cur_rank := case rec.payload ->> 'status'
    when 'queued' then 0 when 'sent' then 1 when 'delivered' then 2
    when 'read' then 3 when 'failed' then 1 else 0 end;
  -- failed may follow sent/queued; delivered/read never regress
  if p_status <> 'failed' and new_rank <= cur_rank then return false; end if;
  if p_status = 'failed' and (rec.payload ->> 'status') in ('delivered','read') then
    return false;
  end if;
  update public.sync_records
     set version = rec.version + 1,
         payload = rec.payload || jsonb_build_object(
           'status', p_status, 'version', rec.version + 1,
           'errorMessage', p_error),
         server_seq = nextval('public.sync_seq'), updated_at = now()
   where gym_id = rec.gym_id and entity_table = rec.entity_table
     and entity_id = rec.entity_id;
  return true;
end;
$$;
revoke all on function public.apply_whatsapp_status(text, text, text) from public;
revoke all on function public.apply_whatsapp_status(text, text, text) from anon, authenticated;

-- Defence in depth: Supabase grants broad default privileges on public tables.
-- Clients may only read; every write goes through the functions above.
revoke all on public.sync_records, public.sync_applied, public.gym_access
  from anon, authenticated;
grant select on public.sync_records, public.gym_access to authenticated;
