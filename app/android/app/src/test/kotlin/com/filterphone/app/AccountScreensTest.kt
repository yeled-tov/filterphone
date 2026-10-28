package com.filterphone.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** מסך הוספת חשבון Google — מזוהה כדי לבקש את המדיניות מיד (שירותי Google מחוץ ל-VPN כשהמנהל התיר). */
class AccountScreensTest {
    @Test
    fun recognizesTheAddAccountScreens() {
        assertTrue(AccountScreens.isAddAccount("com.android.settings", "com.android.settings.accounts.AddAccountSettings"))
        assertTrue(AccountScreens.isAddAccount("com.android.settings", "com.android.settings.accounts.ChooseAccountActivity"))
        assertTrue(AccountScreens.isAddAccount("com.google.android.gms",
            "com.google.android.gms.auth.uiflows.minutemaid.MinuteMaidActivity"))
        assertTrue(AccountScreens.isAddAccount("com.samsung.android.settings", "x.accounts.AddAccountSettings"))
    }

    @Test
    fun ignoresEverythingElse() {
        assertFalse(AccountScreens.isAddAccount("com.android.settings", "com.android.settings.Settings\$WifiSettingsActivity"))
        assertFalse(AccountScreens.isAddAccount("com.android.chrome", "org.chromium.chrome.browser.AddAccountThing"))
        assertFalse(AccountScreens.isAddAccount("com.google.android.gms", null))
        assertFalse(AccountScreens.isAddAccount(null, "AddAccount"))
    }
}
