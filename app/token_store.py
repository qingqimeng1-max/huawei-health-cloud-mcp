"""Token 安全存储：Fernet 加密后存 SQLite，文件权限 600。

任务书 §8 验收项 9：token 只能加密存 DB，不进日志、不进 git。
密钥文件 data/.fernet_key（600），DB data/health.db（600）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys

sys.path.insert(0, "/home/hatch/workspace/huawei-health-mcp")

from cryptography.fernet import Fernet

BASE_DIR = os.environ.get("APP_BASE_DIR", "/home/hatch/workspace/huawei-health-mcp")
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
KEY_PATH = os.path.join(DATA_DIR, ".fernet_key")
DB_PATH = os.path.join(DATA_DIR, "health.db")


def _secure_file(path: str) -> None:
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 600


def _get_fernet() -> Fernet:
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(KEY_PATH):
        key = Fernet.generate_key()
        with open(KEY_PATH, "wb") as f:
            f.write(key)
        _secure_file(KEY_PATH)
    else:
        with open(KEY_PATH, "rb") as f:
            key = f.read()
    return Fernet(key)


def _get_db() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    fresh = not os.path.exists(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    if fresh:
        _secure_file(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kv_store ("
        "  key TEXT PRIMARY KEY,"
        "  value TEXT NOT NULL,"
        "  updated_at INTEGER NOT NULL)"
    )
    conn.commit()
    return conn


def save_tokens(tokens) -> None:
    """加密存储 Tokens.as_dict()。"""
    import time

    fernet = _get_fernet()
    payload = json.dumps(tokens.as_dict).encode("utf-8")
    blob = fernet.encrypt(payload).decode("ascii")
    conn = _get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO kv_store (key, value, updated_at)"
            " VALUES ('huawei_tokens', ?, ?)",
            (blob, int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()
    _secure_file(DB_PATH)


def load_tokens():
    """读出并解密为 Tokens 对象；没有则返回 None。"""
    from hwclient.client import Tokens

    if not os.path.exists(DB_PATH):
        return None
    fernet = _get_fernet()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT value FROM kv_store WHERE key='huawei_tokens'"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    data = json.loads(fernet.decrypt(row[0].encode("ascii")).decode("utf-8"))
    allowed = {"access_token", "refresh_token", "uid", "site_id",
               "expires_at", "refresh_expires_at", "session_host"}
    return Tokens(**{k: v for k, v in data.items() if k in allowed})
