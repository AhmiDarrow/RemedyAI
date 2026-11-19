package com.remedy.groveconnect

import android.app.Application
import com.remedy.groveconnect.connect.ConnectController

class GroveApp : Application() {
    lateinit var controller: ConnectController
        private set

    override fun onCreate() {
        super.onCreate()
        com.remedy.groveconnect.connect.IrohLink.init(this)
        controller = ConnectController(this)
    }

    override fun onTerminate() {
        controller.shutdown()
        super.onTerminate()
    }
}
