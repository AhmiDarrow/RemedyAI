package com.remedy.groveconnect.api

import java.io.IOException
import java.io.StringReader
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.Assert.assertThrows

class BoundedLineReaderTest {
    @Test
    fun readsCrLfAndFinalUnterminatedLine() {
        BoundedLineReader(StringReader("one\r\ntwo"), 8).use { reader ->
            assertEquals("one", reader.readLine())
            assertEquals("two", reader.readLine())
            assertNull(reader.readLine())
        }
    }

    @Test
    fun rejectsNewlineFreeInputBeforeAllocatingPastLimit() {
        BoundedLineReader(StringReader("x".repeat(65)), 64).use { reader ->
            assertThrows(IOException::class.java) { reader.readLine() }
        }
    }
}
