"""الإعدادات: PUBLIC_BASE_URL وعنوان التطبيق العام."""
import json
import os

from urllib.parse import urlparse
import ipaddress

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# مجلد البيانات: قابل للتوجيه لمجلد دائم عند النشر (WPD_DATA_DIR=/data)
DATA_DIR = os.environ.get("WPD_DATA_DIR") or os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

# المنفذ: منصات النشر تحقن PORT (يليه WPD_PORT ثم الافتراضي 8081)
PORT = int(os.environ.get("PORT", os.environ.get("WPD_PORT", "8081")))


def _is_local_host(host: str) -> bool:
    h = (host or "").strip().lower().rstrip(".")
    if h in ("localhost", "localhost.localdomain", "localdomain"):
        return True
    if h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h.strip("[]"))
        return bool(
            ip.is_loopback or ip.is_private or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified
        )
    except ValueError:
        return False


def load_config() -> dict:
    base = (os.environ.get("PUBLIC_BASE_URL") or "").strip()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            disk = json.load(f)
            if not base and isinstance(disk, dict):
                base = str(disk.get("public_base_url") or "").strip()
    except (OSError, ValueError):
        pass
    base = base.rstrip("/")
    return {"public_base_url": base, "is_public": is_public_base(base)}


def save_config(public_base_url: str) -> dict:
    base = (public_base_url or "").strip().rstrip("/")
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"public_base_url": base}, f, ensure_ascii=False)
    os.replace(tmp, CONFIG_PATH)
    return {"public_base_url": base, "is_public": is_public_base(base)}


def is_public_base(base: str) -> bool:
    """عام = https + مضيف غير محلي."""
    if not base:
        return False
    try:
        p = urlparse(base)
    except ValueError:
        return False
    if p.scheme != "https":
        return False
    host = (p.hostname or "").strip("[]")
    if not host or _is_local_host(host):
        return False
    return True


def effective_base(request_host: str) -> tuple:
    """يرجع (base_url, is_public). يفضّل PUBLIC_BASE_URL دائمًا لروابط المشاركة."""
    cfg = load_config()
    if cfg["public_base_url"]:
        return cfg["public_base_url"], cfg["is_public"]
    host = (request_host or "").split(",")[0].strip()
    if host:
        scheme = "https" if host.endswith(":443") else "http"
        return f"{scheme}://{host}", False
    return f"http://127.0.0.1:{PORT}", False
