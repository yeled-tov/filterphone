import 'package:flutter/material.dart';

import '../app_controller.dart';
import '../theme.dart';
import 'change_request_screen.dart';

/// המסך הראשי: האם הטלפון מוגן, ומה עוד צריך לעשות.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  bool _pausePromptShowing = false;

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    if (c.adminPauseActive && !_pausePromptShowing) {
      _pausePromptShowing = true;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) _showPausePrompt(c);
      });
    } else if (!c.adminPauseActive) {
      _pausePromptShowing = false;
    }
    return Scaffold(
      appBar: AppBar(
        title: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 36,
              height: 36,
              decoration: BoxDecoration(
                color: Theme.of(context).colorScheme.primary,
                borderRadius: BorderRadius.circular(11),
              ),
              child: const Icon(Icons.shield_rounded, color: Colors.white, size: 22),
            ),
            const SizedBox(width: 9),
            const Text('פילטרפון'),
          ],
        ),
        actions: [
          IconButton(
            tooltip: 'רענון',
            onPressed: c.busy ? null : () => c.refresh(forceSync: true),
            icon: const Icon(Icons.refresh_rounded),
          ),
        ],
      ),
      body: SafeArea(
        child: RefreshIndicator(
          onRefresh: () => c.refresh(forceSync: true),
          child: ListView(
            padding: const EdgeInsets.all(16),
            children: [
              if (c.deviceRemoved) _RemovedBanner(controller: c),
              if (c.device != null && !c.device!.isActive) const _Banner(
                icon: Icons.block,
                text: 'המכשיר הושבת על ידי המנהל. פנו ל-FilterPhone.',
              ),
              _StatusHero(controller: c),
              const SizedBox(height: 4),
              if (c.protection.updateAvailable != null) ...[
                const SizedBox(height: 16),
                _UpdateCard(controller: c),
              ],
              if (c.notice != null || c.error != null) ...[
                const SizedBox(height: 16),
                _Message(controller: c),
              ],
              const SizedBox(height: 16),
              _Checklist(controller: c),
              const SizedBox(height: 16),
              _StatusDetails(controller: c),
              if (c.device?.isActive ?? false) ...[
                const SizedBox(height: 16),
                _BlocksCard(controller: c),
              ],
              const SizedBox(height: 16),
              _ServerProof(controller: c),
              if (!c.deviceOwner) ...[
                const SizedBox(height: 16),
                _RootProvisionCard(controller: c),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _showPausePrompt(AppController c) async {
    if (!mounted || !c.adminPauseActive) {
      _pausePromptShowing = false;
      return;
    }
    await showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (dialogContext) {
        bool working = false;
        String? actionError;
        return StatefulBuilder(
          builder: (dialogContext, setDialogState) => PopScope(
            canPop: false,
            child: AlertDialog(
              title: const Text('החסימה הושהתה'),
              content: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('בחר מה לעשות בטלפון הזה. הבחירה פועלת מקומית ואינה תלויה בכך שהשרת זמין.'),
                  if (actionError != null) ...[
                    const SizedBox(height: 12),
                    Text(actionError!, style: TextStyle(color: Theme.of(dialogContext).colorScheme.error)),
                  ],
                  if (working) ...[
                    const SizedBox(height: 12),
                    const Center(child: CircularProgressIndicator()),
                  ],
                ],
              ),
              actions: [
                TextButton(
                  onPressed: working ? null : () async {
                    setDialogState(() { working = true; actionError = null; });
                    final ok = await c.resumeProtectionLocally();
                    if (!mounted || !dialogContext.mounted) return;
                    if (ok) {
                      Navigator.of(dialogContext).pop();
                    } else {
                      setDialogState(() { working = false; actionError = c.error ?? 'השחזור נכשל.'; });
                    }
                  },
                  child: const Text('החזר חסימה'),
                ),
                FilledButton.tonal(
                  onPressed: working ? null : () async {
                    final confirmed = await showDialog<bool>(
                      context: dialogContext,
                      barrierDismissible: false,
                      builder: (confirmContext) => AlertDialog(
                        title: const Text('הסרת ניהול מהמכשיר'),
                        content: const Text('פעולה זו משחררת את מגבלות Device Owner, מפסיקה את ה-VPN ומסירה את מדיניות החסימה המקומית. לאחר מכן ניתן להסיר את FilterPhone דרך הגדרות Android. ההגנה תיפסק.'),
                        actions: [
                          TextButton(onPressed: () => Navigator.of(confirmContext).pop(false), child: const Text('ביטול')),
                          FilledButton(onPressed: () => Navigator.of(confirmContext).pop(true), child: const Text('אשר הסרה')),
                        ],
                      ),
                    );
                    if (confirmed != true || !mounted || !dialogContext.mounted) return;
                    setDialogState(() { working = true; actionError = null; });
                    final ok = await c.removeManagementLocally();
                    if (!mounted || !dialogContext.mounted) return;
                    if (ok) {
                      Navigator.of(dialogContext).pop();
                    } else {
                      setDialogState(() { working = false; actionError = c.error ?? 'הסרת הניהול נכשלה.'; });
                    }
                  },
                  child: const Text('הסר ניהול'),
                ),
              ],
            ),
          ),
        );
      },
    );
    _pausePromptShowing = false;
    if (mounted) setState(() {});
  }
}

class _RootProvisionCard extends StatelessWidget {
  const _RootProvisionCard({required this.controller});
  final AppController controller;

  String _message(Map<String, Object?> result) {
    switch (result['result']) {
      case 'device_owner_set': return 'ההקמה הצליחה. המכשיר הוגדר כמנוהל.';
      case 'already_managed': return 'ניהול המכשיר כבר פעיל.';
      case 'no_root': return 'לא נמצא Root מאושר במכשיר. לא בוצע שינוי.';
      case 'root_denied': return 'בקשת Root נדחתה או לא קיבלה הרשאת מנהל. אפשר לאשר את בקשת su ואז לנסות שוב.';
      case 'requires_factory_reset': return 'Android אינו מאפשר להגדיר Device Owner במצב הנוכחי. אין כאן מעקף בטוח; בדרך התקנית נדרשת בדרך כלל הקמה בזמן ההתקנה הראשונית או לאחר איפוס. לא בוצע איפוס.';
      case 'accounts_present': return 'יש חשבונות במכשיר שמונעים את ההקמה דרך Root. לא בוצע שינוי.';
      case 'already_has_device_owner': return 'כבר מוגדר מנהל מכשיר אחר. FilterPhone לא החליף אותו.';
      default: return 'ההקמה לא הושלמה (${result['code'] ?? 'שגיאה'}). לא נעשה מעקף אבטחה.';
    }
  }

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 16),
        child: Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Text('הקמה מתקדמת — Root / מנהל מכשיר', style: TextStyle(fontWeight: FontWeight.w600)),
                const SizedBox(height: 6),
                const Text('ניסיון דרך Root יעבוד רק במכשיר שיש בו Root מאושר, ורק אם Android מאפשר להגדיר Device Owner. לא מבוצע איפוס אוטומטי.'),
                const SizedBox(height: 12),
                FilledButton.icon(
                  onPressed: controller.rootProvisioning ? null : () async {
                    final result = await controller.provisionRoot();
                    if (!context.mounted) return;
                    await showDialog<void>(
                      context: context,
                      builder: (context) => AlertDialog(
                        title: Text(result['ok'] == true ? 'ההקמה הצליחה' : 'תוצאת בדיקת Root'),
                        content: Text(_message(result)),
                        actions: [FilledButton(onPressed: () => Navigator.of(context).pop(), child: const Text('סגירה'))],
                      ),
                    );
                  },
                  icon: controller.rootProvisioning
                      ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.admin_panel_settings_rounded),
                  label: Text(controller.rootProvisioning ? 'בודק ומנסה להקים...' : 'בדוק Root והקם בלחיצה'),
                ),
              ],
            ),
          ),
        ),
      );
}

class _StatusHero extends StatelessWidget {
  const _StatusHero({required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final theme = Theme.of(context);
    final ok = c.isProtected;
    final color = ok ? StatusColors.ok(context) : StatusColors.warn(context);
    final d = c.device;
    return Card(
      clipBehavior: Clip.antiAlias,
      child: Container(
        padding: const EdgeInsets.fromLTRB(20, 22, 20, 20),
        decoration: BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topRight,
            end: Alignment.bottomLeft,
            colors: [
              theme.colorScheme.primaryContainer,
              theme.colorScheme.surface,
            ],
          ),
        ),
        child: Column(
          children: [
            Container(
              width: 72,
              height: 72,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: color.withValues(alpha: .10),
              ),
              child: Icon(ok ? Icons.verified_user_rounded : Icons.gpp_maybe_rounded, size: 43, color: color),
            ),
            const SizedBox(height: 12),
            Text(ok ? 'הטלפון מוגן' : 'נדרשת פעולה כדי להשלים את ההגנה',
                textAlign: TextAlign.center,
                style: theme.textTheme.titleLarge?.copyWith(color: color, fontWeight: FontWeight.w600)),
            if (d != null) ...[
              const SizedBox(height: 8),
              Text('רמת סינון: ${d.levelName}', style: theme.textTheme.bodyLarge),
              const SizedBox(height: 16),
              Row(
                children: [
                  Expanded(child: _Stat(value: d.blockedToday, label: 'תמונות נחסמו היום')),
                  const SizedBox(width: 12),
                  Expanded(child: _Stat(value: d.paintedToday, label: 'תמונות נצבעו היום')),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({required this.value, required this.label});
  final int value;
  final String label;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 12),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerHigh,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(children: [
        Text('$value', style: theme.textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w600)),
        Text(label, style: theme.textTheme.bodySmall),
      ]),
    );
  }
}

class _Checklist extends StatelessWidget {
  const _Checklist({required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final checks = c.checks;
    return Card(
      child: Column(
        children: [
          _CheckTile(
            ok: checks[Check.vpn]!,
            title: 'חיבור לסינון (VPN)',
            okText: 'מחובר',
            todo: 'הסינון לא פעיל כרגע.',
            actionLabel: 'חיבור',
            onAction: c.busy ? null : c.connectVpn,
          ),
          const Divider(height: 1),
          _CheckTile(
            ok: checks[Check.certificate]!,
            title: 'תעודת סינון',
            okText: 'מותקנת',
            todo: 'בלי התעודה אי אפשר לסנן תמונות באתרים מאובטחים.',
            actionLabel: 'התקנה',
            onAction: c.busy ? null : c.installCertificate,
          ),
          const Divider(height: 1),
          _CheckTile(
            ok: checks[Check.alwaysOn]!,
            optional: true,
            title: 'VPN תמיד מופעל',
            okText: c.vpn.lockdown == true ? 'פעיל, כולל חסימה בלי VPN' : 'פעיל',
            todo: 'בהגדרות ה-VPN בחרו FilterPhone והפעילו "VPN תמיד מופעל" '
                'ו"חסימת חיבורים ללא VPN".',
            actionLabel: 'הגדרות',
            onAction: c.openVpnSettings,
          ),
          const Divider(height: 1),
          _CheckTile(
            ok: checks[Check.battery]!,
            title: 'פעולה ברקע (סוללה)',
            okText: 'ללא הגבלה — אנדרואיד לא יעצור את הסינון',
            todo: 'כדי לחסוך סוללה אנדרואיד עוצר אפליקציות ברקע — וזה עלול לנתק את הסינון ואת המגן. '
                'צריך לאשר ל-FilterPhone לפעול ללא הגבלת סוללה.',
            actionLabel: 'אישור',
            onAction: c.requestBatteryExemption,
          ),
          if (c.guardNeeded) ...[
            const Divider(height: 1),
            _CheckTile(
              ok: checks[Check.guard]!,
              title: 'מגן FilterPhone',
              okText: c.deviceOwner ? 'פעיל (מנהל מכשיר)' : 'פעיל — אפליקציות ומסכים חסומים לא נפתחים',
              todo: 'המנהל חסם אפליקציות או מסכים בטלפון הזה. כדי שזה יעבוד: '
                  'בהגדרות הנגישות מפעילים את "מגן FilterPhone". '
                  'המגן רואה רק איזו אפליקציה נפתחה — לא הודעות, לא תוכן ולא הקלדות.',
              actionLabel: 'הפעלה',
              onAction: c.openGuardSettings,
            ),
          ],
          if (!checks[Check.privateDns]!) ...[
            const Divider(height: 1),
            const _CheckTile(
              ok: false,
              title: 'DNS פרטי',
              okText: '',
              todo: 'מוגדר "DNS פרטי" שעוקף את הסינון וחוסם את הגלישה. '
                  'בהגדרות ← חיבורים ← DNS פרטי ← כבוי.',
            ),
          ],
        ],
      ),
    );
  }
}

/// מה חסום בטלפון הזה (מעבר לסינון התמונות), ובקשת שינוי למנהל.
class _BlocksCard extends StatelessWidget {
  const _BlocksCard({required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final d = controller.device!;
    final theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(children: [
              Icon(Icons.block, color: theme.colorScheme.primary),
              const SizedBox(width: 8),
              Expanded(child: Text('חסימות פעילות', style: theme.textTheme.titleMedium)),
              if (d.sitesToday > 0) Text('${d.sitesToday} היום', style: theme.textTheme.bodySmall),
            ]),
            const SizedBox(height: 10),
            if (d.blocks.isEmpty)
              Text('רק סינון תמונות ואתרים לפי הרמה.', style: theme.textTheme.bodyMedium)
            else
              Wrap(spacing: 6, runSpacing: 6, children: [
                for (final b in d.blocks) Chip(label: Text(b.title), visualDensity: VisualDensity.compact),
              ]),
            const SizedBox(height: 12),
            if (d.hasPendingChange)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Row(children: [
                  Icon(Icons.schedule, size: 18, color: StatusColors.warn(context)),
                  const SizedBox(width: 6),
                  const Expanded(child: Text('בקשת שינוי נשלחה ומחכה למנהל.')),
                ]),
              ),
            OutlinedButton.icon(
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => ChangeRequestScreen(controller: controller)),
              ),
              icon: const Icon(Icons.edit_note),
              label: const Text('בקשת שינוי (רמה / חסימות)'),
            ),
          ],
        ),
      ),
    );
  }
}

/// פירוט המצב — מה באמת פועל עכשיו בטלפון (לא רק "מוגן / לא מוגן").
class _StatusDetails extends StatelessWidget {
  const _StatusDetails({required this.controller});
  final AppController controller;

  static String _ago(DateTime? t) {
    if (t == null) return 'עוד לא';
    final d = DateTime.now().difference(t);
    if (d.inMinutes < 2) return 'עכשיו';
    if (d.inMinutes < 60) return 'לפני ${d.inMinutes} דק\'';
    if (d.inHours < 24) return 'לפני ${d.inHours} שע\'';
    return 'לפני ${d.inDays} ימים';
  }

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final p = c.protection;
    final theme = Theme.of(context);
    Color? tone(bool? good) => good == null ? null : (good ? StatusColors.ok(context) : StatusColors.warn(context));
    Widget row(String label, String value, {bool? good}) => Padding(
          padding: const EdgeInsets.symmetric(vertical: 4),
          child: Row(children: [
            Expanded(child: Text(label, style: theme.textTheme.bodyMedium)),
            Text(value, style: theme.textTheme.bodyMedium?.copyWith(color: tone(good), fontWeight: FontWeight.w600)),
          ]),
        );
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('מצב ההגנה', style: theme.textTheme.titleMedium),
            const SizedBox(height: 8),
            row('VPN (סינון)', c.vpn.isUp ? 'פעיל' : 'לא פעיל', good: c.vpn.isUp),
            row('מגן FilterPhone (נגישות)',
                p.guardEnabled ? (p.guardRunning ? 'פעיל' : 'מופעל, ממתין') : (c.guardNeeded ? 'כבוי' : 'לא נדרש'),
                good: p.guardEnabled || !c.guardNeeded),
            row('מנהל מכשיר (Device Owner)', p.deviceOwner ? 'פעיל' : 'לא פעיל'),
            row('סוללה', p.batteryUnrestricted == null ? 'לא ידוע' : (p.backgroundOk ? 'ללא הגבלה' : 'מוגבלת'),
                good: p.batteryUnrestricted == null ? null : p.backgroundOk),
            row('סנכרון מדיניות אחרון', _ago(p.lastPolicySync)),
            row('בדיקת VPN אחרונה שעברה', _ago(p.lastVpnHealthOk ?? c.vpn.lastHealthOk)),
            if (!p.deviceOwner && !p.deviceAdmin) ...[
              const SizedBox(height: 8),
              Text('הגנה מפני הסרה: אפשר להפעיל "מנהל מכשיר" — זה מקשה על הסרת FilterPhone ומודיע למנהל '
                  'אם מבטלים. (זו לא נעילה מלאה; נעילה מלאה — רק בהקמה כמכשיר מנוהל.)',
                  style: theme.textTheme.bodySmall),
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: TextButton(onPressed: c.requestDeviceAdmin, child: const Text('הפעלת הגנה מפני הסרה')),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _CheckTile extends StatelessWidget {
  const _CheckTile({
    required this.ok,
    required this.title,
    required this.okText,
    required this.todo,
    this.actionLabel,
    this.onAction,
    this.optional = false,
  });

  final bool ok;
  final bool optional;
  final String title;
  final String okText;
  final String todo;
  final String? actionLabel;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    final color = ok
        ? StatusColors.ok(context)
        : optional
            ? StatusColors.warn(context)
            : StatusColors.bad(context);
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      leading: Icon(ok ? Icons.check_circle : Icons.error_outline, color: color),
      title: Text(title),
      subtitle: Text(ok ? okText : todo),
      isThreeLine: !ok,
      trailing: ok || actionLabel == null
          ? null
          : OutlinedButton(onPressed: onAction, child: Text(actionLabel!)),
    );
  }
}

class _Message extends StatelessWidget {
  const _Message({required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final isError = c.error != null;
    final theme = Theme.of(context);
    return Card(
      color: isError ? theme.colorScheme.errorContainer : theme.colorScheme.secondaryContainer,
      child: ListTile(
        leading: Icon(isError ? Icons.error : Icons.info_outline),
        title: Text(c.error ?? c.notice ?? ''),
        trailing: IconButton(icon: const Icon(Icons.close), onPressed: c.clearNotice),
      ),
    );
  }
}

class _ServerProof extends StatelessWidget {
  const _ServerProof({required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final theme = Theme.of(context);
    final String text;
    final IconData icon;
    if (!c.serverReachable) {
      text = 'אין חיבור לשרת כרגע';
      icon = Icons.cloud_off;
    } else if (c.device?.viaVpn == true) {
      text = 'השרת מאשר: התעבורה של הטלפון עוברת בסינון';
      icon = Icons.cloud_done;
    } else {
      text = 'השרת עוד לא רואה את הטלפון דרך הסינון';
      icon = Icons.cloud_queue;
    }
    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        Icon(icon, size: 18, color: theme.colorScheme.onSurfaceVariant),
        const SizedBox(width: 8),
        Flexible(child: Text(text, style: theme.textTheme.bodySmall)),
      ],
    );
  }
}

class _UpdateCard extends StatelessWidget {
  const _UpdateCard({required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final version = controller.protection.updateAvailable ?? '';
    return Card(
      child: ListTile(
        leading: const Icon(Icons.system_update),
        title: Text(version.isEmpty ? 'גרסה חדשה של FilterPhone' : 'גרסה חדשה של FilterPhone ($version)'),
        subtitle: const Text('מורידים מדף ההורדה ומתקינים מעל הקיימת — ההגדרות והסינון נשמרים.'),
        trailing: FilledButton(onPressed: controller.openUpdatePage, child: const Text('להורדה')),
      ),
    );
  }
}

class _Banner extends StatelessWidget {
  const _Banner({required this.icon, required this.text});
  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 16),
        child: Card(
          color: Theme.of(context).colorScheme.errorContainer,
          child: ListTile(leading: Icon(icon), title: Text(text)),
        ),
      );
}

class _RemovedBanner extends StatelessWidget {
  const _RemovedBanner({required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 16),
        child: Card(
          color: Theme.of(context).colorScheme.errorContainer,
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Text('המכשיר הוסר מהמערכת. כדי להפעיל מחדש צריך קוד הפעלה חדש.'),
                const SizedBox(height: 12),
                FilledButton(onPressed: controller.resetDevice, child: const Text('הפעלה מחדש')),
              ],
            ),
          ),
        ),
      );
}
