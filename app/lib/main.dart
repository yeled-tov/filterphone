import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import 'src/app_controller.dart';
import 'src/screens/registration_screen.dart';
import 'src/screens/home_screen.dart';
import 'src/theme.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(FilterPhoneApp(controller: AppController()..start()));
}

class FilterPhoneApp extends StatefulWidget {
  const FilterPhoneApp({super.key, required this.controller});
  final AppController controller;

  @override
  State<FilterPhoneApp> createState() => _FilterPhoneAppState();
}

class _FilterPhoneAppState extends State<FilterPhoneApp> with WidgetsBindingObserver {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    widget.controller.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // חוזרים מההגדרות (אחרי התקנת התעודה / הפעלת always-on) — מרעננים מיד
    if (state == AppLifecycleState.resumed && widget.controller.phase == Phase.home) {
      widget.controller.refresh();
    }
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'FilterPhone',
      debugShowCheckedModeBanner: false,
      locale: const Locale('he'),
      supportedLocales: const [Locale('he')],
      localizationsDelegates: const [
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      theme: buildTheme(Brightness.light),
      darkTheme: buildTheme(Brightness.dark),
      home: ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) => switch (widget.controller.phase) {
          Phase.loading => const Scaffold(body: Center(child: CircularProgressIndicator())),
          Phase.activation => RegistrationScreen(controller: widget.controller),
          Phase.home => HomeScreen(controller: widget.controller),
          Phase.unsupported => const _Unsupported(),
        },
      ),
    );
  }
}

class _Unsupported extends StatelessWidget {
  const _Unsupported();

  @override
  Widget build(BuildContext context) => const Scaffold(
        body: Center(
          child: Padding(
            padding: EdgeInsets.all(32),
            child: Text(
              'הגרסה לאייפון בדרך.\nבינתיים FilterPhone זמין לאנדרואיד.',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 18),
            ),
          ),
        ),
      );
}
