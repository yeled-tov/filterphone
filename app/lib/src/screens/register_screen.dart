import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api_client.dart';
import '../app_controller.dart';
import 'widgets.dart';

/// טופס ההצטרפות, בארבעה שלבים: פרטים → רמת סינון → מה עוד לחסום → סיכום ושליחה.
class RegisterScreen extends StatefulWidget {
  const RegisterScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends State<RegisterScreen> {
  static const _steps = ['פרטים', 'רמת סינון', 'חסימות', 'שליחה'];
  int _step = 0;
  final _detailsKey = GlobalKey<FormState>();
  late final TextEditingController _name, _phone, _email, _device, _note;

  RegistrationForm get form => widget.controller.form;

  @override
  void initState() {
    super.initState();
    _name = TextEditingController(text: form.name);
    _phone = TextEditingController(text: form.phone);
    _email = TextEditingController(text: form.email);
    _device = TextEditingController(text: form.deviceName);
    _note = TextEditingController(text: form.note);
  }

  @override
  void dispose() {
    for (final c in [_name, _phone, _email, _device, _note]) {
      c.dispose();
    }
    super.dispose();
  }

  void _save() {
    form
      ..name = _name.text
      ..phone = _phone.text
      ..email = _email.text
      ..deviceName = _device.text
      ..note = _note.text;
  }

  Future<void> _next() async {
    FocusScope.of(context).unfocus();
    if (_step == 0 && !(_detailsKey.currentState?.validate() ?? false)) return;
    _save();
    if (_step < _steps.length - 1) {
      setState(() => _step++);
      return;
    }
    await widget.controller.submitRegistration();
  }

  void _back() {
    _save();
    if (_step == 0) {
      widget.controller.backToWelcome();
    } else {
      setState(() => _step--);
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    final catalog = c.catalog;
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) _back();
      },
      child: Scaffold(
        appBar: AppBar(
          title: const Text('הצטרפות ל-FilterPhone'),
          leading: IconButton(icon: const Icon(Icons.arrow_back), onPressed: _back, tooltip: 'חזרה'),
        ),
        body: SafeArea(
          child: Column(
            children: [
              _StepBar(steps: _steps, current: _step),
              Expanded(
                child: catalog == null
                    ? _CatalogLoading(controller: c)
                    : ListView(
                        padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
                        children: [
                          switch (_step) {
                            0 => _details(),
                            1 => _level(catalog),
                            2 => _blocks(catalog),
                            _ => _summary(catalog),
                          },
                          if (c.error != null) ...[
                            const SizedBox(height: 12),
                            MessageCard(text: c.error!, isError: true, onClose: c.clearNotice),
                          ],
                        ],
                      ),
              ),
              _BottomBar(
                step: _step,
                last: _step == _steps.length - 1,
                busy: c.busy,
                enabled: catalog != null,
                onBack: _back,
                onNext: _next,
              ),
            ],
          ),
        ),
      ),
    );
  }

  // ---------------------------------------------------------------- שלב 1: פרטים

  Widget _details() => Form(
        key: _detailsKey,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const SectionTitle('הפרטים שלכם', subtitle: 'כדי שנוכל לחזור אליכם ולפתוח את החיבור.'),
            TextFormField(
              controller: _name,
              textInputAction: TextInputAction.next,
              decoration: const InputDecoration(labelText: 'שם מלא', prefixIcon: Icon(Icons.person_outline)),
              validator: (v) => (v ?? '').trim().length < 2 ? 'נא למלא שם מלא' : null,
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _phone,
              keyboardType: TextInputType.phone,
              textDirection: TextDirection.ltr,
              textInputAction: TextInputAction.next,
              inputFormatters: [FilteringTextInputFormatter.allow(RegExp(r'[0-9+\- ]'))],
              decoration: const InputDecoration(labelText: 'טלפון', hintText: '050-1234567', prefixIcon: Icon(Icons.phone_outlined)),
              validator: (v) => RegExp(r'\d').allMatches(v ?? '').length < 9 ? 'מספר טלפון לא תקין' : null,
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _email,
              keyboardType: TextInputType.emailAddress,
              textDirection: TextDirection.ltr,
              textInputAction: TextInputAction.next,
              decoration: const InputDecoration(labelText: 'מייל (לא חובה)', prefixIcon: Icon(Icons.mail_outline)),
              validator: (v) {
                final t = (v ?? '').trim();
                return t.isEmpty || RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]{2,}$').hasMatch(t) ? null : 'כתובת מייל לא תקינה';
              },
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _device,
              textInputAction: TextInputAction.done,
              decoration: const InputDecoration(
                labelText: 'שם המכשיר (לא חובה)',
                hintText: 'למשל: הטלפון של משה',
                prefixIcon: Icon(Icons.smartphone),
              ),
            ),
          ],
        ),
      );

  // ---------------------------------------------------------------- שלב 2: רמה

  Widget _level(Catalog catalog) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const SectionTitle('איזו רמת סינון תמונות?',
              subtitle: 'בחרו את הרמה שמתאימה לכם. הרמה נקבעת סופית על ידי המנהל, ורק הוא יכול לשנות אותה.'),
          LevelCards(
            levels: catalog.levels,
            selected: form.level,
            onSelect: (l) => setState(() => form.level = l),
          ),
        ],
      );

  // ---------------------------------------------------------------- שלב 3: חסימות

  Widget _blocks(Catalog catalog) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const SectionTitle('מה עוד לחסום?',
              subtitle: 'מעבר לסינון התמונות: רשתות חברתיות, וואטסאפ, חנות האפליקציות ועוד. אפשר לדלג.'),
          OptionsPicker(
            catalog: catalog,
            selected: form.options,
            onChanged: (s) => setState(() => form.options
              ..clear()
              ..addAll(s)),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _note,
            minLines: 3,
            maxLines: 6,
            maxLength: 1500,
            decoration: const InputDecoration(
              labelText: 'משהו נוסף? (לא חובה)',
              hintText: 'אתר מסוים לחסום, אפליקציה, בקשה מיוחדת…',
              alignLabelWithHint: true,
            ),
          ),
        ],
      );

  // ---------------------------------------------------------------- שלב 4: סיכום

  Widget _summary(Catalog catalog) {
    final theme = Theme.of(context);
    final level = catalog.level(form.level);
    final titles = form.options.map((id) => catalog.option(id)?.title).whereType<String>().toList();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const SectionTitle('הכול נכון?', subtitle: 'אפשר לחזור ולשנות לפני השליחה.'),
        Card(
          child: Column(children: [
            _SummaryRow(icon: Icons.person_outline, title: 'שם', value: form.name, onEdit: () => setState(() => _step = 0)),
            _SummaryRow(icon: Icons.phone_outlined, title: 'טלפון', value: form.phone, ltr: true),
            if (form.email.trim().isNotEmpty) _SummaryRow(icon: Icons.mail_outline, title: 'מייל', value: form.email, ltr: true),
            const Divider(height: 1),
            _SummaryRow(
              icon: Icons.tune,
              title: 'רמת סינון',
              value: level == null ? '${form.level}' : 'רמה ${level.level} · ${level.name} — ${level.short}',
              onEdit: () => setState(() => _step = 1),
            ),
            const Divider(height: 1),
            ListTile(
              leading: const Icon(Icons.block),
              title: const Text('חסימות'),
              subtitle: titles.isEmpty
                  ? const Text('רק סינון תמונות ואתרים')
                  : Padding(
                      padding: const EdgeInsets.only(top: 6),
                      child: Wrap(spacing: 6, runSpacing: 6, children: [
                        for (final t in titles) Chip(label: Text(t), visualDensity: VisualDensity.compact),
                      ]),
                    ),
              trailing: IconButton(icon: const Icon(Icons.edit_outlined), onPressed: () => setState(() => _step = 2)),
            ),
            if (form.note.trim().isNotEmpty) ...[
              const Divider(height: 1),
              _SummaryRow(icon: Icons.notes, title: 'הערות', value: form.note),
            ],
          ]),
        ),
        const SizedBox(height: 16),
        Card(
          color: theme.colorScheme.secondaryContainer,
          child: const Padding(
            padding: EdgeInsets.all(16),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('מה קורה אחרי השליחה?', style: TextStyle(fontWeight: FontWeight.w600)),
              SizedBox(height: 8),
              Text('1. הבקשה מגיעה אלינו עם כל מה שבחרתם.'),
              Text('2. נחזור אליכם להסדרת התשלום.'),
              Text('3. אחרי האישור החיבור נפתח — והאפליקציה מתחברת לבד.'),
            ]),
          ),
        ),
      ],
    );
  }
}

class _SummaryRow extends StatelessWidget {
  const _SummaryRow({required this.icon, required this.title, required this.value, this.onEdit, this.ltr = false});
  final IconData icon;
  final String title;
  final String value;
  final VoidCallback? onEdit;
  final bool ltr;

  @override
  Widget build(BuildContext context) => ListTile(
        leading: Icon(icon),
        title: Text(title),
        subtitle: Text(value.trim().isEmpty ? '—' : value.trim(),
            textDirection: ltr ? TextDirection.ltr : null, textAlign: ltr ? TextAlign.end : null),
        trailing: onEdit == null ? null : IconButton(icon: const Icon(Icons.edit_outlined), onPressed: onEdit),
      );
}

class _StepBar extends StatelessWidget {
  const _StepBar({required this.steps, required this.current});
  final List<String> steps;
  final int current;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
      child: Row(children: [
        for (var i = 0; i < steps.length; i++) ...[
          Expanded(
            child: Column(children: [
              AnimatedContainer(
                duration: const Duration(milliseconds: 200),
                height: 5,
                decoration: BoxDecoration(
                  color: i <= current ? scheme.primary : scheme.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(99),
                ),
              ),
              const SizedBox(height: 4),
              Text(steps[i],
                  style: TextStyle(
                    fontSize: 12,
                    fontWeight: i == current ? FontWeight.w700 : FontWeight.w400,
                    color: i <= current ? scheme.primary : scheme.onSurfaceVariant,
                  )),
            ]),
          ),
          if (i < steps.length - 1) const SizedBox(width: 6),
        ],
      ]),
    );
  }
}

class _BottomBar extends StatelessWidget {
  const _BottomBar({
    required this.step,
    required this.last,
    required this.busy,
    required this.enabled,
    required this.onBack,
    required this.onNext,
  });
  final int step;
  final bool last;
  final bool busy;
  final bool enabled;
  final VoidCallback onBack;
  final VoidCallback onNext;

  @override
  Widget build(BuildContext context) => Material(
        elevation: 3,
        color: Theme.of(context).colorScheme.surface,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
          child: Row(children: [
            if (step > 0) ...[
              Expanded(
                child: OutlinedButton(
                  style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(52)),
                  onPressed: busy ? null : onBack,
                  child: const Text('הקודם'),
                ),
              ),
              const SizedBox(width: 12),
            ],
            Expanded(
              flex: 2,
              child: FilledButton(
                onPressed: busy || !enabled ? null : onNext,
                child: busy
                    ? const SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.5))
                    : Text(last ? 'שליחת הבקשה' : 'המשך'),
              ),
            ),
          ]),
        ),
      );
}

class _CatalogLoading extends StatelessWidget {
  const _CatalogLoading({required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: controller.error == null
              ? const CircularProgressIndicator()
              : Column(mainAxisSize: MainAxisSize.min, children: [
                  Text(controller.error!, textAlign: TextAlign.center),
                  const SizedBox(height: 12),
                  OutlinedButton(onPressed: controller.loadCatalog, child: const Text('נסו שוב')),
                ]),
        ),
      );
}
