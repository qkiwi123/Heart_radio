package com.example.heart_radio
import org.junit.Assert.*
import org.junit.Test
class TransportSelectionTest {
    private val first="a".repeat(32)
    private val second="b".repeat(32)
    @Test fun onlyModeCannotBeBypassedByOtherAuthenticatedChannel(){
        val route=TransportSelection()
        assertFalse(route.select("rfcomm","wifi",first));assertNull(route.transport)
    }
    @Test fun oldDisconnectCannotReleaseReplacement(){
        val route=TransportSelection()
        route.select("auto_bluetooth","rfcomm",first);route.select("auto_bluetooth","wifi",second)
        assertFalse(route.release("rfcomm",first));assertTrue(route.owns("wifi",second))
        assertFalse(route.release("wifi",first));assertTrue(route.release("wifi",second));assertNull(route.transport)
    }
    @Test(expected=IllegalArgumentException::class) fun invalidIdentityNeverSelects(){TransportSelection().select("auto_wifi","wifi","old")}
}
