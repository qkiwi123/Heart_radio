package com.example.heart_radio

import org.junit.Assert.*
import org.junit.Test

class LocationWindowTest {
    @Test fun oldAndFutureLocationsCannotSatisfyNewRequest() {
        assertFalse(LocationWindow.isFresh(99, 100, 120, 200))
        assertFalse(LocationWindow.isFresh(121, 100, 120, 200))
        assertTrue(LocationWindow.isFresh(100, 100, 120, 200))
        assertTrue(LocationWindow.isFresh(120, 100, 120, 200))
    }
    @Test fun fixReceivedAtOrAfterDeadlineIsRejected() {
        assertFalse(LocationWindow.isFresh(150, 100, 200, 200))
        assertFalse(LocationWindow.isFresh(150, 100, 201, 200))
        assertTrue(LocationWindow.isFresh(150, 100, 199, 200))
    }
}
