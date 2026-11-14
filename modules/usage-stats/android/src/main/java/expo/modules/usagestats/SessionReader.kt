package expo.modules.usagestats

import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import java.time.LocalDate
import java.time.ZoneId
import kotlin.math.max
import kotlin.math.min

/**
 * Foreground-session reconstruction from Android's usage events.
 *
 * Shared by UsageStatsModule (daily capture) and ScreenTimeAlert (the
 * background check during the day), so a warning can never disagree with the
 * figure the app shows.
 */
internal object SessionReader {

  /** One continuous period with a single app in the foreground. */
  data class Session(val packageName: String, val start: Long, val end: Long)

  /**
   * Foreground sessions in [startMs, endMs), non-overlapping, newest-wins.
   *
   * WHY ACTIVITY-KEYED SESSIONS (the bug this replaced)
   * ---------------------------------------------------
   * The previous rule ignored ACTIVITY_PAUSED / ACTIVITY_STOPPED entirely and
   * kept a session open until a DIFFERENT package was resumed or the screen
   * went off. It was introduced because PAUSED/STOPPED are per activity: a
   * STOPPED for WhatsApp's CommunityNavigation arriving just after RESUMED for
   * its Conversation screen ended a live WhatsApp session.
   *
   * But ignoring them over-corrected badly. Any time an app left the front
   * without another app announcing itself, the session simply stayed open.
   * Replayed against the phone's own event log (dumpsys usagestats) for
   * 13 Nov up to 18:36:
   *
   *     old rule:  10h 31m   (the launcher alone accrued 5h 01m)
   *     this rule:  5h 48m
   *     Digital Wellbeing at the same moment: 5h 58m
   *
   * Keying open sessions by package AND activity class fixes the original
   * problem properly: a STOPPED for one activity can only close that same
   * activity, never a sibling screen of the same app that is still in front.
   */
  fun collect(context: Context, startMs: Long, endMs: Long): List<Session> {
    val manager = context.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
    // Query a little before the window so a session already in progress at
    // `startMs` is captured and can be clipped, rather than being lost.
    val lookBack = 12L * 60 * 60 * 1000
    val events = manager.queryEvents(startMs - lookBack, endMs)

    // "package/activity" -> (package, start)
    val open = HashMap<String, Pair<String, Long>>()
    val raw = mutableListOf<Session>()

    fun closeAll(at: Long) {
      for ((pkg, s) in open.values) if (at > s) raw.add(Session(pkg, s, at))
      open.clear()
    }

    val event = UsageEvents.Event()
    while (events.hasNextEvent()) {
      events.getNextEvent(event)
      val pkg = event.packageName ?: continue
      val key = "$pkg/${event.className}"

      when (event.eventType) {
        // ACTIVITY_RESUMED == MOVE_TO_FOREGROUND == 1.
        EVENT_ACTIVITY_RESUMED -> if (!open.containsKey(key)) open[key] = pkg to event.timeStamp

        // ACTIVITY_PAUSED == MOVE_TO_BACKGROUND == 2, ACTIVITY_STOPPED == 23.
        // Close only the activity they name.
        EVENT_ACTIVITY_PAUSED,
        EVENT_ACTIVITY_STOPPED -> open.remove(key)?.let { (p, s) ->
          if (event.timeStamp > s) raw.add(Session(p, s, event.timeStamp))
        }

        // The screen going off, the device locking, or shutting down end all
        // foreground use regardless of which app was in front.
        EVENT_SCREEN_NON_INTERACTIVE,
        EVENT_KEYGUARD_SHOWN,
        EVENT_DEVICE_SHUTDOWN -> closeAll(event.timeStamp)
      }
    }

    // Still open when the stream ends: close at the earlier of the window edge
    // and NOW, so "today" never claims hours that have not happened yet.
    closeAll(min(endMs, System.currentTimeMillis()))

    val clipped = mutableListOf<Session>()
    for (session in nonOverlapping(raw)) {
      val clippedStart = max(session.start, startMs)
      val clippedEnd = min(session.end, endMs)
      if (clippedEnd > clippedStart) clipped.add(Session(session.packageName, clippedStart, clippedEnd))
    }
    return mergeAdjacent(clipped)
  }

  /**
   * Resolve overlapping sessions so each instant belongs to exactly one app:
   * the one whose session started most recently (the app most recently brought
   * to the front). Overlaps come from split screen, picture-in-picture and the
   * few milliseconds of a hand-over. Without this, summing sessions counts
   * that time twice; with it, per-app minutes add up to the total exactly.
   */
  private fun nonOverlapping(sessions: List<Session>): List<Session> {
    if (sessions.isEmpty()) return sessions
    val bounds = sessions.flatMap { listOf(it.start, it.end) }.distinct().sorted()
    val byStart = sessions.sortedBy { it.start }
    val out = mutableListOf<Session>()
    val active = mutableListOf<Session>()
    var next = 0
    for (i in 0 until bounds.size - 1) {
      val a = bounds[i]
      val b = bounds[i + 1]
      while (next < byStart.size && byStart[next].start <= a) active.add(byStart[next++])
      active.removeAll { it.end <= a }
      val winner = active.filter { it.end >= b }.maxByOrNull { it.start } ?: continue
      val last = out.lastOrNull()
      if (last != null && last.packageName == winner.packageName && last.end == a) {
        out[out.size - 1] = last.copy(end = b)
      } else {
        out.add(Session(winner.packageName, a, b))
      }
    }
    return out
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

  /**
   * Packages whose time is NOT counted in screen time: the launcher (home
   * screen) and the system UI.
   *
   * Re-measured once sessions were reconstructed correctly. The earlier note
   * here concluded Digital Wellbeing counts launcher time, but that was
   * measured with the old session rule, which inflated the launcher several
   * fold. Against the same event log, 13 Nov to 18:36:
   *
   *     launcher counted    6h 22m
   *     launcher excluded   5h 48m      Digital Wellbeing: 5h 58m
   *
   * This app IS counted now, because Digital Wellbeing counts it (it appears
   * as its own slice there), and matching that figure is the point.
   */
  fun timeExcluded(context: Context): Set<String> =
    launchExcluded(context) - context.packageName + "android"

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
  fun launchExcluded(context: Context): Set<String> {
    val pm = context.packageManager
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

  /** Screen time counted in [startMs, endMs), exactly as the app counts it. */
  fun screenMinutes(context: Context, startMs: Long, endMs: Long): Int {
    val excluded = timeExcluded(context)
    val ms = collect(context, startMs, endMs)
      .filter { it.packageName !in excluded }
      .sumOf { it.end - it.start }
    return Math.round(ms / 60000.0).toInt()
  }

  /** Screen time since local midnight. */
  fun todayMinutes(context: Context): Int {
    val zone = ZoneId.systemDefault()
    val start = LocalDate.now(zone).atStartOfDay(zone).toInstant().toEpochMilli()
    return screenMinutes(context, start, System.currentTimeMillis())
  }

  const val EVENT_ACTIVITY_RESUMED = 1
  const val EVENT_ACTIVITY_PAUSED = 2
  const val EVENT_ACTIVITY_STOPPED = 23
  const val EVENT_SCREEN_NON_INTERACTIVE = 16
  const val EVENT_KEYGUARD_SHOWN = 17
  const val EVENT_DEVICE_SHUTDOWN = 26

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
}
