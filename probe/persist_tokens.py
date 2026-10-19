#!/usr/bin/env python3
"""换码即存：stdin 读用户粘贴的 hms:// 文本 → 换 token → 加密存 DB → 拉步数验证。

授权码只在内存里用一次，不落盘、不打印。token 加密后才进 SQLite（600 权限）。
"""
import sys
from datetime import date, timedelta

sys.path.insert(0, "/home/hatch/workspace/huawei-health-mcp")

from hwclient.client import (
    HuaweiHealthClient,
    authorization_code_from,
    day_int,
    exchange_authorization_code,
)
from app.token_store import load_tokens, save_tokens


def main() -> int:
    code = authorization_code_from(sys.stdin.read())
    if not code:
        print("FAIL: 无法从输入中提取到授权码")
        return 1
    print(f"授权码已提取（长度 {len(code)}），开始换 token…")

    try:
        tokens = exchange_authorization_code(code)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: 换 token 失败：{type(e).__name__}: {e}")
        return 2
    print(f"token 换取成功，uid={tokens.uid}")

    save_tokens(tokens)
    print("token 已加密存入本地数据库（180 天有效，自动续期）")

    # 从 DB 读回，确认加解密链路可用
    tokens2 = load_tokens()
    if not tokens2 or not tokens2.access_token:
        print("FAIL: token 存入后读回失败")
        return 3
    print("token 读回验证通过")

    client = HuaweiHealthClient(tokens2)
    today = date.today()
    stat = client.sports_stat(day_int(today - timedelta(days=2)), day_int(today))
    import json
    body = json.dumps(stat, ensure_ascii=False)
    print("getSportsStat 返回（摘要）：")
    print(body[:1500])
    print()
    print("DONE：阶段 0 完成，token 已持久化，可进阶段 1。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
