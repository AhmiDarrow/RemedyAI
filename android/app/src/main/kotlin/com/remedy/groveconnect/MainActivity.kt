package com.remedy.groveconnect

import android.os.Bundle
import androidx.activity.OnBackPressedCallback
import androidx.activity.compose.setContent
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.remedy.groveconnect.connect.Reachable
import com.remedy.groveconnect.connect.RemoteState
import com.remedy.groveconnect.ui.LockScreen
import com.remedy.groveconnect.ui.PairScreen
import com.remedy.groveconnect.ui.RemoteScreen
import com.remedy.groveconnect.ui.theme.GroveTheme

class MainActivity : FragmentActivity() {
    private val controller get() = (application as GroveApp).controller
    private var screen by mutableStateOf(UiScreen.Lock)
    private var ui by mutableStateOf(RemoteState())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ui = controller.state
        controller.onChange = {
            ui = controller.state
            if ((ui.reachable == Reachable.OnLan || ui.reachable == Reachable.OnRelay) && ui.shimUrl != null) {
                screen = UiScreen.Remote
            }
        }
        onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() {
                    if (screen == UiScreen.Remote) {
                        controller.shutdown()
                        screen = if (controller.state.paired) UiScreen.Pair else UiScreen.Pair
                    } else {
                        finish()
                    }
                }
            },
        )
        setContent {
            GroveTheme {
                when (screen) {
                    UiScreen.Lock -> LockScreen(onUnlock = { promptUnlock() }, error = ui.error)
                    UiScreen.Pair -> PairScreen(
                        state = ui,
                        onPair = { controller.pair(it) },
                        onUnpair = { controller.unpair() },
                    )
                    UiScreen.Remote -> RemoteScreen(
                        state = ui,
                        onStop = { controller.stopGeneration() },
                        onClose = {
                            controller.shutdown()
                            screen = UiScreen.Pair
                        },
                        onResolve = { id, yes -> controller.resolveApproval(id, yes) },
                        onPairAnother = {
                            controller.shutdown()
                            screen = UiScreen.Pair
                        },
                    )
                }
            }
        }
    }

    override fun onDestroy() {
        controller.onChange = null
        if (isFinishing) controller.shutdown()
        super.onDestroy()
    }

    private fun promptUnlock() {
        val exec = ContextCompat.getMainExecutor(this)
        val prompt = BiometricPrompt(
            this,
            exec,
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                    controller.unlock()
                    screen = UiScreen.Pair
                }

                override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                    ui = ui.copy(error = errString.toString())
                }
            },
        )
        val authenticators = BiometricManager.Authenticators.BIOMETRIC_WEAK or
            BiometricManager.Authenticators.DEVICE_CREDENTIAL
        val mgr = BiometricManager.from(this)
        if (mgr.canAuthenticate(authenticators) != BiometricManager.BIOMETRIC_SUCCESS) {
            ui = ui.copy(error = "Set a screen lock (PIN, pattern, or biometric) to open the remote.")
            return
        }
        prompt.authenticate(
            BiometricPrompt.PromptInfo.Builder()
                .setTitle("Unlock Grove Connect")
                .setSubtitle("Confirm it's you to open the remote")
                .setAllowedAuthenticators(authenticators)
                .build(),
        )
    }
}

private enum class UiScreen { Lock, Pair, Remote }
