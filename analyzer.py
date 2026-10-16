"""تحليل الروابط: جلب HTML، استخراج البيانات، فحص الصور المرشحة.

قواعد الأمان:
- يقبل http/https فقط.
- يمنع SSRF: يحظر localhost والشبكات الداخلية قبل كل طلب وبعد كل تحويل.
- مهلات محددة وحدود حجم، وبدون إعادة محاولات غير ضرورية.
"""
import concurrent.futures
import gzip
import http.cookiejar
import io
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import zlib
from html.parser import HTMLParser

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

MAX_CANDIDATES = 24
HTML_MAX_BYTES = 1_500_000
IMG_MAX_BYTES = 5_000_000
FETCH_TIMEOUT = 12
IMG_TIMEOUT = 10
MAX_REDIRECTS = 5

TARGET_RATIO = 1200 / 630  # 1.904...

UA_BROWSER = (
    "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36 "
    "WhatsAppPreviewDoctor/2.0"
)

# جرّة كوكيز مشتركة: بعض جدران الحماية تزرع كوكي ثم تتوقعه في الطلب التالي
_COOKIE_JAR = http.cookiejar.CookieJar()


def _browser_headers(accept: str, full: bool = False) -> dict:
    """هيدرات تحاكي المتصفح. full=True للمحاولة الثانية بعد 403."""
    h = {
        "User-Agent": UA_BROWSER,
        "Accept": accept,
        "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Upgrade-Insecure-Requests": "1",
    }
    if accept.startswith("text/html"):
        h.update({"Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
                  "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1"})
    else:
        h.update({"Sec-Fetch-Dest": "image", "Sec-Fetch-Mode": "no-cors",
                  "Sec-Fetch-Site": "cross-site"})
    if full:
        h["Cache-Control"] = "max-age=0"
    return h


def _decode_body(data: bytes, encoding: str) -> bytes:
    enc = (encoding or "").lower()
    try:
        if "gzip" in enc:
            return gzip.decompress(data)
        if "deflate" in enc:
            try:
                return zlib.decompress(data)
            except zlib.error:
                return zlib.decompress(data, -zlib.MAX_WBITS)
    except (OSError, EOFError, zlib.error):
        pass
    return data

BLOCKED_HOSTNAMES = {
    "localhost", "localhost.localdomain", "localdomain",
    "metadata.google.internal", "metadata.google",
    "instance-data", "instance-data-compute",
}


class UrlBlockedError(ValueError):
    pass


class FetchError(ValueError):
    def __init__(self, message, status=None, no_retry=False):
        super().__init__(message)
        self.status = status
        self.no_retry = no_retry


def allow_private_enabled(allow_private=None) -> bool:
    if allow_private is not None:
        return bool(allow_private)
    import os
    return os.environ.get("WPD_ALLOW_PRIVATE", "") == "1"


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return bool(
        ip.is_loopback or ip.is_private or ip.is_link_local
        or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


def check_host_allowed(hostname: str, allow_private=None) -> None:
    allow_private = allow_private_enabled(allow_private)
    host = (hostname or "").strip().lower().rstrip(".").strip("[]")
    if not host:
        raise UrlBlockedError("العنوان فارغ أو غير صالح")
    if allow_private:  # وضع الاختبار المحلي فقط (WPD_ALLOW_PRIVATE=1)
        return
    if host in BLOCKED_HOSTNAMES or host.endswith(".localhost"):
        raise UrlBlockedError(f"العنوان محظور: {host}")
    if host in ("localhost",):
        raise UrlBlockedError("الوصول إلى localhost محظور")
    # صدّ الصيغ الرقمية الملتبسة بسرعة عبر getaddrinfo ثم فحص كل IP
    try:
        infos = socket.getaddrinfo(host, None, family=socket.AF_UNSPEC,
                                   type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise UrlBlockedError(f"تعذّر حل اسم المضيف: {host}")
    if not infos:
        raise UrlBlockedError(f"تعذّر حل اسم المضيف: {host}")
    for info in infos:
        ip_str = info[4][0]
        if _is_blocked_ip(ip_str):
            raise UrlBlockedError(
                f"الوصول إلى الشبكات الداخلية محظور: {host}")


def validate_url(url: str, allow_private=None) -> urllib.parse.ParseResult:
    url = (url or "").strip()
    if len(url) > 2048:
        raise UrlBlockedError("الرابط أطول من الحد المسموح")
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        raise UrlBlockedError("صيغة الرابط غير صالحة")
    if parts.scheme not in ("http", "https"):
        raise UrlBlockedError("يُقبل http وhttps فقط")
    if not parts.hostname:
        raise UrlBlockedError("الرابط بلا مضيف")
    if "@" in (parts.netloc or ""):
        raise UrlBlockedError("صيغة المضيف غير مسموحة")
    check_host_allowed(parts.hostname, allow_private=allow_private)
    return parts


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read_limited(resp, limit: int) -> bytes:
    chunks = []
    remaining = limit + 1
    while remaining > 0:
        chunk = resp.read(min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def fetch_bytes(url: str, timeout: int, limit: int, accept: str,
                allow_private=None,
                referer: str = None, full_headers: bool = False) -> tuple:
    """يجلب الرابط مع تتبّع يدوي للتحويلات (بحد أقصى) والتحقق بعد كل تحويل.

    يعيد (الbytes, الرابط_النهائي, نوع_المحتوى, الحالة).
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        validate_url(current, allow_private=allow_private)
        req = urllib.request.Request(
            current, headers=_browser_headers(accept, full=full_headers))
        if referer:
            req.add_header("Referer", referer)
        opener = urllib.request.build_opener(
            _NoRedirect, urllib.request.HTTPCookieProcessor(_COOKIE_JAR))
        try:
            resp = opener.open(req, timeout=timeout)
            status = getattr(resp, "status", 200) or 200
            final = resp.geturl()
            if final and final != current:
                validate_url(final, allow_private=allow_private)
                current = final
            ctype = resp.headers.get("Content-Type", "")
            data = _decode_body(
                _read_limited(resp, limit),
                resp.headers.get("Content-Encoding", ""))
            try:
                resp.close()
            except Exception:
                pass
            if len(data) > limit:
                raise FetchError("حجم الاستجابة تجاوز الحد المسموح", status=status)
            return data, current, ctype, status
        except urllib.error.HTTPError as e:
            code = getattr(e, "code", None)
            if code in (301, 302, 303, 307, 308):
                loc = e.headers.get("Location") if e.headers else None
                if not loc:
                    raise FetchError("تحويل بلا وجهة", status=code)
                current = urllib.parse.urljoin(current, loc)
                try:
                    e.close()
                except Exception:
                    pass
                continue
            if code in (404, 410):
                raise FetchError(f"الصورة محذوفة (HTTP {code})",
                                 status=code, no_retry=True)
            if code == 403:
                raise FetchError("ممنوع الوصول (HTTP 403)",
                                 status=code, no_retry=False)
            raise FetchError(f"خطأ HTTP {code}", status=code,
                             no_retry=code is not None and 400 <= code < 500)
        except (UrlBlockedError, FetchError):
            raise
        except socket.timeout:
            raise FetchError("انتهت مهلة الاتصال")
        except OSError as e:
            raise FetchError(f"خطأ شبكة: {e}")
    raise FetchError("عدد التحويلات تجاوز الحد المسموح")


# ---------- تحليل HTML ----------

def _parse_srcset(value: str):
    out = []
    for part in (value or "").split(","):
        part = part.strip()
        if not part:
            continue
        token = part.split()[0]
        if token:
            out.append(token)
    return out


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_chunks = []
        self.in_title = False
        self.meta = []       # (attr, name, content)
        self.links = []      # dict rel/href
        self.imgs = []       # dict src/srcset/...
        self.posters = []
        self.ld_images = []
        self._in_ld = False
        self._ld_buf = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            content = a.get("content", "").strip()
            if not content:
                return
            if "property" in a:
                self.meta.append(("property", a["property"].strip().lower(), content))
            elif "name" in a:
                self.meta.append(("name", a["name"].strip().lower(), content))
            elif "itemprop" in a:
                self.meta.append(("itemprop", a["itemprop"].strip().lower(), content))
        elif tag == "link":
            rel = a.get("rel", "").lower()
            href = a.get("href", "").strip()
            if href:
                self.links.append({"rel": rel, "href": href})
        elif tag == "img":
            self.imgs.append({
                "src": a.get("src", "").strip(),
                "srcset": a.get("srcset", ""),
                "data-src": a.get("data-src", "").strip(),
                "data-original": a.get("data-original", "").strip(),
            })
        elif tag == "source":
            if a.get("srcset"):
                self.imgs.append({"src": "", "srcset": a.get("srcset", ""),
                                  "data-src": a.get("src", "").strip(),
                                  "data-original": ""})
            elif a.get("src"):
                self.imgs.append({"src": a.get("src", "").strip(), "srcset": "",
                                  "data-src": "", "data-original": ""})
        elif tag in ("video", "meta"):
            if a.get("poster"):
                self.posters.append(a["poster"].strip())
        elif tag == "script":
            if a.get("type", "").lower() == "application/ld+json":
                self._in_ld = True
                self._ld_buf = []

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        elif tag == "script" and self._in_ld:
            self._in_ld = False
            raw = "".join(self._ld_buf)
            self.ld_images.extend(_extract_ld_images(raw))

    def handle_data(self, data):
        if self.in_title:
            self.title_chunks.append(data)
        if self._in_ld:
            self._ld_buf.append(data)


def _extract_ld_images(raw: str):
    found = []
    try:
        obj = json.loads(raw)
    except ValueError:
        # التقط أي image/url بشكل متساهل
        for m in re.finditer(r'"image"\s*:\s*"([^"]+)"', raw):
            found.append(m.group(1))
        return found
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                kl = str(k).lower()
                if kl in ("image", "thumbnailurl", "thumbnail", "logo") and isinstance(v, str):
                    found.append(v)
                elif kl in ("image",) and isinstance(v, list):
                    for item in v:
                        if isinstance(item, str):
                            found.append(item)
                        elif isinstance(item, dict) and isinstance(item.get("url"), str):
                            found.append(item["url"])
                    continue
                else:
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return [x for x in found if isinstance(x, str)]


def _norm_url(u: str) -> str:
    u = (u or "").strip()
    if u.startswith("//"):
        u = "https:" + u
    return u


def extract_page_info(html: str, base_url: str) -> dict:
    p = _PageParser()
    try:
        p.feed(html[:HTML_MAX_BYTES])
    except Exception:
        pass
    title = re.sub(r"\s+", " ", "".join(p.title_chunks)).strip()[:300]
    og, tw, other = {}, {}, {}
    description = ""
    for attr, name, content in p.meta:
        c = content.strip()[:1000]
        if name == "description" and not description:
            description = c
        if name.startswith("og:"):
            og[name[3:]] = c
        elif name.startswith("twitter:"):
            tw[name[8:]] = c
        elif name in ("description",):
            other[name] = c
    if not title:
        title = og.get("title", "")[:300]
    if not description:
        description = og.get("description", tw.get("description", ""))[:500]

    icons = []
    for l in p.links:
        rel = l["rel"]
        if "apple-touch-icon" in rel or "icon" in rel.split() or "icon" in rel:
            href = _norm_url(l["href"])
            if href and not href.lower().startswith("data:"):
                icons.append(urllib.parse.urljoin(base_url, href))

    def abs_url(u):
        u = _norm_url(u)
        if not u or u.lower().startswith("data:") or u.lower().startswith("blob:"):
            return ""
        return urllib.parse.urljoin(base_url, u)

    ordered = []
    for key in ("image", "image:secure_url", "image:url"):
        if og.get(key):
            ordered.append(("og", abs_url(og[key])))
    for key in ("image", "image:src"):
        if tw.get(key):
            ordered.append(("twitter", abs_url(tw[key])))
    for ic in icons[:4]:
        ordered.append(("icon", ic))
    for li in p.ld_images[:6]:
        if abs_url(li):
            ordered.append(("structured", abs_url(li)))
    for poster in p.posters[:4]:
        if abs_url(poster):
            ordered.append(("poster", abs_url(poster)))
    for im in p.imgs:
        for cand in [im.get("src", ""), im.get("data-src", ""),
                     im.get("data-original", "")] + _parse_srcset(im.get("srcset", "")):
            au = abs_url(cand)
            if au:
                ordered.append(("img", au))

    deduped = []
    seen = set()
    for source, u in ordered:
        if not u or not u.startswith(("http://", "https://")):
            continue
        key = u.split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        deduped.append({"url": u, "source": source})
        if len(deduped) >= MAX_CANDIDATES:
            break

    return {
        "title": title,
        "description": description,
        "og": og,
        "twitter": tw,
        "icons": icons[:6],
        "candidates": deduped,
    }


# ---------- فحص الصور ----------

def score_image(w: int, h: int, nbytes: int) -> float:
    if not w or not h:
        return 0.0
    area = min(w * h, 3_000_000) / 3_000_000  # 0..1
    ratio = w / h
    # عقوبة فرق نسبة العرض إلى الارتفاع عن 1.91:1 (بحد أقصى معقول)
    diff = abs(ratio - TARGET_RATIO) / TARGET_RATIO
    aspect = max(0.0, 1.0 - min(diff, 1.5) / 1.5)
    size_bonus = 0.0
    if w >= 1200 and h >= 630:
        size_bonus = 8.0
    elif w >= 600 and h >= 315:
        size_bonus = 4.0
    elif w < 200 or h < 200:
        size_bonus = -15.0
    return round(area * 55 + aspect * 35 + size_bonus, 1)


def check_image(url: str, allow_private=None, page_url: str = None) -> dict:
    """يفحص صورة واحدة ويعيد قاموسًا بالحالة والأبعاد والدرجة."""
    result = {"url": url, "ok": False, "reason": "", "width": 0,
              "height": 0, "bytes": 0, "format": "", "score": 0.0,
              "no_retry": False}
    if Image is None:
        result["reason"] = "مكتبة الصور غير مثبتة (Pillow)"
        return result
    try:
        validate_url(url, allow_private=allow_private)
    except UrlBlockedError as e:
        result["reason"] = str(e)
        result["no_retry"] = True
        return result
    data = None
    for attempt in (0, 1):
        try:
            if attempt == 0:
                data, _, ctype, _ = fetch_bytes(
                    url, IMG_TIMEOUT, IMG_MAX_BYTES, "image/*,*/*;q=0.8",
                    allow_private=allow_private)
            else:
                # محاولة ثانية واحدة فقط: هيدرات متصفح كاملة + Referer
                try:
                    p0 = urllib.parse.urlsplit(url)
                    origin = "%s://%s/" % (p0.scheme, p0.netloc)
                except ValueError:
                    origin = None
                data, _, ctype, _ = fetch_bytes(
                    url, IMG_TIMEOUT, IMG_MAX_BYTES,
                    "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    allow_private=allow_private,
                    referer=page_url or origin, full_headers=True)
            break
        except FetchError as e:
            if e.no_retry or attempt == 1 or e.status != 403:
                result["reason"] = str(e)
                result["no_retry"] = bool(e.no_retry)
                return result
            continue  # 403 في الأولى → محاولة ثانية مبررة واحدة فقط
        except UrlBlockedError as e:
            result["reason"] = str(e)
            result["no_retry"] = True
            return result
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            w, h = im.size
            fmt = (im.format or "").upper()
        if w <= 0 or h <= 0:
            result["reason"] = "أبعاد غير صالحة"
            return result
        result.update(ok=True, width=w, height=h, bytes=len(data),
                      format=fmt, score=score_image(w, h, len(data)))
        return result
    except Exception:
        result["reason"] = "الملف ليس صورة صالحة"
        result["no_retry"] = True
        return result


def fetch_page(url: str, allow_private=None) -> tuple:
    """يجلب HTML الصفحة. يعيد (html, final_url)."""
    accept = ("text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8")
    try:
        data, final, ctype, status = fetch_bytes(
            url, FETCH_TIMEOUT, HTML_MAX_BYTES, accept,
            allow_private=allow_private)
    except FetchError as e:
        # محاولة ثانية واحدة فقط عند 403 (هيدرات كاملة + Referer لأصل الموقع)
        if e.status != 403 or e.no_retry:
            raise
        try:
            p0 = urllib.parse.urlsplit(url)
            origin = "%s://%s/" % (p0.scheme, p0.netloc)
        except ValueError:
            origin = None
        data, final, ctype, status = fetch_bytes(
            url, FETCH_TIMEOUT, HTML_MAX_BYTES, accept,
            allow_private=allow_private, referer=origin, full_headers=True)
    if status in (404, 410):
        raise FetchError(f"الصفحة غير موجودة (HTTP {status})", status=status)
    if status >= 400:
        raise FetchError(f"خطأ HTTP {status}", status=status)
    html = data.decode("utf-8", errors="replace")
    return html, final


def analyze_url(url: str, allow_private=None) -> dict:
    url = (url or "").strip()
    validate_url(url, allow_private=allow_private)
    html, final_url = fetch_page(url, allow_private=allow_private)
    info = extract_page_info(html, final_url)

    candidates = info["candidates"]
    results = []
    if candidates:
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(6, len(candidates))) as ex:
            futs = {ex.submit(check_image, c["url"],
                              allow_private, final_url): c for c in candidates}
            by_url = {}
            for fut, c in futs.items():
                try:
                    r = fut.result()
                except Exception as e:  # لا يُسقط الفحص كله
                    r = {"url": c["url"], "ok": False,
                         "reason": f"خطأ داخلي: {e}", "width": 0,
                         "height": 0, "bytes": 0, "format": "",
                         "score": 0.0, "no_retry": True}
                r["source"] = c["source"]
                by_url[c["url"]] = r
        results = [by_url[c["url"]] for c in candidates]

    valids = [r for r in results if r["ok"]]
    default = None
    default_note = ""
    # القاعدة: og:image أولًا إذا صالحة
    og_urls = {c["url"] for c in candidates if c["source"] == "og"}
    og_valid = [r for r in results if r["ok"] and r["url"] in og_urls]
    if og_valid:
        default = og_valid[0]
        default_note = "الصورة الافتراضية من الموقع (og:image)"
    elif valids:
        default = max(valids, key=lambda r: r["score"])
        default_note = "بديل: الصورة الافتراضية للموقع غير صالحة"
    return {
        "ok": True,
        "page_url": url,
        "final_url": final_url,
        "title": info["title"],
        "description": info["description"],
        "og": info["og"],
        "twitter": info["twitter"],
        "default": ({
            "url": default["url"], "width": default["width"],
            "height": default["height"], "score": default["score"],
            "source": default.get("source", "")} if default else None),
        "default_note": default_note,
        "images": results,
        "counts": {"candidates": len(results),
                   "valid": len(valids),
                   "failed": len(results) - len(valids)},
    }
