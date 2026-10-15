import java.util.Properties

plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// חתימת שחרור: ב-GitHub Actions נוצר key.properties מתוך הסודות של המאגר.
// בלי הקובץ (בנייה מקומית) — חתימת debug.
val keyProperties = Properties().apply {
    val f = rootProject.file("key.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

android {
    namespace = "com.filterphone.app"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        applicationId = "com.filterphone.app"
        minSdk = 26
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        if (keyProperties.isNotEmpty()) {
            create("release") {
                storeFile = rootProject.file(keyProperties.getProperty("storeFile"))
                storePassword = keyProperties.getProperty("storePassword")
                keyAlias = keyProperties.getProperty("keyAlias")
                keyPassword = keyProperties.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    packaging {
        // ספריות ה-native של WireGuard נטענות ישירות מה-APK
        jniLibs.useLegacyPackaging = false
    }

    // בדיקות יחידה של הקוד הנייטיבי (JVM): org.json האמיתי במקום ה-stub של android.jar
    testOptions {
        unitTests.isReturnDefaultValues = true
    }

    // Lint: שגיאות (למשל API חדש בלי בדיקת גרסה, מניפסט שגוי) מכשילות את הבנייה ב-CI.
    // רק הקוד שלנו — לא התוספים של Flutter ולא הבדיקות שלהם.
    lint {
        abortOnError = true
        checkDependencies = false
        ignoreTestSources = true
        warningsAsErrors = false
        disable += setOf("GradleDependency", "NewerVersionAvailable", "AndroidGradlePluginVersion", "OldTargetApi")
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

dependencies {
    implementation("com.wireguard.android:tunnel:1.0.20260102")
    // הקמה בלחיצה אחת בלי מחשב ובלי אפליקציה חיצונית: ADB דרך ניפוי אלחוטי, מתוך האפליקציה עצמה.
    // libadb-android מבצע התאמה (pairing) וחיבור TLS ל-adbd המקומי, ומגלה את הפורטים ב-mDNS (אנדרואיד 11+).
    implementation("com.github.MuntashirAkon:libadb-android:3.1.1")
    implementation("org.conscrypt:conscrypt-android:2.5.3")               // TLSv1.3 + SPAKE2 להתאמה
    implementation("org.lsposed.hiddenapibypass:hiddenapibypass:6.1")     // גישה ל-API נסתר (אנדרואיד 9+)
    implementation("org.bouncycastle:bcpkix-jdk18on:1.78.1")              // יצירת תעודת ADB (X.509) חתומה-עצמית
    implementation("org.bouncycastle:bcprov-jdk18on:1.78.1")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20250517")
}

flutter {
    source = "../.."
}
