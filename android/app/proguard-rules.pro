# RemedyConnect — no minify in v1. Keep BouncyCastle lightweight API intact if R8 is enabled later.
-keep class org.bouncycastle.** { *; }
-dontwarn org.bouncycastle.**
