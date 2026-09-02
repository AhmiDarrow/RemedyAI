package com.remedy.groveconnect

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.OnBackPressedCallback
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import androidx.compose.runtime.getValue
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import com.remedy.groveconnect.connect.Reachable
import com.remedy.groveconnect.connect.RemoteState
import com.remedy.groveconnect.api.RemedyApi
import com.remedy.groveconnect.ui.HomeScreen
import com.remedy.groveconnect.ui.HubScreen
import com.remedy.groveconnect.ui.LockScreen
import com.remedy.groveconnect.ui.PairScreen
import com.remedy.groveconnect.ui.theme.GroveTheme

class MainActivity : FragmentActivity() {
    private val controller get() = (application as GroveApp).controller
    private var screen by mutableStateOf(UiScreen.Lock)
    private var ui by mutableStateOf(RemoteState())
    // Android 13+ needs runtime consent before the foreground service's
    // notification is visible. Denial is fine: the service still runs, the
    // user just does not see the "remote is live" tile.
    private val notifPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { /* no-op */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        ui = controller.state
        controller.onChange = {
            ui = controller.state
            if (screen != UiScreen.Lock && screen != UiScreen.Home &&
                (ui.reachable == Reachable.OnLan || ui.reachable == Reachable.OnRelay) && ui.shimUrl != null
            ) {
                // Native portal is the home now — the old webview "hub" is gone.
                screen = UiScreen.Home
            }
        }
        onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() {
                    when (screen) {
                        UiScreen.Home -> {
                            controller.shutdown()
                            screen = UiScreen.Pair
                        }
                        UiScreen.Hub -> {
                            controller.shutdown()
                            screen = UiScreen.Pair
                        }
                        else -> finish()
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
                        onReconnect = { controller.connectLast() },
                        onUnpair = { controller.unpair() },
                    )
                    UiScreen.Hub -> HubScreen(
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
                        onOpenFullRemote = { screen = UiScreen.Home },
                        onReconnect = { controller.connectLast() },
                    )
                    UiScreen.Home -> {
                        val shim = ui.shimUrl
                        if (shim != null) {
                            // The shim URL is http://127.0.0.1:{port}/{token}/?connect=1 —
                            // the API base must be the token path WITHOUT the query,
                            // otherwise every request path lands after "?connect=1"
                            // and the shim 403s it. substringBefore('?') fixes that.
                            val apiBase = shim.substringBefore('?').trimEnd('/')
                            val api = remember(apiBase) { RemedyApi(apiBase) }
                            DisposableEffect(api) {
                                onDispose { api.shutdown() }
                            }
                            HomeScreen(
                                state = ui,
                                api = api,
                                onClose = {
                                    controller.shutdown()
                                    screen = UiScreen.Pair
                                },
                                onStop = { controller.stopGeneration() },
                                onRefresh = { controller.refreshNow() },
                            )
                        } else {
                            // Not connected — bounce back to Pair.
                            LaunchedEffect(Unit) { screen = UiScreen.Pair }
                        }
                    }
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
                    if (controller.state.paired) {
                        controller.connectLast()
                        screen = UiScreen.Hub
                    } else {
                        screen = UiScreen.Pair
                    }
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
                .setTitle("Unlock RemedyConnect")
                .setSubtitle("Confirm it's you to open the remote")
                .setAllowedAuthenticators(authenticators)
                .build(),
        )
    }
}

private enum class UiScreen { Lock, Pair, Hub, Home }
