part of 'app_shell.dart';

/// לשונית "סייר מוגן": חיפוש למעלה, האתרים החיוניים (ממשלה, בנקים, אשראי, קופות חולים) וקביעה
/// כדפדפן ברירת המחדל. הדפדפן עצמו (BrowserActivity באנדרואיד) עובר את כל הסינון — כמו Chrome.
/// בטלפון בלי גלישה: רק האתרים החיוניים (אם המנהל הפעיל), או הסבר שהסייר כבוי.
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
  static const _groupIcons = {
    'ממשלה ושירותים': Icons.account_balance_rounded,
    'בנקים': Icons.savings_rounded,
    'כרטיסי אשראי': Icons.credit_card_rounded,
    'קופות חולים ובריאות': Icons.local_hospital_rounded,
  };

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
    // אתרים חיוניים בלבד: חיפוש = סינון הרשימה; כתובת (gov.il, ...) — נפתחת, והסייר בודק שהיא מותרת
    if (!info.full && !t.contains('.')) return;
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
    final sites = q.isEmpty ? info.sites : info.sites.where((s) => s.title.contains(q) || s.url.contains(q)).toList();
    final groups = <String, List<SafeSite>>{};
    for (final s in sites) {
      groups.putIfAbsent(s.group, () => []).add(s);
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 28),
        children: [
          TextField(
            controller: _search,
            textInputAction: info.full ? TextInputAction.search : TextInputAction.go,
            keyboardType: TextInputType.url,
            onChanged: (v) => setState(() => _filter = v),
            onSubmitted: _submit,
            decoration: InputDecoration(
              prefixIcon: const Icon(Icons.search_rounded),
              hintText: info.full ? 'חיפוש או כתובת אתר' : 'חיפוש באתרים החיוניים',
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
          const SizedBox(height: 12),
          if (!info.full)
            _note(context, Icons.info_outline_rounded,
                'בטלפון הזה פתוחים רק האתרים החיוניים שברשימה — בלי חיפוש באינטרנט.'),
          if (!info.isDefault) _defaultCard(context) else
            _note(context, Icons.check_circle_rounded, 'הסייר המוגן הוא דפדפן ברירת המחדל — קישורים מאפליקציות נפתחים בו.',
                color: StatusColors.ok(context)),
          if (info.full && q.isNotEmpty)
            Card(
              child: ListTile(
                leading: const Icon(Icons.travel_explore_rounded),
                title: Text('חיפוש: $q', maxLines: 1, overflow: TextOverflow.ellipsis),
                trailing: const Icon(Icons.chevron_left_rounded),
                onTap: () => _submit(q),
              ),
            ),
          for (final entry in groups.entries) ...[
            const SizedBox(height: 14),
            Row(children: [
              Icon(_groupIcons[entry.key] ?? Icons.public_rounded, size: 20, color: theme.colorScheme.primary),
              const SizedBox(width: 8),
              Text(entry.key, style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w800)),
            ]),
            const SizedBox(height: 8),
            GridView.count(
              crossAxisCount: 3,
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              mainAxisSpacing: 8,
              crossAxisSpacing: 8,
              childAspectRatio: 1.05,
              children: [for (final s in entry.value) _SiteTile(site: s, onTap: () => _open(url: s.url))],
            ),
          ],
          if (sites.isEmpty && q.isNotEmpty && !info.full)
            Padding(
              padding: const EdgeInsets.only(top: 24),
              child: Text('לא נמצא אתר כזה ברשימה', textAlign: TextAlign.center, style: theme.textTheme.bodyMedium),
            ),
        ],
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

class _SiteTile extends StatelessWidget {
  const _SiteTile({required this.site, required this.onTap});
  final SafeSite site;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final letter = site.title.isEmpty ? '•' : site.title.characters.first;
    return Material(
      color: theme.colorScheme.surfaceContainerHigh,
      borderRadius: BorderRadius.circular(16),
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 10),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              CircleAvatar(
                radius: 20,
                backgroundColor: theme.colorScheme.primaryContainer,
                child: Text(letter,
                    style: TextStyle(color: theme.colorScheme.primary, fontWeight: FontWeight.w800, fontSize: 18)),
              ),
              const SizedBox(height: 8),
              Text(site.title,
                  textAlign: TextAlign.center,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.labelMedium?.copyWith(fontWeight: FontWeight.w700)),
            ],
          ),
        ),
      ),
    );
  }
}
