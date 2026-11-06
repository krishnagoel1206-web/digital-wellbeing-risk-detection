package expo.modules.usagestats

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Tests for the capture merge rules.
 *
 * WHY THESE EXIST
 * ---------------
 * Every other mistake in this project is recoverable. Retrain a model, fix a
 * caveat, re-read a database. This logic is different: Android discards its
 * usage-event stream after roughly a week, so a measurement it loses can
 * never be obtained again from any source. The failure mode is not an error
 * message - it is a real 8.7-hour day silently becoming a quiet one, months
 * before anyone looks at the chart and wonders.
 *
 * The scenario in [a_day_that_has_aged_out_of_androids_window_is_refused] is
 * the one the whole mechanism exists for, and it was previously unexercised:
 * on the live device every captured day was still inside Android's window, so
 * nothing had yet needed protecting.
 */
class CaptureMergeTest {

  private fun facts(minutes: Int, hadUsage: Boolean = true) =
    DayFacts(hadAnyUsage = hadUsage, totalScreenMinutes = minutes)

  // ------------------------------------------------------------------
  // Nothing stored yet
  // ------------------------------------------------------------------

  @Test
  fun a_date_never_seen_before_is_stored() {
    assertEquals(
      PutResult.CREATED,
      decideMerge(stored = null, incoming = facts(520), isToday = false, identical = false),
    )
  }

  @Test
  fun a_first_read_of_today_is_stored() {
    assertEquals(
      PutResult.CREATED,
      decideMerge(stored = null, incoming = facts(7), isToday = true, identical = false),
    )
  }

  // ------------------------------------------------------------------
  // The protection: a past day must not be degraded
  // ------------------------------------------------------------------

  @Test
  fun a_day_that_has_aged_out_of_androids_window_is_refused() {
    // The read nine days later: Android no longer has the events, so it
    // reports no activity at all. Indistinguishable from an unused day, and
    // far more likely to be the window expiring.
    //
    // NOTE on what this does and does not prove. An aged-out read carries
    // hadAnyUsage=false AND zero minutes, so BOTH rule 2 and rule 3 would
    // refuse it - deleting rule 2 leaves this test green. That was found by
    // deleting each rule in turn and re-running, and it is why the test below
    // exists: without it, rule 2 was untested and this test's name overstated
    // what it covered.
    val storedMeasurement = facts(520)
    val emptyReread = facts(0, hadUsage = false)

    assertEquals(
      "a measured day was overwritten by an empty re-read and is now lost",
      PutResult.REFUSED_DEGRADED,
      decideMerge(storedMeasurement, emptyReread, isToday = false, identical = false),
    )
  }

  @Test
  fun an_unknown_day_never_replaces_a_measured_one_even_at_equal_minutes() {
    // The case that isolates rule 2, and the only one where it is the rule
    // doing the work.
    //
    // A day can carry activity while rounding to zero screen minutes - a few
    // app launches and a glance at the lock screen. Android later forgets the
    // date and reports no activity, also at zero minutes. Rule 3 compares
    // 0 < 0 and permits the write; only rule 2 sees that a KNOWN day is about
    // to be replaced by an UNKNOWN one.
    //
    // The distinction is the whole basis of this project's treatment of
    // missing data: "measured as nothing" and "not measured" are different
    // facts, and one must never be allowed to masquerade as the other.
    val knownButQuiet = facts(0, hadUsage = true)
    val unknown = facts(0, hadUsage = false)

    assertEquals(
      "a measured quiet day was overwritten by a day we simply never saw",
      PutResult.REFUSED_DEGRADED,
      decideMerge(knownButQuiet, unknown, isToday = false, identical = false),
    )
  }

  @Test
  fun a_past_day_reporting_less_screen_time_is_refused() {
    // Events aging out mid-window: the day still reports activity, but less
    // of it than when it was first captured. The earlier read is the better
    // measurement.
    assertEquals(
      PutResult.REFUSED_DEGRADED,
      decideMerge(facts(520), facts(410), isToday = false, identical = false),
    )
  }

  @Test
  fun one_minute_of_loss_is_still_loss() {
    assertEquals(
      PutResult.REFUSED_DEGRADED,
      decideMerge(facts(520), facts(519), isToday = false, identical = false),
    )
  }

  @Test
  fun a_past_day_reporting_more_screen_time_is_accepted() {
    // Not a degradation, so there is nothing to protect. A later read holding
    // MORE is the better measurement and should win.
    assertEquals(
      PutResult.UPDATED,
      decideMerge(facts(500), facts(520), isToday = false, identical = false),
    )
  }

  @Test
  fun an_unknown_day_may_be_replaced_by_another_unknown_read() {
    // Rule 2 guards a MEASURED day against an unknown read. Where the stored
    // day is itself unknown there is no measurement at risk, so the write is
    // allowed - otherwise the first unknown read of a date would pin it
    // forever and a later successful read could never correct it.
    assertEquals(
      PutResult.UPDATED,
      decideMerge(
        stored = facts(0, hadUsage = false),
        incoming = facts(0, hadUsage = false),
        isToday = false,
        identical = false,
      ),
    )
  }

  // ------------------------------------------------------------------
  // Today is the exception
  // ------------------------------------------------------------------

  @Test
  fun todays_total_may_grow_through_the_day() {
    assertEquals(
      PutResult.UPDATED,
      decideMerge(facts(54), facts(190), isToday = true, identical = false),
    )
  }

  @Test
  fun todays_total_may_also_shrink_without_being_refused() {
    // The refusal rules are deliberately not applied to today. Applying them
    // would freeze the current day at its first reading of the morning, which
    // is the opposite of the intent: today is the one date whose newest read
    // is always the most complete.
    //
    // A shrink here is not expected, but preferring a stale figure for the
    // day in progress would be worse than accepting a corrected one.
    assertEquals(
      "today was frozen at an earlier reading",
      PutResult.UPDATED,
      decideMerge(facts(190), facts(54), isToday = true, identical = false),
    )
  }

  @Test
  fun today_reporting_no_activity_is_not_refused() {
    assertEquals(
      PutResult.UPDATED,
      decideMerge(facts(54), facts(0, hadUsage = false), isToday = true, identical = false),
    )
  }

  // ------------------------------------------------------------------
  // Unchanged days
  // ------------------------------------------------------------------

  @Test
  fun an_identical_read_changes_nothing() {
    assertEquals(
      PutResult.UNCHANGED,
      decideMerge(facts(520), facts(520), isToday = false, identical = true),
    )
  }

  @Test
  fun equal_screen_time_with_other_fields_changed_is_an_update() {
    // `identical` is supplied by the caller rather than derived from DayFacts
    // precisely for this case: two reads can agree on screen time while
    // differing in unlock count, notification coverage or the sleep estimate.
    // Reporting UNCHANGED would leave those corrections un-uploaded.
    assertEquals(
      "a day whose other fields changed was not marked for re-upload",
      PutResult.UPDATED,
      decideMerge(facts(520), facts(520), isToday = false, identical = false),
    )
  }

  @Test
  fun an_identical_read_of_today_changes_nothing() {
    assertEquals(
      PutResult.UNCHANGED,
      decideMerge(facts(54), facts(54), isToday = true, identical = true),
    )
  }

  // ------------------------------------------------------------------
  // The sequence the device actually goes through
  // ------------------------------------------------------------------

  @Test
  fun a_day_survives_its_whole_life_cycle() {
    // One date, followed from first capture to long after Android forgets it.
    // This is the behaviour the feature promises, asserted end to end rather
    // than as isolated rules.
    var stored: DayFacts? = null

    // Morning of the day itself: 54 minutes so far.
    assertEquals(
      PutResult.CREATED,
      decideMerge(stored, facts(54), isToday = true, identical = false),
    )
    stored = facts(54)

    // Later the same day, still accumulating.
    assertEquals(
      PutResult.UPDATED,
      decideMerge(stored, facts(430), isToday = true, identical = false),
    )
    stored = facts(430)

    // Just before midnight.
    assertEquals(
      PutResult.UPDATED,
      decideMerge(stored, facts(520), isToday = true, identical = false),
    )
    stored = facts(520)

    // The next few days: re-read inside Android's window, same figure.
    assertEquals(
      PutResult.UNCHANGED,
      decideMerge(stored, facts(520), isToday = false, identical = true),
    )

    // A week on, events begin aging out and the read comes back short.
    assertEquals(
      PutResult.REFUSED_DEGRADED,
      decideMerge(stored, facts(300), isToday = false, identical = false),
    )

    // Later still, Android has nothing for the date at all.
    assertEquals(
      PutResult.REFUSED_DEGRADED,
      decideMerge(stored, facts(0, hadUsage = false), isToday = false, identical = false),
    )

    // The original measurement is intact, which is the entire point.
    assertEquals(520, stored.totalScreenMinutes)
  }
}
