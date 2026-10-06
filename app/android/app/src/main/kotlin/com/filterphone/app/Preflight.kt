package com.filterphone.app

import android.app.admin.DevicePolicyManager
import android.content.Context
import android.os.Build
import java.io.File

/**
 * אבחון טרום-הקמה: מה המכשיר הזה באמת תומך בו, ובאיזה מסלול אפשר להפוך אותו למנוהל.
 *
 * כל הזיהויים נשענים על API רשמי (DevicePolicyManager.isProvisioningAllowed הוא הבדיקה הרשמית
 * של אנדרואיד "האם אפשר עדיין להפוך למנהל מכשיר"). root מזוהה לפי קיום su — בלי להריץ שום פקודה
 * מprivilegeד כאן (ההרצה היא רק בהסכמה מפורשת, ב-RootProvisioner). לא נעשה שום exploit/מעקף.
 *
 * ההערכה עצמה (evaluate) טהורה — מקבלת snapshot ונבדקת בבדיקות יחידה.
 */
object Preflight {

    enum class Result {
        ALREADY_MANAGED,          // כבר מנהל מכשיר — אין מה להקים
        READY_WITH_ROOT,          // כשיר, ויש root → הקמה בלחיצה אפשרית
        READY_FOR_PROVISIONING,   // כשיר (isProvisioningAllowed) → QR / adb (טכנאי)
        REQUIRES_FACTORY_RESET,   // כבר מאותחל/יש חשבונות → צריך איפוס להקמה כמנהל מכשיר
        UNSUPPORTED,              // גרסת אנדרואיד / יצרן לא תומכים
    }

    /** תמונת מצב של יכולות המכשיר. רק עובדות — בלי מסקנות. */
    data class Caps(
        val sdk: Int,
        val manufacturer: String,
        val model: String,
        val deviceOwner: Boolean,
        val profileOwner: Boolean,
        val provisioningAllowed: Boolean,   // DevicePolicyManager.isProvisioningAllowed(PROVISION_MANAGED_DEVICE)
        val rootAvailable: Boolean,
        val accessibilityEnabled: Boolean,
        val alwaysOnSupported: Boolean,
        val lockdownSupported: Boolean,
    ) {
        fun toMap(): Map<String, Any> = mapOf(
            "sdk" to sdk, "manufacturer" to manufacturer, "model" to model,
            "device_owner" to deviceOwner, "profile_owner" to profileOwner,
            "provisioning_allowed" to provisioningAllowed, "root" to rootAvailable,
            "accessibility" to accessibilityEnabled, "always_on" to alwaysOnSupported,
            "lockdown" to lockdownSupported,
        )
    }

    private const val MIN_SDK = Build.VERSION_CODES.O   // 26 — minSdk של האפליקציה

    /** ההערכה — טהורה. מחזירה את המסלול ואת הסיבות (מפתחות קבועים, לתרגום בצד ה-UI). */
    fun evaluate(c: Caps): Pair<Result, List<String>> {
        val reasons = mutableListOf<String>()
        if (c.deviceOwner) return Result.ALREADY_MANAGED to listOf("already_device_owner")
        if (c.sdk < MIN_SDK) return Result.UNSUPPORTED to listOf("android_too_old")
        if (c.profileOwner) reasons += "profile_owner_present"
        if (c.provisioningAllowed) {
            return if (c.rootAvailable) Result.READY_WITH_ROOT to (reasons + "root_available")
            else Result.READY_FOR_PROVISIONING to (reasons + "provisioning_allowed")
        }
        // אנדרואיד מסרב להקמה כמנהל מכשיר — כמעט תמיד בגלל חשבון/הקמה קיימים
        reasons += "provisioning_blocked"
        return Result.REQUIRES_FACTORY_RESET to reasons
    }

    /** היכולות בפועל של המכשיר (קריאות API; בלי הרצת פקודות). */
    fun capabilities(context: Context): Caps {
        val dpm = context.getSystemService(DevicePolicyManager::class.java)
        val deviceOwner = runCatching { dpm?.isDeviceOwnerApp(context.packageName) == true }.getOrDefault(false)
        val profileOwner = runCatching { dpm?.isProfileOwnerApp(context.packageName) == true }.getOrDefault(false)
        val provisioningAllowed = runCatching {
            dpm?.isProvisioningAllowed(DevicePolicyManager.ACTION_PROVISION_MANAGED_DEVICE) == true
        }.getOrDefault(false)
        return Caps(
            sdk = Build.VERSION.SDK_INT,
            manufacturer = Build.MANUFACTURER ?: "",
            model = Build.MODEL ?: "",
            deviceOwner = deviceOwner,
            profileOwner = profileOwner,
            provisioningAllowed = provisioningAllowed,
            rootAvailable = rootAvailable(),
            accessibilityEnabled = runCatching { GuardPolicy.isServiceEnabled(context) }.getOrDefault(false),
            alwaysOnSupported = Build.VERSION.SDK_INT >= Build.VERSION_CODES.N,
            lockdownSupported = Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q,
        )
    }

    /** האם קיים su (root). בדיקת נוכחות בלבד — לא מריצים שום דבר כאן. */
    fun rootAvailable(): Boolean {
        val paths = arrayOf(
            "/sbin/su", "/system/bin/su", "/system/xbin/su", "/vendor/bin/su",
            "/su/bin/su", "/system/sbin/su", "/data/local/xbin/su", "/data/local/bin/su",
        )
        return runCatching { paths.any { File(it).exists() } }.getOrDefault(false) ||
            (Build.TAGS?.contains("test-keys") == true)
    }

    /** למעטפת המתודה: {capabilities, result, reasons}. */
    fun report(context: Context): Map<String, Any> {
        val caps = capabilities(context)
        val (result, reasons) = evaluate(caps)
        return mapOf("capabilities" to caps.toMap(), "result" to result.name, "reasons" to reasons)
    }
}
