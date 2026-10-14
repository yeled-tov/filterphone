import 'dart:async';
import 'dart:convert';

import 'package:flutter/services.dart';

/// מצב המנהרה כפי שהקוד הנייטיבי מדווח.
class VpnStatus {
  const VpnStatus({
    required this.state,
    this.hasConfig = false,
    this.permission = false,
    this.alwaysOn,
    this.lockdown,
    this.lastHandshake,
    this.rx = 0,
    this.tx = 0,
    this.error,
    this.hasKey = true,
    this.lastHealthOk,
    this.reconnects = 0,
  });

  final String state; // UP / DOWN / TOGGLE
  final bool hasConfig;
  final bool permission;
  final bool? alwaysOn; // null = לא ידוע (המנהרה לא פעילה)
  final bool? lockdown;
  final DateTime? lastHandshake;
  final int rx;
  final int tx;
  final String? error;
  final bool hasKey; // המפתח הפרטי קיים (false = ה-Keystore נמחק — צריך מפתח חדש)
  final DateTime? lastHealthOk; // בדיקת התקינות האחרונה שעברה (handshake טרי)
  final int reconnects;

  bool get isUp => state == 'UP';

  factory VpnStatus.fromMap(Map<Object?, Object?> m) {
    final hs = (m['lastHandshakeMs'] as num?)?.toInt() ?? 0;
    return VpnStatus(
      state: m['state'] as String? ?? 'DOWN',
      hasConfig: m['hasConfig'] as bool? ?? false,
      permission: m['permission'] as bool? ?? false,
      alwaysOn: m['alwaysOn'] as bool?,
      lockdown: m['lockdown'] as bool?,
      lastHandshake: hs > 0 ? DateTime.fromMillisecondsSinceEpoch(hs) : null,
      rx: (m['rx'] as num?)?.toInt() ?? 0,
      tx: (m['tx'] as num?)?.toInt() ?? 0,
      error: m['error'] as String?,
      hasKey: m['hasKey'] as bool? ?? true,
      lastHealthOk: _time(m['lastHealthOkMs']),
      reconnects: (m['reconnects'] as num?)?.toInt() ?? 0,
    );
  }

  static const down = VpnStatus(state: 'DOWN');
}

DateTime? _time(Object? ms) {
  final v = (ms as num?)?.toInt() ?? 0;
  return v > 0 ? DateTime.fromMillisecondsSinceEpoch(v) : null;
}

/// מצב ההגנה בטלפון — מה שהמסך הראשי מציג, ומה שנשלח לשרת בדיווח התקופתי.
class ProtectionStatus {
  const ProtectionStatus({
    this.batteryUnrestricted,
    this.backgroundRestricted,
    this.notifications,
    this.guardEnabled = false,
    this.guardRunning = false,
    this.deviceOwner = false,
    this.deviceAdmin = false,
    this.policyVersion = 0,
    this.lastPolicySync,
    this.lastVpnHealthOk,
    this.lastHeartbeat,
    this.heartbeatError,
    this.policyError,
    this.updateAvailable,
  });

  final bool? batteryUnrestricted; // null = לא ידוע
  final bool? backgroundRestricted;
  final bool? notifications;
  final bool guardEnabled;
  final bool guardRunning;
  final bool deviceOwner;
  final bool deviceAdmin;
  final int policyVersion;
  final DateTime? lastPolicySync;
  final DateTime? lastVpnHealthOk;
  final DateTime? lastHeartbeat;
  final String? heartbeatError;
  final String? policyError;

  /// גרסה חדשה שהמשתמש צריך להתקין בעצמו (בלי מנהל מכשיר). במנהל מכשיר העדכון שקט — תמיד null.
  final String? updateAvailable;

  /// הטלפון לא מגביל את FilterPhone ברקע (סוללה + הגבלת רקע).
  bool get backgroundOk => batteryUnrestricted == true && backgroundRestricted != true;

  factory ProtectionStatus.fromMap(Map<Object?, Object?> m) => ProtectionStatus(
        batteryUnrestricted: m['batteryUnrestricted'] as bool?,
        backgroundRestricted: m['backgroundRestricted'] as bool?,
        notifications: m['notifications'] as bool?,
        guardEnabled: m['guardEnabled'] as bool? ?? false,
        guardRunning: m['guardRunning'] as bool? ?? false,
        deviceOwner: m['deviceOwner'] as bool? ?? false,
        deviceAdmin: m['deviceAdmin'] as bool? ?? false,
        policyVersion: (m['policyVersion'] as num?)?.toInt() ?? 0,
        lastPolicySync: _time(m['lastPolicySyncMs']),
        lastVpnHealthOk: _time(m['lastVpnHealthOkMs']),
        lastHeartbeat: _time(m['lastHeartbeatMs']),
        heartbeatError: m['heartbeatError'] as String?,
        policyError: m['policyError'] as String?,
        updateAvailable: m['updateAvailable'] as String?,
      );

  static const unknown = ProtectionStatus();
}

/// ממשק לקוד הנייטיבי. מופרד לממשק כדי שאפשר יהיה להחליף בבדיקות ובאייפון.
abstract class NativeBridge {
  Future<Map<String, Object?>> deviceInfo();

  /// מזהה יציב של הטלפון (hash) — לשחזור לפי מספר טלפון. אותו טלפון אחרי התקנה מחדש = אותו מזהה.
  Future<String> deviceUid();
  Future<String> generateKeyPair();
  Future<String?> publicKey();
  Future<void> saveVpnConfig(Map<String, Object?> vpn);
  Future<bool> prepareVpn();
  Future<String> connect();
  Future<VpnStatus> vpnStatus();
  Stream<String> vpnStateChanges();
  Future<bool> isCaInstalled(String pem);
  Future<String> installCa(String pem);
  Future<void> openVpnSettings();
  Future<String> privateDnsMode();
  Future<bool> isDeviceOwner();
  Future<void> reset();
  /// השהיה שהמנהל אישר: {active, until (מילישניות)}.
  Future<Map<String, Object?>> adminPauseStatus();

  /// "החזרת ההגנה" בזמן השהיה — הכול חוזר מיד (גם בשרת).
  Future<bool> resumeProtectionLocally();

  /// "הסרת הכול" — רק בזמן השהיה מאושרת: ניהול, VPN, תעודה (במנהל מכשיר), נתונים. מחזיר דוח.
  Future<Map<String, Object?>> uninstallEverything();

  /// חלון הסרת האפליקציה של אנדרואיד (אחרי uninstallEverything).
  Future<bool> requestSelfUninstall();

  Future<Map<String, Object?>> provisionRoot();

  /// הקמה בלחיצה אחת: כל מה שאפשר בלי מגע (root / מנהל מכשיר). מחזיר מה הושלם.
  Future<Map<String, Object?>> setupAll(String? caPem);

  // --- מגן FilterPhone: חסימת אפליקציות ומסכים בטלפון עצמו ---

  /// האפליקציות שאפשר לפתוח מהמסך הראשי: [{package, label, system}].
  Future<List<Map<String, Object?>>> installedApps();

  /// האם שירות הנגישות של FilterPhone מופעל (בלעדיו — אין חסימת אפליקציות, אלא אם מנהל מכשיר).
  Future<bool> isGuardEnabled();

  /// פותח את הגדרות הנגישות, כדי להפעיל את המגן.
  Future<void> openGuardSettings();

  /// שומר את רשימת החסימות מהשרת; במנהל מכשיר — גם מסתיר את האפליקציות.
  /// [api] ו-[token] מאפשרים למגן לרענן את הרשימה לבד, גם כשהאפליקציה סגורה (null = מחיקה).
  Future<void> setGuardPolicy(Map<String, Object?> policy, {String? api, String? token});

  // --- אמינות: דיווח לשרת, סוללה, מנהל מכשיר ---

  /// כתובת השרת והאסימון — לדיווח התקופתי מהרקע (גם כשהאפליקציה סגורה).
  Future<void> setCredentials(String? api, String? token);

  /// המצב המלא: סוללה, מגן, מנהל מכשיר, סנכרון אחרון, בדיקת VPN אחרונה.
  Future<ProtectionStatus> protectionStatus();

  /// דיווח לשרת עכשיו (מחזיר true אם השרת ענה). מחיל מדיניות חדשה אם יש.
  Future<bool> syncNow();

  /// השירות הקבוע ("הסינון פעיל") ששומר על ה-VPN ברקע.
  Future<bool> startProtection();

  /// חלון המערכת לפטור מאופטימיזציית סוללה. מחזיר מה נפתח (dialog / list / app_details / already).
  Future<String> requestBatteryExemption();

  /// הרשאת התראות (אנדרואיד 13+) — להתראה "הסינון פעיל".
  Future<void> requestNotifications();

  /// מנהל מכשיר רגיל (לא Device Owner): מקשה על הסרה בלבד.
  Future<bool> requestDeviceAdmin();

  /// דף ההורדה בדפדפן — כשיש גרסה חדשה ואין מנהל מכשיר (המשתמש מתקין בעצמו).
  Future<bool> openUpdatePage();

  /// קוד הפעלה שהגיע ב-QR של הקמת Device Owner: {code, api}, או null.
  Future<Map<String, String?>?> provisioningInfo();
  Future<void> clearProvisioning();

  /// פתיחת ערוץ התמיכה ב-WhatsApp עם מזהה המכשיר ותקציר מצב ההגנה.
  Future<void> openSupportWhatsApp(String number, String message);

  /// נפתחנו מהתראה ("יש תשובה מהתמיכה")? מחזיר 'support' פעם אחת, אחרת null.
  Future<String?> launchTarget();

  Future<void> clearSupportNotification();

  // --- החנות ---

  /// התקנה / עדכון דרך מנהל המכשיר: started / not_owner / busy / invalid / up_to_date.
  Future<String> installStoreApp(Map<String, Object?> item);

  /// לכל חבילה: {installed_version, state (installing / installed / failed:...)}.
  Future<Map<String, Map<String, Object?>>> storeAppStates(List<String> packages);

  /// פתיחת אפליקציה מותקנת.
  Future<bool> openApp(String package);
}

/// מימוש דרך MethodChannel (אנדרואיד היום; אייפון — כשנוסיף את ה-Network Extension).
class ChannelNativeBridge implements NativeBridge {
  static const _channel = MethodChannel('filterphone/native');
  static const _events = EventChannel('filterphone/vpn_state');

  Future<T> _call<T>(String method, [Map<String, Object?>? args]) async {
    final result = await _channel.invokeMethod<T>(method, args);
    return result as T;
  }

  @override
  Future<Map<String, Object?>> deviceInfo() async =>
      Map<String, Object?>.from(await _call<Map<Object?, Object?>>('deviceInfo'));

  @override
  Future<String> deviceUid() => _call<String>('deviceUid');

  @override
  Future<String> generateKeyPair() => _call<String>('generateKeyPair');

  @override
  Future<String?> publicKey() => _channel.invokeMethod<String>('publicKey');

  @override
  Future<void> saveVpnConfig(Map<String, Object?> vpn) =>
      _call<bool>('saveVpnConfig', {'vpn': jsonEncode(vpn)});

  @override
  Future<bool> prepareVpn() => _call<bool>('prepareVpn');

  @override
  Future<String> connect() => _call<String>('connect');

  @override
  Future<VpnStatus> vpnStatus() async =>
      VpnStatus.fromMap(await _call<Map<Object?, Object?>>('vpnStatus'));

  @override
  Stream<String> vpnStateChanges() =>
      _events.receiveBroadcastStream().map((e) => e as String);

  @override
  Future<bool> isCaInstalled(String pem) => _call<bool>('isCaInstalled', {'pem': pem});

  @override
  Future<String> installCa(String pem) => _call<String>('installCa', {'pem': pem});

  @override
  Future<void> openVpnSettings() => _call<bool>('openVpnSettings');

  @override
  Future<String> privateDnsMode() => _call<String>('privateDnsMode');

  @override
  Future<bool> isDeviceOwner() => _call<bool>('isDeviceOwner');

  @override
  Future<void> reset() => _call<bool>('reset');

  @override
  Future<Map<String, Object?>> adminPauseStatus() async =>
      Map<String, Object?>.from(await _call<Map<Object?, Object?>>('adminPauseStatus'));

  @override
  Future<bool> resumeProtectionLocally() => _call<bool>('resumeProtectionLocally');

  @override
  Future<Map<String, Object?>> uninstallEverything() async =>
      Map<String, Object?>.from(await _call<Map<Object?, Object?>>('uninstallEverything'));

  @override
  Future<bool> requestSelfUninstall() => _call<bool>('requestSelfUninstall');

  @override
  Future<Map<String, Object?>> setupAll(String? caPem) async =>
      Map<String, Object?>.from(await _call<Map<Object?, Object?>>('setupAll', {'ca': caPem}));

  @override
  Future<Map<String, Object?>> provisionRoot() async =>
      Map<String, Object?>.from(await _call<Map<Object?, Object?>>('provisionRoot'));

  @override
  Future<List<Map<String, Object?>>> installedApps() async {
    final list = await _call<List<Object?>>('installedApps');
    return list.map((e) => Map<String, Object?>.from(e as Map)).toList();
  }

  @override
  Future<bool> isGuardEnabled() => _call<bool>('isGuardEnabled');

  @override
  Future<void> openGuardSettings() => _call<bool>('openGuardSettings');

  @override
  Future<void> setGuardPolicy(Map<String, Object?> policy, {String? api, String? token}) =>
      _call<bool>('setGuardPolicy', {'policy': jsonEncode(policy), 'api': api, 'token': token});

  @override
  Future<void> setCredentials(String? api, String? token) =>
      _call<bool>('setCredentials', {'api': api, 'token': token});

  @override
  Future<ProtectionStatus> protectionStatus() async =>
      ProtectionStatus.fromMap(await _call<Map<Object?, Object?>>('protectionStatus'));

  @override
  Future<bool> syncNow() => _call<bool>('syncNow');

  @override
  Future<bool> startProtection() => _call<bool>('startProtection');

  @override
  Future<String> requestBatteryExemption() => _call<String>('requestBatteryExemption');

  @override
  Future<void> requestNotifications() => _call<bool>('requestNotifications');

  @override
  Future<bool> requestDeviceAdmin() => _call<bool>('requestDeviceAdmin');

  @override
  Future<bool> openUpdatePage() => _call<bool>('openUpdatePage');

  @override
  Future<Map<String, String?>?> provisioningInfo() async {
    final m = await _channel.invokeMethod<Map<Object?, Object?>>('provisioningInfo');
    return m?.map((k, v) => MapEntry(k as String, v as String?));
  }

  @override
  Future<void> clearProvisioning() => _call<bool>('clearProvisioning');

  @override
  Future<void> openSupportWhatsApp(String number, String message) =>
      _call<bool>('openSupportWhatsApp', {'number': number, 'message': message});

  @override
  Future<String?> launchTarget() => _channel.invokeMethod<String>('launchTarget');

  @override
  Future<void> clearSupportNotification() => _call<bool>('clearSupportNotification');

  @override
  Future<String> installStoreApp(Map<String, Object?> item) =>
      _call<String>('installStoreApp', {'item': jsonEncode(item)});

  @override
  Future<Map<String, Map<String, Object?>>> storeAppStates(List<String> packages) async {
    final m = await _call<Map<Object?, Object?>>('storeAppStates', {'packages': packages});
    return m.map((k, v) => MapEntry(k as String, Map<String, Object?>.from(v as Map)));
  }

  @override
  Future<bool> openApp(String package) => _call<bool>('openApp', {'package': package});
}
