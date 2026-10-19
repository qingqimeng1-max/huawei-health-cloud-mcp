#!/usr/bin/env python3
"""阶段 0 探测：生成 OAuth 授权 URL（任务书 §3.2）。

参数与任务书完全一致，不改。把打印出的 URL 发给用户走人工授权流程。
"""
import sys

sys.path.insert(0, "/home/hatch/workspace/huawei-health-mcp")

from hwclient.client import authorization_url

if __name__ == "__main__":
    url = authorization_url(language="zh-cn")
    print(url)
