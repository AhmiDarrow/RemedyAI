package com.remedy.groveconnect.ui

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import com.google.mlkit.vision.barcode.BarcodeScanner
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import com.remedy.groveconnect.ui.theme.Accent
import com.remedy.groveconnect.ui.theme.BgPrimary
import com.remedy.groveconnect.ui.theme.Border
import com.remedy.groveconnect.ui.theme.Error
import com.remedy.groveconnect.ui.theme.TextMuted
import com.remedy.groveconnect.ui.theme.TextPrimary
import com.remedy.groveconnect.ui.theme.TextSecondary
import java.util.concurrent.Executors

/**
 * Camera preview that scans a Grove Connect pairing QR and hands the raw
 * pairing text to [onScanned]. Falls back to a permission request when the
 * camera is not granted. The user can still paste — scanning is convenience.
 *
 * The scanner disarms after the first hit so one QR does not fire twice.
 * [rearmKey] re-arms it whenever its value changes — the caller passes the
 * pairing error so a bad scan can be retried without leaving the screen.
 */
@Composable
fun QrScanBox(
    onScanned: (String) -> Unit,
    onError: (String) -> Unit,
    modifier: Modifier = Modifier,
    rearmKey: Any? = null,
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    var hasPermission by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
                PackageManager.PERMISSION_GRANTED,
        )
    }
    var armed by remember { mutableStateOf(true) }
    var cameraError by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(rearmKey) { armed = true }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        hasPermission = granted
        if (!granted) onError("Camera permission denied — paste the code instead.")
    }

    val scanner: BarcodeScanner = remember {
        BarcodeScanning.getClient(
            BarcodeScannerOptions.Builder()
                .setBarcodeFormats(Barcode.FORMAT_QR_CODE)
                .build(),
        )
    }
    val analysisExecutor = remember { Executors.newSingleThreadExecutor() }

    DisposableEffect(Unit) {
        onDispose {
            scanner.close()
            analysisExecutor.shutdown()
        }
    }

    Box(
        modifier = modifier
            .fillMaxWidth()
            .height(280.dp)
            .clip(RoundedCornerShape(16.dp))
            .background(BgPrimary)
            .border(1.dp, Border, RoundedCornerShape(16.dp)),
        contentAlignment = Alignment.Center,
    ) {
        when {
            cameraError != null -> Column(
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
                modifier = Modifier.padding(24.dp),
            ) {
                Text(cameraError ?: "Camera unavailable", color = Error, fontSize = 14.sp)
                Spacer(Modifier.height(12.dp))
                Button(
                    onClick = {
                        cameraError = null
                        armed = true
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = Accent),
                ) { Text("Try again") }
            }

            !hasPermission -> Column(
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
                modifier = Modifier.padding(24.dp),
            ) {
                Text(
                    "Camera permission is needed to scan the QR.",
                    color = TextSecondary,
                    fontSize = 14.sp,
                    textAlign = TextAlign.Center,
                )
                Spacer(Modifier.height(12.dp))
                Button(
                    onClick = { permissionLauncher.launch(Manifest.permission.CAMERA) },
                    colors = ButtonDefaults.buttonColors(containerColor = Accent),
                ) { Text("Allow camera") }
            }

            else -> {
                AndroidView(
                    factory = { ctx ->
                        val previewView = PreviewView(ctx)
                        val providerFuture = ProcessCameraProvider.getInstance(ctx)
                        providerFuture.addListener({
                            try {
                                val provider = providerFuture.get()
                                val preview = Preview.Builder().build().also {
                                    it.surfaceProvider = previewView.surfaceProvider
                                }
                                val analysis = ImageAnalysis.Builder()
                                    .setBackpressureStrategy(
                                        ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST,
                                    )
                                    .build()
                                    .also {
                                        it.setAnalyzer(analysisExecutor) { imageProxy ->
                                            analyzeFrame(imageProxy, scanner) { raw ->
                                                if (armed) {
                                                    armed = false
                                                    onScanned(raw)
                                                }
                                            }
                                        }
                                    }
                                provider.unbindAll()
                                provider.bindToLifecycle(
                                    lifecycleOwner,
                                    CameraSelector.DEFAULT_BACK_CAMERA,
                                    preview,
                                    analysis,
                                )
                            } catch (e: Exception) {
                                cameraError = e.message ?: "Camera unavailable"
                            }
                        }, ContextCompat.getMainExecutor(ctx))
                        previewView
                    },
                    modifier = Modifier.fillMaxSize(),
                )
                Text(
                    "Point at the QR on this PC",
                    color = TextPrimary,
                    fontSize = 13.sp,
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .padding(bottom = 14.dp),
                )
                if (!armed) {
                    Text(
                        "Scanned — pairing…",
                        color = TextMuted,
                        fontSize = 12.sp,
                        modifier = Modifier.align(Alignment.TopCenter).padding(top = 12.dp),
                    )
                }
            }
        }
    }
}

private fun analyzeFrame(
    imageProxy: ImageProxy,
    scanner: BarcodeScanner,
    onRaw: (String) -> Unit,
) {
    val mediaImage = imageProxy.image
    if (mediaImage == null) {
        imageProxy.close()
        return
    }
    val input = InputImage.fromMediaImage(mediaImage, imageProxy.imageInfo.rotationDegrees)
    scanner.process(input)
        .addOnSuccessListener { barcodes ->
            val raw = barcodes.firstOrNull()?.rawValue
            if (!raw.isNullOrBlank()) onRaw(raw)
        }
        .addOnCompleteListener { imageProxy.close() }
}
