"""اختبارات محلية قابلة للتكرار لمشروع v2 (لا تعتمد على مواقع خارجية).

يُشغّل: python3 tests/test_all.py
يبدأ خادم تجهيزات محليًا + خادم التطبيق كعملية فرعية، ثم يتحقق من كل معايير القبول.
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
DATA = os.path.join(BASE, "data")
FIXDIR = os.path.join(DATA, ".test_fixtures")

APP_PORT = None
APP_URL = None


def _free_port():
    import socket as _s
    s = _s.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))


def http(method, url, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def api(method, path, body=None):
    st, ct, raw = http(method, APP_URL + path, body)
    try:
        obj = json.loads(raw.decode())
    except ValueError:
        return st, ct, None
    return st, ct, obj


# ---------- تجهيز صور التجهيزات ----------
def make_images():
    from PIL import Image
    os.makedirs(FIXDIR, exist_ok=True)
    specs = {"wide": (1600, 600, (200, 60, 60)),
             "square": (800, 800, (60, 200, 60)),
             "vertical": (600, 1200, (60, 60, 200)),
             "icon": (192, 192, (200, 200, 60)),
             "ogsmall": (400, 300, (150, 80, 180))}
    for name, (w, h, color) in specs.items():
        im = Image.new("RGB", (w, h), color)
        im.save(os.path.join(FIXDIR, name + (".png" if name == "icon" else ".jpg")))
    with open(os.path.join(FIXDIR, "notimage.bin"), "w") as f:
        f.write("this is not an image")


# ---------- خادم التجهيزات ----------
class Fix(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/page-og.html":
            html = f"""<html><head><title>صفحة تجريبية</title>
<meta name="description" content="وصف تجريبي">
<meta property="og:title" content="عنوان OG">
<meta property="og:description" content="وصف OG">
<meta property="og:image" content="{{B}}/ogsmall.jpg">
<meta name="twitter:image" content="{{B}}/square.jpg">
<link rel="icon" href="{{B}}/icon.png">
<script type="application/ld+json">{{"@type":"Article","image":"{{B}}/vertical.jpg"}}</script>
</head><body>
<img src="{{B}}/wide.jpg">
<img src="{{B}}/wide.jpg">
<img srcset="{{B}}/square.jpg 800w, {{B}}/vertical.jpg 400w">
</body></html>""".replace("{B}", FIXBASE)
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/page-noimg.html":
            body = "<html><head><title>بلا صور</title></head><body><p>no images</p></body></html>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/page-broken.html":
            html = (f"<html><head><title>معطوبة</title>"
                    f'<meta property="og:image" content="{FIXBASE}/missing.jpg">'
                    f"</head><body>"
                    f'<img src="{FIXBASE}/square.jpg"></body></html>')
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/redirect":
            self.send_response(302)
            self.send_header("Location", FIXBASE + "/page-og.html")
            self.end_headers()
        elif path == "/forbidden.jpg":
            # 403 بدون Referer، وصورة صالحة مع Referer (لاختبار المحاولة المبررة)
            if self.headers.get("Referer"):
                return self._file("square.jpg", "image/jpeg")
            self.send_response(403)
            self.end_headers()
        elif path in ("/wide.jpg", "/square.jpg", "/vertical.jpg", "/ogsmall.jpg"):
            self._file(path[1:], "image/jpeg")
        elif path == "/icon.png":
            self._file("icon.png", "image/png")
        elif path == "/notimage.bin":
            body = b"this is not an image"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def _file(self, name, ctype):
        try:
            with open(os.path.join(FIXDIR, name), "rb") as f:
                body = f.read()
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_app(allow_private, port=None, extra_env=None):
    port = port or APP_PORT
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    env["WPD_PORT"] = str(port)
    env.pop("PORT", None)  # عزل تام: لا تدع PORT موروثًا يغلب منفذ الاختبار
    for k, v in (extra_env or {}).items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    if allow_private:
        env["WPD_ALLOW_PRIVATE"] = "1"
    else:
        env.pop("WPD_ALLOW_PRIVATE", None)
    p = subprocess.Popen([sys.executable, os.path.join(BASE, "server.py")],
                         cwd=BASE, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            req = urllib.request.Request(
                base + "/api/health",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                if r.status == 200:
                    return p
        except OSError:
            pass
        time.sleep(0.5)
    p.terminate()
    raise RuntimeError("app server did not start")


def stop_app(p):
    try:
        p.terminate()
        p.wait(timeout=8)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


def main():
    global FIXBASE, APP_PORT, APP_URL
    # منفذ حر لكل تشغيل: حصانة ضد أي خادم عالق من تجربة سابقة
    APP_PORT = _free_port()
    APP_URL = f"http://127.0.0.1:{APP_PORT}"
    make_images()
    fixsrv = ThreadingHTTPServer(("127.0.0.1", 0), Fix)
    FIXBASE = f"http://127.0.0.1:{fixsrv.server_address[1]}"
    threading.Thread(target=fixsrv.serve_forever, daemon=True).start()

    # ===== المرحلة 1: وظائف كاملة (سماح خاص للوصول للتجهيزات المحلية) =====
    app = start_app(allow_private=True)
    # عزل تام عن حالة المستخدم: صفّر العنوان العام المخزن قبل البدء
    api("POST", "/api/config", {"public_base_url": ""})
    try:
        # 1) الصحة
        st, ct, o = api("GET", "/api/health")
        check("health-200-json", st == 200 and o and o.get("ok") is True and "json" in ct, f"{st} {ct}")

        # 2) رابط غير صالح (scheme مرفوض) → JSON وليس HTML
        st, ct, o = api("POST", "/api/analyze", {"url": "ftp://example.com/x"})
        check("analyze-reject-scheme", st == 400 and o and o.get("ok") is False and "json" in ct, f"{st}")

        # 3) طلب JSON معطوب → JSON
        st, ct, raw = http("POST", APP_URL + "/api/analyze", timeout=15)
        st2, ct2, o2 = st, ct, None
        try:
            o2 = json.loads(raw.decode())
        except ValueError:
            pass
        # http() بدون body ترسل null body → الخادم يقرأ Content-Length=0 → {} → "أدخل رابطًا"
        check("analyze-empty-json", st2 == 400 and o2 and o2.get("ok") is False, f"{st2}")

        # 4) مسار API مجهول → JSON
        st, ct, o = api("GET", "/api/nope")
        check("api-404-json", st == 404 and "json" in ct and o.get("ok") is False, f"{st} {ct}")

        # 5) صفحة بها og:image صالحة + بدائل
        st, ct, d = api("POST", "/api/analyze", {"url": FIXBASE + "/page-og.html"})
        check("analyze-og-ok", st == 200 and d.get("ok") is True, f"{st} {(d or {}).get('error')}")
        if d and d.get("ok"):
            check("extract-title", "صفحة تجريبية" in (d.get("title") or ""), d.get("title"))
            check("extract-desc", "وصف تجريبي" in (d.get("description") or ""), d.get("description"))
            check("extract-og", (d.get("og") or {}).get("title") == "عنوان OG", d.get("og"))
            check("extract-twitter", "square" in (d.get("twitter") or {}).get("image", ""), d.get("twitter"))
            check("candidates-cap", (d.get("counts") or {}).get("candidates", 99) <= 24, d.get("counts"))
            # إزالة التكرار: wide مكررة مرتين في الصفحة
            urls = [im["url"] for im in d.get("images", [])]
            check("dedupe", len(urls) == len(set(urls)), f"{len(urls)} vs {len(set(urls))}")
            # القاعدة: الافتراضية = og:image حتى لو صورتها أصغر/أقل درجة
            check("default-is-og", d.get("default") and "ogsmall" in d["default"]["url"], d.get("default"))
            check("default-note", "og:image" in (d.get("default_note") or ""), d.get("default_note"))
            check("alts-valid", any("wide" in im["url"] and im["ok"] for im in d["images"]))
            check("icon-present", any(im["source"] == "icon" and im["ok"] for im in d["images"]))
            check("srcset-present", any("vertical" in im["url"] for im in d["images"]))
            check("ld-present", any(im["source"] == "structured" for im in d["images"]))

        # 6) صفحة بلا صور
        st, ct, d2 = api("POST", "/api/analyze", {"url": FIXBASE + "/page-noimg.html"})
        check("noimg-ok", st == 200 and d2.get("ok") and d2.get("default") is None
              and d2["counts"]["candidates"] == 0, f"{st} {d2}")

        # 7) og معطوبة → بديل مع توضيح
        st, ct, d3 = api("POST", "/api/analyze", {"url": FIXBASE + "/page-broken.html"})
        ok7 = st == 200 and d3.get("ok") and d3.get("default") and "square" in d3["default"]["url"]
        check("broken-og-fallback", ok7, f"{st} {d3}")
        if d3 and d3.get("ok"):
            check("broken-og-note", "بديل" in (d3.get("default_note") or ""), d3.get("default_note"))
            miss = [im for im in d3["images"] if "missing" in im["url"]]
            check("missing-404-reason", miss and not miss[0]["ok"] and "404" in (miss[0]["reason"] or ""),
                  miss[0] if miss else None)

        # 8) تحويل داخلي يُتّبع
        st, ct, d4 = api("POST", "/api/analyze", {"url": FIXBASE + "/redirect"})
        check("redirect-followed", st == 200 and d4.get("ok") and d4.get("final_url", "").endswith("/page-og.html"),
              f"{st} {(d4 or {}).get('final_url')}")

        # 9) تجهيز المعاينات: مربعة/عمودية/عريضة × 3 أوضاع → 1200×630 فعلية
        from PIL import Image
        first = None
        for src, label in [("square", "مربعة"), ("vertical", "عمودية"), ("wide", "عريضة")]:
            for mode in ("cover", "contain", "blur"):
                st, ct, g = api("POST", "/api/generate", {
                    "image_url": FIXBASE + f"/{src}.jpg",
                    "page_url": FIXBASE + "/page-og.html",
                    "title": "عنوان تجريبي", "description": "وصف تجريبي", "mode": mode})
                good = st == 200 and g.get("ok") and g.get("width") == 1200 and g.get("height") == 630
                check(f"gen-{src}-{mode}", good, f"{st} {(g or {}).get('error')}")
                if good:
                    pst, pctype, praw = http("GET", g["preview_url"], timeout=15)
                    try:
                        im = Image.open(io.BytesIO(praw))
                        im.load()
                        dims = im.size == (1200, 630)
                    except Exception:
                        dims = False
                    check(f"gen-{src}-{mode}-dims", pst == 200 and "jpeg" in pctype and dims,
                          f"{pst} {pctype}")
                    if src == "square" and mode == "cover":
                        first = g  # للاستخدام لاحقًا

        # 10) فرادة الروابط
        st, ct, ga = api("POST", "/api/generate", {"image_url": FIXBASE + "/wide.jpg",
                                                  "page_url": FIXBASE + "/page-og.html", "mode": "cover"})
        st, ct, gb = api("POST", "/api/generate", {"image_url": FIXBASE + "/wide.jpg",
                                                  "page_url": FIXBASE + "/page-og.html", "mode": "cover"})
        check("unique-tokens", ga.get("token") != gb.get("token"), (ga.get("token"), gb.get("token")))

        # 11) OG في HTML صفحة المشاركة + التحويل
        if first is None:
            check("share-og-tags", False, "no preview generated")
            check("share-og-nojs", False, "no preview generated")
            check("share-redirect", False, "no preview generated")
        else:
            share_path = urllib.request.urlsplit(first["share_url"]).path
            preview_path = urllib.request.urlsplit(first["preview_url"]).path
            token = first["token"]
            st, ct, sraw = http("GET", APP_URL + share_path, timeout=15)
            shtml = sraw.decode()
            tags = ["og:title", "og:description", "og:type", "og:url", "og:image",
                    "og:image:width", "og:image:height"]
            missing = [t for t in tags if f'property="{t}"' not in shtml]
            check("share-og-tags", st == 200 and not missing, f"missing={missing}")
            check("share-og-nojs", 'property="og:image"' in shtml and "1200" in shtml)
            check("share-redirect", 'http-equiv="refresh"' in shtml and FIXBASE in shtml)

            # 11ب) طلبات HEAD (الروبوتات تبدأ بها) — كانت ترد 501 قبل الإصلاح
            import http.client as _hc
            conn = _hc.HTTPConnection("127.0.0.1", APP_PORT, timeout=15)
            conn.request("HEAD", share_path)
            hr = conn.getresponse()
            hbody = hr.read()
            check("head-share", hr.status == 200 and "text/html" in (hr.getheader("Content-Type") or "")
                  and len(hbody) == 0, f"{hr.status} body={len(hbody)}")
            conn.request("HEAD", preview_path)
            hr2 = conn.getresponse()
            hbody2 = hr2.read()
            check("head-preview", hr2.status == 200 and "image/jpeg" in (hr2.getheader("Content-Type") or "")
                  and len(hbody2) == 0, f"{hr2.status} body={len(hbody2)}")
            conn.close()

            # 11ج) فحص ما يراه الروبوت عبر الخادم نفسه
            st, ct, sc = api("GET", "/api/sharecheck/" + token)
            sc_ok = (st == 200 and sc and sc.get("ok") is True
                     and all((sc.get("og") or {}).get(t) for t in tags))
            check("sharecheck-og", sc_ok, f"{st} {sc}")
            if sc and sc.get("ok"):
                pv = sc.get("preview") or {}
                check("sharecheck-preview", pv.get("exists") is True and pv.get("width") == 1200
                      and pv.get("height") == 630, pv)
                check("sharecheck-preview-bytes", 0 < pv.get("bytes", 0) <= 512_000, pv.get("bytes"))
                check("sharecheck-redirect", (sc.get("redirect_target") or "").startswith(FIXBASE),
                      sc.get("redirect_target"))
                # الصورة المعلنة هي فعلًا ملف JPEG الناتج (تطابق تام للبايتات)
                pst, pctype, praw = http("GET", APP_URL + preview_path, timeout=15)
                try:
                    disk = open(os.path.join(BASE, "data", "previews", token + ".jpg"), "rb").read()
                except OSError:
                    disk = b""
                check("sharecheck-ogimage-bytes", pst == 200 and praw == disk and len(disk) > 0,
                      f"{pst} net={len(praw)} disk={len(disk)}")

            # 11د) robots.txt متساهل
            st, ct, rraw = http("GET", APP_URL + "/robots.txt", timeout=15)
            check("robots", st == 200 and b"Allow" in rraw, f"{st}")

        # 11هـ) سقف حجم JPEG مع صورة ضجيجية (تُجبر تخفيض الجودة)
        try:
            import images as IMG
            from PIL import Image as _PI
            noise = _PI.effect_noise((1200, 630), 128).convert("RGB")
            nbuf = io.BytesIO()
            noise.save(nbuf, "PNG")
            out = IMG.compose_preview(nbuf.getvalue(), "cover")
            im2 = _PI.open(io.BytesIO(out))
            im2.load()
            check("size-cap", len(out) <= 512_000 and im2.size == (1200, 630), len(out))
        except Exception as e:
            check("size-cap", False, str(e))

        # 12) عنوان الصورة: مع base محلي → تحذير لا عام
        check("local-warn", bool(first) and "warning" in first and first.get("is_public") is False,
              first)

        # 13) ضبط PUBLIC_BASE_URL ثم توليد رابط عام
        st, ct, c = api("POST", "/api/config", {"public_base_url": "https://demo-test.trycloudflare.com"})
        check("config-save", st == 200 and c.get("is_public") is True, c)
        st, ct, gp = api("POST", "/api/generate", {"image_url": FIXBASE + "/wide.jpg",
                                                  "page_url": FIXBASE + "/page-og.html", "mode": "blur"})
        check("public-share-url", gp.get("share_url", "").startswith("https://demo-test.trycloudflare.com/share/"),
              gp.get("share_url"))
        check("public-preview-url", gp.get("preview_url", "").startswith("https://demo-test.trycloudflare.com/preview/"),
              gp.get("preview_url"))
        api("POST", "/api/config", {"public_base_url": ""})  # تنظيف

        # 13ب) وحدة فك gzip والكوكيز (بلا شبكة)
        import analyzer as AZU, gzip as _gz, http.cookiejar as _cj
        check("gzip-decode", AZU._decode_body(_gz.compress(b"hello" * 50), "gzip") == b"hello" * 50)
        check("gzip-fallback", AZU._decode_body(b"not-gzip", "gzip") == b"not-gzip")
        check("cookie-jar", isinstance(AZU._COOKIE_JAR, _cj.CookieJar))

        # 13ج) الرفع اليدوي
        import io as _io2
        from PIL import Image as _TPI
        _buf = _io2.BytesIO()
        _TPI.new("RGB", (640, 480), (10, 100, 200)).save(_buf, "PNG")
        _png = _buf.getvalue()
        _bnd = "TESTBOUND123"

        def _mpart(name, filename, ctype, payload):
            return (b"--" + _bnd.encode() + b"\r\n"
                    + ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                       % (name, filename)).encode()
                    + ("Content-Type: %s\r\n\r\n" % ctype).encode()
                    + payload + b"\r\n--" + _bnd.encode() + b"--\r\n")

        def _post_upload(body):
            req = urllib.request.Request(
                APP_URL + "/api/upload", data=body, method="POST",
                headers={"Content-Type": "multipart/form-data; boundary=" + _bnd})
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return r.status, json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                try:
                    return e.code, json.loads(e.read().decode())
                except ValueError:
                    return e.code, None

        ust, uo = _post_upload(_mpart("image", "t.png", "image/png", _png))
        check("upload-ok", ust == 200 and uo and uo.get("ok") is True and uo.get("width") == 640
              and uo.get("height") == 480, f"{ust} {uo}")
        ust2, uo2 = _post_upload(_mpart("image", "x.txt", "text/plain", b"not an image"))
        check("upload-not-image", ust2 == 422 and uo2 and uo2.get("ok") is False, f"{ust2} {uo2}")
        ust3, uo3 = _post_upload(_mpart("other", "t.png", "image/png", _png))
        check("upload-wrong-field", ust3 == 400 and uo3 and uo3.get("ok") is False, f"{ust3} {uo3}")
        if uo and uo.get("upload_id"):
            st, ct, gu = api("POST", "/api/generate", {
                "upload_id": uo["upload_id"], "page_url": FIXBASE + "/page-og.html",
                "title": "من رفع يدوي", "description": "d", "mode": "contain"})
            check("gen-from-upload", st == 200 and gu.get("ok") and gu.get("width") == 1200,
                  f"{st} {(gu or {}).get('error')}")
            if gu and gu.get("ok"):
                _pp = urllib.request.urlsplit(gu["preview_url"]).path
                _pst, _pct, _praw = http("GET", APP_URL + _pp, timeout=15)
                try:
                    _im = _TPI.open(_io2.BytesIO(_praw))
                    _im.load()
                    _ok = _im.size == (1200, 630)
                except Exception:
                    _ok = False
                check("gen-from-upload-dims", _pst == 200 and _ok, f"{_pst}")
        st, ct, gbad = api("POST", "/api/generate", {"upload_id": "bad!!id", "mode": "cover"})
        check("gen-upload-badid", st == 400 and gbad and gbad.get("ok") is False, f"{st} {gbad}")
        st, ct, gmiss = api("POST", "/api/generate", {"upload_id": "Abcdefgh12345678", "mode": "cover"})
        check("gen-upload-missing", st == 410 and gmiss and gmiss.get("ok") is False, f"{st} {gmiss}")

        # 13د) كشف الرابط المولود بعنوان قديم
        api("POST", "/api/config", {"public_base_url": "https://old-base.trycloudflare.com"})
        st, ct, gold = api("POST", "/api/generate", {"image_url": FIXBASE + "/wide.jpg",
                                                    "page_url": FIXBASE + "/page-og.html", "mode": "cover"})
        api("POST", "/api/config", {"public_base_url": "https://new-base.trycloudflare.com"})
        if gold and gold.get("token"):
            st, ct, scm = api("GET", "/api/sharecheck/" + gold["token"])
            check("stale-base-flag", st == 200 and scm.get("base_mismatch") is True
                  and scm.get("generated_with_base", "").startswith("https://old-base"),
                  f"{st} {scm}")
        else:
            check("stale-base-flag", False, "no token")
        api("POST", "/api/config", {"public_base_url": ""})  # تنظيف

        # 14) 403 مع Referer (محاولة مبررة واحدة) — مباشرة عبر check_image
        import analyzer as AZ
        r403 = AZ.check_image(FIXBASE + "/forbidden.jpg", allow_private=True,
                              page_url=FIXBASE + "/page-og.html")
        check("403-retry-with-referer", r403.get("ok") is True, r403.get("reason"))
    finally:
        stop_app(app)

    # ===== المرحلة 2: SSRF محظور فعليًا (بدون سماح خاص) =====
    app2 = start_app(allow_private=False)
    try:
        st, ct, o = api("POST", "/api/analyze", {"url": FIXBASE + "/page-og.html"})
        check("ssrf-block-analyze", st == 400 and o and o.get("ok") is False and "json" in ct,
              f"{st} {o}")
        import analyzer as AZ2
        blocked = 0
        for bad in ["http://localhost:9/x", "http://127.0.0.1/x", "http://10.0.0.1/",
                    "http://192.168.1.1/", "http://169.254.169.254/", "ftp://x.com/"]:
            try:
                AZ2.validate_url(bad, allow_private=False)
            except Exception:
                blocked += 1
        check("ssrf-block-list", blocked == 6, f"{blocked}/6")
    finally:
        stop_app(app2)

    # ===== المرحلة 3: بيئة النشر (PORT + WPD_DATA_DIR) =====
    p3port = _free_port()
    custdir = os.path.join(DATA, ".test_envdata")
    shutil.rmtree(custdir, ignore_errors=True)
    _old_port, _old_url = APP_PORT, APP_URL
    APP_PORT, APP_URL = p3port, f"http://127.0.0.1:{p3port}"
    app3 = start_app(allow_private=True,
                     extra_env={"WPD_DATA_DIR": custdir, "PUBLIC_BASE_URL": ""})
    try:
        st, ct, h = api("GET", "/api/health")
        check("deploy-port", st == 200 and h.get("port") == p3port, f"{st} {h}")
        st, ct, g3 = api("POST", "/api/generate", {
            "image_url": FIXBASE + "/square.jpg",
            "page_url": FIXBASE + "/page-og.html",
            "title": "T", "description": "D", "mode": "cover"})
        tok3 = (g3 or {}).get("token")
        prev_ok = bool(tok3) and os.path.isfile(
            os.path.join(custdir, "previews", tok3 + ".jpg"))
        try:
            with open(os.path.join(custdir, "shares.json"), encoding="utf-8") as f:
                shares_ok = tok3 in json.load(f)
        except (OSError, ValueError):
            shares_ok = False
        check("deploy-datadir", st == 200 and prev_ok and shares_ok,
              f"{st} prev={prev_ok} shares={shares_ok}")
    finally:
        stop_app(app3)
        APP_PORT, APP_URL = _old_port, _old_url
        shutil.rmtree(custdir, ignore_errors=True)

    # ===== المرحلة 4: جاهزية النشر (Docker + Hugging Face) =====
    try:
        with open(os.path.join(BASE, "Dockerfile"), encoding="utf-8") as f:
            dock = f.read()
        dock_ok = "7860" in dock and "requirements.txt" in dock and "server.py" in dock
    except OSError:
        dock_ok = False
    check("deploy-dockerfile", dock_ok)
    try:
        import yaml as _yaml
        with open(os.path.join(BASE, "README.md"), encoding="utf-8") as f:
            head = f.read(800)
        fm = None
        if head.startswith("---"):
            fm = _yaml.safe_load(head.split("---", 2)[1])
        hf_ok = (isinstance(fm, dict) and fm.get("sdk") == "gradio"
                 and int(fm.get("app_port", 0)) == 7860
                 and fm.get("app_file") == "server.py")
    except Exception:
        hf_ok = False
    check("deploy-hf-frontmatter", hf_ok, "sdk:gradio + app_file:server.py + app_port:7860")

    # ===== المرحلة 5: sharekit (منطق المعاينة النقي) =====
    import sharekit as SK
    _rec = {"title": "T <b>", "description": "D",
            "original_url": "https://o.example/x", "base_used": "https://b.example"}
    _tok = "Abcdefgh12345678"
    _html = SK.render_share_html(
        title=_rec["title"], description=_rec["description"],
        page_url="https://b.example/share/" + _tok,
        preview_url="https://b.example/preview/" + _tok + ".jpg",
        target=_rec["original_url"])
    check("kit-tags", all('property="%s"' % k in _html for k in SK.OG_KEYS))
    check("kit-escape", "<b>" not in _html and "&lt;b&gt;" in _html)
    check("kit-jsquote", 'location.href="https://o.example/x"' in _html)
    _kdir = os.path.join(DATA, ".test_sharekit")
    os.makedirs(_kdir, exist_ok=True)
    _KI = _TPI.new("RGB", (1200, 630), (1, 2, 3))
    _KI.save(os.path.join(_kdir, _tok + ".jpg"))
    check("kit-read", SK.read_preview(_kdir, _tok) is not None)
    check("kit-read-bad", SK.read_preview(_kdir, "bad!!") is None
          and SK.read_preview(_kdir, "Abcdefgh1234567X") is None)
    _info = SK.preview_info(_kdir, _tok)
    check("kit-info", _info["exists"] and _info["width"] == 1200 and _info["height"] == 630, _info)
    _sc = SK.assemble_sharecheck(
        record=dict(_rec, base_used="https://old.example"), token=_tok,
        base="https://b.example", is_public=True, preview_dir=_kdir)
    check("kit-assemble", _sc["ok"] and _sc["base_mismatch"] is True
          and _sc["preview"]["exists"] and _sc["og"]["og:image"].endswith(_tok + ".jpg"))
    _rep = SK.format_sharecheck_report(_sc)
    check("kit-report", "og:image" in _rep and "عنوان قديم" in _rep)
    shutil.rmtree(_kdir, ignore_errors=True)

    fixsrv.shutdown()
    # تنظيف مخلفات الاختبار: إعادة حالة المصنع
    for _f in ("shares.json", "config.json", "server.pid"):
        try:
            os.remove(os.path.join(DATA, _f))
        except OSError:
            pass
    for _d in ("previews", "uploads"):
        _dd = os.path.join(DATA, _d)
        try:
            for _f in os.listdir(_dd):
                if _f.endswith((".jpg", ".bin")):
                    os.remove(os.path.join(_dd, _f))
        except OSError:
            pass
    print(f"\nالنتيجة: {len(PASS)} ناجح / {len(FAIL)} فاشل")
    if FAIL:
        print("الفاشلة: " + ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
