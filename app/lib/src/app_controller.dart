import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'api_client.dart';
import 'config.dart';
import 'native_bridge.dart';

/// השלבים של האפליקציה:
///   welcome → register (טופס) → pending (מחכה לאישור המנהל) → home
///   welcome → activation (קוד) → home
enum Phase { loading, welcome, register, activation, pending, home, unsupported }

/// סעיף ברשימת "מה צריך כדי שהטלפון יהיה מוגן".
enum Check { vpn, certificate, alwaysOn, privateDns, guard, battery }

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
  bool rootProvisioning = false;

  /// סוללה, מנהל מכשיר, סנכרון אחרון, בדיקת VPN אחרונה — מהקוד הנייטיבי.
  ProtectionStatus protection = ProtectionStatus.unknown;
  DateTime? _lastSync;

  /// הקטלוג (רמות ואפשרויות) — לטופס ההרשמה ולבקשת שינוי.
  Catalog? catalog;

  /// הטופס בזמן ההרשמה (נשמר בין השלבים).
  RegistrationForm form = RegistrationForm();

  /// מה נשלח בבקשת ההצטרפות — מוצג במסך ההמתנה.
  Map<String, Object?>? pendingSummary;

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
    try {
      await native.deviceInfo();
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
      final e = isTransfer
          ? await api.transfer(token: trimmed, publicKey: publicKey, name: deviceName,
              platform: 'android', appVersion: appVersion)
          : await api.enroll(code: trimmed, publicKey: publicKey, name: deviceName,
              platform: 'android', appVersion: appVersion);
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
    });
  }

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

  /// גרסה חדשה (בלי מנהל מכשיר): דף ההורדה בדפדפן. ההתקנה — של המשתמש, בחלון של אנדרואיד.
  Future<void> openUpdatePage() async {
    try {
      if (!await native.openUpdatePage()) error = 'לא נפתח דף ההורדה';
    } on PlatformException catch (e) {
      error = 'לא נפתח דף ההורדה: ${e.message ?? e.code}';
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

  /// מחזיר את ההגנה מקומית, גם אם השרת לא זמין. nonce ההשהיה נדחה מקומית כדי שלא
  /// יופעל שוב בסנכרון הבא; השהיה חדשה מהשרת תקבל nonce אחר ותוצג מחדש.
  Future<bool> resumeProtectionLocally() async {
    try {
      final ok = await native.resumeProtectionLocally();
      if (!ok) throw StateError('הטלפון לא הצליח לשחזר את ההגנה');
      adminPauseActive = false;
      error = null;
      notice = 'ההגנה שוחזרה מקומית בטלפון.';
      await refresh(forceSync: true);
      return !adminPauseActive;
    } catch (e) {
      error = 'לא ניתן לשחזר את ההגנה: $e';
      notifyListeners();
      return false;
    }
  }

  /// מסלול חירום מקומי: משחרר Device Owner, מסיר את מדיניות החסימה המקומית ומפסיק VPN.
  /// לאחר מכן האפליקציה חוזרת למסך הפעלה וניתן להסיר אותה דרך הגדרות Android.
  Future<bool> removeManagementLocally() async {
    try {
      final ok = await native.removeManagementLocally();
      if (!ok) throw StateError('Android לא אפשר להסיר את ניהול המכשיר');
      adminPauseActive = false;
      await resetDevice();
      return true;
    } catch (e) {
      error = 'הסרת הניהול לא הושלמה: $e';
      notifyListeners();
      return false;
    }
  }

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
