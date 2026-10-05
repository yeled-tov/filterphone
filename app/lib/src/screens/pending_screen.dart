import 'package:flutter/material.dart';

import '../app_controller.dart';
import 'widgets.dart';

/// אחרי ההרשמה: הבקשה אצל המנהל. האפליקציה בודקת כל 20 שניות, ומתחברת לבד ברגע שאושרה.
class PendingScreen extends StatelessWidget {
  const PendingScreen({super.key, required this.controller});
  final AppController controller;

  Future<void> _cancel(BuildContext context) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('לבטל את הבקשה?'),
        content: const Text('הבקשה תימחק, ותוכלו להירשם מחדש בכל עת.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('לא')),
          FilledButton.tonal(onPressed: () => Navigator.pop(ctx, true), child: const Text('ביטול הבקשה')),
        ],
      ),
    );
    if (ok == true) await controller.cancelRegistration();
  }

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;
    final rejected = c.device?.isRejected ?? false;
    final summary = c.pendingSummary;
    final catalog = c.catalog;
    final level = summary?['level'] as int?;
    final options = (summary?['options'] as List? ?? const []).cast<String>();
    return Scaffold(
      body: SafeArea(
        child: RefreshIndicator(
          onRefresh: c.refreshPending,
          child: ListView(
            padding: const EdgeInsets.all(20),
            children: [
              const SizedBox(height: 12),
              Center(
                child: Container(
                  width: 96,
                  height: 96,
                  decoration: BoxDecoration(
                    color: rejected ? scheme.errorContainer : scheme.primaryContainer,
                    shape: BoxShape.circle,
                  ),
                  child: Icon(rejected ? Icons.block : Icons.hourglass_top_rounded,
                      size: 52, color: rejected ? scheme.error : scheme.primary),
                ),
              ),
              const SizedBox(height: 16),
              Text(rejected ? 'הבקשה לא אושרה' : 'הבקשה נשלחה!',
                  textAlign: TextAlign.center, style: theme.textTheme.headlineSmall),
              const SizedBox(height: 8),
              Text(
                c.device?.message.isNotEmpty == true
                    ? c.device!.message
                    : 'הבקשה התקבלה ומחכה לאישור. נעדכן ברגע שהחיבור ייפתח.',
                textAlign: TextAlign.center,
                style: theme.textTheme.bodyLarge,
              ),
              const SizedBox(height: 24),
              if (!rejected)
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text('מה קורה עכשיו?', style: theme.textTheme.titleMedium),
                      const SizedBox(height: 10),
                      const _Step(n: 1, text: 'הבקשה הגיעה למנהל, עם כל מה שבחרתם.', done: true),
                      const _Step(n: 2, text: 'נחזור אליכם להסדרת התשלום.'),
                      const _Step(n: 3, text: 'אחרי האישור — האפליקציה מתחברת לבד. אפשר לסגור אותה בינתיים.'),
                    ]),
                  ),
                ),
              if (summary != null) ...[
                const SizedBox(height: 12),
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text('מה ביקשתם', style: theme.textTheme.titleMedium),
                      const SizedBox(height: 8),
                      Text('${summary['name'] ?? ''} · ${summary['phone'] ?? ''}'),
                      if (level != null)
                        Text('רמת סינון: ${catalog?.level(level)?.name ?? level}',
                            style: theme.textTheme.bodyMedium),
                      if (options.isNotEmpty) ...[
                        const SizedBox(height: 8),
                        Wrap(spacing: 6, runSpacing: 6, children: [
                          for (final id in options)
                            Chip(label: Text(catalog?.option(id)?.title ?? id), visualDensity: VisualDensity.compact),
                        ]),
                      ],
                      if ((summary['note'] as String? ?? '').isNotEmpty) ...[
                        const SizedBox(height: 8),
                        Text('"${summary['note']}"', style: theme.textTheme.bodySmall),
                      ],
                    ]),
                  ),
                ),
              ],
              const SizedBox(height: 16),
              if (!c.serverReachable)
                const MessageCard(text: 'אין חיבור לשרת כרגע. ננסה שוב אוטומטית.', isError: true),
              if (c.error != null) MessageCard(text: c.error!, isError: true, onClose: c.clearNotice),
              const SizedBox(height: 8),
              if (rejected)
                FilledButton(onPressed: c.cancelRegistration, child: const Text('שליחת בקשה חדשה'))
              else ...[
                OutlinedButton.icon(
                  style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(48)),
                  onPressed: c.refreshPending,
                  icon: const Icon(Icons.refresh),
                  label: const Text('בדיקה עכשיו'),
                ),
                const SizedBox(height: 8),
                TextButton(onPressed: () => _cancel(context), child: const Text('ביטול הבקשה')),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _Step extends StatelessWidget {
  const _Step({required this.n, required this.text, this.done = false});
  final int n;
  final String text;
  final bool done;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        CircleAvatar(
          radius: 12,
          backgroundColor: done ? scheme.primary : scheme.surfaceContainerHighest,
          child: done
              ? Icon(Icons.check, size: 15, color: scheme.onPrimary)
              : Text('$n', style: const TextStyle(fontSize: 12)),
        ),
        const SizedBox(width: 10),
        Expanded(child: Text(text)),
      ]),
    );
  }
}
