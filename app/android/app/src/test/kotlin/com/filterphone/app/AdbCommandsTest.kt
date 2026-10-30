package com.filterphone.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AdbCommandsTest {
    private val pkg = "com.filterphone.app"

    @Test
    fun accountAppsExcludeOurselvesAndFrameworkAndDeduplicate() {
        val authenticators = listOf(
            "com.google.android.gms", "com.google.android.gms",   // כפילות
            "com.whatsapp.w4b", "android", pkg, "",
        )
        val chosen = AdbCommands.accountPackages(authenticators, pkg)
        assertEquals(listOf("com.google.android.gms", "com.whatsapp.w4b"), chosen)   // ממוין, בלי כפילות/מסגרת/עצמנו
        assertEquals(emptyList<String>(), AdbCommands.accountPackages(emptyList(), pkg))
        assertEquals(emptyList<String>(), AdbCommands.accountPackages(listOf("android", pkg), pkg))
    }

    @Test
    fun accountTrickDisablesRunsAndReEnablesInOneCommand() {
        val cmd = AdbCommands.accountTrick(pkg, listOf("com.google.android.gms", "com.google.android.gsf"))
        // משביתים, מריצים set-device-owner, ומדליקים בחזרה — הכול בפקודה אחת (אטומית ב-adbd)
        assertTrue(cmd.contains("pm disable-user --user 0"))
        assertTrue(cmd.contains("dpm set-device-owner $pkg/.DeviceAdmin"))
        assertTrue(cmd.contains("pm enable"))
        assertTrue(cmd.indexOf("disable-user") < cmd.indexOf("set-device-owner"))
        assertTrue(cmd.indexOf("set-device-owner") < cmd.indexOf("pm enable"))
        assertTrue(cmd.contains("com.google.android.gms"))
        assertTrue(cmd.contains("com.google.android.gsf"))
        // בלי אפליקציות חשבון — פקודה פשוטה
        assertEquals(AdbCommands.setDeviceOwner(pkg), AdbCommands.accountTrick(pkg, emptyList()))
    }

    @Test
    fun blockedByAccountsMatchesAndroidWording() {
        assertTrue(AdbCommands.blockedByAccounts(
            "java.lang.IllegalStateException: Not allowed to set the device owner because there are already " +
                "some accounts on the device"))
        assertFalse(AdbCommands.blockedByAccounts("Success: Device owner set to package com.filterphone.app"))
        assertFalse(AdbCommands.blockedByAccounts("already set up a device owner"))
    }

    @Test
    fun succeededReadsEitherTheDpmMessageOrTheTrickReturnCode() {
        assertTrue(AdbCommands.succeeded("Success: Device owner set to package ComponentInfo{...}"))
        assertTrue(AdbCommands.succeeded("...\nFP_RC=0"))
        assertFalse(AdbCommands.succeeded("FP_RC=1"))
        assertFalse(AdbCommands.succeeded("some error"))
    }

    @Test
    fun followupsCoverSecureSettingsBatteryVpnAndNotifications() {
        val owner = AdbCommands.followups(pkg, 34, owner = true)
        assertTrue(owner.any { it.contains("WRITE_SECURE_SETTINGS") })
        assertTrue(owner.any { it.contains("deviceidle whitelist +$pkg") })
        assertTrue(owner.any { it.contains("POST_NOTIFICATIONS") })      // אנדרואיד 13+
        assertFalse(owner.any { it.contains("ACTIVATE_VPN") })            // מנהל מכשיר — לא צריך appop ל-VPN
        // לא מנהל מכשיר (למשל root בלבד) ואנדרואיד 12 — יש ACTIVATE_VPN, אין POST_NOTIFICATIONS
        val notOwner = AdbCommands.followups(pkg, 31, owner = false)
        assertTrue(notOwner.any { it.contains("ACTIVATE_VPN") })
        assertFalse(notOwner.any { it.contains("POST_NOTIFICATIONS") })
    }

    @Test
    fun accountTypesReadOnlyTheOwnerTypesWithoutNames() {
        val dumpsys = """
            User UserInfo{0:Owner:c13}:
              Accounts: 3
                Account {name=someone@gmail.com, type=com.google}
                Account {name=someone@gmail.com, type=com.google}
                Account {name=x@samsung.com, type=com.osp.app.signin}
              AccountId, Action_Type, timestamp, UID, TableName, Key
            User UserInfo{150:Secure Folder:1030}:
              Accounts: 1
                Account {name=y, type=com.whatsapp}
        """.trimIndent()
        assertEquals(listOf("com.google", "com.osp.app.signin"), AdbCommands.accountTypes(dumpsys))
        assertEquals(emptyList<String>(), AdbCommands.accountTypes("Accounts: 0"))
    }

    @Test
    fun extraUsersListsEverythingButTheOwner() {
        val out = "Users:\n\tUserInfo{0:Owner:c13} running\n\tUserInfo{95:DUAL_APP:2000030}\n\tUserInfo{150:Secure Folder:1030}"
        assertEquals(listOf("DUAL_APP", "Secure Folder"), AdbCommands.extraUsers(out))
        assertEquals(emptyList<String>(), AdbCommands.extraUsers("Users:\n\tUserInfo{0:Owner:c13} running"))
    }
}
