part of 'app_shell.dart';

/// מקור אמת אחד למצב ההתקנה של אפליקציות החנות (גרסה מותקנת + installing/installed/failed),
/// והפעולות: התקנה (ישירה / דרך Google Play), הסרה, פתיחה ובקשה מהמנהל. הגריד ועמוד האפליקציה
/// שניהם קוראים ממנו — כך שלחיצה בעמוד מתעדכנת מיד גם בגריד.
class StoreController extends ChangeNotifier {
  StoreController(this.c);
  final AppController c;

  Map<String, Map<String, Object?>> _states = {};
  Timer? _poll;
  bool _disposed = false;

  List<StoreApp> get apps => c.device?.storeApps ?? const [];

  int? installedVersion(StoreApp a) => (_states[a.package]?['installed_version'] as num?)?.toInt();
  String? state(StoreApp a) => _states[a.package]?['state'] as String?;
  bool installed(StoreApp a) => installedVersion(a) != null;
  bool installing(StoreApp a) => state(a) == 'installing';
  bool hasUpdate(StoreApp a) {
    final v = installedVersion(a);
    return v != null && a.versionCode != null && v < a.versionCode!;
  }

  bool isFresh(StoreApp a) {
    final now = DateTime.now();
    return (a.addedAt != null && now.difference(a.addedAt!).inDays < 30) ||
        (a.updatedAt != null && now.difference(a.updatedAt!).inDays < 14);
  }

  @override
  void dispose() {
    _disposed = true;
    _poll?.cancel();
    super.dispose();
  }

  Future<void> loadStates() async {
    final pkgs = apps.map((a) => a.package).toList();
    if (pkgs.isEmpty) return;
    try {
      final states = await c.native.storeAppStates(pkgs);
      if (_disposed) return;
      _states = states;
      notifyListeners();
      _poll?.cancel();
      if (states.values.any((s) => s['state'] == 'installing')) {
        _poll = Timer(const Duration(seconds: 2), loadStates);
      }
    } on PlatformException catch (e) {
      debugPrint('store states failed: $e');
    }
  }

  Future<void> reload() async {
    await c.refresh(forceSync: true);
    await loadStates();
  }

  /// מתקין (ישיר דרך מנהל המכשיר, או דרך Google Play). מחזיר הודעה למשתמש.
  Future<String> install(StoreApp a) async {
    if (a.viaPlay && !(c.deviceOwner && a.directInstall)) {
      final ok = await c.native.openPlay(a.package, a.playUrl, titles: [...a.playTitles, a.label]);
      return ok ? 'נפתח ב-Google Play להתקנת ${a.label}' : 'לא ניתן לפתוח את Google Play';
    }
    try {
      final r = await c.native.installStoreApp(a.raw);
      if (r == 'not_owner') return await request(a);
      final msg = switch (r) {
        'started' => 'מתקין את ${a.label}…',
        'up_to_date' => '${a.label} כבר מעודכנת',
        'busy' => 'התקנה אחרת בתהליך — נסו שוב בעוד רגע',
        _ => 'לא ניתן להתקין את ${a.label} כרגע',
      };
      await loadStates();
      return msg;
    } on PlatformException catch (e) {
      return 'ההתקנה נכשלה: ${e.message ?? e.code}';
    }
  }

  Future<String> uninstall(StoreApp a) async {
    try {
      final r = await c.native.uninstallApp(a.package);
      final msg = switch (r) {
        'started' => 'מסיר את ${a.label}…',
        'dialog' => 'אשרו את ההסרה',
        'not_installed' => '${a.label} אינה מותקנת',
        _ => 'לא ניתן להסיר כרגע',
      };
      await loadStates();
      return msg;
    } on PlatformException catch (e) {
      return 'ההסרה נכשלה: ${e.message ?? e.code}';
    }
  }

  Future<void> open(StoreApp a) async {
    try {
      await c.native.openApp(a.package);
    } on PlatformException catch (_) {}
  }

  /// בקשת אפליקציה מהמנהל (כשאין מנהל מכשיר / אין קובץ). note חופשי, או לפי האפליקציה.
  Future<String> request(StoreApp? a, {String? note}) async {
    if (!(c.device?.isActive ?? false)) return 'החנות זמינה לאחר שהמכשיר פעיל ומאושר.';
    final text = note ?? (a == null ? '' : 'בקשת התקנת אפליקציה: ${a.label} (${a.package})');
    if (text.trim().isEmpty) return '';
    final ok = await c.requestChange(note: text);
    return ok ? 'הבקשה נשלחה למנהל. נעדכן כשהיא תטופל.' : (c.error ?? 'לא הצלחנו לשלוח את הבקשה.');
  }
}

/// כפתור הפעולה המתאים למצב האפליקציה (התקנה / עדכון / פתיחה / בקשה / מתקין…).
enum _ActionKind { install, update, open, request, installing }

_ActionKind _actionFor(StoreController s, StoreApp a) {
  if (s.installing(a)) return _ActionKind.installing;
  final canInstall = (s.c.deviceOwner && a.directInstall) || a.viaPlay;
  if (s.hasUpdate(a) && (s.c.deviceOwner && a.directInstall)) return _ActionKind.update;
  if (s.installed(a)) return _ActionKind.open;
  return canInstall ? _ActionKind.install : _ActionKind.request;
}

const _allTab = 'הכול';

enum _StoreView { apps, updates }

/// חנות FilterPhone — כמו חנות אמיתית: טאבים של קטגוריות מוצמדים למעלה, אריחים מרובעים,
/// ולחיצה פותחת עמוד אפליקציה. בטאב "הכול" — שורה לכל קטגוריה.
class AppStoreScreen extends StatefulWidget {
  const AppStoreScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<AppStoreScreen> createState() => _AppStoreScreenState();
}

class _AppStoreScreenState extends State<AppStoreScreen> {
  late final StoreController _store = StoreController(widget.controller);
  _StoreView _view = _StoreView.apps;
  String _category = _allTab;
  String _query = '';
  final _search = TextEditingController();

  AppController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    c.addListener(_onController);
    _store.addListener(_onStore);
    _store.loadStates();
  }

  @override
  void dispose() {
    c.removeListener(_onController);
    _store.removeListener(_onStore);
    _store.dispose();
    _search.dispose();
    super.dispose();
  }

  void _onController() => _store.loadStates();
  void _onStore() {
    if (mounted) setState(() {});
  }

  Future<void> _toast(Future<String> action) async {
    final messenger = ScaffoldMessenger.of(context);
    final msg = await action;
    if (msg.isNotEmpty && mounted) messenger.showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _requestCustom() async {
    final input = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('בקשת אפליקציה'),
        content: TextField(
          controller: input,
          autofocus: true,
          decoration: const InputDecoration(hintText: 'שם האפליקציה (ולמה היא נחוצה)'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('ביטול')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('שליחה')),
        ],
      ),
    );
    if (ok == true && input.text.trim().isNotEmpty) {
      await _toast(_store.request(null, note: 'בקשת אפליקציה חדשה לחנות: ${input.text.trim()}'));
    }
  }

  void _openDetail(StoreApp a) {
    Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => AppDetailScreen(store: _store, package: a.package),
    ));
  }

  /// הטאבים: "הכול", ואחריו הקטגוריות בסדר מהשרת — רק כאלה שיש בהן אפליקציות.
  List<String> _tabs(List<StoreApp> apps) {
    final present = {for (final a in apps) if (a.category.isNotEmpty) a.category};
    final order = c.device?.storeCategories ?? kStoreCategories;
    final extra = present.where((x) => !order.contains(x)).toList()..sort();
    return [_allTab, ...order.where(present.contains), ...extra];
  }

  bool _matches(StoreApp a, String q) =>
      q.isEmpty ||
      a.label.toLowerCase().contains(q) ||
      a.package.contains(q) ||
      a.note.toLowerCase().contains(q) ||
      a.category.contains(q);

  @override
  Widget build(BuildContext context) {
    final apps = _store.apps;
    final tabs = _tabs(apps);
    if (!tabs.contains(_category)) _category = _allTab;
    final q = _query.trim().toLowerCase();
    final grouped = _category == _allTab && q.isEmpty;
    final visible = apps.where((a) => (_category == _allTab || a.category == _category) && _matches(a, q)).toList();

    return _SectionScaffold(
      title: 'חנות אפליקציות',
      actions: [
        IconButton(tooltip: 'בקשת אפליקציה', onPressed: _requestCustom, icon: const Icon(Icons.add_circle_outline)),
        IconButton(tooltip: 'רענון', onPressed: () => _toast(_store.reload().then((_) => '')), icon: const Icon(Icons.refresh_rounded)),
      ],
      child: RefreshIndicator(
        onRefresh: _store.reload,
        child: CustomScrollView(
          physics: const AlwaysScrollableScrollPhysics(),
          slivers: [
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
                child: SegmentedButton<_StoreView>(
                  showSelectedIcon: false,
                  segments: const [
                    ButtonSegment(value: _StoreView.apps, label: Text('אפליקציות'), icon: Icon(Icons.apps_rounded)),
                    ButtonSegment(value: _StoreView.updates, label: Text('עדכונים'), icon: Icon(Icons.system_update_rounded)),
                  ],
                  selected: {_view},
                  onSelectionChanged: (v) => setState(() => _view = v.first),
                ),
              ),
            ),
            if (_view == _StoreView.updates)
              ..._UpdatesView.slivers(context, _store, _openDetail, _toast)
            else ...[
              SliverToBoxAdapter(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(16, 10, 16, 4),
                  child: TextField(
                    controller: _search,
                    onChanged: (value) => setState(() => _query = value),
                    decoration: InputDecoration(
                      hintText: 'חיפוש אפליקציות...',
                      prefixIcon: const Icon(Icons.search_rounded),
                      suffixIcon: _query.isEmpty
                          ? null
                          : IconButton(
                              tooltip: 'ניקוי',
                              icon: const Icon(Icons.close_rounded),
                              onPressed: () => setState(() {
                                _search.clear();
                                _query = '';
                              }),
                            ),
                    ),
                  ),
                ),
              ),
              if (tabs.length > 1)
                SliverPersistentHeader(
                  pinned: true,
                  delegate: _CategoryTabsHeader(
                    tabs: tabs,
                    selected: _category,
                    onSelect: (t) => setState(() => _category = t),
                    background: Theme.of(context).colorScheme.surface,
                  ),
                ),
              if (visible.isEmpty)
                SliverToBoxAdapter(
                  child: _StoreEmpty(
                    text: apps.isEmpty
                        ? 'המנהל עוד לא הוסיף אפליקציות לחנות. אפשר לבקש אפליקציה בכפתור למעלה.'
                        : 'לא נמצאה אפליקציה מתאימה. אפשר לבקש אותה מהמנהל.',
                    onRequest: _requestCustom,
                  ),
                )
              else if (grouped)
                for (final cat in tabs.skip(1))
                  SliverToBoxAdapter(
                    child: _CategoryRow(
                      title: cat,
                      apps: visible.where((a) => a.category == cat).toList(),
                      store: _store,
                      onOpen: _openDetail,
                      onMore: () => setState(() => _category = cat),
                    ),
                  )
              else
                SliverPadding(
                  padding: const EdgeInsets.fromLTRB(16, 14, 16, 28),
                  sliver: SliverGrid(
                    gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
                      maxCrossAxisExtent: 112,
                      mainAxisSpacing: 16,
                      crossAxisSpacing: 12,
                      childAspectRatio: 0.72,
                    ),
                    delegate: SliverChildBuilderDelegate(
                      (context, i) => _AppTile(store: _store, app: visible[i], onTap: () => _openDetail(visible[i])),
                      childCount: visible.length,
                    ),
                  ),
                ),
              if (grouped && apps.any((a) => a.category.isEmpty))
                SliverToBoxAdapter(
                  child: _CategoryRow(
                    title: 'עוד',
                    apps: apps.where((a) => a.category.isEmpty).toList(),
                    store: _store,
                    onOpen: _openDetail,
                  ),
                ),
              const SliverToBoxAdapter(child: SizedBox(height: 24)),
            ],
          ],
        ),
      ),
    );
  }
}

/// הטאבים של הקטגוריות — נשארים למעלה בזמן גלילה (כמו בחנות של גוגל).
class _CategoryTabsHeader extends SliverPersistentHeaderDelegate {
  _CategoryTabsHeader({required this.tabs, required this.selected, required this.onSelect, required this.background});
  final List<String> tabs;
  final String selected;
  final ValueChanged<String> onSelect;
  final Color background;

  static const _height = 50.0;

  @override
  double get minExtent => _height;
  @override
  double get maxExtent => _height;

  @override
  bool shouldRebuild(_CategoryTabsHeader old) =>
      old.selected != selected || old.background != background || !listEquals(old.tabs, tabs);

  @override
  Widget build(BuildContext context, double shrinkOffset, bool overlapsContent) {
    final theme = Theme.of(context);
    return Material(
      color: background,
      elevation: overlapsContent ? 2 : 0,
      child: ListView.builder(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 10),
        itemCount: tabs.length,
        itemBuilder: (_, i) {
          final t = tabs[i];
          final on = t == selected;
          return InkWell(
            onTap: () => onSelect(t),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              alignment: Alignment.center,
              decoration: BoxDecoration(
                border: Border(
                  bottom: BorderSide(color: on ? theme.colorScheme.primary : Colors.transparent, width: 3),
                ),
              ),
              child: Text(
                t,
                style: theme.textTheme.titleSmall?.copyWith(
                  fontWeight: on ? FontWeight.w800 : FontWeight.w500,
                  color: on ? theme.colorScheme.primary : theme.colorScheme.onSurfaceVariant,
                ),
              ),
            ),
          );
        },
      ),
    );
  }
}

/// שורה של קטגוריה בטאב "הכול": כותרת, "עוד ›", ואריחים בגלילה אופקית.
class _CategoryRow extends StatelessWidget {
  const _CategoryRow({required this.title, required this.apps, required this.store, required this.onOpen, this.onMore});
  final String title;
  final List<StoreApp> apps;
  final StoreController store;
  final ValueChanged<StoreApp> onOpen;
  final VoidCallback? onMore;

  @override
  Widget build(BuildContext context) {
    if (apps.isEmpty) return const SizedBox.shrink();
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.only(top: 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 8, 6),
            child: Row(
              children: [
                Expanded(child: Text(title, style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w800))),
                if (onMore != null)
                  TextButton(onPressed: onMore, child: Text('הכול (${apps.length}) ›')),
              ],
            ),
          ),
          SizedBox(
            height: 128,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 16),
              itemCount: apps.length,
              separatorBuilder: (_, _) => const SizedBox(width: 12),
              itemBuilder: (_, i) => SizedBox(
                width: 84,
                child: _AppTile(store: store, app: apps[i], onTap: () => onOpen(apps[i])),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// טאב העדכונים (מוחלף בגרסה המלאה בהמשך): אפליקציות מהחנות שיש להן גרסה חדשה.
class _UpdatesView {
  static List<Widget> slivers(BuildContext context, StoreController store, ValueChanged<StoreApp> onOpen,
      Future<void> Function(Future<String>) toast) {
    final updates = store.apps.where(store.hasUpdate).toList();
    if (updates.isEmpty) {
      return [
        const SliverToBoxAdapter(
          child: Padding(padding: EdgeInsets.all(32), child: Center(child: Text('כל האפליקציות מעודכנות ✓'))),
        ),
      ];
    }
    return [
      SliverList.builder(
        itemCount: updates.length,
        itemBuilder: (_, i) => ListTile(
          leading: _AppIcon(app: updates[i], size: 44, radius: 12),
          title: Text(updates[i].label),
          trailing: FilledButton.tonal(onPressed: () => toast(store.install(updates[i])), child: const Text('עדכון')),
          onTap: () => onOpen(updates[i]),
        ),
      ),
    ];
  }
}

/// אריח מרובע: אייקון גדול, שם, וסימון מצב קטן (מותקן / עדכון).
class _AppTile extends StatelessWidget {
  const _AppTile({required this.store, required this.app, required this.onTap});
  final StoreController store;
  final StoreApp app;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final badge = store.installing(app)
        ? const SizedBox(width: 15, height: 15, child: CircularProgressIndicator(strokeWidth: 2))
        : store.hasUpdate(app)
            ? Icon(Icons.arrow_circle_up_rounded, size: 17, color: theme.colorScheme.primary)
            : store.installed(app)
                ? Icon(Icons.check_circle_rounded, size: 17, color: StatusColors.ok(context))
                : null;
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(22),
      child: Column(
        children: [
          Stack(
            children: [
              _AppIcon(app: app, size: 76, radius: 22),
              if (badge != null)
                Positioned(
                  bottom: -2,
                  left: -2,
                  child: Container(
                    padding: const EdgeInsets.all(2),
                    decoration: BoxDecoration(color: theme.colorScheme.surface, shape: BoxShape.circle),
                    child: badge,
                  ),
                ),
            ],
          ),
          const SizedBox(height: 7),
          Text(
            app.label,
            maxLines: 2,
            textAlign: TextAlign.center,
            overflow: TextOverflow.ellipsis,
            style: theme.textTheme.labelMedium?.copyWith(fontWeight: FontWeight.w600, height: 1.15),
          ),
        ],
      ),
    );
  }
}

/// אייקון אפליקציה: תמונה מהשרת אם יש, אחרת אות ראשונה על רקע צבעוני.
class _AppIcon extends StatelessWidget {
  const _AppIcon({required this.app, this.size = 56, this.radius = 16});
  final StoreApp app;
  final double size;
  final double radius;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final fallback = Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(color: theme.colorScheme.primaryContainer, borderRadius: BorderRadius.circular(radius)),
      child: Text(
        app.label.characters.firstOrNull ?? '?',
        style: theme.textTheme.titleLarge?.copyWith(color: theme.colorScheme.primary, fontWeight: FontWeight.w900),
      ),
    );
    if (app.iconUrl.isEmpty) return fallback;
    return ClipRRect(
      borderRadius: BorderRadius.circular(radius),
      child: Image.network(
        app.iconUrl,
        width: size,
        height: size,
        fit: BoxFit.cover,
        errorBuilder: (_, _, _) => fallback,
      ),
    );
  }
}

class _StoreEmpty extends StatelessWidget {
  const _StoreEmpty({required this.text, required this.onRequest});
  final String text;
  final VoidCallback onRequest;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 36),
      child: Column(children: [
        Icon(Icons.storefront_outlined, size: 44, color: theme.colorScheme.onSurfaceVariant),
        const SizedBox(height: 10),
        Text(text, textAlign: TextAlign.center, style: theme.textTheme.bodyMedium),
        const SizedBox(height: 12),
        OutlinedButton.icon(onPressed: onRequest, icon: const Icon(Icons.add), label: const Text('בקשת אפליקציה')),
      ]),
    );
  }
}

/// עמוד אפליקציה — כמו בחנות אמיתית: אייקון, שם, כפתורים, צילומי מסך ותיאור.
class AppDetailScreen extends StatefulWidget {
  const AppDetailScreen({super.key, required this.store, required this.package});
  final StoreController store;
  final String package;

  @override
  State<AppDetailScreen> createState() => _AppDetailScreenState();
}

class _AppDetailScreenState extends State<AppDetailScreen> {
  StoreController get s => widget.store;
  StoreApp? get app => s.apps.where((a) => a.package == widget.package).firstOrNull;

  @override
  void initState() {
    super.initState();
    s.addListener(_onStore);
    s.loadStates();
  }

  @override
  void dispose() {
    s.removeListener(_onStore);
    super.dispose();
  }

  void _onStore() {
    if (mounted) setState(() {});
  }

  Future<void> _run(Future<String> action) async {
    final messenger = ScaffoldMessenger.of(context);
    final msg = await action;
    if (msg.isNotEmpty && mounted) messenger.showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _confirmUninstall(StoreApp a) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('להסיר את ${a.label}?'),
        content: const Text('האפליקציה תוסר מהטלפון. אפשר להתקין אותה שוב מהחנות בכל רגע.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('ביטול')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('הסרה')),
        ],
      ),
    );
    if (ok == true) await _run(s.uninstall(a));
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final a = app;
    if (a == null) {
      return const Scaffold(body: Center(child: Text('האפליקציה כבר לא בחנות')));
    }
    final kind = _actionFor(s, a);
    final isFilterTube = a.package.startsWith('com.filtertube');
    final meta = [
      if (a.category.isNotEmpty) a.category,
      if (a.versionName.isNotEmpty) 'גרסה ${a.versionName}',
      if (a.viaPlay) 'מ-Google Play',
    ].join(' · ');

    return Scaffold(
      appBar: AppBar(title: Text(a.label)),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 28),
        children: [
          Row(
            children: [
              _AppIcon(app: a, size: 72, radius: 20),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(a.label, style: theme.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900)),
                    if (meta.isNotEmpty) ...[
                      const SizedBox(height: 3),
                      Text(meta, style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.primary)),
                    ],
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          _primaryAction(kind, a),
          if (s.installed(a) && !a.package.startsWith('com.filterphone')) ...[
            const SizedBox(height: 8),
            OutlinedButton.icon(
              onPressed: () => _confirmUninstall(a),
              icon: const Icon(Icons.delete_outline_rounded, size: 18),
              label: const Text('הסרה'),
            ),
          ],
          if (a.screenshots.isNotEmpty) ...[
            const SizedBox(height: 20),
            _Screenshots(urls: a.screenshots),
          ],
          const SizedBox(height: 20),
          Text(a.description.isNotEmpty ? a.description : (a.note.isNotEmpty ? a.note : 'אפליקציה מאושרת על ידי FilterPhone.'),
              style: theme.textTheme.bodyMedium?.copyWith(height: 1.5)),
          if (isFilterTube) ...[
            const SizedBox(height: 16),
            Container(
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                color: theme.colorScheme.primaryContainer.withValues(alpha: .4),
                borderRadius: BorderRadius.circular(16),
              ),
              child: Row(children: [
                Icon(Icons.verified_user_rounded, color: theme.colorScheme.primary),
                const SizedBox(width: 10),
                Expanded(
                  child: Text('FilterTube עובדת מחוץ ל-VPN והסינון מובנה בתוכה — לכן היא פועלת גם כשהסינון של FilterPhone פעיל.',
                      style: theme.textTheme.bodySmall),
                ),
              ]),
            ),
          ],
          if (kind == _ActionKind.request) ...[
            const SizedBox(height: 16),
            Text('בטלפון הזה ההתקנה נעשית דרך המנהל. לחיצה על "בקשה" שולחת את האפליקציה לאישור.',
                style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
          ],
        ],
      ),
    );
  }

  Widget _primaryAction(_ActionKind kind, StoreApp a) {
    switch (kind) {
      case _ActionKind.installing:
        return const FilledButton(
          onPressed: null,
          child: SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2.4)),
        );
      case _ActionKind.update:
        return FilledButton.icon(
            onPressed: () => _run(s.install(a)), icon: const Icon(Icons.system_update_rounded), label: const Text('עדכון'));
      case _ActionKind.open:
        return FilledButton.icon(
            onPressed: () => s.open(a), icon: const Icon(Icons.open_in_new_rounded), label: const Text('פתיחה'));
      case _ActionKind.install:
        return FilledButton.icon(
            onPressed: () => _run(s.install(a)),
            icon: Icon(a.viaPlay ? Icons.shop_rounded : Icons.download_rounded),
            label: Text(a.viaPlay ? 'התקנה מ-Google Play' : 'התקנה'));
      case _ActionKind.request:
        return FilledButton.tonalIcon(
            onPressed: () => _run(s.request(a)), icon: const Icon(Icons.send_rounded), label: const Text('בקשה מהמנהל'));
    }
  }
}

/// פס צילומי מסך אופקי (תמונות מהשרת).
class _Screenshots extends StatelessWidget {
  const _Screenshots({required this.urls});
  final List<String> urls;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return SizedBox(
      height: 320,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        itemCount: urls.length,
        separatorBuilder: (_, _) => const SizedBox(width: 10),
        itemBuilder: (_, i) => ClipRRect(
          borderRadius: BorderRadius.circular(16),
          child: Image.network(
            urls[i],
            height: 320,
            fit: BoxFit.cover,
            errorBuilder: (_, _, _) => Container(
              width: 180,
              height: 320,
              alignment: Alignment.center,
              color: theme.colorScheme.surfaceContainerHighest,
              child: const Icon(Icons.broken_image_outlined),
            ),
            loadingBuilder: (_, child, progress) =>
                progress == null ? child : SizedBox(width: 180, height: 320, child: Center(child: CircularProgressIndicator(strokeWidth: 2, value: progress.expectedTotalBytes != null ? progress.cumulativeBytesLoaded / progress.expectedTotalBytes! : null))),
          ),
        ),
      ),
    );
  }
}
