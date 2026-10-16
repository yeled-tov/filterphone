import 'dart:async';
import 'dart:io';

import 'package:filterphone/src/api_client.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

void main() {
  test('network errors get a precise message instead of a blanket "no internet"', () {
    String d(Object e) => ApiClient.describeNetworkError(e);
    expect(d(TimeoutException('x')), contains('(timeout)'));
    expect(d(http.ClientException("Failed host lookup: 'api.filterphone.com'")), contains('(dns)'));
    expect(d(const SocketException('Failed host lookup: x (OS Error: No address associated with hostname)')),
        contains('(dns)'));
    expect(d(const SocketException('Network is unreachable')), contains('(offline)'));
    expect(d(const HandshakeException('Handshake error in client')), contains('(tls)'));
    expect(d(http.ClientException('Connection reset by peer')), contains('(reset)'));
    expect(d(http.ClientException('Connection refused')), contains('(reset)'));
    expect(d(Exception('something else')), 'אין חיבור לשרת. בדקו את החיבור לאינטרנט ונסו שוב.');
  });

  test('a failed request surfaces the precise reason with status 0', () async {
    final api = ApiClient(
      client: MockClient((_) async => throw http.ClientException("Failed host lookup: 'api.filterphone.com'")),
      baseUrl: 'https://api.filterphone.com',
    );
    await expectLater(
      api.catalog(),
      throwsA(isA<ApiException>()
          .having((e) => e.status, 'status', 0)
          .having((e) => e.message, 'message', contains('(dns)'))),
    );
  });
}
