plugins {
    alias(libs.plugins.android.application)
}

android {
    namespace = "com.example.heart_radio"
    compileSdk {
        version = release(36) {
            minorApiLevel = 1
        }
    }

    defaultConfig {
        applicationId = "dev.local.watch2.hrprobe"
        manifestPlaceholders["exportBluetoothDiagnostics"] = "false"
        minSdk = 27
        targetSdk = 36
        versionCode = 56
        versionName = "1.14.1-stress"
        testInstrumentationRunner = "com.example.heart_radio.IdleInstrumentation"

    }

    buildTypes {
        debug {
            manifestPlaceholders["exportBluetoothDiagnostics"] = "true"
        }
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
}

dependencies {
    testImplementation(libs.junit)
}
