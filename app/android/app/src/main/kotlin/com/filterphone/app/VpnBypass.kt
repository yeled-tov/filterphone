package com.filterphone.app

import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import org.json.JSONArray
import org.json.JSONObject
import java.security.MessageDigest

/**
 * אפליקציות שעוברות מחוץ ל-VPN (פיצול מנהרה לפי אפליקציה) — לפי הרשימה במדיניות מהשרת.
 *
 * למה: FilterTube (שתי הגרסאות) מתחברת ליוטיוב ישירות מהטלפון. דרך ה-VPN יוטיוב רואה כתובת של שרת
 * ומסרב לעבוד; מחוץ ל-VPN היא משתמשת בחיבור הרגיל של הטלפון.
 * המחיר: אפליקציה מוחרגת לא עוברת שום סינון של FilterPhone (גם לא DNS) — לכן רק לפי שם חבילה מדויק,
 * ואם המנהל נעל חתימה — רק כשתעודת החתימה של האפליקציה המותקנת תואמת (אחרת היא נשארת בסינון).
 *
 * במנהל מכשיר עם "חסימה בלי VPN" (lockdown) האפליקציות המוחרגות נכנסות גם לרשימת ההיתר של אנדרואיד
 * (אנדרואיד 10+). באנדרואיד 9 ומטה אין רשימה כזו — הן יישארו בלי רשת, וזה מדווח לשרת.
 */
object VpnBypass {
    /** זהה לברירת המחדל בשרת (VPN_BYPASS_APPS) — לשרת שעוד לא שולח את השדה. */
    val DEFAULTS: List<App> = listOf(App("com.filtertube.app"), App("com.filtertube.filtertube"))
    private const val MAX = 20
    private val SHA = Regex("^[0-9a-f]{64}$")
    private val PKG = Regex("^[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z0-9_]+)+$")

    data class App(val pkg: String, val certs: Set<String> = emptySet())

    data class Decision(
        val excluded: Set<String>,          // יוצאות מחוץ ל-VPN
        val mismatch: Set<String>,          // מותקנות, אבל החתימה לא תואמת לנעילה — נשארות בסינון
        val certs: Map<String, String>,     // החתימה הנוכחית של כל אפליקציה מהרשימה שמותקנת (לדיווח)
    ) {
        companion object {
            val NONE = Decision(emptySet(), emptySet(), emptyMap())
        }
    }

    /**
     * מהמדיניות: null/חסר = ברירת המחדל; {"enabled": false} = כלום. טהור — נבדק ביחידה.
     * שם חבילה לא תקין או טביעה לא תקינה — מדלגים (בלי לבטל את השאר).
     */
    fun parse(raw: Any?): List<App> {
        val o = raw as? JSONObject ?: return DEFAULTS
        if (!o.optBoolean("enabled", true)) return emptyList()
        val a = o.optJSONArray("apps") ?: return DEFAULTS
        val out = ArrayList<App>()
        for (i in 0 until minOf(a.length(), MAX)) {
            val item = a.optJSONObject(i) ?: continue
            val pkg = item.optString("package")
            if (!PKG.matches(pkg)) continue
            val certs = item.optJSONArray("certs")?.let { c ->
                (0 until c.length()).map { c.optString(it).lowercase() }.filter { SHA.matches(it) }.toSet()
            } ?: emptySet()
            out += App(pkg, certs)
        }
        return out
    }

    /**
     * מה מוחרג. installed = חבילה → טביעות החתימה שלה (הנוכחית אחרונה), רק למה שמותקן. טהור.
     * עם נעילה: מספיקה התאמה לאחת מהחתימות שהאפליקציה הוכיחה (כולל היסטוריית החלפת מפתח).
     */
    fun decide(apps: List<App>, installed: Map<String, List<String>>, self: String): Decision {
        val excluded = LinkedHashSet<String>()
        val mismatch = LinkedHashSet<String>()
        val certs = LinkedHashMap<String, String>()
        for (a in apps) {
            if (a.pkg == self) continue
            val signers = installed[a.pkg] ?: continue
            signers.lastOrNull()?.let { certs[a.pkg] = it }
            if (a.certs.isNotEmpty() && signers.none { it in a.certs }) mismatch += a.pkg else excluded += a.pkg
        }
        return Decision(excluded, mismatch, certs)
    }

    /** ההחלטה לפי המדיניות השמורה והאפליקציות שמותקנות עכשיו. */
    fun current(context: Context): Decision {
        val apps = GuardPolicy.get(context).vpnBypass
        if (apps.isEmpty()) return Decision.NONE
        val installed = HashMap<String, List<String>>()
        for (a in apps) signerDigests(context, a.pkg)?.let { installed[a.pkg] = it }
        return decide(apps, installed, context.packageName)
    }

    /** האם החבילה ברשימה (כדי להגיב רק להתקנה/הסרה שרלוונטית). */
    fun listed(context: Context, pkg: String): Boolean = GuardPolicy.get(context).vpnBypass.any { it.pkg == pkg }

    /** SHA-256 של תעודות החתימה (הנוכחית אחרונה). null = לא מותקנת. */
    fun signerDigests(context: Context, pkg: String): List<String>? {
        val pm = context.packageManager
        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                val si = pm.getPackageInfo(pkg, PackageManager.GET_SIGNING_CERTIFICATES).signingInfo
                    ?: return emptyList()
                val sigs = if (si.hasMultipleSigners()) si.apkContentsSigners else si.signingCertificateHistory
                sigs.orEmpty().map { sha256(it.toByteArray()) }
            } else {
                @Suppress("DEPRECATION")
                val sigs = pm.getPackageInfo(pkg, PackageManager.GET_SIGNATURES).signatures
                sigs.orEmpty().map { sha256(it.toByteArray()) }
            }
        } catch (_: PackageManager.NameNotFoundException) {
            null
        }
    }

    /** לדיווח לשרת: מה מוחרג בפועל, מה לא הותאם, החתימות (ציבוריות — בכל APK), והתנגשות עם lockdown. */
    fun report(applied: Set<String>, decision: Decision, lockdownConflict: Boolean): JSONObject =
        JSONObject()
            .put("applied", JSONArray(applied.toList()))
            .put("mismatch", JSONArray(decision.mismatch.toList()))
            .put("certs", JSONObject(decision.certs))
            .put("lockdown_conflict", lockdownConflict)

    private fun sha256(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
}
