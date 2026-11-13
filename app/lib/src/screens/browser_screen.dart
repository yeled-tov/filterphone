part of 'app_shell.dart';

/// לשונית "סייר מוגן": פתיחת הסייר (חלון משלו עם דף בית, אתרים מוצמדים, כרטיסיות והיסטוריה — וסמל משלו
/// במסך הבית), חיפוש מהיר וקביעה כדפדפן ברירת המחדל. הסייר עצמו (BrowserActivity באנדרואיד) עובר את כל הסינון,
/// ונעול כש-FilterPhone לא מגן על הטלפון. בטלפון בלי גלישה: רק האתרים המאושרים, או הסבר שהסייר כבוי.
class BrowserScreen extends StatefulWidget {
  const BrowserScreen({super.key, required this.controller, this.visible = true, this.onOpenTab});
  final AppController controller;

  /// הלשונית מוצגת עכשיו — רק אז בודקים מחדש (מצב / דפדפן ברירת מחדל אחרי חלון המערכת).
  final bool visible;
  final void Function(int tab)? onOpenTab;

  @override
  State<BrowserScreen> createState() => _BrowserScreenState();
}

class _BrowserScreenState extends State<BrowserScreen> {
  static const _profileTab = 3;
  final _search = TextEditingController();
  SafeBrowserInfo? _info;
  int _seenResume = -1;
  String _filter = '';

  AppController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    c.addListener(_onController);
    _load();
  }

  @override
  void didUpdateWidget(BrowserScreen old) {
    super.didUpdateWidget(old);
    if (widget.visible && !old.visible) _load();
  }

  @override
  void dispose() {
    c.removeListener(_onController);
    _search.dispose();
    super.dispose();
  }

  /// חזרנו לאפליקציה (אולי מחלון "דפדפן ברירת מחדל", או שהמדיניות השתנתה) — בודקים שוב.
  void _onController() {
    if (c.resumeTick != _seenResume && widget.visible) _load();
  }

  Future<void> _load() async {
    _seenResume = c.resumeTick;
    SafeBrowserInfo info;
    try {
      info = await c.native.safeBrowser();
    } on Exception catch (e) {
      debugPrint('safe browser: $e');
      info = SafeBrowserInfo.off;
    }
    if (mounted) setState(() => _info = info);
  }

  Future<void> _open({String? url, String? query}) async {
    final ok = await c.native.openSafeBrowser(url: url, query: query);
    if (!ok && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('לא הצלחנו לפתוח את הסייר')));
    }
  }

  void _submit(String text) {
    final t = text.trim();
    if (t.isEmpty) return;
    final info = _info;
    if (info == null) return;
    // באתרים מאושרים בלבד החיפוש מחפש בתוכם (הסייר עצמו מגביל) — כתובת נפתחת, והסייר בודק שהיא מותרת
    FocusScope.of(context).unfocus();
    unawaited(_open(query: t));
  }

  @override
  Widget build(BuildContext context) {
    final info = _info;
    return _SectionScaffold(
      title: 'סייר מוגן',
      child: info == null
          ? const Center(child: CircularProgressIndicator())
          : info.enabled
              ? _body(context, info)
              : _offState(context),
    );
  }

  Widget _offState(BuildContext context) {
    final theme = Theme.of(context);
    return ListView(
      padding: const EdgeInsets.fromLTRB(24, 48, 24, 24),
      children: [
        Icon(Icons.public_off_rounded, size: 64, color: theme.colorScheme.onSurfaceVariant),
        const SizedBox(height: 16),
        Text('הסייר המוגן לא פעיל בטלפון הזה',
            textAlign: TextAlign.center, style: theme.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w800)),
        const SizedBox(height: 8),
        Text('גלישה באינטרנט לא פתוחה בטלפון הזה. אם צריך אתר חיוני (בנק, קופת חולים, ממשלה) — אפשר לבקש מהמנהל.',
            textAlign: TextAlign.center, style: theme.textTheme.bodyMedium),
        const SizedBox(height: 20),
        OutlinedButton.icon(
          onPressed: () => widget.onOpenTab?.call(_profileTab),
          icon: const Icon(Icons.shield_rounded),
          label: const Text('למצב ההגנה'),
        ),
      ],
    );
  }

  Widget _body(BuildContext context, SafeBrowserInfo info) {
    final theme = Theme.of(context);
    final q = _filter.trim();
    final matches = q.length < 2
        ? const <SafeSite>[]
        : info.sites.where((s) => s.title.contains(q) || s.url.contains(q)).take(5).toList();
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 28),
        children: [
          _openCard(context, info),
          const SizedBox(height: 14),
          TextField(
            controller: _search,
            textInputAction: TextInputAction.search,
            keyboardType: TextInputType.url,
            onChanged: (v) => setState(() => _filter = v),
            onSubmitted: _submit,
            decoration: InputDecoration(
              prefixIcon: const Icon(Icons.search_rounded),
              hintText: info.full ? 'חיפוש או כתובת אתר' : 'מה מחפשים? למשל: חידוש תו נכה',
              suffixIcon: _filter.isEmpty
                  ? null
                  : IconButton(
                      tooltip: 'ניקוי',
                      icon: const Icon(Icons.close_rounded),
                      onPressed: () => setState(() {
                        _search.clear();
                        _filter = '';
                      }),
                    ),
            ),
          ),
          for (final s in matches)
            ListTile(
              contentPadding: const EdgeInsets.symmetric(horizontal: 8),
              leading: CircleAvatar(
                radius: 18,
                backgroundColor: theme.colorScheme.primaryContainer,
                child: Text(s.title.isEmpty ? '•' : s.title.characters.first,
                    style: TextStyle(color: theme.colorScheme.primary, fontWeight: FontWeight.w800)),
              ),
              title: Text(s.title, maxLines: 1, overflow: TextOverflow.ellipsis),
              subtitle: Text(s.group, maxLines: 1, overflow: TextOverflow.ellipsis),
              onTap: () => _open(url: s.url),
            ),
          if (q.isNotEmpty)
            ListTile(
              contentPadding: const EdgeInsets.symmetric(horizontal: 8),
              leading: const Icon(Icons.travel_explore_rounded),
              title: Text(info.full ? 'חיפוש: $q' : 'חיפוש באתרים המאושרים: $q',
                  maxLines: 1, overflow: TextOverflow.ellipsis),
              onTap: () => _submit(q),
            ),
          const SizedBox(height: 12),
          if (!info.full)
            _note(context, Icons.info_outline_rounded,
                'בטלפון הזה פתוחים רק אתרים מאושרים (ממשלה, בנקים, אשראי, קופות חולים) — והחיפוש מחפש בתוכם.'),
          if (!info.isDefault) _defaultCard(context) else
            _note(context, Icons.check_circle_rounded, 'הסייר המוגן הוא דפדפן ברירת המחדל — קישורים מאפליקציות נפתחים בו.',
                color: StatusColors.ok(context)),
        ],
      ),
    );
  }

  /// הסייר עצמו (דף הבית, האתרים המוצמדים, כרטיסיות, היסטוריה) — חלון משלו, עם סמל משלו במסך הבית.
  Widget _openCard(BuildContext context, SafeBrowserInfo info) {
    final theme = Theme.of(context);
    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () => _open(),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 16),
          child: Row(children: [
            Container(
              width: 52,
              height: 52,
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(14),
                gradient: const LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: [Color(0xFF36B0F2), Color(0xFF0A5A97)],
                ),
              ),
              child: const Icon(Icons.public_rounded, color: Colors.white, size: 30),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('פתיחת הסייר המוגן', style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w800)),
                const SizedBox(height: 2),
                Text(
                    info.full
                        ? 'גלישה מסוננת: האתרים שלך, כרטיסיות והיסטוריה. יש לו סמל משלו במסך הבית.'
                        : 'האתרים המאושרים, חיפוש בתוכם, כרטיסיות והיסטוריה. יש לו סמל משלו במסך הבית.',
                    style: theme.textTheme.bodySmall),
              ]),
            ),
            const Icon(Icons.chevron_left_rounded),
          ]),
        ),
      ),
    );
  }

  Widget _note(BuildContext context, IconData icon, String text, {Color? color}) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(children: [
        Icon(icon, size: 18, color: color ?? theme.colorScheme.onSurfaceVariant),
        const SizedBox(width: 8),
        Expanded(child: Text(text, style: theme.textTheme.bodySmall)),
      ]),
    );
  }

  Widget _defaultCard(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(children: [
              Icon(Icons.open_in_browser_rounded, color: theme.colorScheme.primary),
              const SizedBox(width: 8),
              Expanded(
                child: Text('דפדפן ברירת המחדל',
                    style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w800)),
              ),
            ]),
            const SizedBox(height: 6),
            Text('קישורים מוואטסאפ, מהודעות ומאפליקציות אחרות ייפתחו ישר בסייר המוגן.',
                style: theme.textTheme.bodySmall),
            const SizedBox(height: 10),
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: FilledButton.tonal(
                onPressed: () async {
                  await c.native.requestDefaultBrowser();
                },
                child: const Text('קביעה כדפדפן ברירת מחדל'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
