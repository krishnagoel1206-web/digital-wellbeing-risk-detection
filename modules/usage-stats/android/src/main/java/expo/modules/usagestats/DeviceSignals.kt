package expo.modules.usagestats

import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.database.Cursor
import android.provider.CallLog
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * Additional per-day signals read from the device, kept separate from
 * UsageStatsModule so each source can be reasoned about on its own.
 *
 * WHAT IS HERE AND WHAT IS NOT
 * ----------------------------
 * Here: unlock counts, call log, and a sleep ESTIMATE. All three are either
 * directly measurable or derivable from data Android retains historically.
 *
 * Not here: battery drain. Android does not retain a historical record of
 * daily battery consumption, and the only way to produce one would be to
 * sample the level whenever the app happens to run. With sparse, irregular
 * samples the result would be a number with no defensible accuracy. Given
 * this project's rule against presenting unreliable figures as measurements,
 * battery drain is left unavailable (null) rather than estimated badly.
 */
object DeviceSignals {

  // -----------------------------------------------------------------------
  // Unlocks
  // -----------------------------------------------------------------------

  /** Event type constants, compared numerically for cross-version safety. */
  private const val EVENT_SCREEN_INTERACTIVE = 15
  private const val EVENT_KEYGUARD_HIDDEN = 18
  private const val EVENT_NOTIFICATION_INTERRUPTION = 12

  /**
   * Bounds within which an inactivity gap is accepted as sleep.
   *
   * Below 4h the phone was probably used during the night, so no single
   * sleep period is visible. Above 11h the gap cannot be distinguished from
   * simply not picking the phone up, so it is reported but not trusted.
   */
  private const val MIN_PLAUSIBLE_SLEEP_HOURS = 4.0
  private const val MAX_PLAUSIBLE_SLEEP_HOURS = 11.0

  /**
   * Counts phone unlocks per local date.
   *
   * KEYGUARD_HIDDEN is the precise signal: it fires when the lock screen is
   * dismissed, which is exactly "the user unlocked the phone".
   *
   * Some devices or configurations (no secure lock set) never emit it, so if
   * no KEYGUARD_HIDDEN events are seen at all we fall back to
   * SCREEN_INTERACTIVE, which counts screen-on events instead. That is a
   * slightly different quantity - it includes waking the screen without
   * unlocking - so the fallback is reported, not hidden.
   *
   * @return (counts by ISO date, signal name actually used)
   */
  fun unlocksByDate(
    manager: UsageStatsManager,
    startMs: Long,
    endMs: Long,
    zone: ZoneId
  ): Pair<Map<String, Int>, String> {
    val keyguard = mutableMapOf<String, Int>()
    val screenOn = mutableMapOf<String, Int>()

    val events = manager.queryEvents(startMs, endMs)
    val event = UsageEvents.Event()
    while (events.hasNextEvent()) {
      events.getNextEvent(event)
      val target = when (event.eventType) {
        EVENT_KEYGUARD_HIDDEN -> keyguard
        EVENT_SCREEN_INTERACTIVE -> screenOn
        else -> null
      } ?: continue

      val date = LocalDateTime.ofInstant(Instant.ofEpochMilli(event.timeStamp), zone)
        .toLocalDate()
        .toString()
      target[date] = (target[date] ?: 0) + 1
    }

    return if (keyguard.isNotEmpty()) {
      keyguard to "KEYGUARD_HIDDEN"
    } else {
      screenOn to "SCREEN_INTERACTIVE (fallback: counts screen wakes, not only unlocks)"
    }
  }

  // -----------------------------------------------------------------------
  // Notifications - the way Digital Wellbeing counts them
  // -----------------------------------------------------------------------

  /**
   * Notifications per day from Android's own NOTIFICATION_INTERRUPTION usage
   * events - one per notification that actually alerted the user. This is
   * the figure Digital Wellbeing shows as "notifications received".
   *
   * WHY NOT THE LISTENER
   * The notification listener sees every POST, and apps re-post the same
   * notification each time its content changes (a chat app updates one
   * notification per new message; progress and status notifications update
   * constantly). Measured on 14 Nov up to 16:46: listener 746, interruption
   * events 352, Digital Wellbeing ~336.
   *
   * [earliestEventMs] is the oldest event Android still holds, so callers can
   * tell a day Android has already forgotten (unknown) from a quiet day (0).
   * [visible] is false if this device withholds these events from apps.
   */
  data class NotificationEvents(
    val counts: Map<String, Int>,
    val earliestEventMs: Long?,
    val visible: Boolean,
  )

  fun notificationsByDate(
    manager: UsageStatsManager,
    startMs: Long,
    endMs: Long,
    zone: ZoneId
  ): NotificationEvents {
    val counts = mutableMapOf<String, Int>()
    var earliest: Long? = null
    var seenAny = false
    // Look back two extra days only to learn where Android's retention starts.
    val events = manager.queryEvents(startMs - 2L * 24 * 60 * 60 * 1000, endMs)
    val event = UsageEvents.Event()
    while (events.hasNextEvent()) {
      events.getNextEvent(event)
      if (earliest == null || event.timeStamp < earliest) earliest = event.timeStamp
      if (event.eventType != EVENT_NOTIFICATION_INTERRUPTION) continue
      seenAny = true
      if (event.timeStamp < startMs) continue
      val date = LocalDateTime.ofInstant(Instant.ofEpochMilli(event.timeStamp), zone)
        .toLocalDate()
        .toString()
      counts[date] = (counts[date] ?: 0) + 1
    }
    return NotificationEvents(counts, earliest, seenAny)
  }

  // -----------------------------------------------------------------------
  // Call log
  // -----------------------------------------------------------------------

  data class CallDay(val count: Int, val durationMinutes: Int)

  /**
   * Reads call counts and total duration per local date.
   *
   * Unlike usage events, the call log IS retained long-term by Android, so
   * this genuinely backfills. Requires READ_CALL_LOG, which the caller must
   * have obtained first.
   *
   * Missed and rejected calls are counted in `count` but contribute no
   * duration, which is correct: they happened, but no time was spent on them.
   */
  fun callsByDate(
    context: Context,
    startMs: Long,
    endMs: Long,
    zone: ZoneId
  ): Map<String, CallDay> {
    // Counts and seconds accumulate in LOCAL maps. An earlier version used
    // object-level maps, which two concurrent calls would have corrupted.
    val counts = mutableMapOf<String, Int>()
    val seconds = mutableMapOf<String, Long>()

    val projection = arrayOf(CallLog.Calls.DATE, CallLog.Calls.DURATION)
    val selection = "${CallLog.Calls.DATE} >= ? AND ${CallLog.Calls.DATE} < ?"
    val args = arrayOf(startMs.toString(), endMs.toString())

    var cursor: Cursor? = null
    try {
      cursor = context.contentResolver.query(
        CallLog.Calls.CONTENT_URI,
        projection,
        selection,
        args,
        "${CallLog.Calls.DATE} ASC"
      )

      if (cursor != null) {
        val dateIndex = cursor.getColumnIndexOrThrow(CallLog.Calls.DATE)
        val durationIndex = cursor.getColumnIndexOrThrow(CallLog.Calls.DURATION)

        while (cursor.moveToNext()) {
          val timestamp = cursor.getLong(dateIndex)
          val durationSeconds = cursor.getLong(durationIndex)

          val date = LocalDateTime.ofInstant(Instant.ofEpochMilli(timestamp), zone)
            .toLocalDate()
            .toString()

          counts[date] = (counts[date] ?: 0) + 1
          seconds[date] = (seconds[date] ?: 0L) + durationSeconds
        }
      }
    } catch (_: SecurityException) {
      // Permission revoked between the check and the read. Returning empty is
      // correct: no data, rather than a fabricated zero for every day.
      return emptyMap()
    } finally {
      cursor?.close()
    }

    // Seconds are summed first and converted once, so a day of many short
    // calls does not lose time to repeated per-call rounding.
    return counts.mapValues { (date, count) ->
      CallDay(
        count = count,
        durationMinutes = ((seconds[date] ?: 0L) / 60.0).roundToInt()
      )
    }
  }

  // -----------------------------------------------------------------------
  // Sleep estimate
  // -----------------------------------------------------------------------

  data class SleepEstimate(val hours: Double, val confident: Boolean, val reason: String)

  /**
   * Estimates a night's sleep as the longest period of phone inactivity
   * inside an overnight window.
   *
   * THIS IS AN ESTIMATE AND IS LABELLED AS ONE. Android exposes no sleep
   * data (only Health Connect does, and only if a tracker writes it). The
   * assumption is that the longest uninterrupted gap between 20:00 and 12:00
   * the following day corresponds to sleep.
   *
   * Known failure modes, which is why `confident` exists:
   *   - a phone left untouched during the day inflates the gap
   *   - someone who uses their phone during the night produces several short
   *     gaps and the estimate collapses
   *   - a day with no events at all yields nothing
   *
   * Estimates outside 3-14 hours are returned with confident = false, since
   * they are more likely an artefact than a real night.
   *
   * @param sessionBoundaries sorted (start, end) pairs of foreground activity
   */
  fun estimateSleepForNight(
    date: LocalDate,
    sessionBoundaries: List<Pair<Long, Long>>,
    zone: ZoneId
  ): SleepEstimate? {
    // Window: 20:00 on `date` through 12:00 the next day.
    val windowStart = date.atTime(20, 0).atZone(zone).toInstant().toEpochMilli()
    val windowEnd = date.plusDays(1).atTime(12, 0).atZone(zone).toInstant().toEpochMilli()

    val relevant = sessionBoundaries
      .mapNotNull { (start, end) ->
        val s = max(start, windowStart)
        val e = min(end, windowEnd)
        if (e > s) s to e else null
      }
      .sortedBy { it.first }

    if (relevant.isEmpty()) {
      return SleepEstimate(
        hours = 0.0,
        confident = false,
        reason = "no phone activity recorded in the overnight window, so no gap could be measured"
      )
    }

    var longestGapMs = 0L
    // Gap between the window opening and the first activity.
    longestGapMs = max(longestGapMs, relevant.first().first - windowStart)
    // Gaps between consecutive sessions.
    for (i in 0 until relevant.size - 1) {
      val gap = relevant[i + 1].first - relevant[i].second
      if (gap > longestGapMs) longestGapMs = gap
    }
    // Gap between the last activity and the window closing.
    longestGapMs = max(longestGapMs, windowEnd - relevant.last().second)

    val hours = longestGapMs / 3_600_000.0

    // Confidence band tightened to 4-11 hours.
    //
    // The first version allowed 3-14h, and a real measurement of 13.6h passed
    // as "confident" - that was a long idle stretch (phone untouched overnight
    // AND through the morning), not a 13.6-hour night. An upper bound above
    // ~11h cannot distinguish sleep from simply not picking the phone up, so
    // anything beyond it is reported as an estimate we do not trust.
    val plausible = hours in MIN_PLAUSIBLE_SLEEP_HOURS..MAX_PLAUSIBLE_SLEEP_HOURS

    return SleepEstimate(
      hours = (hours * 10).roundToInt() / 10.0,
      confident = plausible,
      reason = if (plausible) {
        "longest inactivity gap in the 20:00-12:00 window"
      } else if (hours > MAX_PLAUSIBLE_SLEEP_HOURS) {
        "longest gap was ${"%.1f".format(hours)}h, above the " +
          "${MAX_PLAUSIBLE_SLEEP_HOURS.toInt()}h ceiling - this is a long " +
          "period of not touching the phone rather than a measurable night"
      } else {
        "longest gap was only ${"%.1f".format(hours)}h, below the " +
          "${MIN_PLAUSIBLE_SLEEP_HOURS.toInt()}h floor, so the phone was " +
          "probably used during the night and no single sleep period is visible"
      }
    )
  }
}
