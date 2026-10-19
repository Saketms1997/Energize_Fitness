// Runs 0001_sync_core.sql against an in-process PostgreSQL (PGlite) with a
// minimal stand-in for Supabase's auth schema.
//   cd backend/tests && npm i @electric-sql/pglite && node sync_core.test.mjs
import { PGlite } from '@electric-sql/pglite';
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const db = new PGlite();
const GYM = '11111111-1111-4111-8111-111111111111';
const OTHER_GYM = '22222222-2222-4222-8222-222222222222';
const ALICE = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const BOB = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';

await db.exec(`
  create schema auth;
  create table auth.users (id uuid primary key);
  create function auth.uid() returns uuid language sql stable as
    $$ select nullif(current_setting('request.jwt.uid', true), '')::uuid $$;
  create role authenticated; create role anon; grant usage on schema public to authenticated; grant usage on schema auth to authenticated;
  insert into auth.users values ('${ALICE}'), ('${BOB}');
`);
await db.exec(readFileSync(new URL('../migrations/0001_sync_core.sql', import.meta.url), 'utf8'));

const as = async (uid, sql, params) => {
  await db.exec(`set request.jwt.uid = '${uid ?? ''}'; set role authenticated;`);
  try {
    return await db.query(sql, params);
  } finally {
    await db.exec('reset role;');
  }
};
const fails = async (uid, sql, params, code) => {
  await assert.rejects(() => as(uid, sql, params), (e) => {
    if (code) assert.equal(e.code, code, e.message);
    return true;
  });
};

const member = (id, version, extra = {}) => ({
  id, gymId: GYM, version, fullName: 'Ravi', ...extra,
});
const op = (key, table, id, payload, operation = 'update') => ({
  idempotency_key: key, entity_table: table, entity_id: id, operation, payload,
});
const apply = async (uid, gym, ops) =>
  (await as(uid, 'select public.apply_sync_ops($1, $2::jsonb) as r', [gym, JSON.stringify(ops)])).rows[0].r;

const M1 = '33333333-3333-4333-8333-333333333333';

// claiming
await as(ALICE, 'select public.claim_gym($1)', [GYM]);
await as(ALICE, 'select public.claim_gym($1)', [GYM]); // idempotent
await fails(BOB, 'select public.claim_gym($1)', [GYM], '42501');
await fails(null, 'select public.claim_gym($1)', [GYM], '28000');

// access control
await fails(BOB, 'select public.apply_sync_ops($1, $2::jsonb)', [GYM, '[]'], '42501');

// insert, replay, newer, stale
let r = await apply(ALICE, GYM, [op('k1', 'members', M1, member(M1, 1), 'insert')]);
assert.equal(r[0].outcome, 'applied');
r = await apply(ALICE, GYM, [op('k1', 'members', M1, member(M1, 1), 'insert')]);
assert.equal(r[0].outcome, 'applied', 'retry with same key returns original outcome');
r = await apply(ALICE, GYM, [op('k2', 'members', M1, member(M1, 2, { fullName: 'Ravi K' }))]);
assert.equal(r[0].outcome, 'applied');
r = await apply(ALICE, GYM, [op('k3', 'members', M1, member(M1, 2, { fullName: 'Other' }))]);
assert.equal(r[0].outcome, 'conflict');
r = await apply(ALICE, GYM, [op('k4', 'members', M1, member(M1, 1, { fullName: 'Old' }))]);
assert.equal(r[0].outcome, 'conflict');
let row = (await as(ALICE, 'select * from public.sync_records where entity_id = $1', [M1])).rows[0];
assert.equal(row.version, 2);
assert.equal(row.payload.fullName, 'Ravi K', 'conflicting write must not overwrite');

// identical replay under a new key is not a conflict
r = await apply(ALICE, GYM, [op('k5', 'members', M1, member(M1, 2, { fullName: 'Ravi K' }))]);
assert.equal(r[0].outcome, 'applied');

// tombstone
r = await apply(ALICE, GYM, [op('k6', 'members', M1, member(M1, 3), 'delete')]);
row = (await as(ALICE, 'select * from public.sync_records where entity_id = $1', [M1])).rows[0];
assert.equal(row.deleted, true);

// pull ordering: server_seq increases on every accepted change
const seqs = (await as(ALICE, 'select server_seq from public.sync_records')).rows;
assert.equal(seqs.length, 1);

// cross-gym and table allow-list protections
await fails(ALICE, 'select public.apply_sync_ops($1, $2::jsonb)',
  [GYM, JSON.stringify([op('x1', 'members', M1, { ...member(M1, 9), gymId: OTHER_GYM })])], '42501');
await fails(ALICE, 'select public.apply_sync_ops($1, $2::jsonb)',
  [GYM, JSON.stringify([op('x2', 'gym_owners', M1, member(M1, 1))])], '23514');
await fails(ALICE, 'select public.apply_sync_ops($1, $2::jsonb)',
  [GYM, JSON.stringify([op('x3', 'members', M1, { gymId: GYM })])], '22023');

// RLS: direct writes are impossible, reads are scoped
await fails(ALICE, `insert into public.sync_records (gym_id, entity_table, entity_id, version, payload)
  values ($1, 'members', $2, 1, '{}')`, [GYM, M1]);
assert.equal((await as(BOB, 'select * from public.sync_records')).rows.length, 0);
assert.equal((await as(ALICE, 'select * from public.sync_records')).rows.length, 1);
await fails(ALICE, 'update public.gym_access set user_id = $1', [BOB]);

// WhatsApp status webhook function: forward-only, service role only
const W1 = '44444444-4444-4444-8444-444444444444';
await apply(ALICE, GYM, [op('w1', 'whatsapp_messages', W1,
  { id: W1, gymId: GYM, version: 1, status: 'sent', providerMessageId: 'wamid.X' }, 'insert')]);
await fails(ALICE, "select public.apply_whatsapp_status('wamid.X','delivered')", [], '42501');
assert.equal((await db.query("select public.apply_whatsapp_status('wamid.X','delivered') as r")).rows[0].r, true);
assert.equal((await db.query("select public.apply_whatsapp_status('wamid.X','sent') as r")).rows[0].r, false, 'no regression');
assert.equal((await db.query("select public.apply_whatsapp_status('wamid.X','failed') as r")).rows[0].r, false, 'delivered never becomes failed');
assert.equal((await db.query("select public.apply_whatsapp_status('wamid.NOPE','read') as r")).rows[0].r, false);
row = (await as(ALICE, 'select * from public.sync_records where entity_id = $1', [W1])).rows[0];
assert.equal(row.payload.status, 'delivered');
assert.equal(row.version, 2);
// a device holding version 1 now conflicts instead of clobbering the webhook's update
r = await apply(ALICE, GYM, [op('w2', 'whatsapp_messages', W1,
  { id: W1, gymId: GYM, version: 2, status: 'sent', providerMessageId: 'wamid.X' })]);
assert.equal(r[0].outcome, 'conflict');

console.log('sync_core: all assertions passed');
