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
 * A session opens on ACTIVITY_RESUMED and closes on the next ACTIVITY_PAUSED,
 * ACTIVITY_STOPPED, SCREEN_NON_INTERACTIVE or DEVICE_SHUTDOWN for that
 * package - or when a different package comes to the foreground, since only
 * one app is foreground at a time.
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

  private fun usageStatsManager(): UsageStatsManager =
    context.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager

  // -------------------------------------------------------------------------
  // Session reconstruction
  // -------------------------------------------------------------------------

  /** One continuous period with a single app in the foreground. */
  private data class Session(val packageName: String, val start: Long, val end: Long)

  private fun collectSessions(startMs: Long, endMs: Long): List<Session> {
    val manager = usageStatsManager()
    // Query a little before the window so a session already in progress at
    // `startMs` is captured and can be clipped, rather than being lost.
    val lookBack = 12L * 60 * 60 * 1000
    val events = manager.queryEvents(startMs - lookBack, endMs)

    val sessions = mutableListOf<Session>()
    var openPackage: String? = null
    var openStart = 0L

    val event = UsageEvents.Event()
    while (events.hasNextEvent()) {
      events.getNextEvent(event)

      when (event.eventType) {
        // ACTIVITY_RESUMED == MOVE_TO_FOREGROUND == 1.
        // A RESUMED is the only reliable "this app is now in front" signal,
        // so each one closes whatever was open and starts a new session.
        EVENT_ACTIVITY_RESUMED -> {
          if (openPackage != null) {
            sessions.add(Session(openPackage, openStart, event.timeStamp))
          }
          openPackage = event.packageName
          openStart = event.timeStamp
        }

        // The screen going off, the device locking, or shutting down all end
        // foreground use regardless of which app was in front.
        EVENT_SCREEN_NON_INTERACTIVE,
        EVENT_KEYGUARD_SHOWN,
        EVENT_DEVICE_SHUTDOWN -> {
          if (openPackage != null) {
            sessions.add(Session(openPackage, openStart, event.timeStamp))
            openPackage = null
          }
        }

        // ACTIVITY_PAUSED (2) and ACTIVITY_STOPPED (23) are DELIBERATELY
        // IGNORED.
        //
        // They are emitted per ACTIVITY, not per app, so navigating between
        // screens inside one app produces a PAUSED/STOPPED for the activity
        // being left while the app itself stays in front. Acting on them
        // closed sessions that were still live and silently discarded the
        // time until the next RESUMED.
        //
        // Measured on a real device: screen time came out around 4.9h against
        // roughly 5.8h in Digital Wellbeing. The event dump showed the cause
        // directly - e.g. RESUMED(whatsapp/Conversation) at 22:50:00 followed
        // by STOPPED(whatsapp/CommunityNavigation) at 22:50:01, which ended a
        // session that had just begun and dropped the next 28 seconds.
        //
        // They can also arrive out of order for apps that are already in the
        // background, which makes them unusable as a session terminator.
        else -> Unit
      }
    }

    // A session still open when the stream ends is closed at the earlier of
    // the window edge and NOW. Clamping to now matters because the window for
    // "today" ends at tomorrow's midnight, and closing at that edge would
    // attribute hours that have not happened yet.
    if (openPackage != null) {
      val closeAt = min(endMs, System.currentTimeMillis())
      if (closeAt > openStart) {
        sessions.add(Session(openPackage, openStart, closeAt))
      }
    }

    // Clip to the requested window and discard anything degenerate.
    val clipped = sessions.mapNotNull { session ->
      val clippedStart = max(session.start, startMs)
      val clippedEnd = min(session.end, endMs)
      if (clippedEnd > clippedStart) Session(session.packageName, clippedStart, clippedEnd) else null
    }

    return mergeAdjacent(clipped)
  }

  /**
   * Merges consecutive sessions of the SAME app separated by a tiny gap, and
   * drops sessions too short to represent real use.
   *
   * WHY THIS IS NECESSARY
   * ---------------------
   * Android emits ACTIVITY_RESUMED / ACTIVITY_PAUSED per ACTIVITY, not per
   * app. Navigating between screens inside one app therefore produces a
   * rapid pause-then-resume pair, and naive counting treats each as a
   * separate "app open".
   *
   * Measured on a real device before this fix: 1,036 app launches in a single
   * day, against 97 unlocks. That is not a plausible number of times someone
   * opened an app - it is the same handful of apps being re-counted on every
   * internal screen transition.
   *
   * Two corrections:
   *   - sessions of the same package separated by less than MERGE_GAP_MS are
   *     treated as one continuous visit;
   *   - sessions shorter than MIN_SESSION_MS are dropped, since a sub-second
   *     foreground flicker is a transition artefact, not usage.
   *
   * Total screen TIME is barely affected (the merged gaps are tiny), but the
   * launch COUNT becomes meaningful.
   */
  private fun mergeAdjacent(sessions: List<Session>): List<Session> {
    if (sessions.isEmpty()) return sessions

    val ordered = sessions.sortedBy { it.start }
    val merged = mutableListOf<Session>()

    for (session in ordered) {
      val last = merged.lastOrNull()
      if (
        last != null &&
        last.packageName == session.packageName &&
        session.start - last.end <= MERGE_GAP_MS
      ) {
        // Extend the previous visit instead of starting a new one.
        merged[merged.size - 1] = last.copy(end = max(last.end, session.end))
      } else {
        merged.add(session)
      }
    }

    return merged.filter { it.end - it.start >= MIN_SESSION_MS }
  }

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

  /**
   * Packages whose time is NOT counted at all.
   *
   * Only this app. Counting our own foreground time would mean that checking
   * your usage increases your usage.
   *
   * Note Digital Wellbeing does count this app, so its total will always sit
   * slightly above ours. That is a deliberate definitional difference, not an
   * error.
   */
  private fun timeExcludedPackages(): Set<String> = setOf(context.packageName)

  /**
   * Packages whose time COUNTS but whose appearances are not "app opens".
   *
   * WHY THE SPLIT
   * -------------
   * Two measurements against Digital Wellbeing settled this:
   *
   *   launcher counted   -> 7h04m vs DW 6h40m   (+24 min)
   *   launcher excluded  -> 6h12m vs DW 6h50m   (-38 min)
   *
   * So DW evidently DOES count home-screen time - it is simply folded into
   * "Other" rather than shown as its own slice. An earlier version of this
   * code assumed the absence of a launcher slice meant exclusion, and that
   * assumption was wrong by 38 minutes.
   *
   * But the launcher still must not inflate the OPEN count: going
   * app -> home -> app is two app opens, not three. Likewise the system UI
   * (status bar, notification shade, recents) is not an app you "open".
   *
   * Hence: their time is counted, their launches are not.
   *
   * The launcher is resolved through the HOME intent rather than hard-coded,
   * so this works on any device and respects a third-party launcher.
   */
  private fun launchExcludedPackages(pm: PackageManager): Set<String> {
    val excluded = mutableSetOf(context.packageName)

    try {
      val homeIntent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)
      val resolved = pm.resolveActivity(homeIntent, PackageManager.MATCH_DEFAULT_ONLY)
      resolved?.activityInfo?.packageName?.let { excluded.add(it) }

      // Some devices answer the HOME intent with a resolver rather than the
      // real launcher, so every registered home activity is excluded.
      for (info in pm.queryIntentActivities(homeIntent, PackageManager.MATCH_DEFAULT_ONLY)) {
        info.activityInfo?.packageName?.let { excluded.add(it) }
      }
    } catch (_: Exception) {
      // If resolution fails the launcher simply stays counted; an exception
      // here must not take the whole reading down.
    }

    // The system UI draws the status bar, notification shade and recents.
    // None of that is app usage.
    excluded.add("com.android.systemui")

    return excluded
  }

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
    val timeExcluded = timeExcludedPackages()
    val launchExcluded = launchExcludedPackages(pm)

    for (session in sessions) {
      // Only this app is left out of time entirely. The launcher and system
      // UI DO count toward screen time (Digital Wellbeing counts them too),
      // but are not counted as app opens further below.
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

    // Session boundaries, excluding this app, feed the sleep estimate.
    val boundaries = sessions
      .filter { it.packageName !in timeExcluded }
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
          "hadAnyUsage" to (totals != null)
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
    val timeExcluded = timeExcludedPackages()

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
    private const val EVENT_ACTIVITY_RESUMED = 1
    private const val EVENT_SCREEN_NON_INTERACTIVE = 16
    private const val EVENT_KEYGUARD_SHOWN = 17
    private const val EVENT_DEVICE_SHUTDOWN = 26

    /**
     * Gap below which two sessions of the same app are one visit.
     *
     * 10 seconds: long enough to absorb activity transitions inside an app
     * (which take milliseconds) and brief interruptions, short enough that
     * genuinely reopening an app after putting it down still counts twice.
     */
    private const val MERGE_GAP_MS = 10_000L

    /**
     * Shortest session counted at all. A foreground period under a second is
     * a transition artefact rather than use.
     */
    private const val MIN_SESSION_MS = 1_000L

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
