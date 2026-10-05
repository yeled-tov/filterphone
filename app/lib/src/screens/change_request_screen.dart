import 'package:flutter/material.dart';

import '../app_controller.dart';
import 'widgets.dart';

/// בקשת שינוי: רמה אחרת, חסימות נוספות או פחות, או בקשה חופשית. נשלחת למנהל — רק הוא מבצע.
class ChangeRequestScreen extends StatefulWidget {
  const ChangeRequestScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<ChangeRequestScreen> createState() => _ChangeRequestScreenState();
}

class _ChangeRequestScreenState extends State<ChangeRequestScreen> {
  late int _level;
  late Set<String> _options;
  late final Set<String> _current;
  final _note = TextEditingController();

  @override
  void initState() {
    super.initState();
    final d = widget.controller.device;
    _level = d?.level ?? 1;
    _current = {for (final b in d?.blocks ?? const []) b.id};
    _options = {..._current};
    if (widget.controller.catalog == null) widget.controller.loadCatalog();
  }

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  bool get _changed =>
      _level != widget.controller.device?.level ||
      !(_options.length == _current.length && _options.containsAll(_current)) ||
      _note.text.trim().isNotEmpty;

  Future<void> _send() async {
    final c = widget.controller;
    final optionsChanged = !(_options.length == _current.length && _options.containsAll(_current));
    final ok = await c.requestChange(
      level: _level != c.device?.level ? _level : null,
      options: optionsChanged ? (_options.where((id) => c.catalog?.option(id) != null).toList()..sort()) : null,
      note: _note.text.trim(),
    );
    if (ok && mounted) Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) {
          final c = widget.controller;
          final catalog = c.catalog;
          return Scaffold(
            appBar: AppBar(title: const Text('בקשת שינוי')),
            body: SafeArea(
              child: catalog == null
                  ? Center(
                      child: c.error == null
                          ? const CircularProgressIndicator()
                          : Text(c.error!, textAlign: TextAlign.center))
                  : ListView(
                      padding: const EdgeInsets.all(16),
                      children: [
                        const SectionTitle('מה לשנות?',
                            subtitle: 'הבקשה נשלחת למנהל. רק הוא יכול לשנות את הסינון — '
                                'כך גם אי אפשר "לפתוח" את הטלפון בלי אישור.'),
                        if (c.device?.hasPendingChange ?? false)
                          const MessageCard(text: 'כבר יש בקשה פתוחה — שליחה נוספת תעדכן אותה.'),
                        const SizedBox(height: 8),
                        Text('רמת סינון', style: Theme.of(context).textTheme.titleMedium),
                        const SizedBox(height: 8),
                        LevelCards(
                          levels: catalog.levels,
                          selected: _level,
                          current: c.device?.level,
                          onSelect: (l) => setState(() => _level = l),
                        ),
                        const SizedBox(height: 8),
                        Text('חסימות', style: Theme.of(context).textTheme.titleMedium),
                        const SizedBox(height: 8),
                        OptionsPicker(
                          catalog: catalog,
                          selected: _options,
                          onChanged: (s) => setState(() => _options = s),
                        ),
                        const SizedBox(height: 12),
                        TextField(
                          controller: _note,
                          minLines: 2,
                          maxLines: 5,
                          maxLength: 1500,
                          onChanged: (_) => setState(() {}),
                          decoration: const InputDecoration(
                            labelText: 'הסבר / בקשה נוספת',
                            alignLabelWithHint: true,
                          ),
                        ),
                        if (c.error != null) MessageCard(text: c.error!, isError: true, onClose: c.clearNotice),
                        const SizedBox(height: 8),
                        FilledButton(
                          onPressed: c.busy || !_changed ? null : _send,
                          child: c.busy
                              ? const SizedBox(
                                  width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.5))
                              : const Text('שליחה למנהל'),
                        ),
                      ],
                    ),
            ),
          );
        },
      );
}
