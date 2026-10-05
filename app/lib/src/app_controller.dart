import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'api_client.dart';
import 'config.dart';
import 'native_bridge.dart';

enum Phase { loading, activation, home, unsupported }

/// סעיף ברשימת "מה צריך כדי שהטלפון יהיה מוגן".
enum Check { vpn, certificate, alwaysOn, privateDns }

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
  bool serverReachable = true;
  bool deviceRemoved = false;

  Timer? _timer;
  StreamSubscription<String>? _vpnSub;

  // ------------------------------------------------------------------ מצב מחושב

  Map<Check, bool> get checks => {
        Check.vpn: vpn.isUp,
        Check.certificate: caInstalled,
        Check.alwaysOn: vpn.alwaysOn == true,
        Check.privateDns: privateDns != 'hostname',
      };

  /// מוגן = המנהרה פעילה, התעודה מותקנת, ו-DNS פרטי לא עוקף את הסינון.
  /// "תמיד מופעל" מומלץ מאוד אבל לא תנאי (אנדרואיד לא תמיד מדווח עליו).
  bool get isProtected =>
      checks[Check.vpn]! && checks[Check.certificate]! && checks[Check.privateDns]!;

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
      phase = Phase.activation;
      notifyListeners();
      return;
    }
    phase = Phase.home;
    notifyListeners();
    _listenVpn();
    await refresh();
    unawaited(_syncVpnConfig());
    _startTimer();
  }

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
      _startTimer();
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

  void clearNotice() {
    notice = null;
    error = null;
    notifyListeners();
  }

  /// מחיקת ההגדרות מהטלפון (למשל אחרי שהמנהל הסיר את המכשיר).
  Future<void> resetDevice() async {
    _timer?.cancel();
    await _vpnSub?.cancel();
    await native.reset();
    await store.write(_kToken, null);
    await store.write(_kCa, null);
    await store.write(_kVpnHash, null);
    _token = null;
    device = null;
    deviceRemoved = false;
    vpn = VpnStatus.down;
    phase = Phase.activation;
    notifyListeners();
  }

  // ------------------------------------------------------------------ רענון

  Future<void> refresh() async {
    try {
      vpn = await native.vpnStatus();
      privateDns = await native.privateDnsMode();
      deviceOwner = await native.isDeviceOwner();
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
      } on ApiException catch (e) {
        serverReachable = e.status != 0;
        deviceRemoved = e.isUnauthorized;
      }
    }
    notifyListeners();
  }

  /// אם השרת שינה את הגדרות ה-VPN (למשל החלפת מפתח שרת) — שומרים ומתחברים מחדש.
  Future<void> _syncVpnConfig() async {
    final token = _token;
    if (token == null) return;
    try {
      final cfg = await api.vpnConfig(token);
      final h = _hash(cfg);
      if (h != await store.read(_kVpnHash)) {
        await native.saveVpnConfig(cfg);
        await store.write(_kVpnHash, h);
        if (vpn.isUp) await native.connect();
      }
    } on Exception catch (e) {
      debugPrint('vpn config sync skipped: $e');
    }
  }

  void _listenVpn() {
    _vpnSub ??= native.vpnStateChanges().listen((_) => refresh());
  }

  void _startTimer() {
    _timer?.cancel();
    _timer = Timer.periodic(refreshInterval, (_) => refresh());
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

  @override
  void dispose() {
    _timer?.cancel();
    _vpnSub?.cancel();
    super.dispose();
  }
}

class _UserMessage implements Exception {
  const _UserMessage(this.message);
  final String message;
}
