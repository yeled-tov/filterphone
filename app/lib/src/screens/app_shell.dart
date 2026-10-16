import 'dart:async';

import 'package:flutter/foundation.dart' show listEquals;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api_client.dart';
import '../app_controller.dart';
import '../native_bridge.dart';
import '../support_bot.dart';
import '../theme.dart';
import 'change_request_screen.dart';
import 'home_screen.dart';

part 'support_screen.dart';
part 'store_screen.dart';

class AppShell extends StatefulWidget {
  const AppShell({super.key, required this.controller});
  final AppController controller;

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  static const _supportTab = 2;
  int _index = 0;
  AppController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    c.addListener(_onController);
    if (c.takeRequestedTab() == 'support') _index = _supportTab;
  }

  @override
  void dispose() {
    c.removeListener(_onController);
    super.dispose();
  }

  /// נפתחנו מהתראה "יש תשובה מהתמיכה" — ישר לצ'אט.
  void _onController() {
    if (c.takeRequestedTab() == 'support' && mounted) setState(() => _index = _supportTab);
  }

  void _openTab(int i) => setState(() => _index = i);

  @override
  Widget build(BuildContext context) {
    final unread = c.supportUnread;
    return Scaffold(
      body: IndexedStack(
        index: _index,
        children: [
          HomeScreen(controller: c),
          AppStoreScreen(controller: c),
          SupportScreen(controller: c, visible: _index == _supportTab, onOpenTab: _openTab),
          ProfileScreen(controller: c),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (value) => setState(() => _index = value),
        destinations: [
          const NavigationDestination(
            icon: Icon(Icons.shield_outlined),
            selectedIcon: Icon(Icons.shield),
            label: 'סינון',
          ),
          const NavigationDestination(
            icon: Icon(Icons.apps_outlined),
            selectedIcon: Icon(Icons.apps),
            label: 'אפליקציות',
          ),
          NavigationDestination(
            icon: Badge(
              isLabelVisible: unread > 0,
              label: Text('$unread'),
              child: const Icon(Icons.support_agent_outlined),
            ),
            selectedIcon: const Icon(Icons.support_agent),
            label: 'תמיכה',
          ),
          const NavigationDestination(
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
                  value: controller.androidId.isNotEmpty ? controller.androidId : (d?.id.toString() ?? '—'),
                  action: controller.androidId.isEmpty
                      ? null
                      : () => _copy(context, 'מזהה המכשיר', controller.androidId),
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
