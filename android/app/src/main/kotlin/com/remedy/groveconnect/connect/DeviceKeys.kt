package com.remedy.groveconnect.connect

import android.annotation.SuppressLint
import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKeys
import com.remedy.groveconnect.core.Crypto
import com.remedy.groveconnect.core.Protocol
import com.remedy.groveconnect.core.UrlSafeB64

/**
 * Device static X25519.
 *
 * Android Keystore on minSdk 26 does not expose X25519 KeyAgreement, so the
 * 32-byte seed is stored in EncryptedSharedPreferences (AES-256-GCM wrapping
 * key in Android Keystore). Not plaintext on disk.
 */
class DeviceKeys(ctx: Context) {
    private val prefs: SharedPreferences = encryptedPrefs(ctx)

    @SuppressLint("ApplySharedPref") // Key durability is required before a handshake can be reported successful.
    fun staticPair(): Pair<ByteArray, ByteArray> {
        val existing = prefs.getString(KEY_PRIV, null)
        if (existing != null) {
            try {
                val priv = UrlSafeB64.decode(existing)
                require(priv.size == Protocol.KEY_LEN)
                // The private key is authoritative; deriving prevents a corrupt
                // cached public key from permanently breaking Noise handshakes.
                return priv to Crypto.x25519Public(priv)
            } catch (e: Exception) {
                Log.w(TAG, "device key invalid, clearing pairing: ${e.javaClass.simpleName}")
                prefs.edit().clear().commit()
                throw IllegalStateException("Phone security key was repaired. Pair this phone again.", e)
            }
        }
        val (priv, pub) = Crypto.generateX25519()
        check(prefs.edit()
            .putString(KEY_PRIV, UrlSafeB64.encode(priv))
            .putString(KEY_PUB, UrlSafeB64.encode(pub))
            .commit()) { "Could not save the phone security key." }
        return priv to pub
    }

    companion object {
        private const val TAG = "DeviceKeys"
        private const val KEY_PRIV = "device_x25519_priv"
        private const val KEY_PUB = "device_x25519_pub"

        private const val PREFS_NAME = "grove_connect"

        /**
         * Opens the encrypted prefs. After a Keystore reset (device restore,
         * lock-screen change on some OEMs) the wrapping key no longer matches
         * the on-disk keyset and Tink throws (AEADBadTagException et al.) at
         * process start — which made the app unlaunchable. Wipe the file and
         * recreate once; the user re-pairs. A second failure is a real bug.
         */
        fun encryptedPrefs(ctx: Context): SharedPreferences {
            return try {
                openEncryptedPrefs(ctx)
            } catch (first: Exception) {
                Log.w(TAG, "encrypted prefs unreadable, recreating: ${first.javaClass.simpleName}")
                ctx.deleteSharedPreferences(PREFS_NAME)
                try {
                    openEncryptedPrefs(ctx)
                } catch (second: Exception) {
                    second.addSuppressed(first)
                    throw second
                }
            }
        }

        private fun openEncryptedPrefs(ctx: Context): SharedPreferences {
            val alias = MasterKeys.getOrCreate(MasterKeys.AES256_GCM_SPEC)
            return EncryptedSharedPreferences.create(
                PREFS_NAME,
                alias,
                ctx,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
            )
        }
    }
}

class PairStore(ctx: Context) {
    private val prefs = DeviceKeys.encryptedPrefs(ctx)

    fun pinnedHostPub(): ByteArray? {
        val raw = prefs.getString(PIN, null) ?: return null
        return try {
            val decoded = UrlSafeB64.decode(raw)
            if (decoded.size == Protocol.KEY_LEN) decoded else {
                clearPair()
                null
            }
        } catch (_: Exception) {
            clearPair()
            null
        }
    }

    fun pinHost(hostPub: ByteArray) {
        require(hostPub.size == Protocol.KEY_LEN)
        prefs.edit().putString(PIN, UrlSafeB64.encode(hostPub)).apply()
    }

    fun lastLan(): String? = prefs.getString(LAN, null)

    fun saveLan(host: String, port: Int) {
        prefs.edit().putString(LAN, "$host:$port").apply()
    }

    fun saveRelay(host: String?, port: Int?) {
        if (host.isNullOrBlank() || port == null) {
            prefs.edit().remove(RELAY).apply()
        } else {
            prefs.edit().putString(RELAY, "$host:$port").apply()
        }
    }

    fun lastRelay(): Pair<String, Int>? {
        val raw = prefs.getString(RELAY, null) ?: return null
        val idx = raw.lastIndexOf(':')
        if (idx <= 0) return null
        val port = raw.substring(idx + 1).toIntOrNull() ?: return null
        return raw.substring(0, idx) to port
    }

    fun saveTailscale(host: String?, port: Int?) {
        if (host.isNullOrBlank() || port == null) {
            prefs.edit().remove(TS).apply()
        } else {
            prefs.edit().putString(TS, "$host:$port").apply()
        }
    }

    fun lastTailscale(): Pair<String, Int>? {
        val raw = prefs.getString(TS, null) ?: return null
        val idx = raw.lastIndexOf(':')
        if (idx <= 0) return null
        val port = raw.substring(idx + 1).toIntOrNull() ?: return null
        return raw.substring(0, idx) to port
    }

    fun saveRdv(hosts: List<Pair<String, Int>>) {
        if (hosts.isEmpty()) {
            prefs.edit().remove(RDV).apply()
        } else {
            prefs.edit().putString(RDV, hosts.joinToString(";") { "${it.first}:${it.second}" }).apply()
        }
    }

    fun lastRdv(): List<Pair<String, Int>> {
        val raw = prefs.getString(RDV, null) ?: return emptyList()
        return raw.split(';').mapNotNull { chunk ->
            val idx = chunk.lastIndexOf(':')
            if (idx <= 0) return@mapNotNull null
            val port = chunk.substring(idx + 1).toIntOrNull() ?: return@mapNotNull null
            chunk.substring(0, idx) to port
        }
    }

    @SuppressLint("ApplySharedPref")
    fun saveDeviceId(id: String) {
        check(prefs.edit().putString(DEVICE, id).commit()) { "Could not save paired device identity." }
    }

    fun deviceId(): String? = prefs.getString(DEVICE, null)

    fun isPaired(): Boolean = pinnedHostPub() != null

    /** Persist a complete, versioned pairing in one durable transaction. */
    @SuppressLint("ApplySharedPref") // Pairing fields must land atomically and durably.
    fun savePair(qr: com.remedy.groveconnect.core.QrPayload) {
        val edit = prefs.edit()
            .putInt(VERSION, 1)
            .putString(PIN, UrlSafeB64.encode(qr.hostPub))
            .putString(LAN, "${qr.lanHost}:${qr.lanPort}")
        if (qr.tailscaleHost.isNullOrBlank() || qr.tailscalePort == null) edit.remove(TS)
        else edit.putString(TS, "${qr.tailscaleHost}:${qr.tailscalePort}")
        if (qr.relayHost.isNullOrBlank() || qr.relayPort == null) edit.remove(RELAY)
        else edit.putString(RELAY, "${qr.relayHost}:${qr.relayPort}")
        if (qr.rdvHosts.isEmpty()) edit.remove(RDV)
        else edit.putString(RDV, qr.rdvHosts.joinToString(";") { "${it.first}:${it.second}" })
        check(edit.commit()) { "Could not save pairing securely." }
    }

    @SuppressLint("ApplySharedPref") // Revocation must be durable before returning to the UI.
    fun clearPair() {
        check(
            prefs.edit().remove(VERSION).remove(PIN).remove(LAN).remove(RELAY)
                .remove(RDV).remove(DEVICE).remove(TS).commit(),
        ) { "Could not securely remove this pairing." }
    }

    companion object {
        private const val PIN = "pinned_host_pub"
        private const val LAN = "last_lan"
        private const val TS = "last_tailscale"
        private const val RELAY = "last_relay"
        private const val RDV = "rdv_hosts"
        private const val DEVICE = "device_id"
        private const val VERSION = "pair_version"
    }
}
