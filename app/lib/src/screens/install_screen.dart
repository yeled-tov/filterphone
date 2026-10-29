import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../app_controller.dart';
import '../native_bridge.dart';
import '../theme.dart';

/// מסך ההתקנה: אוטומטי (Root / ניפוי באגים אלחוטי — צעד אחרי צעד) או ידני (מחשב / בלי ניהול מכשיר).
///
/// בניפוי האלחוטי FilterPhone מוביל: בודק מה כבר מוכן (אפשרויות מפתחים, Wi-Fi, התראות), פותח את המסך הנכון
/// בהגדרות עם הסבר מדויק לפי היצרן, ומתקדם לבד כשחוזרים. כל לחיצה ואישור של אנדרואיד — המשתמש עושה
/// בעצמו; את קוד ההתאמה מקלידים בהתראה (בלי לצאת מהמסך של הקוד), וההקמה ממשיכה לבד.
class InstallScreen extends StatefulWidget {
  const InstallScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<InstallScreen> createState() => _InstallScreenState();
}

class _InstallScreenState extends State<InstallScreen> with WidgetsBindingObserver {
  AppController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      c.setInstallScreenOpen(true);
      _refresh();
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    c.installScreenOpen = false;          // בלי notify בזמן dispose
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _refresh();   // חזרו מההגדרות — מה השתנה?
  }

  Future<void> _refresh() async {
    await c.refreshSetupInfo();
    await c.loadLastSetupReport();
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) {
        final info = c.setupInfo;
        final theme = Theme.of(context);
        final done = c.deviceOwner || (info?.deviceOwner ?? false);
        return Scaffold(
          appBar: AppBar(title: const Text('התקנת הסינון')),
          body: ListView(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
            children: [
              if (info != null)
                Text('${info.manufacturer} ${info.model} · ${info.androidVersion}',
                    style: theme.textTheme.bodySmall, textAlign: TextAlign.center),
              const SizedBox(height: 8),
              if (done) _DoneCard(controller: c) else ...[
                _ReportCard(controller: c),
                _SectionTitle('התקנה אוטומטית', 'מומלץ — FilterPhone מקבל ניהול מלא של הטלפון (אי אפשר להסיר אותו או לעקוף אותו).'),
                _RootCard(controller: c),
                const SizedBox(height: 12),
                if (info == null || info.wirelessSupported)
                  _WirelessCard(controller: c, info: info)
                else
                  const _InfoCard(icon: Icons.wifi_off_rounded,
                      text: 'ניפוי באגים אלחוטי קיים רק מאנדרואיד 11. בטלפון הזה — Root, מחשב, או צעד-צעד.'),
                const SizedBox(height: 20),
                const _SectionTitle('התקנה ידנית', 'כשהאוטומטית לא מתאימה.'),
                _ComputerCard(info: info),
                const SizedBox(height: 12),
                _StepByStepCard(controller: c),
              ],
            ],
          ),
        );
      },
    );
  }
}

// ------------------------------------------------------------------ הסברים לפי יצרן

/// איפה "מספר Build" (שבע לחיצות מדליקות את אפשרויות המפתחים) — לפי היצרן.
String buildNumberHint(String vendor) => switch (vendor) {
      'samsung' => 'במסך שנפתח ("אודות הטלפון") ← "מידע על התוכנה" ← לחצו 7 פעמים ברצף על "מספר Build".',
      'xiaomi' => 'במסך שנפתח ("אודות הטלפון") לחצו 7 פעמים ברצף על "גרסת MIUI" / "גרסת OS" '
          '(ב-HyperOS: "Xiaomi HyperOS").',
      'oneplus' => 'במסך שנפתח ("אודות המכשיר") ← "גרסה" ← לחצו 7 פעמים ברצף על "מספר Build".',
      _ => 'במסך שנפתח ("אודות הטלפון") לחצו 7 פעמים ברצף על "מספר Build" '
          '(אם לא רואים — הוא בתוך "מידע על התוכנה").',
    };

/// איפה "אפשרויות מפתחים" — כשצריך להגיע לבד.
String developerOptionsPath(String vendor) => switch (vendor) {
      'xiaomi' => 'הגדרות ← הגדרות נוספות ← אפשרויות מפתחים',
      'samsung' => 'הגדרות ← אפשרויות מפתחים (בתחתית הרשימה)',
      'oneplus' => 'הגדרות ← הגדרות נוספות / מערכת ← אפשרויות מפתחים',
      _ => 'הגדרות ← מערכת ← אפשרויות מפתחים',
    };

/// הסבר לחשבון שחוסם את ההקמה: (שם, איך מסירים). [type] — סוג החשבון באנדרואיד.
(String, String) accountBlocker(String type) {
  if (type == 'com.google') {
    return ('חשבון Google', 'הגדרות ← חשבונות (וגיבוי) ← ניהול חשבונות ← Google ← הסרת חשבון.');
  }
  if (type == 'com.osp.app.signin') {
    return ('חשבון Samsung', 'הגדרות ← חשבונות וגיבוי ← ניהול חשבונות ← Samsung account ← הסרת חשבון '
        '(צריך את הסיסמה של החשבון).');
  }
  if (type.startsWith('com.xiaomi')) return ('חשבון Xiaomi (Mi)', 'הגדרות ← Mi Account (למעלה) ← יציאה מהחשבון.');
  if (type.startsWith('com.whatsapp')) {
    return ('WhatsApp', 'הגדרות ← חשבונות ← WhatsApp ← הסרת חשבון (ההודעות בוואטסאפ לא נמחקות).');
  }
  if (type.contains('heytap') || type.contains('oplus') || type.contains('oneplus') || type.contains('oppo')) {
    return ('חשבון OnePlus / OPPO', 'הגדרות ← (השם שלך למעלה) ← יציאה מהחשבון.');
  }
  if (type.contains('huawei')) return ('Huawei ID', 'הגדרות ← Huawei ID ← יציאה.');
  return ('חשבון "$type"', 'הגדרות ← חשבונות ← החשבון ← הסרת חשבון.');
}

/// הסבר למשתמש נוסף שחוסם (תיקייה מאובטחת, אפליקציות כפולות, מרחב שני).
(String, String) userBlocker(String name) {
  final n = name.toLowerCase();
  if (n.contains('secure folder') || n.contains('knox')) {
    return ('תיקייה מאובטחת', 'הגדרות ← אבטחה ופרטיות ← תיקייה מאובטחת ← הגדרות נוספות ← הסרת התקנה.');
  }
  if (n.contains('dual') || n.contains('clone') || n.contains('parallel')) {
    return ('אפליקציות כפולות', 'הגדרות ← תכונות מתקדמות ← Dual Messenger / אפליקציות כפולות ← לכבות לכל האפליקציות.');
  }
  if (n.contains('xspace') || n.contains('second')) return ('מרחב שני', 'הגדרות ← מרחב שני ← מחיקת המרחב.');
  if (n.contains('work')) return ('פרופיל עבודה', 'הגדרות ← חשבונות ← פרופיל עבודה ← הסרה.');
  return ('משתמש "$name"', 'הגדרות ← משתמשים (או "משתמשים מרובים") ← המשתמש ← מחיקה.');
}

/// מה לעשות אחרי כישלון — טקסט ראשי לפי הסיבה.
String failureText(String reason) => switch (reason) {
      'accounts_present' => 'אנדרואיד לא מאפשר להפוך למנהל מכשיר כשיש בטלפון חשבונות. ניסינו להשבית אותם זמנית — '
          'מה שנשאר צריך להסיר ידנית (אחרי ההתקנה אפשר להוסיף בחזרה):',
      'multiple_users' => 'יש בטלפון משתמש נוסף — אנדרואיד לא מאפשר ניהול מכשיר כשהוא קיים. צריך להסיר:',
      'already_has_device_owner' => 'בטלפון כבר מוגדרת אפליקציית ניהול אחרת. צריך להסיר אותה (או איפוס להגדרות יצרן).',
      'provisioning_blocked' || 'requires_factory_reset' =>
        'אנדרואיד מאפשר ניהול מכשיר בטלפון הזה רק אחרי איפוס (הקמה עם קוד QR). אפשר בינתיים את "צעד-צעד".',
      'pairing_failed' || 'bad_pairing_code' => 'הקוד לא התאים או שפג תוקפו. לחצו שוב "התאמת מכשיר באמצעות קוד" '
          'והקלידו את הקוד החדש.',
      'pairing_port_not_found' => 'לא מצאנו את חלון ההתאמה. ודאו שהחלון עם הקוד פתוח והטלפון מחובר ל-Wi-Fi, ונסו שוב.',
      'connect_failed' => 'ההתאמה הצליחה אבל החיבור נכשל. ודאו ש"ניפוי באגים אלחוטי" דלוק ושהטלפון ב-Wi-Fi, ונסו שוב.',
      'no_root' => 'לא נמצא Root בטלפון הזה — השתמשו בניפוי באגים אלחוטי.',
      'root_denied' => 'אפליקציית ה-Root לא אישרה. פתחו אותה, אשרו את FilterPhone ונסו שוב.',
      _ => 'ההתקנה לא הושלמה. אפשר לנסות שוב, או לפנות לתמיכה.',
    };

// ------------------------------------------------------------------ כרטיסים

class _SectionTitle extends StatelessWidget {
  const _SectionTitle(this.title, this.sub);
  final String title;
  final String sub;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.only(top: 8, bottom: 8),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(title, style: theme.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w800)),
        Text(sub, style: theme.textTheme.bodySmall),
      ]),
    );
  }
}

class _InfoCard extends StatelessWidget {
  const _InfoCard({required this.icon, required this.text});
  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) => Card(
        child: ListTile(leading: Icon(icon), title: Text(text)),
      );
}

class _DoneCard extends StatelessWidget {
  const _DoneCard({required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final missing = controller.missingRequired;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(children: [
          Icon(Icons.verified_rounded, size: 56, color: StatusColors.ok(context)),
          const SizedBox(height: 8),
          Text('ההתקנה הושלמה — הטלפון מנוהל על ידי FilterPhone',
              textAlign: TextAlign.center, style: Theme.of(context).textTheme.titleMedium),
          if (missing.isNotEmpty) ...[
            const SizedBox(height: 8),
            const Text('נשארו כמה אישורים — חזרו למסך הפרופיל ואשרו אותם.', textAlign: TextAlign.center),
          ],
          const SizedBox(height: 12),
          FilledButton(onPressed: () => Navigator.of(context).maybePop(), child: const Text('חזרה')),
        ]),
      ),
    );
  }
}

/// תוצאת הניסיון האחרון (גם כשהקוד הוקלד בהתראה): מה חסם, ומה בדיוק להסיר.
class _ReportCard extends StatelessWidget {
  const _ReportCard({required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final r = controller.setupReport;
    final reason = '${r?['device_owner_result'] ?? ''}';
    if (r == null || r['device_owner'] == true || reason.isEmpty ||
        reason == 'already_managed' || reason == 'device_owner_set') {
      return const SizedBox.shrink();
    }
    final blockers = [for (final b in (r['blockers'] as List? ?? const [])) '$b'];
    final theme = Theme.of(context);
    final items = [
      for (final b in blockers) b.startsWith('user:') ? userBlocker(b.substring(5)) : accountBlocker(b),
    ];
    return Card(
      color: theme.colorScheme.errorContainer.withValues(alpha: .35),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Row(children: [
            Icon(Icons.info_outline_rounded, color: theme.colorScheme.error),
            const SizedBox(width: 8),
            Text('הניסיון האחרון לא הושלם', style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w800)),
          ]),
          const SizedBox(height: 6),
          Text(failureText(reason)),
          for (final (title, how) in items)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                const Text('•  '),
                Expanded(child: Text.rich(TextSpan(children: [
                  TextSpan(text: '$title: ', style: const TextStyle(fontWeight: FontWeight.w700)),
                  TextSpan(text: how),
                ]))),
              ]),
            ),
          if (items.isNotEmpty) ...[
            const SizedBox(height: 8),
            const Text('אחרי ההסרה — חזרו לכאן ונסו שוב (את קוד ההתאמה צריך להוציא מחדש).'),
          ],
        ]),
      ),
    );
  }
}

class _RootCard extends StatelessWidget {
  const _RootCard({required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final running = c.setupStep != null && c.setupStep != SetupStep.done;
    return _OptionCard(
      icon: Icons.verified_user_rounded,
      title: 'יש Root בטלפון',
      body: 'לוחצים, מאשרים את בקשת ה-Root (Superuser) שקופצת — וכל השאר אוטומטי: ניהול מכשיר, מגן, תעודה, '
          'סוללה וחיבור לסינון. חשבונות שחוסמים — מושבתים לרגע ומוחזרים לבד.',
      child: FilledButton.icon(
        onPressed: running || c.busy ? null : c.oneTapSetup,
        icon: running ? const SizedBox.square(dimension: 18, child: CircularProgressIndicator(strokeWidth: 2))
            : const Icon(Icons.bolt_rounded, size: 18),
        label: Text(running ? 'מתקין… ${AppController.setupStepTitle(c.setupStep!)}' : 'התקנה עם Root'),
      ),
    );
  }
}

class _OptionCard extends StatelessWidget {
  const _OptionCard({required this.icon, required this.title, required this.body, required this.child});
  final IconData icon;
  final String title;
  final String body;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Icon(icon, color: theme.colorScheme.primary),
            const SizedBox(width: 10),
            Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(title, style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w800)),
              const SizedBox(height: 3),
              Text(body, style: theme.textTheme.bodySmall),
            ])),
          ]),
          const SizedBox(height: 12),
          child,
        ]),
      ),
    );
  }
}

/// ניפוי באגים אלחוטי — חמישה צעדים. כל צעד: מה עושים, כפתור שפותח את המסך הנכון, וסימון ✓ כשזה מוכן.
class _WirelessCard extends StatefulWidget {
  const _WirelessCard({required this.controller, required this.info});
  final AppController controller;
  final SetupStatus? info;

  @override
  State<_WirelessCard> createState() => _WirelessCardState();
}

class _WirelessCardState extends State<_WirelessCard> {
  bool _codePosted = false;     // התראת הקוד הוצגה
  bool _wirelessOpened = false;  // המשתמש פתח את מסך "ניפוי באגים אלחוטי" (אנדרואיד לא תמיד מגלה אם הוא דלוק)
  bool _manual = false;
  bool _working = false;
  final _code = TextEditingController();

  AppController get c => widget.controller;

  @override
  void dispose() {
    _code.dispose();
    super.dispose();
  }

  Future<void> _openWireless() async {
    setState(() => _wirelessOpened = true);
    await c.openSetupPage('wireless');
  }

  Future<void> _pairStep() async {
    final ok = await c.startWirelessNotification();
    setState(() => _codePosted = ok);
    await c.openSetupPage('wireless');
  }

  Future<void> _submit() async {
    final code = _code.text.trim();
    if (code.length != 6 || int.tryParse(code) == null) return;
    setState(() => _working = true);
    await c.wirelessSetup(code);
    if (mounted) setState(() => _working = false);
  }

  @override
  Widget build(BuildContext context) {
    final info = widget.info;
    final vendor = info?.vendor ?? 'other';
    final notif = info?.notifications ?? false;
    final wifi = info?.wifi ?? false;
    final dev = info?.devOptions ?? false;
    final wireless = info?.adbWifi ?? false;
    final steps = <_Step>[
      _Step('התראות', notif,
          'קוד ההתאמה מוקלד בהתראה של FilterPhone (כדי לא לצאת מהמסך של הקוד) — צריך לאשר התראות.',
          notif ? null : ('אישור התראות', () async {
            await c.requestNotifications();
            await c.refreshSetupInfo();
          })),
      _Step('חיבור ל-Wi-Fi', wifi,
          'ניפוי באגים אלחוטי עובד רק כשהטלפון מחובר ל-Wi-Fi (אפשר גם נקודה חמה מטלפון אחר).',
          wifi ? null : ('הגדרות Wi-Fi', () => c.openSetupPage('wifi'))),
      _Step('הפעלת "אפשרויות מפתחים"', dev,
          '${buildNumberHint(vendor)} אם מתבקשים — מקלידים את קוד הנעילה של הטלפון. כשמופיע "אתה מפתח עכשיו" — '
          'חוזרים לכאן.',
          dev ? null : ('פתיחת "אודות הטלפון"', () => c.openSetupPage('about'))),
      _Step('הדלקת "ניפוי באגים אלחוטי"', wireless || _wirelessOpened && dev,
          'במסך שנפתח גוללים ל"ניפוי באגים אלחוטי" (Wireless debugging) ← לוחצים על השורה עצמה ← מדליקים את המתג '
          'למעלה ← בחלון "לאפשר ניפוי באגים אלחוטי ברשת הזו?" מסמנים "אפשר תמיד ברשת זו" ולוחצים "אישור". '
          'לא נפתח? ${developerOptionsPath(vendor)}.',
          ('פתיחת "ניפוי באגים אלחוטי"', _openWireless)),
      _Step('קוד ההתאמה', false,
          'במסך "ניפוי באגים אלחוטי" לוחצים "התאמת מכשיר באמצעות קוד התאמה" — מופיע קוד בן 6 ספרות. בלי לסגור '
          'את החלון: מושכים מלמעלה את פס ההתראות ← בהתראה של FilterPhone לוחצים "שליחת הקוד" ← מקלידים את 6 '
          'הספרות ← שולחים. ההתקנה ממשיכה לבד (כולל השבתה רגעית של חשבונות שחוסמים), ובסוף מופיעה התראה.',
          (_codePosted ? 'פתיחה שוב + התראת הקוד' : 'התראת הקוד + פתיחת המסך', _pairStep)),
    ];
    final current = steps.indexWhere((s) => !s.done);
    final theme = Theme.of(context);
    return _OptionCard(
      icon: Icons.wifi_tethering_rounded,
      title: 'אין Root — ניפוי באגים אלחוטי (בלי מחשב)',
      body: 'FilterPhone מוביל צעד אחרי צעד ופותח בכל פעם את המסך הנכון. מה שכבר מוכן מסומן ✓.',
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        for (var i = 0; i < steps.length; i++)
          _StepTile(index: i + 1, step: steps[i], active: i == current),
        if (_codePosted)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Row(children: [
              Icon(Icons.notifications_active_rounded, size: 18, color: theme.colorScheme.primary),
              const SizedBox(width: 6),
              const Expanded(child: Text('התראת הקוד מחכה בפס ההתראות.')),
            ]),
          ),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: TextButton(
            onPressed: () => setState(() => _manual = !_manual),
            child: const Text('אין התראה? להקליד את הקוד כאן'),
          ),
        ),
        if (_manual) ...[
          const Text('שימו לב: יציאה ממסך הקוד מחליפה אותו. פתחו את הקוד, חזרו לכאן מהר והקלידו.',
              style: TextStyle(fontSize: 12)),
          const SizedBox(height: 6),
          TextField(
            controller: _code,
            enabled: !_working,
            keyboardType: TextInputType.number,
            textAlign: TextAlign.center,
            maxLength: 6,
            style: theme.textTheme.headlineSmall?.copyWith(letterSpacing: 8),
            decoration: const InputDecoration(hintText: '••••••', counterText: '', border: OutlineInputBorder()),
            onChanged: (_) => setState(() {}),
            onSubmitted: (_) => _submit(),
          ),
          const SizedBox(height: 8),
          FilledButton(
            onPressed: _working || _code.text.trim().length != 6 ? null : _submit,
            child: _working ? const Text('מתקין…') : const Text('התחלת ההתקנה'),
          ),
        ],
      ]),
    );
  }
}

class _Step {
  const _Step(this.title, this.done, this.how, this.action);
  final String title;
  final bool done;
  final String how;
  final (String, Future<void> Function())? action;
}

class _StepTile extends StatelessWidget {
  const _StepTile({required this.index, required this.step, required this.active});
  final int index;
  final _Step step;
  final bool active;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final color = step.done ? StatusColors.ok(context) : active ? theme.colorScheme.primary : theme.colorScheme.outline;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        CircleAvatar(
          radius: 13,
          backgroundColor: color,
          child: step.done
              ? const Icon(Icons.check_rounded, size: 16, color: Colors.white)
              : Text('$index', style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.bold)),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(step.title, style: theme.textTheme.titleSmall?.copyWith(
                fontWeight: active ? FontWeight.w800 : FontWeight.w600,
                decoration: step.done ? TextDecoration.lineThrough : null)),
            if (active || !step.done) ...[
              const SizedBox(height: 2),
              Text(step.how, style: theme.textTheme.bodySmall),
            ],
            if (step.action != null && (active || !step.done)) ...[
              const SizedBox(height: 6),
              active
                  ? FilledButton.tonal(onPressed: step.action!.$2, child: Text(step.action!.$1))
                  : OutlinedButton(onPressed: step.action!.$2, child: Text(step.action!.$1)),
            ],
          ]),
        ),
      ]),
    );
  }
}

/// התקנה עם מחשב: הסבר + הפקודות, כל אחת עם כפתור העתקה (ו"העתקת הכול").
class _ComputerCard extends StatelessWidget {
  const _ComputerCard({required this.info});
  final SetupStatus? info;

  static const pkg = 'com.filterphone.app';
  static const commands = [
    ('בדיקה שהטלפון מחובר (צריך להופיע מספר עם "device")', 'adb devices'),
    ('הפיכת FilterPhone למנהל המכשיר', 'adb shell dpm set-device-owner $pkg/.DeviceAdmin'),
    ('המגן חוזר לבד אם מכבים אותו', 'adb shell pm grant $pkg android.permission.WRITE_SECURE_SETTINGS'),
    ('פטור מחיסכון סוללה', 'adb shell dumpsys deviceidle whitelist +$pkg'),
    ('עבודה ברקע', 'adb shell cmd appops set $pkg RUN_ANY_IN_BACKGROUND allow'),
  ];
  static const accountsFix =
      'adb shell "pm disable-user --user 0 com.google.android.gms; dpm set-device-owner $pkg/.DeviceAdmin; '
      'pm enable com.google.android.gms"';

  @override
  Widget build(BuildContext context) {
    final vendor = info?.vendor ?? 'other';
    final theme = Theme.of(context);
    Widget cmd(String label, String text) => Padding(
          padding: const EdgeInsets.only(top: 8),
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text(label, style: theme.textTheme.bodySmall),
            const SizedBox(height: 3),
            Container(
              decoration: BoxDecoration(
                color: theme.colorScheme.surfaceContainerHighest,
                borderRadius: BorderRadius.circular(10),
              ),
              padding: const EdgeInsetsDirectional.only(start: 10),
              child: Row(children: [
                Expanded(
                  child: Directionality(
                    textDirection: TextDirection.ltr,
                    child: SelectableText(text, style: const TextStyle(fontFamily: 'monospace', fontSize: 12.5)),
                  ),
                ),
                IconButton(
                  tooltip: 'העתקה',
                  icon: const Icon(Icons.copy_rounded, size: 18),
                  onPressed: () async {
                    await Clipboard.setData(ClipboardData(text: text));
                    if (context.mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('הועתק')));
                    }
                  },
                ),
              ]),
            ),
          ]),
        );
    final steps = [
      'בטלפון: מדליקים "אפשרויות מפתחים" — ${buildNumberHint(vendor).replaceFirst('במסך שנפתח', 'הגדרות ←')}',
      'בטלפון: ${developerOptionsPath(vendor)} ← מדליקים "ניפוי באגים ב-USB" (USB debugging).',
      'במחשב: מורידים את "Android SDK Platform-Tools" מהאתר developer.android.com/tools/releases/platform-tools, '
          'פותחים את התיקייה, ופותחים בה חלון פקודות (Windows: כותבים cmd בשורת הכתובת של התיקייה ולוחצים Enter).',
      'מחברים את הטלפון בכבל. בטלפון קופץ "לאפשר ניפוי באגים ב-USB?" — מסמנים "אפשר תמיד מהמחשב הזה" ← "אישור".',
      'מריצים את הפקודות למטה אחת אחרי השנייה (במק / לינוקס: ./adb במקום adb).',
    ];
    return Card(
      child: Theme(
        data: theme.copyWith(dividerColor: Colors.transparent),
        child: ExpansionTile(
          leading: Icon(Icons.computer_rounded, color: theme.colorScheme.primary),
          title: const Text('עם מחשב (כבל USB)', style: TextStyle(fontWeight: FontWeight.w800)),
          subtitle: const Text('הדרך הבטוחה ביותר כשיש מחשב. כל הפקודות מוכנות להעתקה.'),
          childrenPadding: const EdgeInsets.fromLTRB(14, 0, 14, 14),
          expandedCrossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            for (var i = 0; i < steps.length; i++)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 3),
                child: Text('${i + 1}. ${steps[i]}'),
              ),
            for (final (label, text) in commands) cmd(label, text),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              icon: const Icon(Icons.copy_all_rounded, size: 18),
              label: const Text('העתקת כל הפקודות'),
              onPressed: () async {
                await Clipboard.setData(ClipboardData(text: [for (final (_, t) in commands.skip(1)) t].join('\n')));
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('הפקודות הועתקו')));
                }
              },
            ),
            const SizedBox(height: 12),
            Text('מופיעה שגיאה על "accounts" (חשבונות)?', style: theme.textTheme.titleSmall),
            const Text('אנדרואיד לא מאפשר ניהול מכשיר כשיש חשבון. הפקודה הזו משביתה את שירותי Google לשנייה, '
                'מגדירה את FilterPhone ומדליקה אותם בחזרה. חשבון Samsung / Xiaomi — צריך להסיר ידנית בהגדרות '
                '(ואפשר להוסיף בחזרה אחרי).'),
            cmd('במקום הפקודה השנייה', accountsFix),
            const SizedBox(height: 8),
            const Text('בסוף — חוזרים ל-FilterPhone. המסך הזה יזהה לבד שההתקנה הושלמה.'),
          ],
        ),
      ),
    );
  }
}

/// בלי ניהול מכשיר — רק חלונות האישור של אנדרואיד, אחד אחרי השני (הדרך הקודמת).
class _StepByStepCard extends StatelessWidget {
  const _StepByStepCard({required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final running = c.setupStep != null && c.setupStep != SetupStep.done;
    return _OptionCard(
      icon: Icons.checklist_rounded,
      title: 'צעד-צעד בלי ניהול מכשיר (פחות מומלץ)',
      body: 'מאשרים בעצמך את חלונות אנדרואיד: חיבור לסינון (VPN), תעודה, סוללה ומגן FilterPhone. הסינון עובד, '
          'אבל בלי ניהול מכשיר קל יותר להסיר את FilterPhone או לעקוף חלק מהחסימות.',
      child: OutlinedButton.icon(
        onPressed: running || c.busy ? null : c.stepByStepSetup,
        icon: const Icon(Icons.play_arrow_rounded, size: 18),
        label: Text(running ? 'עכשיו: ${AppController.setupStepTitle(c.setupStep!)}' : 'התחלה צעד-צעד'),
      ),
    );
  }
}
