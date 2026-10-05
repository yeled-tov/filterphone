import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';

import '../app_controller.dart';

/// בקשת הצטרפות. מנהל מאשר את הפרטים ומוסר ללקוח קוד הפעלה.
class RegistrationScreen extends StatefulWidget {
  const RegistrationScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<RegistrationScreen> createState() => _RegistrationScreenState();
}

class _RegistrationScreenState extends State<RegistrationScreen> {
  final _formKey = GlobalKey<FormState>();
  final _name = TextEditingController();
  final _email = TextEditingController();
  final _phone = TextEditingController();
  int _level = 1;

  Future<void> _showActivationDialog() async {
    final code = TextEditingController();
    final deviceName = TextEditingController();
    final form = GlobalKey<FormState>();
    await showDialog<void>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('הפעלת מכשיר'),
        content: Form(
          key: form,
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            TextFormField(
              controller: code,
              textCapitalization: TextCapitalization.characters,
              textDirection: TextDirection.ltr,
              decoration: const InputDecoration(labelText: 'קוד הפעלה'),
              validator: (value) => (value?.replaceAll(RegExp(r'[^A-Za-z0-9]'), '').length ?? 0) == 10
                  ? null
                  : 'יש להזין קוד תקין',
            ),
            TextField(
              controller: deviceName,
              decoration: const InputDecoration(labelText: 'שם המכשיר (לא חובה)'),
            ),
          ]),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext), child: const Text('ביטול')),
          FilledButton(
            onPressed: () async {
              if (!form.currentState!.validate()) return;
              final activated = await widget.controller.activate(code.text, deviceName.text);
              if (activated && dialogContext.mounted) Navigator.pop(dialogContext);
            },
            child: const Text('המשך'),
          ),
        ],
      ),
    );
    code.dispose();
    deviceName.dispose();
  }

  @override
  void dispose() {
    _name.dispose();
    _email.dispose();
    _phone.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    FocusScope.of(context).unfocus();
    if (!_formKey.currentState!.validate()) return;
    final sent = await widget.controller.requestAccess(
      name: _name.text,
      email: _email.text,
      phone: _phone.text,
      level: _level,
    );
    if (sent && mounted) {
      _name.clear();
      _email.clear();
      _phone.clear();
    }
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
              constraints: const BoxConstraints(maxWidth: 440),
              child: Form(
                key: _formKey,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Icon(Icons.shield_outlined, size: 68, color: theme.colorScheme.primary),
                    const SizedBox(height: 12),
                    Text('FilterPhone',
                        textAlign: TextAlign.center, style: theme.textTheme.headlineMedium),
                    const SizedBox(height: 8),
                    Text('בקשת הצטרפות',
                        textAlign: TextAlign.center, style: theme.textTheme.titleLarge),
                    const SizedBox(height: 8),
                    Text('מלאו פרטים ובחרו רמת סינון. לאחר שנבדוק את הבקשה נחזור אליכם.',
                        textAlign: TextAlign.center, style: theme.textTheme.bodyMedium),
                    const SizedBox(height: 24),
                    TextFormField(
                      controller: _name,
                      textCapitalization: TextCapitalization.words,
                      autofillHints: const [AutofillHints.name],
                      textInputAction: TextInputAction.next,
                      decoration: const InputDecoration(labelText: 'שם מלא'),
                      validator: (value) => (value?.trim().length ?? 0) < 2 ? 'נא להזין שם' : null,
                    ),
                    const SizedBox(height: 12),
                    TextFormField(
                      controller: _email,
                      keyboardType: TextInputType.emailAddress,
                      autofillHints: const [AutofillHints.email],
                      textInputAction: TextInputAction.next,
                      autocorrect: false,
                      decoration: const InputDecoration(labelText: 'כתובת דוא״ל'),
                      validator: (value) {
                        final email = value?.trim() ?? '';
                        return RegExp(r'^[^\s@]+@[^\s@]+\.[^\s@]+$').hasMatch(email)
                            ? null
                            : 'נא להזין כתובת דוא״ל תקינה';
                      },
                    ),
                    const SizedBox(height: 12),
                    TextFormField(
                      controller: _phone,
                      keyboardType: TextInputType.phone,
                      autofillHints: const [AutofillHints.telephoneNumber],
                      textInputAction: TextInputAction.next,
                      decoration: const InputDecoration(labelText: 'מספר טלפון'),
                      validator: (value) {
                        final digits = (value ?? '').replaceAll(RegExp(r'\D'), '');
                        return digits.length >= 7 && digits.length <= 15
                            ? null
                            : 'נא להזין מספר טלפון תקין';
                      },
                    ),
                    const SizedBox(height: 16),
                    DropdownButtonFormField<int>(
                      value: _level,
                      decoration: const InputDecoration(labelText: 'רמת סינון מבוקשת'),
                      items: const [
                        DropdownMenuItem(value: 1, child: Text('מחמיר — חסימת תמונות נשים')),
                        DropdownMenuItem(value: 2, child: Text('בינוני — כיסוי אזורים שהמודל מזהה')),
                        DropdownMenuItem(value: 3, child: Text('בסיסי — חסימת עירום מפורש')),
                      ],
                      onChanged: c.busy ? null : (value) => setState(() => _level = value ?? 1),
                    ),
                    const SizedBox(height: 20),
                    FilledButton.icon(
                      onPressed: c.busy ? null : _submit,
                      icon: c.busy
                          ? const SizedBox(width: 18, height: 18,
                              child: CircularProgressIndicator(strokeWidth: 2))
                          : const Icon(Icons.send_outlined),
                      label: Text(c.busy ? 'שולח בקשה…' : 'שליחת בקשת הצטרפות'),
                    ),
                    if (c.error != null) ...[
                      const SizedBox(height: 14),
                      Text(c.error!, textAlign: TextAlign.center,
                          style: TextStyle(color: theme.colorScheme.error)),
                    ],
                    if (c.notice != null) ...[
                      const SizedBox(height: 14),
                      Card(
                        color: theme.colorScheme.secondaryContainer,
                        child: Padding(
                          padding: const EdgeInsets.all(16),
                          child: Text(c.notice!, textAlign: TextAlign.center),
                        ),
                      ),
                    ],
                    if (defaultTargetPlatform == TargetPlatform.iOS)
                      Text(
                        'אפשר לשלוח בקשת הצטרפות גם מהאייפון. חיבור ה‑VPN לאייפון עדיין לא הושלם, '
                        'ולכן האפליקציה לא תסמן את המכשיר כמוגן עד שתהיה תמיכה מלאה.',
                        textAlign: TextAlign.center,
                        style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.error),
                      )
                    else
                      TextButton(
                        onPressed: c.busy ? null : _showActivationDialog,
                        child: const Text('כבר קיבלתם קוד הפעלה? הפעלת מכשיר'),
                      ),
                    const SizedBox(height: 20),
                    Text(
                      'הפרטים ישמשו לטיפול בבקשה וליצירת קשר בנושא השירות. '
                      'הבקשה אינה מפעילה את הסינון עד לאישור ולמסירת קוד הפעלה.',
                      textAlign: TextAlign.center,
                      style: theme.textTheme.bodySmall,
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
