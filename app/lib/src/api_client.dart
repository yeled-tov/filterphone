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

// ------------------------------------------------------------------ קטלוג (טופס ההרשמה)

class LevelInfo {
  const LevelInfo({required this.level, required this.name, required this.short, required this.desc});
  final int level;
  final String name;
  final String short;
  final String desc;

  factory LevelInfo.fromJson(Map<String, Object?> j) => LevelInfo(
        level: (j['level'] as num).toInt(),
        name: j['name'] as String? ?? '',
        short: j['short'] as String? ?? '',
        desc: j['desc'] as String? ?? '',
      );
}

class CategoryInfo {
  const CategoryInfo({required this.id, required this.title, required this.desc});
  final String id;
  final String title;
  final String desc;

  factory CategoryInfo.fromJson(Map<String, Object?> j) => CategoryInfo(
        id: j['id'] as String,
        title: j['title'] as String? ?? '',
        desc: j['desc'] as String? ?? '',
      );
}

class OptionInfo {
  const OptionInfo({
    required this.id,
    required this.category,
    required this.title,
    this.desc = '',
    this.popular = false,
    this.warn = '',
  });
  final String id;
  final String category;
  final String title;
  final String desc;
  final bool popular;
  final String warn;

  factory OptionInfo.fromJson(Map<String, Object?> j) => OptionInfo(
        id: j['id'] as String,
        category: j['category'] as String? ?? '',
        title: j['title'] as String? ?? '',
        desc: j['desc'] as String? ?? '',
        popular: j['popular'] as bool? ?? false,
        warn: j['warn'] as String? ?? '',
      );
}

class Catalog {
  const Catalog({required this.levels, required this.categories, required this.options});
  final List<LevelInfo> levels;
  final List<CategoryInfo> categories;
  final List<OptionInfo> options;

  List<OptionInfo> inCategory(String id) => options.where((o) => o.category == id).toList();
  List<OptionInfo> get popular => options.where((o) => o.popular).toList();
  OptionInfo? option(String id) {
    for (final o in options) {
      if (o.id == id) return o;
    }
    return null;
  }

  LevelInfo? level(int n) {
    for (final l in levels) {
      if (l.level == n) return l;
    }
    return null;
  }

  factory Catalog.fromJson(Map<String, Object?> j) => Catalog(
        levels: _list(j['levels']).map(LevelInfo.fromJson).toList(),
        categories: _list(j['categories']).map(CategoryInfo.fromJson).toList(),
        options: _list(j['options']).map(OptionInfo.fromJson).toList(),
      );
}

List<Map<String, Object?>> _list(Object? v) =>
    (v as List? ?? const []).map((e) => Map<String, Object?>.from(e as Map)).toList();

// ------------------------------------------------------------------ הרשמה והפעלה

class RegistrationForm {
  RegistrationForm({
    this.name = '',
    this.phone = '',
    this.email = '',
    this.deviceName = '',
    this.level = 1,
    Set<String>? options,
    this.note = '',
  }) : options = options ?? <String>{};

  String name;
  String phone;
  String email;
  String deviceName;
  int level;
  final Set<String> options;
  String note;
}

class Registration {
  Registration({required this.token, required this.status, required this.message});
  final String token;
  final String status;
  final String message;

  factory Registration.fromJson(Map<String, Object?> j) => Registration(
        token: j['token'] as String,
        status: j['status'] as String? ?? 'pending',
        message: j['message'] as String? ?? '',
      );
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

/// הגדרות ה-VPN (בלי התעודה) + התעודה — כמו שהשרת מחזיר ל-/v1/device/vpn.
class VpnBundle {
  VpnBundle(this.vpn, this.caPem);
  final Map<String, Object?> vpn;
  final String? caPem;

  factory VpnBundle.fromJson(Map<String, Object?> j) {
    final vpn = Map<String, Object?>.from(j)..remove('ca');
    final ca = j['ca'];
    return VpnBundle(vpn, ca is Map ? ca['pem'] as String? : null);
  }
}

// ------------------------------------------------------------------ מצב המכשיר

class BlockInfo {
  const BlockInfo({required this.id, required this.title, required this.category});
  final String id;
  final String title;
  final String category;

  factory BlockInfo.fromJson(Map<String, Object?> j) => BlockInfo(
        id: j['id'] as String? ?? '',
        title: j['title'] as String? ?? '',
        category: j['category'] as String? ?? '',
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
    this.sitesToday = 0,
    this.message = '',
    this.requestKind,
    this.requestStatus,
    this.blocks = const [],
    this.guardPolicy = const {},
    this.appsReportNeeded = false,
  });

  final String name;
  final String status; // pending / active / disabled / rejected
  final int level;
  final String levelName;
  final bool viaVpn; // הבקשה הזו הגיעה לשרת דרך המנהרה — הוכחה שהסינון פעיל
  final int blockedToday;
  final int paintedToday;
  final int sitesToday;
  final String message;
  final String? requestKind; // join / change — הבקשה האחרונה
  final String? requestStatus; // pending / approved / rejected / cancelled
  final List<BlockInfo> blocks;
  final Map<String, Object?> guardPolicy; // blocked_apps, blocked_screens — נאכף בטלפון
  final bool appsReportNeeded;

  bool get isActive => status == 'active';
  bool get isPending => status == 'pending';
  bool get isRejected => status == 'rejected';
  bool get hasPendingChange => requestKind == 'change' && requestStatus == 'pending';
  bool get needsGuard =>
      (guardPolicy['blocked_apps'] as List? ?? const []).isNotEmpty ||
      (guardPolicy['blocked_screens'] as List? ?? const []).isNotEmpty ||
      (guardPolicy['keywords'] as Map?)?['enabled'] == true;

  factory DeviceStatus.fromJson(Map<String, Object?> j) {
    final today = Map<String, Object?>.from(j['today'] as Map? ?? const {});
    final req = j['request'] is Map ? Map<String, Object?>.from(j['request'] as Map) : null;
    return DeviceStatus(
      name: j['name'] as String? ?? '',
      status: j['status'] as String? ?? 'active',
      level: (j['level'] as num?)?.toInt() ?? 1,
      levelName: j['level_name'] as String? ?? '',
      viaVpn: j['via_vpn'] as bool? ?? false,
      blockedToday: (today['blocked'] as num?)?.toInt() ?? 0,
      paintedToday: (today['painted'] as num?)?.toInt() ?? 0,
      sitesToday: (today['sites'] as num?)?.toInt() ?? 0,
      message: j['message'] as String? ?? '',
      requestKind: req?['kind'] as String?,
      requestStatus: req?['status'] as String?,
      blocks: _list(j['blocks']).map(BlockInfo.fromJson).toList(),
      guardPolicy: Map<String, Object?>.from(j['device_policy'] as Map? ?? const {}),
      appsReportNeeded: j['apps_report_needed'] as bool? ?? false,
    );
  }
}

// ------------------------------------------------------------------ הלקוח

class ApiClient {
  ApiClient({http.Client? client, String? baseUrl})
      : _http = client ?? http.Client(),
        _base = baseUrl ?? apiBaseUrl;

  final http.Client _http;
  final String _base;
  static const _timeout = Duration(seconds: 15);

  /// כתובת השרת — גם למגן שבטלפון, שמרענן את רשימת החסימות בעצמו.
  String get baseUrl => _base;

  Future<Catalog> catalog() async => Catalog.fromJson(await _send('GET', '/v1/catalog'));

  Future<Registration> register({
    required RegistrationForm form,
    required String publicKey,
    required String platform,
    required String appVersion,
  }) async =>
      Registration.fromJson(await _send('POST', '/v1/register', body: {
        'name': form.name.trim(),
        'phone': form.phone.trim(),
        'email': form.email.trim(),
        'level': form.level,
        'options': form.options.toList()..sort(),
        'note': form.note.trim(),
        'device_name': form.deviceName.trim(),
        'public_key': publicKey,
        'platform': platform,
        'app_version': appVersion,
      }));

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

  Future<VpnBundle> vpnConfig(String token) async =>
      VpnBundle.fromJson(await _send('GET', '/v1/device/vpn', token: token));

  /// מפתח VPN חדש (הקודם אבד בטלפון) — אותה כתובת ואותה מדיניות.
  Future<VpnBundle> rekey(String token, String publicKey) async =>
      VpnBundle.fromJson(await _send('POST', '/v1/device/rekey', token: token, body: {'public_key': publicKey}));

  Future<void> requestChange(String token, {int? level, List<String>? options, String note = ''}) =>
      _send('POST', '/v1/device/requests', token: token, body: {
        'level': level,
        'options': options,
        'note': note,
      });

  Future<void> report(String token, {List<Map<String, Object?>>? apps, Map<String, Object?>? guard}) =>
      _send('POST', '/v1/device/report', token: token, body: {'apps': ?apps, 'guard': ?guard});

  Future<void> cancel(String token) => _send('DELETE', '/v1/device', token: token);

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
      res = await switch (method) {
        'POST' => _http.post(uri, headers: headers, body: jsonEncode(body)),
        'DELETE' => _http.delete(uri, headers: headers),
        _ => _http.get(uri, headers: headers),
      }
          .timeout(_timeout);
    } on Exception {
      throw ApiException(0, 'אין חיבור לשרת. בדקו את החיבור לאינטרנט ונסו שוב.');
    }
    Object? decoded;
    try {
      decoded = res.bodyBytes.isEmpty ? <String, Object?>{} : jsonDecode(utf8.decode(res.bodyBytes));
    } on FormatException {
      decoded = null;
    }
    if (res.statusCode >= 200 && res.statusCode < 300) {
      return decoded is Map ? Map<String, Object?>.from(decoded) : <String, Object?>{};
    }
    final message = decoded is Map && decoded['error'] is String
        ? decoded['error'] as String
        : 'שגיאה בשרת (${res.statusCode})';
    throw ApiException(res.statusCode, message);
  }
}
