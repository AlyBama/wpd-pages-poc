"""إثبات صلاحية النشر الاستاتيكي (PoC) — يعمل على نسخة wpd-pages-poc فقط.

يغطي: إعداد Pages، الرفع، توليد رابطين، النشر (ملفات+tags+JPEG+commit)،
وحقول sharecheck الاستاتيكية. لا يمس المشروع المستقر إطلاقًا.
"""
import io
import json
import os
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
PORT = 8082
APP = f"http://127.0.0.1:{PORT}"
STABLE_FIX = "/storage/emulated/0/b/whatsapp-preview-doctor-v2/data/.test_fixtures"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(APP + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except ValueError:
            return e.code, None


def start():
    env = dict(os.environ)
    env["WPD_PORT"] = str(PORT)
    env["WPD_ALLOW_PRIVATE"] = "1"
    env.pop("PORT", None)
    p = subprocess.Popen([sys.executable, os.path.join(BASE, "server.py")],
                         cwd=BASE, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            st, o = api("GET", "/api/health")
            if st == 200 and o.get("ok"):
                return p
        except OSError:
            pass
        time.sleep(0.5)
    p.terminate()
    raise RuntimeError("poc server did not start")


def upload_png(path):
    with open(path, "rb") as f:
        payload = f.read()
    bnd = "POCBOUND"
    body = (b"--" + bnd.encode() + b"\r\n"
            b'Content-Disposition: form-data; name="image"; filename="w.jpg"\r\n'
            b"Content-Type: image/jpeg\r\n\r\n" + payload +
            b"\r\n--" + bnd.encode() + b"--\r\n")
    req = urllib.request.Request(APP + "/api/upload", data=body, method="POST",
                                 headers={"Content-Type": "multipart/form-data; boundary=" + bnd})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode())


def main():
    app = start()
    # 1) إعداد عنوان النشر (قيمة تجريبية محلية الصيغة لكن https عام الشكل)
    pages = "https://poc-test.github.io/wppoc"
    st, c = api("POST", "/api/config", {"pages_base_url": pages})
    check("poc-config-pages", st == 200 and c.get("pages_base_url") == pages
          and c.get("pages_is_public") is True, f"{st} {c}")
    # الحفاظ على القيمة عند حفظ العنوان العام وحده
    st, c2 = api("POST", "/api/config", {"public_base_url": ""})
    check("poc-config-preserve", st == 200 and c2.get("pages_base_url") == pages, f"{st} {c2}")
    try:
        # 2) رفع صورة حقيقية (من تجهيزات المستقر — قراءة فقط) وتوليد رابطين
        tokens = []
        for mode in ("cover", "blur"):
            ust, uo = upload_png(os.path.join(STABLE_FIX, "wide.jpg"))
            check(f"poc-upload-{mode}", ust == 200 and uo.get("ok"), f"{ust} {uo}")
            st, g = api("POST", "/api/generate", {
                "upload_id": uo["upload_id"], "page_url": "https://example.com/a",
                "title": "عنوان PoC", "description": "وصف PoC", "mode": mode})
            check(f"poc-generate-{mode}", st == 200 and g.get("ok") and g.get("width") == 1200,
                  f"{st} {(g or {}).get('error')}")
            tokens.append(g["token"])

        # 3) النشر: ملفات + وسوم + JPEG + commit (بدون remote بعد → pushed:false)
        for i, tok in enumerate(tokens):
            st, pb = api("POST", "/api/publish", {"token": tok})
            check(f"poc-publish-{i}", st == 200 and pb.get("ok")
                  and pb.get("static_url") == f"{pages}/s/{tok}.html"
                  and pb.get("committed") is True, f"{st} {pb}")
            check(f"poc-publish-noremote-{i}", pb.get("pushed") is False, pb)
            hp = os.path.join(BASE, "s", tok + ".html")
            jp = os.path.join(BASE, "p", tok + ".jpg")
            check(f"poc-files-{i}", os.path.isfile(hp) and os.path.isfile(jp))
            html = open(hp, encoding="utf-8").read()
            tags = ["og:title", "og:description", "og:type", "og:url", "og:image",
                    "og:image:width", "og:image:height"]
            check(f"poc-statictags-{i}", all(f'property="{t}"' in html for t in tags)
                  and f"{pages}/p/{tok}.jpg" in html and f"{pages}/s/{tok}.html" in html)
            from PIL import Image as _PI
            im = _PI.open(jp)
            im.load()
            check(f"poc-staticjpeg-{i}", im.size == (1200, 630), im.size)
            with open(os.path.join(BASE, "data", "previews", tok + ".jpg"), "rb") as f:
                disk = f.read()
            check(f"poc-jpeg-identical-{i}", open(jp, "rb").read() == disk)
            st, sc = api("GET", "/api/sharecheck/" + tok)
            check(f"poc-sharecheck-static-{i}", st == 200
                  and sc.get("static_url") == f"{pages}/s/{tok}.html"
                  and sc.get("static_file_exists") is True, f"{st} {sc}")
        check("poc-nojekyll", os.path.isfile(os.path.join(BASE, ".nojekyll")))

        # 4) commits النشر موجودة في السجل
        _genv = dict(os.environ)
        _genv["GIT_CONFIG_COUNT"] = "1"
        _genv["GIT_CONFIG_KEY_0"] = "safe.directory"
        _genv["GIT_CONFIG_VALUE_0"] = BASE
        r = subprocess.run(["git", "-C", BASE, "log", "--oneline", "--grep=publish"],
                           capture_output=True, text=True, timeout=30, env=_genv)
        check("poc-commits", r.returncode == 0 and r.stdout.count("publish") >= 2, r.stdout.strip())
    finally:
        try:
            app.terminate()
            app.wait(timeout=8)
        except Exception:
            pass

    print(f"\nالنتيجة: {len(PASS)} ناجح / {len(FAIL)} فاشل")
    if FAIL:
        print("الفاشلة: " + ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
