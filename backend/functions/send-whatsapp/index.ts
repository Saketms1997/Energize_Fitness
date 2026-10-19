// Supabase Edge Function: send one WhatsApp template message.
//
// Secrets (set with `supabase secrets set`, never in the app):
//   WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID
// SUPABASE_URL / SUPABASE_ANON_KEY are provided by the platform.
import { createClient } from 'npm:@supabase/supabase-js@2';
import { buildTemplatePayload, validateSendRequest, type SendRequest } from '../_shared/whatsapp_logic.ts';

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

Deno.serve(async (req) => {
  if (req.method !== 'POST') return json(405, { error: 'Method not allowed' });

  const auth = req.headers.get('Authorization') ?? '';
  const supabase = createClient(
    Deno.env.get('SUPABASE_URL')!,
    Deno.env.get('SUPABASE_ANON_KEY')!,
    { global: { headers: { Authorization: auth } } },
  );
  const { data: user, error: authError } = await supabase.auth.getUser();
  if (authError || !user?.user) return json(401, { error: 'Not signed in' });

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return json(400, { error: 'Invalid JSON' });
  }
  const problem = validateSendRequest(body);
  if (problem) return json(400, { error: problem });
  const request = body as SendRequest;

  // The caller must own the gym the message belongs to.
  const { data: allowed, error: accessError } = await supabase.rpc('has_gym_access', {
    p_gym: request.gymId,
  });
  if (accessError || allowed !== true) return json(403, { error: 'No access to this gym' });

  const token = Deno.env.get('WHATSAPP_ACCESS_TOKEN');
  const phoneId = Deno.env.get('WHATSAPP_PHONE_NUMBER_ID');
  if (!token || !phoneId) return json(503, { error: 'WhatsApp is not configured' });

  const res = await fetch(`https://graph.facebook.com/v21.0/${phoneId}/messages`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(buildTemplatePayload(request)),
  });
  const result = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message = String(result?.error?.message ?? `WhatsApp error ${res.status}`).slice(0, 300);
    // Pass 5xx/429 through so the app retries; everything else is permanent.
    return json(res.status >= 500 || res.status === 429 ? 502 : 422, { error: message });
  }
  const id = result?.messages?.[0]?.id;
  if (typeof id !== 'string') return json(502, { error: 'Unexpected WhatsApp response' });
  return json(200, { providerMessageId: id });
});
