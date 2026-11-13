package expo.modules.usagestats

/**
 * The merge decision for a captured day, as a pure function.
 *
 * WHY THIS IS SEPARATE FROM CapturedDayStore
 * ------------------------------------------
 * This is the most dangerous logic in the project. Its failure mode is not a
 * crash or a wrong figure on a screen - it is the SILENT, PERMANENT loss of
 * measurements that can never be re-read, because Android discards its usage
 * events after about a week. A mistake here would look exactly like a quiet
 * day.
 *
 * Logic like that should be tested, and it cannot be while it is tangled up
 * with SharedPreferences, a Context and JSON parsing: a test would need an
 * Android runtime to say anything about a decision that depends on three
 * booleans and two integers. So the decision lives here, with no Android
 * imports and no I/O, and [CapturedDayStore] is reduced to reading, calling
 * this, and writing.
 *
 * See CaptureMergeTest for the rules exercised one at a time, including the
 * aging-out scenario that is the whole reason the refusal exists.
 */

/**
 * The only facts a merge decision depends on.
 *
 * Deliberately narrow. The stored day carries about fifteen fields, and
 * passing the whole thing in would invite the decision to start depending on
 * fields whose behaviour nobody has reasoned about.
 */
data class DayFacts(
  /**
   * Whether Android reported any foreground activity for this date.
   *
   * False is ambiguous and that ambiguity is the point: it means either the
   * phone was genuinely unused, or the date has aged out of Android's
   * retention window. The two are indistinguishable through this API, which
   * is why a false can never be trusted to overwrite a true.
   */
  val hadAnyUsage: Boolean,
  val totalScreenMinutes: Int,
  /**
   * Which version of the screen-time calculation produced the figure.
   * Days stored before this field existed read as 1.
   */
  val calcVersion: Int = 1,
)

/**
 * The current screen-time calculation. Bump when the calculation changes in
 * a way that makes earlier stored figures wrong, so they can be re-read.
 *
 *   1  sessions closed only by the next app's RESUMED or by screen-off;
 *      inflated the launcher several fold (10h 31m vs 5h 58m in DW).
 *   2  activity-keyed sessions closed by their own PAUSED/STOPPED,
 *      non-overlapping, launcher and system UI excluded (5h 48m).
 */
const val CURRENT_CALC_VERSION = 2

/** What storing a day did. */
enum class PutResult {
  /** No record existed for this date. */
  CREATED,

  /** Stored values changed, so the day needs uploading again. */
  UPDATED,

  /** Identical to what was already stored. */
  UNCHANGED,

  /**
   * The incoming read was WORSE than the stored day and was refused.
   *
   * Expected and healthy once a date leaves Android's retention window: it
   * means the local copy is now the only record of that day, and it was
   * protected rather than overwritten with an empty read.
   */
  REFUSED_DEGRADED,
}

/**
 * Decide what to do with a freshly read day.
 *
 * THE RULES, AND WHY EACH EXISTS
 * ------------------------------
 * 1. Nothing stored yet - take it.
 *
 * 2. For a date in the PAST, a read reporting NO activity never replaces a
 *    stored day that had some. Overwriting a captured Tuesday of 8.7 hours
 *    with the empty read taken nine days later would erase a real
 *    measurement and replace it with a fabricated quiet day.
 *
 * 3. For a date in the PAST, a read reporting LESS screen time never replaces
 *    the stored day. A completed day's total cannot shrink; if it appears to,
 *    events are aging out mid-window and the earlier, fuller read is the
 *    better measurement.
 *
 * 2a. EXCEPT a re-read by a NEWER calculation that still sees the day's
 *    activity. Rules 2 and 3 protect a measurement from being degraded by a
 *    worse READ of the same thing; they were never meant to protect a figure
 *    the calculation itself got wrong. When the calculation is fixed, a
 *    day still inside Android's window is re-read correctly and must be
 *    allowed to go DOWN. A day that has aged out (no activity seen) is
 *    still refused by rule 2 - the old figure is then the only one there is.
 *
 * 4. TODAY is always replaced. Its total is still accumulating, so the newest
 *    read is by definition the most complete. Rules 2 and 3 are deliberately
 *    NOT applied, or the current day would freeze at its first reading of the
 *    morning and never grow.
 *
 * `identical` is supplied by the caller rather than derived from [DayFacts],
 * because two days can agree on these two fields while differing in unlock
 * count, notification coverage or the sleep estimate - all of which must
 * still mark the day for re-upload.
 */
fun decideMerge(
  stored: DayFacts?,
  incoming: DayFacts,
  isToday: Boolean,
  identical: Boolean,
): PutResult {
  if (stored == null) return PutResult.CREATED

  if (!isToday) {
    // Rule 2a: a newer calculation that can still see the day replaces it.
    if (incoming.hadAnyUsage && incoming.calcVersion > stored.calcVersion) {
      return if (identical) PutResult.UNCHANGED else PutResult.UPDATED
    }

    // Rule 2: an empty read never erases a measured day.
    if (stored.hadAnyUsage && !incoming.hadAnyUsage) return PutResult.REFUSED_DEGRADED

    // Rule 3: a past day's screen time cannot shrink.
    if (incoming.totalScreenMinutes < stored.totalScreenMinutes) {
      return PutResult.REFUSED_DEGRADED
    }
  }

  return if (identical) PutResult.UNCHANGED else PutResult.UPDATED
}
