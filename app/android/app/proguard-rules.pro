# WireGuard: קוד ה-Go קורא חזרה ל-Java דרך JNI — אסור לשנות שמות או להסיר
-keep class com.wireguard.** { *; }
-dontwarn com.wireguard.**

# רכיבי האפליקציה שמוגדרים במניפסט / נקראים מ-Flutter
-keep class com.filterphone.app.** { *; }

# ניפוי אלחוטי מתוך האפליקציה (libadb) + TLS (conscrypt) + יצירת תעודת ADB (sun.security)
-keep class io.github.muntashirakon.adb.** { *; }
-dontwarn io.github.muntashirakon.adb.**
-keep class org.conscrypt.** { *; }
-dontwarn org.conscrypt.**
-keep class org.lsposed.hiddenapibypass.** { *; }
-dontwarn org.lsposed.hiddenapibypass.**
-keep class org.bouncycastle.** { *; }
-dontwarn org.bouncycastle.**
-dontwarn javax.naming.**
