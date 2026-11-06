# Add project specific ProGuard rules here.
# By default, the flags in this file are appended to flags specified
# in /usr/local/Cellar/android-sdk/24.3.3/tools/proguard/proguard-android.txt
# You can edit the include path and order by changing the proguardFiles
# directive in build.gradle.
#
# For more details, see
#   http://developer.android.com/guide/developing/tools/proguard.html

# react-native-reanimated
-keep class com.swmansion.reanimated.** { *; }
-keep class com.facebook.react.turbomodule.** { *; }

# Add any project specific keep options here:

# --- WorkManager / Room ------------------------------------------------------
#
# Fixes a release-only crash on launch:
#
#   java.lang.RuntimeException: Unable to get provider
#   androidx.startup.InitializationProvider: java.lang.NoSuchMethodException:
#   androidx.work.impl.WorkDatabase_Impl.<init> []
#
# Room generates a `*_Impl` subclass for each RoomDatabase and instantiates it
# REFLECTIVELY, by class name. R8 sees no caller for the generated no-argument
# constructor, concludes it is dead, and removes it - so the app dies before
# any JavaScript runs. Debug builds are unaffected because minification is off,
# which is why this only appeared in the release APK.
#
# WorkManager is in the dependency graph because expo-background-task schedules
# the daily usage capture through it. The capture matters: Android discards its
# usage-event stream after about a week, so a day never captured is lost for
# good.
-keep class * extends androidx.room.RoomDatabase { <init>(); }
-keep class androidx.work.impl.** { *; }
-keep class androidx.startup.** { *; }

# Expo's module registry resolves native modules by name at runtime, so R8
# cannot see those classes being used either.
-keep class expo.modules.** { *; }
-keep class * extends expo.modules.kotlin.modules.Module { *; }

# This project's own native module, found by name from JavaScript.
-keep class expo.modules.usagestats.** { *; }
