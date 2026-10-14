package com.filterphone.app

import android.annotation.SuppressLint
import android.content.Context
import android.provider.Settings
import java.security.MessageDigest

/**
 * מזהה יציב של הטלפון לשחזור לפי מספר טלפון: אותו טלפון אחרי הסרה והתקנה מחדש → אותו מזהה
 * (ההגנה חוזרת לבד); איפוס יצרן או טלפון אחר → מזהה אחר (החלפת מכשיר באישור המנהל).
 *
 * ANDROID_ID (אנדרואיד 8+) ייחודי לאפליקציה + משתמש + מכשיר, ומשתנה באיפוס. לא נשלח כמו שהוא —
 * רק hash עם שם החבילה, כך שאי אפשר לקשר אותו לאפליקציות אחרות.
 */
object DeviceId {
    @SuppressLint("HardwareIds")
    fun get(context: Context): String {
        val androidId = Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID).orEmpty()
        return of(androidId, context.packageName)
    }

    /** טהור — נבדק ביחידה. */
    fun of(androidId: String, pkg: String): String =
        MessageDigest.getInstance("SHA-256").digest("$androidId:$pkg".toByteArray())
            .joinToString("") { "%02x".format(it) }.take(32)
}
