import 'dart:io';

import 'package:filterphone/src/kosher_roots.dart';
import 'package:filterphone/src/native_bridge.dart';
import 'package:flutter_test/flutter_test.dart';

final _block = RegExp(r'-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----');

void main() {
  test('תעודות נטפרי ב-Dart זהות לאלה שבאנדרואיד', () {
    final android = File('android/app/src/main/res/raw/kosher_netfree.pem').readAsStringSync();
    final a = _block.allMatches(android).map((m) => m.group(0)).toList();
    final d = _block.allMatches(kosherRootsPem).map((m) => m.group(0)).toList();
    expect(a.length, 10);
    expect(d, a);
  });

  test('ברשת נטפרי ה-VPN מושהה בכוונה — נחשב מסונן', () {
    final s = VpnStatus.fromMap({'state': 'DOWN', 'kosherNet': 'netfree'});
    expect(s.isUp, isFalse);
    expect(s.protected, isTrue);
    expect(s.kosherLabel, 'נטפרי');
    final off = VpnStatus.fromMap({'state': 'DOWN'});
    expect(off.protected, isFalse);
    expect(off.kosherLabel, isNull);
  });
}
