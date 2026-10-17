import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../app_controller.dart';
import 'home_screen.dart';

/// הגדרות: מראה, עדכונים, הצעות ייעול (ישר לוואטסאפ של המנהל), המצב הטכני המלא, הגנה מתקדמת ואודות.
/// מגיעים מכפתור "הגדרות" בטאב הפרופיל.
class SettingsScreen extends StatelessWidget {
  const SettingsScreen({super.key, required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final c = controller;
        return Scaffold(
          appBar: AppBar(title: const Text('הגדרות')),
          body: ListView(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
            children: [
              _Section(
                title: 'מראה',
                icon: Icons.palette_outlined,
                children: [
                  const Text('ערכת נושא'),
                  const SizedBox(height: 8),
                  SegmentedButton<String>(
                    showSelectedIcon: false,
                    segments: const [
                      ButtonSegment(value: 'system', label: Text('לפי המערכת')),
                      ButtonSegment(value: 'light', label: Text('בהיר')),
                      ButtonSegment(value: 'dark', label: Text('כהה')),
                    ],
                    selected: {c.themeMode},
                    onSelectionChanged: (v) => c.setThemeMode(v.first),
                  ),
                  const SizedBox(height: 16),
                  const Text('גודל טקסט'),
                  const SizedBox(height: 8),
                  SegmentedButton<double>(
                    showSelectedIcon: false,
                    segments: const [
                      ButtonSegment(value: 1.0, label: Text('רגיל')),
                      ButtonSegment(value: 1.15, label: Text('גדול')),
                      ButtonSegment(value: 1.3, label: Text('גדול מאוד')),
                    ],
                    selected: {c.textScale},
                    onSelectionChanged: (v) => c.setTextScale(v.first),
                  ),
                ],
              ),
              _UpdatesSection(controller: c),
              _SuggestionSection(controller: c),
              _SupportToolsSection(controller: c),
              // המצב הטכני המלא (מה שהיה בטאב הסינון) — כרטיס משלו
              const SizedBox(height: 12),
              StatusDetailsCard(controller: c),
              const SizedBox(height: 8),
              ServerProofLine(controller: c),
              if (!c.deviceOwner) ...[
                const SizedBox(height: 20),
                const _Heading('הגנה מתקדמת',
                    'צריבת ניהול (Device Owner) נועלת את ההגנה: אי אפשר להסיר את FilterPhone, לכבות את '
                    'הסינון או לנקות לו נתונים — גם לא עם Root.'),
                const SizedBox(height: 8),
                OneTapCard(controller: c),
              ],
              _Section(
                title: 'אודות',
                icon: Icons.info_outline,
                children: [
                  _Row('גרסה', c.appVersion.isEmpty ? '—' : c.appVersion),
                  _Row('מזהה מכשיר', c.androidId.isEmpty ? '—' : c.androidId,
                      onCopy: c.androidId.isEmpty
                          ? null
                          : () async {
                              await Clipboard.setData(ClipboardData(text: c.androidId));
                              if (context.mounted) {
                                ScaffoldMessenger.of(context)
                                    .showSnackBar(const SnackBar(content: Text('מזהה המכשיר הועתק')));
                              }
                            }),
                  _Row('ניהול מכשיר', c.deviceOwner ? 'מכשיר מנוהל (צריבה)' : 'רגיל'),
                ],
              ),
            ],
          ),
        );
      },
    );
  }
}

/// עדכוני FilterPhone. במנהל מכשיר — מתעדכן לבד בשקט. בלי מנהל מכשיר — הורדה והתקנה מתוך האפליקציה.
class _UpdatesSection extends StatefulWidget {
  const _UpdatesSection({required this.controller});
  final AppController controller;

  @override
  State<_UpdatesSection> createState() => _UpdatesSectionState();
}

class _UpdatesSectionState extends State<_UpdatesSection> {
  bool _checking = false;

  Future<void> _check() async {
    setState(() => _checking = true);
    await widget.controller.refresh(forceSync: true);
    if (!mounted) return;
    setState(() => _checking = false);
    final v = widget.controller.protection.updateAvailable;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(v == null ? 'יש לך את הגרסה האחרונה ✓' : 'נמצאה גרסה חדשה: $v'),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    final available = c.protection.updateAvailable;
    return _Section(
      title: 'עדכונים',
      icon: Icons.system_update_outlined,
      children: [
        _Row('הגרסה שמותקנת', c.appVersion.isEmpty ? '—' : c.appVersion),
        const SizedBox(height: 8),
        if (c.deviceOwner)
          const Text('הטלפון מנוהל — עדכונים מותקנים לבד, בשקט, ברגע שהם יוצאים.')
        else if (available != null)
          FilledButton.icon(
            onPressed: c.openUpdatePage,
            icon: const Icon(Icons.download_rounded),
            label: Text('הורדה והתקנה של גרסה $available'),
          )
        else
          OutlinedButton.icon(
            onPressed: _checking ? null : _check,
            icon: _checking
                ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.refresh_rounded),
            label: const Text('בדיקת עדכונים'),
          ),
      ],
    );
  }
}

/// הצעת ייעול — נשלחת ישר לוואטסאפ של המנהל.
class _SuggestionSection extends StatefulWidget {
  const _SuggestionSection({required this.controller});
  final AppController controller;

  @override
  State<_SuggestionSection> createState() => _SuggestionSectionState();
}

class _SuggestionSectionState extends State<_SuggestionSection> {
  final _text = TextEditingController();
  bool _sending = false;

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    setState(() => _sending = true);
    final error = await widget.controller.sendSuggestion(_text.text);
    if (!mounted) return;
    setState(() => _sending = false);
    if (error == null) _text.clear();
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(error ?? 'תודה! ההצעה נשלחה למנהל 🙏'),
    ));
  }

  @override
  Widget build(BuildContext context) {
    return _Section(
      title: 'הצעות ייעול',
      icon: Icons.lightbulb_outline,
      children: [
        const Text('רעיון לשיפור FilterPhone? כתבו כאן — זה מגיע ישירות למנהל.'),
        const SizedBox(height: 10),
        TextField(
          controller: _text,
          minLines: 3,
          maxLines: 6,
          maxLength: 1000,
          decoration: const InputDecoration(
            hintText: 'למשל: הייתי רוצה ש…',
            border: OutlineInputBorder(),
          ),
        ),
        FilledButton.icon(
          onPressed: _sending ? null : _send,
          icon: const Icon(Icons.send_rounded),
          label: const Text('שליחת ההצעה'),
        ),
      ],
    );
  }
}

/// כלי תמיכה: שליחת דוח אבחון למנהל בלחיצה, ושיתוף FilterPhone עם חבר.
class _SupportToolsSection extends StatefulWidget {
  const _SupportToolsSection({required this.controller});
  final AppController controller;

  @override
  State<_SupportToolsSection> createState() => _SupportToolsSectionState();
}

class _SupportToolsSectionState extends State<_SupportToolsSection> {
  bool _sending = false;

  Future<void> _sendDiag() async {
    setState(() => _sending = true);
    final error = await widget.controller.sendDiagnosticReport();
    if (!mounted) return;
    setState(() => _sending = false);
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(error ?? 'דוח האבחון נשלח למנהל ✓'),
    ));
  }

  Future<void> _share() async {
    try {
      await widget.controller.native.shareText(widget.controller.shareText);
    } on PlatformException catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('לא ניתן לשתף: ${e.message ?? e.code}')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    return _Section(
      title: 'עוד',
      icon: Icons.handyman_outlined,
      children: [
        if (c.canChatWithSupport)
          OutlinedButton.icon(
            onPressed: _sending ? null : _sendDiag,
            icon: _sending
                ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.description_outlined),
            label: const Text('שליחת דוח אבחון לתמיכה'),
          ),
        const SizedBox(height: 8),
        FilledButton.tonalIcon(
          onPressed: _share,
          icon: const Icon(Icons.ios_share_rounded),
          label: const Text('שיתוף FilterPhone עם חבר'),
        ),
      ],
    );
  }
}

class _Section extends StatelessWidget {
  const _Section({required this.title, required this.icon, required this.children});
  final String title;
  final IconData icon;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.only(top: 12),
      child: Card(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 14, 16, 16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(children: [
                Icon(icon, size: 21, color: theme.colorScheme.primary),
                const SizedBox(width: 8),
                Text(title, style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w800)),
              ]),
              const SizedBox(height: 12),
              ...children,
            ],
          ),
        ),
      ),
    );
  }
}

class _Heading extends StatelessWidget {
  const _Heading(this.title, this.body);
  final String title;
  final String body;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 4),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(title, style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w800)),
        const SizedBox(height: 2),
        Text(body, style: theme.textTheme.bodySmall),
      ]),
    );
  }
}

class _Row extends StatelessWidget {
  const _Row(this.label, this.value, {this.onCopy});
  final String label;
  final String value;
  final VoidCallback? onCopy;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(children: [
        Expanded(child: Text(label, style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant))),
        Flexible(
          child: Text(value, textAlign: TextAlign.end, style: theme.textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w700)),
        ),
        if (onCopy != null)
          IconButton(
            tooltip: 'העתקה',
            visualDensity: VisualDensity.compact,
            onPressed: onCopy,
            icon: const Icon(Icons.copy_rounded, size: 18),
          ),
      ]),
    );
  }
}
