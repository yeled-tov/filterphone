import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:filterphone/main.dart';
import 'package:filterphone/src/api_client.dart';
import 'package:filterphone/src/app_controller.dart';
import 'package:filterphone/src/native_bridge.dart';
import 'package:filterphone/src/screens/home_screen.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class FakeNative implements NativeBridge {
  bool permission = true;
  bool caInstalled = false;
  String state = 'DOWN';
  bool alwaysOn = false;
  String privateDns = 'off';
  Map<String, Object?>? savedVpn;
  final _states = StreamController<String>.broadcast();

  String uid = 'ab' * 16;
  @override
  Future<String> deviceUid() async => uid;
  @override
  Future<Map<String, Object?>> deviceInfo() async =>
      {'model': 'Pixel 9', 'manufacturer': 'Google', 'sdk': 36, 'appVersion': '1.0.0'};
  int keyPairs = 0;

  @override
  Future<String> generateKeyPair() async {
    keyPairs++;
    hasKey = true;
    return base64Encode(List.filled(32, 7 + keyPairs));
  }
  @override
  Future<String?> publicKey() async => null;
  @override
  Future<void> saveVpnConfig(Map<String, Object?> vpn) async => savedVpn = vpn;
  @override
  Future<bool> prepareVpn() async => permission;
  @override
  Future<String> connect() async {
    state = 'UP';
    _states.add(state);
    return state;
  }

  bool hasKey = true;

  @override
  Future<VpnStatus> vpnStatus() async => VpnStatus(
      state: state, permission: permission, hasConfig: savedVpn != null, alwaysOn: alwaysOn, hasKey: hasKey);
  @override
  Stream<String> vpnStateChanges() => _states.stream;
  @override
  Future<bool> isCaInstalled(String pem) async => caInstalled;
  @override
  Future<String> installCa(String pem) async => 'settings';
  @override
  Future<void> openVpnSettings() async {}
  @override
  Future<String> privateDnsMode() async => privateDns;
  @override
  Future<bool> isDeviceOwner() async => false;
  @override
  Future<void> reset() async {
    savedVpn = null;
    state = 'DOWN';
  }

  bool paused = false;
  int resumes = 0;
  @override
  Future<Map<String, Object?>> adminPauseStatus() async =>
      {'active': paused, 'until': paused ? DateTime.now().add(const Duration(hours: 1)).millisecondsSinceEpoch : 0};
  @override
  Future<bool> resumeProtectionLocally() async {
    resumes++;
    paused = false;
    return true;
  }
  int uninstalls = 0;
  @override
  Future<Map<String, Object?>> uninstallEverything() async {
    uninstalls++;
    return {'ok': true, 'ca_removed': false, 'server_notified': true};
  }
  @override
  Future<bool> requestSelfUninstall() async => true;
  Map<String, Object?> setupResult = {'root': false, 'device_owner': false, 'secure_settings': false};
  @override
  Future<Map<String, Object?>> setupAll(String? caPem) async => setupResult;
  @override
  Future<Map<String, Object?>> provisionRoot() async => {
        'ok': false, 'code': 'ENROLL-ROOT-001', 'result': 'no_root', 'technical': ''
      };

  bool guard = false;
  Map<String, Object?>? guardPolicy;
  String? guardToken;
  int appsRequests = 0;

  @override
  Future<List<Map<String, Object?>>> installedApps() async {
    appsRequests++;
    return [
      {'package': 'com.whatsapp', 'label': 'WhatsApp', 'system': false},
      {'package': 'org.mozilla.firefox', 'label': 'Firefox', 'system': false},
    ];
  }

  @override
  Future<bool> isGuardEnabled() async => guard;
  @override
  Future<void> openGuardSettings() async {}
  @override
  Future<void> setGuardPolicy(Map<String, Object?> policy, {String? api, String? token}) async {
    guardPolicy = policy;
    guardToken = token;
  }

  // --- אמינות ---
  bool batteryUnrestricted = true;
  int batteryRequests = 0;
  String? updateAvailable;
  int updatePageOpens = 0;
  int syncs = 0;
  int protectionStarts = 0;
  String? credentialsApi;
  String? credentialsToken;
  Map<String, String?>? provisioning;
  bool provisioningCleared = false;

  @override
  Future<void> setCredentials(String? api, String? token) async {
    credentialsApi = api;
    credentialsToken = token;
  }

  @override
  Future<ProtectionStatus> protectionStatus() async => ProtectionStatus(
        batteryUnrestricted: batteryUnrestricted,
        backgroundRestricted: false,
        guardEnabled: guard,
        guardRunning: guard,
        policyVersion: (guardPolicy?['version'] as int?) ?? 0,
        lastPolicySync: guardPolicy == null ? null : DateTime.now(),
        updateAvailable: updateAvailable,
      );

  @override
  Future<bool> openUpdatePage() async {
    updatePageOpens++;
    return true;
  }

  @override
  Future<bool> syncNow() async {
    syncs++;
    return true;
  }

  @override
  Future<bool> startProtection() async {
    protectionStarts++;
    return true;
  }

  @override
  Future<String> requestBatteryExemption() async {
    batteryRequests++;
    return 'dialog';
  }

  @override
  Future<void> requestNotifications() async {}

  @override
  Future<bool> requestDeviceAdmin() async => true;

  @override
  Future<Map<String, String?>?> provisioningInfo() async => provisioning;

  @override
  Future<void> clearProvisioning() async {
    provisioning = null;
    provisioningCleared = true;
  }

  @override
  Future<void> openSupportWhatsApp(String number, String message) async {}
}

class MemoryStore implements KeyValueStore {
  final Map<String, String> data = {};
  @override
  Future<String?> read(String key) async => data[key];
  @override
  Future<void> write(String key, String? value) async =>
      value == null ? data.remove(key) : data[key] = value;
}

const _vpn = {
  'address': '10.8.0.10/32',
  'dns': ['10.8.0.1'],
  'mtu': 1280,
  'server_public_key': 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=',
  'preshared_key': 'BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBA=',
  'endpoint': 'vpn.filterphone.com:51820',
  'allowed_ips': ['0.0.0.0/0', '::/0'],
  'persistent_keepalive': 25,
};

MockClient fakeServer({int deviceStatus = 200, String? enrollError}) => MockClient((req) async {
      final path = req.url.path;
      if (path == '/v1/enroll') {
        if (enrollError != null) {
          return http.Response(jsonEncode({'error': enrollError}), 403,
              headers: {'content-type': 'application/json; charset=utf-8'});
        }
        final body = jsonDecode(req.body) as Map;
        expect(body['public_key'], isNotEmpty);
        expect(body['code'], 'ABCDE-FGHJK');
        return http.Response(
            jsonEncode({
              'token': 'device-token',
              'level': 1,
              'level_name': 'מחמיר',
              'vpn': _vpn,
              'ca': {'pem': '-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n'},
            }),
            201,
            headers: {'content-type': 'application/json; charset=utf-8'});
      }
      if (path == '/v1/transfer') {
        final body = jsonDecode(req.body) as Map;
        expect(body['token'], startsWith('FPT-'));
        expect(body['public_key'], isNotEmpty);
        return http.Response(
            jsonEncode({
              'token': 'new-device-token',
              'level': 2,
              'level_name': 'בינוני',
              'vpn': _vpn,
              'ca': {'pem': '-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n'},
            }),
            201,
            headers: {'content-type': 'application/json; charset=utf-8'});
      }
      if (path == '/v1/device') {
        expect(req.headers['authorization'], startsWith('Bearer '));
        if (deviceStatus != 200) {
          return http.Response(jsonEncode({'error': 'המכשיר לא מזוהה'}), deviceStatus,
              headers: {'content-type': 'application/json; charset=utf-8'});
        }
        return http.Response(
            jsonEncode({
              'name': 'Pixel',
              'status': 'active',
              'level': 1,
              'level_name': 'מחמיר',
              'via_vpn': true,
              'today': {'blocked': 4, 'painted': 1},
            }),
            200,
            headers: {'content-type': 'application/json; charset=utf-8'});
      }
      if (path == '/v1/device/vpn') {
        return http.Response(jsonEncode(_vpn), 200);
      }
      if (path == '/v1/device/rekey') {
        final body = jsonDecode(req.body) as Map;
        expect(body['public_key'], isNotEmpty);
        rekeys.add(body['public_key'] as String);
        return http.Response(jsonEncode(_vpn), 200);
      }
      return http.Response('not found', 404);
    });

final rekeys = <String>[];

/// שרת בלי expect בתוך התשובות — לבדיקות מסך, שבהן הבקשות רצות בזמן pumpWidget.
MockClient quietServer() => MockClient((req) async {
      const json = {'content-type': 'application/json; charset=utf-8'};
      return switch (req.url.path) {
        '/v1/device' => http.Response(
            jsonEncode({'name': 'Pixel', 'status': 'active', 'level': 1, 'level_name': 'מחמיר', 'via_vpn': true}),
            200,
            headers: json),
        '/v1/device/vpn' => http.Response(jsonEncode(_vpn), 200, headers: json),
        _ => http.Response('not found', 404),
      };
    });

const _catalog = {
  'levels': [
    {'level': 1, 'name': 'מחמיר', 'short': 'כל תמונה של אישה נחסמת', 'desc': '...'},
    {'level': 2, 'name': 'בינוני', 'short': 'לבוש צנוע עובר', 'desc': '...'},
    {'level': 3, 'name': 'בסיסי', 'short': 'רק עירום', 'desc': '...'},
  ],
  'categories': [
    {'id': 'messaging', 'title': 'וואטסאפ', 'desc': ''},
    {'id': 'google', 'title': 'גוגל', 'desc': ''},
  ],
  'options': [
    {'id': 'whatsapp_profile', 'category': 'messaging', 'title': 'תמונות פרופיל בוואטסאפ', 'popular': true},
    {'id': 'whatsapp_status', 'category': 'messaging', 'title': 'סטטוסים בוואטסאפ', 'popular': true},
    {'id': 'play_store', 'category': 'google', 'title': 'חנות Google Play', 'popular': true, 'warn': 'עדכונים ייעצרו'},
  ],
};

/// שרת מדומה להרשמה: ממתין → (אחרי approve()) פעיל עם חסימות.
class SignupServer {
  String status = 'pending';
  Map<String, Object?>? registered;
  Map<String, Object?>? changeRequest;
  Map<String, Object?>? report;
  bool cancelled = false;

  void approve() => status = 'active';

  static http.Response json(Object body, [int code = 200]) => http.Response(jsonEncode(body), code,
      headers: {'content-type': 'application/json; charset=utf-8'});

  MockClient get client => MockClient((req) async {
        final path = req.url.path;
        if (path == '/v1/catalog') return json(_catalog);
        if (path == '/v1/register') {
          registered = Map<String, Object?>.from(jsonDecode(req.body) as Map);
          return json({'device_id': 1, 'token': 'pending-token', 'status': 'pending', 'message': 'ממתין'}, 201);
        }
        expect(req.headers['authorization'], 'Bearer pending-token');
        if (path == '/v1/device' && req.method == 'DELETE') {
          cancelled = true;
          return http.Response('', 204);
        }
        if (path == '/v1/device') {
          if (cancelled) return json({'error': 'המכשיר לא מזוהה'}, 401);
          if (status != 'active') {
            return json({'name': 'Pixel', 'status': status, 'level': 2, 'level_name': 'בינוני',
                'message': status == 'rejected' ? 'הבקשה לא אושרה. לא שולם' : 'ממתין'});
          }
          return json({
            'name': 'Pixel', 'status': 'active', 'level': 2, 'level_name': 'בינוני', 'via_vpn': true,
            'today': {'blocked': 2, 'painted': 1, 'sites': 5},
            'blocks': [{'id': 'whatsapp_status', 'title': 'סטטוסים בוואטסאפ', 'category': 'messaging'}],
            'device_policy': {
              'blocked_apps': ['org.mozilla.firefox'],
              'blocked_screens': [{'package': 'com.whatsapp', 'match': ['StatusPlayback']}],
            },
            'apps_report_needed': true,
            if (changeRequest != null) 'request': {'kind': 'change', 'status': 'pending'},
          });
        }
        if (path == '/v1/device/vpn') {
          return json({..._vpn, 'ca': {'pem': '-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n'}});
        }
        if (path == '/v1/device/requests') {
          changeRequest = Map<String, Object?>.from(jsonDecode(req.body) as Map);
          return json({'request_id': 9, 'status': 'pending'}, 201);
        }
        if (path == '/v1/device/report') {
          report = Map<String, Object?>.from(jsonDecode(req.body) as Map);
          return json({'ok': true});
        }
        return http.Response('not found', 404);
      });
}

AppController makeController(FakeNative native, MockClient server, [MemoryStore? store]) =>
    AppController(
      native: native,
      api: ApiClient(client: server, baseUrl: 'https://api.test'),
      store: store ?? MemoryStore(),
    );

void main() {
  test('new install starts on the welcome screen', () async {
    final c = makeController(FakeNative(), fakeServer());
    await c.start();
    expect(c.phase, Phase.welcome);
    c.openActivation();
    expect(c.phase, Phase.activation);
    c.backToWelcome();
    expect(c.phase, Phase.welcome);
  });

  test('registration waits for approval, then connects by itself', () async {
    final native = FakeNative();
    final server = SignupServer();
    final store = MemoryStore();
    final c = makeController(native, server.client, store);
    await c.start();
    await c.openRegister();
    expect(c.phase, Phase.register);
    expect(c.catalog?.popular.length, 3);
    c.form
      ..name = 'משה כהן'
      ..phone = '050-1234567'
      ..level = 2
      ..options.addAll(['whatsapp_status', 'play_store'])
      ..note = 'לחסום גם אתר X';
    expect(await c.submitRegistration(), isTrue);
    expect(c.phase, Phase.pending);
    expect(server.registered?['name'], 'משה כהן');
    expect(server.registered?['options'], ['play_store', 'whatsapp_status']);
    expect(server.registered?['device_name'], 'Google Pixel 9');
    expect(store.data['device_token'], 'pending-token');
    expect(native.savedVpn, isNull); // עוד אין VPN

    // אחרי הפעלה מחדש של האפליקציה — עדיין במסך ההמתנה, עם הסיכום
    final c2 = makeController(native, server.client, store);
    await c2.start();
    expect(c2.phase, Phase.pending);
    expect(c2.pendingSummary?['note'], 'לחסום גם אתר X');

    server.approve();
    await c2.refreshPending();
    expect(c2.phase, Phase.home);
    expect(native.savedVpn?['address'], '10.8.0.10/32');
    expect(native.state, 'UP');
    expect(c2.device?.blocks.single.id, 'whatsapp_status');
    // החסימות בטלפון עברו לשירות הנייטיבי, והטלפון דיווח על האפליקציות שלו
    expect(native.guardPolicy?['blocked_apps'], ['org.mozilla.firefox']);
    expect(native.guardToken, 'pending-token'); // כדי שהמגן ירענן לבד גם כשהאפליקציה סגורה
    await Future<void>.delayed(Duration.zero);
    expect((server.report?['apps'] as List).length, 2);
    expect((server.report?['guard'] as Map)['accessibility'], isFalse);
    // המנהל חסם אפליקציות → בלי המגן הטלפון לא נחשב מוגן
    native.caInstalled = true;
    await c2.refresh();
    expect(c2.guardNeeded, isTrue);
    expect(c2.isProtected, isFalse);
    native.guard = true;
    await c2.refresh();
    expect(c2.isProtected, isTrue);
    c.dispose();
    c2.dispose();
  });

  test('rejected request shows the reason and can start over', () async {
    final native = FakeNative();
    final server = SignupServer()..status = 'rejected';
    final store = MemoryStore()..data['device_token'] = 'pending-token';
    final c = makeController(native, server.client, store);
    await c.start();
    expect(c.phase, Phase.pending);
    expect(c.device?.isRejected, isTrue);
    expect(c.device?.message, contains('לא שולם'));
    await c.cancelRegistration();
    expect(server.cancelled, isTrue);
    expect(c.phase, Phase.welcome);
    expect(store.data.containsKey('device_token'), isFalse);
    c.dispose();
  });

  test('change request is sent to the admin, never applied locally', () async {
    final native = FakeNative();
    final server = SignupServer()..status = 'active';
    final store = MemoryStore()
      ..data['device_token'] = 'pending-token'
      ..data['vpn_config_hash'] = 'x';
    final c = makeController(native, server.client, store);
    await c.start();
    expect(c.phase, Phase.home);
    expect(await c.requestChange(level: 3, options: ['play_store'], note: 'בבקשה'), isTrue);
    expect(server.changeRequest, {'level': 3, 'options': ['play_store'], 'note': 'בבקשה'});
    expect(c.device?.level, 2); // הרמה לא משתנה עד שהמנהל מאשר
    expect(c.device?.hasPendingChange, isTrue);
    expect(c.notice, contains('נשלחה'));
    c.dispose();
  });

  test('activation enrolls, saves VPN config and connects', () async {
    final native = FakeNative();
    final store = MemoryStore();
    final c = makeController(native, fakeServer(), store);
    await c.start();
    expect(await c.activate('abcde-fghjk'.toUpperCase(), ''), isTrue);
    expect(c.phase, Phase.home);
    expect(native.savedVpn?['address'], '10.8.0.10/32');
    expect(native.state, 'UP');
    expect(store.data['device_token'], 'device-token');
    expect(c.device?.blockedToday, 4);
    expect(c.device?.viaVpn, isTrue);
    // התעודה עוד לא מותקנת — הטלפון לא מוגן במלואו
    expect(c.isProtected, isFalse);
    native.caInstalled = true;
    await c.refresh();
    expect(c.isProtected, isTrue);
    c.dispose();
  });

  test('transfer code activates via /v1/transfer and inherits the level', () async {
    final native = FakeNative();
    final store = MemoryStore();
    final c = makeController(native, fakeServer(), store);
    await c.start();
    // קוד החלפה (ארוך, בלי מקף) → נתיב /v1/transfer, יורש רמה 2 מהמכשיר הישן
    expect(await c.activate('FPT-abc123def456ghi789', ''), isTrue);
    expect(c.phase, Phase.home);
    expect(store.data['device_token'], 'new-device-token');
    expect(native.savedVpn?['address'], '10.8.0.10/32');
    c.dispose();
  });

  test('wrong code shows the server message in Hebrew', () async {
    final c = makeController(FakeNative(), fakeServer(enrollError: 'קוד ההפעלה לא תקין או שפג תוקפו'));
    await c.start();
    c.openActivation();
    expect(await c.activate('ABCDE-FGHJK', ''), isFalse);
    expect(c.error, 'קוד ההפעלה לא תקין או שפג תוקפו');
    expect(c.phase, Phase.activation);
  });

  test('declining the VPN permission keeps the activation but asks again', () async {
    final native = FakeNative()..permission = false;
    final c = makeController(native, fakeServer());
    await c.start();
    expect(await c.activate('ABCDE-FGHJK', ''), isTrue);
    expect(c.phase, Phase.home);
    expect(c.notice, contains('VPN'));
    expect(c.checks[Check.vpn], isFalse);
    c.dispose();
  });

  test('strict private DNS breaks protection', () async {
    final native = FakeNative()
      ..caInstalled = true
      ..privateDns = 'hostname';
    final store = MemoryStore()
      ..data['device_token'] = 'device-token'
      ..data['vpn_config_hash'] = 'x';
    final c = makeController(native, fakeServer(), store);
    await c.start();
    native.state = 'UP';
    await c.refresh();
    expect(c.checks[Check.privateDns], isFalse);
    expect(c.isProtected, isFalse);
    c.dispose();
  });

  test('device removed by admin is detected and can be reset', () async {
    final native = FakeNative();
    final store = MemoryStore()
      ..data['device_token'] = 'device-token'
      ..data['vpn_config_hash'] = 'x';
    final c = makeController(native, fakeServer(deviceStatus: 401), store);
    await c.start();
    expect(c.deviceRemoved, isTrue);
    await c.resetDevice();
    expect(c.phase, Phase.welcome);
    expect(store.data.containsKey('device_token'), isFalse);
    expect(native.guardPolicy, isEmpty);
    expect(native.guardToken, isNull);
  });

  test('battery optimization blocks "protected" until the user exempts the app', () async {
    final native = FakeNative()
      ..caInstalled = true
      ..batteryUnrestricted = false;
    final store = MemoryStore()
      ..data['device_token'] = 'device-token'
      ..data['ca_pem'] = 'PEM'
      ..data['vpn_config_hash'] = 'x';
    final c = makeController(native, fakeServer(), store);
    await c.start();
    native.state = 'UP';
    await c.refresh();
    expect(c.checks[Check.battery], isFalse);
    expect(c.isProtected, isFalse);
    await c.requestBatteryExemption();
    expect(native.batteryRequests, 1);
    // המשתמש אישר בחלון של המערכת; חוזרים לאפליקציה → refresh
    native.batteryUnrestricted = true;
    await c.refresh();
    expect(c.checks[Check.battery], isTrue);
    expect(c.isProtected, isTrue);
    c.dispose();
  });

  test('home registers background reporting and syncs at most once a minute', () async {
    final native = FakeNative()..caInstalled = true;
    final store = MemoryStore()
      ..data['device_token'] = 'device-token'
      ..data['vpn_config_hash'] = 'x';
    final c = makeController(native, fakeServer(), store);
    await c.start();
    expect(native.credentialsToken, 'device-token');
    expect(native.credentialsApi, 'https://api.test');
    expect(native.protectionStarts, greaterThan(0));
    final before = native.syncs;
    await c.refresh();
    await c.refresh();
    expect(native.syncs, before); // פחות מדקה — בלי דיווח נוסף
    expect(native.syncs, greaterThanOrEqualTo(1));
    c.dispose();
  });

  test('device-owner QR provisioning activates by itself with the code from the QR', () async {
    final native = FakeNative()..provisioning = {'code': 'ABCDE-FGHJK', 'api': 'https://api.test'};
    final store = MemoryStore();
    final c = makeController(native, fakeServer(), store);
    await c.start();
    expect(c.phase, Phase.home);
    expect(store.data['device_token'], 'device-token');
    expect(native.savedVpn?['address'], '10.8.0.10/32');
    expect(native.provisioningCleared, isTrue);
    c.dispose();
  });

  test('lost VPN key is replaced: new key pair, server rekey, config saved again', () async {
    rekeys.clear();
    final native = FakeNative()
      ..hasKey = false
      ..savedVpn = null;
    final store = MemoryStore()
      ..data['device_token'] = 'device-token'
      ..data['vpn_config_hash'] = 'x';
    final c = makeController(native, fakeServer(), store);
    await c.start();
    await Future<void>.delayed(const Duration(milliseconds: 10));
    expect(rekeys, hasLength(1));
    expect(native.keyPairs, 1);
    expect(native.savedVpn?['address'], '10.8.0.10/32');
    expect(native.state, 'UP');
    c.dispose();
  });

  test('one-tap setup opens each remaining approval in turn and finishes', () async {
    final native = FakeNative()
      ..batteryUnrestricted = false;
    final c = makeController(native, fakeServer());
    await c.start();
    await c.activate('ABCDE-FGHJK', '');
    expect(c.vpn.isUp, isTrue);
    await c.oneTapSetup();
    expect(c.setupStep, SetupStep.certificate);         // VPN כבר מחובר — הצעד הבא: תעודה
    native.caInstalled = true;                          // המשתמש התקין בהגדרות וחזר
    await c.onAppResumed();
    expect(c.setupStep, SetupStep.battery);
    expect(native.batteryRequests, 1);
    native.batteryUnrestricted = true;                  // אישר את חלון הסוללה
    await c.onAppResumed();
    expect(c.setupStep, SetupStep.done);
    expect(c.isProtected, isTrue);
    expect(c.notice, contains('הושלמה'));
  });

  test('one-tap setup does not reopen a skipped approval in a loop', () async {
    final native = FakeNative();
    final c = makeController(native, fakeServer());
    await c.start();
    await c.activate('ABCDE-FGHJK', '');
    await c.oneTapSetup();
    expect(c.setupStep, SetupStep.certificate);
    await c.onAppResumed();                              // חזר בלי להתקין
    expect(c.setupStep, isNull);
    expect(c.notice, contains('נעצרה'));
  });

  test('authorized pause: restore brings protection back; remove-all wipes the phone', () async {
    final native = FakeNative()..paused = true;
    final store = MemoryStore();
    final c = makeController(native, fakeServer(), store);
    await c.start();
    await c.activate('ABCDE-FGHJK', '');
    await c.refresh();
    expect(c.adminPauseActive, isTrue);
    expect(c.pauseUntil, isNotNull);
    expect(await c.resumeProtection(), isTrue);
    expect(native.resumes, 1);
    expect(c.adminPauseActive, isFalse);

    native.paused = true;
    await c.refresh();
    final report = await c.uninstallEverything();
    expect(report?['ok'], isTrue);
    expect(native.uninstalls, 1);
    expect(c.phase, Phase.welcome);
    expect(await store.read('device_token'), isNull);
  });

  test('restore by phone: same phone comes back by itself, another phone asks to transfer', () async {
    String? requested;
    final server = MockClient((req) async {
      const json = {'content-type': 'application/json; charset=utf-8'};
      final body = req.body.isEmpty ? <String, Object?>{} : jsonDecode(req.body) as Map;
      switch (req.url.path) {
        case '/v1/restore':
          expect(body['phone'], '050-1234567');
          if (body['device_uid'] == 'ab' * 16) {
            return http.Response(jsonEncode({'status': 'active', 'device_id': 3, 'token': 'restored-token', 'level': 2,
                'level_name': 'בינוני', 'vpn': _vpn,
                'ca': {'pem': '-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n'}}), 200, headers: json);
          }
          return http.Response(jsonEncode({'status': 'needs_choice', 'customer_name': 'משה',
              'devices': [{'id': 3, 'name': 'גלקסי ישן', 'status': 'active', 'last_seen_at': 1}]}), 200, headers: json);
        case '/v1/restore/request':
          requested = '${body['mode']}:${body['old_device_id']}';
          return http.Response(jsonEncode({'device_id': 9, 'token': 'swap-token', 'status': 'pending',
              'message': 'נשלח למנהל'}), 201, headers: json);
        case '/v1/device':
          return http.Response(jsonEncode({'name': 'Pixel', 'status': 'pending', 'level': 2, 'level_name': 'בינוני'}),
              200, headers: json);
      }
      return http.Response(jsonEncode({'name': 'Pixel', 'status': 'active', 'level': 2, 'level_name': 'בינוני'}),
          200, headers: json);
    });
    // אותו טלפון
    final same = FakeNative();
    final c1 = makeController(same, server);
    await c1.start();
    c1.openRestore();
    expect(c1.phase, Phase.restore);
    expect(await c1.restoreByPhone('050-1234567'), isTrue);
    expect(c1.phase, Phase.home);
    expect(same.savedVpn, isNotNull);
    // טלפון אחר
    final other = FakeNative()..uid = 'cd' * 16;
    final store = MemoryStore();
    final c2 = makeController(other, server, store);
    await c2.start();
    c2.openRestore();
    await c2.restoreByPhone('050-1234567');
    expect(c2.phase, Phase.restore);
    expect(c2.restoreResult?.status, 'needs_choice');
    expect(c2.restoreResult?.devices.single.name, 'גלקסי ישן');
    expect(await c2.requestRestore(mode: 'swap', oldDeviceId: 3), isTrue);
    expect(requested, 'swap:3');
    expect(c2.phase, Phase.pending);
    expect(await store.read('device_token'), 'swap-token');
  });

  test('lost VPN config (same key) is saved again from the server', () async {
    rekeys.clear();
    final native = FakeNative()..savedVpn = null;
    final store = MemoryStore()
      ..data['device_token'] = 'device-token'
      ..data['vpn_config_hash'] = base64Encode(utf8.encode(jsonEncode(_vpn)));     // אותו hash — בעבר לא נשמר שוב
    final c = makeController(native, fakeServer(), store);
    await c.start();
    await Future<void>.delayed(const Duration(milliseconds: 10));
    expect(rekeys, isEmpty);
    expect(native.savedVpn?['address'], '10.8.0.10/32');
    c.dispose();
  });

  testWidgets('welcome and activation screens render in Hebrew, right to left', (tester) async {
    final c = makeController(FakeNative(), fakeServer());
    await c.start();
    await tester.pumpWidget(FilterPhoneApp(controller: c));
    await tester.pump();
    expect(find.text('הצטרפות לשירות'), findsOneWidget);
    await tester.tap(find.text('יש לי קוד הפעלה'));
    await tester.pump();
    expect(find.text('הזינו את קוד ההפעלה שקיבלתם'), findsOneWidget);
    final dir = Directionality.of(tester.element(find.text('הפעלה')));
    expect(dir, TextDirection.rtl);
  });

  testWidgets('a new version (without device owner) shows a download button', (tester) async {
    final native = FakeNative()
      ..caInstalled = true
      ..updateAvailable = '1.0.8';
    final store = MemoryStore()
      ..data['device_token'] = 'device-token'
      ..data['ca_pem'] = 'PEM'
      ..data['vpn_config_hash'] = 'x';
    final c = makeController(native, quietServer(), store);
    await c.start();
    // המסך הראשי לבד (בלי מחזור החיים של האפליקציה, שמרענן מול השרת בזמן pumpWidget)
    await tester.pumpWidget(MaterialApp(
      home: Directionality(
        textDirection: TextDirection.rtl,
        child: ListenableBuilder(listenable: c, builder: (_, _) => HomeScreen(controller: c)),
      ),
    ));
    await tester.pump();
    expect(find.text('גרסה חדשה של FilterPhone (1.0.8)'), findsOneWidget);
    await tester.tap(find.text('להורדה'));
    await tester.pump();
    expect(native.updatePageOpens, 1);
    // אחרי העדכון הטלפון כבר לא מדווח על גרסה חדשה — ההודעה נעלמת
    native.updateAvailable = null;
    await c.refresh();
    await tester.pump();
    expect(find.text('להורדה'), findsNothing);
    c.dispose();
  });

  testWidgets('registration form walks through all four steps', (tester) async {
    final server = SignupServer();
    final c = makeController(FakeNative(), server.client);
    await c.start();
    await tester.pumpWidget(FilterPhoneApp(controller: c));
    await tester.tap(find.text('הצטרפות לשירות'));
    await tester.pumpAndSettle();
    // שלב 1: בלי שם — לא ממשיכים
    await tester.tap(find.text('המשך'));
    await tester.pumpAndSettle();
    expect(find.text('נא למלא שם מלא'), findsOneWidget);
    await tester.enterText(find.byType(TextFormField).at(0), 'שרה לוי');
    await tester.enterText(find.byType(TextFormField).at(1), '052-7654321');
    await tester.tap(find.text('המשך'));
    await tester.pumpAndSettle();
    // שלב 2: רמה
    expect(find.text('איזו רמת סינון תמונות?'), findsOneWidget);
    await tester.tap(find.text('רמה 2 · בינוני'));
    await tester.tap(find.text('המשך'));
    await tester.pumpAndSettle();
    // שלב 3: "הכי מבוקשים" מודגשים
    expect(find.text('הכי מבוקשים'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilterChip, 'תמונות פרופיל בוואטסאפ'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('המשך'));
    await tester.pumpAndSettle();
    // שלב 4: סיכום ושליחה
    expect(find.text('הכול נכון?'), findsOneWidget);
    expect(find.text('שרה לוי'), findsOneWidget);
    await tester.tap(find.text('שליחת הבקשה'));
    await tester.pumpAndSettle();
    expect(server.registered?['level'], 2);
    expect(server.registered?['options'], ['whatsapp_profile']);
    expect(find.text('הבקשה נשלחה!'), findsOneWidget);
    // ה-controller נסגר יחד עם האפליקציה (FilterPhoneApp.dispose) — לא כאן
  });
}
