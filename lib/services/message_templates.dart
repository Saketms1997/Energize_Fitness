import 'dart:convert';

import '../core/money.dart';
import '../shared/formatters.dart';

/// Messages the app can send. Each has ONE text with {{n}} placeholders: it is
/// both the local preview/wa.me text and the body that must be registered
/// (identically) as a WhatsApp Cloud API template named [cloudName].
enum MessageTemplate {
  welcome(
    'energize_welcome',
    'Hi {{1}}, welcome to {{2}}! Your member ID is {{3}}. We are glad to have you.',
  ),
  paymentReceived(
    'energize_payment_received',
    'Hi {{1}}, we received your payment of {{2}} at {{3}}. Your {{4}} membership is valid until {{5}}. Thank you!',
  ),
  expiryReminder(
    'energize_expiry_reminder',
    'Hi {{1}}, your {{2}} membership at {{3}} ends on {{4}}. Renew soon to keep training without a break.',
  ),
  expired(
    'energize_membership_expired',
    'Hi {{1}}, your {{2}} membership at {{3}} ended on {{4}}. Visit us or reply here to renew.',
  ),
  paymentDue(
    'energize_payment_due',
    'Hi {{1}}, a balance of {{2}} is pending for your {{3}} membership at {{4}}. Please complete the payment to start it.',
  );

  const MessageTemplate(this.cloudName, this.text);
  final String cloudName;
  final String text;

  static MessageTemplate? byKey(String key) {
    for (final t in values) {
      if (t.name == key) return t;
    }
    return null;
  }
}

class RenderedMessage {
  const RenderedMessage(this.template, this.params, this.body);
  final MessageTemplate template;
  final List<String> params;
  final String body;

  String get paramsJson => jsonEncode(params);
}

class MessageComposer {
  const MessageComposer._();

  static String _s(String v) => v
      .replaceAll(RegExp(r'[\r\n\t]+'), ' ')
      .replaceAll(RegExp(r' {2,}'), ' ')
      .trim();

  static RenderedMessage render(MessageTemplate t, List<String> params) {
    var body = t.text;
    final clean = params.map(_s).toList();
    for (var i = 0; i < clean.length; i++) {
      body = body.replaceAll('{{${i + 1}}}', clean[i]);
    }
    assert(!body.contains('{{'), 'unfilled placeholder in ${t.name}');
    return RenderedMessage(t, clean, body);
  }

  static RenderedMessage welcome({
    required String name,
    required String gym,
    required String memberCode,
  }) => render(MessageTemplate.welcome, [name, gym, memberCode]);

  static RenderedMessage paymentReceived({
    required String name,
    required int amountPaise,
    required String gym,
    required String plan,
    required DateTime endDate,
  }) => render(MessageTemplate.paymentReceived, [
    name,
    Money.formatPaise(amountPaise),
    gym,
    plan,
    formatDate(endDate),
  ]);

  static RenderedMessage expiryReminder({
    required String name,
    required String plan,
    required String gym,
    required DateTime endDate,
  }) => render(MessageTemplate.expiryReminder, [
    name,
    plan,
    gym,
    formatDate(endDate),
  ]);

  static RenderedMessage expired({
    required String name,
    required String plan,
    required String gym,
    required DateTime endDate,
  }) => render(MessageTemplate.expired, [name, plan, gym, formatDate(endDate)]);

  static RenderedMessage paymentDue({
    required String name,
    required int balancePaise,
    required String plan,
    required String gym,
  }) => render(MessageTemplate.paymentDue, [
    name,
    Money.formatPaise(balancePaise),
    plan,
    gym,
  ]);
}

/// Click-to-chat link: opens WhatsApp (Desktop/Web/mobile) with the text
/// pre-filled so the owner can send it manually. Needs no API account.
String waMeLink(String phone10, String body) =>
    'https://wa.me/91$phone10?text=${Uri.encodeComponent(body)}';
