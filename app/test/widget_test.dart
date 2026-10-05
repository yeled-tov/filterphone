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

AppController makeController(FakeNative native, MockClient server, [MemoryStore? store]) =>
    AppController(
      native: native,
      api: ApiClient(client: server, baseUrl: 'https://api.test'),
      store: store ?? MemoryStore(),
    );

void main() {
  test('new install starts on the activation screen', () async {
    final c = makeController(FakeNative(), fakeServer());
    await c.start();
    expect(c.phase, Phase.activation);
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
    final store = MemoryStore()..data['device_token'] = 'device-token';
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
    final store = MemoryStore()..data['device_token'] = 'device-token';
    final c = makeController(native, fakeServer(deviceStatus: 401), store);
    await c.start();
    expect(c.deviceRemoved, isTrue);
    await c.resetDevice();
    expect(c.phase, Phase.activation);
    expect(store.data.containsKey('device_token'), isFalse);
  });

  testWidgets('activation screen renders in Hebrew, right to left', (tester) async {
    final c = makeController(FakeNative(), fakeServer());
    await c.start();
    await tester.pumpWidget(FilterPhoneApp(controller: c));
    await tester.pump();
    expect(find.text('הזינו את קוד ההפעלה שקיבלתם'), findsOneWidget);
    final dir = Directionality.of(tester.element(find.text('הפעלה')));
    expect(dir, TextDirection.rtl);
  });
}
