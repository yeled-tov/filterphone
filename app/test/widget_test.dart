import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:filterphone/main.dart';
import 'package:filterphone/src/api_client.dart';
import 'package:filterphone/src/app_controller.dart';
import 'package:filterphone/src/native_bridge.dart';
import 'package:filterphone/src/screens/app_shell.dart';
import 'package:filterphone/src/screens/home_screen.dart';
import 'package:filterphone/src/screens/settings_screen.dart';
import 'package:filterphone/src/support_bot.dart';
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
  String androidIdValue = '0123456789abcdef';
  @override
  Future<String> androidId() async => androidIdValue;
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
  bool deviceOwner = false;
  @override
  Future<bool> isDeviceOwner() async => deviceOwner;
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

  bool wireless = true;
  int devOptionsOpened = 0;
  String? wirelessCode;
  Map<String, Object?> wirelessResult = {'root': false, 'device_owner': false, 'secure_settings': false};
  @override
  Future<bool> wirelessSupported() async => wireless;
  @override
  Future<bool> openDeveloperOptions() async {
    devOptionsOpened++;
    return true;
  }
  @override
  Future<Map<String, Object?>> provisionWireless(String code, int port, String? caPem) async {
    wirelessCode = code;
    return wirelessResult;
  }

  int wirelessNotifications = 0;
  @override
  Future<bool> startWirelessNotification(String? caPem) async {
    wirelessNotifications++;
    return true;
  }

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
  Future<String> openUpdatePage() async {
    updatePageOpens++;
    return 'page';
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

  final whatsappOpens = <String>[];
  String waNumber = '';
  String waMessage = '';
  @override
  Future<void> openSupportWhatsApp(String number, String message) async {
    waNumber = number;
    waMessage = message;
    whatsappOpens.add(number);
  }

  final shared = <String>[];
  @override
  Future<void> shareText(String text) async => shared.add(text);

  String? launch;
  int supportNotificationClears = 0;
  @override
  Future<String?> launchTarget() async {
    final t = launch;
    launch = null;
    return t;
  }
  @override
  Future<void> clearSupportNotification() async => supportNotificationClears++;

  final installs = <String>[];
  @override
  Future<String> installStoreApp(Map<String, Object?> item) async {
    installs.add(item['package'] as String);
    return 'started';
  }

  final storeUninstalls = <String>[];
  @override
  Future<String> uninstallApp(String package) async {
    storeUninstalls.add(package);
    return 'started';
  }

  List<InstalledApp> phoneApps = const [];
  final progressEvents = StreamController<InstallProgress>.broadcast();
  @override
  Future<List<InstalledApp>> installedForUpdates() async => phoneApps;
  @override
  Stream<InstallProgress> installProgress() => progressEvents.stream;

  final playOpens = <String>[];
  final playTitles = <List<String>>[];
  @override
  Future<bool> openPlay(String package, String? url, {List<String> titles = const []}) async {
    playTitles.add(titles);
    playOpens.add(package);
    return true;
  }
  @override
  Future<Map<String, Map<String, Object?>>> storeAppStates(List<String> packages) async =>
      {for (final p in packages) p: {'installed_version': p == 'org.example.notes' ? 3 : null, 'state': null}};
  @override
  Future<bool> openApp(String package) async => true;
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

  test('reinstall on a known phone (same Android ID) opens restore by itself', () async {
    String? sentUid;
    final server = MockClient((req) async {
      const json = {'content-type': 'application/json; charset=utf-8'};
      if (req.url.path == '/v1/recognize') {
        sentUid = (jsonDecode(req.body) as Map)['device_uid'] as String?;
        return http.Response(jsonEncode({'known': true, 'status': 'restorable', 'device_name': 'גלקסי'}), 200,
            headers: json);
      }
      return http.Response('{}', 404, headers: json);
    });
    final native = FakeNative();
    final c = makeController(native, server);
    await c.start();
    expect(sentUid, native.uid);
    expect(c.phase, Phase.restore);
    expect(c.recognized, 'restorable');
    expect(c.notice, contains('זיהינו'));
  });

  test('a phone the server does not know stays on the welcome screen', () async {
    final server = MockClient((req) async => http.Response(jsonEncode({'known': false}), 200,
        headers: {'content-type': 'application/json; charset=utf-8'}));
    final c = makeController(FakeNative(), server);
    await c.start();
    expect(c.phase, Phase.welcome);
    expect(c.recognized, isNull);
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

  testWidgets('store shows the server catalog with install, update and request actions', (tester) async {
    final native = FakeNative()..caInstalled = true;
    final server = MockClient((req) async {
      const json = {'content-type': 'application/json; charset=utf-8'};
      if (req.url.path == '/v1/device') {
        return http.Response(jsonEncode({'name': 'Pixel', 'status': 'active', 'level': 1, 'level_name': 'מחמיר',
            'via_vpn': true, 'managed_apps': [
              {'package': 'org.example.notes', 'label': 'פתקים', 'category': 'כלים', 'url': 'https://api.test/v1/apps/n-4.apk',
               'sha256': 'ab' * 32, 'version_code': 4, 'version_name': '1.4', 'added_at': 1},
              {'package': 'org.example.maps', 'label': 'מפות', 'category': 'ניווט'},
            ]}), 200, headers: json);
      }
      if (req.url.path == '/v1/device/vpn') return http.Response(jsonEncode(_vpn), 200, headers: json);
      if (req.url.path == '/v1/enroll') {
        return http.Response(jsonEncode({'token': 't', 'level': 1, 'level_name': 'מחמיר', 'vpn': _vpn,
            'ca': {'pem': 'x'}}), 201, headers: json);
      }
      return http.Response('{}', 200, headers: json);
    });
    final c = makeController(native, server);
    await tester.runAsync(() async {
      await c.start();
      await c.activate('ABCDE-FGHJK', '');
      await c.refresh();
    });
    expect(c.device!.storeApps.map((a) => a.label), ['פתקים', 'מפות']);
    expect(c.device!.storeApps.first.directInstall, isTrue);
    expect(c.device!.storeApps.last.directInstall, isFalse);
    await tester.pumpWidget(MaterialApp(home: Directionality(textDirection: TextDirection.rtl,
        child: AppStoreScreen(controller: c))));
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 50)));
    await tester.pumpAndSettle();
    // הגריד מציג אריח לכל אפליקציה
    expect(find.text('פתקים'), findsOneWidget);
    expect(find.text('מפות'), findsOneWidget);
    // לחיצה על אריח פותחת את עמוד האפליקציה; בלי מנהל מכשיר — כפתור "בקשה מהמנהל"
    await tester.tap(find.text('מפות'));
    await tester.pumpAndSettle();
    expect(find.text('בקשה מהמנהל'), findsOneWidget);
  });

  testWidgets('store has category tabs on top, rows under "all", and filtering by tab', (tester) async {
    tester.view.physicalSize = const Size(800, 2000);       // כל השורות על המסך
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    final native = FakeNative()..caInstalled = true;
    final server = MockClient((req) async {
      const json = {'content-type': 'application/json; charset=utf-8'};
      if (req.url.path == '/v1/device') {
        return http.Response(jsonEncode({'name': 'Pixel', 'status': 'active', 'level': 1, 'level_name': 'מחמיר',
            'via_vpn': true,
            'store_categories': ['תקשורת', 'בנקים ותשלומים', 'תחבורה', 'מדיה'],
            'managed_apps': [
              {'package': 'com.waze', 'label': 'וייז', 'category': 'תחבורה',
               'play_url': 'https://play.google.com/store/apps/details?id=com.waze',
               'play_titles': ['Waze: ניווט ועדכוני תנועה']},
              {'package': 'com.whatsapp', 'label': 'וואטסאפ', 'category': 'תקשורת',
               'play_url': 'https://play.google.com/store/apps/details?id=com.whatsapp'},
              {'package': 'com.filtertube.app', 'label': 'FilterTube', 'category': 'מדיה'},
            ]}), 200, headers: json);
      }
      if (req.url.path == '/v1/device/vpn') return http.Response(jsonEncode(_vpn), 200, headers: json);
      if (req.url.path == '/v1/enroll') {
        return http.Response(jsonEncode({'token': 't', 'level': 1, 'level_name': 'מחמיר', 'vpn': _vpn,
            'ca': {'pem': 'x'}}), 201, headers: json);
      }
      return http.Response('{}', 200, headers: json);
    });
    final c = makeController(native, server);
    await tester.runAsync(() async {
      await c.start();
      await c.activate('ABCDE-FGHJK', '');
      await c.refresh();
    });
    await tester.pumpWidget(MaterialApp(home: Directionality(textDirection: TextDirection.rtl,
        child: AppStoreScreen(controller: c))));
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 50)));
    await tester.pumpAndSettle();
    // טאבים בסדר מהשרת — רק קטגוריות שיש בהן אפליקציות ("בנקים ותשלומים" ריקה ולא מוצגת)
    final tabs = ['הכול', 'תקשורת', 'תחבורה', 'מדיה'];
    final xs = [for (final t in tabs) tester.getCenter(find.text(t).first).dx];
    for (var i = 1; i < xs.length; i++) {
      expect(xs[i], lessThan(xs[i - 1]), reason: 'RTL: ${tabs[i]} אחרי ${tabs[i - 1]}');
    }
    expect(find.text('בנקים ותשלומים'), findsNothing);
    // "הכול": שורה לכל קטגוריה, עם כל האפליקציות
    expect(find.text('וייז'), findsOneWidget);
    expect(find.text('וואטסאפ'), findsOneWidget);
    expect(find.text('FilterTube'), findsOneWidget);
    // טאב "מדיה" — רק FilterTube
    await tester.tap(find.text('מדיה').first);
    await tester.pumpAndSettle();
    expect(find.text('FilterTube'), findsOneWidget);
    expect(find.text('וייז'), findsNothing);
    expect(find.text('וואטסאפ'), findsNothing);
    // אפליקציה מ-Google Play: הכפתור פותח את העמוד שלה ב-Play
    await tester.tap(find.text('הכול').first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('וייז'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('התקנה מ-Google Play'));
    await tester.pump(const Duration(milliseconds: 200));     // פס "מתקין…" מסתובב עד ש-Play מתחיל
    expect(native.playOpens, ['com.waze']);
    expect(find.text('מתקין…'), findsOneWidget);
    // השמות שהמגן מזהה בחלון ההתקנה: מה שהשרת שלח מ-Play + השם בחנות
    expect(native.playTitles.single, ['Waze: ניווט ועדכוני תנועה', 'וייז']);
  });

  testWidgets('updates tab: all phone apps, Play status, progress bar, and update-all queue', (tester) async {
    tester.view.physicalSize = const Size(800, 2000);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    final native = FakeNative()
      ..caInstalled = true
      ..phoneApps = const [
        InstalledApp(package: 'com.bnhp.payments.paymentsapp', label: 'ביט', versionName: '6.10', versionCode: 610,
            fromPlay: true, sizeBytes: 214 * 1024 * 1024),
        InstalledApp(package: 'com.waze', label: 'Waze', versionName: '5.1', versionCode: 51, updatedMs: 1000),
        InstalledApp(package: 'com.google.android.calculator', label: 'מחשבון', versionName: '9.0', versionCode: 90,
            system: true),
      ];
    final checked = <Object?>[];
    final server = MockClient((req) async {
      const json = {'content-type': 'application/json; charset=utf-8'};
      if (req.url.path == '/v1/device') {
        return http.Response(jsonEncode({'name': 'Pixel', 'status': 'active', 'level': 1, 'level_name': 'מחמיר',
            'via_vpn': true, 'managed_apps': [
              {'package': 'com.waze', 'label': 'וייז', 'category': 'תחבורה',
               'play_url': 'https://play.google.com/store/apps/details?id=com.waze', 'play_titles': ['Waze Navigation']},
            ]}), 200, headers: json);
      }
      if (req.url.path == '/v1/device/app-updates') {
        checked.add(jsonDecode(req.body));
        final day = DateTime.now().millisecondsSinceEpoch ~/ 1000 - 3 * 86400;
        return http.Response(jsonEncode({'apps': {
          'com.bnhp.payments.paymentsapp': {'status': 'update', 'play_version': '6.18', 'play_updated': day},
          'com.waze': {'status': 'maybe', 'play_version': null, 'play_updated': day},
          'com.google.android.calculator': {'status': 'current', 'play_version': null, 'play_updated': 1},
        }}), 200, headers: json);
      }
      if (req.url.path == '/v1/device/vpn') return http.Response(jsonEncode(_vpn), 200, headers: json);
      if (req.url.path == '/v1/enroll') {
        return http.Response(jsonEncode({'token': 't', 'level': 1, 'level_name': 'מחמיר', 'vpn': _vpn,
            'ca': {'pem': 'x'}}), 201, headers: json);
      }
      return http.Response('{}', 200, headers: json);
    });
    final c = makeController(native, server);
    await tester.runAsync(() async {
      await c.start();
      await c.activate('ABCDE-FGHJK', '');
      await c.refresh();
    });
    await tester.pumpWidget(MaterialApp(home: Directionality(textDirection: TextDirection.rtl,
        child: AppStoreScreen(controller: c))));
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 50)));
    await tester.pumpAndSettle();

    await tester.tap(find.text('עדכונים'));
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 100)));
    await tester.pumpAndSettle();
    // נשלח לשרת: חבילה, גרסה ומתי עודכנה — בלי אייקונים
    final sent = (checked.single as Map)['apps'] as List;
    expect(sent.first, {'package': 'com.bnhp.payments.paymentsapp', 'version_name': '6.10', 'updated_ms': 0});
    expect(find.text('2 עדכונים זמינים'), findsOneWidget);
    // כרטיס: שם, חבילה, תגית Google Play, "מותקנת: X ← Y" וגודל
    expect(find.text('ביט'), findsOneWidget);
    expect(find.text('com.bnhp.payments.paymentsapp'), findsOneWidget);
    expect(find.text('Google Play'), findsOneWidget);
    expect(find.text('מותקנת: 6.10 ← 6.18'), findsOneWidget);
    expect(find.text('214 MB'), findsOneWidget);
    expect(find.textContaining('עודכנה ב-Google Play לפני 3 ימים'), findsOneWidget);
    expect(find.text('מעודכנות ואחרות (1)'), findsOneWidget);
    // הכפתור קומפקטי — לא נמתח לכל רוחב הכרטיס (כמו בערכת הנושא)
    final btn = tester.getSize(find.ancestor(of: find.text('עדכון').first, matching: find.byWidgetPredicate((w) => w is FilledButton)));
    expect(btn.width, lessThan(200));

    // "עדכון" לביט → נפתח עמוד Play נעול; פס "ממתין" (מסתובב — לכן pump ולא pumpAndSettle)
    await tester.tap(find.text('עדכון').first);
    await tester.pump(const Duration(milliseconds: 200));
    expect(native.playOpens, ['com.bnhp.payments.paymentsapp']);
    expect(find.text('ממתין ל-Google Play…'), findsOneWidget);
    native.progressEvents.add(const InstallProgress('com.bnhp.payments.paymentsapp', .45, 'running'));
    await tester.pump(const Duration(milliseconds: 200));
    expect(find.text('מתקין… 45%'), findsOneWidget);
    native.progressEvents.add(const InstallProgress('com.bnhp.payments.paymentsapp', 1, 'done'));
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 50)));
    await tester.pump(const Duration(milliseconds: 200));
    expect(find.text('1 עדכונים זמינים'), findsOneWidget);
    expect(find.text('מתקין… 45%'), findsNothing);

    // "עדכון הכול" — וייז נפתחת עם השם שלה ב-Play (לחלון הנעול)
    await tester.tap(find.text('עדכון הכול'));
    await tester.pump(const Duration(milliseconds: 200));
    expect(native.playOpens.last, 'com.waze');
    expect(native.playTitles.last, containsAll(['Waze Navigation', 'וייז', 'Waze']));
  });

  testWidgets('updates tab: ⊘ excludes an app from updates and update-all, persisted, with restore', (tester) async {
    tester.view.physicalSize = const Size(800, 2000);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    final native = FakeNative()
      ..caInstalled = true
      ..phoneApps = const [
        InstalledApp(package: 'com.bnhp.payments.paymentsapp', label: 'ביט', versionName: '6.10', versionCode: 610),
        InstalledApp(package: 'com.waze', label: 'Waze', versionName: '5.1', versionCode: 51),
      ];
    final server = MockClient((req) async {
      const json = {'content-type': 'application/json; charset=utf-8'};
      if (req.url.path == '/v1/device') {
        return http.Response(jsonEncode({'name': 'Pixel', 'status': 'active', 'level': 1, 'level_name': 'מחמיר',
            'via_vpn': true}), 200, headers: json);
      }
      if (req.url.path == '/v1/device/app-updates') {
        return http.Response(jsonEncode({'apps': {
          'com.bnhp.payments.paymentsapp': {'status': 'update', 'play_version': '6.18'},
          'com.waze': {'status': 'update', 'play_version': '5.2'},
        }}), 200, headers: json);
      }
      if (req.url.path == '/v1/device/vpn') return http.Response(jsonEncode(_vpn), 200, headers: json);
      if (req.url.path == '/v1/enroll') {
        return http.Response(jsonEncode({'token': 't', 'level': 1, 'level_name': 'מחמיר', 'vpn': _vpn,
            'ca': {'pem': 'x'}}), 201, headers: json);
      }
      return http.Response('{}', 200, headers: json);
    });
    final store = MemoryStore();
    final c = makeController(native, server, store);
    await tester.runAsync(() async {
      await c.start();
      await c.activate('ABCDE-FGHJK', '');
      await c.refresh();
    });
    await tester.pumpWidget(MaterialApp(home: Directionality(textDirection: TextDirection.rtl,
        child: AppStoreScreen(controller: c))));
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 50)));
    await tester.pumpAndSettle();
    await tester.tap(find.text('עדכונים'));
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 100)));
    await tester.pumpAndSettle();
    expect(find.text('2 עדכונים זמינים'), findsOneWidget);

    // ⊘ על וייז → יוצאת מהספירה, עוברת ל"מוחרגות" ונשמרת בטלפון
    await tester.tap(find.byTooltip('לא לעדכן את האפליקציה הזו').last);
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 20)));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('Waze לא תתעדכן'), findsOneWidget);
    expect(find.text('1 עדכונים זמינים'), findsOneWidget);
    expect(find.text('מוחרגות מעדכון (1)'), findsOneWidget);
    expect(jsonDecode(store.data['store_update_excluded']!), ['com.waze']);

    // "עדכון הכול" — רק ביט; וייז המוחרגת לא נפתחת
    await tester.tap(find.text('עדכון הכול'));
    await tester.pump(const Duration(milliseconds: 200));
    expect(native.playOpens, ['com.bnhp.payments.paymentsapp']);

    // ההחרגה נשארת גם אחרי פתיחה מחדש של האפליקציה
    final c2 = makeController(native, server, store);
    await tester.runAsync(c2.start);
    expect(c2.updateExcluded, {'com.waze'});

    // החזרה לעדכונים
    await tester.tap(find.text('מוחרגות מעדכון (1)'));
    await tester.pump(const Duration(milliseconds: 400));          // פס "ממתין" של ביט מסתובב — לכן לא pumpAndSettle
    await tester.pump(const Duration(milliseconds: 400));
    await tester.tap(find.text('החזרה לעדכונים'));
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 20)));
    await tester.pump(const Duration(milliseconds: 300));
    expect(c.updateExcluded, isEmpty);
    expect(find.text('מוחרגות מעדכון (1)'), findsNothing);
  });

  testWidgets('device owner installs directly from the store detail page', (tester) async {
    final native = FakeNative()
      ..caInstalled = true
      ..deviceOwner = true;
    final server = MockClient((req) async {
      const json = {'content-type': 'application/json; charset=utf-8'};
      if (req.url.path == '/v1/device') {
        return http.Response(jsonEncode({'name': 'Pixel', 'status': 'active', 'level': 1, 'level_name': 'מחמיר',
            'via_vpn': true, 'managed_apps': [
              {'package': 'org.example.newapp', 'label': 'פתקים', 'category': 'כלים', 'url': 'https://api.test/v1/apps/n-4.apk',
               'sha256': 'ab' * 32, 'version_code': 4, 'version_name': '1.4', 'added_at': 1,
               'description': 'אפליקציית פתקים כשרה', 'screenshots': <String>[]},
            ]}), 200, headers: json);
      }
      if (req.url.path == '/v1/device/vpn') return http.Response(jsonEncode(_vpn), 200, headers: json);
      if (req.url.path == '/v1/enroll') {
        return http.Response(jsonEncode({'token': 't', 'level': 1, 'level_name': 'מחמיר', 'vpn': _vpn,
            'ca': {'pem': 'x'}}), 201, headers: json);
      }
      return http.Response('{}', 200, headers: json);
    });
    final c = makeController(native, server);
    await tester.runAsync(() async {
      await c.start();
      await c.activate('ABCDE-FGHJK', '');
      await c.refresh();
    });
    await tester.pumpWidget(MaterialApp(home: Directionality(textDirection: TextDirection.rtl,
        child: AppStoreScreen(controller: c))));
    await tester.pumpAndSettle();
    await tester.tap(find.text('פתקים'));
    await tester.pumpAndSettle();
    expect(find.text('אפליקציית פתקים כשרה'), findsOneWidget);        // התיאור בעמוד
    await tester.tap(find.text('התקנה'));
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 20)));
    expect(native.installs, contains('org.example.newapp'));
  });

  group('support assistant', () {
    const healthy = BotContext(vpnUp: true, caInstalled: true, batteryOk: true, levelName: 'מחמיר');

    test('answers from the real phone state and offers the fix', () {
      final down = SupportBot.answer('האינטרנט לא עובד', const BotContext(vpnUp: false));
      expect(down.topic, 'vpn');
      expect(down.text, contains('מנותק'));
      expect(down.actions.first, BotAction.reconnectVpn);
      final battery = SupportBot.answer('ה-VPN מתנתק כל הזמן', const BotContext(vpnUp: true, batteryOk: false));
      expect(battery.actions, [BotAction.battery]);
      final fine = SupportBot.answer('vpn', healthy);
      expect(fine.text, contains('תקין'));
      final paused = SupportBot.answer('אין סינון', const BotContext(paused: true));
      expect(paused.text, contains('מושהית'));
    });

    test('picks the specific topic before the general one', () {
      expect(SupportBot.answer('יש לי אפליקציה אפורה', healthy).topic, 'grey_app');
      expect(SupportBot.answer('איך מתקינים אפליקציה?', healthy).topic, 'install');
      expect(SupportBot.answer('אתר נחסם בטעות', healthy).topic, 'blocked');
      expect(SupportBot.answer('FilterTube לא נטען', healthy).topic, 'youtube');
      // "תודה" בתוך תלונה — עונים על התלונה, לא על התודה
      expect(SupportBot.answer('תודה, אבל ה-VPN עדיין לא עובד', const BotContext()).topic, 'vpn');
      expect(SupportBot.answer('תודה רבה!', healthy).topic, 'thanks');
      expect(SupportBot.answer('שלום', healthy).topic, 'greeting');
    });

    test('guard and WhatsApp answers depend on whether the guard is on', () {
      const off = BotContext(guardNeeded: true, guardOn: false);
      expect(SupportBot.answer('הנגישות נכבתה', off).actions, [BotAction.openGuard]);
      expect(SupportBot.answer('רואים תמונות פרופיל בוואטסאפ', off).actions, [BotAction.openGuard]);
      expect(SupportBot.answer('וואטסאפ', const BotContext(guardNeeded: true, guardOn: true)).actions, isEmpty);
    });

    test('unknown questions and explicit asks go to a representative', () {
      expect(SupportBot.answer('קשקוש בלבוש', healthy).actions, [BotAction.human]);
      expect(SupportBot.answer('אני רוצה לדבר עם נציג', healthy).actions, [BotAction.human]);
      expect(SupportBot.answer('איך משהים את ההגנה?', healthy).actions, [BotAction.human]);
    });
  });

  test('chat with a representative: diagnostics attached, replies arrive, unread from the server', () async {
    final native = FakeNative()..caInstalled = true;
    final sent = <Map<String, Object?>>[];
    final replies = <Map<String, Object?>>[];
    var unread = 0;
    final server = MockClient((req) async {
      const json = {'content-type': 'application/json; charset=utf-8'};
      switch (req.url.path) {
        case '/v1/enroll':
          return http.Response(jsonEncode({'token': 't', 'level': 1, 'level_name': 'מחמיר', 'vpn': _vpn,
              'ca': {'pem': 'x'}}), 201, headers: json);
        case '/v1/device/vpn':
          return http.Response(jsonEncode(_vpn), 200, headers: json);
        case '/v1/device':
          return http.Response(jsonEncode({'name': 'Pixel', 'status': 'active', 'level': 1, 'level_name': 'מחמיר',
              'via_vpn': true, 'support': {'unread': unread, 'whatsapp': '972521112233'}}), 200, headers: json);
        case '/v1/device/support' when req.method == 'POST':
          final body = Map<String, Object?>.from(jsonDecode(req.body) as Map);
          sent.add(body);
          return http.Response(jsonEncode({'id': sent.length, 'from': 'customer', 'text': body['text'], 'ts': 1000}),
              201, headers: json);
        case '/v1/device/support':
          final after = int.parse(req.url.queryParameters['after'] ?? '0');
          final all = [
            for (var i = 0; i < sent.length; i++) {'id': i + 1, 'from': 'customer', 'text': sent[i]['text'], 'ts': 1000},
            ...replies,
          ].where((m) => (m['id'] as int) > after).toList();
          unread = 0;
          return http.Response(jsonEncode({'messages': all}), 200, headers: json);
      }
      return http.Response('{}', 200, headers: json);
    });
    final c = makeController(native, server);
    await c.start();
    await c.activate('ABCDE-FGHJK', '');
    expect(c.supportWhatsApp, '972521112233');                      // מהשרת — לא מהבנייה
    expect(await c.sendSupport('ה-VPN לא מתחבר', topic: 'vpn'), isNull);
    final diag = Map<String, Object?>.from(sent.single['diag'] as Map);
    expect(diag['topic'], 'vpn');
    expect(diag['app_version'], '1.0.0');
    expect(diag.keys, isNot(contains('contacts')));
    expect(c.supportThread!.single.text, 'ה-VPN לא מתחבר');
    // הנציג ענה (מוואטסאפ) — מגיע בטעינה הבאה, רק החדשות
    replies.add({'id': 2, 'from': 'support', 'text': 'תפעיל מחדש 🙂', 'ts': 1100});
    unread = 1;
    await c.refresh();
    expect(c.supportUnread, 1);
    await c.loadSupport();
    expect(c.supportThread!.map((m) => m.fromSupport), [false, true]);
    expect(c.supportThread!.last.text, 'תפעיל מחדש 🙂');
    c.dispose();
  });

  testWidgets('support tab: quick self-check, in-app chat with the manager, and WhatsApp', (tester) async {
    final native = FakeNative()
      ..caInstalled = true
      ..batteryUnrestricted = false;
    final server = MockClient((req) async {
      const json = {'content-type': 'application/json; charset=utf-8'};
      return switch (req.url.path) {
        '/v1/enroll' => http.Response(jsonEncode({'token': 't', 'level': 1, 'level_name': 'מחמיר', 'vpn': _vpn,
            'ca': {'pem': 'x'}}), 201, headers: json),
        '/v1/device/vpn' => http.Response(jsonEncode(_vpn), 200, headers: json),
        '/v1/device' => http.Response(jsonEncode({'name': 'Pixel', 'status': 'active', 'level': 1,
            'level_name': 'מחמיר', 'via_vpn': true, 'customer': {'name': 'דוד', 'phone': '0521234567'}}), 200, headers: json),
        '/v1/device/support' => http.Response(jsonEncode({'messages': [
            {'id': 1, 'from': 'support', 'text': 'שלום, איך אפשר לעזור?', 'ts': 1000}]}), 200, headers: json),
        _ => http.Response('{}', 200, headers: json),
      };
    });
    await tester.binding.setSurfaceSize(const Size(800, 2600));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final c = makeController(native, server);
    await tester.runAsync(() async {
      await c.start();
      await c.activate('ABCDE-FGHJK', '');
    });
    await tester.pumpWidget(MaterialApp(home: Directionality(textDirection: TextDirection.rtl,
        child: SupportScreen(controller: c))));
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 50)));
    await tester.pump();
    // אין יותר "עוזר מיידי" — התמיכה היא ישירות עם המנהל
    expect(find.text('עוזר מיידי'), findsNothing);
    // בדיקה מהירה: הסוללה מוגבלת → כפתור תיקון
    expect(find.text('אנדרואיד מגביל את FilterPhone ברקע'), findsOneWidget);
    await tester.tap(find.text('תיקון').first);
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 20)));
    expect(native.batteryRequests, 1);
    // תמיכה בוואטסאפ: מזהה המכשיר והטלפון של הלקוח בהודעה
    await tester.tap(find.text('תמיכה בוואטסאפ'));
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 20)));
    expect(native.waNumber, '972527186881');
    expect(native.waMessage, contains('מכשיר #'));
    expect(native.waMessage, contains('0521234567'));
    // השיחה עם המנהל נטענת מהשרת וההתראה נמחקת
    expect(find.text('שלום, איך אפשר לעזור?'), findsOneWidget);
    expect(native.supportNotificationClears, 1);
    await tester.pumpWidget(const SizedBox());                        // עוצר את הרענון התקופתי
    c.dispose();
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
    await tester.tap(find.text('עדכון עכשיו'));
    await tester.pump();
    expect(native.updatePageOpens, 1);
    // אחרי העדכון הטלפון כבר לא מדווח על גרסה חדשה — ההודעה נעלמת
    native.updateAvailable = null;
    await c.refresh();
    await tester.pump();
    expect(find.text('עדכון עכשיו'), findsNothing);
    c.dispose();
  });

  group('filter tab, permission window and settings', () {
    MemoryStore activeStore() => MemoryStore()
      ..data['device_token'] = 'device-token'
      ..data['ca_pem'] = 'PEM'
      ..data['vpn_config_hash'] = 'x';

    Widget rtl(Widget child) => MaterialApp(home: Directionality(textDirection: TextDirection.rtl, child: child));

    Future<void> tallScreen(WidgetTester tester) async {
      await tester.binding.setSurfaceSize(const Size(800, 2600));
      addTearDown(() => tester.binding.setSurfaceSize(null));
    }

    testWidgets('protected phone: no permissions list — only what is active for this customer', (tester) async {
      await tallScreen(tester);
      final native = FakeNative()
        ..caInstalled = true
        ..state = 'UP';
      final c = makeController(native, quietServer(), activeStore());
      await c.start();
      await tester.pumpWidget(rtl(ListenableBuilder(listenable: c, builder: (_, _) => HomeScreen(controller: c))));
      await tester.pump();
      expect(c.isProtected, isTrue);
      expect(c.attentionNeeded, isEmpty);
      expect(find.text('מה פעיל אצלך'), findsOneWidget);
      expect(find.text('סינון תמונות ואתרים — מחמיר'), findsOneWidget);
      expect(find.byType(MissingChecksList), findsNothing);
      expect(find.text('הקמה בלחיצה אחת'), findsNothing);
      expect(find.text('מצב ההגנה'), findsNothing);                // הפירוט הטכני — בהגדרות
      c.dispose();
    });

    testWidgets('a dropped permission: the filter tab asks only for what is missing', (tester) async {
      await tallScreen(tester);
      final native = FakeNative()
        ..caInstalled = true
        ..state = 'UP'
        ..batteryUnrestricted = false;
      final c = makeController(native, quietServer(), activeStore());
      await c.start();
      await tester.pumpWidget(rtl(ListenableBuilder(listenable: c, builder: (_, _) => HomeScreen(controller: c))));
      await tester.pump();
      expect(c.attentionNeeded, [Check.battery]);
      expect(find.text('עוד דבר אחד וסיימנו'), findsOneWidget);
      expect(find.text('פעולה ברקע (סוללה)'), findsOneWidget);
      expect(find.text('תעודת סינון'), findsNothing);
      expect(find.text('חיבור לסינון (VPN)'), findsNothing);
      await tester.tap(find.descendant(of: find.byType(MissingChecksList), matching: find.text('אישור')));
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 20)));
      expect(native.batteryRequests, 1);
      c.dispose();
    });

    testWidgets('the permission window asks for what dropped and closes by itself once it is back', (tester) async {
      // הגדרות ה-VPN כבר שמורות וזהות לשרת — הסנכרון לא מחבר מחדש באמצע הבדיקה
      final native = FakeNative()
        ..caInstalled = true
        ..state = 'UP'
        ..savedVpn = Map<String, Object?>.from(_vpn);
      final store = activeStore()..data['vpn_config_hash'] = base64Encode(utf8.encode(jsonEncode(_vpn)));
      final c = makeController(native, quietServer(), store);
      await c.start();
      await tester.pumpWidget(rtl(Builder(
        builder: (context) => Scaffold(
          body: Center(
            child: FilledButton(
              onPressed: () => showModalBottomSheet<void>(
                  context: context, builder: (_) => MissingPermissionSheet(controller: c)),
              child: const Text('פתיחה'),
            ),
          ),
        ),
      )));
      // ה-VPN נותק (למשל ניקוי נתונים) — החלון דורש בדיוק את זה
      native.state = 'DOWN';
      await c.refresh();
      expect(c.attentionNeeded, [Check.vpn]);
      await tester.tap(find.text('פתיחה'));
      await tester.pumpAndSettle();
      expect(find.text('FilterPhone צריך אישור כדי להמשיך להגן'), findsOneWidget);
      expect(find.text('חיבור לסינון (VPN)'), findsOneWidget);
      expect(find.text('פעולה ברקע (סוללה)'), findsNothing);
      native.state = 'UP';
      await c.refresh();
      await tester.pumpAndSettle();
      expect(find.text('FilterPhone צריך אישור כדי להמשיך להגן'), findsNothing);
      c.dispose();
    });

    testWidgets('profile: no duplicate protection cards, and a settings button', (tester) async {
      await tallScreen(tester);
      final native = FakeNative()
        ..caInstalled = true
        ..state = 'UP';
      final c = makeController(native, quietServer(), activeStore());
      await c.start();
      await tester.pumpWidget(rtl(ListenableBuilder(listenable: c, builder: (_, _) => ProfileScreen(controller: c))));
      await tester.pump();
      expect(find.text('מה פעיל בהגנה'), findsNothing);
      expect(find.text('חסימות פעילות'), findsNothing);
      await tester.tap(find.text('הגדרות'));
      await tester.pumpAndSettle();
      expect(find.text('מראה'), findsOneWidget);
      expect(find.text('הצעות ייעול'), findsOneWidget);
      c.dispose();
    });

    testWidgets('settings: theme and text size are kept, updates install in the app, suggestions reach the admin',
        (tester) async {
      await tallScreen(tester);
      final sent = <Map<String, Object?>>[];
      final server = MockClient((req) async {
        const json = {'content-type': 'application/json; charset=utf-8'};
        switch (req.url.path) {
          case '/v1/device':
            return http.Response(jsonEncode({'name': 'Pixel', 'status': 'active', 'level': 1, 'level_name': 'מחמיר',
                'via_vpn': true}), 200, headers: json);
          case '/v1/device/vpn':
            return http.Response(jsonEncode(_vpn), 200, headers: json);
          case '/v1/device/support' when req.method == 'POST':
            final body = Map<String, Object?>.from(jsonDecode(req.body) as Map);
            sent.add(body);
            return http.Response(jsonEncode({'id': 1, 'from': 'customer', 'text': body['text'], 'ts': 1000}), 201,
                headers: json);
          case '/v1/device/support':
            return http.Response(jsonEncode({'messages': []}), 200, headers: json);
        }
        return http.Response('{}', 200, headers: json);
      });
      final native = FakeNative()
        ..caInstalled = true
        ..state = 'UP'
        ..updateAvailable = '1.0.9';
      final store = activeStore();
      final c = makeController(native, server, store);
      await c.start();
      await tester.pumpWidget(rtl(SettingsScreen(controller: c)));
      await tester.pump();
      // מראה
      await tester.tap(find.text('כהה'));
      await tester.pump();
      expect(c.themeMode, 'dark');
      await tester.tap(find.text('גדול'));
      await tester.pump();
      expect(c.textScale, 1.15);
      expect(store.data['ui_theme'], 'dark');
      expect(store.data['ui_text_scale'], '1.15');
      // עדכון מתוך האפליקציה (בלי מנהל מכשיר)
      await tester.tap(find.text('הורדה והתקנה של גרסה 1.0.9'));
      await tester.pump();
      expect(native.updatePageOpens, 1);
      // הצעת ייעול
      await tester.enterText(find.byType(TextField), 'שיהיה גם מצב לילה אוטומטי');
      await tester.tap(find.text('שליחת ההצעה'));
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 50)));
      await tester.pump();
      expect(sent.single['text'], '💡 הצעת ייעול: שיהיה גם מצב לילה אוטומטי');
      expect(Map<String, Object?>.from(sent.single['diag'] as Map)['topic'], 'suggestion');
      expect(find.text('תודה! ההצעה נשלחה למנהל 🙏'), findsOneWidget);
      c.dispose();
      // ההגדרות נשמרות בטלפון — גם אחרי פתיחה מחדש
      final again = makeController(FakeNative(), quietServer(), store);
      await again.start();
      expect(again.themeMode, 'dark');
      expect(again.textScale, 1.15);
      again.dispose();
    });

    testWidgets('admin device: the support tab shows a support-console entry with unread count', (tester) async {
      await tallScreen(tester);
      final threadsCalled = <bool>[];
      final native = FakeNative()
        ..caInstalled = true
        ..state = 'UP';
      final server = MockClient((req) async {
        const json = {'content-type': 'application/json; charset=utf-8'};
        switch (req.url.path) {
          case '/v1/device':
            return http.Response(jsonEncode({'name': 'Pixel', 'status': 'active', 'level': 1, 'level_name': 'מחמיר',
                'via_vpn': true, 'admin': {'support_unread': 2}}), 200, headers: json);
          case '/v1/device/vpn':
            return http.Response(jsonEncode(_vpn), 200, headers: json);
          case '/v1/device/admin/support':
            threadsCalled.add(true);
            return http.Response(jsonEncode({'unread': 2, 'threads': [
              {'device_id': 7, 'device_name': 'גלקסי', 'customer': 'דוד', 'last_text': 'אין אינטרנט',
               'last_from': 'customer', 'last_ts': 1000, 'unread': 1, 'status': 'active'},
            ]}), 200, headers: json);
          case '/v1/device/support':
            return http.Response(jsonEncode({'messages': []}), 200, headers: json);
        }
        return http.Response('{}', 200, headers: json);
      });
      final c = makeController(native, server, activeStore());
      await c.start();
      await tester.pumpWidget(rtl(SupportScreen(controller: c)));
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 50)));
      await tester.pump();
      expect(c.isAdminDevice, isTrue);
      expect(find.text('ניהול תמיכה'), findsOneWidget);
      expect(find.text('2 פניות ממתינות לך'), findsOneWidget);
      // פתיחת הקונסולה — רשימת השיחות נטענת מהשרת
      await tester.tap(find.text('ניהול תמיכה'));
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 50)));
      await tester.pumpAndSettle();
      expect(threadsCalled, isNotEmpty);
      expect(find.text('דוד'), findsOneWidget);
      expect(find.text('אין אינטרנט'), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
      c.dispose();
    });

    test('a suggestion needs text', () async {
      final c = makeController(FakeNative(), quietServer(), activeStore());
      await c.start();
      expect(await c.sendSuggestion('   '), 'כתבו את ההצעה קודם');
      c.dispose();
    });
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
