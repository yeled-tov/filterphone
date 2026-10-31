package com.filterphone.crm

import android.content.Context
import android.util.Log

/** בדיקה אחת בשרת: אירועים חדשים → התראות. משותף לשירות ולבדיקה התקופתית (AlarmManager). */
object Poller {
    enum class Result { OK, NO_TOKEN, UNAUTHORIZED, ERROR }

    private val lock = Any()

    fun run(context: Context): Result = synchronized(lock) {
        val c = context.applicationContext
        val token = Crm.token(c) ?: return Result.NO_TOKEN
        val cursor = Crm.cursor(c)
        return try {
            val poll = Api.poll(token, cursor)
            // בדיקה ראשונה אחרי כניסה: רק נקודת התחלה — בלי להציף בהיסטוריה
            if (cursor != null) Notifier.show(c, poll.events)
            if (poll.cursor.isNotEmpty()) Crm.setCursor(c, poll.cursor)
            if (Crm.reloginShown(c)) {
                Crm.setReloginShown(c, false)
                Notifier.cancelRelogin(c)
            }
            Updates.maybeCheck(c)
            Result.OK
        } catch (e: Api.Unauthorized) {
            Notifier.relogin(c)
            Result.UNAUTHORIZED
        } catch (e: Exception) {
            Log.w(Crm.TAG, "poll failed: ${e.javaClass.simpleName}")
            Result.ERROR
        }
    }
}
