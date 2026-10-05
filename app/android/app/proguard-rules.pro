# WireGuard: קוד ה-Go קורא חזרה ל-Java דרך JNI — אסור לשנות שמות או להסיר
-keep class com.wireguard.** { *; }
-dontwarn com.wireguard.**

# רכיבי האפליקציה שמוגדרים במניפסט / נקראים מ-Flutter
-keep class com.filterphone.app.** { *; }
