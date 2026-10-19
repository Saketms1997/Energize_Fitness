# WhatsApp message templates

Register each template in Meta Business Manager → WhatsApp Manager → Message templates.
Category **Utility**, language **English (en)**, body text **exactly** as below (placeholders `{{1}}`… are filled in by the app in this order). Header, footer and buttons: none.

| Template name | Body | Parameters |
|---|---|---|
| `energize_welcome` | Hi {{1}}, welcome to {{2}}! Your member ID is {{3}}. We are glad to have you. | name, gym, member ID |
| `energize_payment_received` | Hi {{1}}, we received your payment of {{2}} at {{3}}. Your {{4}} membership is valid until {{5}}. Thank you! | name, amount, gym, plan, end date |
| `energize_expiry_reminder` | Hi {{1}}, your {{2}} membership at {{3}} ends on {{4}}. Renew soon to keep training without a break. | name, plan, gym, end date |
| `energize_membership_expired` | Hi {{1}}, your {{2}} membership at {{3}} ended on {{4}}. Visit us or reply here to renew. | name, plan, gym, end date |
| `energize_payment_due` | Hi {{1}}, a balance of {{2}} is pending for your {{3}} membership at {{4}}. Please complete the payment to start it. | name, balance, plan, gym |

The text lives in `lib/services/message_templates.dart`; if you change a sentence there, register a new template version with the same text, or the cloud send will be rejected.

Consent: the app only queues messages for members whose *WhatsApp consent* box was ticked at registration (the time consent was given is recorded).
