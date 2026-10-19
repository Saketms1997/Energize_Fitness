// Pure helpers shared by the WhatsApp edge functions. No Deno/Node APIs other
// than Web Crypto, so the same file is unit-tested under Node.

export const TEMPLATES = [
  'energize_welcome',
  'energize_payment_received',
  'energize_expiry_reminder',
  'energize_membership_expired',
  'energize_payment_due',
] as const;

export interface SendRequest {
  gymId: string;
  messageId: string;
  to: string;
  template: string;
  params: string[];
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Returns an error string, or null when the request is acceptable. */
export function validateSendRequest(b: unknown): string | null {
  if (typeof b !== 'object' || b === null) return 'Body must be an object';
  const r = b as Record<string, unknown>;
  if (typeof r.gymId !== 'string' || !UUID.test(r.gymId)) return 'Invalid gymId';
  if (typeof r.messageId !== 'string' || !UUID.test(r.messageId)) return 'Invalid messageId';
  // Indian mobile numbers only: 91 + 10 digits starting 6-9
  if (typeof r.to !== 'string' || !/^91[6-9]\d{9}$/.test(r.to)) return 'Invalid recipient';
  if (typeof r.template !== 'string' || !(TEMPLATES as readonly string[]).includes(r.template)) {
    return 'Unknown template';
  }
  if (!Array.isArray(r.params) || r.params.length < 1 || r.params.length > 8) {
    return 'Invalid params';
  }
  for (const p of r.params) {
    if (typeof p !== 'string' || p.length === 0 || p.length > 200 || /[\r\n\t]/.test(p)) {
      return 'Invalid param value';
    }
  }
  return null;
}

/** WhatsApp Cloud API request body for an approved template message. */
export function buildTemplatePayload(req: SendRequest, language = 'en') {
  return {
    messaging_product: 'whatsapp',
    recipient_type: 'individual',
    to: req.to,
    type: 'template',
    template: {
      name: req.template,
      language: { code: language },
      components: [
        {
          type: 'body',
          parameters: req.params.map((text) => ({ type: 'text', text })),
        },
      ],
    },
  };
}

const enc = new TextEncoder();

function hex(buf: ArrayBuffer): string {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/** Verifies Meta's `X-Hub-Signature-256: sha256=<hmac of raw body>` header. */
export async function verifySignature(
  rawBody: string,
  header: string | null,
  appSecret: string,
): Promise<boolean> {
  if (!header || !appSecret || !header.startsWith('sha256=')) return false;
  const key = await crypto.subtle.importKey(
    'raw',
    enc.encode(appSecret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const sig = hex(await crypto.subtle.sign('HMAC', key, enc.encode(rawBody)));
  return timingSafeEqual(sig, header.slice('sha256='.length).toLowerCase());
}

export interface StatusUpdate {
  providerMessageId: string;
  status: 'sent' | 'delivered' | 'read' | 'failed';
  error?: string;
}

/** Extracts delivery statuses from a webhook notification, ignoring junk. */
export function parseStatuses(payload: unknown): StatusUpdate[] {
  const out: StatusUpdate[] = [];
  // deno-lint-ignore no-explicit-any
  const entries = (payload as any)?.entry;
  if (!Array.isArray(entries)) return out;
  for (const e of entries) {
    for (const c of e?.changes ?? []) {
      for (const s of c?.value?.statuses ?? []) {
        if (typeof s?.id !== 'string') continue;
        if (!['sent', 'delivered', 'read', 'failed'].includes(s.status)) continue;
        const error =
          s.status === 'failed'
            ? String(s.errors?.[0]?.title ?? s.errors?.[0]?.message ?? 'Delivery failed').slice(0, 300)
            : undefined;
        out.push({ providerMessageId: s.id, status: s.status, error });
      }
    }
  }
  return out;
}
