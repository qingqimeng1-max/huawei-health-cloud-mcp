"""SQLite 存储层：任务书 §5 的两级表 + 同步游标（kv_store 由 token_store 管理）。

所有时间统一存 UTC 毫秒；daily_summary.day 用本地日期字符串。

游标语义（修复误报）：
- 成功：set_cursor_ok() 推进 synced_until，更新 last_ok_at，清 last_error，fail_count 归零
- 失败：set_cursor_error() 只记 last_error，fail_count+1，不碰 synced_until 和 last_ok_at
"""
from __future__ import annotations

import os
import sqlite3
import stat
import sys
import time

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from app.token_store import DB_PATH, DATA_DIR  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_health_point (
  id INTEGER PRIMARY KEY,
  metric TEXT NOT NULL,
  ts_utc INTEGER NOT NULL,
  value REAL,
  extra JSON,
  data_id TEXT,
  UNIQUE(metric, data_id)
);
CREATE INDEX IF NOT EXISTS idx_raw_metric_ts ON raw_health_point(metric, ts_utc);

CREATE TABLE IF NOT EXISTS workout (
  id INTEGER PRIMARY KEY,
  data_id TEXT UNIQUE,
  start_utc INTEGER, end_utc INTEGER,
  sport_type INTEGER, duration_min INTEGER,
  distance_m INTEGER, avg_pace_sec_per_km INTEGER,
  avg_hr INTEGER, max_hr INTEGER, kcal REAL
);

CREATE TABLE IF NOT EXISTS daily_summary (
  day TEXT PRIMARY KEY,
  steps INTEGER, distance_km REAL, kcal REAL,
  resting_hr INTEGER, hr_min INTEGER, hr_max INTEGER,
  sleep_total_min INTEGER, sleep_deep_min INTEGER, sleep_rem_min INTEGER,
  sleep_light_min INTEGER, sleep_awake_min INTEGER,
  sleep_bedtime TEXT, sleep_waketime TEXT, sleep_score INTEGER,
  spo2_avg REAL, spo2_min REAL,
  workout_count INTEGER, updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS sync_cursor (
  metric TEXT PRIMARY KEY,
  synced_until INTEGER,
  last_ok_at INTEGER,
  last_error TEXT,
  fail_count INTEGER DEFAULT 0,
  last_run_at INTEGER,
  last_duration_s REAL
);
"""

# 老版本 sync_cursor 只有 4 列，启动时补齐
_MIGRATIONS = [
    "ALTER TABLE sync_cursor ADD COLUMN fail_count INTEGER DEFAULT 0",
    "ALTER TABLE sync_cursor ADD COLUMN last_run_at INTEGER",
    "ALTER TABLE sync_cursor ADD COLUMN last_duration_s REAL",
]


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sync_cursor)")}
    for sql in _MIGRATIONS:
        col = sql.split("ADD COLUMN")[1].split()[0]
        if col not in cols:
            conn.execute(sql)


def get_db() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    fresh = not os.path.exists(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if fresh:
        os.chmod(DB_PATH, stat.S_IRUSR | stat.S_IWUSR)
    conn.executescript(SCHEMA)
    _migrate(conn)
    # kv_store 由 token_store 创建，这里兜底
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kv_store ("
        " key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)"
    )
    conn.commit()
    return conn


def upsert_daily(day: str, fields: dict) -> None:
    """按天 upsert daily_summary，只更新传入的字段。"""
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        return
    fields["updated_at"] = int(time.time())
    conn = get_db()
    try:
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        updates = ", ".join(f"{c}=excluded.{c}" for c in fields if c != "day")
        conn.execute(
            f"INSERT INTO daily_summary (day, {cols}) VALUES (?, {placeholders})"
            f" ON CONFLICT(day) DO UPDATE SET {updates}",
            [day, *fields.values()],
        )
        conn.commit()
    finally:
        conn.close()


def upsert_workout(w: dict) -> None:
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO workout
               (data_id, start_utc, end_utc, sport_type, duration_min,
                distance_m, avg_pace_sec_per_km, avg_hr, max_hr, kcal)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(data_id) DO UPDATE SET
                 end_utc=excluded.end_utc, duration_min=excluded.duration_min,
                 distance_m=excluded.distance_m, kcal=excluded.kcal""",
            (w.get("data_id"), w.get("start_utc"), w.get("end_utc"),
             w.get("sport_type"), w.get("duration_min"), w.get("distance_m"),
             w.get("avg_pace_sec_per_km"), w.get("avg_hr"), w.get("max_hr"),
             w.get("kcal")),
        )
        conn.commit()
    finally:
        conn.close()


def get_cursor(metric: str) -> int | None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT synced_until FROM sync_cursor WHERE metric=?", (metric,)
        ).fetchone()
        return row["synced_until"] if row else None
    finally:
        conn.close()


def get_cursor_status(metric: str) -> dict | None:
    """取某指标的完整同步状态（含 fail_count，供健康检查用）。"""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM sync_cursor WHERE metric=?", (metric,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def mark_run_start(metric: str) -> None:
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO sync_cursor (metric, last_run_at)
               VALUES (?, ?)
               ON CONFLICT(metric) DO UPDATE SET last_run_at=excluded.last_run_at""",
            (metric, int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()


def set_cursor_ok(metric: str, synced_until: int, duration_s: float | None = None) -> None:
    """同步成功：推进游标，更新 last_ok_at，清掉错误和失败计数。"""
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO sync_cursor
               (metric, synced_until, last_ok_at, last_error, fail_count, last_duration_s)
               VALUES (?,?,?,?,0,?)
               ON CONFLICT(metric) DO UPDATE SET
                 synced_until=excluded.synced_until,
                 last_ok_at=excluded.last_ok_at,
                 last_error=NULL,
                 fail_count=0,
                 last_duration_s=excluded.last_duration_s""",
            (metric, synced_until, int(time.time()), None, duration_s),
        )
        conn.commit()
    finally:
        conn.close()


def set_cursor_error(metric: str, error: str) -> None:
    """同步失败：只记错误和失败计数，不推进游标，不碰 last_ok_at。"""
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO sync_cursor (metric, last_error, fail_count)
               VALUES (?,?,1)
               ON CONFLICT(metric) DO UPDATE SET
                 last_error=excluded.last_error,
                 fail_count=COALESCE(sync_cursor.fail_count, 0) + 1""",
            (metric, error),
        )
        conn.commit()
    finally:
        conn.close()
