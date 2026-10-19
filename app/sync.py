"""同步逻辑：任务书 §4。按指标拉 [游标-3天, 今天]，写 SQLite。

日志只打计数和状态，不打原始健康数据值。
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, "/home/hatch/workspace/huawei-health-mcp")

from hwclient.client import (
    HuaweiHealthClient,
    daily_activity,
    daily_totals,
    day_int,
    refresh_tokens,
    sport_sessions,
)
from hwclient.const import SESSION_SPORT_TYPES
from app.token_store import load_tokens, save_tokens
from app import store

_LOGGER = logging.getLogger("sync")
TZ = ZoneInfo("Asia/Shanghai")
BACKFILL_DAYS = 3      # 回溯窗口（任务书 §4.5）
FIRST_SYNC_DAYS = 30   # 首次全量最多回溯（任务书 §4.5）


def _ymd_range(synced_until: int | None) -> tuple[int, int]:
    today = date.today()
    end = day_int(today)
    if synced_until:
        base = datetime.strptime(str(synced_until), "%Y%m%d").date()
        start_d = max(base - timedelta(days=BACKFILL_DAYS),
                      today - timedelta(days=FIRST_SYNC_DAYS))
    else:
        start_d = today - timedelta(days=FIRST_SYNC_DAYS)
    return day_int(start_d), end


def _iso_local(ms: float | None) -> str | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, TZ).isoformat(timespec="seconds")


def _to_ms(dt: datetime | None) -> int | None:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return int(dt.timestamp() * 1000)


def _get_client() -> HuaweiHealthClient:
    tokens = load_tokens()
    if not tokens or not tokens.access_token:
        raise RuntimeError("DB 里没有 token，先跑授权流程")
    if tokens.needs_rotation():
        _LOGGER.info("access token 将过期，轮换…")
        refresh_tokens(tokens)
        save_tokens(tokens)
        _LOGGER.info("token 已轮换并保存")
    return HuaweiHealthClient(tokens)


def sync_steps(client: HuaweiHealthClient) -> int:
    start, end = _ymd_range(store.get_cursor("steps"))
    resp = client.sports_stat(start, end)
    totals = daily_totals(daily_activity(resp.get("sportStat") or []))
    n = 0
    for day, row in totals.items():
        store.upsert_daily(day, {
            "steps": row.get("steps"),
            "distance_km": round((row.get("distance_m") or 0) / 1000, 2),
            "kcal": row.get("kcal"),
        })
        n += 1
    store.set_cursor("steps", end)
    _LOGGER.info("steps: %d 天已同步", n)
    return n


def sync_heart_rate(client: HuaweiHealthClient) -> int:
    start, end = _ymd_range(store.get_cursor("heart_rate"))
    resp = client.health_stat(start, end, [7])
    n = 0
    for rec in resp.get("heartRateTotal") or []:
        day = str(rec.get("recordDay"))
        day_iso = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
        basic = rec.get("heartRateBasic") or {}
        resting = basic.get("avgRestingHeartRate") or basic.get("lastRestHeartRate")
        store.upsert_daily(day_iso, {
            "resting_hr": resting or None,
            "hr_min": basic.get("minHeartRate") or None,
            "hr_max": basic.get("maxHeartRate") or None,
        })
        n += 1
    store.set_cursor("heart_rate", end)
    _LOGGER.info("heart_rate: %d 天已同步", n)
    return n


def sync_sleep(client: HuaweiHealthClient) -> int:
    start, end = _ymd_range(store.get_cursor("sleep"))
    resp = client.health_stat(start, end, [9])
    n = 0
    for rec in resp.get("professionalSleepTotal") or []:
        day = str(rec.get("recordDay"))
        day_iso = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
        s = rec.get("professionalSleep") or {}
        spo2 = s.get("lastAvgSpO2")
        spo2 = None if spo2 is None or spo2 < 0 else round(float(spo2), 1)
        store.upsert_daily(day_iso, {
            "sleep_total_min": s.get("allSleepTime") or None,
            "sleep_deep_min": s.get("deepSleepTime") or None,
            "sleep_rem_min": s.get("dreamTime") or None,
            "sleep_light_min": s.get("lightSleepTime") or None,
            "sleep_awake_min": s.get("awakeTime"),
            "sleep_bedtime": _iso_local(s.get("goBedTime")),
            "sleep_waketime": _iso_local(s.get("wakeupTime")),
            "sleep_score": s.get("sleepScore") or None,
            "spo2_avg": spo2,
        })
        n += 1
    store.set_cursor("sleep", end)
    _LOGGER.info("sleep: %d 天已同步", n)
    return n


def sync_workouts(client: HuaweiHealthClient) -> int:
    start, end = _ymd_range(store.get_cursor("workouts"))
    records = client._by_time_range(
        lambda a, b: client.sports_detail(a, b, SESSION_SPORT_TYPES), start, end)
    sessions = sport_sessions(records)
    per_day: dict[str, int] = {}
    for s in sessions:
        start_ms = _to_ms(s.get("start"))
        end_ms = _to_ms(s.get("end"))
        dist_m = s.get("distance_m") or 0
        dur_min = s.get("duration_min") or 0
        pace = int(dur_min * 60 / (dist_m / 1000)) if dist_m > 0 and dur_min > 0 else None
        data_id = f"w:{s.get('sport_type')}:{start_ms}"
        store.upsert_workout({
            "data_id": data_id,
            "start_utc": start_ms,
            "end_utc": end_ms,
            "sport_type": s.get("sport_type"),
            "duration_min": dur_min or None,
            "distance_m": dist_m or None,
            "avg_pace_sec_per_km": pace,
            "avg_hr": None, "max_hr": None,
            "kcal": s.get("kcal"),
        })
        day = s["start"].date().isoformat() if s.get("start") else None
        if day:
            per_day[day] = per_day.get(day, 0) + 1
    for day, cnt in per_day.items():
        store.upsert_daily(day, {"workout_count": cnt})
    store.set_cursor("workouts", end)
    _LOGGER.info("workouts: %d 条已同步", len(sessions))
    return len(sessions)


def sync_all() -> dict:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = _get_client()
    results = {}
    for name, fn in [("steps", sync_steps), ("heart_rate", sync_heart_rate),
                     ("sleep", sync_sleep), ("workouts", sync_workouts)]:
        try:
            results[name] = fn(client)
            time.sleep(0.6)  # 请求间隔 ≥500ms（任务书 §7）
        except Exception as e:  # noqa: BLE001
            _LOGGER.error("%s 同步失败: %s: %s", name, type(e).__name__, e)
            store.set_cursor(name, store.get_cursor(name) or 0, error=f"{type(e).__name__}: {e}")
            results[name] = f"ERROR: {e}"
    return results


if __name__ == "__main__":
    print(sync_all())
