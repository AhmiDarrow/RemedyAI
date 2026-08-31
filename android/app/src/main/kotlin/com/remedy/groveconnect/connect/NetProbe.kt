package com.remedy.groveconnect.connect

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities

/** Cheap network-class probe: Wi-Fi/Ethernet vs cellular/mobile data. */
object NetProbe {
    /** True when the active network is Wi-Fi (or wired). False on mobile data. */
    fun isWifi(ctx: Context): Boolean {
        val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            ?: return false
        val net = cm.activeNetwork ?: return false
        val caps = cm.getNetworkCapabilities(net) ?: return false
        return caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
            caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)
    }
}
