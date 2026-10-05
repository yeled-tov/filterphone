import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../app_controller.dart';

/// מסך ראשון: הזנת קוד ההפעלה שהתקבל מ-FilterPhone.
class ActivationScreen extends StatefulWidget {
  const ActivationScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<ActivationScreen> createState() => _ActivationScreenState();
}

class _ActivationScreenState extends State<ActivationScreen> {
  final _code = TextEditingController();
  final _name = TextEditingController();

  @override
  void dispose() {
    _code.dispose();
    _name.dispose();
    super.dispose();
  }

  bool get _codeLooksValid => _code.text.replaceAll(RegExp(r'[^A-Za-z0-9]'), '').length == 10;

  Future<void> _submit() async {
    FocusScope.of(context).unfocus();
    await widget.controller.activate(_code.text, _name.text);
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    final theme = Theme.of(context);
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Icon(Icons.shield_outlined, size: 72, color: theme.colorScheme.primary),
                  const SizedBox(height: 16),
                  Text('FilterPhone',
                      textAlign: TextAlign.center, style: theme.textTheme.headlineMedium),
                  const SizedBox(height: 8),
                  Text('הזינו את קוד ההפעלה שקיבלתם',
                      textAlign: TextAlign.center, style: theme.textTheme.bodyLarge),
                  const SizedBox(height: 32),
                  TextField(
                    controller: _code,
                    textDirection: TextDirection.ltr,
                    textAlign: TextAlign.center,
                    textCapitalization: TextCapitalization.characters,
                    autocorrect: false,
                    enableSuggestions: false,
                    maxLength: 11,
                    style: const TextStyle(fontSize: 24, letterSpacing: 3, fontFamily: 'monospace'),
                    inputFormatters: [
                      FilteringTextInputFormatter.allow(RegExp(r'[A-Za-z0-9-]')),
                      _UpperCaseFormatter(),
                    ],
                    decoration: const InputDecoration(hintText: 'XXXXX-XXXXX', counterText: ''),
                    onChanged: (_) => setState(() {}),
                    onSubmitted: (_) => _codeLooksValid ? _submit() : null,
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _name,
                    decoration: const InputDecoration(
                      labelText: 'שם המכשיר (לא חובה)',
                      hintText: 'למשל: הטלפון של משה',
                    ),
                  ),
                  const SizedBox(height: 24),
                  FilledButton(
                    onPressed: c.busy || !_codeLooksValid ? null : _submit,
                    child: c.busy
                        ? const SizedBox(
                            width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.5))
                        : const Text('הפעלה'),
                  ),
                  if (c.error != null) ...[
                    const SizedBox(height: 16),
                    Text(c.error!,
                        textAlign: TextAlign.center,
                        style: TextStyle(color: theme.colorScheme.error)),
                  ],
                  const SizedBox(height: 32),
                  Text(
                    'אחרי ההפעלה הטלפון יתחבר ל-VPN של FilterPhone. '
                    'כל הגלישה תעבור סינון, כולל תמונות.',
                    textAlign: TextAlign.center,
                    style: theme.textTheme.bodySmall,
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _UpperCaseFormatter extends TextInputFormatter {
  @override
  TextEditingValue formatEditUpdate(TextEditingValue oldValue, TextEditingValue newValue) =>
      newValue.copyWith(text: newValue.text.toUpperCase());
}
