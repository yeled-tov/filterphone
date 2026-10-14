import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api_client.dart';
import '../app_controller.dart';

/// "כבר יש לי חשבון": שחזור ההגנה לפי מספר הטלפון של החשבון.
///   אותו טלפון (אחרי הסרה והתקנה מחדש) — ההגנה חוזרת מיד, בלי המתנה.
///   טלפון אחר (איפוס / טלפון חדש) — שאלה: להעביר את ההגנה לטלפון הזה, או להוסיף מכשיר נוסף.
///   שתי האפשרויות נשלחות לאישור המנהל.
class RestoreScreen extends StatefulWidget {
  const RestoreScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<RestoreScreen> createState() => _RestoreScreenState();
}

class _RestoreScreenState extends State<RestoreScreen> {
  final _phone = TextEditingController();
  int? _selected;

  @override
  void dispose() {
    _phone.dispose();
    super.dispose();
  }

  bool get _phoneLooksValid => _phone.text.replaceAll(RegExp(r'\D'), '').length >= 9;

  static String _ago(DateTime? t) {
    if (t == null) return 'לא התחבר עדיין';
    final d = DateTime.now().difference(t);
    if (d.inMinutes < 60) return 'מחובר לאחרונה לפני ${d.inMinutes} דק\'';
    if (d.inHours < 48) return 'מחובר לאחרונה לפני ${d.inHours} שעות';
    return 'מחובר לאחרונה לפני ${d.inDays} ימים';
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    final theme = Theme.of(context);
    final r = c.restoreResult;
    return Scaffold(
      appBar: AppBar(
        title: const Text('שחזור ההגנה'),
        leading: IconButton(icon: const Icon(Icons.arrow_back), onPressed: c.backToWelcome, tooltip: 'חזרה'),
      ),
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 440),
              child: r != null && r.status == 'needs_choice'
                  ? _choice(context, c, r)
                  : Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Icon(Icons.restore_rounded, size: 64, color: theme.colorScheme.primary),
                        const SizedBox(height: 12),
                        Text('הזינו את מספר הטלפון של החשבון',
                            textAlign: TextAlign.center, style: theme.textTheme.titleLarge),
                        const SizedBox(height: 8),
                        Text('אם זה אותו טלפון — ההגנה תחזור מיד. אם זה טלפון אחר — נשאל אם להעביר אליו את ההגנה.',
                            textAlign: TextAlign.center, style: theme.textTheme.bodyMedium),
                        const SizedBox(height: 24),
                        TextField(
                          controller: _phone,
                          keyboardType: TextInputType.phone,
                          textDirection: TextDirection.ltr,
                          textAlign: TextAlign.center,
                          style: const TextStyle(fontSize: 22, letterSpacing: 1.5),
                          inputFormatters: [FilteringTextInputFormatter.allow(RegExp(r'[0-9+\- ]'))],
                          decoration: const InputDecoration(hintText: '050-1234567', labelText: 'מספר טלפון'),
                          onChanged: (_) => setState(() {}),
                        ),
                        const SizedBox(height: 20),
                        FilledButton(
                          onPressed: c.busy || !_phoneLooksValid ? null : () => c.restoreByPhone(_phone.text),
                          child: c.busy
                              ? const SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.5))
                              : const Text('שחזור'),
                        ),
                        if (c.error != null) ...[
                          const SizedBox(height: 16),
                          Text(c.error!, textAlign: TextAlign.center, style: TextStyle(color: theme.colorScheme.error)),
                          if (c.error!.contains('להירשם'))
                            TextButton(onPressed: c.openRegister, child: const Text('הצטרפות כלקוח חדש')),
                        ],
                        if (c.notice != null && r?.status == 'pending') ...[
                          const SizedBox(height: 16),
                          Text(c.notice!, textAlign: TextAlign.center),
                        ],
                      ],
                    ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _choice(BuildContext context, AppController c, RestoreLookup r) {
    final theme = Theme.of(context);
    final devices = r.devices;
    _selected ??= devices.isNotEmpty ? devices.first.id : null;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Icon(Icons.phonelink_setup_rounded, size: 64, color: theme.colorScheme.primary),
        const SizedBox(height: 12),
        Text(r.customerName.isEmpty ? 'מצאנו את החשבון' : 'שלום ${r.customerName}',
            textAlign: TextAlign.center, style: theme.textTheme.titleLarge),
        const SizedBox(height: 8),
        Text('לחשבון הזה יש הגנה על טלפון אחר. להעביר את ההגנה לטלפון הזה?',
            textAlign: TextAlign.center, style: theme.textTheme.bodyLarge),
        const SizedBox(height: 16),
        if (devices.length > 1) Text('איזה טלפון מוחלף?', style: theme.textTheme.titleSmall),
        RadioGroup<int>(
          groupValue: _selected,
          onChanged: (v) => setState(() => _selected = v),
          child: Column(children: [
            for (final d in devices)
              Card(
                child: RadioListTile<int>(
                  value: d.id,
                  title: Text(d.name),
                  subtitle: Text(d.status == 'active' ? _ago(d.lastSeen) : 'מנותק'),
                ),
              ),
          ]),
        ),
        const SizedBox(height: 12),
        FilledButton.icon(
          onPressed: c.busy || _selected == null ? null : () => c.requestRestore(mode: 'swap', oldDeviceId: _selected),
          icon: const Icon(Icons.swap_horiz_rounded),
          label: const Text('העברת ההגנה לטלפון הזה'),
        ),
        const SizedBox(height: 6),
        Text('הטלפון הקודם יתנתק מההגנה ברגע שהמנהל יאשר.',
            textAlign: TextAlign.center, style: theme.textTheme.bodySmall),
        const SizedBox(height: 16),
        OutlinedButton.icon(
          style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(48)),
          onPressed: c.busy ? null : () => c.requestRestore(mode: 'additional'),
          icon: const Icon(Icons.add_to_home_screen_rounded),
          label: const Text('זה מכשיר נוסף (בתשלום נפרד)'),
        ),
        if (c.error != null) ...[
          const SizedBox(height: 16),
          Text(c.error!, textAlign: TextAlign.center, style: TextStyle(color: theme.colorScheme.error)),
        ],
      ],
    );
  }
}
