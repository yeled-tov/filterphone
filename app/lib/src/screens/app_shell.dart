import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api_client.dart';
import '../app_controller.dart';
import '../config.dart';
import '../theme.dart';
import 'change_request_screen.dart';
import 'home_screen.dart';

class AppShell extends StatefulWidget {
  const AppShell({super.key, required this.controller});
  final AppController controller;

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  int _index = 0;
  AppController get c => widget.controller;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: IndexedStack(
        index: _index,
        children: [
          HomeScreen(controller: c),
          AppStoreScreen(controller: c),
          SupportScreen(controller: c),
          ProfileScreen(controller: c),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (value) => setState(() => _index = value),
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.shield_outlined),
            selectedIcon: Icon(Icons.shield),
            label: 'סינון',
          ),
          NavigationDestination(
            icon: Icon(Icons.apps_outlined),
            selectedIcon: Icon(Icons.apps),
            label: 'אפליקציות',
          ),
          NavigationDestination(
            icon: Icon(Icons.support_agent_outlined),
            selectedIcon: Icon(Icons.support_agent),
            label: 'תמיכה',
          ),
          NavigationDestination(
            icon: Icon(Icons.person_outline),
            selectedIcon: Icon(Icons.person),
            label: 'פרופיל',
          ),
        ],
      ),
    );
  }
}

class _SectionScaffold extends StatelessWidget {
  const _SectionScaffold({
    required this.title,
    required this.child,
    this.actions = const [],
  });

  final String title;
  final Widget child;
  final List<Widget> actions;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: _BrandTitle(title: title),
        centerTitle: false,
        actions: actions,
      ),
      body: child,
    );
  }
}

class _BrandTitle extends StatelessWidget {
  const _BrandTitle({required this.title});
  final String title;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 38,
          height: 38,
          decoration: BoxDecoration(
            gradient: LinearGradient(
              colors: [
                theme.colorScheme.primary,
                theme.colorScheme.primary.withValues(alpha: .78),
              ],
              begin: Alignment.topRight,
              end: Alignment.bottomLeft,
            ),
            borderRadius: BorderRadius.circular(12),
          ),
          child: const Icon(Icons.shield_rounded, color: Colors.white, size: 23),
        ),
        const SizedBox(width: 10),
        Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'פילטרפון',
              style: theme.textTheme.titleMedium?.copyWith(
                fontWeight: FontWeight.w800,
                color: theme.colorScheme.onSurface,
                height: 1,
              ),
            ),
            Text(
              title,
              style: theme.textTheme.labelSmall?.copyWith(
                color: theme.colorScheme.primary,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
        ),
      ],
    );
  }
}

class AppStoreScreen extends StatefulWidget {
  const AppStoreScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<AppStoreScreen> createState() => _AppStoreScreenState();
}

enum _StoreTab { all, updates, fresh }

/// החנות של FilterPhone: רק אפליקציות שהמנהל אישר (מהשרת). בטלפון מנוהל (מנהל מכשיר) ההתקנה
/// והעדכון נעשים ישירות דרך FilterPhone — גם כשהתקנת אפליקציות חסומה בטלפון. בלי מנהל מכשיר,
/// או לאפליקציה בלי קובץ אצלנו — שולחים בקשה למנהל.
class _AppStoreScreenState extends State<AppStoreScreen> {
  _StoreTab _tab = _StoreTab.all;
  String _category = 'הכול';
  String _query = '';
  final _search = TextEditingController();
  Map<String, Map<String, Object?>> _states = {};
  Timer? _poll;

  AppController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    c.addListener(_onController);
    _loadStates();
  }

  @override
  void dispose() {
    c.removeListener(_onController);
    _poll?.cancel();
    _search.dispose();
    super.dispose();
  }

  List<StoreApp> get _apps => c.device?.storeApps ?? const [];

  void _onController() => _loadStates();

  Future<void> _loadStates() async {
    final pkgs = _apps.map((a) => a.package).toList();
    if (pkgs.isEmpty) return;
    try {
      final states = await c.native.storeAppStates(pkgs);
      if (!mounted) return;
      setState(() => _states = states);
      final installing = states.values.any((s) => s['state'] == 'installing');
      _poll?.cancel();
      if (installing) _poll = Timer(const Duration(seconds: 2), _loadStates);
    } on PlatformException catch (e) {
      debugPrint('store states failed: $e');
    }
  }

  int? _installed(StoreApp a) => (_states[a.package]?['installed_version'] as num?)?.toInt();
  String? _state(StoreApp a) => _states[a.package]?['state'] as String?;
  bool _hasUpdate(StoreApp a) {
    final v = _installed(a);
    return v != null && a.versionCode != null && v < a.versionCode!;
  }

  bool _isFresh(StoreApp a) {
    final now = DateTime.now();
    return (a.addedAt != null && now.difference(a.addedAt!).inDays < 30) ||
        (a.updatedAt != null && now.difference(a.updatedAt!).inDays < 14);
  }

  Future<void> _reload() async {
    await c.refresh(forceSync: true);
    await _loadStates();
  }

  Future<void> _install(StoreApp a) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      final r = await c.native.installStoreApp(a.raw);
      final text = switch (r) {
        'started' => 'מתקין את ${a.label}…',
        'up_to_date' => '${a.label} כבר מעודכנת',
        'busy' => 'התקנה אחרת בתהליך — נסו שוב בעוד רגע',
        'not_owner' => 'בטלפון הזה ההתקנה נעשית דרך המנהל — נשלחת בקשה',
        _ => 'לא ניתן להתקין את ${a.label} כרגע',
      };
      messenger.showSnackBar(SnackBar(content: Text(text)));
      if (r == 'not_owner') await _request(a);
    } on PlatformException catch (e) {
      messenger.showSnackBar(SnackBar(content: Text('ההתקנה נכשלה: ${e.message ?? e.code}')));
    }
    await _loadStates();
  }

  Future<void> _request(StoreApp? a) async {
    final messenger = ScaffoldMessenger.of(context);
    if (!(c.device?.isActive ?? false)) {
      messenger.showSnackBar(const SnackBar(content: Text('החנות זמינה לאחר שהמכשיר פעיל ומאושר.')));
      return;
    }
    var note = a == null ? '' : 'בקשת התקנת אפליקציה: ${a.label} (${a.package})';
    if (a == null) {
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
      if (ok != true || input.text.trim().isEmpty) return;
      note = 'בקשת אפליקציה חדשה לחנות: ${input.text.trim()}';
    }
    final ok = await c.requestChange(note: note);
    if (!mounted) return;
    messenger.showSnackBar(SnackBar(
        content: Text(ok ? 'הבקשה נשלחה למנהל. נעדכן כשהיא תטופל.' : (c.error ?? 'לא הצלחנו לשלוח את הבקשה.'))));
  }

  @override
  Widget build(BuildContext context) {
    final apps = _apps;
    final updates = apps.where(_hasUpdate).length;
    final categories = ['הכול', ...{for (final a in apps) if (a.category.isNotEmpty) a.category}];
    final q = _query.trim().toLowerCase();
    final visible = apps.where((a) {
      if (_tab == _StoreTab.updates && !_hasUpdate(a)) return false;
      if (_tab == _StoreTab.fresh && !_isFresh(a)) return false;
      if (_category != 'הכול' && a.category != _category) return false;
      return q.isEmpty || a.label.toLowerCase().contains(q) || a.package.contains(q) ||
          a.note.toLowerCase().contains(q);
    }).toList();

    return _SectionScaffold(
      title: 'חנות אפליקציות',
      actions: [
        IconButton(tooltip: 'בקשת אפליקציה', onPressed: () => _request(null), icon: const Icon(Icons.add_circle_outline)),
        IconButton(tooltip: 'רענון', onPressed: _reload, icon: const Icon(Icons.refresh_rounded)),
      ],
      child: RefreshIndicator(
        onRefresh: _reload,
        child: CustomScrollView(
          physics: const AlwaysScrollableScrollPhysics(),
          slivers: [
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
                child: _StoreHero(controller: c),
              ),
            ),
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 10, 16, 0),
                child: SegmentedButton<_StoreTab>(
                  segments: [
                    const ButtonSegment(value: _StoreTab.all, label: Text('הכול'), icon: Icon(Icons.apps_rounded)),
                    ButtonSegment(value: _StoreTab.updates, label: Text(updates > 0 ? 'עדכונים ($updates)' : 'עדכונים'),
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
                      for (final a in apps.where(_hasUpdate)) {
                        await _install(a);
                      }
                    },
                    icon: const Icon(Icons.update_rounded),
                    label: const Text('עדכון הכול'),
                  ),
                ),
              ),
            SliverPadding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
              sliver: visible.isEmpty
                  ? SliverToBoxAdapter(child: _StoreEmpty(
                      text: apps.isEmpty
                          ? 'המנהל עוד לא הוסיף אפליקציות לחנות. אפשר לבקש אפליקציה בכפתור למטה.'
                          : _tab == _StoreTab.updates ? 'כל האפליקציות מעודכנות ✓' : 'לא נמצאה אפליקציה מתאימה.',
                      onRequest: () => _request(null)))
                  : SliverList.separated(
                      itemCount: visible.length,
                      itemBuilder: (context, i) {
                        final a = visible[i];
                        return _StoreAppCard(
                          app: a,
                          installedVersion: _installed(a),
                          state: _state(a),
                          canInstall: c.deviceOwner && a.directInstall,
                          onInstall: () => _install(a),
                          onRequest: () => _request(a),
                          onOpen: () => c.native.openApp(a.package),
                        );
                      },
                      separatorBuilder: (_, _) => const SizedBox(height: 10),
                    ),
            ),
          ],
        ),
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
      padding: const EdgeInsets.symmetric(vertical: 32),
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
      padding: const EdgeInsets.all(18),
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
            width: 52,
            height: 52,
            decoration: BoxDecoration(color: theme.colorScheme.primary, shape: BoxShape.circle),
            child: const Icon(Icons.verified_rounded, color: Colors.white, size: 28),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(active ? 'אפליקציות באישור FilterPhone' : 'האפליקציות זמינות לאחר אישור המכשיר',
                    style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w800)),
                const SizedBox(height: 4),
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

class _StoreAppCard extends StatelessWidget {
  const _StoreAppCard({
    required this.app,
    required this.installedVersion,
    required this.state,
    required this.canInstall,
    required this.onInstall,
    required this.onRequest,
    required this.onOpen,
  });

  final StoreApp app;
  final int? installedVersion;
  final String? state;
  final bool canInstall;
  final VoidCallback onInstall;
  final VoidCallback onRequest;
  final VoidCallback onOpen;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final installed = installedVersion != null;
    final update = installed && app.versionCode != null && installedVersion! < app.versionCode!;
    final installing = state == 'installing';
    final failed = state?.startsWith('failed') == true;
    final Widget action;
    if (installing) {
      action = const SizedBox(width: 84, child: Center(child: SizedBox(width: 24, height: 24, child: CircularProgressIndicator(strokeWidth: 2.5))));
    } else if (update && canInstall) {
      action = FilledButton(onPressed: onInstall, child: const Text('עדכון'));
    } else if (installed) {
      action = OutlinedButton(onPressed: onOpen, child: const Text('פתיחה'));
    } else if (canInstall) {
      action = FilledButton(onPressed: onInstall, child: const Text('התקנה'));
    } else {
      action = FilledButton.tonal(onPressed: onRequest, child: const Text('בקשה'));
    }
    final details = [
      if (app.category.isNotEmpty) app.category,
      if (app.versionName.isNotEmpty) 'גרסה ${app.versionName}',
      if (update) 'יש עדכון',
    ].join(' · ');
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 14, 12, 14),
        child: Row(
          children: [
            Container(
              width: 52,
              height: 52,
              decoration: BoxDecoration(color: theme.colorScheme.primaryContainer, borderRadius: BorderRadius.circular(16)),
              child: Icon(Icons.android_rounded, size: 28, color: theme.colorScheme.primary),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(app.label, style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w800)),
                  if (app.note.isNotEmpty) ...[
                    const SizedBox(height: 3),
                    Text(app.note, style: theme.textTheme.bodySmall),
                  ],
                  if (details.isNotEmpty) ...[
                    const SizedBox(height: 4),
                    Text(details, style: theme.textTheme.labelSmall?.copyWith(color: theme.colorScheme.primary)),
                  ],
                  if (failed) ...[
                    const SizedBox(height: 4),
                    Text('ההתקנה האחרונה נכשלה — אפשר לנסות שוב',
                        style: theme.textTheme.labelSmall?.copyWith(color: StatusColors.bad(context))),
                  ],
                ],
              ),
            ),
            const SizedBox(width: 8),
            action,
          ],
        ),
      ),
    );
  }
}

class SupportScreen extends StatefulWidget {
  const SupportScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<SupportScreen> createState() => _SupportScreenState();
}

class _SupportScreenState extends State<SupportScreen> {
  final _text = TextEditingController();
  final _scroll = ScrollController();

  final List<_ChatMessage> _messages = [
    const _ChatMessage(
      fromBot: true,
      text: 'שלום 👋 אני העוזר של FilterPhone. אני יכול לעזור בהגנה, VPN, חסימות, אפליקציות והגדרות. במה לעזור?',
    ),
  ];

  final _quick = const [
    'הסינון לא עובד',
    'ה-VPN לא מתחבר',
    'איך מתקינים אפליקציה?',
    'אני צריך נציג',
  ];

  @override
  void dispose() {
    _text.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _send([String? preset]) async {
    final value = (preset ?? _text.text).trim();
    if (value.isEmpty) return;
    _text.clear();
    setState(() => _messages.add(_ChatMessage(fromBot: false, text: value)));

    await Future<void>.delayed(const Duration(milliseconds: 220));
    if (!mounted) return;
    setState(() => _messages.add(_ChatMessage(fromBot: true, text: _botReply(value))));
    await Future<void>.delayed(const Duration(milliseconds: 50));

    if (_scroll.hasClients) {
      await _scroll.animateTo(
        _scroll.position.maxScrollExtent,
        duration: const Duration(milliseconds: 220),
        curve: Curves.easeOut,
      );
    }
  }

  String _botReply(String input) {
    final t = input.toLowerCase();
    final c = widget.controller;

    if (t.contains('vpn')) {
      return c.vpn.isUp
          ? 'ה-VPN מחובר כרגע ✅ אם עדיין יש בעיה, כתוב לי מה בדיוק לא נפתח.'
          : 'אני רואה שה-VPN לא מחובר. עבור ללשונית "סינון" ולחץ על "חיבור". אם זה נכשל, שלח לי את הודעת השגיאה.';
    }
    if (t.contains('אפליקציה')) {
      return 'בלשונית "אפליקציות" תראה את האפליקציות המאושרות. כרגע אפליקציה שלא מותקנת מוגשת כבקשת התקנה למנהל.';
    }
    if (t.contains('נציג') || t.contains('אדם') || t.contains('וואטסאפ')) {
      return 'אפשר לעבור עכשיו לנציג. אכין את הפנייה עם מזהה המכשיר והמצב הנוכחי כדי שהטיפול יהיה מהיר.';
    }
    if (t.contains('חסימה') || t.contains('מסונן') || t.contains('סינון')) {
      final level = c.device?.levelName;
      return level == null
          ? 'אני צריך שהמכשיר יהיה פעיל כדי לבדוק את רמת הסינון.'
          : 'רמת הסינון הפעילה כרגע היא "$level". בלשונית "סינון" אפשר לראות את כל מצב ההגנה והחסימות.';
    }
    return 'אני כאן כדי לעזור. כתוב למשל "ה-VPN לא מתחבר", "החסימה לא עובדת", או "אני צריך נציג".';
  }

  Future<void> _openWhatsApp() async {
    final d = widget.controller.device;
    final message = [
      'שלום, אני צריך עזרה ב-FilterPhone.',
      'מזהה מכשיר: ${d?.id.toString() ?? 'לא ידוע'}',
      'מכשיר: ${d?.name ?? 'לא ידוע'}',
      'רמת סינון: ${d?.levelName ?? 'לא ידוע'}',
      'VPN: ${widget.controller.vpn.isUp ? 'פעיל' : 'לא פעיל'}',
    ].join('\n');

    if (supportWhatsAppNumber.trim().isEmpty) {
      if (!mounted) return;
      showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('חיבור לנציג'),
          content: const Text(
            'מספר התמיכה עדיין לא הוגדר בבניית האפליקציה. '
            'הבוט זמין כבר עכשיו; לאחר הגדרת מספר WhatsApp ייפתח צ\'אט ישיר לנציג.',
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(context), child: const Text('סגור')),
          ],
        ),
      );
      return;
    }

    try {
      await widget.controller.native.openSupportWhatsApp(
        supportWhatsAppNumber,
        message,
      );
    } on PlatformException catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('לא ניתן לפתוח WhatsApp: ${e.message ?? e.code}')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return _SectionScaffold(
      title: 'שירות ותמיכה',
      actions: [
        IconButton(
          tooltip: 'נציג WhatsApp',
          onPressed: _openWhatsApp,
          icon: const Icon(Icons.chat_rounded),
        ),
      ],
      child: Column(
        children: [
          Expanded(
            child: ListView(
              controller: _scroll,
              padding: const EdgeInsets.fromLTRB(16, 18, 16, 12),
              children: [
                _SupportHeader(onHuman: _openWhatsApp),
                const SizedBox(height: 14),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    for (final prompt in _quick)
                      ActionChip(
                        label: Text(prompt),
                        onPressed: () => _send(prompt),
                      ),
                  ],
                ),
                const SizedBox(height: 18),
                for (final message in _messages) ...[
                  _ChatBubble(message: message),
                  const SizedBox(height: 10),
                ],
              ],
            ),
          ),
          _ChatComposer(controller: _text, onSend: _send),
        ],
      ),
    );
  }
}

class _SupportHeader extends StatelessWidget {
  const _SupportHeader({required this.onHuman});
  final VoidCallback onHuman;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: theme.colorScheme.primaryContainer,
        borderRadius: BorderRadius.circular(22),
      ),
      child: Row(
        children: [
          CircleAvatar(
            radius: 28,
            backgroundColor: theme.colorScheme.primary,
            child: const Icon(Icons.support_agent, color: Colors.white, size: 29),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('תמיכה חכמה', style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w900)),
                const SizedBox(height: 3),
                Text('הבוט זמין 24/7. צריך אותי? עוברים לנציג.', style: theme.textTheme.bodySmall),
              ],
            ),
          ),
          IconButton(
            tooltip: 'נציג',
            onPressed: onHuman,
            icon: const Icon(Icons.arrow_back_rounded),
          ),
        ],
      ),
    );
  }
}

class _ChatComposer extends StatelessWidget {
  const _ChatComposer({required this.controller, required this.onSend});
  final TextEditingController controller;
  final Future<void> Function() onSend;

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
                decoration: InputDecoration(
                  hintText: 'כתבו הודעה...',
                  prefixIcon: const Icon(Icons.message_outlined),
                  suffixIcon: IconButton(
                    onPressed: () => FocusScope.of(context).unfocus(),
                    icon: Icon(Icons.keyboard_hide_rounded, color: theme.colorScheme.outline),
                  ),
                ),
              ),
            ),
            const SizedBox(width: 8),
            Material(
              color: theme.colorScheme.primary,
              borderRadius: BorderRadius.circular(16),
              child: InkWell(
                onTap: () async => onSend(),
                borderRadius: BorderRadius.circular(16),
                child: const SizedBox(
                  width: 54,
                  height: 54,
                  child: Icon(Icons.send_rounded, color: Colors.white),
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
  const _ChatBubble({required this.message});
  final _ChatMessage message;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final bg = message.fromBot ? theme.colorScheme.surfaceContainerHighest : theme.colorScheme.primaryContainer;
    return Align(
      alignment: message.fromBot ? AlignmentDirectional.centerStart : AlignmentDirectional.centerEnd,
      child: Container(
        constraints: const BoxConstraints(maxWidth: 340),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
        decoration: BoxDecoration(
          color: bg,
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(18),
            topRight: const Radius.circular(18),
            bottomLeft: Radius.circular(message.fromBot ? 5 : 18),
            bottomRight: Radius.circular(message.fromBot ? 18 : 5),
          ),
        ),
        child: Text(message.text),
      ),
    );
  }
}

class ProfileScreen extends StatelessWidget {
  const ProfileScreen({super.key, required this.controller});
  final AppController controller;

  String _initials(String value) {
    final parts = value.trim().split(RegExp(r'\s+')).where((p) => p.isNotEmpty).toList();
    if (parts.isEmpty) return 'פ';
    if (parts.length == 1) return parts.first.substring(0, 1);
    return '${parts.first.substring(0, 1)}${parts.last.substring(0, 1)}';
  }

  Future<void> _copy(BuildContext context, String label, String value) async {
    await Clipboard.setData(ClipboardData(text: value));
    if (!context.mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$label הועתק')));
  }

  void _changeRequest(BuildContext context) {
    Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => ChangeRequestScreen(controller: controller)),
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final d = controller.device;
    final customer = d?.customer;
    final displayName = customer?.name.isNotEmpty == true ? customer!.name : (d?.name ?? 'לקוח FilterPhone');
    final statusGood = controller.isProtected;

    return _SectionScaffold(
      title: 'פרופיל וחשבון',
      actions: [
        IconButton(
          tooltip: 'רענון',
          onPressed: controller.refresh,
          icon: const Icon(Icons.refresh_rounded),
        ),
      ],
      child: RefreshIndicator(
        onRefresh: controller.refresh,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 28),
          children: [
            Container(
              padding: const EdgeInsets.fromLTRB(20, 22, 20, 18),
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(24),
                gradient: LinearGradient(
                  colors: [
                    theme.colorScheme.primaryContainer,
                    theme.colorScheme.surface,
                  ],
                  begin: Alignment.topRight,
                  end: Alignment.bottomLeft,
                ),
                border: Border.all(color: theme.colorScheme.outlineVariant),
              ),
              child: Column(
                children: [
                  CircleAvatar(
                    radius: 37,
                    backgroundColor: theme.colorScheme.primary,
                    child: Text(
                      _initials(displayName),
                      style: const TextStyle(color: Colors.white, fontSize: 26, fontWeight: FontWeight.w800),
                    ),
                  ),
                  const SizedBox(height: 12),
                  Text(
                    displayName,
                    style: theme.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w900),
                    textAlign: TextAlign.center,
                  ),
                  const SizedBox(height: 4),
                  Text(
                    customer?.phone.isNotEmpty == true ? customer!.phone : 'חשבון FilterPhone',
                    style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                  ),
                  const SizedBox(height: 14),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
                    decoration: BoxDecoration(
                      color: (statusGood ? StatusColors.ok(context) : StatusColors.warn(context)).withValues(alpha: .10),
                      borderRadius: BorderRadius.circular(30),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(
                          statusGood ? Icons.verified_rounded : Icons.warning_amber_rounded,
                          size: 17,
                          color: statusGood ? StatusColors.ok(context) : StatusColors.warn(context),
                        ),
                        const SizedBox(width: 6),
                        Text(
                          statusGood ? 'ההגנה פעילה' : 'נדרשת בדיקה',
                          style: theme.textTheme.labelLarge?.copyWith(
                            color: statusGood ? StatusColors.ok(context) : StatusColors.warn(context),
                            fontWeight: FontWeight.w800,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 14),
            _ProfileCard(
              title: 'פרטי החשבון',
              icon: Icons.person_rounded,
              children: [
                _InfoRow(label: 'שם', value: customer?.name.isNotEmpty == true ? customer!.name : '—'),
                _InfoRow(label: 'טלפון', value: customer?.phone.isNotEmpty == true ? customer!.phone : '—'),
                _InfoRow(label: 'אימייל', value: customer?.email.isNotEmpty == true ? customer!.email : '—'),
              ],
            ),
            const SizedBox(height: 12),
            _ProfileCard(
              title: 'המכשיר שלי',
              icon: Icons.phone_android_rounded,
              children: [
                _InfoRow(label: 'שם המכשיר', value: d?.name ?? '—'),
                _InfoRow(
                  label: 'מזהה מכשיר',
                  value: d?.id.toString() ?? '—',
                  action: d == null ? null : () => _copy(context, 'מזהה המכשיר', d.id.toString()),
                ),
                _InfoRow(label: 'ניהול מכשיר', value: d?.status == 'active' && controller.deviceOwner ? 'Device Owner פעיל' : 'לא פעיל'),
              ],
            ),
            const SizedBox(height: 12),
            _ProfileCard(
              title: 'מה פעיל בהגנה',
              icon: Icons.shield_rounded,
              children: [
                _ProtectionRow('רמת סינון', d?.levelName ?? '—', d != null),
                _ProtectionRow('VPN', controller.vpn.isUp ? 'מחובר' : 'לא מחובר', controller.vpn.isUp),
                _ProtectionRow('נגישות', controller.protection.guardEnabled ? 'מופעלת' : 'לא מופעלת', controller.protection.guardEnabled || !controller.guardNeeded),
                _ProtectionRow('סוללה', controller.protection.backgroundOk ? 'ללא הגבלה' : 'מוגבלת', controller.protection.backgroundOk),
              ],
            ),
            const SizedBox(height: 12),
            _ProfileCard(
              title: 'חסימות פעילות',
              icon: Icons.block_rounded,
              children: [
                if (d?.blocks.isEmpty ?? true)
                  const Padding(
                    padding: EdgeInsets.symmetric(vertical: 4),
                    child: Text('אין חסימות נוספות מעבר לרמת הסינון.'),
                  )
                else
                  Wrap(
                    spacing: 7,
                    runSpacing: 7,
                    children: [
                      for (final block in d!.blocks)
                        Chip(
                          label: Text(block.title),
                          visualDensity: VisualDensity.compact,
                        ),
                    ],
                  ),
              ],
            ),
            const SizedBox(height: 16),
            FilledButton.icon(
              onPressed: d == null ? null : () => _changeRequest(context),
              icon: const Icon(Icons.tune_rounded),
              label: const Text('בקשת שינוי בהגנה'),
            ),
            const SizedBox(height: 8),
            if (controller.protection.updateAvailable != null)
              OutlinedButton.icon(
                onPressed: controller.openUpdatePage,
                icon: const Icon(Icons.system_update_rounded),
                label: Text('עדכון FilterPhone ${controller.protection.updateAvailable}'),
              ),
          ],
        ),
      ),
    );
  }
}

class _ProfileCard extends StatelessWidget {
  const _ProfileCard({required this.title, required this.icon, required this.children});
  final String title;
  final IconData icon;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 15),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Icon(icon, size: 21, color: theme.colorScheme.primary),
                const SizedBox(width: 8),
                Text(title, style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w800)),
              ],
            ),
            const SizedBox(height: 10),
            ...children,
          ],
        ),
      ),
    );
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow({required this.label, required this.value, this.action});
  final String label;
  final String value;
  final VoidCallback? action;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          Expanded(
            child: Text(label, style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
          ),
          Flexible(
            child: Text(
              value,
              textAlign: TextAlign.end,
              style: theme.textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w700),
            ),
          ),
          if (action != null)
            IconButton(
              tooltip: 'העתק',
              visualDensity: VisualDensity.compact,
              onPressed: action,
              icon: const Icon(Icons.copy_rounded, size: 18),
            ),
        ],
      ),
    );
  }
}

class _ProtectionRow extends StatelessWidget {
  const _ProtectionRow(this.label, this.value, this.good);
  final String label;
  final String value;
  final bool good;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final color = good ? StatusColors.ok(context) : StatusColors.warn(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          Icon(good ? Icons.check_circle_rounded : Icons.warning_amber_rounded, size: 18, color: color),
          const SizedBox(width: 9),
          Expanded(child: Text(label)),
          Text(value, style: theme.textTheme.bodyMedium?.copyWith(color: color, fontWeight: FontWeight.w800)),
        ],
      ),
    );
  }
}

class _ChatMessage {
  const _ChatMessage({required this.fromBot, required this.text});
  final bool fromBot;
  final String text;
}
