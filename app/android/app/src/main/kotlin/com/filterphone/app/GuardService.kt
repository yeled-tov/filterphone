package com.filterphone.app

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.graphics.Rect
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.Toast
import java.util.ArrayDeque
import java.util.concurrent.Executors

/**
 * מגן FilterPhone — שירות נגישות שאוכף בטלפון עצמו את מה שהמנהל חסם:
 *   - אפליקציה חסומה נפתחה → חזרה למסך הבית.
 *   - מסך חסום נפתח (למשל צפייה בסטטוס בוואטסאפ) → "אחורה".
 *   - תמונת פרופיל בוואטסאפ נפתחה (אם המנהל חסם) → "אחורה" (ראו WaProfileDetector).
 *
 * פרטיות: השירות רואה איזו אפליקציה / איזה מסך נפתחו. רק בוואטסאפ, ורק כשהמנהל חסם תמונות
 * פרופיל, הוא בודק גם מזהים ומיקום של רכיבי תמונה — לא קורא טקסט, הודעות או הקלדות, ולא שומר כלום.
 *
 * עמידות: עובד בלי רשת (המדיניות השמורה), אחרי אתחול / מוות של התהליך (המערכת מחברת את
 * השירות מחדש → onServiceConnected), ובודק את המסך הנוכחי מיד כשהמדיניות משתנה.
 */
class GuardService : AccessibilityService() {
    private val main = Handler(Looper.getMainLooper())
    private var lastBlockAt = 0L
    private var lastBlockPkg = ""
    private var lastToastAt = 0L
    private var lastServerCheckAt = 0L
    private var foreground: String? = null
    private var contentEvents = false
    private var scanKeywords = false
    private var keywordDetector: KeywordDetector? = null
    private var keywordVersion = -1
    private var lastScanAt = 0L
    private var waStrikes = 0
    private var waFirstStrikeAt = 0L
    private var lastAliveMark = 0L
    private var lastWaWindow: String? = null        // המסך האחרון של וואטסאפ (Activity) — לשכבת ההחרגה
    private var lastWaTabScanAt = 0L
    private var settingsOpen = false                // ההגדרות בחזית — בודקים גם שינויי תוכן (דפים שנטענים לאט)
    private var lastSettingsScanAt = 0L
    private var launcherMenuPkg = ""                // מסך הבית שבו נלחץ ארוך הסמל שלנו
    private val curtain by lazy { PlayCurtain(this) }
    private var curtainBase = -1                    // תחתית הכפתור כשעמוד ה-Play בראש — לזיהוי גלילה
    private var lastPlayScrollBackAt = 0L
    private val debug by lazy { (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0 }

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        safely("connect") {
            markAlive(force = true)
            reconfigure()
            recheckNow()
        }
        Recovery.syncSoon(this)            // לדווח לשרת שהמגן חזר
    }

    /**
     * המגן רץ באותו תהליך כמו ה-VPN: חריגה כאן הייתה מפילה את התהליך — ואיתו את המנהרה, כלומר את
     * האינטרנט בכל הטלפון. לכן כל אירוע / משימה של המגן עטופים: תקלה נרשמת, מדווחת לשרת, ונמשכים.
     */
    private inline fun safely(where: String, block: () -> Unit) {
        try {
            block()
        } catch (t: Throwable) {
            if (t is OutOfMemoryError) throw t
            onGuardError(where, t)
        }
    }

    private var lastGuardErrorAt = 0L

    private fun onGuardError(where: String, t: Throwable) {
        Log.e(TAG, "guard $where failed", t)
        val now = SystemClock.elapsedRealtime()
        if (now - lastGuardErrorAt < 60_000) return            // לא מציפים את האחסון ברצף תקלות
        lastGuardErrorAt = now
        runCatching {
            ProtectionState.inc(this, ProtectionState.GUARD_ERRORS)
            ProtectionState.setString(this, ProtectionState.GUARD_ERROR,
                "$where: ${t.javaClass.simpleName} ${t.message.orEmpty()}".take(80))
        }
    }

    /** "המגן צריך לפעול" (מכשיר רשום, בלי השהיה) — נבדק לכל היותר פעם ב-5 שניות (קריאה ל-Keystore). */
    private var wantedAt = 0L
    private var wantedValue = false

    private fun guardWanted(): Boolean {
        val now = SystemClock.elapsedRealtime()
        if (now - wantedAt > 5_000) {
            wantedValue = runCatching { AccessibilityGuard.wanted(this) }.getOrDefault(false)
            wantedAt = now
        }
        return wantedValue
    }

    /**
     * סוגי האירועים: תמיד מעבר בין מסכים ולחיצה ארוכה (על הסמל של FilterPhone במסך הבית); שינויי תוכן —
     * רק בוואטסאפ (תמונות פרופיל / עדכונים), ב-Play בזמן חלון התקנה, ובהגדרות (הגנה עצמית — הדפים
     * נטענים בהדרגה); שינויי טקסט — כשחסימת מילות חיפוש פעילה (כדי לתפוס הקלדה בשדה חיפוש).
     */
    private var lastPlayScanAt = 0L
    private var lastPlayReturnAt = 0L
    private val playReturns = ArrayDeque<Long>()

    private fun reconfigure() {
        val policy = GuardPolicy.get(this)
        val wantContent = wantsContent(foreground, policy) || (settingsOpen && guardWanted())
        scanKeywords = policy.keywords != null
        if (scanKeywords && keywordVersion != policy.version) {
            keywordDetector = KeywordDetector(policy.keywords!!.terms)
            keywordVersion = policy.version
        } else if (!scanKeywords) {
            keywordDetector = null
            keywordVersion = -1
        }
        if (serviceInfo == null) return
        val info = serviceInfo
        info.eventTypes = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED or AccessibilityEvent.TYPE_VIEW_LONG_CLICKED or
            (if (wantContent) AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED or AccessibilityEvent.TYPE_VIEW_SCROLLED else 0) or
            (if (scanKeywords) AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED else 0)
        info.flags = info.flags or AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS
        info.notificationTimeout = if (wantContent) 150 else 0
        runCatching { serviceInfo = info }
        contentEvents = wantContent
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        safely("event") { handleEvent(event) }
    }

    private fun handleEvent(event: AccessibilityEvent?) {
        if (event == null) return
        val pkg = event.packageName?.toString() ?: return
        // מצב תחזוקה (השהיה שהמנהל אישר): המגן לא חוסם עד שהתוקף פג. עדיין מסמנים "חי".
        if (AdminState.isPaused(this)) {
            if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) markAlive()
            return
        }
        val policy = GuardPolicy.get(this)
        if (event.eventType == AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED) {
            if (scanKeywords) onTyped(pkg, event)
            return
        }
        if (event.eventType == AccessibilityEvent.TYPE_VIEW_LONG_CLICKED) {
            onLongClick(pkg, event)
            return
        }
        if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            markAlive()
            trackSettings(pkg)
            if (pkg != PlayLock.PLAY && pkg != packageName && pkg != SYSTEM_UI) hideCurtain()
            if (guardSelf(pkg, event.className?.toString())) return
            if (guardCritical(pkg)) return
            endPlayWindowIfLeft(pkg)
            trackForeground(pkg, policy)
            if (guardPlay(pkg, policy, fromContent = false)) return
            if (!policy.isEmpty) onWindow(pkg, event.className?.toString(), policy)
            if (scanKeywords) checkFocusedInput(pkg)
        } else if (contentEvents) {
            if (pkg in SETTINGS_PACKAGES) {
                onSettingsContent(pkg)
                return
            }
            if (guardPlay(pkg, policy, fromContent = true)) return
            onContent(pkg, policy)
        }
    }

    // ------------------------------------------------------------ הגנה עצמית: רק FilterPhone

    /**
     * לחיצה ארוכה על הסמל של FilterPhone במסך הבית / במגירה → "אחורה" (התפריט נסגר לפני שאפשר להגיע
     * ממנו ל"מידע על האפליקציה" / "השהיית אפליקציה" / הסרה). שאר האפליקציות — כרגיל.
     */
    private fun onLongClick(pkg: String, event: AccessibilityEvent) {
        if (pkg == packageName || !guardWanted() || !isLauncher(pkg)) return
        val texts = ArrayList<String>(4)
        event.text?.forEach { t -> t?.toString()?.let(texts::add) }
        event.contentDescription?.toString()?.let(texts::add)
        if (!SelfProtect.isOurIcon(texts, appLabel)) {
            val src = runCatching { event.source }.getOrNull() ?: return
            val ours = SelfProtect.isOurIcon(listOfNotNull(src.text?.toString(), src.contentDescription?.toString()), appLabel)
            recycle(src)
            if (!ours) return
        }
        if (block(pkg, GLOBAL_ACTION_BACK, R.string.guard_self_protected, minGapMs = 300)) {
            ProtectionState.inc(this, ProtectionState.SCREENS_BLOCKED)
        }
        // התפריט לפעמים נפתח רגע אחרי האירוע — בודקים שוב ונסגר אם עדיין פתוח
        launcherMenuPkg = pkg
        main.removeCallbacks(closeLauncherMenu)
        main.postDelayed(closeLauncherMenu, 350)
        main.postDelayed(closeLauncherMenu, 900)
    }

    private val closeLauncherMenu = Runnable {
        safely("launcher-menu") {
            val root = runCatching { rootInActiveWindow }.getOrNull() ?: return@safely
            try {
                if (root.packageName?.toString() == launcherMenuPkg && SelfProtect.isLauncherMenu(collectTexts(root))) {
                    performGlobalAction(GLOBAL_ACTION_BACK)
                }
            } finally {
                recycle(root)
            }
        }
    }

    /**
     * ההגדרות בחזית? אז גם שינויי תוכן (דף "מידע על האפליקציה" ודף מנהל המכשיר נטענים בהדרגה — הכפתורים
     * מופיעים אחרי פתיחת המסך), ובדיקה חוזרת קצרה אחרי שהמסך נפתח. מקלדת / שורת המצב לא "סוגרות" את ההגדרות.
     */
    private fun trackSettings(pkg: String) {
        if (pkg == packageName || pkg == SYSTEM_UI || isIme(pkg)) return
        val open = pkg in SETTINGS_PACKAGES
        if (open && guardWanted()) {
            main.removeCallbacks(settingsRecheck)
            main.postDelayed(settingsRecheck, 400)
            main.postDelayed(settingsRecheck, 1_100)
        }
        if (open != settingsOpen) {
            settingsOpen = open
            reconfigure()
        }
    }

    private fun onSettingsContent(pkg: String) {
        val now = SystemClock.elapsedRealtime()
        if (now - lastSettingsScanAt < SCAN_EVERY_MS) {
            // רצף שינויים — בודקים שוב בסופו (שלא יפוספס המצב האחרון של הדף)
            main.removeCallbacks(settingsRecheck)
            main.postDelayed(settingsRecheck, SCAN_EVERY_MS)
            return
        }
        lastSettingsScanAt = now
        if (!guardWanted()) return
        if (!guardSelf(pkg, null)) guardCritical(pkg)
    }

    private val settingsRecheck = Runnable {
        safely("settings-recheck") {
            if (AdminState.isPaused(this) || !guardWanted()) return@safely
            val root = runCatching { rootInActiveWindow }.getOrNull() ?: return@safely
            val p = root.packageName?.toString()
            recycle(root)
            if (p != null && p in SETTINGS_PACKAGES) {
                lastSettingsScanAt = SystemClock.elapsedRealtime()
                if (!guardSelf(p, null)) guardCritical(p)
            }
        }
    }

    private var imePkg: String? = null
    private var imeAt = 0L

    private fun isIme(pkg: String): Boolean {
        val now = SystemClock.elapsedRealtime()
        if (now - imeAt > 60_000) {
            imePkg = runCatching {
                android.provider.Settings.Secure.getString(contentResolver, android.provider.Settings.Secure.DEFAULT_INPUT_METHOD)
                    ?.substringBefore('/')
            }.getOrNull()
            imeAt = now
        }
        return pkg == imePkg
    }

    /**
     * המשתמש הקליד בשדה (חיפוש, כתובת, כל שדה טקסט): אם מה שהוקלד מכיל מילה חסומה — חזרה אחורה,
     * וניקוי השדה (אם אפשר) כדי שהחיפוש לא יישאר. בלי לשמור או לשלוח את מה שהוקלד.
     */
    private fun onTyped(pkg: String, event: AccessibilityEvent) {
        if (pkg == packageName || event.isPassword) return
        val detector = keywordDetector ?: return
        val typed = event.text.joinToString(" ")
        val hit = detector.match(typed) ?: event.source?.let { src ->
            val m = if (src.isPassword) null else detector.match(src.text) ?: detector.match(src.contentDescription)
            if (m != null) clearField(src)
            recycle(src)
            m
        }
        if (hit != null) blockKeyword(pkg, hit)
    }

    /**
     * הגנה עצמית: בהגדרות הנגישות אפשר להיכנס, אבל לא לדף של מגן FilterPhone (שם מכבים אותו).
     * הדף מזוהה לפי התיאור שלנו (guard_description) — הוא מוצג רק שם. חלון "לעצור את השירות?"
     * (כשהמגן פעיל) — נסגר. בהשהיה שהמנהל אישר, או כשהמכשיר לא רשום — לא חוסמים.
     */
    private fun guardSelf(pkg: String, cls: String?): Boolean {
        if (pkg !in SETTINGS_PACKAGES || !guardWanted()) return false
        val root = runCatching { rootInActiveWindow }.getOrNull() ?: return false
        try {
            if (root.packageName?.toString() !in SETTINGS_PACKAGES) return false
            val ourPage = root.findAccessibilityNodeInfosByText(descriptionKey).also { it.forEach(::recycle) }.isNotEmpty()
            val stopDialog = !ourPage && cls?.contains("dialog", ignoreCase = true) == true &&
                GuardPolicy.isServiceEnabled(this) &&
                root.findAccessibilityNodeInfosByText(getString(R.string.guard_label)).also { it.forEach(::recycle) }.isNotEmpty()
            if (!ourPage && !stopDialog) return false
            if (block(pkg, GLOBAL_ACTION_BACK, R.string.guard_self_protected, minGapMs = 700)) {
                ProtectionState.inc(this, ProtectionState.SCREENS_BLOCKED)
            }
            return true
        } catch (e: Exception) {
            return false
        } finally {
            recycle(root)
        }
    }

    /**
     * מסך בהגדרות שבו אפשר לפגוע ב-FilterPhone עצמה — עצירה כפויה, ניקוי נתונים, הסרה, או ביטול מנהל
     * המכשיר. מקפיץ למסך הבית לפני שהמשתמש מספיק ללחוץ. (באנדרואיד 16 "אילוץ עצירה" מכבה נגישות +
     * VPN — זה החור שנסגר.) בהשהיה שהמנהל אישר כבר לא מגיעים לכאן (נבדק למעלה).
     */
    private fun guardCritical(pkg: String): Boolean {
        if (pkg !in SETTINGS_PACKAGES || !guardWanted()) return false
        val root = runCatching { rootInActiveWindow }.getOrNull() ?: return false
        try {
            if (root.packageName?.toString() !in SETTINGS_PACKAGES) return false
            val texts = collectTexts(root)
            if (!SelfProtect.isCriticalScreen(texts, appLabel)) return false
            if (block(pkg, GLOBAL_ACTION_HOME, R.string.guard_self_protected, minGapMs = 700)) {
                ProtectionState.inc(this, ProtectionState.SCREENS_BLOCKED)
            }
            return true
        } catch (e: Exception) {
            return false
        } finally {
            recycle(root)
        }
    }

    /** כל הטקסטים הגלויים בחלון (text + contentDescription), עד MAX_NODES — ל-SelfProtect. */
    private fun collectTexts(root: AccessibilityNodeInfo): List<String> {
        val out = ArrayList<String>(64)
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        var seen = 0
        while (queue.isNotEmpty() && seen < MAX_NODES) {
            val node = queue.removeFirst()
            seen++
            node.text?.toString()?.takeIf { it.isNotBlank() }?.let { out.add(it) }
            node.contentDescription?.toString()?.takeIf { it.isNotBlank() }?.let { out.add(it) }
            for (i in 0 until node.childCount) {
                runCatching { node.getChild(i) }.getOrNull()?.let { queue.add(it) }
            }
            if (node !== root) recycle(node)      // את root משחררים אצל הקורא
        }
        queue.forEach { if (it !== root) recycle(it) }
        return out
    }

    // שם האפליקציה כפי שמוצג ב-Settings (בדיוק מה שמופיע בכותרת דף פרטי האפליקציה)
    private val appLabel by lazy {
        runCatching { packageManager.getApplicationLabel(applicationInfo).toString() }.getOrDefault("FilterPhone")
    }

    /** תחילת התיאור שלנו — מספיק ייחודי, וקצר כדי לעבור גם כשהטקסט מקוצר במסך. */
    private val descriptionKey by lazy { getString(R.string.guard_description).take(32) }

    /** מסך נפתח עם טקסט שכבר מולא (למשל חזרה לתוצאות חיפוש): בודקים את שדה הקלט שבפוקוס. */
    private fun checkFocusedInput(pkg: String) {
        if (pkg == packageName) return
        val detector = keywordDetector ?: return
        val node = runCatching { findFocus(AccessibilityNodeInfo.FOCUS_INPUT) }.getOrNull() ?: return
        val hit = if (node.isPassword) null else detector.match(node.text) ?: detector.match(node.contentDescription)
        if (hit != null) clearField(node)
        recycle(node)
        if (hit != null) blockKeyword(pkg, hit)
    }

    private fun clearField(node: AccessibilityNodeInfo) {
        if (!node.isEditable) return
        runCatching {
            val args = android.os.Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, "")
            }
            node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        }
    }

    private fun blockKeyword(pkg: String, rule: String) {
        if (debug) Log.d(TAG, "keyword blocked: $rule")
        if (block(pkg, GLOBAL_ACTION_BACK, R.string.guard_keyword_blocked, minGapMs = 700)) {
            ProtectionState.inc(this, ProtectionState.KEYWORDS_BLOCKED)
        }
    }

    private fun trackForeground(pkg: String, policy: GuardPolicy.Policy) {
        // מקלדת / שורת המערכת / FilterPhone עצמה לא "מחליפות" את האפליקציה שבחזית
        if (pkg == packageName || pkg == "com.android.systemui") return
        if (pkg in GuardPolicy.untouchable(this) && !isLauncher(pkg)) return
        if (pkg != foreground) {
            foreground = pkg
            if (wantsContent(pkg, policy) != contentEvents) reconfigure()
        }
    }

    /**
     * שינויי תוכן נחוצים: בוואטסאפ (תמונות פרופיל / טאב העדכונים — מעבר בין טאבים לא פותח מסך חדש),
     * וב-Play בזמן חלון התקנה (ניווט בתוך Play).
     */
    private fun wantsContent(pkg: String?, policy: GuardPolicy.Policy): Boolean =
        policy.waProfile?.packages?.contains(pkg) == true ||
            (policy.blocksWaUpdates && pkg in WaScreens.PACKAGES) ||
            (pkg == PlayLock.PLAY && PlayLock.active(SystemClock.elapsedRealtime()) != null)

    // ------------------------------------------------------------ שלב א': חלון Play לאפליקציה אחת

    /**
     * Play חסום לכניסה, אבל החנות שלנו פתחה "חלון התקנה" לאפליקציה מסוימת: מתירים רק את העמוד שלה
     * (וחלונות הביניים של ההתקנה). ניווט החוצה — חוזרים לעמוד שלה. true = טופל (לא לחסום כרגיל).
     */
    private fun guardPlay(pkg: String, policy: GuardPolicy.Policy, fromContent: Boolean): Boolean {
        if (pkg != PlayLock.PLAY || PlayLock.PLAY !in policy.apps) return false
        val now = SystemClock.elapsedRealtime()
        val s = PlayLock.active(now) ?: run {                  // אין חלון — החסימה הרגילה
            hideCurtain()
            return false
        }
        if (fromContent && now - lastPlayScanAt < PLAY_SCAN_MS) {
            // רצף (גלילה) — בודקים שוב בסופו, שהדף הלבן יעמוד במקום הנכון
            main.removeCallbacks(playRecheck)
            main.postDelayed(playRecheck, PLAY_SCAN_MS)
            return true
        }
        lastPlayScanAt = now
        PlayLock.onVersion(ManagedApps.installedVersion(this, s.pkg), now)
        val root = runCatching { rootInActiveWindow }.getOrNull() ?: return true
        try {
            if (root.packageName?.toString() != PlayLock.PLAY) return true
            val boxes = ArrayList<PlayLock.Box>(64)
            val (texts, search) = collectOrdered(root, boxes)
            if (PlayLock.verdict(texts, search, s.titles) == PlayLock.Verdict.RETURN) {
                hideCurtain()
                returnToPlayTarget(s.pkg, now)
            } else {
                // עמוד מותר (האפליקציה שנבחרה): מכסים מתחת לכפתור ההתקנה/עדכון אם הוא במסך — גם אם שם
                // האפליקציה לא זוהה בראש (עברית / מבנה Play אחר). חלון ביניים בלי כפתור — בלי דף.
                updateCurtain(root, boxes, PlayLock.isTargetPage(texts, s.titles), now)
            }
        } catch (e: Exception) {
            Log.w(TAG, "play window check failed", e)
        } finally {
            recycle(root)
        }
        return true
    }

    private val playRecheck = Runnable {
        safely("play-recheck") {
            if (!AdminState.isPaused(this)) guardPlay(PlayLock.PLAY, GuardPolicy.get(this), fromContent = false)
        }
    }

    /**
     * "דף לבן" מתחת לכפתור ההתקנה בעמוד של האפליקציה שנבחרה — אי אפשר לראות או לגלול ל"אפליקציות דומות".
     * גללו בכל זאת (מהחלק העליון) — הדף עוקב אחרי הכפתור, והעמוד חוזר לראש.
     */
    private fun updateCurtain(root: AccessibilityNodeInfo, boxes: List<PlayLock.Box>, isTarget: Boolean, now: Long) {
        val screenH = curtain.screenHeight()
        val buttonTop = PlayLock.curtainTop(boxes, screenH, curtain.marginPx())
        // זוהה כפתור ראשי — הדף הלבן מתחתיו בדיוק (עובד בכל עמוד מותר, גם בלי זיהוי שם האפליקציה).
        // לא זוהה כפתור אך זה עמוד האפליקציה שנבחרה (נטען / גלול) — קו ברירת מחדל בטוח + גלילה לראש.
        // חלון ביניים (חשבון / אישורים) בלי כפתור — בלי דף.
        val top: Int
        if (buttonTop != null) {
            if (buttonTop > curtainBase) curtainBase = buttonTop
            if (buttonTop < curtainBase - curtain.marginPx() * 3) scrollPlayBack(root, now)
            top = buttonTop
        } else if (isTarget) {
            scrollPlayBack(root, now)
            top = PlayLock.fallbackTop(screenH)
        } else {
            hideCurtain()
            return
        }
        curtain.show(top)
        main.removeCallbacks(curtainCheck)
        main.postDelayed(curtainCheck, CURTAIN_CHECK_MS)
    }

    private fun hideCurtain() {
        if (!curtain.isShown && curtainBase < 0) return
        curtain.hide()
        curtainBase = -1
        main.removeCallbacks(curtainCheck)
    }

    /** גלילה אחת לאחור ברשימה הראשית של העמוד (לא יותר מפעם ב-400ms). */
    private fun scrollPlayBack(root: AccessibilityNodeInfo, now: Long) {
        if (now - lastPlayScrollBackAt < 400) return
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        val seen = ArrayList<AccessibilityNodeInfo>()
        queue.add(root)
        try {
            var visited = 0
            while (queue.isNotEmpty() && visited < MAX_NODES) {
                val n = queue.removeFirst()
                if (n !== root) seen += n
                visited++
                if (n.isScrollable) {
                    lastPlayScrollBackAt = now
                    n.performAction(AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD)
                    return
                }
                for (i in 0 until n.childCount) runCatching { n.getChild(i) }.getOrNull()?.let { queue.add(it) }
            }
        } finally {
            seen.forEach { recycle(it) }
            queue.forEach { if (it !== root) recycle(it) }
        }
    }

    /** כל עוד הדף מוצג: יצאו מ-Play / החלון נגמר / השהיה מאושרת — הדף יורד (גם בלי אירוע). */
    private val curtainCheck: Runnable = Runnable {
        safely("curtain-check") {
            if (!curtain.isShown) return@safely
            val now = SystemClock.elapsedRealtime()
            val root = runCatching { rootInActiveWindow }.getOrNull()
            val p = root?.packageName?.toString()
            root?.let { recycle(it) }
            if (AdminState.isPaused(this) || PlayLock.active(now) == null ||
                (p != null && p != PlayLock.PLAY && p != packageName)) {
                hideCurtain()
            } else {
                main.postDelayed(curtainCheck, CURTAIN_CHECK_MS)
            }
        }
    }

    /** חזרה לעמוד של האפליקציה שנבחרה. ניסיונות חוזרים ברצף (מישהו "נלחם") — סוגרים את החלון. */
    private fun returnToPlayTarget(target: String, now: Long) {
        if (now - lastPlayReturnAt < 1_200) return
        lastPlayReturnAt = now
        playReturns.addLast(now)
        while (playReturns.isNotEmpty() && now - playReturns.first() > 30_000) playReturns.removeFirst()
        if (playReturns.size > 4) {
            PlayLock.end()
            hideCurtain()
            playReturns.clear()
            reconfigure()
            block(PlayLock.PLAY, GLOBAL_ACTION_HOME, R.string.guard_play_window_closed, minGapMs = 0)
            return
        }
        val intent = android.content.Intent(android.content.Intent.ACTION_VIEW,
            android.net.Uri.parse("market://details?id=$target"))
            .setPackage(PlayLock.PLAY)
            .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK or android.content.Intent.FLAG_ACTIVITY_CLEAR_TOP)
        runCatching { startActivity(intent) }.onFailure { performGlobalAction(GLOBAL_ACTION_BACK) }
        if (now - lastToastAt > 3_000) {
            lastToastAt = now
            Toast.makeText(this, R.string.guard_play_only_selected, Toast.LENGTH_SHORT).show()
        }
    }

    /** יצאו מ-Play לאפליקציה אחרת / למסך הבית — חלון ההתקנה נסגר (Play חוזר להיות חסום). */
    private fun endPlayWindowIfLeft(pkg: String) {
        val s = PlayLock.session ?: return
        if (pkg == s.pkg || pkg == packageName || pkg in PlayLock.COMPANIONS) return
        if (pkg in GuardPolicy.untouchable(this) && !isLauncher(pkg)) return     // מקלדת וכו'
        PlayLock.end()
        hideCurtain()
        playReturns.clear()
        reconfigure()
    }

    /**
     * הטקסטים בחלון לפי סדר הרכיבים (מלמעלה למטה), ויש בו שדה הקלדה? — ל-PlayLock. אם [boxes] ניתן —
     * גם המיקום במסך של כל טקסט גלוי (לדף הלבן).
     */
    private fun collectOrdered(root: AccessibilityNodeInfo, boxes: MutableList<PlayLock.Box>? = null): Pair<List<String>, Boolean> {
        val out = ArrayList<String>(64)
        var editable = false
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        stack.addLast(root)
        var seen = 0
        val rect = Rect()
        while (stack.isNotEmpty() && seen < MAX_NODES) {
            val node = stack.removeLast()
            seen++
            if (node.isEditable || node.className?.toString() == "android.widget.EditText") editable = true
            val text = node.text?.toString()?.takeIf { it.isNotBlank() }
            val desc = node.contentDescription?.toString()?.takeIf { it.isNotBlank() }
            text?.let { out.add(it) }
            desc?.let { out.add(it) }
            if (boxes != null && (text != null || desc != null) && node.isVisibleToUser) {
                node.getBoundsInScreen(rect)
                text?.let { boxes.add(PlayLock.Box(it, rect.top, rect.bottom)) }
                desc?.let { boxes.add(PlayLock.Box(it, rect.top, rect.bottom)) }
            }
            for (i in node.childCount - 1 downTo 0) {
                runCatching { node.getChild(i) }.getOrNull()?.let { stack.addLast(it) }
            }
            if (node !== root) recycle(node)
        }
        stack.forEach { if (it !== root) recycle(it) }
        return out to editable
    }

    private fun isLauncher(pkg: String): Boolean =
        runCatching {
            val home = android.content.Intent(android.content.Intent.ACTION_MAIN).addCategory(android.content.Intent.CATEGORY_HOME)
            packageManager.queryIntentActivities(home, 0).any { it.activityInfo.packageName == pkg }
        }.getOrDefault(false)

    private fun onWindow(pkg: String, cls: String?, policy: GuardPolicy.Policy) {
        when (GuardPolicy.check(this, policy, pkg, cls)) {
            GuardPolicy.Block.APP -> {
                block(pkg, GLOBAL_ACTION_HOME, R.string.guard_app_blocked)
                ProtectionState.inc(this, ProtectionState.APPS_BLOCKED)
                checkServerForOpening(pkg)
                return
            }
            GuardPolicy.Block.SCREEN -> {
                block(pkg, GLOBAL_ACTION_BACK, R.string.guard_screen_blocked)
                ProtectionState.inc(this, ProtectionState.SCREENS_BLOCKED)
                return
            }
            null -> Unit
        }
        if (policy.blocksWaUpdates && pkg in WaScreens.PACKAGES && guardWaUpdates(pkg, fromContent = false)) return
        val wa = policy.waProfile ?: return
        val detector = WaProfileDetector(wa)
        if (!detector.watches(pkg)) return
        if (cls != null) lastWaWindow = cls
        if (detector.excluded(lastWaWindow)) return
        val rule = detector.matchWindow(pkg, cls) ?: scan(detector)
        if (rule != null) blockProfilePhoto(pkg, rule, cls)
    }

    private fun onContent(pkg: String, policy: GuardPolicy.Policy) {
        if (policy.blocksWaUpdates && pkg in WaScreens.PACKAGES && guardWaUpdates(pkg, fromContent = true)) return
        val wa = policy.waProfile ?: return
        val detector = WaProfileDetector(wa)
        if (!detector.watches(pkg) || detector.excluded(lastWaWindow)) return
        val now = SystemClock.elapsedRealtime()
        if (now - lastScanAt < SCAN_EVERY_MS) return       // עד ~4 סריקות בשנייה, גם בגלילה מהירה
        lastScanAt = now
        scan(detector)?.let { blockProfilePhoto(pkg, it, null) }
    }

    /**
     * טאב "עדכונים" בוואטסאפ (סטטוסים + ערוצים) נבחר, והמנהל חסם אותו → לוחצים על הטאב "צ'אטים"
     * (ואם לא נמצא — "אחורה"). קורא רק את שמות הטאבים והסימון "נבחר" — לא הודעות. true = טופל.
     */
    private fun guardWaUpdates(pkg: String, fromContent: Boolean): Boolean {
        val now = SystemClock.elapsedRealtime()
        if (fromContent && now - lastWaTabScanAt < SCAN_EVERY_MS) return false
        lastWaTabScanAt = now
        val root = runCatching { rootInActiveWindow }.getOrNull() ?: return false
        val seen = ArrayList<AccessibilityNodeInfo>()
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        try {
            if (root.packageName?.toString() !in WaScreens.PACKAGES) return false
            queue.add(root)
            var updates = false
            var chats: AccessibilityNodeInfo? = null
            var visited = 0
            while (queue.isNotEmpty() && visited < MAX_NODES) {
                val n = queue.removeFirst()
                seen += n
                visited++
                val text = n.text?.toString()
                val desc = n.contentDescription?.toString()
                if (!updates && n.isSelected) {
                    // הטאב הנבחר: השם על הרכיב עצמו, או על הכיתוב שבתוכו
                    updates = WaScreens.isUpdatesLabel(text, desc) || (0 until n.childCount).any { i ->
                        runCatching { n.getChild(i) }.getOrNull()?.let { ch ->
                            WaScreens.isUpdatesLabel(ch.text?.toString(), ch.contentDescription?.toString())
                                .also { recycle(ch) }
                        } ?: false
                    }
                }
                if (chats == null && WaScreens.isChatsTab(text, desc)) chats = n
                for (i in 0 until n.childCount) runCatching { n.getChild(i) }.getOrNull()?.let { queue.add(it) }
            }
            if (!updates) return false
            val clicked = chats?.let { clickSelfOrParent(it) } ?: false
            if (block(pkg, if (clicked) NO_GLOBAL_ACTION else GLOBAL_ACTION_BACK, R.string.guard_wa_updates_blocked,
                    minGapMs = 700)) {
                ProtectionState.inc(this, ProtectionState.SCREENS_BLOCKED)
            }
            return true
        } catch (e: Exception) {
            return false
        } finally {
            seen.forEach { recycle(it) }
            queue.forEach { recycle(it) }
        }
    }

    /** לחיצה על הרכיב, או על ההורה הלחיץ הקרוב (עד 4 רמות) — הטאב עצמו לפעמים לא "לחיץ" אלא העוטף שלו. */
    private fun clickSelfOrParent(node: AccessibilityNodeInfo): Boolean {
        var n: AccessibilityNodeInfo? = node
        val parents = ArrayList<AccessibilityNodeInfo>()
        try {
            repeat(5) {
                val cur = n ?: return false
                if (cur.isClickable) return cur.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                n = runCatching { cur.parent }.getOrNull()?.also { parents += it }
            }
            return false
        } finally {
            parents.forEach { recycle(it) }
        }
    }

    /** שכבות 2–3: מעבר מוגבל על עץ הרכיבים של החלון הפעיל (עד MAX_NODES), בלי לקרוא טקסט. */
    private fun scan(detector: WaProfileDetector): String? {
        val root = runCatching { rootInActiveWindow }.getOrNull() ?: return null
        if (!detector.watches(root.packageName?.toString())) {
            recycle(root)
            return null
        }
        val dm = resources.displayMetrics
        val screen = (dm.widthPixels.toLong() * dm.heightPixels).coerceAtLeast(1)
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        val seen = ArrayList<AccessibilityNodeInfo>()
        queue.add(root)
        val rect = Rect()
        val nodes = sequence {
            var visited = 0
            while (queue.isNotEmpty() && visited < MAX_NODES) {
                val n = queue.poll() ?: break
                seen += n
                visited++
                if (!n.isVisibleToUser) continue
                n.getBoundsInScreen(rect)
                val area = (rect.width().toLong().coerceAtLeast(0) * rect.height().toLong().coerceAtLeast(0)).toFloat() / screen
                val cls = n.className?.toString()
                val isImage = cls != null && (cls.endsWith("ImageView") || cls.endsWith("ImageButton"))
                yield(WaProfileDetector.Node(
                    viewId = n.viewIdResourceName,
                    className = cls,
                    description = if (isImage) n.contentDescription?.toString() else null,   // רק ברכיבי תמונה
                    areaFraction = area,
                ))
                for (i in 0 until n.childCount) n.getChild(i)?.let { queue.add(it) }
            }
        }
        return try {
            detector.matchNodes(nodes)
        } catch (e: Exception) {
            null
        } finally {
            seen.forEach { recycle(it) }
            queue.forEach { recycle(it) }
        }
    }

    @Suppress("DEPRECATION")
    private fun recycle(n: AccessibilityNodeInfo) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) runCatching { n.recycle() }
    }

    /**
     * יציאה מצפייה בתמונת פרופיל: "אחורה". אם אחרי כמה ניסיונות המסך עדיין שם (וואטסאפ "החזיר"
     * אותו) — מסך הבית. בלי לולאה: כל פעולה פעם אחת בחלון זמן, ואחר כך הפוגה.
     */
    private fun blockProfilePhoto(pkg: String, rule: String, cls: String?) {
        val now = SystemClock.elapsedRealtime()
        if (now - waFirstStrikeAt > 5_000) {
            waStrikes = 0
            waFirstStrikeAt = now
        }
        waStrikes++
        if (debug) Log.d(TAG, "wa-profile: rule=$rule window=${cls ?: "-"} strike=$waStrikes")
        val action = if (waStrikes >= 3) GLOBAL_ACTION_HOME else GLOBAL_ACTION_BACK
        if (block(pkg, action, R.string.guard_profile_photo_blocked, minGapMs = 700)) {
            ProtectionState.inc(this, ProtectionState.WA_PHOTOS_BLOCKED)
        }
    }

    private fun block(pkg: String, action: Int, message: Int, minGapMs: Long = 400): Boolean {
        val now = SystemClock.elapsedRealtime()
        // אפליקציה שנסגרת שולחת כמה אירועים ברצף — פעולה אחת מספיקה
        if (pkg == lastBlockPkg && now - lastBlockAt < minGapMs) return false
        lastBlockPkg = pkg
        lastBlockAt = now
        if (action != NO_GLOBAL_ACTION) performGlobalAction(action)
        if (now - lastToastAt > 3_000) {
            lastToastAt = now
            Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
        }
        return true
    }

    /**
     * פתחו אפליקציה חסומה: אולי הבוט / המנהל פתחו אותה זמנית ממש עכשיו (הטלפון מושך מדיניות רק כל
     * כמה דקות). בודקים מול השרת מיד — ואם היא פתוחה עכשיו, פותחים אותה בשבילו. פעם ב-30 שניות לכל היותר.
     */
    private fun checkServerForOpening(pkg: String) {
        val now = SystemClock.elapsedRealtime()
        if (now - lastServerCheckAt < SERVER_CHECK_GAP_MS) return
        lastServerCheckAt = now
        val app = applicationContext
        io.execute {
            val ok = runCatching { Sync.heartbeat(app) }.getOrDefault(false)
            if (!ok || !GuardPolicy.tempAllowed(GuardPolicy.get(app), pkg)) return@execute
            main.post {
                runCatching {
                    packageManager.getLaunchIntentForPackage(pkg)?.let { startActivity(it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
                }.onFailure { Log.w(TAG, "reopen after temp-open failed", it) }
            }
        }
    }

    /** המדיניות השתנתה (או השירות רק התחבר): בודקים את מה שפתוח עכשיו, בלי לחכות למעבר מסך. */
    private fun recheckNow() {
        val root = runCatching { rootInActiveWindow }.getOrNull() ?: return
        val pkg = root.packageName?.toString()
        recycle(root)
        if (pkg == null) return
        val policy = GuardPolicy.get(this)
        trackForeground(pkg, policy)
        if (!policy.isEmpty) onWindow(pkg, null, policy)
    }

    private fun markAlive(force: Boolean = false) {
        val now = SystemClock.elapsedRealtime()
        if (!force && now - lastAliveMark < 60_000) return
        lastAliveMark = now
        ProtectionState.mark(this, ProtectionState.GUARD_ALIVE)
    }

    override fun onInterrupt() = Unit

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        instance = null
        runCatching { curtain.hide() }
        Recovery.syncSoon(this)            // המגן כובה — לדווח לשרת
        // כובה בהגדרות? עם ההרשאה (הקמה) — מדליקים מחדש מיד (לא בהשהיה שהמנהל אישר)
        val app = applicationContext
        // Handler נפרד: onDestroy מנקה את ה-main של השירות מיד אחרי onUnbind
        Handler(Looper.getMainLooper()).postDelayed({
            Recovery.runAsync(app) { AccessibilityGuard.ensureEnabled(app, "unbind") }
        }, 500)
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        instance = null
        main.removeCallbacksAndMessages(null)
        runCatching { curtain.hide() }
        super.onDestroy()
    }

    companion object {
        private const val TAG = "FilterPhone"
        private const val SCAN_EVERY_MS = 250L
        /** ל-block(): כבר טיפלנו בעצמנו (למשל לחצנו על טאב) — רק ההודעה, בלי פעולה גלובלית. */
        private const val NO_GLOBAL_ACTION = 0
        private const val MAX_NODES = 300
        /** אפליקציות ההגדרות (כולל הגדרות הנגישות של סמסונג). */
        private val SETTINGS_PACKAGES = setOf("com.android.settings", "com.samsung.accessibility",
            "com.samsung.android.accessibility", "com.coloros.accessibility")

        @Volatile private var instance: GuardService? = null

        val isRunning: Boolean get() = instance != null

        private const val PLAY_SCAN_MS = 300L
        private const val CURTAIN_CHECK_MS = 1_500L
        private const val SYSTEM_UI = "com.android.systemui"
        private const val SERVER_CHECK_GAP_MS = 30_000L
        private val io = Executors.newSingleThreadExecutor { r -> Thread(r, "fp-guard-io").apply { isDaemon = true } }

        /** החנות פתחה חלון התקנה ב-Play — שינויי תוכן ב-Play נחוצים מעכשיו (מכל חוט). */
        fun onPlaySession() {
            val s = instance ?: return
            s.main.post { s.reconfigure() }
        }

        /** נקרא אחרי שמירת מדיניות חדשה (מכל חוט). */
        fun onPolicyChanged() {
            val s = instance ?: return
            s.main.post {
                s.reconfigure()
                s.recheckNow()
            }
        }
    }
}
