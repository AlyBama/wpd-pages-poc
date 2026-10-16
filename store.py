"""مخزن المشاركات في ملف JSON مع قفل للخيوط."""
import json
import os
import threading
import time

import config

DATA_DIR = config.DATA_DIR
SHARES_PATH = os.path.join(DATA_DIR, "shares.json")

_lock = threading.Lock()


def _read_all() -> dict:
    try:
        with open(SHARES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_all(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SHARES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, SHARES_PATH)


def save_share(token: str, record: dict) -> None:
    record = dict(record)
    record["created_at"] = int(time.time())
    with _lock:
        data = _read_all()
        data[token] = record
        _write_all(data)


def get_share(token: str):
    with _lock:
        return _read_all().get(token)
