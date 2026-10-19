// node --experimental-strip-types whatsapp_logic.test.mjs
import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import {
  TEMPLATES, buildTemplatePayload, parseStatuses, validateSendRequest, verifySignature,
} from '../functions/_shared/whatsapp_logic.ts';

const good = {
  gymId: '11111111-1111-4111-8111-111111111111',
  messageId: '22222222-2222-4222-8222-222222222222',
  to: '919876543210',
  template: 'energize_welcome',
  params: ['Ravi', 'Iron Temple', 'M0001'],
};
assert.equal(validateSendRequest(good), null);
for (const [name, patch] of Object.entries({
  badGym: { gymId: 'x' },
  landline: { to: '911234567890' },
  noCountryCode: { to: '9876543210' },
  plusSign: { to: '+919876543210' },
  unknownTemplate: { template: 'free_text' },
  noParams: { params: [] },
  tooMany: { params: Array(9).fill('a') },
  newline: { params: ['a\nb', 'c', 'd'] },
  nonString: { params: [1, 2, 3] },
  longParam: { params: ['a'.repeat(201), 'b', 'c'] },
})) {
  assert.notEqual(validateSendRequest({ ...good, ...patch }), null, name);
}
assert.notEqual(validateSendRequest(null), null);
assert.notEqual(validateSendRequest('x'), null);
assert.equal(TEMPLATES.length, 5);

const p = buildTemplatePayload(good);
assert.equal(p.type, 'template');
assert.equal(p.template.name, 'energize_welcome');
assert.deepEqual(p.template.components[0].parameters, [
  { type: 'text', text: 'Ravi' }, { type: 'text', text: 'Iron Temple' }, { type: 'text', text: 'M0001' },
]);

const secret = 's3cret';
const body = JSON.stringify({ hello: 'world' });
const sig = 'sha256=' + createHmac('sha256', secret).update(body).digest('hex');
assert.equal(await verifySignature(body, sig, secret), true);
assert.equal(await verifySignature(body + ' ', sig, secret), false, 'tampered body');
assert.equal(await verifySignature(body, sig, 'other'), false, 'wrong secret');
assert.equal(await verifySignature(body, null, secret), false);
assert.equal(await verifySignature(body, 'sha256=abc', secret), false);
assert.equal(await verifySignature(body, sig.replace('sha256=', ''), secret), false);
assert.equal(await verifySignature(body, sig, ''), false, 'unset secret never validates');

const hook = {
  entry: [{ changes: [{ value: { statuses: [
    { id: 'wamid.A', status: 'delivered' },
    { id: 'wamid.B', status: 'failed', errors: [{ title: 'Not on WhatsApp' }] },
    { id: 'wamid.C', status: 'weird' },
    { status: 'sent' },
  ] } }] }],
};
assert.deepEqual(parseStatuses(hook), [
  { providerMessageId: 'wamid.A', status: 'delivered', error: undefined },
  { providerMessageId: 'wamid.B', status: 'failed', error: 'Not on WhatsApp' },
]);
assert.deepEqual(parseStatuses({}), []);
assert.deepEqual(parseStatuses(null), []);

console.log('whatsapp_logic: all assertions passed');
