import 'package:flutter/material.dart';

import '../app_controller.dart';
import '../theme.dart';
import 'change_request_screen.dart';

/// המסך הראשי: האם הטלפון מוגן, ומה עוד צריך לעשות.
class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key, required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final c = controller;
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
              if (c.adminPauseActive) ...[
                _PauseCard(controller: c),
                const SizedBox(height: 16),
              ],
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
              if (!c.adminPauseActive && (!c.isProtected || !c.deviceOwner || c.setupStep != null)) ...[
                const SizedBox(height: 16),
                _OneTapCard(controller: c),
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
            ],
          ),
        ),
      ),
    );
  }
}

/// השהיה שהמנהל אישר: הטלפון פתוח לגמרי עד הזמן שנקבע. שתי אפשרויות — להחזיר את ההגנה מיד,
/// או להסיר הכול (רק עכשיו אפשר, ועם אזהרה ברורה שאין דרך חזרה).
class _PauseCard extends StatelessWidget {
  const _PauseCard({required this.controller});
  final AppController controller;

  static String _two(int n) => n.toString().padLeft(2, '0');

  Future<void> _confirmUninstall(BuildContext context) async {
    final c = controller;
    var understood = false;
    final ok = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (dialogContext, setState) => AlertDialog(
          icon: Icon(Icons.warning_amber_rounded, color: StatusColors.bad(dialogContext), size: 36),
          title: const Text('להסיר את כל ההגנה מהטלפון?'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('זה יסיר הכול — כאילו FilterPhone לא היה בטלפון:'),
                const SizedBox(height: 8),
                const Text('• הצריבה (ניהול המכשיר) תימחק — ולא יהיה אפשר להחזיר אותה בלי הקמה מחדש.\n'
                    '• ה-VPN והסינון יוסרו.\n'
                    '• תעודת הסינון תוסר.\n'
                    '• האפליקציה עצמה תוסר.'),
                if (!c.deviceOwner) ...[
                  const SizedBox(height: 8),
                  const Text('בטלפון הזה את תעודת הסינון מוחקים ידנית: הגדרות ← אבטחה ← הצפנה ופרטי כניסה ← '
                      'אישורים מהימנים ← משתמש ← FilterPhone CA ← הסרה.'),
                ],
                const SizedBox(height: 12),
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  controlAffinity: ListTileControlAffinity.leading,
                  value: understood,
                  onChanged: (v) => setState(() => understood = v ?? false),
                  title: const Text('הבנתי — אין דרך חזרה'),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.of(dialogContext).pop(false), child: const Text('ביטול')),
            FilledButton(
              style: FilledButton.styleFrom(backgroundColor: StatusColors.bad(dialogContext)),
              onPressed: understood ? () => Navigator.of(dialogContext).pop(true) : null,
              child: const Text('הסרת הכול'),
            ),
          ],
        ),
      ),
    );
    if (ok == true) await c.uninstallEverything();
  }

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final theme = Theme.of(context);
    final until = c.pauseUntil;
    final untilText = until == null ? '' : ' עד ${_two(until.hour)}:${_two(until.minute)}';
    return Card(
      color: theme.colorScheme.tertiaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(children: [
              Icon(Icons.pause_circle_filled_rounded, size: 36, color: theme.colorScheme.onTertiaryContainer),
              const SizedBox(width: 12),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text('ההגנה מושהית$untilText', style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w800)),
                  const SizedBox(height: 2),
                  const Text('המנהל אישר השהיה: הטלפון פתוח לגמרי — בלי סינון ובלי חסימות. בסוף הזמן הכול חוזר לבד.'),
                ]),
              ),
            ]),
            const SizedBox(height: 14),
            FilledButton.icon(
              onPressed: c.busy ? null : c.resumeProtection,
              icon: const Icon(Icons.shield_rounded),
              label: const Text('החזרת ההגנה עכשיו'),
            ),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              style: OutlinedButton.styleFrom(foregroundColor: StatusColors.bad(context)),
              onPressed: c.busy ? null : () => _confirmUninstall(context),
              icon: const Icon(Icons.delete_forever_rounded),
              label: const Text('הסרת הכול מהטלפון'),
            ),
          ],
        ),
      ),
    );
  }
}

/// הקמה בלחיצה אחת: עם root — הכול לבד; בלי root — כל אישור של אנדרואיד נפתח בתורו.
class _OneTapCard extends StatelessWidget {
  const _OneTapCard({required this.controller});
  final AppController controller;

  static const _howTo = {
    SetupStep.automatic: 'מגדיר את מה שאפשר לבד… אם מופיע חלון של אפליקציית Root — לחצו "אפשר".',
    SetupStep.vpn: 'בחלון שנפתח לחצו "אישור" — זה החיבור לסינון.',
    SetupStep.certificate: 'בהגדרות שנפתחו: "התקנת אישור" ← "אישור CA" ← "התקן בכל זאת" ← בחרו את הקובץ FilterPhone-CA.crt.',
    SetupStep.battery: 'בחלון שנפתח לחצו "אפשר" — כדי שאנדרואיד לא יעצור את הסינון ברקע.',
    SetupStep.guard: 'ברשימה בחרו "מגן FilterPhone" ← הפעלה ← "אפשר". אחר כך חזרו לכאן.',
    SetupStep.done: 'הכול מוכן.',
  };

  static String _result(Map<String, Object?> r) {
    switch (r['device_owner_result']) {
      case 'accounts_present':
        return 'כדי שהטלפון יהיה מנוהל לגמרי (צריבה) אנדרואיד דורש שלא יהיו חשבונות בטלפון. '
            'אפשר להסיר זמנית את החשבונות (הגדרות ← חשבונות) וללחוץ שוב, או להקים אחרי איפוס עם קוד QR.';
      case 'already_has_device_owner':
        return 'בטלפון כבר מוגדרת אפליקציית ניהול אחרת — FilterPhone לא מחליף אותה.';
      case 'requires_factory_reset':
      case 'provisioning_blocked':
        return 'אנדרואיד מאפשר צריבה רק אחרי איפוס (עם קוד QR). כל השאר הוגדר.';
    }
    return '';
  }

  /// צעד-אחר-צעד להקמה בלי מחשב ובלי אפליקציה חיצונית (אנדרואיד 11+, ניפוי אלחוטי מובנה) — בשפה פשוטה.
  static const _adbSteps = [
    'פותחים הגדרות ← אודות הטלפון, ולוחצים 7 פעמים על "מספר Build" (מספר גרסה) עד שכתוב "הפכת למפתח".',
    'חוזרים להגדרות ← מערכת ← אפשרויות מפתחים, ומדליקים את "ניפוי באגים אלחוטי" (Wireless debugging).',
    'נכנסים ל"ניפוי באגים אלחוטי" ← "התאמת מכשיר באמצעות קוד התאמה" — מופיע קוד בן 6 ספרות.',
    'חוזרים לכאן, מזינים את הקוד — ו-FilterPhone יגדיר את הכול לבד. בלי מחשב, בלי אפליקציה נוספת.',
  ];

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final theme = Theme.of(context);
    final step = c.setupStep;
    final running = step != null && step != SetupStep.done;
    final r = c.setupReport;
    final disabled = running || c.busy;
    Widget mark(String label, Object? ok) => Chip(
          visualDensity: VisualDensity.compact,
          avatar: Icon(ok == true ? Icons.check_circle : Icons.remove_circle_outline,
              size: 18, color: ok == true ? StatusColors.ok(context) : theme.colorScheme.outline),
          label: Text(label),
        );
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(children: [
              CircleAvatar(
                radius: 22,
                backgroundColor: theme.colorScheme.primary,
                child: const Icon(Icons.bolt_rounded, color: Colors.white),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text('הקמה בלחיצה אחת', style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w800)),
                  const SizedBox(height: 2),
                  const Text('FilterPhone מגדיר הכול לבד: צריבת ניהול, מגן, תעודה, סוללה וחיבור לסינון. '
                      'בחרו את הדרך שמתאימה לטלפון שלכם.'),
                ]),
              ),
            ]),
            if (running) ...[
              const SizedBox(height: 14),
              const LinearProgressIndicator(),
              const SizedBox(height: 8),
              Text('עכשיו: ${AppController.setupStepTitle(step)}',
                  style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w700)),
              Text(_howTo[step] ?? ''),
            ] else ...[
              const SizedBox(height: 14),
              // דרך 1 — Root
              _SetupOption(
                icon: Icons.verified_user_rounded,
                title: 'יש לי Root בטלפון',
                body: 'אם הטלפון שלך עבר Root — לחצו כאן ואשרו את בקשת ה-Root (Superuser) שתקפוץ. '
                    'כל השאר (צריבה, מגן, תעודה, סוללה, VPN) יוגדר אוטומטית, בלי אף אישור נוסף.',
                buttonLabel: 'הקמה עם Root',
                buttonIcon: Icons.bolt_rounded,
                onPressed: disabled ? null : c.oneTapSetup,
              ),
              const SizedBox(height: 10),
              // דרך 2 — בלי Root: ניפוי אלחוטי מובנה (בלי מחשב, בלי אפליקציה חיצונית)
              _SetupOption(
                icon: Icons.wifi_tethering_rounded,
                title: 'אין לי Root — הקמה בלי מחשב',
                body: 'ההקמה עובדת דרך "ניפוי אלחוטי" מתוך FilterPhone עצמה — בלי מחשב ובלי אפליקציה נוספת. '
                    'צריך להפעיל פעם אחת "אפשרויות מפתחים" ו"ניפוי באגים אלחוטי", ולהזין קוד התאמה. ההסבר למטה.',
                steps: _adbSteps,
                buttonLabel: 'הקמה בלי Root',
                buttonIcon: Icons.wifi_tethering_rounded,
                onPressed: disabled ? null : () => _startWirelessSetup(context, c),
              ),
            ],
            if (r != null && r['error'] == null) ...[
              const SizedBox(height: 12),
              Wrap(spacing: 6, runSpacing: 6, children: [
                mark('Root', r['root']),
                mark('צריבה (מנהל מכשיר)', r['device_owner']),
                mark('מגן חוזר לבד', r['secure_settings']),
                mark('מגן FilterPhone', c.guardEnabled),
                mark('תעודה', c.caInstalled),
                mark('סוללה', c.protection.backgroundOk),
                mark('VPN', c.vpn.isUp),
              ]),
              if (_result(r).isNotEmpty) ...[
                const SizedBox(height: 8),
                Text(_result(r), style: theme.textTheme.bodySmall),
              ],
            ],
          ],
        ),
      ),
    );
  }
}

/// אריח של דרך הקמה (Root / ADB): כותרת, הסבר, שלבים (אופציונלי) וכפתור ברוחב מלא.
class _SetupOption extends StatelessWidget {
  const _SetupOption({
    required this.icon,
    required this.title,
    required this.body,
    required this.buttonLabel,
    required this.buttonIcon,
    required this.onPressed,
    this.steps,
  });

  final IconData icon;
  final String title;
  final String body;
  final String buttonLabel;
  final IconData buttonIcon;
  final VoidCallback? onPressed;
  final List<String>? steps;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerHighest.withValues(alpha: .4),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: theme.colorScheme.outlineVariant),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(icon, color: theme.colorScheme.primary),
              const SizedBox(width: 10),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(title, style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w800)),
                  const SizedBox(height: 3),
                  Text(body, style: theme.textTheme.bodySmall),
                ]),
              ),
            ],
          ),
          if (steps != null)
            Material(
              type: MaterialType.transparency,     // ל-ExpansionTile (ListTile) בתוך רקע צבעוני
              child: Theme(
              data: theme.copyWith(dividerColor: Colors.transparent),
              child: ExpansionTile(
                tilePadding: EdgeInsets.zero,
                childrenPadding: const EdgeInsets.only(bottom: 8),
                title: Text('איך מפעילים (צעד אחר צעד)', style: theme.textTheme.bodyMedium),
                children: [
                  for (var i = 0; i < steps!.length; i++)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 4),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          CircleAvatar(
                            radius: 11,
                            backgroundColor: theme.colorScheme.primary,
                            child: Text('${i + 1}',
                                style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.bold)),
                          ),
                          const SizedBox(width: 10),
                          Expanded(child: Text(steps![i], style: theme.textTheme.bodySmall)),
                        ],
                      ),
                    ),
                ],
              ),
            ),
            ),
          const SizedBox(height: 10),
          FilledButton.icon(
            onPressed: onPressed,
            icon: Icon(buttonIcon, size: 18),
            label: Text(buttonLabel),
          ),
        ],
      ),
    );
  }
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

  /// הכפתור צמוד להסבר שלו (מתחת לטקסט, באותו צד) — לא "מרחף" בצד השני של השורה.
  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final color = ok
        ? StatusColors.ok(context)
        : optional
            ? StatusColors.warn(context)
            : StatusColors.bad(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Icon(ok ? Icons.check_circle : Icons.error_outline, color: color),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title, style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w700)),
                const SizedBox(height: 2),
                Text(ok ? okText : todo, style: theme.textTheme.bodyMedium),
                if (!ok && actionLabel != null) ...[
                  const SizedBox(height: 8),
                  FilledButton.tonal(
                    onPressed: onAction,
                    style: FilledButton.styleFrom(visualDensity: VisualDensity.compact),
                    child: Text(actionLabel!),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
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
    final theme = Theme.of(context);
    final version = controller.protection.updateAvailable ?? '';
    // פריסה מפורשת (Row + Expanded + כפתור ברוחב מלא מתחת) — כדי שהטקסט לא יצטמצם לרוחב אפס
    // ויירד אות-אחרי-אות בצד המסך (הבאג שדווח). כאן הטקסט תמיד מקבל את כל רוחב הכרטיס.
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(Icons.system_update, color: theme.colorScheme.primary),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        version.isEmpty
                            ? 'גרסה חדשה של FilterPhone'
                            : 'גרסה חדשה של FilterPhone ($version)',
                        style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        'מורידים מדף ההורדה ומתקינים מעל הקיימת — ההגדרות והסינון נשמרים.',
                        style: theme.textTheme.bodySmall,
                      ),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            FilledButton.icon(
              onPressed: controller.openUpdatePage,
              icon: const Icon(Icons.download_rounded, size: 18),
              label: const Text('להורדה'),
            ),
          ],
        ),
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

/// פותח את זרימת ההקמה בניפוי אלחוטי: מסביר, פותח אפשרויות מפתחים, ומקבל את קוד ההתאמה.
Future<void> _startWirelessSetup(BuildContext context, AppController c) async {
  await showDialog<void>(
    context: context,
    barrierDismissible: true,
    builder: (_) => _WirelessDialog(controller: c),
  );
}

/// דיאלוג ההקמה בניפוי אלחוטי (בלי מחשב): הדלקת ניפוי אלחוטי + קוד התאמה בן 6 ספרות.
class _WirelessDialog extends StatefulWidget {
  const _WirelessDialog({required this.controller});
  final AppController controller;

  @override
  State<_WirelessDialog> createState() => _WirelessDialogState();
}

class _WirelessDialogState extends State<_WirelessDialog> {
  final _code = TextEditingController();
  bool _working = false;

  @override
  void dispose() {
    _code.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final code = _code.text.trim();
    if (code.length != 6 || int.tryParse(code) == null) {
      setState(() {});
      return;
    }
    setState(() => _working = true);
    final ok = await widget.controller.wirelessSetup(code);
    if (!mounted) return;
    Navigator.of(context).pop();
    if (!ok) {
      final msg = widget.controller.notice;
      if (msg != null && msg.isNotEmpty) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final valid = _code.text.trim().length == 6 && int.tryParse(_code.text.trim()) != null;
    return AlertDialog(
      title: const Text('הקמה בלי מחשב'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text('1. הדליקו "ניפוי באגים אלחוטי" (אפשרויות מפתחים).'),
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: TextButton.icon(
                onPressed: _working ? null : widget.controller.openDeveloperOptions,
                icon: const Icon(Icons.settings_rounded, size: 18),
                label: const Text('פתח אפשרויות מפתחים'),
              ),
            ),
            const Text('2. נכנסים ל"ניפוי באגים אלחוטי" ← "התאמת מכשיר באמצעות קוד התאמה".'),
            const SizedBox(height: 4),
            const Text('3. מזינים כאן את הקוד בן 6 הספרות שהופיע:'),
            const SizedBox(height: 10),
            TextField(
              controller: _code,
              enabled: !_working,
              keyboardType: TextInputType.number,
              textAlign: TextAlign.center,
              maxLength: 6,
              style: theme.textTheme.headlineSmall?.copyWith(letterSpacing: 8),
              decoration: const InputDecoration(
                hintText: '••••••',
                counterText: '',
                border: OutlineInputBorder(),
              ),
              onChanged: (_) => setState(() {}),
              onSubmitted: (_) => _submit(),
            ),
            if (_working) ...[
              const SizedBox(height: 14),
              const LinearProgressIndicator(),
              const SizedBox(height: 8),
              const Text('מתחבר ומגדיר… זה יכול לקחת כמה שניות.'),
            ],
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: _working ? null : () => Navigator.of(context).pop(),
          child: const Text('ביטול'),
        ),
        FilledButton(
          onPressed: (_working || !valid) ? null : _submit,
          child: const Text('התחל הקמה'),
        ),
      ],
    );
  }
}
