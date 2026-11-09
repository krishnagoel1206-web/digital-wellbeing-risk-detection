package expo.modules.usagestats

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONObject
import java.time.LocalDate
import java.time.ZoneId

/**
 * Durable storage for days already read off Android.
 *
 * WHY THIS EXISTS - THE DATA IS PERISHABLE
 * ----------------------------------------
 * UsageStatsManager keeps the detailed event stream for roughly one week.
 * Everything this project derives from it - screen time, app categories,
 * launches, late-night minutes, unlocks, the sleep estimate - is reconstructed
 * live from those events, so a day nobody read within that week is gone for
 * good. No backend, no retry and no later sync can recover it.
 *
 * That turns what looks like a convenience feature into a correctness one. The
 * personal forecaster needs 21 CONSECUTIVE measured days and the personal
 * baseline needs 14; a single week where the app was not opened does not delay
 * those targets, it resets the run.
 *
 * So capture is separated from upload. This store holds each day as soon as it
 * is read, on the device, whether or not the backend is reachable - which on a
 * laptop-hosted API over a phone hotspot is most of the time. Upload becomes a
 * later, retryable step over data that is already safe.
 *
 * WHY A RE-CAPTURE MUST NOT SIMPLY OVERWRITE
 * ------------------------------------------
 * The obvious implementation - store whatever the latest read says - destroys
 * exactly the history it is meant to protect. Reading a date whose events have
 * aged out returns no activity, which is indistinguishable from a genuinely
 * unused day. Overwriting a captured Tuesday of 8.7 hours with the empty read
 * taken nine days later would erase the measurement and replace it with a
 * fabricated quiet day.
 *
 * Hence [putDay]'s merge rules, which are the real content of this class.
 *
 * STORAGE
 * -------
 * SharedPreferences, keyed by local date, mirroring NotificationCounterService.
 * A day is one small JSON object of about fifteen numbers, so a year of
 * history is a few tens of kilobytes - well within what SharedPreferences is
 * for, and it survives the process being killed, which is the only property
 * that matters here.
 *
 * Days are returned to JavaScript as JSON STRINGS rather than maps. The
 * alternative is a JSON-to-Map converter in Kotlin whose only purpose is to
 * undo work the caller immediately redoes, and every field would have to be
 * enumerated twice, so adding one would mean editing two places.
 */
object CapturedDayStore {

  private const val PREFS_NAME = "usage_stats_captured"
  private const val DAY_PREFIX = "day_"
  private const val UPLOADED_PREFIX = "uploaded_"
  private const val KEY_LAST_CAPTURE = "last_capture_ms"

  /**
   * How long captured days are kept.
   *
   * Far longer than Android's own week, because that is the entire point, and
   * longer than any model here needs so that a year-long history can
   * accumulate without pruning quietly capping it.
   */
  private const val RETENTION_DAYS = 400L

  private fun prefs(context: Context): SharedPreferences =
    context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

  /**
   * Store one captured day, refusing a read that would lose information.
   *
   * The merge rules, in order:
   *
   *  1. A read reporting NO activity never replaces a stored day that had
   *     some. Android cannot distinguish "unused" from "older than my
   *     retention", so an empty read of a day we already measured is far more
   *     likely to be the window expiring than a day the phone sat untouched.
   *
   *  2. For a date in the PAST, a read reporting LESS screen time than the
   *     stored day never replaces it. A completed day's total cannot shrink;
   *     if it appears to, events are aging out mid-window and the earlier,
   *     fuller read is the better measurement.
   *
   *  3. TODAY is always replaced, because today's total is still
   *     accumulating and the newest read is by definition the most complete.
   *
   * Rule 2 is deliberately not applied to today - that would freeze the
   * current day at its first reading of the morning.
   */
  fun putDay(
    context: Context,
    date: String,
    payload: JSONObject,
    today: LocalDate,
  ): PutResult {
    val store = prefs(context)
    val key = DAY_PREFIX + date
    val existingRaw = store.getString(key, null)
    val serialised = payload.toString()

    // The decision itself is pure and lives in CaptureMerge.kt, where it can
    // be tested without an Android runtime. This function is reduced to
    // reading, asking, and writing - see that file for why the split matters.
    val stored = existingRaw?.let { raw ->
      runCatching { JSONObject(raw) }.getOrNull()?.let {
        DayFacts(
          hadAnyUsage = it.optBoolean("hadAnyUsage", false),
          totalScreenMinutes = it.optInt("totalScreenMinutes", 0),
        )
      }
    }

    val incoming = DayFacts(
      hadAnyUsage = payload.optBoolean("hadAnyUsage", false),
      totalScreenMinutes = payload.optInt("totalScreenMinutes", 0),
    )

    val decision = decideMerge(
      stored = stored,
      incoming = incoming,
      isToday = date == today.toString(),
      identical = serialised == existingRaw,
    )

    when (decision) {
      PutResult.REFUSED_DEGRADED, PutResult.UNCHANGED -> return decision
      PutResult.CREATED, PutResult.UPDATED -> {
        store.edit()
          .putString(key, serialised)
          .putBoolean(UPLOADED_PREFIX + date, false)
          .apply()
        return decision
      }
    }
  }

  /** Record that a capture pass ran, whether or not anything changed. */
  fun recordCapture(context: Context, atMs: Long) {
    prefs(context).edit().putLong(KEY_LAST_CAPTURE, atMs).apply()
  }

  fun lastCaptureMs(context: Context): Long? {
    val store = prefs(context)
    return if (store.contains(KEY_LAST_CAPTURE)) store.getLong(KEY_LAST_CAPTURE, 0L) else null
  }

  /**
   * Captured days not yet accepted by the backend, oldest first.
   *
   * Oldest first so that a partial upload still extends the run of
   * consecutive days from the far end, rather than leaving a hole in the
   * middle that the lag features cannot bridge.
   */
  fun pendingDays(context: Context): List<String> {
    val store = prefs(context)
    return store.all.keys
      .asSequence()
      .filter { it.startsWith(DAY_PREFIX) }
      .map { it.removePrefix(DAY_PREFIX) }
      .filter { !store.getBoolean(UPLOADED_PREFIX + it, false) }
      .sorted()
      .mapNotNull { store.getString(DAY_PREFIX + it, null) }
      .toList()
  }

  /**
   * Mark dates as accepted by the backend.
   *
   * The day itself is KEPT, not deleted. It cost a visit inside Android's
   * one-week window to obtain and can never be re-read once that window
   * passes, so the local copy stays as the durable record - if the backend
   * database is ever lost or rebuilt, the history can be replayed from here.
   */
  fun markUploaded(context: Context, dates: List<String>) {
    val editor = prefs(context).edit()
    for (date in dates) editor.putBoolean(UPLOADED_PREFIX + date, true)
    editor.apply()
  }

  /**
   * Mark every captured day as needing upload again.
   *
   * WHY THIS IS NEEDED
   * ------------------
   * The uploaded flags record that a day reached A backend - they do not
   * record WHICH. That was harmless while there was only ever one, and became
   * wrong the moment the backend moved from a laptop over the network to an
   * interpreter inside this app: every day was flagged as delivered to a
   * database that the app can no longer see, so nothing would be re-sent and
   * the user would open a working app showing an empty history.
   *
   * Resending costs nothing. The upsert is idempotent, and the captured day
   * is the same measurement either way, so re-delivering a day the backend
   * already holds changes nothing.
   */
  fun markAllPending(context: Context) {
    val store = prefs(context)
    val editor = store.edit()
    for (key in store.all.keys) {
      if (key.startsWith(UPLOADED_PREFIX)) editor.putBoolean(key, false)
    }
    editor.apply()
  }

  /** Dates held locally, oldest first. */
  fun capturedDates(context: Context): List<String> =
    prefs(context).all.keys
      .asSequence()
      .filter { it.startsWith(DAY_PREFIX) }
      .map { it.removePrefix(DAY_PREFIX) }
      .sorted()
      .toList()

  /**
   * Drop days past the retention horizon.
   *
   * A date that cannot be parsed is removed rather than left behind, so a
   * corrupted key cannot accumulate or break the sorted listings above.
   */
  fun prune(context: Context, zone: ZoneId) {
    val store = prefs(context)
    val cutoff = LocalDate.now(zone).minusDays(RETENTION_DAYS)
    val editor = store.edit()
    var changed = false

    for (key in store.all.keys) {
      val date = when {
        key.startsWith(DAY_PREFIX) -> key.removePrefix(DAY_PREFIX)
        key.startsWith(UPLOADED_PREFIX) -> key.removePrefix(UPLOADED_PREFIX)
        else -> continue
      }
      val parsed = runCatching { LocalDate.parse(date) }.getOrNull()
      if (parsed == null || parsed.isBefore(cutoff)) {
        editor.remove(key)
        changed = true
      }
    }

    if (changed) editor.apply()
  }
}
