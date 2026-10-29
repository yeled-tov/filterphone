import 'package:flutter/material.dart';

import '../app_controller.dart';
import '../theme.dart';
import 'change_request_screen.dart';
import 'install_screen.dart';

/// מצב ההגנה (מה שהיה בטאב "סינון", עכשיו בתוך טאב הפרופיל): האם הטלפון מוגן, ומה עוד צריך לעשות.
/// רק מה שחסר — כשהכול תקין אין כאן רשימת הרשאות בכלל (המצב הטכני המלא: הגדרות).
class ProtectionPanel extends StatelessWidget {
  const ProtectionPanel({super.key, required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final running = c.setupStep != null && c.setupStep != SetupStep.done;
    const gap = SizedBox(height: 16);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (c.deviceRemoved) _RemovedBanner(controller: c),                 // הבאנרים — עם רווח משלהם
        if (c.device != null && !c.device!.isActive)
          const _Banner(icon: Icons.block, text: 'המכשיר הושבת על ידי המנהל. פנו ל-FilterPhone.'),
        if (c.adminPauseActive) ...[_PauseCard(controller: c), gap],
        _StatusHero(controller: c),
        if (c.protection.updateAvailable != null) ...[gap, _UpdateCard(controller: c)],
        if (c.notice != null || c.error != null) ...[gap, _Message(controller: c)],
        if (!c.adminPauseActive && (c.missingRequired.isNotEmpty || running)) ...[
          gap,
          _FixCard(controller: c),
          if (!c.deviceOwner || running) ...[gap, OneTapCard(controller: c)],
        ],
        if (c.device?.isActive ?? false) ...[gap, _ActiveCard(controller: c)],
      ],
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
class OneTapCard extends StatelessWidget {
  const OneTapCard({super.key, required this.controller});
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
                  Text('התקנת הסינון', style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w800)),
                  const SizedBox(height: 2),
                  const Text('FilterPhone מגדיר הכול: ניהול מכשיר, מגן, תעודה, סוללה וחיבור לסינון. '
                      'במסך ההתקנה בוחרים את הדרך שמתאימה לטלפון.'),
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
              FilledButton.icon(
                onPressed: disabled ? null : () => openInstallScreen(context, c),
                icon: const Icon(Icons.install_mobile_rounded, size: 18),
                label: const Text('למסך ההתקנה'),
              ),
              const SizedBox(height: 6),
              Text('אוטומטי (Root / ניפוי באגים אלחוטי — צעד אחרי צעד) או ידני (מחשב / צעד-צעד).',
                  style: theme.textTheme.bodySmall, textAlign: TextAlign.center),
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
                mark('VPN', c.vpn.protected),
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

/// הפריטים שחסרים — כל אחד עם הסבר קצר וכפתור שפותח בדיוק את החלון של אנדרואיד שצריך.
/// במצב ההגנה שבפרופיל (_FixCard) ובחלון שקופץ לבד כשהרשאה נופלת (AppShell).
class MissingChecksList extends StatelessWidget {
  const MissingChecksList({super.key, required this.controller, required this.only});
  final AppController controller;
  final List<Check> only;

  @override
  Widget build(BuildContext context) {
    final c = controller;
    Widget tile(Check k) => switch (k) {
          Check.vpn => _CheckTile(
              ok: false,
              title: 'חיבור לסינון (VPN)',
              okText: 'מחובר',
              todo: 'הסינון לא פעיל כרגע. לחצו "חיבור" ואשרו בחלון של אנדרואיד.',
              actionLabel: 'חיבור',
              onAction: c.busy ? null : c.connectVpn,
            ),
          Check.certificate => _CheckTile(
              ok: false,
              title: 'תעודת סינון',
              okText: 'מותקנת',
              todo: 'בלי התעודה אי אפשר לסנן תמונות באתרים מאובטחים.',
              actionLabel: 'התקנה',
              onAction: c.busy ? null : c.installCertificate,
            ),
          Check.battery => _CheckTile(
              ok: false,
              title: 'פעולה ברקע (סוללה)',
              okText: 'ללא הגבלה',
              todo: 'אנדרואיד עלול לעצור את הסינון ברקע כדי לחסוך סוללה. צריך לאשר ל-FilterPhone לפעול ללא הגבלה.',
              actionLabel: 'אישור',
              onAction: c.requestBatteryExemption,
            ),
          Check.guard => _CheckTile(
              ok: false,
              title: 'מגן FilterPhone',
              okText: 'פעיל',
              todo: 'המגן כבוי — ולכן אפליקציות ומסכים שנחסמו נפתחים. בהגדרות הנגישות מפעילים את '
                  '"מגן FilterPhone". המגן רואה רק איזו אפליקציה נפתחה — לא הודעות, לא תוכן ולא הקלדות.',
              actionLabel: 'הפעלה',
              onAction: c.openGuardSettings,
            ),
          Check.privateDns => const _CheckTile(
              ok: false,
              title: 'DNS פרטי',
              okText: '',
              todo: 'מוגדר "DNS פרטי" שעוקף את הסינון וחוסם את הגלישה. בהגדרות ← חיבורים ← DNS פרטי ← כבוי.',
            ),
          Check.alwaysOn => _CheckTile(
              ok: false,
              optional: true,
              title: 'VPN תמיד מופעל',
              okText: '',
              todo: 'בהגדרות ה-VPN בחרו FilterPhone והפעילו "VPN תמיד מופעל" ו"חסימת חיבורים ללא VPN".',
              actionLabel: 'הגדרות',
              onAction: c.openVpnSettings,
            ),
        };
    return Column(
      children: [
        for (var i = 0; i < only.length; i++) ...[
          if (i > 0) const Divider(height: 1),
          tile(only[i]),
        ],
      ],
    );
  }
}

/// "נדרשת פעולה" — רק מה שחסר (אחרי ההקמה הכרטיס הזה לא מופיע בכלל).
class _FixCard extends StatelessWidget {
  const _FixCard({required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final missing = controller.missingRequired;
    if (missing.isEmpty) return const SizedBox.shrink();
    return Card(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 14, 16, 4),
            child: Text(missing.length == 1 ? 'עוד דבר אחד וסיימנו' : 'עוד ${missing.length} דברים וסיימנו',
                style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w800)),
          ),
          MissingChecksList(controller: controller, only: missing),
        ],
      ),
    );
  }
}

/// מה פעיל אצל הלקוח הזה: הסינון לפי הרמה, הגנות נוספות, החסימות — ובקשת שינוי למנהל.
class _ActiveCard extends StatelessWidget {
  const _ActiveCard({required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final d = c.device!;
    final theme = Theme.of(context);
    Widget item(IconData icon, String text) => Padding(
          padding: const EdgeInsets.symmetric(vertical: 5),
          child: Row(children: [
            Icon(icon, size: 20, color: StatusColors.ok(context)),
            const SizedBox(width: 10),
            Expanded(child: Text(text, style: theme.textTheme.bodyMedium)),
          ]),
        );
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(children: [
              Icon(Icons.verified_rounded, color: theme.colorScheme.primary),
              const SizedBox(width: 8),
              Expanded(child: Text('מה פעיל אצלך', style: theme.textTheme.titleMedium)),
              if (d.sitesToday > 0) Text('${d.sitesToday} חסימות היום', style: theme.textTheme.bodySmall),
            ]),
            const SizedBox(height: 8),
            item(Icons.image_search_rounded, 'סינון תמונות ואתרים — ${d.levelName}'),
            if (c.guardNeeded && c.checks[Check.guard]!) item(Icons.shield_moon_rounded, 'מגן FilterPhone — אפליקציות ומסכים שנחסמו לא נפתחים'),
            if (c.deviceOwner) item(Icons.admin_panel_settings_rounded, 'מכשיר מנוהל — אי אפשר להסיר או לעקוף את ההגנה'),
            if (!c.deviceOwner && c.vpn.alwaysOn == true) item(Icons.lock_rounded, 'הסינון תמיד מופעל'),
            if (d.blocks.isNotEmpty) ...[
              const SizedBox(height: 10),
              Text('חסום בטלפון הזה', style: theme.textTheme.labelLarge),
              const SizedBox(height: 6),
              Wrap(spacing: 6, runSpacing: 6, children: [
                for (final b in d.blocks) Chip(label: Text(b.title), visualDensity: VisualDensity.compact),
              ]),
            ],
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
class StatusDetailsCard extends StatelessWidget {
  const StatusDetailsCard({super.key, required this.controller});
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
            row('VPN (סינון)',
                c.vpn.isUp ? 'פעיל' : c.vpn.kosherLabel != null ? 'ברשת ${c.vpn.kosherLabel} — הסינון שלהם פעיל' : 'לא פעיל',
                good: c.vpn.protected),
            row('מגן FilterPhone (נגישות)',
                p.guardEnabled ? (p.guardRunning ? 'פעיל' : 'מופעל, ממתין') : (c.guardNeeded ? 'כבוי' : 'לא נדרש'),
                good: p.guardEnabled || !c.guardNeeded),
            row('מנהל מכשיר (Device Owner)', p.deviceOwner ? 'פעיל' : 'לא פעיל'),
            row('VPN תמיד מופעל',
                c.vpn.alwaysOn == null
                    ? 'לא ידוע'
                    : c.vpn.alwaysOn!
                        ? (c.vpn.lockdown == true ? 'פעיל + חסימה בלי VPN' : 'פעיל')
                        : 'כבוי',
                good: c.vpn.alwaysOn),
            if (c.vpn.alwaysOn == false)
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: TextButton(onPressed: c.openVpnSettings, child: const Text('הפעלת "VPN תמיד מופעל"')),
              ),
            row('סוללה', p.batteryUnrestricted == null ? 'לא ידוע' : (p.backgroundOk ? 'ללא הגבלה' : 'מוגבלת'),
                good: p.batteryUnrestricted == null ? null : p.backgroundOk),
            row('סנכרון מדיניות אחרון', _ago(p.lastPolicySync)),
            row('בדיקת VPN אחרונה שעברה', _ago(p.lastVpnHealthOk ?? c.vpn.lastHealthOk)),
            if (c.vpn.dataOk != null)
              row('תעבורה במנהרה',
                  c.vpn.dataOk! ? 'עוברת (${_ago(c.vpn.lastDataOk)})' : 'לא עברה ${_ago(c.vpn.lastDataFail)}',
                  good: c.vpn.dataOk),
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

class ServerProofLine extends StatelessWidget {
  const ServerProofLine({super.key, required this.controller});
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
                        'FilterPhone מוריד ומתקין את העדכון — ההגדרות והסינון נשמרים.',
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
              label: const Text('עדכון עכשיו'),
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

/// פותח את מסך ההתקנה (אוטומטי / ידני).
Future<void> openInstallScreen(BuildContext context, AppController c) =>
    Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => InstallScreen(controller: c)));
