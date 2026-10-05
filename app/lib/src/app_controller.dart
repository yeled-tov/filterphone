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
enum Check { vpn, certificate, alwaysOn, privateDns, guard }

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

  Map<Check, bool> get checks => {
        Check.vpn: vpn.isUp,
        Check.certificate: caInstalled,
        Check.alwaysOn: vpn.alwaysOn == true,
        Check.privateDns: privateDns != 'hostname',
        Check.guard: guardEnabled || deviceOwner,
      };

  /// מוגן = המנהרה פעילה, התעודה מותקנת, DNS פרטי לא עוקף — ואם המנהל חסם אפליקציות,
  /// גם מגן FilterPhone פעיל. "תמיד מופעל" מומלץ מאוד אבל לא תנאי (אנדרואיד לא תמיד מדווח עליו).
  bool get isProtected =>
      checks[Check.vpn]! &&
      checks[Check.certificate]! &&
      checks[Check.privateDns]! &&
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
    await refresh();
    unawaited(_syncVpnConfig());
    _startTimer(refresh);
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
      final e = await api.enroll(
        code: code.trim(),
        publicKey: publicKey,
        name: name.trim().isEmpty ? '${info['manufacturer']} ${info['model']}' : name.trim(),
        platform: 'android',
        appVersion: '${info['appVersion'] ?? ''}',
      );
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
    form = RegistrationForm();
    vpn = VpnStatus.down;
    phase = Phase.welcome;
    notifyListeners();
  }

  // ------------------------------------------------------------------ רענון

  Future<void> refresh() async {
    try {
      vpn = await native.vpnStatus();
      privateDns = await native.privateDnsMode();
      deviceOwner = await native.isDeviceOwner();
      guardEnabled = await native.isGuardEnabled();
      if (_caPem != null) caInstalled = await native.isCaInstalled(_caPem!);
    } on PlatformException catch (e) {
      debugPrint('native refresh failed: $e');
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
  Future<void> _syncVpnConfig() async {
    final token = _token;
    if (token == null) return;
    try {
      final b = await api.vpnConfig(token);
      final h = _hash(b.vpn);
      if (h != await store.read(_kVpnHash)) {
        await native.saveVpnConfig(b.vpn);
        await store.write(_kVpnHash, h);
        if (vpn.isUp) await native.connect();
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
