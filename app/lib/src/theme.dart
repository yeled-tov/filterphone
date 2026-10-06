
import 'package:flutter/material.dart';

const _seed = Color(0xFF0B78BD);
const _navy = Color(0xFF123B5D);
const _accent = Color(0xFFF5B800);

class StatusColors {
  static Color ok(BuildContext c) =>
      Theme.of(c).brightness == Brightness.dark ? const Color(0xFF4BC276) : const Color(0xFF1A7F37);
  static Color warn(BuildContext c) =>
      Theme.of(c).brightness == Brightness.dark ? const Color(0xFFF0B44D) : const Color(0xFF9C6500);
  static Color bad(BuildContext c) =>
      Theme.of(c).brightness == Brightness.dark ? const Color(0xFFFF6B61) : const Color(0xFFC62828);
}

ThemeData buildTheme(Brightness brightness) {
  final scheme = ColorScheme.fromSeed(
    seedColor: _seed,
    brightness: brightness,
  ).copyWith(
    primary: brightness == Brightness.dark ? const Color(0xFF66B8F0) : _seed,
    onPrimary: Colors.white,
    primaryContainer: brightness == Brightness.dark ? const Color(0xFF154663) : const Color(0xFFE2F1FA),
    onPrimaryContainer: brightness == Brightness.dark ? Colors.white : _navy,
    secondary: _accent,
    onSecondary: _navy,
  );

  return ThemeData(
    colorScheme: scheme,
    useMaterial3: true,
    scaffoldBackgroundColor: brightness == Brightness.dark ? const Color(0xFF0D151B) : const Color(0xFFF8FAFC),
    fontFamily: 'sans-serif',
    appBarTheme: AppBarTheme(
      backgroundColor: brightness == Brightness.dark ? const Color(0xFF0D151B) : Colors.white,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      centerTitle: false,
      titleTextStyle: TextStyle(
        fontSize: 18,
        fontWeight: FontWeight.w800,
        color: brightness == Brightness.dark ? Colors.white : _navy,
      ),
      iconTheme: IconThemeData(color: brightness == Brightness.dark ? Colors.white : _navy),
    ),
    cardTheme: CardThemeData(
      elevation: 0,
      color: brightness == Brightness.dark ? const Color(0xFF15212A) : Colors.white,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(18),
        side: BorderSide(color: scheme.outlineVariant.withValues(alpha: .65)),
      ),
      margin: EdgeInsets.zero,
    ),
    navigationBarTheme: NavigationBarThemeData(
      height: 76,
      elevation: 10,
      backgroundColor: brightness == Brightness.dark ? const Color(0xFF101A21) : Colors.white,
      indicatorColor: brightness == Brightness.dark ? const Color(0xFF154663) : const Color(0xFFE3F3FD),
      labelTextStyle: WidgetStatePropertyAll(
        TextStyle(
          fontSize: 11,
          fontWeight: FontWeight.w700,
          color: brightness == Brightness.dark ? Colors.white : _navy,
        ),
      ),
    ),
    chipTheme: ChipThemeData(
      side: BorderSide(color: scheme.outlineVariant),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
    ),
    dividerTheme: DividerThemeData(color: scheme.outlineVariant.withValues(alpha: .55), thickness: 1),
    inputDecorationTheme: InputDecorationTheme(
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(16),
        borderSide: BorderSide(color: scheme.outlineVariant),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(16),
        borderSide: BorderSide(color: scheme.outlineVariant),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(16),
        borderSide: BorderSide(color: scheme.primary, width: 1.5),
      ),
      filled: true,
      fillColor: brightness == Brightness.dark ? const Color(0xFF15212A) : Colors.white,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 15),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        minimumSize: const Size.fromHeight(50),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(15)),
        textStyle: const TextStyle(fontSize: 15, fontWeight: FontWeight.w800),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        minimumSize: const Size.fromHeight(46),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
    ),
  );
}
