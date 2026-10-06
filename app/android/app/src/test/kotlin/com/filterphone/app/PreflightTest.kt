package com.filterphone.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PreflightTest {
    private fun caps(
        sdk: Int = 34,
        deviceOwner: Boolean = false,
        profileOwner: Boolean = false,
        provisioningAllowed: Boolean = false,
        root: Boolean = false,
    ) = Preflight.Caps(
        sdk = sdk, manufacturer = "Google", model = "Pixel 8", deviceOwner = deviceOwner,
        profileOwner = profileOwner, provisioningAllowed = provisioningAllowed, rootAvailable = root,
        accessibilityEnabled = false, alwaysOnSupported = true, lockdownSupported = true,
    )

    @Test
    fun alreadyDeviceOwnerShortCircuits() {
        val (r, reasons) = Preflight.evaluate(caps(deviceOwner = true, provisioningAllowed = false))
        assertEquals(Preflight.Result.ALREADY_MANAGED, r)
        assertTrue("already_device_owner" in reasons)
    }

    @Test
    fun eligibleWithRootOffersOneTap() {
        val (r, reasons) = Preflight.evaluate(caps(provisioningAllowed = true, root = true))
        assertEquals(Preflight.Result.READY_WITH_ROOT, r)
        assertTrue("root_available" in reasons)
    }

    @Test
    fun eligibleWithoutRootOffersQrOrAdb() {
        val (r, _) = Preflight.evaluate(caps(provisioningAllowed = true, root = false))
        assertEquals(Preflight.Result.READY_FOR_PROVISIONING, r)
    }

    @Test
    fun provisioningBlockedNeedsFactoryReset() {
        val (r, reasons) = Preflight.evaluate(caps(provisioningAllowed = false))
        assertEquals(Preflight.Result.REQUIRES_FACTORY_RESET, r)
        assertTrue("provisioning_blocked" in reasons)
    }

    @Test
    fun oldAndroidUnsupported() {
        val (r, _) = Preflight.evaluate(caps(sdk = 23, provisioningAllowed = true, root = true))
        assertEquals(Preflight.Result.UNSUPPORTED, r)
    }

    @Test
    fun rootDoesNotOverrideAlreadyManaged() {
        // root + כבר מנהל מכשיר → עדיין ALREADY_MANAGED (לא מנסים להקים שוב)
        val (r, _) = Preflight.evaluate(caps(deviceOwner = true, provisioningAllowed = true, root = true))
        assertEquals(Preflight.Result.ALREADY_MANAGED, r)
    }
}
