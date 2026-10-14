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
      final ok = await c.native.openPlay(a.package, a.playUrl);
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

enum _StoreTab { all, updates, fresh }

/// חנות FilterPhone: אריחים מרובעים כמו חנות אמיתית. לחיצה פותחת עמוד אפליקציה.
class AppStoreScreen extends StatefulWidget {
  const AppStoreScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<AppStoreScreen> createState() => _AppStoreScreenState();
}

class _AppStoreScreenState extends State<AppStoreScreen> {
  late final StoreController _store = StoreController(widget.controller);
  _StoreTab _tab = _StoreTab.all;
  String _category = 'הכול';
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

  @override
  Widget build(BuildContext context) {
    final apps = _store.apps;
    final updates = apps.where(_store.hasUpdate).length;
    final categories = ['הכול', ...{for (final a in apps) if (a.category.isNotEmpty) a.category}];
    final q = _query.trim().toLowerCase();
    final visible = apps.where((a) {
      if (_tab == _StoreTab.updates && !_store.hasUpdate(a)) return false;
      if (_tab == _StoreTab.fresh && !_store.isFresh(a)) return false;
      if (_category != 'הכול' && a.category != _category) return false;
      return q.isEmpty ||
          a.label.toLowerCase().contains(q) ||
          a.package.contains(q) ||
          a.note.toLowerCase().contains(q);
    }).toList();

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
              child: Padding(padding: const EdgeInsets.fromLTRB(16, 12, 16, 4), child: _StoreHero(controller: c)),
            ),
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 10, 16, 0),
                child: SegmentedButton<_StoreTab>(
                  showSelectedIcon: false,
                  segments: [
                    const ButtonSegment(value: _StoreTab.all, label: Text('הכול'), icon: Icon(Icons.apps_rounded)),
                    ButtonSegment(
                        value: _StoreTab.updates,
                        label: Text(updates > 0 ? 'עדכונים ($updates)' : 'עדכונים'),
                        icon: const Icon(Icons.system_update_rounded)),
                    const ButtonSegment(value: _StoreTab.fresh, label: Text('חדשות'), icon: Icon(Icons.fiber_new_rounded)),
                  ],
                  selected: {_tab},
                  onSelectionChanged: (s) => setState(() => _tab = s.first),
                ),
              ),
            ),
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 10, 16, 2),
                child: TextField(
                  controller: _search,
                  onChanged: (value) => setState(() => _query = value),
                  decoration: const InputDecoration(hintText: 'חיפוש אפליקציות...', prefixIcon: Icon(Icons.search_rounded)),
                ),
              ),
            ),
            if (categories.length > 1)
              SliverToBoxAdapter(
                child: SizedBox(
                  height: 58,
                  child: ListView.separated(
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                    scrollDirection: Axis.horizontal,
                    itemBuilder: (context, index) => ChoiceChip(
                      label: Text(categories[index]),
                      selected: _category == categories[index],
                      onSelected: (_) => setState(() => _category = categories[index]),
                    ),
                    separatorBuilder: (_, _) => const SizedBox(width: 8),
                    itemCount: categories.length,
                  ),
                ),
              ),
            if (_tab == _StoreTab.updates && updates > 1 && c.deviceOwner)
              SliverToBoxAdapter(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
                  child: FilledButton.tonalIcon(
                    onPressed: () async {
                      for (final a in apps.where(_store.hasUpdate)) {
                        await _store.install(a);
                      }
                    },
                    icon: const Icon(Icons.update_rounded),
                    label: const Text('עדכון הכול'),
                  ),
                ),
              ),
            if (visible.isEmpty)
              SliverToBoxAdapter(
                child: _StoreEmpty(
                  text: apps.isEmpty
                      ? 'המנהל עוד לא הוסיף אפליקציות לחנות. אפשר לבקש אפליקציה בכפתור למעלה.'
                      : _tab == _StoreTab.updates
                          ? 'כל האפליקציות מעודכנות ✓'
                          : 'לא נמצאה אפליקציה מתאימה.',
                  onRequest: _requestCustom,
                ),
              )
            else
              SliverPadding(
                padding: const EdgeInsets.fromLTRB(16, 14, 16, 28),
                sliver: SliverGrid(
                  gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
                    maxCrossAxisExtent: 128,
                    mainAxisSpacing: 16,
                    crossAxisSpacing: 14,
                    childAspectRatio: 0.74,
                  ),
                  delegate: SliverChildBuilderDelegate(
                    (context, i) => _AppTile(store: _store, app: visible[i], onTap: () => _openDetail(visible[i])),
                    childCount: visible.length,
                  ),
                ),
              ),
          ],
        ),
      ),
    );
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

class _StoreHero extends StatelessWidget {
  const _StoreHero({required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final active = controller.device?.isActive ?? false;
    final managed = controller.deviceOwner;
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(22),
        gradient: LinearGradient(
          begin: Alignment.topRight,
          end: Alignment.bottomLeft,
          colors: [theme.colorScheme.primaryContainer, theme.colorScheme.surface],
        ),
        border: Border.all(color: theme.colorScheme.outlineVariant),
      ),
      child: Row(
        children: [
          Container(
            width: 46,
            height: 46,
            decoration: BoxDecoration(color: theme.colorScheme.primary, shape: BoxShape.circle),
            child: const Icon(Icons.verified_rounded, color: Colors.white, size: 25),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(active ? 'אפליקציות באישור FilterPhone' : 'האפליקציות זמינות לאחר אישור המכשיר',
                    style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w800)),
                const SizedBox(height: 3),
                Text(
                  managed
                      ? 'התקנה ועדכונים ישירות מכאן — גם כשהתקנת אפליקציות חסומה בטלפון.'
                      : 'בוחרים אפליקציה ושולחים בקשה — המנהל מאשר ומתקין.',
                  style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                ),
              ],
            ),
          ),
        ],
      ),
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
