import 'package:flutter/material.dart';

import '../api_client.dart';

/// בחירת רמת סינון — כרטיס לכל רמה, עם הסבר מלא.
class LevelCards extends StatelessWidget {
  const LevelCards({
    super.key,
    required this.levels,
    required this.selected,
    required this.onSelect,
    this.current,
  });

  final List<LevelInfo> levels;
  final int selected;
  final ValueChanged<int> onSelect;
  final int? current; // הרמה שפעילה היום (בבקשת שינוי)

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;
    return Column(
      children: [
        for (final l in levels)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Material(
              color: l.level == selected ? scheme.primaryContainer : scheme.surface,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(16),
                side: BorderSide(
                  color: l.level == selected ? scheme.primary : scheme.outlineVariant,
                  width: l.level == selected ? 2 : 1,
                ),
              ),
              child: InkWell(
                borderRadius: BorderRadius.circular(16),
                onTap: () => onSelect(l.level),
                child: Padding(
                  padding: const EdgeInsets.all(14),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Icon(l.level == selected ? Icons.radio_button_checked : Icons.radio_button_off,
                          color: l.level == selected ? scheme.primary : scheme.outline),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Wrap(
                              spacing: 8,
                              crossAxisAlignment: WrapCrossAlignment.center,
                              children: [
                                Text('רמה ${l.level} · ${l.name}',
                                    style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
                                if (current == l.level) const _Tag('הרמה שלך היום'),
                              ],
                            ),
                            const SizedBox(height: 2),
                            Text(l.short, style: theme.textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w500)),
                            const SizedBox(height: 4),
                            Text(l.desc, style: theme.textTheme.bodySmall),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }
}

class _Tag extends StatelessWidget {
  const _Tag(this.text);
  final String text;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: scheme.secondaryContainer,
        borderRadius: BorderRadius.circular(99),
      ),
      child: Text(text, style: Theme.of(context).textTheme.labelSmall),
    );
  }
}

/// בחירת חסימות: "הכי מבוקשים" מודגשים למעלה, ואז כל האפשרויות לפי קטגוריה.
class OptionsPicker extends StatelessWidget {
  const OptionsPicker({super.key, required this.catalog, required this.selected, required this.onChanged});

  final Catalog catalog;
  final Set<String> selected;
  final ValueChanged<Set<String>> onChanged;

  void _toggle(String id, bool on) {
    final next = {...selected};
    on ? next.add(id) : next.remove(id);
    onChanged(next);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Card(
          color: scheme.tertiaryContainer.withValues(alpha: 0.55),
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(children: [
                  Icon(Icons.star_rounded, color: scheme.tertiary),
                  const SizedBox(width: 6),
                  Text('הכי מבוקשים', style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
                ]),
                const SizedBox(height: 4),
                Text('לחיצה אחת — וזה מסומן. אפשר לסמן כמה שרוצים.', style: theme.textTheme.bodySmall),
                const SizedBox(height: 10),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    for (final o in catalog.popular)
                      FilterChip(
                        label: Text(o.title),
                        selected: selected.contains(o.id),
                        onSelected: (on) => _toggle(o.id, on),
                      ),
                  ],
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 12),
        for (final cat in catalog.categories)
          if (catalog.inCategory(cat.id).isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Card(
                clipBehavior: Clip.antiAlias,
                child: ExpansionTile(
                  initiallyExpanded: catalog.inCategory(cat.id).any((o) => selected.contains(o.id)),
                  shape: const Border(),
                  title: Text(cat.title, style: const TextStyle(fontWeight: FontWeight.w600)),
                  subtitle: Text(_countText(cat.id), style: theme.textTheme.bodySmall),
                  children: [
                    for (final o in catalog.inCategory(cat.id))
                      CheckboxListTile(
                        value: selected.contains(o.id),
                        onChanged: (v) => _toggle(o.id, v ?? false),
                        controlAffinity: ListTileControlAffinity.leading,
                        title: Text(o.title),
                        subtitle: o.desc.isEmpty && o.warn.isEmpty
                            ? null
                            : Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  if (o.desc.isNotEmpty) Text(o.desc),
                                  if (o.warn.isNotEmpty)
                                    Padding(
                                      padding: const EdgeInsets.only(top: 2),
                                      child: Text('⚠ ${o.warn}', style: TextStyle(color: scheme.error)),
                                    ),
                                ],
                              ),
                      ),
                  ],
                ),
              ),
            ),
      ],
    );
  }

  String _countText(String cat) {
    final opts = catalog.inCategory(cat);
    final n = opts.where((o) => selected.contains(o.id)).length;
    return n == 0 ? '${opts.length} אפשרויות' : '$n נבחרו מתוך ${opts.length}';
  }
}

/// כותרת קטנה לסעיף.
class SectionTitle extends StatelessWidget {
  const SectionTitle(this.text, {super.key, this.subtitle});
  final String text;
  final String? subtitle;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(text, style: theme.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w600)),
          if (subtitle != null) ...[
            const SizedBox(height: 4),
            Text(subtitle!, style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
          ],
        ],
      ),
    );
  }
}

/// הודעה (שגיאה / מידע) עם כפתור סגירה.
class MessageCard extends StatelessWidget {
  const MessageCard({super.key, required this.text, this.isError = false, this.onClose});
  final String text;
  final bool isError;
  final VoidCallback? onClose;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Card(
      color: isError ? scheme.errorContainer : scheme.secondaryContainer,
      child: ListTile(
        leading: Icon(isError ? Icons.error_outline : Icons.info_outline),
        title: Text(text),
        trailing: onClose == null ? null : IconButton(icon: const Icon(Icons.close), onPressed: onClose),
      ),
    );
  }
}
