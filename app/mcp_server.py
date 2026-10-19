"""MCP Server：任务书 §6。6 个只读 tool + 健康检查接口。

运行：.venv/bin/python app/mcp_server.py  （监听 127.0.0.1:8000/mcp）
"""
import os
import sys
from datetime import date, timedelta

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from fastmcp import FastMCP

from hwclient.const import SPORT_TYPES
from app import store

mcp = FastMCP("huawei-health")


def _days(start_date: str, end_date: str) -> list[str]:
    s = date.fromisoformat(start_date)
    e = date.fromisoformat(end_date)
    if s > e:
        raise ValueError("start_date 不能晚于 end_date")
    if (e - s).days > 90:
        raise ValueError("一次最多查 90 天")
    return [(s + timedelta(days=i)).isoformat() for i in range((e - s).days + 1)]


def _completeness(days: list[str], with_data: set[str]) -> dict:
    missing = [d for d in days if d not in with_data]
    return {"days_requested": len(days), "days_with_data": len(with_data),
            "missing": missing}


def _query_daily(days: list[str]) -> dict[str, dict]:
    conn = store.get_db()
    try:
        rows = conn.execute(
            f"SELECT * FROM daily_summary WHERE day IN ({','.join('?' * len(days))})",
            days,
        ).fetchall()
        return {r["day"]: dict(r) for r in rows}
    finally:
        conn.close()


@mcp.tool()
def get_steps(start_date: str, end_date: str, granularity: str = "day") -> dict:
    """按天查询步数、距离、活动时长、消耗热量。日期格式 YYYY-MM-DD。"""
    if granularity != "day":
        return {"error": {"code": "unsupported_granularity",
                          "message": "当前只同步了天粒度数据，hour 暂不支持"}}
    days = _days(start_date, end_date)
    data = _query_daily(days)
    items, with_data = [], set()
    for d in days:
        r = data.get(d)
        if r and r.get("steps") is not None:
            with_data.add(d)
            items.append({"date": d, "steps": r["steps"],
                          "distance_km": r["distance_km"], "kcal": r["kcal"]})
    return {"items": items, "data_completeness": _completeness(days, with_data)}


@mcp.tool()
def get_heart_rate(start_date: str, end_date: str, kind: str = "resting") -> dict:
    """查询心率。kind=resting 返回每日静息/最低/最高心率。"""
    if kind != "resting":
        return {"error": {"code": "unsupported_kind",
                          "message": "连续心率采样暂未同步，当前仅支持 resting"}}
    days = _days(start_date, end_date)
    data = _query_daily(days)
    items, with_data = [], set()
    for d in days:
        r = data.get(d)
        if r and r.get("resting_hr") is not None:
            with_data.add(d)
            items.append({"date": d, "resting_hr": r["resting_hr"],
                          "hr_min": r["hr_min"], "hr_max": r["hr_max"]})
    return {"items": items, "data_completeness": _completeness(days, with_data)}


@mcp.tool()
def get_sleep(start_date: str, end_date: str) -> dict:
    """查询睡眠：总时长、深睡/浅睡/REM/清醒分段、入睡/起床时间、睡眠评分。"""
    days = _days(start_date, end_date)
    data = _query_daily(days)
    items, with_data = [], set()
    for d in days:
        r = data.get(d)
        if r and r.get("sleep_total_min") is not None:
            with_data.add(d)
            items.append({"date": d, "total_min": r["sleep_total_min"],
                          "deep_min": r["sleep_deep_min"],
                          "rem_min": r["sleep_rem_min"],
                          "light_min": r["sleep_light_min"],
                          "awake_min": r["sleep_awake_min"],
                          "bedtime": r["sleep_bedtime"],
                          "waketime": r["sleep_waketime"],
                          "score": r["sleep_score"]})
    return {"items": items, "data_completeness": _completeness(days, with_data)}


@mcp.tool()
def get_spo2(start_date: str, end_date: str) -> dict:
    """查询血氧。注：该账号的设备目前没有血氧记录，会返回空并如实标注。"""
    days = _days(start_date, end_date)
    data = _query_daily(days)
    items, with_data = [], set()
    for d in days:
        r = data.get(d)
        if r and r.get("spo2_avg") is not None:
            with_data.add(d)
            items.append({"date": d, "spo2_avg": r["spo2_avg"]})
    out = {"items": items, "data_completeness": _completeness(days, with_data)}
    if not items:
        out["note"] = "该设备无血氧记录（接口返回 -1），不是同步故障"
    return out


@mcp.tool()
def get_workouts(start_date: str, end_date: str, activity_type: str | None = None) -> dict:
    """查询运动记录：类型、时长、距离、配速、热量。"""
    days = _days(start_date, end_date)
    conn = store.get_db()
    try:
        rows = conn.execute(
            """SELECT * FROM workout WHERE date(start_utc/1000, 'unixepoch', '+8 hours')
               BETWEEN ? AND ? ORDER BY start_utc""",
            (days[0], days[-1]),
        ).fetchall()
    finally:
        conn.close()
    items = []
    for r in rows:
        r = dict(r)
        stype = SPORT_TYPES.get(r["sport_type"], str(r["sport_type"]))
        if activity_type and activity_type != stype:
            continue
        items.append({
            "date": r["start_utc"] and __import__("datetime").datetime.fromtimestamp(
                r["start_utc"] / 1000).strftime("%Y-%m-%d %H:%M"),
            "activity": stype, "duration_min": r["duration_min"],
            "distance_m": r["distance_m"],
            "avg_pace_sec_per_km": r["avg_pace_sec_per_km"],
            "kcal": r["kcal"],
        })
    with_data = {i["date"][:10] for i in items}
    return {"items": items, "data_completeness": _completeness(days, with_data)}


@mcp.tool()
def get_health_summary(days: int = 7) -> dict:
    """最近 N 天健康总览：步数、心率、睡眠、运动，一次给足上下文。"""
    days = max(1, min(days, 90))
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    return {
        "steps": get_steps(start, end),
        "heart_rate": get_heart_rate(start, end),
        "sleep": get_sleep(start, end),
        "spo2": get_spo2(start, end),
        "workouts": get_workouts(start, end),
    }


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    from starlette.responses import JSONResponse
    return JSONResponse({"status": "ok", "service": "huawei-health-mcp"})


@mcp.custom_route("/debug/last_sync", methods=["GET"])
async def last_sync(request):
    """各指标同步状态：游标、上次成功时间、失败计数、上次运行耗时。

    fail_count>0 表示该指标最近一次同步失败（游标未推进，last_ok_at 是上次成功时间）。
    """
    from starlette.responses import JSONResponse
    conn = store.get_db()
    try:
        rows = conn.execute(
            "SELECT metric, synced_until, last_ok_at, last_error, fail_count,"
            " last_run_at, last_duration_s FROM sync_cursor"
        ).fetchall()
        cursors = [dict(r) for r in rows]
        failing = [c["metric"] for c in cursors if (c.get("fail_count") or 0) > 0]
        return JSONResponse({"cursors": cursors, "failing_metrics": failing})
    finally:
        conn.close()


if __name__ == "__main__":
    import logging
    import os
    import threading
    import time

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    def _scheduler_loop():
        from app.sync import sync_all
        interval = float(os.environ.get("SYNC_INTERVAL_HOURS", "2")) * 3600
        if interval <= 0:
            return
        while True:
            try:
                # 启动时先同步一次，之后按间隔
                sync_all()
            except Exception as e:  # noqa: BLE001
                logging.error("定时同步失败: %s: %s", type(e).__name__, e)
            time.sleep(interval)

    if float(os.environ.get("SYNC_INTERVAL_HOURS", "2")) > 0:
        t = threading.Thread(target=_scheduler_loop, daemon=True,
                             name="sync-scheduler")
        t.start()
        logging.info("定时同步已启动")

    mcp.run(transport="http", host="127.0.0.1", port=8000, path="/mcp")
