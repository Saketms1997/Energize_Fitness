import 'package:flutter/material.dart';

import '../core/validators.dart';
import '../database/app_database.dart';
import '../repositories/gym_repository.dart';

/// Shared gym-profile fields used by first-run setup and Settings.
class GymProfileFormControllers {
  GymProfileFormControllers([Gym? initial])
    : name = TextEditingController(text: initial?.name),
      address = TextEditingController(text: initial?.address),
      contact = TextEditingController(text: initial?.contactNumber),
      upiId = TextEditingController(text: initial?.upiId),
      payee = TextEditingController(text: initial?.upiPayeeName),
      timezone = initial?.timezone ?? 'Asia/Kolkata',
      currency = initial?.currency ?? 'INR';

  final TextEditingController name, address, contact, upiId, payee;
  String timezone;
  String currency;

  GymProfileInput toInput({String? logoPath}) => GymProfileInput(
    name: name.text,
    address: address.text,
    contactNumber: contact.text,
    timezone: timezone,
    currency: currency,
    upiId: upiId.text,
    upiPayeeName: payee.text,
    logoPath: logoPath,
  );

  void dispose() {
    for (final c in [name, address, contact, upiId, payee]) {
      c.dispose();
    }
  }
}

class GymProfileFields extends StatelessWidget {
  const GymProfileFields(this.c, {super.key});
  final GymProfileFormControllers c;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        TextFormField(
          key: const Key('gymName'),
          controller: c.name,
          decoration: const InputDecoration(labelText: 'Gym name'),
          validator: (v) => Validators.required(v, field: 'Gym name'),
        ),
        const SizedBox(height: 12),
        TextFormField(
          controller: c.address,
          decoration: const InputDecoration(labelText: 'Address'),
          maxLines: 2,
        ),
        const SizedBox(height: 12),
        TextFormField(
          controller: c.contact,
          decoration: const InputDecoration(labelText: 'Contact number'),
          keyboardType: TextInputType.phone,
          validator: (v) => Validators.indianMobile(v, optional: true),
        ),
        const SizedBox(height: 12),
        TextFormField(
          controller: c.upiId,
          decoration: const InputDecoration(labelText: 'Owner UPI ID'),
          validator: Validators.upiId,
        ),
        const SizedBox(height: 12),
        TextFormField(
          controller: c.payee,
          decoration: const InputDecoration(labelText: 'UPI payee name'),
          validator: (v) => c.upiId.text.trim().isNotEmpty
              ? Validators.required(v, field: 'Payee name')
              : null,
        ),
      ],
    );
  }
}
