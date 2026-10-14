/// העוזר האוטומטי בלשונית התמיכה. עונה מיד — גם בלי אינטרנט (למשל כשה-VPN נפל) — לפי מה
/// שהמשתמש כתב ולפי המצב האמיתי של הטלפון כרגע, ומציע פעולה שמתקנת. מה שלא נפתר כאן עובר לנציג.
/// טהור (בלי Flutter) — נבדק ביחידה.
library;

/// פעולה שהעוזר מציע כפתור.
enum BotAction { reconnectVpn, openGuard, battery, setup, changeRequest, store, update, human }

/// מצב הטלפון שהעוזר מסתכל עליו.
class BotContext {
  const BotContext({
    this.active = true,
    this.vpnUp = false,
    this.guardNeeded = false,
    this.guardOn = false,
    this.deviceOwner = false,
    this.batteryOk = true,
    this.caInstalled = true,
    this.paused = false,
    this.levelName = '',
    this.updateAvailable = false,
  });

  final bool active;
  final bool vpnUp;
  final bool guardNeeded;
  final bool guardOn;
  final bool deviceOwner;
  final bool batteryOk;
  final bool caInstalled;
  final bool paused;
  final String levelName;
  final bool updateAvailable;
}

class BotReply {
  const BotReply(this.text, {this.actions = const [], this.topic = ''});
  final String text;
  final List<BotAction> actions;

  /// נושא הפנייה — מצורף לנציג אם הלקוח עובר אליו (כדי שיבין מה כבר נבדק).
  final String topic;
}

class _Topic {
  const _Topic(this.id, this.words, this.answer);
  final String id;
  final List<String> words;
  final BotReply Function(BotContext c) answer;
}

class SupportBot {
  SupportBot._();

  /// הצעות מהירות מתחת לצ'אט.
  static const quick = ['האינטרנט לא עובד', 'אתר נחסם בטעות', 'איך מתקינים אפליקציה?', 'אפליקציה אפורה', 'לדבר עם נציג'];

  static String _norm(String s) =>
      ' ${s.toLowerCase().replaceAll(RegExp(r'''[?!.,;:()"'״׳\-–—]'''), ' ').replaceAll(RegExp(r'\s+'), ' ').trim()} ';

  static bool _has(String text, List<String> words) => words.any((w) => text.contains(w));

  static BotReply answer(String input, BotContext c) {
    final t = _norm(input);
    if (t.trim().isEmpty) return const BotReply('כתבו במה אפשר לעזור 🙂');
    for (final topic in _topics) {
      if (_has(t, topic.words)) {
        final r = topic.answer(c);
        return BotReply(r.text, actions: r.actions, topic: topic.id);
      }
    }
    return const BotReply(
      'לא בטוח שהבנתי. אפשר לבחור נושא מההצעות, לכתוב במילים אחרות — או לשלוח את השאלה לנציג.',
      actions: [BotAction.human],
      topic: 'other',
    );
  }

  /// הסדר חשוב: נושא ספציפי לפני כללי (למשל "אפליקציה אפורה" לפני "אפליקציה").
  static final List<_Topic> _topics = [
    _Topic('human', ['נציג', 'בן אדם', 'אדם אמיתי', 'לדבר עם', 'מישהו אמיתי', ' human ', ' agent '], (c) => const BotReply(
          'בשמחה. עוברים לשיחה עם נציג — כתבו שם את השאלה, והתשובה תגיע לכאן (ותקבלו התראה).',
          actions: [BotAction.human],
        )),
    _Topic('pause', ['השהיה', 'להשהות', 'להסיר', 'הסרה', 'לבטל את', 'לכבות את', 'uninstall', 'למחוק את'], (c) => const BotReply(
          'השהיה של ההגנה או הסרה — רק באישור המנהל. כתבו לנציג את הסיבה ולכמה זמן, והוא יאשר מרחוק. '
          'אחרי אישור יופיעו כאן הכפתורים "החזרת ההגנה" ו"הסרת הכול".',
          actions: [BotAction.human],
        )),
    _Topic('new_phone', ['טלפון חדש', 'מכשיר חדש', 'להעביר', 'החלפת מכשיר', 'החלפתי', 'איפוס', 'פירמוט', 'פרמוט'], (c) => const BotReply(
          'בטלפון החדש (או אחרי איפוס): מתקינים FilterPhone ← "כבר יש לי חשבון" ← מספר הטלפון שלכם. '
          'אם זה אותו טלפון — ההגנה חוזרת לבד. אם זה טלפון אחר — המנהל מאשר את ההעברה.',
        )),
    _Topic('grey_app', ['אפור', 'אפורה', 'חסום על ידי', 'מנהל הסינון', 'נעלמה', 'נעלם לי', 'גוגל פליי', 'google play', 'פליי סטור'],
        (c) => const BotReply(
              'אפליקציה אפורה = חסומה לפי ההגדרות שלכם (למשל חנות Google Play). שירותי Google עצמם ממשיכים לעבוד. '
              'צריכים אותה? שלחו בקשת שינוי למנהל, או התקינו אפליקציה מאושרת מלשונית "אפליקציות".',
              actions: [BotAction.changeRequest, BotAction.store],
            )),
    _Topic('youtube', ['יוטיוב', 'youtube', 'פילטר טיוב', 'פילטרטיוב', 'filtertube'], (c) => const BotReply(
          'FilterTube עובד מחוץ ל-VPN (יוטיוב חוסם כתובות של שרתים), והסינון שלו מובנה בתוכו — כך שהוא עובד גם כשהסינון פעיל. '
          'יוטיוב הרגיל נחסם או מוגבל לפי ההגדרות שלכם. אם FilterTube לא נטען — נסו לסגור ולפתוח אותו, ואז נציג.',
          actions: [BotAction.human],
        )),
    _Topic('whatsapp', ['וואטסאפ', 'ווטסאפ', 'וואצאפ', 'whatsapp', 'סטטוס', 'תמונת פרופיל', 'תמונות פרופיל'], (c) {
      final off = c.guardNeeded && !c.guardOn;
      return BotReply(
        off
            ? 'בוואטסאפ, תמונות הפרופיל והסטטוסים מוסתרים על ידי מגן FilterPhone — והוא כבוי עכשיו. לחצו "הפעלת המגן".'
            : 'בוואטסאפ, תמונות פרופיל וסטטוסים מוסתרים לפי ההגדרות שלכם. ההודעות עצמן לא נקראות ולא נשמרות — רק המסך מוסתר.',
        actions: off ? const [BotAction.openGuard] : const [],
      );
    }),
    _Topic('install', ['להתקין', 'התקנה', 'אפליקציה', 'אפליקציות', 'חנות', ' store ', 'להוריד'], (c) => BotReply(
          c.deviceOwner
              ? 'בלשונית "אפליקציות": לוחצים "התקנה" — ההתקנה שקטה ובטוחה, גם כשהתקנת אפליקציות חסומה. '
                  'אין את האפליקציה שרציתם? "בקשה" שולחת אותה למנהל לאישור.'
              : 'בלשונית "אפליקציות" רואים את האפליקציות המאושרות. "בקשה" שולחת למנהל, והוא מאשר ומוסיף.',
          actions: const [BotAction.store],
        )),
    _Topic('update', ['עדכון', 'לעדכן', 'גרסה', 'גירסה', ' update '], (c) => BotReply(
          c.updateAvailable
              ? 'יש גרסה חדשה של FilterPhone. לחצו "עדכון" כדי להתקין אותה.'
              : c.deviceOwner
                  ? 'FilterPhone מתעדכן לבד ובשקט. גם אפליקציות מהחנות מתעדכנות מלשונית "אפליקציות" ← "עדכונים".'
                  : 'FilterPhone מעודכן. כשתצא גרסה חדשה תופיע כאן הודעה.',
          actions: c.updateAvailable ? const [BotAction.update] : const [],
        )),
    _Topic('guard', ['נגישות', 'מגן', 'accessibility', 'נכבה', 'כבה לי'], (c) => BotReply(
          !c.guardNeeded
              ? 'בהגדרות שלכם המגן לא נדרש כרגע — הכול נאכף ברשת.'
              : c.guardOn
                  ? 'מגן FilterPhone פועל ✓. הוא חוסם מסכים ואפליקציות לפי ההגדרות — בלי לקרוא את מה שכתוב בהם.'
                  : 'מגן FilterPhone כבוי — לכן חלק מהחסימות לא פועלות. לחצו "הפעלת המגן" והפעילו את FilterPhone ברשימה.',
          actions: c.guardNeeded && !c.guardOn ? const [BotAction.openGuard] : const [],
        )),
    _Topic('battery', ['סוללה', 'בטריה', 'בטרייה', 'battery', 'נגמרת', 'מתרוקן'], (c) => BotReply(
          c.batteryOk
              ? 'הגדרת הסוללה תקינה ✓. החיבור המוצפן צורך מעט סוללה; אם הסוללה מתרוקנת מהר — כתבו לנציג באיזה מצב זה קורה.'
              : 'אנדרואיד מגביל את FilterPhone ברקע — זה גורם לניתוקים. לחצו "סוללה ללא הגבלה" ואשרו.',
          actions: c.batteryOk ? const [BotAction.human] : const [BotAction.battery],
        )),
    _Topic('level', ['רמה', 'לשנות רמ', 'להחמיר', 'להקל', 'שינוי הגדרות', 'לפתוח אתר', 'לפתוח את'], (c) => BotReply(
          'רמת הסינון${c.levelName.isEmpty ? '' : ' שלכם היא "${c.levelName}"'}. אפשר לבקש מהמנהל שינוי רמה, '
          'פתיחה או חסימה של אתר / אפליקציה — בבקשת שינוי.',
          actions: const [BotAction.changeRequest],
        )),
    _Topic('blocked', ['חסום', 'נחסם', 'חסימה', 'חוסם', 'blocked', 'בטעות'], (c) => BotReply(
          'אתר או תמונה נחסמים לפי הרמה${c.levelName.isEmpty ? '' : ' ("${c.levelName}")'}. אם משהו תקין נחסם בטעות — '
          'כתבו לנציג את שם האתר (למשל ynet.co.il), או בקשו פתיחה בבקשת שינוי.',
          actions: const [BotAction.changeRequest, BotAction.human],
        )),
    _Topic('images', ['תמונה', 'תמונות', 'צבוע', 'צבועה', 'מטושטש', 'לא רואים'], (c) => BotReply(
          'כל תמונה נבדקת בשרת ונחסמת או נצבעת לפי הרמה${c.levelName.isEmpty ? '' : ' ("${c.levelName}")'}. '
          'תמונה תקינה שנחסמה? כתבו לנציג באיזה אתר — זה עוזר לשפר את הסינון.',
          actions: const [BotAction.human],
        )),
    _Topic('payment', ['תשלום', 'מחיר', 'לשלם', 'מנוי', 'חשבונית', 'כסף'], (c) => const BotReply(
          'שאלות על תשלום ומנוי — אצל נציג. עוברים?',
          actions: [BotAction.human],
        )),
    _Topic('vpn', ['vpn', 'וי פי אן', 'אינטרנט', 'גלישה', 'לא נטען', 'לא נפתח', 'לא עובד', 'אין רשת', 'איטי', 'מתנתק',
          'התנתק', 'חיבור', 'סינון', 'מסנן'], (c) {
      if (!c.active) {
        return const BotReply('הטלפון עוד לא אושר על ידי המנהל — הסינון יופעל מיד אחרי האישור.');
      }
      if (c.paused) {
        return const BotReply('ההגנה מושהית כרגע באישור המנהל, ולכן אין סינון. בסוף ההשהיה היא חוזרת לבד '
            '(או מיד, בכפתור "החזרת ההגנה" במסך הראשי).');
      }
      if (!c.vpnUp) {
        return const BotReply(
          'ה-VPN מנותק כרגע — לכן אין סינון (ובטלפון מנוהל גם אין אינטרנט). לחצו "חיבור מחדש". '
          'לא עוזר? "הקמה אוטומטית" בודקת ומתקנת את כל ההגדרות.',
          actions: [BotAction.reconnectVpn, BotAction.setup, BotAction.human],
        );
      }
      if (!c.batteryOk) {
        return const BotReply(
          'ה-VPN מחובר, אבל אנדרואיד מגביל את FilterPhone ברקע — זו הסיבה הנפוצה לניתוקים. לחצו "סוללה ללא הגבלה".',
          actions: [BotAction.battery],
        );
      }
      if (!c.caInstalled) {
        return const BotReply(
          'ה-VPN מחובר, אבל תעודת הסינון לא מותקנת — לכן חלק מהאתרים מציגים שגיאת אבטחה. "הקמה אוטומטית" מתקנת את זה.',
          actions: [BotAction.setup],
        );
      }
      return const BotReply(
        'ה-VPN מחובר ותקין ✓. אם אתר מסוים לא נפתח — ייתכן שהוא חסום לפי הרמה. '
        'נסו לעבור בין Wi-Fi לנתונים סלולריים; אם זה נמשך — כתבו לנציג את שם האתר.',
        actions: [BotAction.human],
      );
    }),
    _Topic('thanks', [' תודה ', ' סבבה ', ' מעולה ', ' אחלה '], (c) => const BotReply(
          'בשמחה 🙂 אם צריך עוד משהו — אני כאן.',
        )),
    _Topic('greeting', [' שלום ', ' היי ', ' הי ', ' בוקר טוב ', ' ערב טוב ', ' hello ', ' hi '], (c) => const BotReply(
          'שלום! אני העוזר של FilterPhone. במה אפשר לעזור? אפשר לבחור נושא מההצעות או לכתוב חופשי.',
        )),
  ];
}
