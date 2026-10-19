#!/usr/bin/env python3
"""阶段 0 探测：换 token + 立刻验证步数（任务书 §3.4 / §3.6）。

用法：把用户粘贴的 hms://redirect_url?code=... 整段经 stdin 喂进来。
授权码只在内存里用一次，不落盘、不打印、不进日志。
验证标准：getSportsStat 返回的最近一天步数（与手机 App 首页对比由人工完成）。
"""
import sys
from datetime import date, timedelta

sys.path.insert(0, "/home/hatch/workspace/huawei-health-mcp")

from hwclient.client import (
    HuaweiHealthClient,
    authorization_code_from,
    exchange_authorization_code,
    day_int,
)
from hwclient import const


def main() -> int:
    pasted = sys.stdin.read()
    code = authorization_code_from(pasted)
    if not code:
        print("FAIL: 无法从输入中提取到授权码")
        return 1
    print(f"授权码已提取（长度 {len(code)}），开始换 token…")

    try:
        tokens = exchange_authorization_code(code)
    except Exception as e:  # noqa: BLE001 - 探测脚本，直接报原文
        print(f"FAIL: 换 token 失败：{type(e).__name__}: {e}")
        return 2

    print(f"token 换取成功，uid={tokens.uid}，siteId={tokens.site_id}")
    # token 只在内存里用，不打印

    client = HuaweiHealthClient(tokens)
    today = date.today()
    start = day_int(today - timedelta(days=2))
    end = day_int(today)
    try:
        stat = client.sports_stat(start, end)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: getSportsStat 调用失败：{type(e).__name__}: {e}")
        return 3

    import json
    print("getSportsStat 返回（原始结构摘要）：")
    print(json.dumps(stat, ensure_ascii=False)[:2000])
    print()
    print("判定：请把上面最近一天的步数与手机 App 首页步数对比，±1% 内算通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
