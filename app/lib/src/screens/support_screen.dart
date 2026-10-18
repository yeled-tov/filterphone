part of 'app_shell.dart';

/// לשונית התמיכה: בחירה למעלה (קוביות זו לצד זו) —
///   • "תמיכה באפליקציה" — כותבים כאן, וההודעה מגיעה למנהל (ואם הוא לא זמין, הבוט עונה). התשובה
///     חוזרת לכאן עם התראה. מצב הסינון מצורף אוטומטית כדי שלא נצטרך לשאול.
///   • "תמיכה בוואטסאפ" — כפתור שפותח את הוואטסאפ של המנהל עם מזהה המכשיר ומספר הטלפון כבר בהודעה.
///   • "ניהול תמיכה" — רק במכשיר המנהל: כל הפניות של הלקוחות.
class SupportScreen extends StatefulWidget {
  const SupportScreen({super.key, required this.controller, this.visible = true, this.onOpenTab});
  final AppController controller;

  /// הלשונית מוצגת עכשיו — רק אז מרעננים את השיחה עם המנהל.
  final bool visible;
  final void Function(int tab)? onOpenTab;

  @override
  State<SupportScreen> createState() => _SupportScreenState();
}

enum _SupportMode { app, whatsapp }

class _SupportScreenState extends State<SupportScreen> {
  static const _poll = Duration(seconds: 8);
  static const _storeTab = 1;
  static const _waGreen = Color(0xFF128C7E);

  _SupportMode _mode = _SupportMode.app;

  final _text = TextEditingController();
  final _scroll = ScrollController();
  Timer? _timer;

  AppController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    // בלי שיחה באפליקציה (עוד לא רשום) — מתחילים בוואטסאפ
    if (!c.canChatWithSupport && c.supportWhatsApp.trim().isNotEmpty) _mode = _SupportMode.whatsapp;
    c.addListener(_onChange);     // השיחה / הבדיקה המהירה מתעדכנות חי (תשובה מהמנהל, הרשאה שנפלה)
    if (widget.visible) _onShown();
  }

  void _setMode(_SupportMode m) {
    setState(() => _mode = m);
    if (m == _SupportMode.app) unawaited(_toBottom());
  }

  void _openConsole() => Navigator.of(context).push(
        MaterialPageRoute(builder: (_) => SupportConsoleScreen(controller: c)),
      );

  void _onChange() {
    if (mounted) setState(() {});
  }

  @override
  void didUpdateWidget(SupportScreen old) {
    super.didUpdateWidget(old);
    if (widget.visible && !old.visible) {
      _onShown();
    } else if (!widget.visible && old.visible) {
      _timer?.cancel();
    }
  }

  @override
  void dispose() {
    c.removeListener(_onChange);
    _timer?.cancel();
    _text.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _onShown() {
    if (!c.canChatWithSupport) return;
    unawaited(c.clearSupportNotification());
    unawaited(c.loadSupport().then((_) => _toBottom()));
    _timer?.cancel();
    _timer = Timer.periodic(_poll, (_) {
      if (mounted && widget.visible) c.loadSupport();
    });
  }

  /// גלילה להודעה האחרונה — אחרי שהיא נבנתה (ברשימה עצלה הגובה הכולל ידוע רק אחרי הציור),
  /// ושוב אם הרשימה גדלה בזמן הגלילה.
  Future<void> _toBottom() async {
    await WidgetsBinding.instance.endOfFrame;
    for (var i = 0; i < 3 && mounted && _scroll.hasClients; i++) {
      final target = _scroll.position.maxScrollExtent;
      if ((_scroll.position.pixels - target).abs() < 1) break;
      await _scroll.animateTo(target, duration: const Duration(milliseconds: 200), curve: Curves.easeOut);
    }
  }

  Future<void> _send([String? preset]) async {
    final value = (preset ?? _text.text).trim();
    if (value.isEmpty) return;
    final error = await c.sendSupport(value);
    if (!mounted) return;
    if (error != null) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(error)));
      return;
    }
    _text.clear();
    await _toBottom();
  }

  Future<void> _run(BotAction a) async {
    switch (a) {
      case BotAction.human:
        break;                                     // כבר במסך התמיכה
      case BotAction.reconnectVpn:
        await c.connectVpn();
      case BotAction.openGuard:
        await c.openGuardSettings();
      case BotAction.battery:
        await c.requestBatteryExemption();
      case BotAction.setup:
        await c.oneTapSetup();
      case BotAction.update:
        await c.openUpdatePage();
      case BotAction.store:
        widget.onOpenTab?.call(_storeTab);
      case BotAction.changeRequest:
        if (!mounted) return;
        await Navigator.of(context).push(MaterialPageRoute(builder: (_) => ChangeRequestScreen(controller: c)));
    }
  }

  /// ההודעה שנפתחת בוואטסאפ: מזהה המכשיר ומספר הטלפון — כדי שהמנהל (והבוט) ידעו מיד במי מדובר.
  String get _waMessage {
    final d = c.device;
    final phone = c.customerPhone;
    return [
      'שלום, אני צריך עזרה ב-FilterPhone.',
      if (d != null) 'מכשיר #${d.id}${d.name.isNotEmpty ? ' · ${d.name}' : ''}',
      if (phone.isNotEmpty) 'טלפון: $phone',
      if (c.androidId.isNotEmpty) 'מזהה: ${c.androidId}',
      'VPN: ${c.vpn.isUp ? 'מחובר' : 'מנותק'}',
    ].join('\n');
  }

  /// פותח את הוואטסאפ של המנהל עם ההודעה המוכנה.
  Future<void> _openWhatsApp() async {
    final message = _waMessage;
    try {
      await c.native.openSupportWhatsApp(c.supportWhatsApp, message);
    } on PlatformException catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('לא ניתן לפתוח WhatsApp: ${e.message ?? e.code}')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final hasWhatsApp = c.supportWhatsApp.trim().isNotEmpty;
    final mode = hasWhatsApp ? _mode : _SupportMode.app;
    final inApp = mode == _SupportMode.app;
    return _SectionScaffold(
      title: 'שירות ותמיכה',
      child: Column(
        children: [
          // הבחירה למעלה: קוביות זו לצד זו (כמו "עוזר מיידי | נציג" פעם)
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
            child: IntrinsicHeight(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Expanded(
                    child: _ModeTile(
                      icon: Icons.support_agent_rounded,
                      label: 'תמיכה באפליקציה',
                      selected: inApp,
                      badge: c.supportUnread,
                      onTap: () => _setMode(_SupportMode.app),
                    ),
                  ),
                  if (hasWhatsApp) ...[
                    const SizedBox(width: 8),
                    Expanded(
                      child: _ModeTile(
                        icon: Icons.chat_rounded,
                        label: 'תמיכה בוואטסאפ',
                        selected: mode == _SupportMode.whatsapp,
                        color: _waGreen,
                        onTap: () => _setMode(_SupportMode.whatsapp),
                      ),
                    ),
                  ],
                  if (c.isAdminDevice) ...[
                    const SizedBox(width: 8),
                    Expanded(
                      child: _ModeTile(
                        icon: Icons.inbox_rounded,
                        label: 'ניהול תמיכה',
                        selected: false,
                        badge: c.adminSupportUnread,
                        color: Theme.of(context).colorScheme.tertiary,
                        onTap: _openConsole,
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ),
          Expanded(child: inApp ? _chat(context) : _whatsAppView(context)),
          if (inApp && c.canChatWithSupport)
            _ChatComposer(
              controller: _text,
              busy: c.supportSending,
              hint: 'כתבו הודעה למנהל...',
              onSend: _send,
            ),
        ],
      ),
    );
  }

  /// תמיכה בוואטסאפ: מה יישלח, וכפתור אחד שפותח את השיחה.
  Widget _whatsAppView(BuildContext context) {
    final theme = Theme.of(context);
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
      children: [
        Container(
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: _waGreen.withValues(alpha: .08),
            borderRadius: BorderRadius.circular(18),
            border: Border.all(color: _waGreen.withValues(alpha: .25)),
          ),
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Row(children: [
              const Icon(Icons.chat_rounded, color: _waGreen, size: 28),
              const SizedBox(width: 10),
              Expanded(
                child: Text('שיחה עם המנהל בוואטסאפ',
                    style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w800)),
              ),
            ]),
            const SizedBox(height: 8),
            Text('הפרטים של הטלפון כבר מוכנים בהודעה — רק לשלוח:',
                style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
            const SizedBox(height: 10),
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: theme.colorScheme.surface,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Text(_waMessage, style: theme.textTheme.bodyMedium),
            ),
            const SizedBox(height: 14),
            FilledButton.icon(
              style: FilledButton.styleFrom(backgroundColor: _waGreen),
              onPressed: _openWhatsApp,
              icon: const Icon(Icons.open_in_new_rounded),
              label: const Text('לחצו לפתיחת השיחה בוואטסאפ'),
            ),
          ]),
        ),
      ],
    );
  }

  Widget _chat(BuildContext context) {
    final theme = Theme.of(context);
    final thread = c.supportThread;
    return ListView(
      controller: _scroll,
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
      children: [
        _QuickCheck(controller: c, onAction: _run),
        const SizedBox(height: 12),
        if (!c.canChatWithSupport)
          Padding(
            padding: const EdgeInsets.all(24),
            child: Text('תמיכה באפליקציה זמינה אחרי ההרשמה. בינתיים אפשר לפנות בוואטסאפ.',
                textAlign: TextAlign.center, style: theme.textTheme.titleMedium),
          )
        else ...[
          Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: theme.colorScheme.primaryContainer.withValues(alpha: .55),
              borderRadius: BorderRadius.circular(16),
            ),
            child: Row(
              children: [
                const Icon(Icons.support_agent),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    'כתבו כאן וההודעה מגיעה למנהל (ואם הוא לא זמין — נחזור אליכם). '
                    'מצב הסינון מצורף אוטומטית, כדי שלא נצטרך לשאול.',
                    style: theme.textTheme.bodySmall,
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 10),
          if (thread == null)
            const Padding(padding: EdgeInsets.all(24), child: Center(child: CircularProgressIndicator()))
          else if (thread.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 24),
              child: Text('עוד אין הודעות. כתבו את השאלה למטה 👇',
                  textAlign: TextAlign.center, style: theme.textTheme.bodyMedium),
            )
          else
            for (final m in thread) ...[
              _ChatBubble(text: m.text, mine: !m.fromSupport, time: m.time, author: m.fromSupport ? 'מנהל' : null),
              const SizedBox(height: 10),
            ],
        ],
      ],
    );
  }
}

/// קובייה בבחירה שלמעלה: אייקון, שם, ומספר ממתינות (אם יש).
class _ModeTile extends StatelessWidget {
  const _ModeTile({
    required this.icon,
    required this.label,
    required this.selected,
    required this.onTap,
    this.badge = 0,
    this.color,
  });
  final IconData icon;
  final String label;
  final bool selected;
  final VoidCallback onTap;
  final int badge;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final accent = color ?? cs.primary;
    return Material(
      color: selected ? accent.withValues(alpha: .14) : cs.surfaceContainerLow,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: BorderSide(color: selected ? accent : cs.outlineVariant, width: selected ? 1.6 : 1),
      ),
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 12),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Badge(
              isLabelVisible: badge > 0,
              label: Text('$badge'),
              child: Icon(icon, color: accent, size: 26),
            ),
            const SizedBox(height: 6),
            Text(label,
                textAlign: TextAlign.center,
                maxLines: 2,
                style: theme.textTheme.labelLarge?.copyWith(
                    fontWeight: selected ? FontWeight.w800 : FontWeight.w600,
                    color: selected ? accent : cs.onSurface)),
          ]),
        ),
      ),
    );
  }
}

/// "בדיקה מהירה": מה לא תקין בטלפון עכשיו — עם כפתור תיקון לכל דבר.
class _QuickCheck extends StatelessWidget {
  const _QuickCheck({required this.controller, required this.onAction});
  final AppController controller;
  final Future<void> Function(BotAction) onAction;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final c = controller;
    final ctx = c.botContext;
    final problems = <(String, BotAction)>[
      if (ctx.active && !ctx.paused && !ctx.vpnUp) ('הסינון (VPN) מנותק', BotAction.reconnectVpn),
      if (ctx.active && !ctx.caInstalled) ('תעודת הסינון לא מותקנת', BotAction.setup),
      if (ctx.guardNeeded && !ctx.guardOn) ('מגן FilterPhone כבוי', BotAction.openGuard),
      if (!ctx.batteryOk) ('אנדרואיד מגביל את FilterPhone ברקע', BotAction.battery),
      if (ctx.updateAvailable) ('יש גרסה חדשה', BotAction.update),
    ];
    final ok = problems.isEmpty;
    final color = ok ? StatusColors.ok(context) : StatusColors.bad(context);
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: color.withValues(alpha: .08),
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: color.withValues(alpha: .3)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(ok ? Icons.verified_rounded : Icons.error_outline_rounded, color: color),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  ok
                      ? (ctx.paused ? 'ההגנה מושהית באישור המנהל' : 'בדיקה מהירה: הכול תקין ✓')
                      : 'בדיקה מהירה: ${problems.length == 1 ? 'נמצא דבר אחד לתקן' : 'נמצאו ${problems.length} דברים לתקן'}',
                  style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w800),
                ),
              ),
            ],
          ),
          for (final (label, action) in problems)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Row(
                children: [
                  Expanded(child: Text(label, style: theme.textTheme.bodyMedium)),
                  const SizedBox(width: 8),
                  FilledButton.tonal(
                    onPressed: c.busy ? null : () => onAction(action),
                    child: const Text('תיקון'),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

class _ChatComposer extends StatelessWidget {
  const _ChatComposer({required this.controller, required this.onSend, required this.hint, this.busy = false});
  final TextEditingController controller;
  final Future<void> Function() onSend;
  final String hint;
  final bool busy;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 6, 12, 10),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Expanded(
              child: TextField(
                controller: controller,
                minLines: 1,
                maxLines: 4,
                maxLength: 2000,
                buildCounter: (_, {required currentLength, required isFocused, maxLength}) => null,
                textInputAction: TextInputAction.newline,
                decoration: InputDecoration(hintText: hint, prefixIcon: const Icon(Icons.message_outlined)),
              ),
            ),
            const SizedBox(width: 8),
            Material(
              color: theme.colorScheme.primary,
              borderRadius: BorderRadius.circular(16),
              child: InkWell(
                onTap: busy ? null : () async => onSend(),
                borderRadius: BorderRadius.circular(16),
                child: SizedBox(
                  width: 54,
                  height: 54,
                  child: busy
                      ? const Padding(
                          padding: EdgeInsets.all(16),
                          child: CircularProgressIndicator(strokeWidth: 2.4, color: Colors.white),
                        )
                      : const Icon(Icons.send_rounded, color: Colors.white),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ChatBubble extends StatelessWidget {
  const _ChatBubble({required this.text, required this.mine, this.time, this.author});
  final String text;
  final bool mine;
  final DateTime? time;
  final String? author;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final bg = mine ? theme.colorScheme.primaryContainer : theme.colorScheme.surfaceContainerHighest;
    final meta = [
      ?author,
      if (time != null) '${time!.hour.toString().padLeft(2, '0')}:${time!.minute.toString().padLeft(2, '0')}',
    ].join(' · ');
    return Align(
      alignment: mine ? AlignmentDirectional.centerEnd : AlignmentDirectional.centerStart,
      child: Container(
        constraints: const BoxConstraints(maxWidth: 340),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
        decoration: BoxDecoration(
          color: bg,
          borderRadius: BorderRadiusDirectional.only(
            topStart: const Radius.circular(18),
            topEnd: const Radius.circular(18),
            bottomStart: Radius.circular(mine ? 18 : 5),
            bottomEnd: Radius.circular(mine ? 5 : 18),
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            SelectableText(text),
            if (meta.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text(meta, style: theme.textTheme.labelSmall?.copyWith(color: theme.colorScheme.outline)),
            ],
          ],
        ),
      ),
    );
  }
}
