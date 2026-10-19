// Supabase Edge Function: receives WhatsApp delivery receipts from Meta.
// Deploy with `--no-verify-jwt` (Meta cannot send a Supabase JWT); requests
// are authenticated by the HMAC signature instead.
//
// Secrets: WHATSAPP_WEBHOOK_VERIFY_TOKEN, WHATSAPP_APP_SECRET,
//          SUPABASE_SERVICE_ROLE_KEY (platform-provided)
import { createClient } from 'npm:@supabase/supabase-js@2';
import { parseStatuses, verifySignature } from '../_shared/whatsapp_logic.ts';

Deno.serve(async (req) => {
  const url = new URL(req.url);

  // Meta's one-time subscription handshake.
  if (req.method === 'GET') {
    const ok =
      url.searchParams.get('hub.mode') === 'subscribe' &&
      url.searchParams.get('hub.verify_token') === Deno.env.get('WHATSAPP_WEBHOOK_VERIFY_TOKEN');
    return ok
      ? new Response(url.searchParams.get('hub.challenge') ?? '', { status: 200 })
      : new Response('Forbidden', { status: 403 });
  }
  if (req.method !== 'POST') return new Response('Method not allowed', { status: 405 });

  const raw = await req.text();
  const valid = await verifySignature(
    raw,
    req.headers.get('X-Hub-Signature-256'),
    Deno.env.get('WHATSAPP_APP_SECRET') ?? '',
  );
  if (!valid) return new Response('Invalid signature', { status: 401 });

  let payload: unknown;
  try {
    payload = JSON.parse(raw);
  } catch {
    return new Response('Bad JSON', { status: 400 });
  }

  const supabase = createClient(
    Deno.env.get('SUPABASE_URL')!,
    Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!,
  );
  for (const s of parseStatuses(payload)) {
    const { error } = await supabase.rpc('apply_whatsapp_status', {
      p_provider_id: s.providerMessageId,
      p_status: s.status,
      p_error: s.error ?? null,
    });
    // A database error should make Meta retry; unknown message ids just return false.
    if (error) return new Response('Retry', { status: 500 });
  }
  return new Response('ok', { status: 200 });
});
