package com.filterphone.app

import org.junit.Assert.assertEquals
import org.junit.Test

class RootProvisionerTest {
    private fun c(s: String) = RootProvisioner.classifyDpmFailure(s)

    @Test
    fun classifiesCommonDpmFailures() {
        assertEquals("accounts_present",
            c("java.lang.IllegalStateException: Not allowed to set the device owner because there are already " +
                "some accounts on the device"))
        assertEquals("already_has_device_owner", c("Error: Already set up a device owner."))
        assertEquals("multiple_users", c("Error: there are already several users on the device"))
        assertEquals("admin_component_error", c("Error: Unknown admin: ComponentInfo{...}"))
        assertEquals("no_output", c("   "))
        assertEquals("dpm_failed", c("some other unexpected error"))
    }

    @Test
    fun provisioningBlockedMessageIsRecognised() {
        assertEquals("provisioning_blocked", c("Not allowed to set the device owner in the current state"))
    }
}
