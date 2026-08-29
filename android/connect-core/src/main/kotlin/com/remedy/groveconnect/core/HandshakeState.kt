package com.remedy.groveconnect.core

/**
 * Noise_IK initiator (phone) and responder (host, for tests / Python sibling).
 *
 * First handshake payload MUST be the 32-byte pair secret. The responder
 * constant-time compares it and fails closed on mismatch.
 */
class HandshakeState private constructor(
    private val initiator: Boolean,
    prologue: ByteArray,
    sPrivIn: ByteArray,
    sPubIn: ByteArray,
    remoteStatic: ByteArray?,
    private val expectedPairSecret: ByteArray?,
) {
    private val sPriv: ByteArray = sPrivIn.copyOf()
    private val sPub: ByteArray = sPubIn.copyOf()
    private val ss = SymmetricState(Protocol.NAME)
    private var ePriv: ByteArray? = null
    private var ePub: ByteArray? = null
    private var re: ByteArray? = null
    private var rs: ByteArray? = remoteStatic?.copyOf()
    private var messageIndex = 0
    var complete: Boolean = false
        private set
    private lateinit var send: CipherState
    private lateinit var recv: CipherState
    var handshakeHash: ByteArray = ByteArray(0)
        private set
    var remoteStaticPublic: ByteArray? = rs?.copyOf()
        private set

    init {
        require(sPriv.size == Protocol.KEY_LEN && sPub.size == Protocol.KEY_LEN)
        ss.mixHash(prologue)
        // IK pre-message: <- s
        if (initiator) {
            val remote = rs ?: throw NoiseException("initiator needs host static")
            ss.mixHash(remote)
        } else {
            ss.mixHash(sPub)
        }
    }

    /** Test hook: force the ephemeral private key (Noise test vectors). */
    fun setEphemeralForTest(priv: ByteArray) {
        ePriv = priv.copyOf()
        ePub = Crypto.x25519Public(priv)
    }

    fun writeMessage(payload: ByteArray = ByteArray(0)): ByteArray {
        val out = ArrayList<ByteArray>(4)
        if (initiator && messageIndex == 0) {
            // -> e, es, s, ss
            val p = if (payload.isEmpty() && expectedPairSecret != null) expectedPairSecret else payload
            ensureEphemeral()
            out += ePub!!
            ss.mixHash(ePub!!)
            ss.mixKey(Crypto.x25519Dh(ePriv!!, rs!!))
            out += ss.encryptAndHash(sPub)
            ss.mixKey(Crypto.x25519Dh(sPriv, rs!!))
            out += ss.encryptAndHash(p)
            messageIndex = 1
        } else if (!initiator && messageIndex == 1) {
            // <- e, ee, se
            // se is direction-sensitive: responder MixKey(DH(e, rs)).
            ensureEphemeral()
            out += ePub!!
            ss.mixHash(ePub!!)
            ss.mixKey(Crypto.x25519Dh(ePriv!!, re!!))
            ss.mixKey(Crypto.x25519Dh(ePriv!!, rs!!))
            out += ss.encryptAndHash(payload)
            finish()
        } else {
            throw NoiseException("writeMessage in wrong state")
        }
        return concat(out)
    }

    fun readMessage(message: ByteArray): ByteArray {
        var off = 0
        fun take(n: Int): ByteArray {
            if (off + n > message.size) throw NoiseException("short handshake")
            val slice = message.copyOfRange(off, off + n)
            off += n
            return slice
        }
        if (!initiator && messageIndex == 0) {
            // read -> e, es, s, ss
            val e = take(32)
            re = e
            ss.mixHash(e)
            ss.mixKey(Crypto.x25519Dh(sPriv, e))
            val encS = take(32 + Protocol.TAG_LEN)
            val remoteS = ss.decryptAndHash(encS)
            rs = remoteS
            remoteStaticPublic = remoteS.copyOf()
            ss.mixKey(Crypto.x25519Dh(sPriv, remoteS))
            val rest = message.copyOfRange(off, message.size)
            val payload = ss.decryptAndHash(rest)
            verifyPairSecret(payload)
            messageIndex = 1
            return payload
        } else if (initiator && messageIndex == 1) {
            val e = take(32)
            re = e
            ss.mixHash(e)
            ss.mixKey(Crypto.x25519Dh(ePriv!!, e))
            ss.mixKey(Crypto.x25519Dh(sPriv, e))
            val rest = message.copyOfRange(off, message.size)
            val payload = ss.decryptAndHash(rest)
            finish()
            return payload
        } else {
            throw NoiseException("readMessage in wrong state")
        }
    }

    fun sendCipher(): CipherState {
        check(complete) { "handshake not finished" }
        return send
    }

    fun recvCipher(): CipherState {
        check(complete) { "handshake not finished" }
        return recv
    }

    private fun finish() {
        val (c1, c2) = ss.split()
        if (initiator) {
            send = c1
            recv = c2
        } else {
            send = c2
            recv = c1
        }
        handshakeHash = ss.handshakeHash()
        complete = true
    }

    private fun ensureEphemeral() {
        if (ePriv == null) {
            val (priv, pub) = Crypto.generateX25519()
            ePriv = priv
            ePub = pub
        }
    }

    private fun verifyPairSecret(payload: ByteArray) {
        val expected = expectedPairSecret ?: return
        if (payload.size != Protocol.KEY_LEN || !Crypto.constantTimeEquals(payload, expected)) {
            throw NoiseException("pair secret mismatch")
        }
    }

    companion object {
        fun initiator(
            localStaticPriv: ByteArray,
            localStaticPub: ByteArray,
            hostStaticPub: ByteArray,
            pairSecret: ByteArray? = null,
            prologue: ByteArray = Protocol.PROLOGUE.toByteArray(Charsets.UTF_8),
        ): HandshakeState {
            if (pairSecret != null) require(pairSecret.size == Protocol.KEY_LEN)
            require(hostStaticPub.size == Protocol.KEY_LEN)
            return HandshakeState(
                initiator = true,
                prologue = prologue,
                sPrivIn = localStaticPriv,
                sPubIn = localStaticPub,
                remoteStatic = hostStaticPub,
                expectedPairSecret = pairSecret,
            )
        }

        fun responder(
            localStaticPriv: ByteArray,
            localStaticPub: ByteArray,
            pairSecret: ByteArray?,
            prologue: ByteArray = Protocol.PROLOGUE.toByteArray(Charsets.UTF_8),
        ): HandshakeState {
            return HandshakeState(
                initiator = false,
                prologue = prologue,
                sPrivIn = localStaticPriv,
                sPubIn = localStaticPub,
                remoteStatic = null,
                expectedPairSecret = pairSecret,
            )
        }
    }
}

private fun concat(parts: List<ByteArray>): ByteArray {
    val n = parts.sumOf { it.size }
    val out = ByteArray(n)
    var o = 0
    for (p in parts) {
        System.arraycopy(p, 0, out, o, p.size)
        o += p.size
    }
    return out
}
