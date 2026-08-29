package com.remedy.groveconnect.core

import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

class NoiseIkTest {
    private fun hex(s: String): ByteArray {
        val clean = s.trim()
        return ByteArray(clean.length / 2) { i ->
            clean.substring(i * 2, i * 2 + 2).toInt(16).toByte()
        }
    }

    private fun ByteArray.toHex(): String = joinToString("") { b ->
        val v = b.toInt() and 0xff
        val hex = Integer.toHexString(v)
        if (hex.length == 1) "0$hex" else hex
    }

    @Test
    fun generateKeypairRoundtrips() {
        val (priv, pub) = Crypto.generateX25519()
        assertContentEquals(pub, Crypto.x25519Public(priv))
    }

    @Test
    fun debugHash() {
        val name = Protocol.NAME.toByteArray()
        val h0 = if (name.size == 32) name else Crypto.blake2s(name)
        kotlin.test.assertEquals("bbea022b948cf3bc5857d70804229179e1116bc40cb8cc074835349c464bca36", h0.toHex(), "h0")
        val prologue = hex("5468657265206973206e6f20726967687420616e642077726f6e672e2054686572652773206f6e6c792066756e20616e6420626f72696e672e")
        val h1 = Crypto.blake2s(h0, prologue)
        kotlin.test.assertEquals("5bb156750593ca8a20dec81f2208ac09fe9639e6989e5f56bcff5e5a198361f3", h1.toHex(), "h prologue")
        val initRs = hex("ea82fd2e81d1285f1b2029e46ca7bcaeeeafed15396d002bd434624a4d580655")
        val h2 = Crypto.blake2s(h1, initRs)
        kotlin.test.assertEquals("b2e7409ee8575150d9b8d176cc326a68c7581746a52147a172b8a73882aa60a2", h2.toHex(), "h rs")
        val initEph = hex("cc95b4ccc4912c5a52c8d2f6b808e13712392c4468f4e3f02a7d2d1590cb9178")
        val ePub = Crypto.x25519Public(initEph)
        val h3 = Crypto.blake2s(h2, ePub)
        kotlin.test.assertEquals("62282308202dee87b14acbf965f46af4718f7e119bf791651f4300c5de8ecc7b", h3.toHex(), "h e")
        val dh = Crypto.x25519Dh(initEph, initRs)
        val hk = Crypto.hkdf(h0, dh, 2)
        kotlin.test.assertEquals("a7b2e46735a1aac6374af349a6108212f1de879c35ccd1ae445a8c3e20462015", hk[1].toHex(), "k after es")
        val initStatic = hex("b7e117ce8ede06ceb89500799a3778d097fc54a3f90bea744493dfc24ec21f32")
        val sPub = Crypto.x25519Public(initStatic)
        val encS = Crypto.aeadEncrypt(hk[1], 0L, h3, sPub)
        kotlin.test.assertEquals(
            "e818de301c97393bfa71ac250becfc8acef64a969e039f407fe44c5ffc3d66d94ff57627ad4cc53882a1b2993babac39",
            encS.toHex(),
            "enc s",
        )
    }

    @Test
    fun snowVectorIkChaChaBlake2s() {
        // snow tests/vectors — Noise_IK_25519_ChaChaPoly_BLAKE2s
        val initStatic = hex("b7e117ce8ede06ceb89500799a3778d097fc54a3f90bea744493dfc24ec21f32")
        val initEph = hex("cc95b4ccc4912c5a52c8d2f6b808e13712392c4468f4e3f02a7d2d1590cb9178")
        val initRemoteStatic = hex("ea82fd2e81d1285f1b2029e46ca7bcaeeeafed15396d002bd434624a4d580655")
        val respStatic = hex("e3187f5c10734e934be3a73c398c7fae07ba3e0cde2fcd9ab03a1c93dcae10f8")
        val respEph = hex("587f83fe736432043d2665fbd47b0506b2cd103b6ba8577f72e117c7ffeb5105")
        val prologue = hex(
            "5468657265206973206e6f20726967687420616e642077726f6e672e2054686572652773206f6e6c792066756e20616e6420626f72696e672e",
        )
        val msg0Payload = hex("910ef4a7c04090f66403fcd8ffaed066e70ed38b576792c4a554cc5016fe5120")
        val msg0Ct = hex(
            "df44a475167152d3e4767b9ad5b28468fb8bd6aaa0b0e7181afd87ede7a8c971e818de301c97393bfa71ac250becfc8acef64a969e039f407fe44c5ffc3d66d94ff57627ad4cc53882a1b2993babac3940edd1eaff34a3d3f08c53570166b3d29d06bd5de3cfb9b85b66001a60a5bb98d72b80da9485655f86db72b46f745c4e",
        )
        val msg1Payload = hex("3ce1e4d6e5f02bfeea1d6620cbf1473b5f55372d6954e98d3e12b6ffd04879d5")
        val msg1Ct = hex(
            "2818be348d38de5f9e3ef545c62e8e276c12e2a64410801a0284e02dfb0d450a848d1001977ef31dfe04b5c824acc04c7bda8490eab6cb6aaa844c4ddf0fda83ec84ed26cd6bee22fab17360b73de5b8",
        )
        val msg2Payload = hex("31e7554ebce419a76bf5cd464e00594ccf55bdc09c234c450850d26ab164238c")
        val msg2Ct = hex(
            "89545eb9b2477d482156692214def7d5aecc00b9244b4435bdb768173841d2a2a2e52bcee32d64fe938c87594b17ee69",
        )
        val msg3Payload = hex("794f643ba08cc7ee7aa39bc055560a0850dbc77bf34d8ab22019bf160ab2c573")
        val msg3Ct = hex(
            "fe35983c049b535cf9ca61cfad5b48e461da90c7c51cb5b873be10e58ae34a384aaebba7f96958b0ef0ac043c5e9c622",
        )

        assertContentEquals(initRemoteStatic, Crypto.x25519Public(respStatic))

        val init = HandshakeState.initiator(
            localStaticPriv = initStatic,
            localStaticPub = Crypto.x25519Public(initStatic),
            hostStaticPub = initRemoteStatic,
            pairSecret = null,
            prologue = prologue,
        )
        init.setEphemeralForTest(initEph)
        val resp = HandshakeState.responder(
            localStaticPriv = respStatic,
            localStaticPub = Crypto.x25519Public(respStatic),
            pairSecret = null,
            prologue = prologue,
        )
        resp.setEphemeralForTest(respEph)

        val m0 = init.writeMessage(msg0Payload)
        assertContentEquals(msg0Ct, m0)
        assertContentEquals(msg0Payload, resp.readMessage(m0))

        val m1 = resp.writeMessage(msg1Payload)
        assertContentEquals(msg1Ct, m1)
        assertContentEquals(msg1Payload, init.readMessage(m1))

        assertTrue(init.complete && resp.complete)
        assertContentEquals(msg2Ct, init.sendCipher().encryptWithAd(ByteArray(0), msg2Payload))
        assertContentEquals(msg2Payload, resp.recvCipher().decryptWithAd(ByteArray(0), msg2Ct))
        assertContentEquals(msg3Ct, resp.sendCipher().encryptWithAd(ByteArray(0), msg3Payload))
        assertContentEquals(msg3Payload, init.recvCipher().decryptWithAd(ByteArray(0), msg3Ct))
    }

    @Test
    fun pairSecretRoundtripAndWrongPsFailsClosed() {
        val (phonePriv, phonePub) = Crypto.generateX25519()
        val (hostPriv, hostPub) = Crypto.generateX25519()
        val ps = ByteArray(32) { 7 }
        val prologue = Protocol.PROLOGUE.toByteArray()

        val init = HandshakeState.initiator(phonePriv, phonePub, hostPub, ps, prologue)
        val resp = HandshakeState.responder(hostPriv, hostPub, ps, prologue)
        val msg1 = init.writeMessage()
        assertContentEquals(ps, resp.readMessage(msg1))
        val msg2 = resp.writeMessage()
        init.readMessage(msg2)

        val rec = RecordCodec.encodeTransport(init.sendCipher(), "hello".toByteArray())
        val body = rec.copyOfRange(4, rec.size)
        val pt = RecordCodec.decodeTransport(resp.recvCipher(), body)
        assertContentEquals("hello".toByteArray(), pt)

        val initBad = HandshakeState.initiator(phonePriv, phonePub, hostPub, ps, prologue)
        val respBad = HandshakeState.responder(hostPriv, hostPub, ByteArray(32) { 9 }, prologue)
        val m = initBad.writeMessage()
        assertFailsWith<NoiseException> { respBad.readMessage(m) }
    }

    @Test
    fun urlSafeB64RoundtripMatchesPythonStyle() {
        val raw = ByteArray(32) { it.toByte() }
        val enc = UrlSafeB64.encode(raw)
        assertTrue('=' !in enc)
        assertTrue('+' !in enc && '/' !in enc)
        assertContentEquals(raw, UrlSafeB64.decode(enc))
        val padded = java.util.Base64.getUrlEncoder().encodeToString(raw)
        assertContentEquals(raw, UrlSafeB64.decode(padded))
    }
}
