package expo.modules.usagestats

import android.content.Context
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId

/**
 * Counts notifications as they arrive.
 *
 * WHY THIS CANNOT BACKFILL - AND WHY THAT IS NOT A BUG
 * ----------------------------------------------------
 * Android keeps no queryable notification history for third-party apps.
 * There is no equivalent of UsageStatsManager for notifications: a
 * NotificationListenerService is told about notifications in real time and
 * that is the only access there is.
 *
 * So notification counts exist from the moment the user grants notification
 * access, and for every day before that the honest answer is "unknown" -
 * which the pipeline represents as null, never as zero. Digital Wellbeing
 * has exactly the same limitation; it can only chart notifications from when
 * it started observing.
 *
 * STORAGE
 * -------
 * Counts are kept in SharedPreferences keyed by local date. SharedPreferences
 * is appropriate here because the data is tiny (one integer per day), must
 * survive the service being killed and restarted by the system, and is read
 * far more often than written.
 *
 * Entries older than the retention window are pruned on write, so the store
 * cannot grow without bound on a device left running for years.
 */
class NotificationCounterService : NotificationListenerService() {

  /**
   * Observation begins here, not at the first notification.
   *
   * The system calls this when it binds the listener, which is the moment
   * counting actually becomes possible. Recording it here rather than on the
   * first counted notification matters for a quiet period: granted at 09:00
   * with nothing arriving until 14:00, the old code recorded 14:00 and so
   * disowned five hours it had genuinely been watching.
   */
  override fun onListenerConnected() {
    super.onListenerConnected()
    openSession(applicationContext)
  }

  /**
   * Observation stops here - and this is the callback that makes re-granting
   * safe.
   *
   * The system calls it when the listener is unbound, which includes the user
   * revoking notification access. Without it, a revoke/re-grant cycle left the
   * single `observing_since_ms` pointing at the ORIGINAL grant, so every day
   * in the gap looked observed and its count came through as a hard zero -
   * asserting "no notifications arrived" for days nobody was watching.
   */
  override fun onListenerDisconnected() {
    closeSession(applicationContext)
    super.onListenerDisconnected()
  }

  override fun onNotificationPosted(sbn: StatusBarNotification?) {
    if (sbn == null) return

    // Ignore this app's own notifications - counting them would mean the act
    // of reporting on notifications inflates the count.
    if (sbn.packageName == packageName) return

    // Ongoing notifications (music players, navigation, downloads) are
    // persistent status displays rather than interruptions, and counting
    // every update would wildly overstate the figure.
    if (sbn.isOngoing) return

    // Group summaries duplicate their children: a chat app posting 5 messages
    // emits 5 notifications plus 1 summary. Skipping summaries avoids
    // double counting.
    val isGroupSummary =
      (sbn.notification.flags and android.app.Notification.FLAG_GROUP_SUMMARY) != 0
    if (isGroupSummary) return

    increment(applicationContext, sbn.postTime)
  }

  companion object {
    private const val PREFS_NAME = "usage_stats_notifications"
    private const val KEY_PREFIX = "count_"

    /**
     * Legacy single timestamp: when counting first began.
     *
     * Superseded by KEY_SESSIONS, and kept only so an existing install does
     * not lose its history on upgrade - see [sessions]. It cannot express a
     * gap, which is precisely why it was replaced.
     */
    private const val KEY_OBSERVING_SINCE = "observing_since_ms"

    /**
     * Observation windows, as `start-end;start-end;start-`.
     *
     * An entry with no end is the window still open. Millisecond epochs,
     * semicolon-separated, so the whole record stays one small string in
     * SharedPreferences and needs no database.
     */
    private const val KEY_SESSIONS = "observation_sessions"

    /** Days of history to retain. Beyond this, entries are pruned. */
    private const val RETENTION_DAYS = 120L

    /**
     * How much of a day may go unobserved before its count is unusable.
     *
     * MUST stay equal to MAX_UNOBSERVED_MINUTES in backend/app/schemas/sync.py.
     * The two are deliberately duplicated so the rule holds even against an
     * older client, exactly as the sleep plausibility band is.
     *
     * WHY NOT ZERO
     * ------------
     * Demanding perfect coverage sounds stricter and is simply wrong in
     * practice. Android unbinds notification listeners routinely - at reboot,
     * on app update, under battery-saver teardown - and rebinds within
     * seconds to a couple of minutes. With a zero tolerance, one reboot would
     * disqualify a whole day, so a phone restarted daily would never report a
     * single notification count. The feature would be technically correct and
     * useless.
     *
     * 15 minutes bounds the understatement at about 1% of a day, which is far
     * below the noise in a daily notification total, while still rejecting
     * every case that motivated this rule: the day access is granted leaves
     * ~1359 minutes unobserved, and a revoked day leaves all 1440.
     *
     * The raw coverage numbers travel with each day regardless, so this
     * threshold is a stated judgement rather than a hidden one.
     */
    private const val MAX_UNOBSERVED_MINUTES = 15L

    private fun prefs(context: Context) =
      context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    private fun dateKeyFor(timestampMs: Long): String =
      LocalDateTime.ofInstant(Instant.ofEpochMilli(timestampMs), ZoneId.systemDefault())
        .toLocalDate()
        .toString()

    private fun increment(context: Context, timestampMs: Long) {
      val store = prefs(context)
      val key = KEY_PREFIX + dateKeyFor(timestampMs)

      val editor = store.edit()
      editor.putInt(key, store.getInt(key, 0) + 1)

      // A notification can only arrive while the listener is bound, so if no
      // window is open the connect callback was missed. Open one now rather
      // than count into a period we have no record of observing.
      if (sessions(context).none { it.second == null }) {
        editor.putString(
          KEY_SESSIONS,
          encodeSessions(sessions(context) + (System.currentTimeMillis() to null))
        )
      }

      pruneOldEntries(store, editor)
      editor.apply()
    }

    // ----------------------------------------------------------------------
    // Observation windows
    // ----------------------------------------------------------------------

    /**
     * Every recorded observation window, oldest first.
     *
     * An install that predates session tracking has only the legacy
     * `observing_since_ms`. That is migrated to a single still-open window, so
     * upgrading does not retroactively disown days that were genuinely
     * observed. The legacy value cannot express a gap, so a pre-upgrade
     * revoke/re-grant cycle remains invisible - nothing can recover a record
     * that was never written.
     */
    fun sessions(context: Context): List<Pair<Long, Long?>> {
      val store = prefs(context)

      val raw = store.getString(KEY_SESSIONS, null)
      if (raw.isNullOrBlank()) {
        val legacy =
          if (store.contains(KEY_OBSERVING_SINCE)) store.getLong(KEY_OBSERVING_SINCE, 0L)
          else null
        return if (legacy != null && legacy > 0L) listOf(legacy to null) else emptyList()
      }

      return raw.split(";").mapNotNull { entry ->
        if (entry.isBlank()) return@mapNotNull null
        val parts = entry.split("-")
        val start = parts.getOrNull(0)?.toLongOrNull() ?: return@mapNotNull null
        val end = parts.getOrNull(1)?.toLongOrNull()
        start to end
      }.sortedBy { it.first }
    }

    private fun encodeSessions(windows: List<Pair<Long, Long?>>): String =
      windows.joinToString(";") { (start, end) -> "$start-${end ?: ""}" }

    private fun openSession(context: Context) {
      val store = prefs(context)
      val current = sessions(context)

      // Already open: the system rebound without an intervening disconnect,
      // so treat observation as continuous rather than inventing a boundary.
      if (current.any { it.second == null }) return

      val updated = pruneSessions(current + (System.currentTimeMillis() to null))
      store.edit().putString(KEY_SESSIONS, encodeSessions(updated)).apply()
    }

    private fun closeSession(context: Context) {
      val store = prefs(context)
      val now = System.currentTimeMillis()

      val updated = sessions(context).map { (start, end) ->
        if (end == null) start to now else start to end
      }
      store.edit().putString(KEY_SESSIONS, encodeSessions(pruneSessions(updated))).apply()
    }

    private fun pruneSessions(windows: List<Pair<Long, Long?>>): List<Pair<Long, Long?>> {
      val cutoff = java.time.LocalDate.now(ZoneId.systemDefault())
        .minusDays(RETENTION_DAYS)
        .atStartOfDay(ZoneId.systemDefault())
        .toInstant()
        .toEpochMilli()
      // A window is kept while it could still overlap a retained day, so a
      // long-running window opened months ago is never dropped.
      return windows.filter { (_, end) -> end == null || end >= cutoff }
    }

    /**
     * Milliseconds of [date] during which the listener was observing.
     *
     * Compared against the day's own length by the caller, so a day shortened
     * or lengthened by a daylight-saving change is still judged correctly.
     *
     * LIMITATION, STATED RATHER THAN HIDDEN
     * -------------------------------------
     * If the process is killed without `onListenerDisconnected` - a hard
     * kill, or a battery-saver teardown - the window stays open and the gap
     * goes unrecorded, so coverage can be over-claimed across an abrupt
     * restart. The callback IS delivered when the user revokes access, which
     * is the case that would otherwise fabricate zeros, so the defect that
     * matters is covered. Closing open windows at the last counted
     * notification instead would under-claim far more often: Android rebinds
     * listeners routinely, and nearly every day would be disqualified by a
     * few idle minutes.
     */
    fun observedMsOnDate(context: Context, date: LocalDate, zone: ZoneId): Long {
      val dayStart = date.atStartOfDay(zone).toInstant().toEpochMilli()
      val dayEnd = date.plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli()
      val now = System.currentTimeMillis()

      var covered = 0L
      for ((start, end) in sessions(context)) {
        // An open window extends to now, never into the future.
        val windowEnd = end ?: now
        val overlapStart = maxOf(start, dayStart)
        val overlapEnd = minOf(windowEnd, dayEnd)
        if (overlapEnd > overlapStart) covered += overlapEnd - overlapStart
      }
      return covered
    }

    /** Length of [date] in this zone, which is not always 24 hours. */
    fun dayLengthMs(date: LocalDate, zone: ZoneId): Long =
      date.plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli() -
        date.atStartOfDay(zone).toInstant().toEpochMilli()

    /**
     * Whether [date] was observed closely enough for its count to mean
     * anything. See [MAX_UNOBSERVED_MINUTES] for why this is not exact
     * equality.
     *
     * A day still in progress is handled by the caller, not here: the unseen
     * remainder of today is not a coverage failure, and the models exclude
     * the current day for separate reasons.
     */
    fun isDateUsable(context: Context, date: LocalDate, zone: ZoneId): Boolean {
      val dayMs = dayLengthMs(date, zone)
      val now = System.currentTimeMillis()
      val dayEnd = date.plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli()

      // For today, only the elapsed part of the day can have been observed.
      val observable = if (dayEnd > now) {
        now - date.atStartOfDay(zone).toInstant().toEpochMilli()
      } else {
        dayMs
      }
      if (observable <= 0L) return false

      val unobserved = observable - observedMsOnDate(context, date, zone)
      return unobserved <= MAX_UNOBSERVED_MINUTES * 60_000L
    }

    private fun pruneOldEntries(
      store: android.content.SharedPreferences,
      editor: android.content.SharedPreferences.Editor
    ) {
      val cutoff = java.time.LocalDate.now(ZoneId.systemDefault()).minusDays(RETENTION_DAYS)
      for (key in store.all.keys) {
        if (!key.startsWith(KEY_PREFIX)) continue
        val datePart = key.removePrefix(KEY_PREFIX)
        try {
          if (java.time.LocalDate.parse(datePart).isBefore(cutoff)) {
            editor.remove(key)
          }
        } catch (_: Exception) {
          // Unparseable key - remove it rather than leave junk behind.
          editor.remove(key)
        }
      }
    }

    /**
     * Raw notification counts per local date, exactly as recorded.
     *
     * This applies NO coverage filtering - an earlier version of this comment
     * claimed it omitted days before observation began, which it never did.
     * The claim was harmless only because no count can exist for a day that
     * was never observed; it would have been actively misleading for a day
     * that was observed for part of its length.
     *
     * Deciding which of these counts describe a WHOLE day is the caller's
     * job, using [observedMsOnDate] against [dayLengthMs]. A count here is a
     * tally of what was seen, not a claim about what happened.
     */
    fun countsByDate(context: Context): Map<String, Int> {
      val store = prefs(context)
      return store.all
        .filterKeys { it.startsWith(KEY_PREFIX) }
        .mapNotNull { (key, value) ->
          val count = value as? Int ?: return@mapNotNull null
          key.removePrefix(KEY_PREFIX) to count
        }
        .toMap()
    }

    /**
     * Epoch ms when observation first began, or null if it never has.
     *
     * Derived from the recorded windows rather than stored separately, so the
     * two can no longer disagree. Retained for display only: it says when
     * counting started and nothing about whether it has run since, which is
     * why no coverage decision may be based on it.
     */
    fun observingSince(context: Context): Long? =
      sessions(context).minOfOrNull { it.first }

    /**
     * Whether the user has granted notification access to this app.
     *
     * Read from the system's enabled-listeners setting, because there is no
     * permission-check API for this.
     */
    fun isEnabled(context: Context): Boolean {
      val enabled = android.provider.Settings.Secure.getString(
        context.contentResolver,
        "enabled_notification_listeners"
      ) ?: return false
      return enabled.split(":").any { entry ->
        entry.contains(context.packageName)
      }
    }
  }
}
