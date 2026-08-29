package com.remedy.groveconnect.connect

import android.content.Context
import android.content.SharedPreferences
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

    fun staticPair(): Pair<ByteArray, ByteArray> {
        val existing = prefs.getString(KEY_PRIV, null)
        if (existing != null) {
            val priv = UrlSafeB64.decode(existing)
            val pub = prefs.getString(KEY_PUB, null)?.let { UrlSafeB64.decode(it) }
                ?: Crypto.x25519Public(priv)
            return priv to pub
        }
        val (priv, pub) = Crypto.generateX25519()
        prefs.edit()
            .putString(KEY_PRIV, UrlSafeB64.encode(priv))
            .putString(KEY_PUB, UrlSafeB64.encode(pub))
            .apply()
        return priv to pub
    }

    companion object {
        private const val KEY_PRIV = "device_x25519_priv"
        private const val KEY_PUB = "device_x25519_pub"

        fun encryptedPrefs(ctx: Context): SharedPreferences {
            val alias = MasterKeys.getOrCreate(MasterKeys.AES256_GCM_SPEC)
            return EncryptedSharedPreferences.create(
                "grove_connect",
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

    fun pinnedHostPub(): ByteArray? =
        prefs.getString(PIN, null)?.let { UrlSafeB64.decode(it) }

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

    fun saveDeviceId(id: String) {
        prefs.edit().putString(DEVICE, id).apply()
    }

    fun deviceId(): String? = prefs.getString(DEVICE, null)

    fun isPaired(): Boolean = pinnedHostPub() != null

    fun clearPair() {
        prefs.edit().remove(PIN).remove(LAN).remove(RELAY).remove(DEVICE).apply()
    }

    companion object {
        private const val PIN = "pinned_host_pub"
        private const val LAN = "last_lan"
        private const val RELAY = "last_relay"
        private const val DEVICE = "device_id"
    }
}
