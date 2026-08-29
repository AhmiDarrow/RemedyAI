package com.remedy.groveconnect.core

internal class SymmetricState(protocolName: String) {
    private var ck: ByteArray
    private var h: ByteArray
    private val cipher = CipherState(null)

    init {
        val name = protocolName.toByteArray(Charsets.UTF_8)
        h = if (name.size == Protocol.HASH_LEN) {
            name.copyOf()
        } else if (name.size < Protocol.HASH_LEN) {
            name.copyOf(Protocol.HASH_LEN)
        } else {
            Crypto.blake2s(name)
        }
        ck = h.copyOf()
    }

    fun mixHash(data: ByteArray) {
        h = Crypto.blake2s(h, data)
    }

    fun mixKey(input: ByteArray) {
        val out = Crypto.hkdf(ck, input, 2)
        ck = out[0]
        cipher.setKey(out[1].copyOf(Protocol.KEY_LEN))
    }

    fun encryptAndHash(plaintext: ByteArray): ByteArray {
        val ct = cipher.encryptWithAd(h, plaintext)
        mixHash(ct)
        return ct
    }

    fun decryptAndHash(ciphertext: ByteArray): ByteArray {
        val pt = cipher.decryptWithAd(h, ciphertext)
        mixHash(ciphertext)
        return pt
    }

    fun handshakeHash(): ByteArray = h.copyOf()

    fun split(): Pair<CipherState, CipherState> {
        val out = Crypto.hkdf(ck, ByteArray(0), 2)
        val c1 = CipherState(out[0].copyOf(Protocol.KEY_LEN))
        val c2 = CipherState(out[1].copyOf(Protocol.KEY_LEN))
        return c1 to c2
    }
}
