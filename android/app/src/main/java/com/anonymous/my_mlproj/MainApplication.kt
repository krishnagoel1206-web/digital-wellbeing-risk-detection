package com.anonymous.my_mlproj

import android.app.Application
import android.content.res.Configuration
import android.util.Log
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

import com.facebook.react.PackageList
import com.facebook.react.ReactApplication
import com.facebook.react.ReactNativeApplicationEntryPoint.loadReactNative
import com.facebook.react.ReactPackage
import com.facebook.react.ReactHost
import com.facebook.react.common.ReleaseLevel
import com.facebook.react.defaults.DefaultNewArchitectureEntryPoint

import expo.modules.ApplicationLifecycleDispatcher
import expo.modules.ExpoReactHostFactory

class MainApplication : Application(), ReactApplication {

  override val reactHost: ReactHost by lazy {
    ExpoReactHostFactory.getDefaultReactHost(
      context = applicationContext,
      packageList =
        PackageList(this).packages.apply {
          // Packages that cannot be autolinked yet can be added manually here, for example:
          // add(MyReactNativePackage())
        }
    )
  }

  override fun onCreate() {
    super.onCreate()
    DefaultNewArchitectureEntryPoint.releaseLevel = try {
      ReleaseLevel.valueOf(BuildConfig.REACT_NATIVE_RELEASE_LEVEL.uppercase())
    } catch (e: IllegalArgumentException) {
      ReleaseLevel.STABLE
    }
    loadReactNative(this)
    ApplicationLifecycleDispatcher.onApplicationCreate(this)

    startEmbeddedPython()
  }

  /**
   * Start the embedded CPython interpreter and prove it runs.
   *
   * WHY THIS IS HERE
   * The Python interpreter must be started once per process, before anything
   * calls into it, and Application.onCreate is the only place guaranteed to
   * run before any screen or background task. Chaquopy's start() is safe to
   * call once; calling it twice throws, hence the isStarted guard.
   *
   * The log line is a deliberate smoke test rather than decoration. Embedded
   * Python fails in ways a successful BUILD cannot reveal - a missing ABI, an
   * interpreter that cannot find its stdlib - and those surface only when the
   * interpreter actually starts on a real device.
   *
   * Failure is caught rather than propagated: the app's capture, charts and
   * stored history do not depend on Python, and taking the whole app down
   * because an experimental interpreter failed would be the wrong trade.
   */
  private fun startEmbeddedPython() {
    try {
      if (!Python.isStarted()) {
        Python.start(AndroidPlatform(this))
      }
      val report = Python.getInstance()
        .getModule("smoke_test")
        .callAttr("report")
        .toString()
      Log.i("EmbeddedPython", "OK: $report")

      val arithmetic = Python.getInstance()
        .getModule("smoke_test")
        .callAttr("arithmetic_check")
        .toString()
      Log.i("EmbeddedPython", arithmetic)
    } catch (e: Throwable) {
      Log.e("EmbeddedPython", "FAILED to start: ${e.message}", e)
    }
  }

  override fun onConfigurationChanged(newConfig: Configuration) {
    super.onConfigurationChanged(newConfig)
    ApplicationLifecycleDispatcher.onConfigurationChanged(this, newConfig)
  }
}
