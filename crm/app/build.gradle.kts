import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// חתימה: ב-GitHub Actions נוצר key.properties מהסודות (אותו מפתח של FilterPhone). בלי הקובץ — debug.
val keyProperties = Properties().apply {
    val f = rootProject.file("key.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}
// versionCode עולה בכל פרסום (מספר ההרצה של deploy.yml) — כך הטלפון מתעדכן לבד
val crmVersionCode = (System.getenv("CRM_VERSION_CODE") ?: "1").toInt()

android {
    namespace = "com.filterphone.crm"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.filterphone.crm"
        minSdk = 26
        targetSdk = 36
        versionCode = crmVersionCode
        versionName = "1.0.$crmVersionCode"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
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
            isMinifyEnabled = false          // אפליקציה קטנה; הגשר ל-JavaScript (@JavascriptInterface) נשאר כמו שהוא
        }
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }

    lint {
        abortOnError = true
        warningsAsErrors = false
        // ObsoleteSdkInt: הסמל המותאם חייב את התיקייה mipmap-anydpi-v26 (בלי v26 — aapt לא מוצא אותו)
        disable += setOf("GradleDependency", "NewerVersionAvailable", "AndroidGradlePluginVersion", "OldTargetApi", "ObsoleteSdkInt")
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20250517")
}
