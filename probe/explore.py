#!/usr/bin/env python3
"""阶段 1 探测：拉各数据端点，看实际返回结构（只打摘要，不打原始值）。
调用间隔 ≥1 秒，遵守任务书 §9 红线 4。
"""
import json
import os
import sys
import time
from datetime import date, timedelta

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from hwclient.client import HuaweiHealthClient, day_int
from hwclient.const import SESSION_SPORT_TYPES
from app.token_store import load_tokens


def summarize(name, payload, depth=2):
    print(f"===== {name} =====")
    rc = payload.get("resultCode")
    print("resultCode:", rc)
    if rc != 0:
        print("desc:", payload.get("resultDesc"))
        return
    data = payload.get("data")
    if isinstance(data, dict):
        print("data keys:", list(data.keys()))
        for k, v in data.items():
            if isinstance(v, list):
                print(f"  {k}: list[{len(v)}]", end="")
                if v and isinstance(v[0], dict):
                    print(" keys:", list(v[0].keys())[:12])
                else:
                    print()
            else:
                print(f"  {k}: {type(v).__name__}")
    elif isinstance(data, list):
        print(f"data: list[{len(data)}]")
        if data and isinstance(data[0], dict):
            print("  [0] keys:", list(data[0].keys())[:15])
    else:
        print("data:", type(data).__name__, str(data)[:100])


def main():
    tokens = load_tokens()
    assert tokens and tokens.access_token, "no tokens in DB"
    client = HuaweiHealthClient(tokens)
    today = date.today()
    s3 = day_int(today - timedelta(days=2))
    s7 = day_int(today - timedelta(days=6))
    e = day_int(today)

    summarize("bind_devices", client.bind_devices())
    time.sleep(1)
    summarize("health_stat[7,9,11,12] 3d",
              client.health_stat(s3, e, [7, 9, 11, 12]))
    time.sleep(1)
    summarize("sports_detail(workouts) 7d",
              client.sports_detail(s7, e, SESSION_SPORT_TYPES))
    print("DONE")


if __name__ == "__main__":
    main()
