import 'package:flutter/material.dart';

import '../app_controller.dart';

/// מסך פתיחה: הצטרפות לשירות (טופס) או הפעלה עם קוד שכבר התקבל.
class WelcomeScreen extends StatelessWidget {
  const WelcomeScreen({super.key, required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 440),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Center(
                    child: Container(
                      width: 96,
                      height: 96,
                      alignment: Alignment.center,
                      decoration: BoxDecoration(color: scheme.primaryContainer, shape: BoxShape.circle),
                      child: Icon(Icons.shield_outlined, size: 56, color: scheme.primary),
                    ),
                  ),
                  const SizedBox(height: 20),
                  Text('FilterPhone', textAlign: TextAlign.center, style: theme.textTheme.headlineMedium),
                  const SizedBox(height: 8),
                  Text('סינון אינטרנט ותמונות לכל הטלפון',
                      textAlign: TextAlign.center, style: theme.textTheme.titleMedium),
                  const SizedBox(height: 28),
                  const _Feature(icon: Icons.image_outlined, text: 'סינון תמונות חכם — בכל אתר, לפי הרמה שתבחרו'),
                  const _Feature(icon: Icons.apps_outlined, text: 'חסימת רשתות, אפליקציות, סטטוסים ותמונות פרופיל'),
                  const _Feature(icon: Icons.lock_outline, text: 'רק המנהל משנה הגדרות — אי אפשר לעקוף מהטלפון'),
                  if (controller.notice != null && controller.recognized != null) ...[
                    const SizedBox(height: 20),
                    Card(
                      color: theme.colorScheme.secondaryContainer,
                      child: Padding(
                        padding: const EdgeInsets.all(14),
                        child: Text(controller.notice!, textAlign: TextAlign.center),
                      ),
                    ),
                  ],
                  const SizedBox(height: 28),
                  FilledButton.icon(
                    onPressed: controller.openRegister,
                    icon: const Icon(Icons.person_add_alt_1),
                    label: const Text('הצטרפות לשירות'),
                  ),
                  const SizedBox(height: 12),
                  OutlinedButton.icon(
                    style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(52)),
                    onPressed: controller.openActivation,
                    icon: const Icon(Icons.vpn_key_outlined),
                    label: const Text('יש לי קוד הפעלה'),
                  ),
                  const SizedBox(height: 4),
                  TextButton.icon(
                    onPressed: controller.openRestore,
                    icon: const Icon(Icons.restore_rounded),
                    label: const Text('כבר יש לי חשבון — שחזור לפי מספר טלפון'),
                  ),
                  const SizedBox(height: 24),
                  Text(
                    'ההצטרפות: ממלאים פרטים ובוחרים מה לחסום → הבקשה מגיעה אלינו → '
                    'אחרי הסדרת התשלום החיבור נפתח, והטלפון מתחבר לבד.',
                    textAlign: TextAlign.center,
                    style: theme.textTheme.bodySmall,
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _Feature extends StatelessWidget {
  const _Feature({required this.icon, required this.text});
  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: Row(children: [
          Icon(icon, color: Theme.of(context).colorScheme.primary),
          const SizedBox(width: 12),
          Expanded(child: Text(text, style: Theme.of(context).textTheme.bodyLarge)),
        ]),
      );
}
