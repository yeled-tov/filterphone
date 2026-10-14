part of 'app_shell.dart';

/// לשונית התמיכה: עוזר אוטומטי (עונה מיד, גם בלי אינטרנט, לפי המצב האמיתי של הטלפון) ושיחה עם נציג
/// (נשמרת בשרת; המנהל עונה מהממשק או מהוואטסאפ שלו, והתשובה מגיעה לכאן + התראה).
class SupportScreen extends StatefulWidget {
  const SupportScreen({super.key, required this.controller, this.visible = true, this.onOpenTab});
  final AppController controller;

  /// הלשונית מוצגת עכשיו — רק אז מרעננים את השיחה עם הנציג.
  final bool visible;
  final void Function(int tab)? onOpenTab;

  @override
  State<SupportScreen> createState() => _SupportScreenState();
}

enum _Mode { bot, human }

class _BotLine {
  const _BotLine({required this.fromBot, required this.text, this.actions = const []});
  final bool fromBot;
  final String text;
  final List<BotAction> actions;
}

class _SupportScreenState extends State<SupportScreen> {
  static const _poll = Duration(seconds: 8);
  static const _storeTab = 1;

  final _text = TextEditingController();
  final _scroll = ScrollController();
  final List<_BotLine> _bot = [
    const _BotLine(
      fromBot: true,
      text: 'שלום 👋 אני העוזר של FilterPhone. אני רואה את מצב הסינון בטלפון ויכול לעזור מיד — '
          'גם כשאין אינטרנט. במה אפשר לעזור?',
    ),
  ];
  _Mode _mode = _Mode.bot;
  String _topic = '';
  Timer? _timer;

  AppController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    if (c.supportUnread > 0) _mode = _Mode.human;
    if (widget.visible) _onShown();
  }

  @override
  void didUpdateWidget(SupportScreen old) {
    super.didUpdateWidget(old);
    if (widget.visible && !old.visible) {
      if (c.supportUnread > 0) _mode = _Mode.human;
      _onShown();
    } else if (!widget.visible && old.visible) {
      _timer?.cancel();
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    _text.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _onShown() {
    if (_mode == _Mode.human) _startHuman();
  }

  void _startHuman() {
    if (!c.canChatWithSupport) return;
    unawaited(c.clearSupportNotification());
    unawaited(c.loadSupport().then((_) => _toBottom()));
    _timer?.cancel();
    _timer = Timer.periodic(_poll, (_) {
      if (mounted && widget.visible && _mode == _Mode.human) c.loadSupport();
    });
  }

  void _setMode(_Mode m) {
    if (m == _mode) return;
    setState(() => _mode = m);
    if (m == _Mode.human) {
      _startHuman();
    } else {
      _timer?.cancel();
    }
    _toBottom();
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
    if (_mode == _Mode.bot) {
      _text.clear();
      final reply = SupportBot.answer(value, c.botContext);
      if (reply.topic.isNotEmpty && reply.topic != 'other') _topic = reply.topic;
      setState(() {
        _bot.add(_BotLine(fromBot: false, text: value));
        _bot.add(_BotLine(fromBot: true, text: reply.text, actions: reply.actions));
      });
      await _toBottom();
      return;
    }
    final error = await c.sendSupport(value, topic: _topic);
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
        _setMode(_Mode.human);
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

  Future<void> _openWhatsApp() async {
    final d = c.device;
    final message = [
      'שלום, אני צריך עזרה ב-FilterPhone.',
      if (d != null) 'מכשיר #${d.id} · ${d.name}',
      'VPN: ${c.vpn.isUp ? 'מחובר' : 'מנותק'}',
    ].join('\n');
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
    return _SectionScaffold(
      title: 'שירות ותמיכה',
      actions: [
        if (hasWhatsApp)
          IconButton(tooltip: 'וואטסאפ', onPressed: _openWhatsApp, icon: const Icon(Icons.chat_rounded)),
      ],
      child: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
            child: SegmentedButton<_Mode>(
              showSelectedIcon: false,
              segments: [
                const ButtonSegment(value: _Mode.bot, icon: Icon(Icons.bolt_rounded), label: Text('עוזר מיידי')),
                ButtonSegment(
                  value: _Mode.human,
                  icon: Badge(
                    isLabelVisible: c.supportUnread > 0,
                    label: Text('${c.supportUnread}'),
                    child: const Icon(Icons.support_agent),
                  ),
                  label: const Text('נציג'),
                ),
              ],
              selected: {_mode},
              onSelectionChanged: (s) => _setMode(s.first),
            ),
          ),
          Expanded(
            child: _mode == _Mode.bot ? _botView(context) : _humanView(context, hasWhatsApp),
          ),
          if (_mode == _Mode.bot || c.canChatWithSupport)
            _ChatComposer(
              controller: _text,
              busy: _mode == _Mode.human && c.supportSending,
              hint: _mode == _Mode.bot ? 'שאלו את העוזר...' : 'הודעה לנציג...',
              onSend: _send,
            ),
        ],
      ),
    );
  }

  Widget _botView(BuildContext context) {
    return ListView(
      controller: _scroll,
      padding: const EdgeInsets.fromLTRB(16, 10, 16, 12),
      children: [
        _QuickCheck(controller: c, onAction: _run),
        const SizedBox(height: 12),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final q in SupportBot.quick) ActionChip(label: Text(q), onPressed: () => _send(q)),
          ],
        ),
        const SizedBox(height: 14),
        for (final line in _bot) ...[
          _ChatBubble(text: line.text, mine: !line.fromBot),
          if (line.actions.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Wrap(
                spacing: 8,
                runSpacing: 6,
                children: [for (final a in line.actions) _actionButton(a)],
              ),
            ),
          const SizedBox(height: 10),
        ],
      ],
    );
  }

  Widget _actionButton(BotAction a) {
    final (label, icon) = switch (a) {
      BotAction.reconnectVpn => ('חיבור מחדש', Icons.vpn_lock_rounded),
      BotAction.openGuard => ('הפעלת המגן', Icons.shield_rounded),
      BotAction.battery => ('סוללה ללא הגבלה', Icons.battery_charging_full_rounded),
      BotAction.setup => ('הקמה אוטומטית', Icons.auto_fix_high_rounded),
      BotAction.changeRequest => ('בקשת שינוי', Icons.edit_note_rounded),
      BotAction.store => ('לאפליקציות', Icons.apps_rounded),
      BotAction.update => ('עדכון', Icons.system_update_rounded),
      BotAction.human => ('לדבר עם נציג', Icons.support_agent_rounded),
    };
    return a == BotAction.human
        ? OutlinedButton.icon(onPressed: () => _run(a), icon: Icon(icon, size: 18), label: Text(label))
        : FilledButton.tonalIcon(onPressed: () => _run(a), icon: Icon(icon, size: 18), label: Text(label));
  }

  Widget _humanView(BuildContext context, bool hasWhatsApp) {
    final theme = Theme.of(context);
    if (!c.canChatWithSupport) {
      return ListView(
        padding: const EdgeInsets.all(24),
        children: [
          Text('שיחה עם נציג זמינה אחרי ההרשמה.', style: theme.textTheme.titleMedium, textAlign: TextAlign.center),
          if (hasWhatsApp) ...[
            const SizedBox(height: 16),
            FilledButton.icon(onPressed: _openWhatsApp, icon: const Icon(Icons.chat_rounded), label: const Text('פנייה בוואטסאפ')),
          ],
        ],
      );
    }
    final thread = c.supportThread;
    return ListView(
      controller: _scroll,
      padding: const EdgeInsets.fromLTRB(16, 10, 16, 12),
      children: [
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
                  'כתבו כאן — ההודעה מגיעה לנציג, והתשובה תופיע כאן (ותקבלו התראה). '
                  'מצב הסינון בטלפון מצורף אוטומטית, כדי שלא נצטרך לשאול.',
                  style: theme.textTheme.bodySmall,
                ),
              ),
            ],
          ),
        ),
        if (hasWhatsApp)
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: TextButton.icon(
              onPressed: _openWhatsApp,
              icon: const Icon(Icons.chat_rounded, size: 18),
              label: const Text('מעדיפים וואטסאפ? לחצו כאן'),
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
            _ChatBubble(text: m.text, mine: !m.fromSupport, time: m.time, author: m.fromSupport ? 'נציג' : null),
            const SizedBox(height: 10),
          ],
      ],
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
