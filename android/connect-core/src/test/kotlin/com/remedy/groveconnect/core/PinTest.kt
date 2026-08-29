package com.remedy.groveconnect.core

import kotlin.test.Test
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

class PinTest {
    private val a = ByteArray(32) { 1 }
    private val b = ByteArray(32) { 2 }

    @Test
    fun firstPairHasNoPin() {
        Pin.check(storedHostPub = null, candidateHostPub = a)
    }

    @Test
    fun matchingPinOk() {
        Pin.check(storedHostPub = a.copyOf(), candidateHostPub = a.copyOf())
    }

    @Test
    fun mismatchFailsClosed() {
        val e = assertFailsWith<PinMismatchException> {
            Pin.check(storedHostPub = a, candidateHostPub = b)
        }
        assertTrue(e.message!!.contains("not the PC"), e.message)
    }

    @Test
    fun oneByteFlipFailsClosed() {
        val flipped = a.copyOf()
        flipped[31] = (flipped[31].toInt() xor 1).toByte()
        assertFailsWith<PinMismatchException> {
            Pin.check(storedHostPub = a, candidateHostPub = flipped)
        }
    }

    @Test
    fun wrongLengthFailsClosed() {
        assertFailsWith<PinMismatchException> {
            Pin.check(storedHostPub = ByteArray(16) { 1 }, candidateHostPub = a)
        }
    }
}
