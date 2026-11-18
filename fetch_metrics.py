#!/usr/bin/env python3
"""Fetch Garmin Connect endpoint-only metrics into garmin_metrics.db.

GarminDB only imports data that arrives as files (FIT, activity/sleep/weight
JSON). Several metrics are computed server-side and served only through
per-date API endpoints; this script polls those endpoints and stores them so
the nightly sync keeps a local history:

- training status: daily status phrase, ACWR, acute/chronic load, VO2 max
- training readiness: daily per-device score (available from ~2025)
- race predictions: current snapshot per run (the endpoint has no date arg)

Modes:
- incremental (default): resume from the last stored date per table
- backfill: --start-date YYYY-MM-DD [--end-date YYYY-MM-DD]
"""

import argparse
import datetime
import json
import logging
import pathlib
import sqlite3
import sys

from garminconnect import Garmin

LOG = logging.getLogger("fetch_metrics")

HOME = pathlib.Path.home()
DB_PATH = HOME / "HealthData/DBs/garmin_metrics.db"
TOKEN_PATH = HOME / ".GarminDb/garmin_tokens.json"
CONFIG_PATH = HOME / ".GarminDb/GarminConnectConfig.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS training_status (
    day TEXT PRIMARY KEY,
    status_phrase TEXT,
    acwr_status TEXT,
    acute_load REAL,
    chronic_load REAL,
    vo2max REAL
);
CREATE TABLE IF NOT EXISTS training_readiness (
    day TEXT,
    device_id INTEGER,
    score INTEGER,
    level TEXT,
    feedback TEXT,
    PRIMARY KEY (day, device_id)
);
CREATE TABLE IF NOT EXISTS race_predictions (
    day TEXT PRIMARY KEY,
    time_5k INTEGER,
    time_10k INTEGER,
    time_half_marathon INTEGER,
    time_marathon INTEGER
);
CREATE TABLE IF NOT EXISTS lactate_threshold (
    day TEXT PRIMARY KEY,
    lt_heart_rate INTEGER,
    lt_speed REAL
);
CREATE TABLE IF NOT EXISTS body_composition (
    day TEXT PRIMARY KEY,
    body_fat REAL,
    muscle_mass REAL,
    bone_mass REAL,
    body_water REAL
);
CREATE TABLE IF NOT EXISTS endurance_score (
    day TEXT PRIMARY KEY,
    average REAL,
    max_value REAL
);
CREATE TABLE IF NOT EXISTS hill_score (
    day TEXT PRIMARY KEY,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS workouts (
    workout_id INTEGER PRIMARY KEY,
    name TEXT,
    sport TEXT,
    update_date TEXT,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS fetch_state (
    table_name TEXT PRIMARY KEY,
    last_day TEXT
);
"""


def connect_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    return con


def last_day(con, table, default):
    """Resume point: the stored per-table last_day, else the default."""
    row = con.execute(
        "SELECT last_day FROM fetch_state WHERE table_name = ?", (table,)
    ).fetchone()
    return row[0] if row else default


def set_last_day(con, table, day):
    con.execute(
        "INSERT INTO fetch_state (table_name, last_day) VALUES (?, ?) "
        "ON CONFLICT(table_name) DO UPDATE SET last_day = excluded.last_day",
        (table, day),
    )


def login():
    config = json.loads(CONFIG_PATH.read_text())
    creds = config["credentials"]
    garmin = Garmin(creds["user"], creds["password"])
    garmin.login(str(TOKEN_PATH))
    return garmin


def daterange(start, end):
    day = start
    while day <= end:
        yield day
        day += datetime.timedelta(days=1)


def fetch_training_status(con, garmin, start, end):
    """Store one row per date; old dates may return rows without data."""
    fetched, empty = 0, 0
    for day in daterange(start, end):
        iso = day.isoformat()
        try:
            payload = garmin.get_training_status(iso)
        except Exception as exc:  # per-date tolerance: log and continue
            LOG.warning("training_status %s failed: %s", iso, exc)
            continue
        devices = payload.get("mostRecentTrainingStatus", {}).get(
            "latestTrainingStatusData", {}
        )
        row = (None, None, None, None, None)
        for _device, entry in devices.items():
            load = entry.get("acuteTrainingLoadDTO") or {}
            vo2max = (payload.get("mostRecentVO2Max") or {}).get(
                "generic", {}
            ).get("vo2MaxPreciseValue")
            row = (
                entry.get("trainingStatusFeedbackPhrase"),
                load.get("acwrStatus"),
                load.get("dailyTrainingLoadAcute"),
                load.get("maxTrainingLoadChronic"),
                vo2max,
            )
            break  # primary device is listed first
        if row[0] is None and row[4] is None:
            empty += 1
        con.execute(
            "INSERT OR REPLACE INTO training_status VALUES (?, ?, ?, ?, ?, ?)",
            (iso, *row),
        )
        set_last_day(con, "training_status", iso)
        fetched += 1
    return fetched, empty


def fetch_training_readiness(con, garmin, start, end):
    """Per-device scores; dates before the device supported them return [] ."""
    fetched = 0
    for day in daterange(start, end):
        iso = day.isoformat()
        try:
            entries = garmin.get_training_readiness(iso) or []
        except Exception as exc:
            LOG.warning("training_readiness %s failed: %s", iso, exc)
            continue
        for entry in entries:
            con.execute(
                "INSERT OR REPLACE INTO training_readiness VALUES (?, ?, ?, ?, ?)",
                (
                    iso,
                    entry.get("deviceId"),
                    entry.get("score"),
                    entry.get("level"),
                    entry.get("feedbackShort"),
                ),
            )
        set_last_day(con, "training_readiness", iso)
        fetched += 1
    return fetched, 0


def fetch_race_predictions(con, garmin):
    """Current-only snapshot; each run appends one dated row."""
    payload = garmin.get_race_predictions() or {}
    day = payload.get("calendarDate")
    if not day:
        return 0
    con.execute(
        "INSERT OR REPLACE INTO race_predictions VALUES (?, ?, ?, ?, ?)",
        (
            day,
            payload.get("time5K"),
            payload.get("time10K"),
            payload.get("timeHalfMarathon"),
            payload.get("timeMarathon"),
        ),
    )
    set_last_day(con, "race_predictions", day)
    return 1


def fetch_lactate_threshold(con, garmin):
    """Snapshot endpoint; store the entry when its date changes.

    speed units come back in an unverified Garmin unit; lt_heart_rate is the
    reliable field.
    """
    payload = (garmin.get_lactate_threshold() or {}).get(
        "speed_and_heart_rate", {}
    )
    day = (payload.get("calendarDate") or "")[:10]
    if not day:
        return 0
    stored = last_day(con, "lactate_threshold", "")
    if day <= stored:
        return 0
    con.execute(
        "INSERT OR REPLACE INTO lactate_threshold VALUES (?, ?, ?)",
        (day, payload.get("heartRate"), payload.get("speed")),
    )
    set_last_day(con, "lactate_threshold", day)
    return 1


def fetch_body_composition(con, garmin, start, end):
    """Index Scale readings; weight duplicates the Weight table, so only
    composition fields are stored."""
    fetched = 0
    for day in daterange(start, end):
        iso = day.isoformat()
        try:
            payload = garmin.get_body_composition(iso) or {}
        except Exception as exc:
            LOG.warning("body_composition %s failed: %s", iso, exc)
            continue
        for sample in payload.get("dateWeightList", []):
            if sample.get("calendarDate", "")[:10] != iso:
                continue
            con.execute(
                "INSERT OR REPLACE INTO body_composition VALUES (?, ?, ?, ?, ?)",
                (
                    iso,
                    sample.get("bodyFat"),
                    sample.get("muscleMass"),
                    sample.get("boneMass"),
                    sample.get("bodyWater"),
                ),
            )
        set_last_day(con, "body_composition", iso)
        con.commit()
        fetched += 1
    return fetched, 0


def fetch_score(con, garmin, start, end, kind):
    """Endurance/hill score: the API returns per-group daily averages for a
    date range; fetch in monthly chunks and upsert per group day."""
    fetched = 0
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + datetime.timedelta(days=30), end)
        a, b = chunk_start.isoformat(), chunk_end.isoformat()
        payload = None
        for attempt in (1, 2):
            try:
                payload = (
                    garmin.get_endurance_score(a, b)
                    if kind == "endurance"
                    else garmin.get_hill_score(a, b)
                )
                break
            except Exception as exc:
                LOG.warning(
                    "%s score %s..%s failed (attempt %d): %s",
                    kind, a, b, attempt, exc,
                )
                if attempt == 1:
                    # long runs outlive the token TTL; re-login and retry once
                    try:
                        garmin.login(str(TOKEN_PATH))
                    except Exception as login_exc:
                        LOG.error("re-login failed: %s", login_exc)
                        break
        if payload is None:
            chunk_start = chunk_end + datetime.timedelta(days=1)
            continue
        group_map = payload.get("groupMap", {}) if isinstance(payload, dict) else {}
        for day, entry in group_map.items():
            if not isinstance(entry, dict):
                continue
            if kind == "endurance":
                con.execute(
                    "INSERT OR REPLACE INTO endurance_score VALUES (?, ?, ?)",
                    (day, entry.get("groupAverage"), entry.get("groupMax")),
                )
            else:
                con.execute(
                    "INSERT OR REPLACE INTO hill_score VALUES (?, ?)",
                    (day, json.dumps(entry, default=str)),
                )
            fetched += 1
        con.commit()
        chunk_start = chunk_end + datetime.timedelta(days=1)
    return fetched, 0


def fetch_workouts(con, garmin):
    """Workout library snapshot; the calendar never links these to days, so
    store the definitions only."""
    entries = garmin.get_workouts() or []
    for w in entries:
        con.execute(
            "INSERT OR REPLACE INTO workouts VALUES (?, ?, ?, ?, ?)",
            (
                w.get("workoutId"),
                w.get("workoutName"),
                (w.get("sportType") or {}).get("sportTypeKey"),
                w.get("updateDate"),
                json.dumps(w, default=str),
            ),
        )
    set_last_day(con, "workouts", datetime.date.today().isoformat())
    return len(entries), 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", help="backfill start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="backfill end date, default today")
    parser.add_argument(
        "--tables",
        help="comma-separated subset to fetch (e.g. endurance_score,hill_score); default all",
    )
    parser.add_argument(
        "--metrics-timeout-note",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    today = datetime.date.today()
    con = connect_db()
    garmin = login()

    if args.start_date:
        start = datetime.date.fromisoformat(args.start_date)
    else:
        # Resume just past the last stored date; oldest meaningful history is
        # 2023-01-01 for training status.
        resume = min(
            last_day(con, "training_status", "2022-12-31"),
            last_day(con, "training_readiness", today.isoformat()),
            last_day(con, "body_composition", today.isoformat()),
        )
        start = datetime.date.fromisoformat(resume) + datetime.timedelta(days=1)
    end = (
        datetime.date.fromisoformat(args.end_date)
        if args.end_date
        else today
    )
    if start > end:
        LOG.info("Nothing to fetch: %s > %s", start, end)
        return

    LOG.info("Fetching %s to %s", start, end)
    counts = {}
    counts["training_readiness"] = fetch_training_readiness(
        con, garmin, start, end
    )
    counts["race_predictions"] = (fetch_race_predictions(con, garmin), 0)
    selected = (
        {t.strip() for t in args.tables.split(",")}
        if args.tables
        else None
    )

    def wanted(table):
        return selected is None or table in selected

    counts = {}
    if wanted("training_status"):
        counts["training_status"] = fetch_training_status(con, garmin, start, end)
    if wanted("training_readiness"):
        counts["training_readiness"] = fetch_training_readiness(
            con, garmin, start, end
        )
    if wanted("race_predictions"):
        counts["race_predictions"] = (fetch_race_predictions(con, garmin), 0)
    if wanted("lactate_threshold"):
        counts["lactate_threshold"] = (fetch_lactate_threshold(con, garmin), 0)
    if wanted("body_composition"):
        counts["body_composition"] = fetch_body_composition(con, garmin, start, end)
    # score endpoints return per-group ranges; incremental runs refetch the
    # last ~5 weeks, a backfill walks from --start-date in monthly chunks
    score_start = max(start, end - datetime.timedelta(days=35))
    if wanted("endurance_score"):
        counts["endurance_score"] = fetch_score(
            con, garmin, score_start, end, "endurance"
        )
    if wanted("hill_score"):
        counts["hill_score"] = fetch_score(con, garmin, score_start, end, "hill")
    if wanted("workouts"):
        counts["workouts"] = fetch_workouts(con, garmin)
    con.commit()

    for table, (fetched, empty) in counts.items():
        LOG.info("%s: %d dates fetched, %d empty", table, fetched, empty)
    if all(f == 0 for f, _ in counts.values()) and start != end:
        LOG.error("Every date failed to fetch")
        sys.exit(1)


if __name__ == "__main__":
    main()
