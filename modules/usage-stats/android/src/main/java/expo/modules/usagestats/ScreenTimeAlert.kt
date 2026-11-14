package expo.modules.usagestats

import android.Manifest
import android.app.AppOpsManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Process
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import java.time.LocalDate
import java.util.concurrent.TimeUnit

/**
 * "Put the phone down" warnings, raised while the day is still going.
 *
 * The app learns two levels from the user's own past days (computed in JS from
 * the stored history and handed over with [configure]):
 *
 *   usual   the median daily screen time - a normal day's total
 *   high    the level at which the unusual-day detector would flag the day
 *           (median + 3 robust standard deviations, the same rule it uses)
 *
 * A WorkManager job measures today's screen time so far every 15 minutes,
 * with the very session rules the app uses ([SessionReader]), and posts at
 * most one notification per level per day. WorkManager keeps the job across
 * app closes and reboots, so the laptop and the app being open are both
 * irrelevant.
 */
internal object ScreenTimeAlert {
  private const val PREFS = "screen_time_alert"
  private const val CHANNEL_ID = "screen_time_alerts"
  private const val WORK_NAME = "screen_time_alert_check"
  private const val NOTIFY_USUAL = 4101
  private const val NOTIFY_HIGH = 4102

  private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

  fun configure(context: Context, enabled: Boolean, usualMinutes: Int, highMinutes: Int, basisDays: Int) {
    prefs(context).edit()
      .putBoolean("enabled", enabled)
      .putInt("usual", usualMinutes)
      .putInt("high", highMinutes)
      .putInt("basis_days", basisDays)
      .apply()
    if (enabled && usualMinutes > 0) schedule(context) else cancel(context)
  }

  fun setEnabled(context: Context, enabled: Boolean) {
    prefs(context).edit().putBoolean("enabled", enabled).apply()
    if (enabled && prefs(context).getInt("usual", 0) > 0) schedule(context) else cancel(context)
  }

  fun state(context: Context): Map<String, Any?> {
    val p = prefs(context)
    val today = if (hasUsageAccess(context)) SessionReader.todayMinutes(context) else null
    return mapOf(
      "enabled" to p.getBoolean("enabled", true),
      "usualMinutes" to p.getInt("usual", 0),
      "highMinutes" to p.getInt("high", 0),
      "basisDays" to p.getInt("basis_days", 0),
      "todayMinutes" to today,
      "notificationsAllowed" to notificationsAllowed(context),
    )
  }

  private fun schedule(context: Context) {
    val request = PeriodicWorkRequestBuilder<ScreenTimeAlertWorker>(15, TimeUnit.MINUTES).build()
    WorkManager.getInstance(context)
      .enqueueUniquePeriodicWork(WORK_NAME, ExistingPeriodicWorkPolicy.KEEP, request)
  }

  private fun cancel(context: Context) {
    WorkManager.getInstance(context).cancelUniqueWork(WORK_NAME)
  }

  /** One pass: measure today, warn if a level was crossed for the first time today. */
  fun check(context: Context) {
    val p = prefs(context)
    if (!p.getBoolean("enabled", true)) return
    val usual = p.getInt("usual", 0)
    val high = p.getInt("high", 0)
    if (usual <= 0 || !hasUsageAccess(context)) return

    val today = LocalDate.now().toString()
    val minutes = SessionReader.todayMinutes(context)

    if (high > usual && minutes >= high && p.getString("high_sent", null) != today) {
      notify(
        context,
        NOTIFY_HIGH,
        "Unusual day - time to close the phone",
        "${hm(minutes)} on your phone today, far above your usual ${hm(usual)}. " +
          "Days like this get flagged as unusual. Put it down for a while.",
      )
      p.edit().putString("high_sent", today).putString("usual_sent", today).apply()
    } else if (minutes >= usual && p.getString("usual_sent", null) != today) {
      notify(
        context,
        NOTIFY_USUAL,
        "You've passed your usual screen time",
        "${hm(minutes)} today - more than your usual ${hm(usual)} a day. " +
          "Good moment to close the phone and take a break.",
      )
      p.edit().putString("usual_sent", today).apply()
    }
  }

  /** A sample warning, so the user can see what one looks like. */
  fun sendTest(context: Context): Boolean {
    val usual = prefs(context).getInt("usual", 0)
    val minutes = if (hasUsageAccess(context)) SessionReader.todayMinutes(context) else 0
    return notify(
      context,
      NOTIFY_USUAL,
      "Test: you've passed your usual screen time",
      "This is how the warning looks. Today so far: ${hm(minutes)}" +
        (if (usual > 0) "; your usual day is ${hm(usual)}." else "."),
    )
  }

  fun notificationsAllowed(context: Context): Boolean {
    val manager = context.getSystemService(NotificationManager::class.java)
    if (!manager.areNotificationsEnabled()) return false
    return Build.VERSION.SDK_INT < 33 ||
      context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
  }

  private fun notify(context: Context, id: Int, title: String, text: String): Boolean {
    if (!notificationsAllowed(context)) return false
    val manager = context.getSystemService(NotificationManager::class.java)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
      manager.createNotificationChannel(
        NotificationChannel(CHANNEL_ID, "Screen-time warnings", NotificationManager.IMPORTANCE_HIGH).apply {
          description = "Warns when today's screen time goes past your usual amount"
        }
      )
    }
    val open = context.packageManager.getLaunchIntentForPackage(context.packageName)
    val tap = open?.let {
      PendingIntent.getActivity(context, 0, it, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }
    val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
      Notification.Builder(context, CHANNEL_ID)
    } else {
      @Suppress("DEPRECATION")
      Notification.Builder(context).setPriority(Notification.PRIORITY_HIGH)
    }
    val notification = builder
      .setSmallIcon(context.applicationInfo.icon)
      .setContentTitle(title)
      .setContentText(text)
      .setStyle(Notification.BigTextStyle().bigText(text))
      .setAutoCancel(true)
      .apply { if (tap != null) setContentIntent(tap) }
      .build()
    manager.notify(id, notification)
    return true
  }

  private fun hasUsageAccess(context: Context): Boolean {
    val ops = context.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
    val mode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
      ops.unsafeCheckOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), context.packageName)
    } else {
      @Suppress("DEPRECATION")
      ops.checkOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), context.packageName)
    }
    return mode == AppOpsManager.MODE_ALLOWED
  }

  private fun hm(minutes: Int): String =
    if (minutes >= 60) "${minutes / 60}h ${minutes % 60}m" else "${minutes}m"
}

/** The 15-minute background check. */
class ScreenTimeAlertWorker(context: Context, params: WorkerParameters) : Worker(context, params) {
  override fun doWork(): Result {
    return try {
      ScreenTimeAlert.check(applicationContext)
      Result.success()
    } catch (_: Exception) {
      // A failed pass is simply retried at the next period.
      Result.success()
    }
  }
}
