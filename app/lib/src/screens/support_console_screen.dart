import 'dart:async';

import 'package:flutter/material.dart';

import '../api_client.dart';
import '../app_controller.dart';

/// ניהול תמיכה — רק במכשיר המנהל. רשימת כל השיחות של הלקוחות (כמו וואטסאפ), עם מספר הפניות שלא
/// נקראו; לחיצה על שיחה פותחת אותה, והמנהל עונה — התשובה מגיעה ללקוח באפליקציה שלו.
class SupportConsoleScreen extends StatefulWidget {
  const SupportConsoleScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<SupportConsoleScreen> createState() => _SupportConsoleScreenState();
}

class _SupportConsoleScreenState extends State<SupportConsoleScreen> {
  static const _poll = Duration(seconds: 8);
  List<SupportThreadInfo>? _threads;
  String? _error;
  Timer? _timer;

  AppController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    _load();
    _timer = Timer.periodic(_poll, (_) => _load());
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final (threads, _) = await c.adminThreads();
      if (!mounted) return;
      setState(() {
        _threads = threads;
        _error = null;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _error = e.status == 0 ? 'אין חיבור לשרת' : e.message);
    }
  }

  static String _ago(DateTime t) {
    final d = DateTime.now().difference(t);
    if (d.inMinutes < 1) return 'עכשיו';
    if (d.inMinutes < 60) return 'לפני ${d.inMinutes} דק\'';
    if (d.inHours < 24) return 'לפני ${d.inHours} שע\'';
    return 'לפני ${d.inDays} ימים';
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final threads = _threads;
    return Scaffold(
      appBar: AppBar(title: const Text('ניהול תמיכה')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: threads == null
            ? (_error != null
                ? ListView(children: [Padding(padding: const EdgeInsets.all(24), child: Text(_error!))])
                : const Center(child: CircularProgressIndicator()))
            : threads.isEmpty
                ? ListView(children: [
                    const Padding(
                      padding: EdgeInsets.all(32),
                      child: Text('אין עדיין פניות מלקוחות.', textAlign: TextAlign.center),
                    ),
                  ])
                : ListView.separated(
                    itemCount: threads.length,
                    separatorBuilder: (_, _) => const Divider(height: 1),
                    itemBuilder: (context, i) {
                      final t = threads[i];
                      final title = t.customer.isNotEmpty ? t.customer : (t.deviceName.isNotEmpty ? t.deviceName : 'מכשיר #${t.deviceId}');
                      return ListTile(
                        leading: CircleAvatar(
                          backgroundColor: t.unread > 0 ? theme.colorScheme.primary : theme.colorScheme.surfaceContainerHighest,
                          child: Text('${t.unread > 0 ? t.unread : ''}',
                              style: TextStyle(color: t.unread > 0 ? Colors.white : theme.colorScheme.onSurfaceVariant)),
                        ),
                        title: Row(children: [
                          Expanded(child: Text(title, maxLines: 1, overflow: TextOverflow.ellipsis,
                              style: TextStyle(fontWeight: t.unread > 0 ? FontWeight.w800 : FontWeight.w500))),
                          Text(_ago(t.lastTime), style: theme.textTheme.bodySmall),
                        ]),
                        subtitle: Text('${t.lastFromSupport ? 'אתה: ' : ''}${t.lastText}',
                            maxLines: 1, overflow: TextOverflow.ellipsis),
                        onTap: () async {
                          await Navigator.of(context).push(MaterialPageRoute(
                            builder: (_) => _AdminThreadScreen(controller: c, thread: t, title: title),
                          ));
                          _load();
                        },
                      );
                    },
                  ),
      ),
    );
  }
}

/// שיחה אחת מצד המנהל: קורא את ההודעות ועונה ללקוח.
class _AdminThreadScreen extends StatefulWidget {
  const _AdminThreadScreen({required this.controller, required this.thread, required this.title});
  final AppController controller;
  final SupportThreadInfo thread;
  final String title;

  @override
  State<_AdminThreadScreen> createState() => _AdminThreadScreenState();
}

class _AdminThreadScreenState extends State<_AdminThreadScreen> {
  static const _poll = Duration(seconds: 6);
  final _text = TextEditingController();
  final _scroll = ScrollController();
  List<SupportMessage>? _messages;
  bool _sending = false;
  Timer? _timer;

  AppController get c => widget.controller;
  int get _deviceId => widget.thread.deviceId;

  @override
  void initState() {
    super.initState();
    _load();
    _timer = Timer.periodic(_poll, (_) => _load());
  }

  @override
  void dispose() {
    _timer?.cancel();
    _text.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final msgs = await c.adminThread(_deviceId);
    if (!mounted) return;
    setState(() => _messages = msgs);
    await _toBottom();
  }

  Future<void> _toBottom() async {
    await WidgetsBinding.instance.endOfFrame;
    if (mounted && _scroll.hasClients) {
      _scroll.jumpTo(_scroll.position.maxScrollExtent);
    }
  }

  Future<void> _send() async {
    final text = _text.text.trim();
    if (text.isEmpty || _sending) return;
    setState(() => _sending = true);
    final error = await c.adminReply(_deviceId, text);
    if (!mounted) return;
    setState(() => _sending = false);
    if (error != null) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(error)));
      return;
    }
    _text.clear();
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final list = _messages;
    return Scaffold(
      appBar: AppBar(title: Text(widget.title)),
      body: Column(
        children: [
          Expanded(
            child: list == null
                ? const Center(child: CircularProgressIndicator())
                : ListView(
                    controller: _scroll,
                    padding: const EdgeInsets.all(16),
                    children: [
                      for (final m in list)
                        Align(
                          alignment: m.fromSupport ? AlignmentDirectional.centerEnd : AlignmentDirectional.centerStart,
                          child: Container(
                            margin: const EdgeInsets.symmetric(vertical: 4),
                            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                            constraints: const BoxConstraints(maxWidth: 320),
                            decoration: BoxDecoration(
                              color: m.fromSupport
                                  ? theme.colorScheme.primary.withValues(alpha: .15)
                                  : theme.colorScheme.surfaceContainerHighest,
                              borderRadius: BorderRadius.circular(14),
                            ),
                            child: Text(m.text),
                          ),
                        ),
                    ],
                  ),
          ),
          SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(12, 6, 12, 10),
              child: Row(children: [
                Expanded(
                  child: TextField(
                    controller: _text,
                    minLines: 1,
                    maxLines: 4,
                    decoration: const InputDecoration(hintText: 'תשובה ללקוח...', border: OutlineInputBorder()),
                  ),
                ),
                const SizedBox(width: 8),
                IconButton.filled(
                  onPressed: _sending ? null : _send,
                  icon: _sending
                      ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.send_rounded),
                ),
              ]),
            ),
          ),
        ],
      ),
    );
  }
}
