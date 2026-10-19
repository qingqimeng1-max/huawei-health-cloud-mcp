#!/usr/bin/env python3
"""阶段 0 探测：生成 OAuth 授权 URL（任务书 §3.2）。

参数与任务书完全一致，不改。把打印出的 URL 发给用户走人工授权流程。
"""
import os
import sys

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from hwclient.client import authorization_url

if __name__ == "__main__":
    url = authorization_url(language="zh-cn")
    print(url)
