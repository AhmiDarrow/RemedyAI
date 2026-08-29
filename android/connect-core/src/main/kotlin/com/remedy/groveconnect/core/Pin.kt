package com.remedy.groveconnect.core

/**
 * After the first successful pair, the host static public key is pinned.
 * A later QR with a different `hp=` fails closed.
 */
object Pin {
    fun check(storedHostPub: ByteArray?, candidateHostPub: ByteArray) {
        require(candidateHostPub.size == Protocol.KEY_LEN)
        if (storedHostPub == null) return
        if (storedHostPub.size != Protocol.KEY_LEN ||
            !Crypto.constantTimeEquals(storedHostPub, candidateHostPub)
        ) {
            throw PinMismatchException()
        }
    }
}

class PinMismatchException :
    Exception("This is not the PC you paired with. Unpair on this phone to start over.")
