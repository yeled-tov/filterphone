
import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'config.dart';

class ApiException implements Exception {
  ApiException(this.status, this.message);
  final int status;
  final String message;

  bool get isUnauthorized => status == 401;

  @override
  String toString() => message;
}

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

/// תשובת "שחזור לפי מספר טלפון": אותו טלפון → [enrollment]; טלפון אחר → רשימת המכשירים בחשבון.
class RestoreLookup {
  RestoreLookup({required this.status, this.enrollment, this.devices = const [], this.message = '', this.customerName = ''});
  final String status; // active / needs_choice / pending
  final Enrollment? enrollment;
  final List<AccountDevice> devices;
  final String message;
  final String customerName;

  factory RestoreLookup.fromJson(Map<String, Object?> j) {
    final status = j['status'] as String? ?? '';
    return RestoreLookup(
      status: status,
      enrollment: status == 'active' ? Enrollment.fromJson(j) : null,
      devices: [
        for (final d in (j['devices'] as List? ?? const []))
          AccountDevice.fromJson(Map<String, Object?>.from(d as Map)),
      ],
      message: j['message'] as String? ?? '',
      customerName: j['customer_name'] as String? ?? '',
    );
  }
}

class AccountDevice {
  AccountDevice({required this.id, required this.name, required this.status, this.lastSeen});
  final int id;
  final String name;
  final String status;
  final DateTime? lastSeen;

  factory AccountDevice.fromJson(Map<String, Object?> j) => AccountDevice(
        id: (j['id'] as num).toInt(),
        name: j['name'] as String? ?? '',
        status: j['status'] as String? ?? '',
        lastSeen: (j['last_seen_at'] as num?) == null
            ? null
            : DateTime.fromMillisecondsSinceEpoch((j['last_seen_at'] as num).toInt() * 1000),
      );
}

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

class CustomerInfo {
  const CustomerInfo({
    this.id,
    this.name = '',
    this.phone = '',
    this.email = '',
    this.payment = '',
    this.paidUntil,
  });

  final int? id;
  final String name;
  final String phone;
  final String email;
  final String payment;
  final int? paidUntil;

  factory CustomerInfo.fromJson(Map<String, Object?> j) => CustomerInfo(
        id: (j['id'] as num?)?.toInt(),
        name: j['name'] as String? ?? '',
        phone: j['phone'] as String? ?? '',
        email: j['email'] as String? ?? '',
        payment: j['payment'] as String? ?? '',
        paidUntil: (j['paid_until'] as num?)?.toInt(),
      );
}

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
    required this.id,
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
    this.customer,
    this.storeApps = const [],
    this.storeCategories = kStoreCategories,
    this.supportUnread = 0,
    this.supportWhatsApp = '',
  });

  final int id;
  final String name;
  final String status;
  final int level;
  final String levelName;
  final bool viaVpn;
  final int blockedToday;
  final int paintedToday;
  final int sitesToday;
  final String message;
  final String? requestKind;
  final String? requestStatus;
  final List<BlockInfo> blocks;
  final Map<String, Object?> guardPolicy;
  final bool appsReportNeeded;
  final CustomerInfo? customer;

  /// קטלוג החנות מהשרת (אפליקציות שהמנהל אישר).
  final List<StoreApp> storeApps;

  /// סדר הטאבים (הקטגוריות) בחנות — מהשרת; שרת ישן → ברירת המחדל.
  final List<String> storeCategories;

  /// תשובות מהתמיכה שעוד לא נקראו, ומספר וואטסאפ לפנייה ישירה (אם המנהל הגדיר).
  final int supportUnread;
  final String supportWhatsApp;

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
    final customer =
        j['customer'] is Map ? CustomerInfo.fromJson(Map<String, Object?>.from(j['customer'] as Map)) : null;
    final support = Map<String, Object?>.from(j['support'] as Map? ?? const {});

    return DeviceStatus(
      id: (j['device_id'] as num?)?.toInt() ?? 0,
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
      customer: customer,
      storeApps: [
        for (final a in (j['managed_apps'] as List? ?? const []))
          if (a is Map) StoreApp.fromJson(Map<String, Object?>.from(a)),
      ],
      storeCategories: [
        for (final c in (j['store_categories'] as List? ?? kStoreCategories))
          if (c is String && c.isNotEmpty) c,
      ],
      supportUnread: (support['unread'] as num?)?.toInt() ?? 0,
      supportWhatsApp: support['whatsapp'] as String? ?? '',
    );
  }
}

/// הודעה בשיחה עם נציג (נשמרת בשרת; המנהל עונה מהממשק או מהוואטסאפ שלו).
class SupportMessage {
  SupportMessage({required this.id, required this.fromSupport, required this.text, required this.time});

  final int id;
  final bool fromSupport;
  final String text;
  final DateTime time;

  factory SupportMessage.fromJson(Map<String, Object?> j) => SupportMessage(
        id: (j['id'] as num?)?.toInt() ?? 0,
        fromSupport: j['from'] == 'support',
        text: j['text'] as String? ?? '',
        time: DateTime.fromMillisecondsSinceEpoch(((j['ts'] as num?)?.toInt() ?? 0) * 1000),
      );
}

/// אפליקציה בחנות של FilterPhone. עם [raw] (url + sha256 + version_code) — ניתנת להתקנה ישירה
/// דרך מנהל המכשיר; בלי — בקשה מהמנהל / התקנה מהחנות הרגילה.
/// מה ש-Google Play אומר על אפליקציה: update / maybe / current / unknown / pending.
class AppUpdateInfo {
  const AppUpdateInfo(this.status, {this.playVersion, this.playUpdated});

  factory AppUpdateInfo.fromJson(Map<String, Object?> j) => AppUpdateInfo(
        j['status'] as String? ?? 'unknown',
        playVersion: j['play_version'] as String?,
        playUpdated: (j['play_updated'] as num?)?.toInt(),
      );

  final String status;
  final String? playVersion;

  /// מתי עודכנה ב-Play (שניות מאז 1970).
  final int? playUpdated;

  bool get needsUpdate => status == 'update' || status == 'maybe';
}

/// הקטגוריות (הטאבים בחנות) בסדר קבוע — כשהשרת לא שלח סדר משלו.
const kStoreCategories = <String>[
  'תקשורת', 'בנקים ותשלומים', 'בריאות', 'תחבורה', 'רכב חשמלי', 'קניות ומשלוחים', 'יהדות',
  'חדשות ופורומים', 'מדיה', 'כלים', 'ממשלה ושירותים', 'עבודה ולימודים',
];

class StoreApp {
  StoreApp({
    required this.package,
    required this.label,
    this.category = '',
    this.note = '',
    this.description = '',
    this.iconUrl = '',
    this.playUrl = '',
    this.playTitles = const [],
    this.screenshots = const [],
    this.versionCode,
    this.versionName = '',
    this.addedAt,
    this.updatedAt,
    this.raw = const {},
  });

  final String package;
  final String label;
  final String category;
  final String note;
  final String description;
  final String iconUrl;
  final String playUrl;

  /// השם ב-Google Play (עברית/אנגלית) — כדי שהמגן יזהה את העמוד שלה בחלון ההתקנה.
  final List<String> playTitles;
  final List<String> screenshots;
  final int? versionCode;
  final String versionName;
  final DateTime? addedAt;
  final DateTime? updatedAt;
  final Map<String, Object?> raw;

  /// התקנה ישירה (APK שמתארח אצלנו, דרך מנהל המכשיר).
  bool get directInstall => versionCode != null && (raw['url'] as String? ?? '').startsWith('https://');

  /// התקנה דרך Google Play (מתווך): אין APK אצלנו אבל יש קישור Play.
  bool get viaPlay => !directInstall && playUrl.startsWith('https://');

  static DateTime? _t(Object? v) =>
      v is num ? DateTime.fromMillisecondsSinceEpoch(v.toInt() * 1000) : null;

  factory StoreApp.fromJson(Map<String, Object?> j) => StoreApp(
        package: j['package'] as String? ?? '',
        label: (j['label'] as String?)?.isNotEmpty == true ? j['label'] as String : (j['package'] as String? ?? ''),
        category: j['category'] as String? ?? '',
        note: j['note'] as String? ?? '',
        description: j['description'] as String? ?? '',
        iconUrl: j['icon_url'] as String? ?? '',
        playUrl: j['play_url'] as String? ?? '',
        playTitles: [for (final t in (j['play_titles'] as List? ?? const [])) if (t is String) t],
        screenshots: [for (final s in (j['screenshots'] as List? ?? const [])) if (s is String) s],
        versionCode: (j['version_code'] as num?)?.toInt(),
        versionName: j['version_name'] as String? ?? '',
        addedAt: _t(j['added_at']),
        updatedAt: _t(j['updated_at']),
        raw: j,
      );
}

class ApiClient {
  /// הודעה מדויקת לפי סוג התקלה ברשת (במקום "אין אינטרנט" לכל דבר) — כדי שהמשתמש, התמיכה והאבחון
  /// יידעו איפה זה נתקע. הקוד בסוגריים (dns / timeout / tls / reset / offline) עוזר לתמיכה.
  static String describeNetworkError(Object e) {
    final s = e.toString().toLowerCase();
    if (e is TimeoutException || s.contains('timed out') || s.contains('timeout')) {
      return 'השרת לא ענה בזמן — כנראה האינטרנט איטי או נתקע. נסו שוב בעוד רגע. (timeout)';
    }
    if (s.contains('failed host lookup') || s.contains('no address associated') || s.contains('nodename nor servname')) {
      return 'הטלפון לא מצליח לאתר את השרת (DNS). בדקו שיש אינטרנט ונסו שוב בעוד רגע. (dns)';
    }
    if (s.contains('network is unreachable') || s.contains('no route to host')) {
      return 'אין כרגע חיבור לאינטרנט בטלפון. התחברו ל-Wi-Fi או לנתונים ונסו שוב. (offline)';
    }
    if (s.contains('handshake') || s.contains('certificate') || s.contains('tls')) {
      return 'החיבור המאובטח לשרת נכשל. בדקו שהתאריך והשעה בטלפון נכונים ונסו שוב. (tls)';
    }
    if (s.contains('connection refused') || s.contains('connection reset') || s.contains('connection closed') ||
        s.contains('broken pipe')) {
      return 'החיבור לשרת נקטע. נסו שוב בעוד רגע. (reset)';
    }
    return 'אין חיבור לשרת. בדקו את החיבור לאינטרנט ונסו שוב.';
  }

  ApiClient({http.Client? client, String? baseUrl})
      : _http = client ?? http.Client(),
        _base = baseUrl ?? apiBaseUrl;

  final http.Client _http;
  final String _base;
  static const _timeout = Duration(seconds: 15);

  String get baseUrl => _base;

  Future<Catalog> catalog() async => Catalog.fromJson(await _send('GET', '/v1/catalog'));

  Future<Registration> register({
    required RegistrationForm form,
    required String publicKey,
    required String platform,
    required String appVersion,
    String deviceUid = '',
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
        if (deviceUid.isNotEmpty) 'device_uid': deviceUid,
      }));

  Future<Enrollment> enroll({
    required String code,
    required String publicKey,
    required String name,
    required String platform,
    required String appVersion,
    String deviceUid = '',
  }) async {
    final j = await _send('POST', '/v1/enroll', body: {
      'code': code,
      'public_key': publicKey,
      'name': name,
      'platform': platform,
      'app_version': appVersion,
      if (deviceUid.isNotEmpty) 'device_uid': deviceUid,
    });
    return Enrollment.fromJson(j);
  }

  /// האם הטלפון הזה (לפי מזהה אנדרואיד) כבר מוכר בשרת — פתיחה ראשונה אחרי התקנה מחדש.
  /// מחזיר: known, ו-status (restorable / pending / admin_disabled). בלי פרטים אישיים.
  Future<Map<String, Object?>> recognize(String deviceUid) async =>
      Map<String, Object?>.from(await _send('POST', '/v1/recognize', body: {'device_uid': deviceUid}) as Map);

  /// שחזור לפי מספר טלפון (אחרי הסרה והתקנה, איפוס או טלפון חדש).
  Future<RestoreLookup> restore({
    required String phone,
    required String deviceUid,
    required String publicKey,
    required String name,
    required String platform,
    required String appVersion,
  }) async =>
      RestoreLookup.fromJson(await _send('POST', '/v1/restore', body: {
        'phone': phone.trim(),
        'device_uid': deviceUid,
        'public_key': publicKey,
        'name': name,
        'platform': platform,
        'app_version': appVersion,
      }));

  /// "העברת ההגנה לטלפון הזה" (swap) או "מכשיר נוסף" (additional) — לאישור המנהל.
  Future<Registration> restoreRequest({
    required String phone,
    required String deviceUid,
    required String publicKey,
    required String name,
    required String platform,
    required String appVersion,
    required String mode,
    int? oldDeviceId,
  }) async =>
      Registration.fromJson(await _send('POST', '/v1/restore/request', body: {
        'phone': phone.trim(),
        'device_uid': deviceUid,
        'public_key': publicKey,
        'name': name,
        'platform': platform,
        'app_version': appVersion,
        'mode': mode,
        'old_device_id': oldDeviceId,
      }));

  /// הפעלה בקוד החלפת מכשיר: הטלפון החדש יורש את הלקוח, הרמה והמדיניות מהישן.
  Future<Enrollment> transfer({
    required String token,
    required String publicKey,
    required String name,
    required String platform,
    required String appVersion,
    String deviceUid = '',
  }) async {
    final j = await _send('POST', '/v1/transfer', body: {
      'token': token,
      'public_key': publicKey,
      'name': name,
      'platform': platform,
      'app_version': appVersion,
      if (deviceUid.isNotEmpty) 'device_uid': deviceUid,
    });
    return Enrollment.fromJson(j);
  }

  Future<DeviceStatus> device(String token) async =>
      DeviceStatus.fromJson(await _send('GET', '/v1/device', token: token));

  Future<VpnBundle> vpnConfig(String token) async =>
      VpnBundle.fromJson(await _send('GET', '/v1/device/vpn', token: token));

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

  /// טאב העדכונים: לכל אפליקציה בטלפון — האם יש ב-Google Play גרסה חדשה.
  Future<Map<String, AppUpdateInfo>> appUpdates(String token, List<Map<String, Object?>> apps) async {
    final j = await _send('POST', '/v1/device/app-updates', token: token, body: {'apps': apps});
    final out = <String, AppUpdateInfo>{};
    final m = j['apps'];
    if (m is Map) {
      for (final e in m.entries) {
        if (e.key is String && e.value is Map) {
          out[e.key as String] = AppUpdateInfo.fromJson(Map<String, Object?>.from(e.value as Map));
        }
      }
    }
    return out;
  }

  /// השיחה עם הנציג — הודעות אחרי [after] (0 = הכול). מסמן את התשובות כנקראו.
  Future<List<SupportMessage>> supportMessages(String token, {int after = 0}) async {
    final j = await _send('GET', '/v1/device/support?after=$after', token: token);
    return [
      for (final m in (j['messages'] as List? ?? const []))
        if (m is Map) SupportMessage.fromJson(Map<String, Object?>.from(m)),
    ];
  }

  /// הודעה לנציג, עם מצב טכני קצר של הטלפון (בלי שום תוכן אישי).
  Future<SupportMessage> sendSupport(String token, String text, Map<String, Object?> diag) async =>
      SupportMessage.fromJson(await _send('POST', '/v1/device/support', token: token, body: {
        'text': text,
        'diag': diag,
      }));

  Future<Map<String, Object?>> _send(
    String method,
    String path, {
    Map<String, Object?>? body,
    String? token,
  }) async {
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
      }.timeout(_timeout);
    } on Exception catch (e) {
      throw ApiException(0, describeNetworkError(e));
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
