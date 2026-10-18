import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'api_client.dart';
import 'config.dart';
import 'native_bridge.dart';
import 'support_bot.dart';

/// השלבים של האפליקציה:
///   welcome → register (טופס) → pending (מחכה לאישור המנהל) → home
///   welcome → activation (קוד) → home
///   welcome → restore (מספר טלפון) → home (אותו טלפון) / pending (טלפון אחר — החלפה באישור המנהל)
enum Phase { loading, welcome, register, activation, restore, pending, home, unsupported }

/// סעיף ברשימת "מה צריך כדי שהטלפון יהיה מוגן".
enum Check { vpn, certificate, alwaysOn, privateDns, guard, battery }

/// הצעד שההקמה בלחיצה אחת מחכה לו (חלון של אנדרואיד שהמשתמש צריך לאשר).
enum SetupStep { automatic, vpn, certificate, battery, guard, done }

/// אחסון קטן ומאובטח (Keystore באנדרואיד / Keychain באייפון).
abstract class KeyValueStore {
  Future<String?> read(String key);
  Future<void> write(String key, String? value);
}

class SecureKeyValueStore implements KeyValueStore {
  static const _s = FlutterSecureStorage();
  @override
  Future<String?> read(String key) => _s.read(key: key);
  @override
  Future<void> write(String key, String? value) =>
      value == null ? _s.delete(key: key) : _s.write(key: key, value: value);
}

/// כל המצב של האפליקציה במקום אחד. המסכים רק מציגים אותו וקוראים לפעולות.
class AppController extends ChangeNotifier {
  AppController({NativeBridge? native, ApiClient? api, KeyValueStore? store})
      : native = native ?? ChannelNativeBridge(),
        api = api ?? ApiClient(),
        store = store ?? SecureKeyValueStore();

  final NativeBridge native;
  final ApiClient api;
  final KeyValueStore store;

  static const _kToken = 'device_token';
  static const _kCa = 'ca_pem';
  static const _kVpnHash = 'vpn_config_hash';
  static const _kGuardHash = 'guard_policy_hash';
  static const _kReport = 'last_report';
  static const _kSummary = 'pending_summary';
  static const reportEvery = Duration(hours: 12);

  Phase phase = Phase.loading;
  bool busy = false;
  String? error;
  String? notice;

  String? _token;
  String? _caPem;
  DeviceStatus? device;
  VpnStatus vpn = VpnStatus.down;
  bool caInstalled = false;
  String privateDns = 'off';
  bool deviceOwner = false;
  bool guardEnabled = false;
  bool serverReachable = true;
  bool deviceRemoved = false;
  bool adminPauseActive = false;
  DateTime? pauseUntil;
  bool rootProvisioning = false;

  /// מה השרת אמר על הטלפון הזה בפתיחה (לפי מזהה אנדרואיד): restorable / pending / admin_disabled.
  String? recognized;

  /// הקמה בלחיצה אחת: הצעד הנוכחי (null = לא רצה), ומה בוצע אוטומטית (root / מנהל מכשיר).
  SetupStep? setupStep;
  Map<String, Object?>? setupReport;
  final Set<SetupStep> _setupTried = {};

  /// סוללה, מנהל מכשיר, סנכרון אחרון, בדיקת VPN אחרונה — מהקוד הנייטיבי.
  ProtectionStatus protection = ProtectionStatus.unknown;
  DateTime? _lastSync;

  /// הקטלוג (רמות ואפשרויות) — לטופס ההרשמה ולבקשת שינוי.
  Catalog? catalog;

  /// הטופס בזמן ההרשמה (נשמר בין השלבים).
  RegistrationForm form = RegistrationForm();

  /// מה נשלח בבקשת ההצטרפות — מוצג במסך ההמתנה.
  Map<String, Object?>? pendingSummary;

  /// שחזור לפי מספר טלפון: התשובה האחרונה (טלפון אחר → בחירה בין החלפה למכשיר נוסף).
  RestoreLookup? restoreResult;
  String restorePhone = '';

  /// השיחה עם הנציג (מהשרת). null = עוד לא נטענה.
  List<SupportMessage>? supportThread;
  bool supportSending = false;
  String? _requestedTab;
  String _appVersion = '';
  String androidId = '';          // ה-ANDROID_ID המקורי של המכשיר — לתצוגה למשתמש ולתמיכה

  /// הגדרות תצוגה (נשמרות בטלפון): ערכת נושא system / light / dark, וגודל טקסט (1.0 = רגיל).
  String themeMode = 'system';
  double textScale = 1.0;
  static const _kTheme = 'ui_theme';
  static const _kTextScale = 'ui_text_scale';
  static const textScales = [1.0, 1.15, 1.3];

  /// טאב העדכונים בחנות: אפליקציות שבחרו לא לעדכן (⊘) — לא נספרות ולא נכללות ב"עדכון הכול".
  Set<String> updateExcluded = {};
  static const _kUpdateExcluded = 'store_update_excluded';

  /// מצב ההרשאות נקרא מהטלפון לפחות פעם אחת (לפני זה לא "דורשים" כלום — עוד לא ידוע מה חסר).
  bool statusLoaded = false;

  /// גדל בכל חזרה לאפליקציה — חלון "חסרה הרשאה" מוצג שוב אם עדיין חסר.
  int resumeTick = 0;

  String get appVersion => _appVersion;

  Timer? _timer;
  StreamSubscription<String>? _vpnSub;

  // ------------------------------------------------------------------ מצב מחושב

  bool get guardNeeded => device?.needsGuard ?? false;

  /// מסכים, הגנות מיוחדות (סטטוסים, תמונות פרופיל) וחסימת מילות חיפוש — רק במגן, גם עם מנהל מכשיר.
  bool get _guardOnlyNeeded {
    final p = device?.guardPolicy ?? const {};
    return (p['blocked_screens'] as List? ?? const []).isNotEmpty ||
        (p['protections'] as Map? ?? const {}).isNotEmpty ||
        (p['keywords'] as Map?)?['enabled'] == true;
  }

  Map<Check, bool> get checks => {
        Check.vpn: vpn.isUp,
        Check.certificate: caInstalled,
        Check.alwaysOn: vpn.alwaysOn == true,
        Check.privateDns: privateDns != 'hostname',
        Check.guard: guardEnabled || (deviceOwner && !_guardOnlyNeeded),
        Check.battery: protection.backgroundOk,
      };

  /// מה מהנדרש חסר עכשיו (בלי "תמיד מופעל", שהוא המלצה).
  List<Check> get missingRequired => [
        for (final k in [Check.vpn, Check.certificate, Check.privateDns, Check.battery, if (guardNeeded) Check.guard])
          if (!checks[k]!) k,
      ];

  /// מה לדרוש מהמשתמש עכשיו — בחלון שקופץ לבד. ריק כשאין טעם לדרוש: עוד לא ידוע, השהיה מאושרת,
  /// מכשיר לא פעיל / הוסר, או שההקמה בלחיצה אחת עוד רצה (היא פותחת את החלונות בעצמה).
  List<Check> get attentionNeeded {
    if (phase != Phase.home || !statusLoaded || adminPauseActive || deviceRemoved) return const [];
    if (!(device?.isActive ?? false)) return const [];
    if (setupStep != null && setupStep != SetupStep.done) return const [];
    return missingRequired;
  }

  /// מוגן = המנהרה פעילה, התעודה מותקנת, DNS פרטי לא עוקף, אנדרואיד לא עוצר את FilterPhone ברקע
  /// (סוללה) — ואם המנהל חסם אפליקציות, גם מגן FilterPhone פעיל.
  /// "תמיד מופעל" מומלץ מאוד אבל לא תנאי (אנדרואיד לא תמיד מדווח עליו).
  bool get isProtected =>
      checks[Check.vpn]! &&
      checks[Check.certificate]! &&
      checks[Check.privateDns]! &&
      checks[Check.battery]! &&
      (!guardNeeded || checks[Check.guard]!);

  // ------------------------------------------------------------------ הפעלה

  Future<void> start() async {
    await _loadUiPrefs();
    try {
      _appVersion = '${(await native.deviceInfo())['appVersion'] ?? ''}';
      androidId = await native.androidId();
    } on MissingPluginException {
      phase = Phase.unsupported; // אייפון — עד שה-Network Extension יתווסף
      notifyListeners();
      return;
    }
    _token = await store.read(_kToken);
    _caPem = await store.read(_kCa);
    if (_token == null) {
      phase = Phase.welcome;
      notifyListeners();
      await _autoProvision();
      if (phase == Phase.welcome) await _recognizeDevice();
      return;
    }
    final summary = await store.read(_kSummary);
    if (summary != null) pendingSummary = Map<String, Object?>.from(jsonDecode(summary) as Map);
    // יש הגדרות VPN שמורות = המכשיר כבר אושר בעבר. אחרת — עדיין מחכים לאישור.
    if (await store.read(_kVpnHash) != null) {
      await _enterHome();
    } else {
      phase = Phase.pending;
      notifyListeners();
      unawaited(_loadCatalogQuietly()); // לשמות החסימות במסך ההמתנה
      await refreshPending();
      _startTimer(refreshPending);
    }
  }

  Future<void> _enterHome() async {
    phase = Phase.home;
    notifyListeners();
    _listenVpn();
    await _nativeCredentials();
    await refresh();
    unawaited(_syncVpnConfig());
    unawaited(checkLaunchTarget());          // נפתחנו מהתראה "יש תשובה מהתמיכה"
    _startTimer(refresh);
  }

  /// טלפון שהוקם כמנהל מכשיר ב-QR: קוד ההפעלה הגיע בתוך ה-QR — מתחברים לבד, בלי הקלדה.
  Future<void> _autoProvision() async {
    Map<String, String?>? info;
    try {
      info = await native.provisioningInfo();
    } on PlatformException catch (e) {
      debugPrint('provisioning info failed: $e');
    } on MissingPluginException {
      return;
    }
    final code = info?['code'];
    if (code == null || code.isEmpty) return;
    notice = 'הטלפון הוקם כמכשיר מנוהל — מתחבר לסינון…';
    final ok = await activate(code, '');
    if (ok) {
      await native.clearProvisioning();
    } else {
      phase = Phase.activation; // הקוד לא עבד (פג תוקף?) — אפשר להזין ידנית
      notifyListeners();
    }
  }

  /// התקנה מחדש באותו טלפון: המזהה של אנדרואיד כבר מוכר בשרת → פותחים ישר את מסך השחזור
  /// (הלקוח מאשר עם מספר הטלפון וההגנה חוזרת מיד). טלפון אחר / אחרי איפוס → מסך הפתיחה הרגיל,
  /// ושם "כבר יש לי חשבון" מציע החלפת מכשיר לפי מספר הטלפון. שקט בכשל (אין רשת).
  Future<void> _recognizeDevice() async {
    try {
      final uid = await _deviceUid();
      if (uid.isEmpty) return;
      final r = await api.recognize(uid);
      if (r['known'] != true || phase != Phase.welcome) return;
      recognized = r['status'] as String?;
      if (recognized == 'restorable') {
        openRestore();
        notice = 'זיהינו שהטלפון הזה כבר היה מוגן ב-FilterPhone. הזינו את מספר הטלפון שנרשם — וההגנה תחזור מיד.';
      } else if (recognized == 'pending') {
        notice = 'מהטלפון הזה כבר נשלחה בקשה — היא ממתינה לאישור. אפשר לבדוק שוב ב"כבר יש לי חשבון".';
      } else if (recognized == 'admin_disabled') {
        notice = 'ההגנה בטלפון הזה הושבתה על ידי המנהל. לחידוש — פנו לתמיכה.';
      }
      notifyListeners();
    } catch (e) {
      debugPrint('recognize skipped: $e');
    }
  }

  /// הדיווח התקופתי מהרקע צריך את כתובת השרת והאסימון (מוצפנים בצד הנייטיבי).
  Future<void> _nativeCredentials() async {
    final token = _token;
    if (token == null) return;
    try {
      await native.setCredentials(api.baseUrl, token);
      await native.startProtection();
    } on PlatformException catch (e) {
      debugPrint('native credentials failed: $e');
    }
  }

  // ------------------------------------------------------------------ ניווט בפתיחה

  void openActivation() {
    error = null;
    phase = Phase.activation;
    notifyListeners();
  }

  Future<void> openRegister() async {
    error = null;
    phase = Phase.register;
    notifyListeners();
    if (catalog == null) await loadCatalog();
  }

  void openRestore() {
    error = null;
    restoreResult = null;
    phase = Phase.restore;
    notifyListeners();
  }

  void backToWelcome() {
    error = null;
    phase = Phase.welcome;
    notifyListeners();
  }

  Future<void> _loadCatalogQuietly() async {
    try {
      catalog ??= await api.catalog();
      notifyListeners();
    } on ApiException catch (e) {
      debugPrint('catalog not loaded: $e');
    }
  }

  Future<void> loadCatalog() async {
    try {
      catalog = await api.catalog();
      error = null;
    } on ApiException catch (e) {
      error = e.message;
    }
    notifyListeners();
  }

  // ------------------------------------------------------------------ הרשמה

  /// שליחת בקשת ההצטרפות. המכשיר נשאר "ממתין" עד שהמנהל מאשר (אחרי הסדרת התשלום).
  Future<bool> submitRegistration() => _guard(() async {
        final info = await native.deviceInfo();
        final publicKey = await native.generateKeyPair();
        if (form.deviceName.trim().isEmpty) {
          form.deviceName = '${info['manufacturer'] ?? ''} ${info['model'] ?? ''}'.trim();
        }
        final r = await api.register(
          form: form,
          publicKey: publicKey,
          platform: 'android',
          appVersion: '${info['appVersion'] ?? ''}',
          deviceUid: await _deviceUid(),
        );
        _token = r.token;
        await store.write(_kToken, r.token);
        pendingSummary = {
          'name': form.name.trim(),
          'phone': form.phone.trim(),
          'level': form.level,
          'options': form.options.toList(),
          'note': form.note.trim(),
          'sent_at': DateTime.now().millisecondsSinceEpoch,
        };
        await store.write(_kSummary, jsonEncode(pendingSummary));
        phase = Phase.pending;
        notifyListeners();
        _startTimer(refreshPending);
      });

  /// בזמן ההמתנה: האם המנהל כבר אישר?
  Future<void> refreshPending() async {
    final token = _token;
    if (token == null) return;
    try {
      device = await api.device(token);
      serverReachable = true;
      if (device!.isActive) {
        await _onApproved();
        return;
      }
    } on ApiException catch (e) {
      serverReachable = e.status != 0;
      if (e.isUnauthorized) {
        // הבקשה נמחקה בצד השרת — מתחילים מחדש
        await resetDevice();
        return;
      }
    }
    notifyListeners();
  }

  Future<void> _onApproved() async {
    final token = _token!;
    final bundle = await api.vpnConfig(token);
    await native.saveVpnConfig(bundle.vpn);
    await store.write(_kVpnHash, _hash(bundle.vpn));
    if (bundle.caPem != null) {
      _caPem = bundle.caPem;
      await store.write(_kCa, bundle.caPem);
    }
    await store.write(_kSummary, null);
    pendingSummary = null;
    notice = 'הבקשה אושרה! עכשיו מחברים את הסינון — אשרו את חיבור ה-VPN ואז התקינו את התעודה.';
    phase = Phase.home;
    notifyListeners();
    _listenVpn();
    _startTimer(refresh);
    await _nativeCredentials();
    unawaited(_askNotifications());
    try {
      await _connect();
    } on _UserMessage catch (e) {
      notice = e.message;
    } on PlatformException catch (e) {
      notice = 'החיבור לא הושלם: ${e.message ?? e.code}. נסו שוב מכפתור "חיבור".';
    }
    await refresh();
  }

  /// ביטול בקשה שעוד לא אושרה (או שנדחתה) — וחזרה להתחלה.
  Future<void> cancelRegistration() async {
    final token = _token;
    if (token != null) {
      try {
        await api.cancel(token);
      } on ApiException catch (e) {
        if (!e.isUnauthorized && e.status != 403) {
          error = e.message;
          notifyListeners();
          return;
        }
      }
    }
    await resetDevice();
  }

  // ------------------------------------------------------------------ הפעלה בקוד

  /// הפעלת המכשיר עם קוד מהמנהל.
  Future<bool> activate(String code, String name) async {
    return _guard(() async {
      final info = await native.deviceInfo();
      final publicKey = await native.generateKeyPair();
      final trimmed = code.trim();
      final deviceName = name.trim().isEmpty ? '${info['manufacturer']} ${info['model']}' : name.trim();
      final appVersion = '${info['appVersion'] ?? ''}';
      // קוד החלפת מכשיר (קידומת FPT-) נרשם דרך /v1/transfer ויורש מהמכשיר הישן; אחרת קוד הפעלה רגיל.
      final isTransfer = trimmed.toUpperCase().startsWith('FPT-');
      final uid = await _deviceUid();
      final e = isTransfer
          ? await api.transfer(token: trimmed, publicKey: publicKey, name: deviceName,
              platform: 'android', appVersion: appVersion, deviceUid: uid)
          : await api.enroll(code: trimmed, publicKey: publicKey, name: deviceName,
              platform: 'android', appVersion: appVersion, deviceUid: uid);
      await _finishEnrollment(e);
    });
  }

  /// אחרי הפעלה / החלפה / שחזור: שומרים הגדרות ומתחברים לסינון.
  Future<void> _finishEnrollment(Enrollment e) async {
    await native.saveVpnConfig(e.vpn);
    await store.write(_kToken, e.token);
    await store.write(_kCa, e.caPem);
    await store.write(_kVpnHash, _hash(e.vpn));
    _token = e.token;
    _caPem = e.caPem;
    phase = Phase.home;
    notifyListeners();
    _listenVpn();
    _startTimer(refresh);
    await _nativeCredentials();
    unawaited(_askNotifications());
    try {
      await _connect();
    } on _UserMessage catch (e) {
      notice = e.message; // ההפעלה הצליחה; את ה-VPN אפשר לאשר מהמסך הראשי
    } on PlatformException catch (e) {
      notice = 'החיבור לא הושלם: ${e.message ?? e.code}. נסו שוב מהמסך הראשי.';
    }
  }

  Future<String> _deviceUid() async {
    try {
      return await native.deviceUid();
    } on PlatformException catch (e) {
      debugPrint('device uid unavailable: $e');
      return '';
    }
  }

  // ------------------------------------------------------------------ שחזור לפי מספר טלפון

  /// הלקוח מזין את מספר הטלפון של החשבון. אותו טלפון (אחרי הסרה/התקנה) → ההגנה חוזרת מיד.
  /// טלפון אחר → [restoreResult] עם המכשירים בחשבון, והמסך שואל "להעביר את ההגנה לטלפון הזה?".
  Future<bool> restoreByPhone(String phone) => _guard(() async {
        final info = await native.deviceInfo();
        final uid = await _deviceUid();
        if (uid.isEmpty) throw const _UserMessage('לא ניתן לזהות את הטלפון. עדכנו את האפליקציה ונסו שוב.');
        final publicKey = await native.generateKeyPair();
        restorePhone = phone.trim();
        final r = await api.restore(
          phone: restorePhone,
          deviceUid: uid,
          publicKey: publicKey,
          name: '${info['manufacturer'] ?? ''} ${info['model'] ?? ''}'.trim(),
          platform: 'android',
          appVersion: '${info['appVersion'] ?? ''}',
        );
        if (r.enrollment != null) {
          notice = 'ההגנה שוחזרה — זה אותו טלפון, הכול חזר כמו שהיה.';
          await _finishEnrollment(r.enrollment!);
          return;
        }
        restoreResult = r;
        if (r.status == 'pending') notice = r.message;
      });

  /// "העברת ההגנה לטלפון הזה" (swap, הישן יתנתק) או "מכשיר נוסף" (additional, בתשלום) — למנהל.
  Future<bool> requestRestore({required String mode, int? oldDeviceId}) => _guard(() async {
        final info = await native.deviceInfo();
        final publicKey = await native.publicKey() ?? await native.generateKeyPair();
        final r = await api.restoreRequest(
          phone: restorePhone,
          deviceUid: await _deviceUid(),
          publicKey: publicKey,
          name: '${info['manufacturer'] ?? ''} ${info['model'] ?? ''}'.trim(),
          platform: 'android',
          appVersion: '${info['appVersion'] ?? ''}',
          mode: mode,
          oldDeviceId: oldDeviceId,
        );
        _token = r.token;
        await store.write(_kToken, r.token);
        pendingSummary = {
          'name': restoreResult?.customerName ?? '',
          'phone': restorePhone,
          'note': r.message,
          'sent_at': DateTime.now().millisecondsSinceEpoch,
        };
        await store.write(_kSummary, jsonEncode(pendingSummary));
        restoreResult = null;
        phase = Phase.pending;
        notifyListeners();
        _startTimer(refreshPending);
      });

  // ------------------------------------------------------------------ פעולות

  Future<bool> connectVpn() => _guard(_connect);

  Future<void> _connect() async {
    if (!await native.prepareVpn()) {
      throw const _UserMessage('צריך לאשר את חיבור ה-VPN כדי שהסינון יפעל.');
    }
    await native.connect();
    await refresh();
  }

  Future<bool> installCertificate() => _guard(() async {
        final pem = _caPem;
        if (pem == null) throw const _UserMessage('התעודה עוד לא התקבלה מהשרת.');
        final mode = await native.installCa(pem);
        if (mode == 'settings') {
          notice = 'הקובץ FilterPhone-CA.crt נשמר בהורדות. בהגדרות: '
              'הצפנה ופרטי כניסה ← התקנת אישור ← אישור CA ← בחרו את הקובץ.';
        }
        await refresh();
      });

  Future<void> openVpnSettings() => native.openVpnSettings();

  Future<void> _askNotifications() async {
    try {
      await native.requestNotifications();
    } on PlatformException catch (e) {
      debugPrint('notifications request failed: $e');
    }
  }

  /// פטור מאופטימיזציית סוללה: חלון המערכת. המצב נבדק שוב כשחוזרים לאפליקציה (refresh ב-resume).
  Future<void> requestBatteryExemption() async {
    try {
      final mode = await native.requestBatteryExemption();
      if (mode == 'list') {
        notice = 'ברשימה: בחרו "כל האפליקציות" ← FilterPhone ← "ללא אופטימיזציה" / "ללא הגבלה".';
      } else if (mode == 'app_details') {
        notice = 'בדף האפליקציה: סוללה ← "ללא הגבלה" (ובטלפוני שיאומי: "הפעלה אוטומטית" ← מופעל).';
      }
    } on PlatformException catch (e) {
      error = 'לא נפתח מסך הסוללה: ${e.message ?? e.code}';
    }
    notifyListeners();
  }

  /// גרסה חדשה (בלי מנהל מכשיר): האפליקציה מורידה, בודקת ומבקשת מאנדרואיד להתקין — המשתמש מאשר
  /// בחלון של אנדרואיד. באנדרואיד 16 ה-VPN יורד רק לרגע ההתקנה (באג של אנדרואיד 16 בעדכון כשה-VPN
  /// פעיל). אם אין עדיין אישור "התקנת אפליקציות לא מוכרות" — נפתח המסך לאשר אותו.
  Future<void> openUpdatePage() async {
    error = null;
    try {
      final r = await native.openUpdatePage();
      switch (r) {
        case 'page':
          break;
        case 'installing':
          notice = 'העדכון ירד ונבדק. אשרו את ההתקנה בחלון של אנדרואיד — ההגנה חוזרת מיד אחריה.';
        case 'permission':
          notice = 'כדי לעדכן מתוך האפליקציה: אשרו ל-FilterPhone "התקנת אפליקציות לא מוכרות", חזרו ולחצו שוב על העדכון.';
        case 'busy':
          notice = 'העדכון כבר בהורדה — רגע…';
        default:
          error = 'העדכון לא הותקן ($r). נסו שוב, או הורידו מדף ההורדה.';
      }
    } on PlatformException catch (e) {
      error = 'לא נפתח העדכון: ${e.message ?? e.code}';
    }
    notifyListeners();
  }

  /// מנהל מכשיר רגיל — רק מקשה על הסרה (לא נעילה מלאה). המשתמש מאשר בחלון של המערכת.
  Future<void> requestDeviceAdmin() async {
    try {
      await native.requestDeviceAdmin();
    } on PlatformException catch (e) {
      error = 'לא נפתח מסך מנהל המכשיר: ${e.message ?? e.code}';
      notifyListeners();
    }
  }

  Future<void> openGuardSettings() async {
    notice = 'בהגדרות הנגישות: "אפליקציות מותקנות" ← "מגן FilterPhone" ← הפעלה. '
        'אם האפשרות אפורה: הגדרות ← אפליקציות ← FilterPhone ← ⋮ ← "התרת הגדרות מוגבלות", ואז שוב.';
    notifyListeners();
    await native.openGuardSettings();
  }

  /// בקשת שינוי (רמה / חסימות) — נשלחת למנהל. רק הוא מבצע.
  Future<bool> requestChange({int? level, List<String>? options, String note = ''}) => _guard(() async {
        final token = _token;
        if (token == null) return;
        await api.requestChange(token, level: level, options: options, note: note);
        notice = 'הבקשה נשלחה למנהל. נעדכן כאן כשהיא תטופל.';
        await refresh();
      });

  void clearNotice() {
    notice = null;
    error = null;
    notifyListeners();
  }

  /// מחיקת ההגדרות מהטלפון (למשל אחרי שהמנהל הסיר את המכשיר).
  Future<void> resetDevice() async {
    _timer?.cancel();
    await _vpnSub?.cancel();
    _vpnSub = null;
    await native.reset();
    try {
      await native.setGuardPolicy(const {});
    } on PlatformException catch (e) {
      debugPrint('guard reset failed: $e');
    }
    for (final k in [_kToken, _kCa, _kVpnHash, _kGuardHash, _kReport, _kSummary]) {
      await store.write(k, null);
    }
    _token = null;
    _caPem = null;
    device = null;
    pendingSummary = null;
    deviceRemoved = false;
    adminPauseActive = false;
    form = RegistrationForm();
    vpn = VpnStatus.down;
    phase = Phase.welcome;
    notifyListeners();
  }

  // ------------------------------------------------------------------ רענון

  Future<void> refresh({bool forceSync = false}) async {
    try {
      vpn = await native.vpnStatus();
      privateDns = await native.privateDnsMode();
      deviceOwner = await native.isDeviceOwner();
      guardEnabled = await native.isGuardEnabled();
      if (_caPem != null) caInstalled = await native.isCaInstalled(_caPem!);
      protection = await native.protectionStatus();
      statusLoaded = true;
    } on PlatformException catch (e) {
      debugPrint('native refresh failed: $e');
    }
    // דיווח לשרת (ואישור גרסת המדיניות) — לכל היותר פעם בדקה כשהאפליקציה פתוחה
    final now = DateTime.now();
    if (_token != null && (forceSync || _lastSync == null || now.difference(_lastSync!) > const Duration(minutes: 1))) {
      _lastSync = now;
      try {
        await native.syncNow();
      } on PlatformException catch (e) {
        debugPrint('sync failed: $e');
      }
    }
    try {
      final pauseState = await native.adminPauseStatus();
      adminPauseActive = pauseState['active'] == true;
      final until = (pauseState['until'] as num?)?.toInt() ?? 0;
      pauseUntil = adminPauseActive && until > 0 ? DateTime.fromMillisecondsSinceEpoch(until) : null;
    } on PlatformException catch (e) {
      debugPrint('pause status failed: $e');
    }
    final token = _token;
    if (token != null) {
      try {
        device = await api.device(token);
        serverReachable = true;
        deviceRemoved = false;
        await _applyGuardPolicy(device!.guardPolicy);
        unawaited(_maybeReport(token));
      } on ApiException catch (e) {
        serverReachable = e.status != 0;
        deviceRemoved = e.isUnauthorized;
      }
    }
    notifyListeners();
  }

  /// "החזרת ההגנה" בזמן השהיה: VPN, מגן ומנהל מכשיר חוזרים מיד — וגם בשרת ההשהיה נגמרת.
  /// עובד גם בלי שרת (המקור הוא הטלפון); השהיה חדשה מהמנהל תוצג שוב.
  Future<bool> resumeProtection() => _guard(() async {
        if (!await native.resumeProtectionLocally()) {
          throw const _UserMessage('הטלפון לא הצליח להחזיר את ההגנה. נסו שוב.');
        }
        adminPauseActive = false;
        pauseUntil = null;
        notice = 'ההגנה חזרה במלואה ✓';
        await refresh(forceSync: true);
      });

  /// "הסרת הכול" — רק בזמן השהיה שהמנהל אישר (נבדק גם בטלפון): ניהול המכשיר, ה-VPN, התעודה
  /// והנתונים נמחקים, השרת מעודכן, ואז נפתח חלון הסרת האפליקציה של אנדרואיד. אין דרך חזרה.
  /// מחזיר דוח (למשל אם התעודה הוסרה או שצריך להסיר אותה ידנית בהגדרות).
  Future<Map<String, Object?>?> uninstallEverything() async {
    if (busy) return null;
    busy = true;
    error = null;
    notifyListeners();
    try {
      final report = await native.uninstallEverything();
      _timer?.cancel();
      await _vpnSub?.cancel();
      _vpnSub = null;
      for (final k in [_kToken, _kCa, _kVpnHash, _kGuardHash, _kReport, _kSummary]) {
        await store.write(k, null);
      }
      _token = null;
      _caPem = null;
      device = null;
      adminPauseActive = false;
      pauseUntil = null;
      vpn = VpnStatus.down;
      phase = Phase.welcome;
      try {
        await native.requestSelfUninstall();
      } on PlatformException catch (e) {
        debugPrint('self uninstall dialog failed: $e');
      }
      return report;
    } on PlatformException catch (e) {
      error = 'ההסרה לא הושלמה: ${e.message ?? e.code}';
      return null;
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  // ------------------------------------------------------------------ הקמה בלחיצה אחת

  /// לוחצים פעם אחת: קודם כל מה שהטלפון יכול לעשות לבד (root / מנהל מכשיר — מנהל מכשיר, מגן,
  /// תעודה, סוללה, VPN), ואז — רק מה שעוד חסר — חלונות האישור של אנדרואיד, אחד אחרי השני.
  /// כשחוזרים מחלון (onAppResumed) ממשיכים לצעד הבא. צעד שהמשתמש דילג עליו לא נפתח שוב בלולאה.
  Future<void> oneTapSetup() async {
    if (setupStep != null && setupStep != SetupStep.done) return;
    _setupTried.clear();
    setupStep = SetupStep.automatic;
    error = null;
    notice = null;
    notifyListeners();
    try {
      setupReport = await native.setupAll(_caPem);
    } on PlatformException catch (e) {
      debugPrint('automatic setup failed: $e');
      setupReport = {'error': e.message ?? e.code};
    }
    await _continueSetup();
  }

  /// הקמה בלחיצה אחת בלי root ובלי מחשב — ניפוי אלחוטי מתוך האפליקציה (כמו סוויצ'): המשתמש הדליק
  /// "ניפוי באגים אלחוטי" והזין את קוד ההתאמה; FilterPhone עושה את כל השאר. מחזיר true בהצלחה.
  Future<bool> wirelessSetup(String code, {int port = 0}) async {
    if (setupStep != null && setupStep != SetupStep.done) return false;
    _setupTried.clear();
    setupStep = SetupStep.automatic;
    error = null;
    notice = null;
    notifyListeners();
    try {
      setupReport = await native.provisionWireless(code.trim(), port, _caPem);
    } on PlatformException catch (e) {
      debugPrint('wireless setup failed: $e');
      setupReport = {'error': e.message ?? e.code};
    }
    final ok = setupReport?['device_owner'] == true;
    if (!ok) {
      final reason = setupReport?['device_owner_result']?.toString() ?? '';
      notice = _wirelessError(reason);
    }
    await _continueSetup();
    return ok;
  }

  /// פותח את "אפשרויות מפתחים" (משם ל"ניפוי באגים אלחוטי").
  Future<void> openDeveloperOptions() async {
    try {
      await native.openDeveloperOptions();
    } catch (e) {
      debugPrint('open developer options failed: $e');
    }
  }

  /// מציג את התראת הזנת קוד ההתאמה (כמו סוויץ'): מקלידים את הקוד בשורת ההתראה בלי לצאת ממסך
  /// "ניפוי אלחוטי", וההקמה ממשיכה לבד ברקע. מחזיר false אם לא נתמך/נכשל.
  Future<bool> startWirelessNotification() async {
    try {
      return await native.startWirelessNotification(_caPem);
    } catch (e) {
      debugPrint('wireless notification failed: $e');
      return false;
    }
  }

  String _wirelessError(String reason) => switch (reason) {
        'bad_pairing_code' => 'קוד ההתאמה צריך להיות 6 ספרות. נסו שוב.',
        'pairing_port_not_found' =>
          'לא זוהה הפורט אוטומטית. ודאו ש"ניפוי באגים אלחוטי" דלוק ושחלון קוד ההתאמה פתוח, ונסו שוב.',
        'pairing_failed' => 'ההתאמה נכשלה — ודאו שהקוד נכון ושהחלון עדיין פתוח, ונסו שוב.',
        'connect_failed' => 'ההתאמה הצליחה אבל החיבור נכשל. ודאו שהטלפון מחובר ל-Wi-Fi, ונסו שוב.',
        'accounts_present' =>
          'יש חשבונות במכשיר שחוסמים את ההקמה. ניסינו לעקוף אוטומטית — אם לא הצליח, הסירו חשבון Google זמנית ונסו שוב.',
        'already_has_device_owner' =>
          'כבר מוגדר מנהל מכשיר אחר. צריך קודם להסיר אותו (או איפוס להגדרות יצרן).',
        'multiple_users' => 'יש כמה משתמשים במכשיר. מחקו משתמשים נוספים ונסו שוב.',
        'wireless_unsupported' => 'ניפוי אלחוטי דורש אנדרואיד 11 ומעלה.',
        _ => 'ההקמה לא הושלמה. אפשר לנסות שוב, או ליצור קשר עם התמיכה.',
      };

  /// האפליקציה חזרה לחזית (המשתמש סיים עם חלון של אנדרואיד) — ממשיכים בהקמה, ומרעננים.
  Future<void> onAppResumed() async {
    resumeTick++;
    unawaited(checkLaunchTarget());
    if (setupStep != null && setupStep != SetupStep.done) {
      await _continueSetup();
    } else if (phase == Phase.home) {
      await refresh();
    }
  }

  // ------------------------------------------------------------------ טאב העדכונים

  /// בדיקה מול Google Play (דרך השרת) — רק כשפותחים את הטאב, לא ברקע.
  Future<Map<String, AppUpdateInfo>> appUpdates(List<InstalledApp> apps) async {
    final token = _token;
    if (token == null || apps.isEmpty) return const {};
    return api.appUpdates(token, [for (final a in apps) a.toServer()]);
  }

  // ------------------------------------------------------------------ תמיכה

  int get supportUnread => device?.supportUnread ?? 0;

  /// מספר לפנייה ישירה בוואטסאפ: מה שהמנהל הגדיר בשרת, ואם אין — מה שנקבע בבניית האפליקציה.
  String get supportWhatsApp {
    final fromServer = device?.supportWhatsApp ?? '';
    return fromServer.isNotEmpty ? fromServer : supportWhatsAppNumber;
  }

  /// שיחה עם נציג דרך השרת — אחרי הרשמה / הפעלה (יש אסימון).
  bool get canChatWithSupport => _token != null;

  /// מספר הטלפון של הלקוח (מהשרת) — לצירוף לפנייה בוואטסאפ, כדי שהמנהל יזהה מיד במי מדובר.
  String get customerPhone => device?.customer?.phone ?? '';

  /// מכשיר המנהל — מותר לו לנהל תמיכה מתוך האפליקציה (רואה את כל השיחות ועונה).
  bool get isAdminDevice => device?.isAdmin ?? false;
  int get adminSupportUnread => device?.adminUnread ?? 0;

  /// רשימת השיחות של כל הלקוחות (מכשיר המנהל). זורק ApiException אם אין הרשאה.
  Future<(List<SupportThreadInfo>, int)> adminThreads() async {
    final token = _token;
    if (token == null) return (<SupportThreadInfo>[], 0);
    return api.adminSupportThreads(token);
  }

  Future<List<SupportMessage>> adminThread(int deviceId) async {
    final token = _token;
    if (token == null) return const [];
    return api.adminSupportThread(token, deviceId);
  }

  /// תשובת המנהל ללקוח. null = נשלח; אחרת הודעת שגיאה.
  Future<String?> adminReply(int deviceId, String text) async {
    final token = _token;
    final body = text.trim();
    if (token == null || body.isEmpty) return 'אין מה לשלוח';
    try {
      await api.adminSupportReply(token, deviceId, body);
      return null;
    } on ApiException catch (e) {
      return e.status == 0 ? 'אין חיבור לשרת — ההודעה לא נשלחה.' : e.message;
    }
  }

  /// המצב שהעוזר האוטומטי מסתכל עליו.
  BotContext get botContext => BotContext(
        active: device?.isActive ?? false,
        vpnUp: vpn.isUp,
        guardNeeded: guardNeeded,
        guardOn: checks[Check.guard]!,
        deviceOwner: deviceOwner,
        batteryOk: protection.backgroundOk,
        caInstalled: caInstalled,
        paused: adminPauseActive,
        levelName: device?.levelName ?? '',
        updateAvailable: protection.updateAvailable != null,
      );

  /// מצב טכני שמצורף לפנייה לנציג (כדי שלא יצטרך לשאול) — בלי שום תוכן אישי.
  Map<String, Object?> supportDiag({String topic = ''}) => {
        'vpn': vpn.state,
        'vpn_error': ?vpn.error,
        'vpn_data_ok': ?vpn.dataOk,
        if (vpn.dataOk == false) 'vpn_data_error': ?vpn.dataError,
        'accessibility': guardEnabled,
        'device_owner': deviceOwner,
        'battery_unrestricted': ?protection.batteryUnrestricted,
        'ca_installed': caInstalled,
        'paused': adminPauseActive,
        'policy_version': protection.policyVersion,
        'level': ?device?.level,
        if (_appVersion.isNotEmpty) 'app_version': _appVersion,
        if (topic.isNotEmpty) 'topic': topic,
      };

  /// טוען את השיחה (רק הודעות חדשות). שקט בכשל — הצ'אט מציג את מה שכבר יש.
  Future<void> loadSupport() async {
    final token = _token;
    if (token == null) return;
    final have = supportThread ?? const <SupportMessage>[];
    try {
      final fresh = await api.supportMessages(token, after: have.isEmpty ? 0 : have.last.id);
      if (fresh.isEmpty && supportThread != null) return;
      supportThread = [...have, ...fresh];
      if (fresh.any((m) => m.fromSupport) && device != null) unawaited(refresh());   // מונה "לא נקראו"
      notifyListeners();
    } on ApiException catch (e) {
      debugPrint('support load failed: ${e.message}');
    }
  }

  /// שולח לנציג. מחזיר הודעת שגיאה למשתמש, או null אם נשלח.
  Future<String?> sendSupport(String text, {String topic = ''}) async {
    final token = _token;
    final body = text.trim();
    if (token == null || body.isEmpty || supportSending) return null;
    supportSending = true;
    notifyListeners();
    try {
      await loadSupport();
      final sent = await api.sendSupport(token, body, supportDiag(topic: topic));
      supportThread = [...?supportThread, sent];
      return null;
    } on ApiException catch (e) {
      return e.status == 0 ? 'אין חיבור לשרת — ההודעה לא נשלחה. נסו שוב כשיש אינטרנט.' : e.message;
    } finally {
      supportSending = false;
      notifyListeners();
    }
  }

  /// הצעת ייעול מהלקוח — מגיעה ישר לוואטסאפ של המנהל (דרך התמיכה, בלי מענה אוטומטי). null = נשלח.
  Future<String?> sendSuggestion(String text) {
    final body = text.trim();
    if (body.isEmpty) return Future.value('כתבו את ההצעה קודם');
    return sendSupport('💡 הצעת ייעול: $body', topic: 'suggestion');
  }

  /// שליחת דוח אבחון לתמיכה — המצב הטכני המלא נשלח כהודעה למנהל (ה-diag מצורף ממילא). null = נשלח.
  Future<String?> sendDiagnosticReport() =>
      sendSupport('📋 דוח אבחון מהמכשיר (נשלח מההגדרות).', topic: 'diag');

  /// טקסט שיתוף להזמנת חבר. הקישור נקבע בבנייה (FP_SHARE_URL) או ברירת מחדל לאתר.
  String get shareText => 'שמעת על FilterPhone? טלפון כשר אמיתי עם סינון תמונות חכם. '
      'להצטרפות: $shareUrl';

  // ------------------------------------------------------------------ הגדרות תצוגה

  Future<void> _loadUiPrefs() async {
    try {
      final t = await store.read(_kTheme);
      if (t != null && const ['system', 'light', 'dark'].contains(t)) themeMode = t;
      final scale = double.tryParse(await store.read(_kTextScale) ?? '');
      if (scale != null && textScales.contains(scale)) textScale = scale;
      final ex = await store.read(_kUpdateExcluded);
      if (ex != null) updateExcluded = {...(jsonDecode(ex) as List).whereType<String>()};
    } on Exception catch (e) {
      debugPrint('ui prefs: $e');
    }
  }

  Future<void> setThemeMode(String mode) async {
    if (!const ['system', 'light', 'dark'].contains(mode) || mode == themeMode) return;
    themeMode = mode;
    notifyListeners();
    await store.write(_kTheme, mode);
  }

  Future<void> setTextScale(double scale) async {
    if (!textScales.contains(scale) || scale == textScale) return;
    textScale = scale;
    notifyListeners();
    await store.write(_kTextScale, '$scale');
  }

  Future<void> setUpdateExcluded(String package, bool excluded) async {
    if (updateExcluded.contains(package) == excluded) return;
    updateExcluded = excluded ? {...updateExcluded, package} : ({...updateExcluded}..remove(package));
    notifyListeners();
    await store.write(_kUpdateExcluded, jsonEncode(updateExcluded.toList()..sort()));
  }

  Future<void> clearSupportNotification() async {
    try {
      await native.clearSupportNotification();
    } on PlatformException catch (_) {
      // לא קריטי
    }
  }

  /// נפתחנו מהתראה של התמיכה → AppShell עובר ללשונית (פעם אחת).
  Future<void> checkLaunchTarget() async {
    try {
      final t = await native.launchTarget();
      if (t == null) return;
      _requestedTab = t;
      notifyListeners();
    } on PlatformException catch (_) {
      // לא קריטי
    }
  }

  String? takeRequestedTab() {
    final t = _requestedTab;
    _requestedTab = null;
    return t;
  }

  void dismissSetup() {
    setupStep = null;
    notifyListeners();
  }

  Future<void> _continueSetup() async {
    await refresh();
    final next = <SetupStep, bool>{
      SetupStep.vpn: !vpn.isUp,
      SetupStep.certificate: !caInstalled && _caPem != null,
      SetupStep.battery: !protection.backgroundOk,
      SetupStep.guard: guardNeeded && !guardEnabled,
    }.entries.where((e) => e.value).map((e) => e.key).toList();
    if (next.isEmpty) {
      setupStep = SetupStep.done;
      notice = 'ההקמה הושלמה — הטלפון מוגן ✓';
      notifyListeners();
      return;
    }
    final step = next.first;
    if (_setupTried.contains(step)) {
      // המשתמש חזר בלי לאשר — לא פותחים שוב בלולאה; אפשר ללחוץ שוב על הכפתור
      setupStep = null;
      notice = 'ההקמה נעצרה ב"${setupStepTitle(step)}". אפשר ללחוץ שוב על "הקמה בלחיצה אחת" או לאשר מהרשימה למטה.';
      notifyListeners();
      return;
    }
    _setupTried.add(step);
    setupStep = step;
    notifyListeners();
    switch (step) {
      case SetupStep.vpn:
        try {
          await _connect();
          await _continueSetup();                 // אושר מיד (או כבר היה) — ממשיכים
        } on _UserMessage catch (e) {
          notice = e.message;
          notifyListeners();
        } on PlatformException catch (e) {
          notice = 'החיבור לא הושלם: ${e.message ?? e.code}';
          notifyListeners();
        }
      case SetupStep.certificate:
        await installCertificate();
        if (caInstalled) await _continueSetup();
      case SetupStep.battery:
        await requestBatteryExemption();
      case SetupStep.guard:
        await openGuardSettings();
      case SetupStep.automatic:
      case SetupStep.done:
        break;
    }
  }

  static String setupStepTitle(SetupStep s) => switch (s) {
        SetupStep.automatic => 'הגדרות אוטומטיות',
        SetupStep.vpn => 'אישור חיבור הסינון (VPN)',
        SetupStep.certificate => 'התקנת תעודת הסינון',
        SetupStep.battery => 'פעולה ברקע (סוללה)',
        SetupStep.guard => 'הפעלת מגן FilterPhone (נגישות)',
        SetupStep.done => 'הושלם',
      };

  /// ניסיון ההקמה התקני דרך su במכשיר שכבר יש בו Root; לא עוקף מגבלות Android.
  Future<Map<String, Object?>> provisionRoot() async {
    rootProvisioning = true;
    notifyListeners();
    try {
      final result = await native.provisionRoot();
      if (result['ok'] == true) {
        error = null;
        notice = 'ניהול המכשיר הוגדר בהצלחה.';
        await refresh(forceSync: true);
      }
      return result;
    } catch (e) {
      error = 'הקמת מנהל המכשיר נכשלה: $e';
      return {'ok': false, 'code': 'ENROLL-ROOT-003', 'result': 'error', 'technical': e.toString()};
    } finally {
      rootProvisioning = false;
      notifyListeners();
    }
  }

  /// רשימת האפליקציות והמסכים החסומים — לשירות שבטלפון. רק כשהשתנתה.
  Future<void> _applyGuardPolicy(Map<String, Object?> policy) async {
    final h = _hash(policy);
    if (h == await store.read(_kGuardHash)) return;
    try {
      await native.setGuardPolicy(policy, api: api.baseUrl, token: _token);
      await store.write(_kGuardHash, h);
    } on PlatformException catch (e) {
      debugPrint('guard policy failed: $e');
    }
  }

  /// דיווח לשרת: אילו אפליקציות מותקנות (כדי שהמנהל יוכל לחסום כל אחת) ומצב ההגנה.
  /// פעם ב-12 שעות, או מיד כשהשרת מבקש / כשמצב ההגנה השתנה.
  Future<void> _maybeReport(String token) async {
    final guard = {
      'accessibility': guardEnabled,
      'device_owner': deviceOwner,
      'ca_installed': caInstalled,
      'private_dns': privateDns,
      if (vpn.alwaysOn != null) 'always_on': vpn.alwaysOn!,
      if (vpn.lockdown != null) 'lockdown': vpn.lockdown!,
    };
    final last = await store.read(_kReport);
    final lastMap = last == null ? null : Map<String, Object?>.from(jsonDecode(last) as Map);
    final lastAt = (lastMap?['at'] as num?)?.toInt() ?? 0;
    final due = DateTime.now().millisecondsSinceEpoch - lastAt > reportEvery.inMilliseconds;
    final changed = jsonEncode(lastMap?['guard']) != jsonEncode(guard);
    if (!due && !changed && !(device?.appsReportNeeded ?? false)) return;
    try {
      final sendApps = due || (device?.appsReportNeeded ?? false);
      final apps = sendApps ? await native.installedApps() : null;
      await api.report(token, apps: apps, guard: guard);
      await store.write(_kReport, jsonEncode({
        'at': sendApps ? DateTime.now().millisecondsSinceEpoch : lastAt,
        'guard': guard,
      }));
    } on Exception catch (e) {
      debugPrint('report skipped: $e');
    }
  }

  /// אם השרת שינה את הגדרות ה-VPN (למשל החלפת מפתח שרת) — שומרים ומתחברים מחדש.
  /// ואם ההגדרות בטלפון נמחקו (ה-Keystore התאפס) — שומרים מחדש; אם גם המפתח הפרטי אבד —
  /// יוצרים מפתח חדש ומעדכנים את השרת (אותה כתובת, אותה מדיניות).
  Future<void> _syncVpnConfig() async {
    final token = _token;
    if (token == null) return;
    try {
      if (!vpn.hasKey) {
        final publicKey = await native.generateKeyPair();
        final b = await api.rekey(token, publicKey);
        await native.saveVpnConfig(b.vpn);
        await store.write(_kVpnHash, _hash(b.vpn));
        notice = 'מפתח הסינון בטלפון חודש. אם צריך — לחצו "חיבור".';
        await _connect();
        return;
      }
      final b = await api.vpnConfig(token);
      final h = _hash(b.vpn);
      if (h != await store.read(_kVpnHash) || !vpn.hasConfig) {
        await native.saveVpnConfig(b.vpn);
        await store.write(_kVpnHash, h);
        if (vpn.isUp || !vpn.hasConfig) await native.connect();
      }
      if (b.caPem != null && b.caPem != _caPem) {
        _caPem = b.caPem;
        await store.write(_kCa, b.caPem);
      }
    } on Exception catch (e) {
      debugPrint('vpn config sync skipped: $e');
    }
  }

  void _listenVpn() {
    _vpnSub ??= native.vpnStateChanges().listen((_) => refresh());
  }

  void _startTimer(Future<void> Function() tick) {
    _timer?.cancel();
    _timer = Timer.periodic(refreshInterval, (_) => tick());
  }

  Future<bool> _guard(Future<void> Function() action) async {
    if (busy) return false;
    busy = true;
    error = null;
    notifyListeners();
    try {
      await action();
      return true;
    } on ApiException catch (e) {
      error = e.message;
    } on _UserMessage catch (e) {
      error = e.message;
    } on PlatformException catch (e) {
      error = 'שגיאה בטלפון: ${e.message ?? e.code}';
    } finally {
      busy = false;
      notifyListeners();
    }
    return false;
  }

  /// טביעה של ההגדרות להשוואה (לא סוד — אותו מידע כבר שמור מוצפן בצד הנייטיבי).
  static String _hash(Map<String, Object?> m) => base64Encode(utf8.encode(jsonEncode(m)));

  bool _disposed = false;

  /// פעולה אסינכרונית (רענון, תשובה מהשרת) יכולה להסתיים אחרי שהמסך נסגר — אז לא מודיעים.
  @override
  void notifyListeners() {
    if (!_disposed) super.notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _timer?.cancel();
    _vpnSub?.cancel();
    super.dispose();
  }
}

class _UserMessage implements Exception {
  const _UserMessage(this.message);
  final String message;
}
