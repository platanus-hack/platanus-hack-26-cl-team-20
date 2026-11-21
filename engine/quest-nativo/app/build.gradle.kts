plugins {
  alias(libs.plugins.android.application)
  alias(libs.plugins.jetbrains.kotlin.android)
  alias(libs.plugins.meta.spatial.plugin)
}

android {
  namespace = "com.smartup.proyector"
  compileSdk = 34

  defaultConfig {
    applicationId = "com.smartup.proyector"
    minSdk = 34
    targetSdk = 34
    versionCode = 1
    versionName = "0.1"
    // el Quest 3 es arm64: solo esa ABI (OpenCV trae 4)
    ndk { abiFilters += "arm64-v8a" }
  }

  packaging { resources.excludes.add("META-INF/LICENSE") }

  lint {
    abortOnError = false
    checkReleaseBuilds = false
  }

  buildTypes {
    release {
      isMinifyEnabled = false
      proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
    }
  }
  compileOptions {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
  }
  kotlinOptions { jvmTarget = "17" }

  // los tests unitarios (gray code, DLT/PnP) corren en la JVM del Mac, sin casco
  testOptions { unitTests.isReturnDefaultValues = true }
}

dependencies {
  implementation(libs.androidx.core.ktx)
  // códigos QR de la grilla de marcadores (solo el codificador, ~550 KB)
  implementation("com.google.zxing:core:3.5.3")
  // ArUco (DICT_4X4_50) sobre la cámara passthrough: OpenCV Android oficial (Maven Central)
  implementation("org.opencv:opencv:4.10.0")

  // Meta Spatial SDK
  implementation(libs.meta.spatial.sdk.base)
  implementation(libs.meta.spatial.sdk.toolkit)
  implementation(libs.meta.spatial.sdk.vr)
  implementation(libs.meta.spatial.sdk.isdk)
  implementation(libs.meta.spatial.sdk.mruk)

  testImplementation(libs.junit)
}
