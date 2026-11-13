"""Tests for importing daily totals copied from Digital Wellbeing.

The import exists to bridge the gap between the ~1 week of detailed events
Android keeps and the 14 days a personal baseline needs. Each rule protects a
different kind of data, so each is tested on its own.
"""

from __future__ import annotations

import datetime as dt

from app.database.models import DataSource, UsageRecord
from app.schemas.sync import DeviceDayPayload, SyncRequest
from app.services import analysis_service, data_service, sync_service

TODAY = dt.date.today()


def ago(n: int) -> dt.date:
    return TODAY - dt.timedelta(days=n)


def wb(date: dt.date, minutes: int = 420, unlocks: int | None = 150, notifications: int | None = 700):
    return {"date": date.isoformat(), "screen_minutes": minutes, "unlocks": unlocks,
            "notifications": notifications}


def add(db, date, source, minutes=400, **extra):
    db.add(UsageRecord(record_date=date, source=source, total_screen_minutes=minutes, **extra))
    db.commit()


def row(db, date) -> UsageRecord:
    return db.query(UsageRecord).filter(UsageRecord.record_date == date).one()


# --------------------------------------------------------------------------
# The import rules
# --------------------------------------------------------------------------


def test_inserts_days_as_wellbeing_source(db_session):
    r = data_service.import_wellbeing(db_session, [wb(ago(20)), wb(ago(19), 300, 99, 1045)])
    assert r["counts"]["inserted"] == 2
    rec = row(db_session, ago(19))
    assert rec.source is DataSource.WELLBEING
    assert (rec.total_screen_minutes, rec.unlock_count, rec.notification_count) == (300, 99, 1045)
    assert rec.late_night_minutes is None and rec.sleep_hours is None  # not shown by DW
    assert "Digital Wellbeing" in rec.note


def test_today_and_impossible_values_are_rejected(db_session):
    r = data_service.import_wellbeing(
        db_session, [wb(TODAY), wb(ago(3), minutes=2000), {"date": "nonsense", "screen_minutes": 5}]
    )
    assert r["counts"]["rejected"] == 3
    assert db_session.query(UsageRecord).count() == 0


def test_replaces_demo_data_and_clears_its_fields(db_session):
    add(db_session, ago(20), DataSource.SYNTHETIC, 500, late_night_minutes=90, sleep_hours=6.0)
    data_service.import_wellbeing(db_session, [wb(ago(20), 420)])
    rec = row(db_session, ago(20))
    assert rec.source is DataSource.WELLBEING and rec.total_screen_minutes == 420
    assert rec.late_night_minutes is None and rec.sleep_hours is None


def test_never_touches_entries_you_made(db_session):
    add(db_session, ago(20), DataSource.MANUAL, 111)
    add(db_session, ago(19), DataSource.CSV, 222)
    r = data_service.import_wellbeing(db_session, [wb(ago(20)), wb(ago(19))])
    assert r["counts"]["kept"] == 2
    assert row(db_session, ago(20)).total_screen_minutes == 111
    assert row(db_session, ago(19)).total_screen_minutes == 222


def test_recent_phone_readings_are_kept(db_session):
    add(db_session, ago(3), DataSource.DEVICE, 900)  # even if far off
    data_service.import_wellbeing(db_session, [wb(ago(3), 420)])
    assert row(db_session, ago(3)).source is DataSource.DEVICE


def test_old_inflated_phone_reading_is_replaced(db_session):
    # Taken before the screen-time fix (launcher over-counted) and aged out of
    # Android's history: 10h31m stored against 7h01m in Digital Wellbeing.
    add(db_session, ago(10), DataSource.DEVICE, 631, late_night_minutes=200)
    r = data_service.import_wellbeing(db_session, [wb(ago(10), 421)])
    assert r["counts"]["replaced_old_phone_reading"] == 1
    rec = row(db_session, ago(10))
    assert rec.source is DataSource.WELLBEING and rec.total_screen_minutes == 421
    assert rec.late_night_minutes is None  # inflated value does not survive


def test_old_phone_reading_that_agrees_is_kept(db_session):
    add(db_session, ago(10), DataSource.DEVICE, 410, late_night_minutes=30)
    data_service.import_wellbeing(db_session, [wb(ago(10), 421)])  # within 3%
    rec = row(db_session, ago(10))
    assert rec.source is DataSource.DEVICE and rec.late_night_minutes == 30


# --------------------------------------------------------------------------
# A later sync must not undo the import with an old reading
# --------------------------------------------------------------------------


def _sync(db, date, minutes):
    return sync_service.sync_days(
        db, SyncRequest(days=[DeviceDayPayload(record_date=date, total_screen_minutes=minutes)])
    )


def test_old_device_upload_does_not_overwrite_import(db_session):
    data_service.import_wellbeing(db_session, [wb(ago(10), 421)])
    _sync(db_session, ago(10), 631)
    rec = row(db_session, ago(10))
    assert rec.source is DataSource.WELLBEING and rec.total_screen_minutes == 421


def test_recent_device_upload_supersedes_import(db_session):
    data_service.import_wellbeing(db_session, [wb(ago(2), 421)])
    _sync(db_session, ago(2), 430)
    rec = row(db_session, ago(2))
    assert rec.source is DataSource.DEVICE and rec.total_screen_minutes == 430


# --------------------------------------------------------------------------
# Scope: imports bridge the gap, then step aside
# --------------------------------------------------------------------------


def test_device_scope_includes_imports_until_enough_phone_days(db_session):
    data_service.import_wellbeing(db_session, [wb(ago(n)) for n in range(15, 30)])
    for n in range(1, 4):
        add(db_session, ago(n), DataSource.DEVICE, 400)
    sources = analysis_service._sources_for("device", db_session)
    assert DataSource.WELLBEING in sources

    for n in range(4, 15):
        add(db_session, ago(n), DataSource.DEVICE, 400)
    sources = analysis_service._sources_for("device", db_session)
    assert sources == [DataSource.DEVICE]
    assert DataSource.WELLBEING in analysis_service._sources_for("real", db_session)


def test_unusual_days_run_on_imported_history(db_session):
    days = [wb(ago(n), 420 + (n % 3) * 10, 150, 700) for n in range(2, 22)]
    days[0] = wb(ago(2), 1000, 150, 700)  # one obviously unusual day
    data_service.import_wellbeing(db_session, days)
    out = analysis_service.anomalies(db_session, limit=30, scope="device")
    assert out["days_assessed"] >= 14
    flagged = {d["date"] for d in out["days"] if d["is_anomalous"]}
    assert str(ago(2)) in {str(x) for x in flagged}


def test_todays_unfinished_phone_day_does_not_switch_imports_off(db_session):
    # 13 finished phone days + today's partial one: still short of 14 usable,
    # so the imports must stay in. (The phone showed "you have 13" before.)
    data_service.import_wellbeing(db_session, [wb(ago(n)) for n in range(14, 27)])
    for n in range(0, 14):
        add(db_session, ago(n), DataSource.DEVICE, 400)
    assert DataSource.WELLBEING in analysis_service._sources_for("device", db_session)
    out = analysis_service.anomalies(db_session, limit=5, scope="device")
    assert out["days_assessed"] == 26
