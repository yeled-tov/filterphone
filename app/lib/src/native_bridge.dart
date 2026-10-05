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
    );
  }

  static const down = VpnStatus(state: 'DOWN');
}

/// ממשק לקוד הנייטיבי. מופרד לממשק כדי שאפשר יהיה להחליף בבדיקות ובאייפון.
abstract class NativeBridge {
  Future<Map<String, Object?>> deviceInfo();
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
}
