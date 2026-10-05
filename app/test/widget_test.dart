import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:filterphone/main.dart';
import 'package:filterphone/src/api_client.dart';
import 'package:filterphone/src/app_controller.dart';
import 'package:filterphone/src/native_bridge.dart';
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

  @override
  Future<Map<String, Object?>> deviceInfo() async =>
      {'model': 'Pixel 9', 'manufacturer': 'Google', 'sdk': 36, 'appVersion': '1.0.0'};
  @override
  Future<String> generateKeyPair() async => base64Encode(List.filled(32, 7));
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

  @override
  Future<VpnStatus> vpnStatus() async =>
      VpnStatus(state: state, permission: permission, hasConfig: savedVpn != null, alwaysOn: alwaysOn);
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
      if (path == '/v1/device') {
        expect(req.headers['authorization'], 'Bearer device-token');
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
      return http.Response('not found', 404);
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
