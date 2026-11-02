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
 * בדפדפן הפנימי של וואטסאפ — רק התוויות של הכפתורים בסרגל ובתפריט שלו (כדי ללחוץ "פתיחה בדפדפן").
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
    private var waBrowserAt = 0L
    private var waBrowserBusy = false               // העברה ל-Chrome באמצע (כמה שלבים עם המתנה) — לא מתחילים שוב
    private var waBrowserReturnUntil = 0L           // הקישור נפתח ב-Chrome והדפדפן הפנימי נשאר מאחור (עד מתי)
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
    private val installOverlay by lazy { PlayInstallOverlay(this) { onInstallButton(it) } }
    private val playBadges by lazy { PlayRequestBadges(this) { title -> onPlayBadgeTap(title) } }   // Play מנוהל
    private var lastManagedScanAt = 0L
    /** הקישורים בהודעה שנלחצה בוואטסאפ (לפני שהדפדפן הפנימי נפתח) — לכרום עובר הקישור האמיתי, לא w.meta.me. */
    private var waClickLinks: List<String> = emptyList()
    private var waClickAt = 0L
    /** כרום: הכפתור הצף ⋮ (דיווח / בדיקה) והתווית ליד תפריט הלחיצה הארוכה — ChromeBubble. */
    private val chromeBubble by lazy { ChromeBubble(this, { onChromeBubble() }, { onChromeChip() }) }
    private var chromePageUrl: String? = null
    private var chromeMenu: Pair<ChromeReport.Kind, String?>? = null
    private val chromeMenuCheck = Runnable { safely("chrome-menu") { checkChromeMenu() } }
    private val chromeChipHide = Runnable { chromeBubble.hideChip(); chromeMenu = null }
    /** Play מנוהל — מתי יצאו מעמוד האפליקציה המאושרת (0 = עדיין בו / השער סגור), ועד מתי לא לפתוח שוב. */
    private var gateLeftAt = 0L
    private var gateQuietUntil = 0L
    private val gateClose: Runnable = Runnable {
        safely("play-gate") {
            gateLeftAt = 0L
            // עדכונים: נסגר רק כשאין עדכון שרץ כבר UPDATE_IDLE_MS ("עדכון הכול" ממשיך גם אחרי שיוצאים)
            val idle = PlayGate.idleMs(this)
            if (PlayGate.isOpen(this) && PlayGate.mode(this) == PlayGate.Mode.UPDATE && idle < PlayGate.UPDATE_IDLE_MS) {
                gateLeftAt = SystemClock.elapsedRealtime()
                main.postDelayed(gateClose, PlayGate.UPDATE_IDLE_MS - idle)
                return@safely
            }
            PlayGate.close(this, "left Play / the app page")
        }
    }
    /** עדכונים שרצים עכשיו בזמן שהשער פתוח — כל התקדמות שומרת אותו פתוח לעדכון הבא בתור. */
    private val gateUpdates = HashSet<Int>()
    private var gateTouchedAt = 0L
    /**
     * התקנה / עדכון מתחילים (נוצר session):
     *   - עדכון של אפליקציה שכבר בטלפון — מותר תמיד; במצב עדכונים השער נשאר פתוח לבא בתור;
     *   - התקנה של אפליקציה מאושרת — במצב התקנה סוגרים מיד (ההתקנה ממשיכה עד הסוף);
     *   - התקנה חדשה של אפליקציה שלא אושרה — סוגרים מיד (והיא עצמה מוסרת — PlayManaged).
     */
    private val gateSessions = object : android.content.pm.PackageInstaller.SessionCallback() {
        override fun onCreated(sessionId: Int) {
            safely("play-gate-session") {
                if (!PlayGate.isOpen(this@GuardService)) return@safely
                val pkg = runCatching { packageManager.packageInstaller.getSessionInfo(sessionId)?.appPackageName }.getOrNull()
                val update = pkg != null && runCatching { packageManager.getPackageInfo(pkg, 0) }.isSuccess
                val approved = pkg != null && pkg in GuardPolicy.get(this@GuardService).allowed
                val mode = PlayGate.mode(this@GuardService)
                Log.i(TAG, "play gate: session for $pkg (update=$update approved=$approved mode=$mode)")
                when {
                    update -> {
                        gateUpdates += sessionId
                        PlayGate.touch(this@GuardService)
                    }
                    approved && mode == PlayGate.Mode.UPDATE -> PlayGate.touch(this@GuardService)
                    else -> {
                        gateQuietUntil = SystemClock.elapsedRealtime() + GATE_QUIET_MS
                        main.removeCallbacks(gateClose)
                        gateLeftAt = 0L
                        PlayGate.close(this@GuardService,
                            if (approved) "install of $pkg started" else "unapproved install of $pkg — closing")
                    }
                }
            }
        }
        override fun onBadgingChanged(sessionId: Int) = Unit
        override fun onActiveChanged(sessionId: Int, active: Boolean) = Unit
        override fun onProgressChanged(sessionId: Int, progress: Float) {
            val now = SystemClock.elapsedRealtime()
            if (sessionId !in gateUpdates || now - gateTouchedAt < 10_000L) return
            gateTouchedAt = now
            safely("play-gate-progress") { PlayGate.touch(this@GuardService) }
        }
        override fun onFinished(sessionId: Int, success: Boolean) {
            if (gateUpdates.remove(sessionId)) safely("play-gate-finished") { PlayGate.touch(this@GuardService) }
        }
    }
    private val avatarMask by lazy { AvatarMask(this) }   // תמונות פרופיל בוואטסאפ — לבן במקומן (WaAvatars)
    private var maskScrollUntil = 0L                // רשימה בוואטסאפ בגלילה — עד אז פסים במקום עיגולים
    private var lastMaskAt = 0L
    private val waImageIds = LinkedHashSet<String>()  // לכיול מרחוק: מזהי רכיבי תמונה ריבועיים שנראו
    private var waImageIdsSavedAt = 0L
    private var playGraceUntil = 0L                 // חזרנו מ-Play לפילטר פון — לא "לחסום" את Play בדרך
    private var installFinishing = false
    private var playLastProgressAt = 0L             // הפעם האחרונה שההתקנה התקדמה (ל"החנות לא מגיבה")
    private val debug by lazy { (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0 }

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        runCatching { packageManager.packageInstaller.registerSessionCallback(gateSessions, main) }
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
            AccessibilityEvent.TYPE_VIEW_CLICKED or                     // הקישור שנלחץ בשיחה בוואטסאפ
            (if (wantContent) AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED or AccessibilityEvent.TYPE_VIEW_SCROLLED else 0) or
            (if (scanKeywords) AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED else 0)
        info.flags = info.flags or AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS or
            AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS      // התפריט של הדפדפן הפנימי (חלון נפרד)
        // הכיסוי של תמונות הפרופיל צריך לזוז עם הגלילה — אירועים בתדירות גבוהה יותר בוואטסאפ
        info.notificationTimeout = if (!wantContent) 0 else if (maskWanted(foreground, policy)) 40 else 150
        if (!maskWanted(foreground, policy) && avatarMask.isShown) avatarMask.hide()
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
            if (avatarMask.isShown) avatarMask.hide()
            return
        }
        val policy = GuardPolicy.get(this)
        if (event.eventType == AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED) {
            if (scanKeywords) onTyped(pkg, event)
            return
        }
        if (event.eventType == AccessibilityEvent.TYPE_VIEW_LONG_CLICKED) {
            if (pkg == ChromeReport.CHROME) ensureChromeBubble()
            onLongClick(pkg, event)
            return
        }
        if (event.eventType == AccessibilityEvent.TYPE_VIEW_CLICKED) {
            if (pkg in WaScreens.PACKAGES) noteWaClick(event)
            else if (pkg == ChromeReport.CHROME) ensureChromeBubble()
            return
        }
        if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            markAlive()
            trackSettings(pkg)
            if (AccountScreens.isAddAccount(pkg, event.className?.toString())) syncForAccountSetup()
            if (pkg != PlayLock.PLAY && pkg != packageName && pkg != SYSTEM_UI) {
                hideInstallOverlay()
                playBadges.hide()
                gateLeft()
            }
            onChromeWindow(pkg)
            // יצאו מוואטסאפ (לא מקלדת / שורת המצב / אנחנו) — בלי כיסוי
            if (avatarMask.isShown && !maskWanted(pkg, policy) && pkg != packageName && pkg != SYSTEM_UI && !isIme(pkg)) {
                avatarMask.hide()
            }
            if (guardSelf(pkg, event.className?.toString())) return
            if (guardCritical(pkg)) return
            endPlayWindowIfLeft(pkg)
            trackForeground(pkg, policy)
            if (guardPlay(pkg, policy, fromContent = false)) return
            if (!policy.isEmpty) onWindow(pkg, event.className?.toString(), policy)
            if (maskWanted(pkg, policy)) {
                // מסך חדש בוואטסאפ: עכשיו, ושוב כשהתוכן נטען
                scheduleMask(0)
                main.postDelayed(maskUpdate, 350)
            }
            if (scanKeywords) checkFocusedInput(pkg)
        } else if (contentEvents) {
            if (pkg in SETTINGS_PACKAGES) {
                onSettingsContent(pkg)
                return
            }
            if (guardPlay(pkg, policy, fromContent = true)) return
            if (maskWanted(pkg, policy)) onMaskEvent(event.eventType == AccessibilityEvent.TYPE_VIEW_SCROLLED)
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

    /**
     * חלון בקשת הרשאת-על (su) של כלי Root (מג'יסק וכו') — דוחים בלחיצת "אחורה" (כלומר: לא מאשרים).
     * נבדק רק כשהאפשרות "כלי Root" פעילה. את עצם האפליקציה (המסך הראשי של המנהל) חוסמים כאפליקציה
     * רגילה — כאן רק את חלון האישור. אם לא זוהה חלון כזה — מחזיר false, וההמשך חוסם כרגיל.
     */
    private var accountSyncAt = 0L

    /** מסך הוספת חשבון Google נפתח — המדיניות מהשרת מיד (לכל היותר פעם בשתי דקות). */
    private fun syncForAccountSetup() {
        val now = SystemClock.elapsedRealtime()
        if (accountSyncAt != 0L && now - accountSyncAt < 2 * 60_000L) return
        accountSyncAt = now
        Recovery.syncSoon(this)
    }

    private fun guardRootPrompt(pkg: String, cls: String?): Boolean {
        if (pkg == packageName) return false
        if (pkg !in RootGuard.MANAGERS && !RootGuard.looksLikeSuWindow(cls)) return false     // סינון זול
        val root = runCatching { rootInActiveWindow }.getOrNull()
        val texts = if (root != null) {
            try { collectTexts(root) } catch (e: Exception) { emptyList() } finally { recycle(root) }
        } else {
            emptyList()
        }
        if (!RootGuard.isSuPrompt(pkg, cls, texts)) return false
        if (block(pkg, GLOBAL_ACTION_BACK, R.string.guard_root_blocked, minGapMs = 700)) {
            ProtectionState.inc(this, ProtectionState.SCREENS_BLOCKED)
        }
        return true
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
            (pkg == PlayLock.PLAY && PlayLock.active(SystemClock.elapsedRealtime()) != null) ||
            (pkg == PlayLock.PLAY && policy.playManaged && PlayLock.PLAY !in policy.apps)

    // ------------------------------------------------------------ שלב א': חלון Play לאפליקציה אחת

    /**
     * Play חסום לכניסה, אבל החנות שלנו פתחה "חלון התקנה" לאפליקציה מסוימת: מתירים רק את העמוד שלה
     * (וחלונות הביניים של ההתקנה). ניווט החוצה — חוזרים לעמוד שלה. true = טופל (לא לחסום כרגיל).
     */
    private fun guardPlay(pkg: String, policy: GuardPolicy.Policy, fromContent: Boolean): Boolean {
        if (pkg == PlayLock.PLAY && policy.playManaged && PlayLock.PLAY !in policy.apps) {
            guardManagedPlay(policy, fromContent)      // Play פתוח ומנוהל — בלי חלון ובלי מסך
            return true
        }
        if (pkg != PlayLock.PLAY || PlayLock.PLAY !in policy.apps) return false
        val now = SystemClock.elapsedRealtime()
        val s = PlayLock.active(now) ?: run {                  // אין חלון — החסימה הרגילה
            hideInstallOverlay()
            return now < playGraceUntil                        // רגע אחרי החזרה לפילטר פון — לא חוסמים
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
            if (root.packageName?.toString() != PlayLock.PLAY) {
                // Play עוד לא בחזית (נפתח מאחורי המסך) — המסך נשאר ומתעדכן
                if (installOverlay.isShown) updateInstallOverlay(null, emptyList(), isTarget = false, s = s, now = now)
                return true
            }
            val (texts, search) = collectOrdered(root)
            if (PlayLock.verdict(texts, search, s.titles) == PlayLock.Verdict.RETURN) {
                hideInstallOverlay()
                returnToPlayTarget(s.pkg, now)
            } else {
                updateInstallOverlay(root, texts, PlayLock.isTargetPage(texts, s.titles), s, now)
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
     * חלון ההתקנה: מסך FilterPhone מעל Play כולו (PlayInstallOverlay) — עולה עוד לפני ש-Play נפתח
     * (onPlaySession) ונשאר עד הסוף, כך שהלקוח לא רואה את Play בכלל ולא יכול לגעת בו. מאחורי המסך:
     * בעמוד של האפליקציה שנבחרה FilterPhone לוחץ "התקנה" / "עדכון" (המשתמש ביקש בחנות שלנו), מציג
     * התקדמות, ובסיום חוזר לחנות של FilterPhone. Play תקוע / מבקש משהו (חשבון, אישור) — "נסה שוב" או
     * חזרה; חלונות מערכת (אישורי התקנה / Play Protect) — לא נוגעים בהם.
     */
    private fun updateInstallOverlay(root: AccessibilityNodeInfo?, texts: List<String>, isTarget: Boolean,
                                     s: PlayLock.Session, now: Long) {
        schedulePlayRecheck()
        val done = PlayLock.installedDuring(s, ManagedApps.installedVersion(this, s.pkg))
        val action = if (isTarget) PlayLock.action(texts) else PlayLock.Action.UNKNOWN
        if (done || action == PlayLock.Action.INSTALLING) playLastProgressAt = now
        val state = when {
            done -> {
                finishInstallSoon()
                overlayState(R.string.play_overlay_done, 100, busy = false,
                    PlayInstallOverlay.Button.OPEN to R.string.play_overlay_open,
                    PlayInstallOverlay.Button.BACK to R.string.play_overlay_back)
            }
            action == PlayLock.Action.INSTALLING -> {
                val pct = PlayLock.progress(texts)
                PlayInstallOverlay.State(
                    if (pct != null) getString(R.string.play_overlay_installing_pct, pct) else getString(R.string.play_overlay_installing),
                    pct, true, listOf(
                        PlayInstallOverlay.Button.BACK to getString(R.string.play_overlay_back_continue),
                        PlayInstallOverlay.Button.CANCEL to getString(R.string.play_overlay_cancel_install)))
            }
            action == PlayLock.Action.INSTALLED -> overlayState(R.string.play_overlay_already, null, busy = false,
                PlayInstallOverlay.Button.OPEN to R.string.play_overlay_open,
                PlayInstallOverlay.Button.BACK to R.string.play_overlay_back)
            PlayLock.stuck(PlayLock.session ?: s, playLastProgressAt, now) -> overlayState(R.string.play_overlay_stuck, null,
                busy = false,
                PlayInstallOverlay.Button.RETRY to R.string.play_overlay_retry,
                PlayInstallOverlay.Button.BACK to R.string.play_overlay_back)
            action == PlayLock.Action.INSTALL || action == PlayLock.Action.UPDATE -> {
                if (root != null && PlayLock.shouldClick(s, action, now) && clickPlayButton(root, PlayLock.buttonTexts(action))) {
                    PlayLock.noteClick(now)
                }
                val cur = PlayLock.session ?: s
                if (cur.clicks >= PlayLock.MAX_CLICKS && now - cur.lastClickAt >= PlayLock.CLICK_RETRY_MS) {
                    // לא התחיל אוטומטית — כפתור משלנו (הלחיצה עוברת לכפתור ב-Play, מאחורי המסך)
                    overlayState(R.string.play_overlay_manual, null, busy = false,
                        PlayInstallOverlay.Button.INSTALL to R.string.play_overlay_install,
                        PlayInstallOverlay.Button.BACK to R.string.play_overlay_back)
                } else {
                    overlayState(if (action == PlayLock.Action.UPDATE) R.string.play_overlay_starting_update
                        else R.string.play_overlay_starting, null, busy = true,
                        PlayInstallOverlay.Button.BACK to R.string.play_overlay_cancel)
                }
            }
            else -> overlayState(R.string.play_overlay_loading, null, busy = true,      // נפתח / חלון ביניים
                PlayInstallOverlay.Button.BACK to R.string.play_overlay_cancel)
        }
        installOverlay.show(s.label, state)
    }

    /** החנות שלנו פתחה חלון התקנה: המסך עולה מיד — עוד לפני ש-Play נפתח מאחוריו. */
    private fun showInstallOverlayNow() {
        val now = SystemClock.elapsedRealtime()
        val s = PlayLock.active(now) ?: return
        if (PlayLock.PLAY !in GuardPolicy.get(this).apps || AdminState.isPaused(this)) return   // Play פתוח — בלי מסך
        installFinishing = false
        playLastProgressAt = now
        updateInstallOverlay(null, emptyList(), isTarget = false, s = s, now = now)
    }

    private fun overlayState(status: Int, progress: Int?, busy: Boolean,
                             vararg buttons: Pair<PlayInstallOverlay.Button, Int>) =
        PlayInstallOverlay.State(getString(status), progress, busy, buttons.map { (b, t) -> b to getString(t) })

    /** כל עוד חלון ההתקנה פתוח ו-Play בחזית — בודקים שוב (אחוזים, סיום) גם בלי אירוע. */
    private fun schedulePlayRecheck() {
        main.removeCallbacks(playRecheck)
        main.postDelayed(playRecheck, OVERLAY_CHECK_MS)
    }

    private fun hideInstallOverlay() {
        installOverlay.hide()
        main.removeCallbacks(playRecheck)
    }

    // ------------------------------------------------------------ Google Play פתוח ומנוהל (PlayManaged)

    /**
     * Play פתוח: גולשים ומחפשים כרגיל. בעמוד של אפליקציה שאינה מאושרת (השם שלה לא ברשימת המאושרות) — כל
     * כפתור "התקנה" מכוסה בתווית "חסום להתקנה · בקשת אישור" (PlayRequestBadges). אפליקציה מאושרת — כרגיל.
     * ההבטחה עצמה אחרי ההתקנה (PlayManaged.onPackageAdded): לא מאושרת — מוסרת מיד.
     */
    private fun guardManagedPlay(policy: GuardPolicy.Policy, fromContent: Boolean) {
        val now = SystemClock.elapsedRealtime()
        if (fromContent && now - lastManagedScanAt < PLAY_SCAN_MS) {
            main.removeCallbacks(managedRecheck)
            main.postDelayed(managedRecheck, PLAY_SCAN_MS)
            return
        }
        lastManagedScanAt = now
        val root = runCatching { rootInActiveWindow }.getOrNull() ?: return
        try {
            if (root.packageName?.toString() != PlayLock.PLAY) {
                playBadges.hide()
                gateLeft()
                return
            }
            val texts = ArrayList<String>(64)
            val buttons = ArrayList<Pair<Rect, Boolean>>(4)
            val searchField = collectInstallButtons(root, texts, buttons)
            val approved = PlayManaged.pageApproved(texts, policy.approvedTitles)
            // שער ההתקנות (מנהל מכשיר): התקנה — רק בעמוד של אפליקציה מאושרת; עדכון — כל אפליקציה שכבר
            // בטלפון (הכפתור הראשי בעמוד שלה "עדכון"), ומסך העדכונים של Play ("עדכון הכול")
            val appPage = !PlayLock.isBrowseScreen(texts, searchField)
            val action = if (appPage) PlayLock.action(texts) else PlayLock.Action.UNKNOWN
            when {
                action == PlayLock.Action.UPDATE || PlayLock.isUpdatesScreen(texts) -> gateApproved(now, PlayGate.Mode.UPDATE)
                approved && action == PlayLock.Action.INSTALL -> gateApproved(now, PlayGate.Mode.INSTALL)
                else -> gateLeft()
            }
            // תוויות "חסום להתקנה": כל כפתור התקנה — חוץ מהכפתור הראשי של אפליקציה מאושרת. גם בעמוד מאושר
            // יש פרסומות והמלצות עם "התקנה" משלהן; הן לא מאושרות.
            val title = PlayManaged.guessTitle(texts)
            val badges = buttons.filterNot { (_, primary) -> primary && approved }
                .map { (r, primary) -> r to if (primary) title else "" }
            if (badges.isEmpty()) playBadges.hide() else playBadges.show(badges)
        } catch (e: Exception) {
            Log.w(TAG, "managed play check failed", e)
        } finally {
            recycle(root)
        }
    }

    /** בעמוד של אפליקציה מאושרת ("התקנה"), או עדכון של אפליקציה שבטלפון — השער פתוח. */
    private fun gateApproved(now: Long, mode: PlayGate.Mode) {
        main.removeCallbacks(gateClose)
        gateLeftAt = 0L
        if (mode == PlayGate.Mode.INSTALL && now < gateQuietUntil) return   // ההתקנה כבר התחילה — לא פותחים שוב מיד
        PlayGate.open(this, mode)
    }

    /** לא בעמוד מאושר: השער נסגר אחרי השהיה קצרה (הלחיצה על "התקנה" לפעמים מקדימה את הסריקה). */
    private fun gateLeft() {
        if (gateLeftAt != 0L || !PlayGate.isOpen(this)) return
        gateLeftAt = SystemClock.elapsedRealtime()
        main.postDelayed(gateClose, PlayGate.LEAVE_GRACE_MS)
    }

    private val managedRecheck = Runnable {
        safely("managed-play") {
            val policy = GuardPolicy.get(this)
            if (!AdminState.isPaused(this) && policy.playManaged) guardManagedPlay(policy, fromContent = false)
        }
    }

    /**
     * הטקסטים בסדר הרכיבים, ומיקום כל כפתור התקנה גלוי — עם סימון אם הוא הכפתור הראשי של העמוד
     * (PlayLock.primaryIndex) או כפתור של פרסומת / המלצה בהמשך. true = יש שדה הקלדה (מסך חיפוש).
     */
    private fun collectInstallButtons(root: AccessibilityNodeInfo, texts: MutableList<String>,
                                      buttons: MutableList<Pair<Rect, Boolean>>): Boolean {
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        stack.addLast(root)
        val primaryLabels = ArrayList<String?>()
        var editable = false
        var seen = 0
        while (stack.isNotEmpty() && seen < MAX_NODES) {
            val node = stack.removeLast()
            seen++
            if (node.isEditable || node.className?.toString() == "android.widget.EditText") editable = true
            val text = node.text?.toString()?.takeIf { it.isNotBlank() }
            val desc = node.contentDescription?.toString()?.takeIf { it.isNotBlank() }
            text?.let(texts::add)
            desc?.let(texts::add)
            val label = text ?: desc
            val install = PlayManaged.isInstallButton(text) || PlayManaged.isInstallButton(desc)
            if (install || PlayLock.isPrimaryLabel(label)) primaryLabels += if (install) "install" else label
            if (node.isVisibleToUser && install) {
                // הכפתור הלחיץ עצמו (הטקסט לפעמים ברכיב פנימי) — המלבן שלו
                var target: AccessibilityNodeInfo? = node
                var depth = 0
                val parents = ArrayList<AccessibilityNodeInfo>()
                while (target != null && !target.isClickable && depth < 3) {
                    target = target.parent?.also { parents += it }
                    depth++
                }
                val r = Rect()
                (target ?: node).getBoundsInScreen(r)
                // הכפתור הראשי של העמוד (לא "התקנה" של פרסומת אחריו)
                val primary = PlayLock.primaryIndex(primaryLabels, PRIMARY_INSTALL) == primaryLabels.lastIndex
                if (r.width() > 0 && r.height() > 0 && buttons.none { it.first == r }) buttons += r to primary
                parents.forEach { recycle(it) }
            }
            for (i in node.childCount - 1 downTo 0) {
                runCatching { node.getChild(i) }.getOrNull()?.let { stack.addLast(it) }
            }
            if (node !== root) recycle(node)
        }
        stack.forEach { if (it !== root) recycle(it) }
        return editable
    }

    // ------------------------------------------------------------ כרום: דיווח / שליחה לבדיקה

    /**
     * חלון חדש: בכרום — הכפתור הצף (ובדיקה אם נפתח תפריט לחיצה ארוכה); באפליקציה אחרת — בלי כפתור.
     * הכפתור יורד רק כשבאמת יצאו מכרום: חלון של FilterPhone עצמה (גם הכפתור עצמו — הוספת השכבה שולחת אירוע
     * בשם FilterPhone, וזה מה שהעלים אותו אחרי שנייה), מקלדת, שורת המערכת, או אירוע מאוחר של אפליקציה אחרת
     * (וואטסאפ אחרי "אחורה", חלונית של Google) — לא מורידים; בודקים אחרי רגע מה באמת בחזית.
     */
    private fun onChromeWindow(pkg: String) {
        // בכל רמות הסינון והגלישה (גם "פתוח") — כל אחד יכול לחסום לעצמו / לדווח / לשלוח לבדיקה
        if (pkg == ChromeReport.CHROME && !AdminState.isPaused(this)) {
            main.removeCallbacks(chromeLeftCheck)
            chromeBubble.show()
            main.removeCallbacks(chromeMenuCheck)
            main.postDelayed(chromeMenuCheck, 250)
            main.postDelayed(chromeMenuCheck, 700)
        } else if (chromeBubble.isShown && pkg != packageName && pkg != SYSTEM_UI && !isIme(pkg)) {
            main.removeCallbacks(chromeLeftCheck)
            main.postDelayed(chromeLeftCheck, CHROME_LEFT_CHECK_MS)
        }
    }

    /** אירוע של אפליקציה אחרת בזמן שהכפתור מוצג: יורד רק אם כרום כבר לא בחזית. */
    private val chromeLeftCheck = Runnable {
        safely("chrome-left") {
            val front = withActiveRoot { it.packageName?.toString() }
            if (front != ChromeReport.CHROME && front != SYSTEM_UI && !(front != null && isIme(front))) {
                chromeBubble.hide()
                chromeMenu = null
            }
        }
    }

    /** לחיצה בתוך כרום והכפתור לא מוצג (נפל / לא עלה) — מעלים אותו שוב. */
    private fun ensureChromeBubble() {
        if (!chromeBubble.isShown && !AdminState.isPaused(this)) {
            main.removeCallbacks(chromeLeftCheck)
            chromeBubble.show()
        }
    }

    /** תפריט הלחיצה הארוכה של כרום (קישור / תמונה) — התווית "דיווח / בדיקה"; אחרת — כתובת הדף. */
    private fun checkChromeMenu() {
        val root = runCatching { rootInActiveWindow }.getOrNull() ?: return
        try {
            if (root.packageName?.toString() != ChromeReport.CHROME) return
            val bar = runCatching { root.findAccessibilityNodeInfosByViewId(ChromeReport.URL_BAR_ID) }.getOrNull().orEmpty()
            bar.firstOrNull()?.text?.toString()?.let { t -> ChromeReport.pageUrl(t)?.let { chromePageUrl = it } }
            bar.forEach { recycle(it) }
            val texts = collectTexts(root)
            val kind = ChromeReport.contextMenu(texts)
            if (kind == null) {
                if (chromeBubble.chipShown) { chromeBubble.hideChip(); chromeMenu = null }
                return
            }
            chromeMenu = kind to ChromeReport.menuUrl(texts)
            chromeBubble.showChip(getString(if (kind == ChromeReport.Kind.IMAGE) R.string.web_report_chip_image
                else R.string.web_report_chip_link))
            main.removeCallbacks(chromeChipHide)
            main.postDelayed(chromeChipHide, 12_000)
        } finally {
            recycle(root)
        }
    }

    /** הכפתור הצף: דיווח / בדיקה על הדף הנוכחי. */
    private fun onChromeBubble() {
        safely("chrome-bubble") {
            val root = runCatching { rootInActiveWindow }.getOrNull()
            try {
                if (root?.packageName?.toString() == ChromeReport.CHROME) {
                    val bar = runCatching { root.findAccessibilityNodeInfosByViewId(ChromeReport.URL_BAR_ID) }.getOrNull().orEmpty()
                    bar.firstOrNull()?.text?.toString()?.let { t -> ChromeReport.pageUrl(t)?.let { chromePageUrl = it } }
                    bar.forEach { recycle(it) }
                }
            } finally {
                root?.let { recycle(it) }
            }
            WebReportActivity.open(this, ChromeReport.Kind.SITE, chromePageUrl, chromePageUrl)
        }
    }

    /** התווית ליד תפריט הלחיצה הארוכה: דיווח / בדיקה על הקישור / התמונה שנבחרו. */
    private fun onChromeChip() {
        safely("chrome-chip") {
            val (kind, url) = chromeMenu ?: return@safely
            chromeBubble.hideChip()
            performGlobalAction(GLOBAL_ACTION_BACK)              // סוגר את התפריט של כרום
            WebReportActivity.open(this, kind, url ?: chromePageUrl.takeIf { kind == ChromeReport.Kind.LINK },
                chromePageUrl)
            chromeMenu = null
        }
    }

    private fun onPlayBadgeTap(title: String) {
        PlayManaged.openRequest(this, title)
    }

    /** כפתור במסך ההתקנה. */
    private fun onInstallButton(b: PlayInstallOverlay.Button) {
        safely("play-overlay") {
            val s = PlayLock.session ?: run { hideInstallOverlay(); return@safely }
            val now = SystemClock.elapsedRealtime()
            when (b) {
                PlayInstallOverlay.Button.INSTALL -> {
                    val root = runCatching { rootInActiveWindow }.getOrNull() ?: return@safely
                    try {
                        if (root.packageName?.toString() == PlayLock.PLAY &&
                            clickPlayButton(root, PlayLock.buttonTexts(PlayLock.Action.INSTALL) +
                                PlayLock.buttonTexts(PlayLock.Action.UPDATE))) PlayLock.noteClick(now)
                    } finally {
                        recycle(root)
                    }
                    playRecheck.run()
                }
                PlayInstallOverlay.Button.CANCEL -> {
                    val root = runCatching { rootInActiveWindow }.getOrNull()
                    try {
                        if (root?.packageName?.toString() == PlayLock.PLAY) {
                            clickPlayButton(root, PlayLock.buttonTexts(PlayLock.Action.INSTALLING))
                        }
                    } finally {
                        root?.let { recycle(it) }
                    }
                    backToFilterPhone()
                }
                PlayInstallOverlay.Button.BACK -> backToFilterPhone()
                PlayInstallOverlay.Button.OPEN -> {
                    val launch = packageManager.getLaunchIntentForPackage(s.pkg)
                    endPlaySession()
                    if (launch == null || runCatching { startActivity(launch.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)) }.isFailure) {
                        backToFilterPhone()
                    }
                }
                PlayInstallOverlay.Button.RETRY -> {
                    PlayLock.retry(now)
                    playLastProgressAt = now
                    val market = android.content.Intent(android.content.Intent.ACTION_VIEW,
                        android.net.Uri.parse("market://details?id=${s.pkg}")).setPackage(PlayLock.PLAY)
                        .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK or android.content.Intent.FLAG_ACTIVITY_CLEAR_TOP)
                    runCatching { startActivity(market) }
                    playRecheck.run()
                }
            }
        }
    }

    /** ההתקנה הסתיימה — רגע להראות "הותקן", וחזרה לחנות של FilterPhone. */
    private fun finishInstallSoon() {
        if (installFinishing) return
        installFinishing = true
        main.postDelayed({ safely("play-finish") { if (PlayLock.session != null) backToFilterPhone() } }, FINISH_SHOW_MS)
    }

    private fun endPlaySession() {
        PlayLock.end()
        installFinishing = false
        hideInstallOverlay()
        playReturns.clear()
        playGraceUntil = SystemClock.elapsedRealtime() + 2_500
        reconfigure()
    }

    /** סוגרים את חלון ההתקנה וחוזרים לפילטר פון (Play חוזר להיות חסום; הורדה שהתחילה ממשיכה ברקע). */
    private fun backToFilterPhone() {
        endPlaySession()
        val home = android.content.Intent(this, MainActivity::class.java).addFlags(
            android.content.Intent.FLAG_ACTIVITY_NEW_TASK or android.content.Intent.FLAG_ACTIVITY_REORDER_TO_FRONT or
                android.content.Intent.FLAG_ACTIVITY_SINGLE_TOP)
        runCatching { startActivity(home) }.onFailure { performGlobalAction(GLOBAL_ACTION_HOME) }
    }

    /**
     * לוחץ על הכפתור ב-Play שהטקסט שלו באחת מהמילים [labels] (התקנה / עדכון / ביטול) — רק רכיב גלוי ופעיל,
     * דרך ACTION_CLICK שלו או של ההורה הלחיץ הקרוב. true אם נלחץ.
     */
    /**
     * לוחץ על הכפתור הראשי של עמוד האפליקציה ב-Play, אם הכיתוב שלו ב-[labels]. רכיבים בסדר המסך (מלמעלה
     * למטה), ורק בין הכפתורים הראשיים (PlayLock.primaryIndex) — אף פעם לא "התקנה" / "עדכון" של פרסומת או
     * המלצה בהמשך העמוד.
     */
    private fun clickPlayButton(root: AccessibilityNodeInfo, labels: Set<String>): Boolean {
        if (labels.isEmpty()) return false
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        val seen = ArrayList<AccessibilityNodeInfo>()
        val primary = ArrayList<Pair<String, AccessibilityNodeInfo>>()     // הכפתורים הראשיים, בסדר המסך
        stack.addLast(root)
        try {
            var visited = 0
            while (stack.isNotEmpty() && visited < MAX_NODES && !PlayLock.primaryDecided(primary.map { it.first })) {
                val n = stack.removeLast()
                if (n !== root) seen += n
                visited++
                val label = n.text?.toString() ?: n.contentDescription?.toString()
                if (label != null && PlayLock.isPrimaryLabel(label)) primary += label to n
                for (i in n.childCount - 1 downTo 0) runCatching { n.getChild(i) }.getOrNull()?.let { stack.addLast(it) }
            }
            val n = PlayLock.primaryIndex(primary.map { it.first }, labels)?.let { primary[it].second } ?: return false
            if (!n.isVisibleToUser || !n.isEnabled) return false
            var target: AccessibilityNodeInfo? = n
            var depth = 0
            while (target != null && !target.isClickable && depth < 5) {
                target = target.parent?.also { seen += it }
                depth++
            }
            return target != null && target.isClickable && target.isEnabled &&
                target.performAction(AccessibilityNodeInfo.ACTION_CLICK)
        } finally {
            seen.forEach { recycle(it) }
            stack.forEach { if (it !== root) recycle(it) }
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
            hideInstallOverlay()
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
        installFinishing = false
        hideInstallOverlay()
        playReturns.clear()
        reconfigure()
    }

    /** הטקסטים בחלון לפי סדר הרכיבים (מלמעלה למטה), ויש בו שדה הקלדה? — ל-PlayLock. */
    private fun collectOrdered(root: AccessibilityNodeInfo): Pair<List<String>, Boolean> {
        val out = ArrayList<String>(64)
        var editable = false
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        stack.addLast(root)
        var seen = 0
        while (stack.isNotEmpty() && seen < MAX_NODES) {
            val node = stack.removeLast()
            seen++
            if (node.isEditable || node.className?.toString() == "android.widget.EditText") editable = true
            val text = node.text?.toString()?.takeIf { it.isNotBlank() }
            val desc = node.contentDescription?.toString()?.takeIf { it.isNotBlank() }
            text?.let { out.add(it) }
            desc?.let { out.add(it) }
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
        if (waBrowserReturnUntil != 0L && pkg in WaScreens.PACKAGES && cls?.startsWith("com.whatsapp") == true &&
            !WaBrowser.isBrowserScreen(cls)) waBrowserReturnUntil = 0L     // חזרו לשיחה — הקישור הבא מועבר כרגיל
        if (policy.blockRootPrompt && guardRootPrompt(pkg, cls)) return    // חלון בקשת הרשאת-על — דחייה
        when (GuardPolicy.check(this, policy, pkg, cls)) {
            GuardPolicy.Block.APP -> {
                block(pkg, GLOBAL_ACTION_HOME, R.string.guard_app_blocked)
                ProtectionState.inc(this, ProtectionState.APPS_BLOCKED)
                checkServerForOpening(pkg)
                return
            }
            GuardPolicy.Block.SCREEN -> {
                if (pkg in WaScreens.PACKAGES && WaBrowser.isBrowserScreen(cls)) {
                    openWaLinkInChrome(pkg, policy)            // הדפדפן הפנימי של וואטסאפ → Chrome
                } else {
                    block(pkg, GLOBAL_ACTION_BACK, R.string.guard_screen_blocked)
                }
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

    // ------------------------------------------------------------ תמונות פרופיל בוואטסאפ: לבן במקומן

    private fun maskWanted(pkg: String?, policy: GuardPolicy.Policy): Boolean =
        policy.waProfile?.let { it.mask && pkg != null && pkg in it.packages } == true

    private val maskUpdate = Runnable { safely("avatar-mask") { updateAvatarMask() } }

    private fun scheduleMask(delayMs: Long) {
        main.removeCallbacks(maskUpdate)
        if (delayMs <= 0) main.post(maskUpdate) else main.postDelayed(maskUpdate, delayMs)
    }

    /** שינוי תוכן / גלילה בוואטסאפ: עדכון מיד (לכל היותר פעם ב-MASK_EVERY_MS); בגלילה — פסים, ובסופה עיגולים. */
    private fun onMaskEvent(scrolled: Boolean) {
        val now = SystemClock.elapsedRealtime()
        if (scrolled) {
            maskScrollUntil = now + MASK_SCROLL_HOLD_MS
            main.postDelayed(maskUpdate, MASK_SCROLL_HOLD_MS + 60)    // הגלילה נעצרה — חזרה לעיגולים
        }
        scheduleMask(if (now - lastMaskAt >= MASK_EVERY_MS) 0 else MASK_EVERY_MS)
    }

    private fun updateAvatarMask() {
        lastMaskAt = SystemClock.elapsedRealtime()
        val wa = GuardPolicy.get(this).waProfile
        if (wa == null || !wa.mask || AdminState.isPaused(this)) {
            avatarMask.hide()
            return
        }
        val root = runCatching { rootInActiveWindow }.getOrNull() ?: return     // רגעי — משאירים כמו שהיה
        if (root.packageName?.toString() !in wa.packages) {
            recycle(root)
            avatarMask.hide()
            return
        }
        val nodes = collectMaskNodes(root)
        val dm = resources.displayMetrics
        val d = dm.density
        val screen = (dm.widthPixels.toLong() * dm.heightPixels).coerceAtLeast(1)
        val minPx = (18 * d).toInt()
        val scrolling = SystemClock.elapsedRealtime() < maskScrollUntil
        avatarMask.show(WaAvatars.masks(nodes.asSequence(), WaAvatars.Config(wa.maskIds, wa.descriptions, wa.minArea),
            screen, minPx, pad = (3 * d).toInt(), scrolling = scrolling, stripMaxPx = (72 * d).toInt()))
        noteWaImageIds(nodes, minPx)
    }

    /** כל הרכיבים הגלויים בחלון (עד MAX_MASK_NODES) עם המיקום, וגבולות הרשימה הנגללת שהם בתוכה. בלי טקסט. */
    private fun collectMaskNodes(root: AccessibilityNodeInfo): List<WaAvatars.Node> {
        val out = ArrayList<WaAvatars.Node>()
        val queue = ArrayDeque<Pair<AccessibilityNodeInfo, WaAvatars.Box?>>()
        val seen = ArrayList<AccessibilityNodeInfo>()
        queue.add(root to null)
        val r = Rect()
        var visited = 0
        try {
            while (queue.isNotEmpty() && visited < MAX_MASK_NODES) {
                val (n, list) = queue.removeFirst()
                seen += n
                visited++
                if (!n.isVisibleToUser) continue
                n.getBoundsInScreen(r)
                val box = WaAvatars.Box(r.left, r.top, r.right, r.bottom)
                val cls = n.className?.toString()
                val isImage = cls != null && (cls.endsWith("ImageView") || cls.endsWith("ImageButton"))
                out += WaAvatars.Node(n.viewIdResourceName, cls, if (isImage) n.contentDescription?.toString() else null,
                    box, list)
                val inner = if (n.isScrollable && box.width > 0 && box.height > 0) box else list
                for (i in 0 until n.childCount) runCatching { n.getChild(i) }.getOrNull()?.let { queue.add(it to inner) }
            }
        } finally {
            seen.forEach { recycle(it) }
            queue.forEach { recycle(it.first) }
        }
        return out
    }

    /** מזהי תמונות ריבועיות שנראו בוואטסאפ — נשמרים (לכל היותר פעם בדקה) ומדווחים לשרת לכיול הכיסוי. */
    private fun noteWaImageIds(nodes: List<WaAvatars.Node>, minPx: Int) {
        if (waImageIds.size >= 30) return
        val before = waImageIds.size
        waImageIds += WaAvatars.squareImageIds(nodes.asSequence(), minPx, 30 - waImageIds.size)
        val now = SystemClock.elapsedRealtime()
        if (waImageIds.size != before && now - waImageIdsSavedAt > 60_000) {
            waImageIdsSavedAt = now
            runCatching { ProtectionState.setString(this, ProtectionState.WA_IMAGE_IDS, waImageIds.joinToString(",")) }
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

    /**
     * הדפדפן הפנימי של וואטסאפ נפתח (WaBrowser) → הקישור נפתח ב-Chrome, שם הוא מסונן:
     *   1. מחכים שהמסך שלו באמת עלה: האירוע מגיע לפני שהסרגל מוצג, ובינתיים החלון הפעיל הוא עדיין השיחה —
     *      וה-⋮ שלה הוא תפריט אחר לגמרי (לכן: לא לוחצים כל עוד יש בחלון שדה הקלדה של שיחה).
     *   2. כפתור "פתיחה בדפדפן" ישיר בסרגל, אם יש; אחרת ⋮ ← "פתיחה בדפדפן".
     *   3. אין בתפריט "פתיחה בדפדפן" — "העתקת הקישור", ו-FilterPhone פותחת את הקישור ב-Chrome (LinkRelayActivity).
     *   4. כלום לא נמצא — אחורה עם הסבר. מה שנמצא בסרגל / בתפריט (רק התוויות) נשמר לאבחון בדשבורד.
     * Chrome חסום במכשיר הזה (מסלול בלי אינטרנט) — אין לאן להעביר: סוגרים.
     */
    private fun openWaLinkInChrome(pkg: String, policy: GuardPolicy.Policy) {
        val now = SystemClock.elapsedRealtime()
        if ((waBrowserBusy && now - waBrowserAt < WA_FLOW_MAX_MS) || now - waBrowserAt < 2_000) return
        waBrowserAt = now
        if (now < waBrowserReturnUntil) {
            // חזרו מ-Chrome אל הדפדפן הפנימי שנשאר מאחור — סוגרים אותו (ולא פותחים שוב את Chrome בלולאה)
            waBrowserReturnUntil = 0L
            performGlobalAction(GLOBAL_ACTION_BACK)
            return
        }
        if (GuardPolicy.check(this, policy, WaBrowser.CHROME, null) != null || !chromeInstalled()) {
            waBrowserDone(pkg, "blocked:chrome", ok = false)
            return
        }
        waBrowserBusy = true
        // נלחץ קישור בהודעה רגע לפני — הוא הכתובת האמיתית: סוגרים את הדפדפן הפנימי ופותחים אותו ב-Chrome
        val clicked = waClickLinks.takeIf { now - waClickAt < WA_CLICK_FRESH_MS }.orEmpty()
        waClickLinks = emptyList()
        if (clicked.isNotEmpty()) {
            main.postDelayed({
                waStep(pkg) {
                    performGlobalAction(GLOBAL_ACTION_BACK)
                    openWaLinks(clicked)
                    waBrowserDone(pkg, "ok:clicked", ok = true)
                }
            }, WA_STEP_MS)
            return
        }
        main.postDelayed({ waBrowserFindMenu(pkg, attempt = 0) }, WA_STEP_MS)
    }

    /** הקישורים מההודעה שנלחצה בוואטסאפ (הטקסט של הרכיב שנלחץ). */
    private fun noteWaClick(event: AccessibilityEvent) {
        val texts = ArrayList<String>(4)
        event.text?.forEach { t -> t?.toString()?.let(texts::add) }
        event.contentDescription?.toString()?.let(texts::add)
        runCatching { event.source }.getOrNull()?.let { src ->
            src.text?.toString()?.let(texts::add)
            src.contentDescription?.toString()?.let(texts::add)
            recycle(src)
        }
        val links = WaBrowser.linksIn(texts)
        if (links.isNotEmpty()) {
            waClickLinks = links
            waClickAt = SystemClock.elapsedRealtime()
        }
    }

    /** קישור אחד — ישר ל-Chrome; כמה — "איזה קישור לפתוח?". */
    private fun openWaLinks(links: List<String>) {
        if (links.size == 1) LinkRelayActivity.openInChrome(this, links[0]) else LinkChooserActivity.open(this, links)
    }

    /**
     * הדפדפן הפנימי הראה רק את המעטפת w.meta.me (לא עובדת בכרום) — סוגרים אותו וקוראים את הקישורים מהשיחה
     * שמאחוריו (ההודעה שנלחצה עדיין על המסך). אין קישור בהודעה — הסבר.
     */
    private fun waOpenFromChat(pkg: String) {
        performGlobalAction(GLOBAL_ACTION_BACK)
        main.postDelayed({
            waStep(pkg) {
                val links = withActiveRoot { root ->
                    if (root.packageName?.toString() == pkg) WaBrowser.linksIn(collectTexts(root)) else emptyList()
                }.orEmpty()
                if (links.isEmpty()) {
                    waBrowserDone(pkg, "fail:shim", ok = false, toast = false)
                    Toast.makeText(this, R.string.wa_link_not_found, Toast.LENGTH_LONG).show()
                } else {
                    openWaLinks(links.takeLast(8).reversed())          // האחרונים בשיחה — למעלה
                    waBrowserDone(pkg, "ok:chat", ok = true)
                }
            }
        }, 600)
    }

    /** שלב 1–2: הסרגל של הדפדפן הפנימי — כפתור ישיר, או ⋮. עד ~3 שניות (הסרגל עולה אחרי האירוע). */
    private fun waBrowserFindMenu(pkg: String, attempt: Int): Unit = waStep(pkg) {
        val labels = ArrayList<String>()
        val step = withActiveRoot { root ->
            when {
                root.packageName?.toString() != pkg || hasComposer(root) -> -1         // עוד לא עלה (או שיצאו ממנו)
                // עדיין על המעטפת w.meta.me — מחכים להפניה לאתר האמיתי (אחרת "פתיחה בדפדפן" תעביר את המעטפת)
                collectTexts(root).any { WaBrowser.isShim(it) } -> if (attempt < WA_SHIM_TRIES) -2 else -3
                clickFirst(root) { t, d -> WaBrowser.isOpenExternal(t, d) } -> 2      // כפתור ישיר בסרגל
                clickFirst(root) { t, d -> WaBrowser.isMoreMenu(t, d) } -> 1          // ⋮ נפתח
                else -> { collectLabels(root, labels); 0 }
            }
        } ?: -1
        when {
            step == -2 -> main.postDelayed({ waBrowserFindMenu(pkg, attempt + 1) }, WA_STEP_MS)
            step == -3 -> waOpenFromChat(pkg)
            step == 2 -> waBrowserDone(pkg, "ok:direct", ok = true)
            step == 1 -> main.postDelayed({ waBrowserPickItem(pkg, attempt = 0) }, WA_STEP_MS)
            attempt < WA_MENU_TRIES -> main.postDelayed({ waBrowserFindMenu(pkg, attempt + 1) }, WA_STEP_MS)
            // הדפדפן הפנימי כבר לא בחזית — לא לוחצים "אחורה" על מה שהמשתמש פתח אחריו
            step == -1 -> waBrowserDone(pkg, "skip:left", ok = true, toast = false)
            else -> waBrowserDone(pkg, "fail:no-menu", ok = false, labels = labels)
        }
    }

    /** שלב 2–3: בתפריט שנפתח (חלון נפרד) — "פתיחה בדפדפן", ובלעדיו "העתקת הקישור". */
    private fun waBrowserPickItem(pkg: String, attempt: Int): Unit = waStep(pkg) {
        val labels = ArrayList<String>()
        // קודם "פתיחה בדפדפן" בכל החלונות, ורק אחר כך "העתקת הקישור" — שלא ייבחר העתקה מהסרגל שמאחורי התפריט
        val step = withPkgWindows(pkg) { root ->
            if (!hasComposer(root) && clickFirst(root) { t, d -> WaBrowser.isOpenExternal(t, d) }) 2 else 0
        }.takeIf { it != 0 } ?: withPkgWindows(pkg) { root ->
            when {
                hasComposer(root) -> 0
                clickFirst(root) { t, d -> WaBrowser.isCopyLink(t, d) } -> 3
                else -> { collectLabels(root, labels); 0 }
            }
        }
        when {
            step == 2 -> waBrowserDone(pkg, "ok:menu", ok = true)
            step == 3 -> main.postDelayed({
                waStep(pkg) {
                    performGlobalAction(GLOBAL_ACTION_BACK)                 // סוגר את הדפדפן הפנימי
                    LinkRelayActivity.start(this)                           // והקישור שהועתק נפתח ב-Chrome
                    waBrowserDone(pkg, "ok:copy", ok = true, toast = false)
                }
            }, WA_STEP_MS)
            attempt < WA_ITEM_TRIES -> main.postDelayed({ waBrowserPickItem(pkg, attempt + 1) }, WA_STEP_MS)
            else -> {
                performGlobalAction(GLOBAL_ACTION_BACK)                     // סוגר את התפריט שנפתח
                waBrowserDone(pkg, "fail:no-item", ok = false, labels = labels)
            }
        }
    }

    /** שלב בהעברה: תקלה לא משאירה את הדפדפן הפנימי פתוח — נסגר כמו כשלא נמצא תפריט. */
    private inline fun waStep(pkg: String, step: () -> Unit) {
        try {
            step()
        } catch (t: Throwable) {
            if (t is OutOfMemoryError) throw t
            onGuardError("wa-browser", t)
            waBrowserDone(pkg, "fail:error", ok = false)
        }
    }

    private fun waBrowserDone(pkg: String, result: String, ok: Boolean, labels: List<String> = emptyList(),
                              toast: Boolean = true) {
        waBrowserBusy = false
        waBrowserAt = SystemClock.elapsedRealtime()
        val found = WaBrowser.summary(labels)
        ProtectionState.setString(this, ProtectionState.WA_BROWSER, if (found.isEmpty()) result else "$result — $found")
        if (debug) Log.d(TAG, "wa-browser: $result ${if (found.isEmpty()) "" else found}")
        if (ok) {
            if (result == "ok:direct" || result == "ok:menu") waBrowserReturnUntil = waBrowserAt + WA_RETURN_MS
            if (toast) Toast.makeText(this, R.string.guard_wa_browser_chrome, Toast.LENGTH_SHORT).show()
            return
        }
        lastBlockAt = 0
        val message = if (result == "blocked:chrome") R.string.guard_wa_browser_no_chrome else R.string.guard_wa_browser_blocked
        block(pkg, GLOBAL_ACTION_BACK, message)
    }

    private fun chromeInstalled(): Boolean = runCatching {
        packageManager.getApplicationInfo(WaBrowser.CHROME, 0).enabled
    }.getOrDefault(false)

    /** בחלון יש שדה הקלדה של שיחה (com.whatsapp:id/entry) — זו השיחה, לא הדפדפן הפנימי. */
    private fun hasComposer(root: AccessibilityNodeInfo): Boolean =
        anyNode(root) { WaBrowser.isComposer(it.viewIdResourceName) }

    /** תוויות של כפתורים (טקסט / תיאור קצרים) — רק לאבחון כשלא נמצא מה ללחוץ. */
    private fun collectLabels(root: AccessibilityNodeInfo, out: MutableList<String>) {
        anyNode(root) { n ->
            if (n.isClickable || n.isCheckable) {
                n.text?.toString()?.let { out += it }
                n.contentDescription?.toString()?.let { out += it }
            }
            false
        }
    }

    /** מעבר על כל הרכיבים (עד MAX_NODES) — true ברגע ש-[match] מחזיר true. */
    private fun anyNode(root: AccessibilityNodeInfo, match: (AccessibilityNodeInfo) -> Boolean): Boolean {
        val seen = ArrayList<AccessibilityNodeInfo>()
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        try {
            var visited = 0
            while (queue.isNotEmpty() && visited < MAX_NODES) {
                val n = queue.removeFirst()
                if (n !== root) seen += n
                visited++
                if (match(n)) return true
                for (i in 0 until n.childCount) runCatching { n.getChild(i) }.getOrNull()?.let { queue.add(it) }
            }
            return false
        } finally {
            seen.forEach { recycle(it) }
            queue.forEach { if (it !== root) recycle(it) }
        }
    }

    /**
     * כל החלונות של [pkg] שעל המסך — התפריט של ⋮ נפתח בחלון נפרד (PopupWindow), שלא תמיד הוא "החלון
     * הפעיל". בלי הרשאה לרשימת החלונות — החלון הפעיל בלבד. העליון קודם; עוצרים בתוצאה הראשונה שאינה 0.
     */
    private fun withPkgWindows(pkg: String, action: (AccessibilityNodeInfo) -> Int): Int {
        val list = runCatching { windows }.getOrNull().orEmpty()
        val roots = list.mapNotNull { w -> runCatching { w.root }.getOrNull() }
        @Suppress("DEPRECATION")
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) list.forEach { runCatching { it.recycle() } }
        try {
            for (root in roots) {
                if (root.packageName?.toString() != pkg) continue
                val r = runCatching { action(root) }.getOrDefault(0)
                if (r != 0) return r
            }
        } finally {
            roots.forEach { recycle(it) }
        }
        if (roots.isNotEmpty()) return 0
        val root = runCatching { rootInActiveWindow }.getOrNull() ?: return 0
        return try {
            if (root.packageName?.toString() == pkg) runCatching { action(root) }.getOrDefault(0) else 0
        } finally {
            recycle(root)
        }
    }

    private fun <T> withActiveRoot(action: (AccessibilityNodeInfo) -> T): T? {
        val root = runCatching { rootInActiveWindow }.getOrNull() ?: return null
        return try {
            action(root)
        } catch (e: Exception) {
            null
        } finally {
            recycle(root)
        }
    }

    /** לוחץ על הרכיב הראשון שמתאים (לפי טקסט / תיאור), או על ההורה הלחיץ שלו. */
    private fun clickFirst(root: AccessibilityNodeInfo, match: (String?, String?) -> Boolean): Boolean {
        val seen = ArrayList<AccessibilityNodeInfo>()
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        try {
            var visited = 0
            while (queue.isNotEmpty() && visited < MAX_NODES) {
                val n = queue.removeFirst()
                if (n !== root) seen += n
                visited++
                if (match(n.text?.toString(), n.contentDescription?.toString())) return clickSelfOrParent(n)
                for (i in 0 until n.childCount) runCatching { n.getChild(i) }.getOrNull()?.let { queue.add(it) }
            }
            return false
        } finally {
            seen.forEach { recycle(it) }
            queue.forEach { if (it !== root) recycle(it) }
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
        runCatching { installOverlay.hide() }
        runCatching { playBadges.hide() }
        runCatching { chromeBubble.hide() }
        runCatching { avatarMask.hide() }
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
        runCatching { packageManager.packageInstaller.unregisterSessionCallback(gateSessions) }
        runCatching { PlayGate.close(this, "guard stopped") }
        runCatching { installOverlay.hide() }
        runCatching { playBadges.hide() }
        runCatching { chromeBubble.hide() }
        runCatching { avatarMask.hide() }
        super.onDestroy()
    }

    companion object {
        private const val TAG = "FilterPhone"
        private const val SCAN_EVERY_MS = 250L
        /** אחרי שהתקנה מאושרת התחילה — לא פותחים את השער שוב מיד (העמוד עוד מציג "התקנה" לרגע). */
        private const val GATE_QUIET_MS = 8_000L
        /** לחיצה על קישור בוואטסאפ — טרייה כל עוד הדפדפן הפנימי נפתח בתוך הזמן הזה. */
        private const val WA_CLICK_FRESH_MS = 10_000L
        /** כמה פעמים (כל WA_STEP_MS) מחכים שהמעטפת w.meta.me תפנה לאתר האמיתי. */
        private const val WA_SHIM_TRIES = 6
        /** אירוע של אפליקציה אחרת כשהכפתור הצף בכרום מוצג: כמה לחכות לפני שבודקים מה באמת בחזית. */
        private const val CHROME_LEFT_CHECK_MS = 450L
        private val PRIMARY_INSTALL = setOf("install")
        /** ל-block(): כבר טיפלנו בעצמנו (למשל לחצנו על טאב) — רק ההודעה, בלי פעולה גלובלית. */
        private const val NO_GLOBAL_ACTION = 0
        private const val MAX_NODES = 300
        private const val MAX_MASK_NODES = 600
        private const val MASK_EVERY_MS = 50L          // עדכון הכיסוי — לכל היותר 20 בשנייה
        private const val MASK_SCROLL_HOLD_MS = 650L   // אחרי אירוע הגלילה האחרון — עוד כמה זמן פסים
        private const val WA_STEP_MS = 250L           // הדפדפן הפנימי: בין ניסיון לניסיון
        private const val WA_MENU_TRIES = 12          // עד ~3 שניות עד שהסרגל שלו עולה
        private const val WA_ITEM_TRIES = 8           // עד ~2 שניות עד שהתפריט נפתח
        private const val WA_FLOW_MAX_MS = 10_000L    // העברה שנתקעה — לא חוסמת את הבאה
        private const val WA_RETURN_MS = 10 * 60_000L // חזרה מ-Chrome לדפדפן הפנימי — עד 10 דקות אחרי ההעברה
        /** אפליקציות ההגדרות (כולל הגדרות הנגישות של סמסונג). */
        private val SETTINGS_PACKAGES = setOf("com.android.settings", "com.samsung.accessibility",
            "com.samsung.android.accessibility", "com.coloros.accessibility")

        @Volatile private var instance: GuardService? = null

        val isRunning: Boolean get() = instance != null

        private const val PLAY_SCAN_MS = 300L
        private const val OVERLAY_CHECK_MS = 800L
        private const val FINISH_SHOW_MS = 1_500L
        private const val SYSTEM_UI = "com.android.systemui"
        private const val SERVER_CHECK_GAP_MS = 30_000L
        private val io = Executors.newSingleThreadExecutor { r -> Thread(r, "fp-guard-io").apply { isDaemon = true } }

        /** החנות פתחה חלון התקנה ב-Play — שינויי תוכן ב-Play נחוצים מעכשיו (מכל חוט). */
        fun onPlaySession() {
            val s = instance ?: return
            s.main.post {
                s.reconfigure()
                s.safely("play-overlay-now") { s.showInstallOverlayNow() }
            }
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
