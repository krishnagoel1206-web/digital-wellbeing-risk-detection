package expo.modules.usagestats

import android.app.AppOpsManager
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.os.Build
import android.os.Process
import android.provider.Settings
import expo.modules.interfaces.permissions.Permissions
import expo.modules.kotlin.Promise
import expo.modules.kotlin.exception.CodedException
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import kotlin.math.max
import kotlin.math.min

/**
 * Reads Android's app-usage statistics - the same underlying data source that
 * Digital Wellbeing is built on.
 *
 * WHY EVENTS RATHER THAN DAILY AGGREGATES
 * ---------------------------------------
 * UsageStatsManager offers two routes:
 *
 *   queryUsageStats(INTERVAL_DAILY)  pre-aggregated totals per app per day.
 *                                    Cheap, retained for months, but widely
 *                                    reported as imprecise - the buckets are
 *                                    not reliably day-aligned and totals can
 *                                    double-count.
 *
 *   queryEvents(start, end)          the raw stream of foreground/background
 *                                    transitions with millisecond timestamps.
 *                                    Precise, and the ONLY way to work out
 *                                    WHEN during the day the phone was used.
 *
 * This module uses queryEvents and reconstructs sessions itself, because:
 *   1. the per-day totals are then exact rather than approximate, and
 *   2. late-night usage - which this project needs and which neither of the
 *      public datasets contained - can only be derived from timestamps.
 *
 * The cost is retention: Android keeps the detailed event stream for roughly
 * a week. `getDailyUsage` therefore reports a `source` field per day, and
 * callers must not present older aggregate-derived days as equally precise.
 *
 * SESSION RECONSTRUCTION
 * ----------------------
 * Each ACTIVITY is followed from ACTIVITY_RESUMED to its own ACTIVITY_PAUSED
 * or ACTIVITY_STOPPED, and every open activity is closed at
 * SCREEN_NON_INTERACTIVE, KEYGUARD_SHOWN or DEVICE_SHUTDOWN. Where two apps
 * overlap, the time goes to the one that came to the front most recently, so
 * sessions never overlap and per-app minutes add up exactly to the total.
 * See collectSessions for why this replaced the earlier rule.
 *
 * Note ACTIVITY_RESUMED (29+) and MOVE_TO_FOREGROUND (21+) are the SAME
 * constant value 1, as are ACTIVITY_PAUSED and MOVE_TO_BACKGROUND at 2, so
 * comparing against the numeric values works across every supported release.
 *
 * Sessions are split at hour boundaries before being attributed, so a session
 * running from 22:40 to 23:30 contributes 20 minutes to the normal day and 30
 * to the late-night window rather than being assigned wholly to one.
 */
class UsageStatsModule : Module() {

  private val context: Context
    get() = appContext.reactContext ?: throw MissingContextException()

  override fun definition() = ModuleDefinition {
    Name("UsageStats")

    /**
     * Whether the user has granted "Usage access" to this app.
     *
     * Checked through AppOpsManager rather than checkSelfPermission, because
     * PACKAGE_USAGE_STATS is an app-op style permission: checkSelfPermission
     * reports it as denied even when the user has enabled it in Settings.
     */
    Function("hasPermission") {
      return@Function hasUsageAccess()
    }

    /** Opens the system screen where the user grants usage access. */
    Function("openPermissionSettings") {
      val intent = Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS).apply {
        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
      }
      context.startActivity(intent)
      return@Function true
    }

    /**
     * Daily usage between two epoch-millisecond instants.
     *
     * @param startMs inclusive range start
     * @param endMs exclusive range end
     * @param lateNightStartHour hour at which the late-night window opens (23)
     * @param lateNightEndHour hour at which it closes (6)
     */
    AsyncFunction("getDailyUsage") { startMs: Double, endMs: Double, lateNightStartHour: Int, lateNightEndHour: Int ->
      if (!hasUsageAccess()) throw PermissionDeniedException()
      return@AsyncFunction buildDailyUsage(
        startMs.toLong(),
        endMs.toLong(),
        lateNightStartHour,
        lateNightEndHour
      )
    }

    /**
     * Read the recent window and PERSIST each day locally.
     *
     * Separate from getDailyUsage because the two answer different questions.
     * getDailyUsage answers "what does Android say right now", for a screen to
     * display. This answers "make sure these days survive", and it must be
     * able to run when no screen is open and no backend is reachable - which,
     * for an API hosted on a laptop over a phone hotspot, is most of the time.
     *
     * Capture is the step with a deadline. UsageStatsManager keeps the event
     * stream for roughly a week, so a day not read inside that window is gone
     * permanently; upload can be retried forever. Hence this writes to local
     * storage first and reports what is now waiting to be sent.
     *
     * Returns per-day outcomes as well as totals, so the caller can state that
     * a degraded read was refused rather than silently appearing to do nothing.
     */
    AsyncFunction("captureDailyUsage") { days: Int, lateNightStartHour: Int, lateNightEndHour: Int ->
      if (!hasUsageAccess()) throw PermissionDeniedException()

      val zone = ZoneId.systemDefault()
      val today = LocalDate.now(zone)
      val nowMs = System.currentTimeMillis()

      // Start at midnight `days-1` ago so whole local days are covered, rather
      // than a rolling window that splits the earliest day in half.
      val startMs = today.minusDays((days - 1).toLong())
        .atStartOfDay(zone).toInstant().toEpochMilli()

      val result = buildDailyUsage(startMs, nowMs, lateNightStartHour, lateNightEndHour)

      @Suppress("UNCHECKED_CAST")
      val dayMaps = (result["days"] as? List<Map<String, Any?>>) ?: emptyList()

      var created = 0
      var updated = 0
      var unchanged = 0
      var refused = 0

      for (day in dayMaps) {
        val date = day["date"] as? String ?: continue

        // A day Android reported no activity for is NOT stored as a zero day.
        // It is either genuinely unused or older than the retention window,
        // and the two are indistinguishable here. The store would refuse to
        // let it overwrite a real day anyway, but not creating one in the
        // first place keeps a fabricated quiet day out of the history.
        val hadUsage = day["hadAnyUsage"] as? Boolean ?: false
        if (!hadUsage) continue

        when (CapturedDayStore.putDay(context, date, toJson(day), today)) {
          PutResult.CREATED -> created++
          PutResult.UPDATED -> updated++
          PutResult.UNCHANGED -> unchanged++
          PutResult.REFUSED_DEGRADED -> refused++
        }
      }

      CapturedDayStore.recordCapture(context, nowMs)
      CapturedDayStore.prune(context, zone)

      return@AsyncFunction mapOf(
        "daysRead" to dayMaps.size,
        "created" to created,
        "updated" to updated,
        "unchanged" to unchanged,
        // Reads refused for reporting LESS than the stored day. Expected and
        // healthy once a date leaves Android's window: it means the local copy
        // is now the only record, and it was protected.
        "refusedDegraded" to refused,
        "capturedDaysTotal" to CapturedDayStore.capturedDates(context).size,
        "pendingUpload" to CapturedDayStore.pendingDays(context).size,
        "capturedAtMs" to nowMs.toDouble()
      )
    }

    /**
     * Captured days the backend has not yet accepted, oldest first, as JSON
     * strings for the caller to parse.
     */
    AsyncFunction("getPendingDays") {
      return@AsyncFunction CapturedDayStore.pendingDays(context)
    }

    /**
     * Queue every captured day for upload again.
     *
     * Used when the backend changes identity - most consequentially when it
     * moved from a laptop onto this device, which left every day flagged as
     * delivered to a database the app can no longer reach.
     */
    Function("markAllDaysPending") {
      CapturedDayStore.markAllPending(context)
      return@Function CapturedDayStore.pendingDays(context).size
    }

    /** Mark dates the backend accepted, so they stop being re-sent. */
    Function("markDaysUploaded") { dates: List<String> ->
      CapturedDayStore.markUploaded(context, dates)
      return@Function CapturedDayStore.pendingDays(context).size
    }

    /** What is held locally, for the app to show the state of capture. */
    Function("getCaptureStatus") {
      val dates = CapturedDayStore.capturedDates(context)
      return@Function mapOf(
        "capturedDays" to dates.size,
        "pendingUpload" to CapturedDayStore.pendingDays(context).size,
        "firstDate" to dates.firstOrNull(),
        "lastDate" to dates.lastOrNull(),
        "lastCaptureMs" to CapturedDayStore.lastCaptureMs(context)?.toDouble(),
        "retentionNote" to
          "Android keeps its event stream for about a week. These days are " +
          "stored on the device so they outlive that window; a day never " +
          "captured inside it cannot be recovered."
      )
    }

    /**
     * Per-app totals over the whole range, so the UI can show which apps
     * dominate and let the user correct a category if Android's is wrong.
     */
    /**
     * Read a file placed in this app's own external files folder
     * (Android/data/<package>/files), or null if it is not there.
     *
     * Used for one-off imports delivered over USB - e.g. history copied from
     * Digital Wellbeing, which keeps daily totals for weeks after Android has
     * discarded the detailed events this app reads. Only bare file names are
     * accepted, so nothing outside that folder can be reached.
     */
    Function("readImportFile") { name: String ->
      val file = importFile(name) ?: return@Function null
      return@Function if (file.isFile) file.readText() else null
    }

    /**
     * Small persistent app flags (e.g. "onboarded"). SharedPreferences rather
     * than a JS storage library: the app already ships this module, and a
     * boolean that must survive restarts does not justify a new dependency.
     */
    Function("getAppFlag") { key: String ->
      return@Function context.getSharedPreferences(APP_FLAGS, Context.MODE_PRIVATE)
        .getBoolean(key, false)
    }

    Function("setAppFlag") { key: String, value: Boolean ->
      context.getSharedPreferences(APP_FLAGS, Context.MODE_PRIVATE)
        .edit().putBoolean(key, value).apply()
      return@Function true
    }

    // ----- screen-time warnings -----------------------------------------

    /** Store the user's usual / unusual daily levels and start or stop the 15-minute check. */
    Function("configureScreenAlert") { enabled: Boolean, usualMinutes: Int, highMinutes: Int, basisDays: Int ->
      ScreenTimeAlert.configure(context, enabled, usualMinutes, highMinutes, basisDays)
      return@Function true
    }

    Function("setScreenAlertEnabled") { enabled: Boolean ->
      ScreenTimeAlert.setEnabled(context, enabled)
      return@Function true
    }

    Function("getScreenAlertState") {
      return@Function ScreenTimeAlert.state(context)
    }

    Function("sendTestScreenAlert") {
      return@Function ScreenTimeAlert.sendTest(context)
    }

    /** Android 13+ asks before an app may post notifications. */
    AsyncFunction("requestPostNotifications") { promise: Promise ->
      val permissions = appContext.permissions
      if (Build.VERSION.SDK_INT < 33 || permissions == null) {
        promise.resolve(mapOf("granted" to ScreenTimeAlert.notificationsAllowed(context)))
        return@AsyncFunction
      }
      Permissions.askForPermissionsWithPermissionsManager(
        permissions,
        promise,
        android.Manifest.permission.POST_NOTIFICATIONS
      )
    }

    /** Remove an import file once it has been processed. */
    Function("deleteImportFile") { name: String ->
      return@Function importFile(name)?.delete() ?: false
    }

    AsyncFunction("getAppBreakdown") { startMs: Double, endMs: Double ->
      if (!hasUsageAccess()) throw PermissionDeniedException()
      return@AsyncFunction buildAppBreakdown(startMs.toLong(), endMs.toLong())
    }

    // ----- call log -------------------------------------------------------

    /** Whether READ_CALL_LOG has been granted. */
    Function("hasCallLogPermission") {
      return@Function context.checkCallingOrSelfPermission(
        android.Manifest.permission.READ_CALL_LOG
      ) == PackageManager.PERMISSION_GRANTED
    }

    /** Shows the standard runtime permission dialog for the call log. */
    AsyncFunction("requestCallLogPermission") { promise: Promise ->
      val permissions = appContext.permissions
      if (permissions == null) {
        promise.reject(
          CodedException(
            "ERR_NO_PERMISSIONS_MANAGER",
            "The permissions manager is unavailable in this build.",
            null
          )
        )
        return@AsyncFunction
      }
      Permissions.askForPermissionsWithPermissionsManager(
        permissions,
        promise,
        android.Manifest.permission.READ_CALL_LOG
      )
    }

    // ----- notification access -------------------------------------------

    /** Whether the user has granted notification access. */
    Function("hasNotificationAccess") {
      return@Function NotificationCounterService.isEnabled(context)
    }

    /** Opens the system screen for granting notification access. */
    Function("openNotificationSettings") {
      val intent = Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS).apply {
        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
      }
      context.startActivity(intent)
      return@Function true
    }

    /**
     * When notification counting began, as epoch ms, or null if never.
     *
     * The app needs this to say plainly that days before this moment have an
     * UNKNOWN notification count rather than a count of zero.
     */
    Function("notificationObservingSince") {
      return@Function NotificationCounterService.observingSince(context)?.toDouble()
    }

    // ----- embedded Python backend ---------------------------------------

    /**
     * Whether this build contains the embedded Python interpreter.
     *
     * The app must work without it - capture, stored history and the charts
     * over them do not need Python - so JavaScript checks rather than assumes.
     */
    Function("isPythonAvailable") {
      return@Function PythonBridge.isAvailable()
    }

    /**
     * Start Python and point it at this app's private storage.
     *
     * Async because it opens the database and may unpack the interpreter on
     * first launch, which must not block the UI thread.
     */
    AsyncFunction("configurePython") {
      return@AsyncFunction PythonBridge.configure(context)
    }

    /**
     * Run one backend endpoint, replacing what used to be an HTTP request.
     *
     * Async because this is where the ML actually runs: fitting a forecaster
     * or scoring a baseline takes long enough to drop frames if it ran on the
     * UI thread.
     */
    AsyncFunction("callPython") { name: String, paramsJson: String ->
      return@AsyncFunction PythonBridge.call(context, name, paramsJson)
    }

    /** Endpoint names this build serves, for diagnostics. */
    Function("pythonRoutes") {
      return@Function PythonBridge.routes()
    }

    /** Diagnostic info, useful when usage looks wrong or empty. */
    AsyncFunction("getDiagnostics") {
      val manager = usageStatsManager()
      val now = System.currentTimeMillis()
      val weekAgo = now - 7L * 24 * 60 * 60 * 1000
      var eventCount = 0
      var earliest = Long.MAX_VALUE
      if (hasUsageAccess()) {
        val events = manager.queryEvents(weekAgo, now)
        val event = UsageEvents.Event()
        while (events.hasNextEvent()) {
          events.getNextEvent(event)
          eventCount++
          if (event.timeStamp < earliest) earliest = event.timeStamp
        }
      }
      return@AsyncFunction mapOf(
        "hasPermission" to hasUsageAccess(),
        "androidSdkInt" to Build.VERSION.SDK_INT,
        "timeZone" to ZoneId.systemDefault().id,
        "eventsInLastSevenDays" to eventCount,
        "earliestEventMs" to if (earliest == Long.MAX_VALUE) null else earliest.toDouble(),
        "note" to "Android retains the detailed event stream for roughly one week."
      )
    }
  }

  // -------------------------------------------------------------------------
  // Permission
  // -------------------------------------------------------------------------

  private fun hasUsageAccess(): Boolean {
    val appOps = context.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
    val mode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
      appOps.unsafeCheckOpNoThrow(
        AppOpsManager.OPSTR_GET_USAGE_STATS,
        Process.myUid(),
        context.packageName
      )
    } else {
      @Suppress("DEPRECATION")
      appOps.checkOpNoThrow(
        AppOpsManager.OPSTR_GET_USAGE_STATS,
        Process.myUid(),
        context.packageName
      )
    }

    // MODE_DEFAULT means "fall back to the permission check", which for this
    // op means consulting the manifest declaration.
    return if (mode == AppOpsManager.MODE_DEFAULT) {
      context.checkCallingOrSelfPermission(
        android.Manifest.permission.PACKAGE_USAGE_STATS
      ) == PackageManager.PERMISSION_GRANTED
    } else {
      mode == AppOpsManager.MODE_ALLOWED
    }
  }

  private fun importFile(name: String): java.io.File? {
    if (!Regex("^[A-Za-z0-9_.-]{1,64}$").matches(name) || name.startsWith(".")) return null
    val dir = context.getExternalFilesDir(null) ?: return null
    return java.io.File(dir, name)
  }

  private fun usageStatsManager(): UsageStatsManager =
    context.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager

  // -------------------------------------------------------------------------
  // Session reconstruction
  // -------------------------------------------------------------------------

  /** Session reconstruction lives in SessionReader, shared with the
   *  background screen-time check so both count time identically. */
  private fun collectSessions(startMs: Long, endMs: Long): List<SessionReader.Session> =
    SessionReader.collect(context, startMs, endMs)

  // -------------------------------------------------------------------------
  // Categorisation
  // -------------------------------------------------------------------------

  /**
   * Maps a package to one of our four buckets.
   *
   * Android's declared `ApplicationInfo.category` is used first, because it is
   * the app author's own classification. Many apps leave it undefined, so a
   * small documented fallback list covers the most common ones. Anything still
   * unknown is reported as "other" rather than guessed - the per-app breakdown
   * is exposed so the user can see exactly what landed where.
   */
  private fun categoryFor(packageName: String, info: ApplicationInfo?): String {
    if (info != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
      when (info.category) {
        ApplicationInfo.CATEGORY_SOCIAL -> return "social"
        ApplicationInfo.CATEGORY_GAME -> return "gaming"
        ApplicationInfo.CATEGORY_PRODUCTIVITY -> return "productivity"
        ApplicationInfo.CATEGORY_VIDEO, ApplicationInfo.CATEGORY_AUDIO -> return "other"
      }
    }

    val lower = packageName.lowercase()
    return when {
      KNOWN_SOCIAL.any { lower.contains(it) } -> "social"
      KNOWN_GAMING.any { lower.contains(it) } -> "gaming"
      KNOWN_PRODUCTIVITY.any { lower.contains(it) } -> "productivity"
      else -> "other"
    }
  }

  private fun appLabel(packageName: String, pm: PackageManager, info: ApplicationInfo?): String {
    return try {
      if (info != null) pm.getApplicationLabel(info).toString() else packageName
    } catch (_: Exception) {
      packageName
    }
  }

  private fun applicationInfo(packageName: String, pm: PackageManager): ApplicationInfo? =
    try {
      pm.getApplicationInfo(packageName, 0)
    } catch (_: PackageManager.NameNotFoundException) {
      null
    }

  private fun timeExcludedPackages(@Suppress("UNUSED_PARAMETER") pm: PackageManager): Set<String> =
    SessionReader.timeExcluded(context)

  private fun launchExcludedPackages(@Suppress("UNUSED_PARAMETER") pm: PackageManager): Set<String> =
    SessionReader.launchExcluded(context)

  // -------------------------------------------------------------------------
  // Aggregation
  // -------------------------------------------------------------------------

  private fun isLateNightHour(hour: Int, startHour: Int, endHour: Int): Boolean =
    // The window wraps midnight (e.g. 23 -> 6), so it is a union of two ranges.
    if (startHour <= endHour) hour in startHour until endHour
    else hour >= startHour || hour < endHour

  private fun buildDailyUsage(
    startMs: Long,
    endMs: Long,
    lateNightStartHour: Int,
    lateNightEndHour: Int
  ): Map<String, Any?> {
    val sessions = collectSessions(startMs, endMs)
    val pm = context.packageManager
    val zone = ZoneId.systemDefault()

    val infoCache = mutableMapOf<String, ApplicationInfo?>()
    val categoryCache = mutableMapOf<String, String>()

    // date -> accumulating totals
    data class DayTotals(
      var totalMs: Long = 0,
      var socialMs: Long = 0,
      var gamingMs: Long = 0,
      var productivityMs: Long = 0,
      var otherMs: Long = 0,
      var lateNightMs: Long = 0,
      var launches: Int = 0
    )

    val byDate = linkedMapOf<String, DayTotals>()
    val timeExcluded = timeExcludedPackages(pm)
    val launchExcluded = launchExcludedPackages(pm)

    for (session in sessions) {
      // The launcher and system UI are navigation, not app use - left out of
      // screen time and of app opens (see timeExcludedPackages).
      if (session.packageName in timeExcluded) continue

      val info = infoCache.getOrPut(session.packageName) { applicationInfo(session.packageName, pm) }
      val category = categoryCache.getOrPut(session.packageName) {
        categoryFor(session.packageName, info)
      }

      // Walk the session hour by hour so both the day and the late-night
      // window receive the correct share of a session that crosses either.
      var cursor = session.start
      var firstSlice = true
      while (cursor < session.end) {
        val dateTime = LocalDateTime.ofInstant(Instant.ofEpochMilli(cursor), zone)
        val hourEndMs = dateTime
          .withMinute(0).withSecond(0).withNano(0)
          .plusHours(1)
          .atZone(zone).toInstant().toEpochMilli()

        val sliceEnd = min(hourEndMs, session.end)
        val sliceMs = sliceEnd - cursor
        val dateKey = dateTime.toLocalDate().toString()
        val totals = byDate.getOrPut(dateKey) { DayTotals() }

        totals.totalMs += sliceMs
        when (category) {
          "social" -> totals.socialMs += sliceMs
          "gaming" -> totals.gamingMs += sliceMs
          "productivity" -> totals.productivityMs += sliceMs
          else -> totals.otherMs += sliceMs
        }
        if (isLateNightHour(dateTime.hour, lateNightStartHour, lateNightEndHour)) {
          totals.lateNightMs += sliceMs
        }
        // An app open is counted once, on the day the session began - and
        // never for the launcher or system UI, which are navigation rather
        // than apps the user chose to open.
        if (firstSlice) {
          if (session.packageName !in launchExcluded) {
            totals.launches += 1
          }
          firstSlice = false
        }

        cursor = sliceEnd
      }
    }

    // ---- additional signals ------------------------------------------------

    val (unlocksByDate, unlockSignal) =
      DeviceSignals.unlocksByDate(usageStatsManager(), startMs, endMs, zone)

    val callLogGranted = context.checkCallingOrSelfPermission(
      android.Manifest.permission.READ_CALL_LOG
    ) == PackageManager.PERMISSION_GRANTED
    val callsByDate =
      if (callLogGranted) DeviceSignals.callsByDate(context, startMs, endMs, zone)
      else emptyMap()

    val notificationAccess = NotificationCounterService.isEnabled(context)
    val notificationCounts =
      if (notificationAccess) NotificationCounterService.countsByDate(context) else emptyMap()
    val observingSince = NotificationCounterService.observingSince(context)

    // Session boundaries feed the sleep estimate. ANY use of the phone -
    // including the home screen - means the person was awake, so only this
    // app is left out (opening it is not evidence about sleep either way).
    val boundaries = sessions
      .filter { it.packageName != context.packageName }
      .map { it.start to it.end }
      .sortedBy { it.first }

    // Emit one entry per calendar day in the requested range, including days
    // with no usage - a zero day is a real observation, and omitting it would
    // leave a gap the baseline would misread as missing data.
    val firstDate = LocalDateTime.ofInstant(Instant.ofEpochMilli(startMs), zone).toLocalDate()
    val lastDate = LocalDateTime.ofInstant(Instant.ofEpochMilli(endMs - 1), zone).toLocalDate()

    // Read once, outside the loop, so every day in one batch is judged
    // against the same instant rather than a clock that moves as we iterate.
    val nowMs = System.currentTimeMillis()

    val days = mutableListOf<Map<String, Any?>>()
    var date: LocalDate = firstDate
    while (!date.isAfter(lastDate)) {
      val key = date.toString()
      val totals = byDate[key]
      val call = callsByDate[key]
      val sleep = DeviceSignals.estimateSleepForNight(date, boundaries, zone)

      // Notifications are null - not zero - for any part of a day we were not
      // watching. Zero would assert "no notifications arrived", which we
      // cannot know.
      //
      // Judged on actual COVERAGE of the day, not on when observation first
      // began. The old test was `observingSince <= dayStart`, which a
      // revoke/re-grant cycle defeated: the stored start still predated the
      // gap, so days nobody watched passed the test and took the `?: 0`
      // branch. A partially covered day is rejected too rather than reported
      // low - the day access was granted is the common case, and its tally
      // covers only the hours since.
      // A small tolerance is allowed - see MAX_UNOBSERVED_MINUTES - because
      // Android rebinds listeners routinely and a zero tolerance would let
      // one reboot disqualify a whole day.
      val observedMs =
        if (notificationAccess) {
          NotificationCounterService.observedMsOnDate(context, date, zone)
        } else {
          0L
        }

      // For a day still in progress, only the elapsed part of it COULD have
      // been observed, so that is what coverage is measured against. Sending
      // the full 1440 would make every sync before midnight look like a
      // coverage failure. Today's count is refreshed by the next sync, and
      // the models exclude the current day in any case.
      val dayEndMs = date.plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli()
      val observableMs = if (dayEndMs > nowMs) {
        nowMs - date.atStartOfDay(zone).toInstant().toEpochMilli()
      } else {
        NotificationCounterService.dayLengthMs(date, zone)
      }

      val usable =
        notificationAccess && NotificationCounterService.isDateUsable(context, date, zone)

      val notificationCount: Int? = when {
        !notificationAccess -> null
        !usable -> null
        else -> notificationCounts[key] ?: 0
      }

      days.add(
        mapOf(
          "date" to key,
          "totalScreenMinutes" to msToMinutes(totals?.totalMs ?: 0),
          "socialMinutes" to msToMinutes(totals?.socialMs ?: 0),
          "gamingMinutes" to msToMinutes(totals?.gamingMs ?: 0),
          "productivityMinutes" to msToMinutes(totals?.productivityMs ?: 0),
          "otherMinutes" to msToMinutes(totals?.otherMs ?: 0),
          "lateNightMinutes" to msToMinutes(totals?.lateNightMs ?: 0),
          "appLaunches" to (totals?.launches ?: 0),
          "unlockCount" to unlocksByDate[key],
          "callsCount" to call?.count,
          "callDurationMinutes" to call?.durationMinutes,
          "notificationCount" to notificationCount,
          // Coverage is sent alongside the count so the backend can apply the
          // same rule independently rather than trusting the client. Both
          // numbers travel, not just a verdict, so the rule stays auditable
          // and a disagreement is visible instead of silent.
          "notificationObservedMinutes" to msToMinutes(observedMs),
          "notificationDayMinutes" to msToMinutes(observableMs),
          // Sleep is an ESTIMATE from inactivity, never a measurement.
          "sleepHours" to sleep?.hours,
          "sleepIsEstimated" to true,
          "sleepConfident" to (sleep?.confident ?: false),
          "sleepReason" to sleep?.reason,
          "hadAnyUsage" to (totals != null),
          // Lets the store tell a corrected re-read from a degraded one.
          "calcVersion" to CURRENT_CALC_VERSION
        )
      )
      date = date.plusDays(1)
    }

    return mapOf(
      "days" to days,
      "sessionCount" to sessions.size,
      "source" to "events",
      "lateNightWindow" to "${lateNightStartHour}:00-${lateNightEndHour}:00",
      // Which sources contributed, so the app can explain every gap rather
      // than leaving the user to guess why a column is empty.
      "sources" to mapOf(
        "usageEvents" to true,
        "unlockSignal" to unlockSignal,
        "callLog" to callLogGranted,
        "notificationAccess" to notificationAccess,
        "notificationObservingSinceMs" to observingSince?.toDouble(),
        // Every observation window, so the app can show WHY a particular day
        // has no count - and so a gap is a visible fact rather than an
        // unexplained hole in the data.
        "notificationSessions" to NotificationCounterService.sessions(context).map { (start, end) ->
          mapOf("startMs" to start.toDouble(), "endMs" to end?.toDouble())
        },
        "batteryDrain" to false
      ),
      "notes" to listOf(
        "Screen time, app categories, launches and late-night minutes are " +
          "reconstructed from the Android usage-event stream.",
        "hadAnyUsage = false means Android reported no foreground activity. " +
          "That may mean the phone was unused OR that the day predates " +
          "Android's event retention window of roughly one week - the two are " +
          "indistinguishable through this API.",
        "notificationCount is null unless the listener was observing for the " +
          "WHOLE of that local day. Android keeps no notification history to " +
          "read, so a day that was watched only in part is genuinely unknown " +
          "rather than low: the day access was granted is reported null, and " +
          "so is any day inside a gap where access had been revoked. " +
          "notificationObservedMinutes and notificationDayMinutes carry the " +
          "coverage this decision was made from.",
        "sleepHours is an ESTIMATE: the longest inactivity gap between 20:00 " +
          "and 12:00. Check sleepConfident before relying on it.",
        "battery drain is not available: Android retains no historical record " +
          "of daily battery consumption, and sampling it irregularly would " +
          "produce a figure with no defensible accuracy."
      )
    )
  }

  /**
   * Serialise one day map for storage.
   *
   * Written generically rather than field-by-field on purpose: the day map is
   * built in one place and a hand-written serialiser would have to be edited
   * every time a field is added there, with a silently dropped field as the
   * failure mode. A null is stored as JSON null rather than omitted, so
   * "measured as absent" stays distinguishable from "this build did not know
   * about the field".
   */
  private fun toJson(day: Map<String, Any?>): org.json.JSONObject {
    val json = org.json.JSONObject()
    for ((key, value) in day) {
      if (value == null) json.put(key, org.json.JSONObject.NULL) else json.put(key, value)
    }
    return json
  }

  private fun buildAppBreakdown(startMs: Long, endMs: Long): Map<String, Any?> {
    val sessions = collectSessions(startMs, endMs)
    val pm = context.packageManager

    val totals = mutableMapOf<String, Long>()
    val launches = mutableMapOf<String, Int>()
    val launchExcluded = launchExcludedPackages(pm)
    val timeExcluded = timeExcludedPackages(pm)

    for (session in sessions) {
      totals[session.packageName] = (totals[session.packageName] ?: 0) + (session.end - session.start)
      launches[session.packageName] = (launches[session.packageName] ?: 0) + 1
    }

    val apps = totals.entries
      .sortedByDescending { it.value }
      .map { (packageName, ms) ->
        val info = applicationInfo(packageName, pm)
        mapOf(
          "packageName" to packageName,
          "label" to appLabel(packageName, pm, info),
          "category" to categoryFor(packageName, info),
          "categorySource" to if (
            info != null &&
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            info.category != ApplicationInfo.CATEGORY_UNDEFINED
          ) "declared" else "heuristic",
          "minutes" to msToMinutes(ms),
          "launches" to (launches[packageName] ?: 0),
          // Shown but NOT included in daily totals, so the breakdown stays a
          // complete picture while the headline figures match the definition
          // Digital Wellbeing appears to use.
          "countedInScreenTime" to (packageName !in timeExcluded),
          "countedInAppOpens" to (packageName !in launchExcluded)
        )
      }

    return mapOf(
      "apps" to apps,
      "note" to (
        "categorySource = 'declared' means the app author set the Android " +
          "category; 'heuristic' means it was inferred from the package name " +
          "and may be wrong."
        )
    )
  }

  private fun msToMinutes(ms: Long): Int = Math.round(ms / 60000.0).toInt()

  companion object {
    // Event type constants, compared numerically so the same code works
    // across API levels. ACTIVITY_RESUMED (29+) and MOVE_TO_FOREGROUND (21+)
    // are the same value, as are ACTIVITY_PAUSED and MOVE_TO_BACKGROUND.
    private const val APP_FLAGS = "app_flags"


    // Documented fallback lists, used ONLY when an app declares no category.
    private val KNOWN_SOCIAL = listOf(
      "instagram", "facebook", "whatsapp", "snapchat", "twitter", "x.android",
      "tiktok", "reddit", "telegram", "discord", "pinterest", "linkedin",
      "threads", "messenger", "wechat", "line.android", "signal"
    )
    private val KNOWN_GAMING = listOf(
      "game", "pubg", "freefire", "garena", "roblox", "minecraft",
      "supercell", "clashofclans", "candycrush", "king.", "ludo", "chess"
    )
    private val KNOWN_PRODUCTIVITY = listOf(
      "docs", "sheets", "slides", "gmail", "outlook", "office", "word",
      "excel", "powerpoint", "notion", "evernote", "keep", "calendar",
      "drive", "dropbox", "slack", "teams", "zoom", "classroom", "github",
      "code", "duolingo", "khan"
    )
  }
}

/** Raised when the module is used before usage access has been granted. */
class PermissionDeniedException : CodedException(
  "ERR_USAGE_ACCESS_DENIED",
  "Usage access has not been granted. Call openPermissionSettings() and enable " +
    "this app under Settings > Apps > Special app access > Usage access.",
  null
)

class MissingContextException : CodedException(
  "ERR_NO_CONTEXT",
  "The Android context was unavailable.",
  null
)
