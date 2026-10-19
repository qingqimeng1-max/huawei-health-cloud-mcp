"""Token 安全存储：Fernet 加密后存 SQLite。

安全设计（对应代码评审问题 2）：
- 密钥与数据库分开存放：密钥在 KEY_DIR（默认 ~/.huawei-health-keys），
  数据库在 DATA_DIR。两者不要一起备份、一起复制。
- 每次启动都校验文件权限（600），不对就地修复并告警。
- 不进 git、不进日志（见 .gitignore）。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.fernet import Fernet

_LOGGER = logging.getLogger("token_store")

BASE_DIR = os.environ.get("APP_BASE_DIR",
                           os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
# 密钥目录默认与数据目录分离（固定路径，不依赖 $HOME，保证各进程一致）；
# Docker 部署时可挂独立 volume 或走 secret
KEY_DIR = os.environ.get("KEY_DIR",
                          os.path.join(os.path.dirname(BASE_DIR), ".huawei-health-keys"))
KEY_PATH = os.path.join(KEY_DIR, ".fernet_key")
DB_PATH = os.path.join(DATA_DIR, "health.db")

_WANT_MODE = stat.S_IRUSR | stat.S_IWUSR  # 600


def _ensure_secure(path: str, what: str) -> None:
    """确保文件权限为 600；不对就地修复并告警。"""
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        return
    if mode != _WANT_MODE:
        _LOGGER.warning("%s 权限为 %o，不是 600，已修复", what, mode)
        os.chmod(path, _WANT_MODE)


def _get_fernet() -> Fernet:
    os.makedirs(KEY_DIR, exist_ok=True)
    if not os.path.exists(KEY_PATH):
        # 迁移：旧版本密钥可能在 DATA_DIR 或 ~/.huawei-health-keys 下，搬到分离目录
        legacy_candidates = [
            os.path.join(DATA_DIR, ".fernet_key"),
            os.path.join(os.path.expanduser("~"), ".huawei-health-keys", ".fernet_key"),
        ]
        moved = False
        for legacy in legacy_candidates:
            if os.path.exists(legacy) and os.path.abspath(legacy) != os.path.abspath(KEY_PATH):
                os.rename(legacy, KEY_PATH)
                moved = True
                _LOGGER.info("已将密钥从 %s 迁移到 %s", legacy, KEY_PATH)
                break
        if not moved:
            key = Fernet.generate_key()
            with open(KEY_PATH, "wb") as f:
                f.write(key)
            _LOGGER.info("已生成新的 token 加密密钥：%s", KEY_PATH)
        os.chmod(KEY_PATH, _WANT_MODE)
    else:
        _ensure_secure(KEY_PATH, "密钥文件")
    with open(KEY_PATH, "rb") as f:
        key = f.read()
    return Fernet(key)


def _get_db() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    fresh = not os.path.exists(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    if fresh:
        os.chmod(DB_PATH, _WANT_MODE)
    else:
        _ensure_secure(DB_PATH, "数据库文件")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kv_store ("
        " key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)"
    )
    conn.commit()
    return conn


def save_tokens(tokens) -> None:
    """加密存储 Tokens.as_dict。"""
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
