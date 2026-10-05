import 'dart:convert';

import 'package:http/http.dart' as http;

import 'config.dart';

/// שגיאה מהשרת, עם הודעה בעברית שאפשר להציג למשתמש.
class ApiException implements Exception {
  ApiException(this.status, this.message);
  final int status;
  final String message;

  bool get isUnauthorized => status == 401;

  @override
  String toString() => message;
}

class Enrollment {
  Enrollment({
    required this.token,
    required this.level,
    required this.levelName,
    required this.vpn,
    required this.caPem,
  });

  final String token;
  final int level;
  final String levelName;
  final Map<String, Object?> vpn;
  final String caPem;

  factory Enrollment.fromJson(Map<String, Object?> j) => Enrollment(
        token: j['token'] as String,
        level: (j['level'] as num).toInt(),
        levelName: j['level_name'] as String? ?? '',
        vpn: Map<String, Object?>.from(j['vpn'] as Map),
        caPem: (j['ca'] as Map)['pem'] as String,
      );
}

class DeviceStatus {
  DeviceStatus({
    required this.name,
    required this.status,
    required this.level,
    required this.levelName,
    required this.viaVpn,
    required this.blockedToday,
    required this.paintedToday,
  });

  final String name;
  final String status; // active / disabled
  final int level;
  final String levelName;
  final bool viaVpn; // הבקשה הזו הגיעה לשרת דרך המנהרה — הוכחה שהסינון פעיל
  final int blockedToday;
  final int paintedToday;

  bool get isActive => status == 'active';

  factory DeviceStatus.fromJson(Map<String, Object?> j) {
    final today = Map<String, Object?>.from(j['today'] as Map? ?? const {});
    return DeviceStatus(
      name: j['name'] as String? ?? '',
      status: j['status'] as String? ?? 'active',
      level: (j['level'] as num?)?.toInt() ?? 1,
      levelName: j['level_name'] as String? ?? '',
      viaVpn: j['via_vpn'] as bool? ?? false,
      blockedToday: (today['blocked'] as num?)?.toInt() ?? 0,
      paintedToday: (today['painted'] as num?)?.toInt() ?? 0,
    );
  }
}

class ApiClient {
  ApiClient({http.Client? client, String? baseUrl})
      : _http = client ?? http.Client(),
        _base = baseUrl ?? apiBaseUrl;

  final http.Client _http;
  final String _base;
  static const _timeout = Duration(seconds: 15);

  Future<Enrollment> enroll({
    required String code,
    required String publicKey,
    required String name,
    required String platform,
    required String appVersion,
  }) async {
    final j = await _send('POST', '/v1/enroll', body: {
      'code': code,
      'public_key': publicKey,
      'name': name,
      'platform': platform,
      'app_version': appVersion,
    });
    return Enrollment.fromJson(j);
  }

  Future<DeviceStatus> device(String token) async =>
      DeviceStatus.fromJson(await _send('GET', '/v1/device', token: token));

  Future<Map<String, Object?>> vpnConfig(String token) =>
      _send('GET', '/v1/device/vpn', token: token);

  Future<Map<String, Object?>> _send(String method, String path,
      {Map<String, Object?>? body, String? token}) async {
    final uri = Uri.parse('$_base$path');
    final headers = {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
      if (token != null) 'Authorization': 'Bearer $token',
    };
    final http.Response res;
    try {
      res = await (method == 'POST'
              ? _http.post(uri, headers: headers, body: jsonEncode(body))
              : _http.get(uri, headers: headers))
          .timeout(_timeout);
    } on Exception {
      throw ApiException(0, 'אין חיבור לשרת. בדקו את החיבור לאינטרנט ונסו שוב.');
    }
    final decoded = res.body.isEmpty ? <String, Object?>{} : jsonDecode(utf8.decode(res.bodyBytes));
    if (res.statusCode >= 200 && res.statusCode < 300) {
      return Map<String, Object?>.from(decoded as Map);
    }
    final message = decoded is Map && decoded['error'] is String
        ? decoded['error'] as String
        : 'שגיאה בשרת (${res.statusCode})';
    throw ApiException(res.statusCode, message);
  }
}
