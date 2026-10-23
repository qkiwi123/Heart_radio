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
        minSdk = 27
        targetSdk = 36
        versionCode = 45
        versionName = "1.11.2-retention"
        testInstrumentationRunner = "com.example.heart_radio.IdleInstrumentation"

    }

    buildTypes {
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
