"""الخادم: http.server من المكتبة القياسية + Pillow فقط. المنفذ 8081."""
import json
import mimetypes
import os
import re
import secrets
import subprocess
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import analyzer
import config
import images
import sharekit
import store

try:
    from PIL import Image as _PILImage
except ImportError:
    _PILImage = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
PREVIEW_DIR = os.path.join(config.DATA_DIR, "previews")
UPLOAD_DIR = os.path.join(config.DATA_DIR, "uploads")
VERSION = sharekit.VERSION
BODY_LIMIT = 1_000_000
UPLOAD_MAX_BYTES = 8_000_000
UPLOAD_BODY_LIMIT = 9_500_000

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# النشر الاستاتيكي (PoC): ملفات تُدفع إلى GitHub Pages في جذر المستودع
STATIC_S_DIR = "s"
STATIC_P_DIR = "p"


def _write_static_files(repo_dir: str, token: str, record: dict,
                        pages_base: str, jpeg_bytes: bytes):
    """يكتب s/<token>.html وp/<token>.jpg و.nojekyll. يعيد (مسار HTML, مسار JPG)."""
    if not sharekit.valid_token(token):
        raise ValueError("رمز غير صالح")
    if not isinstance(record, dict):
        raise ValueError("سجل غير صالح")
    if not jpeg_bytes or jpeg_bytes[:2] != b"\xff\xd8":
        raise ValueError("صورة JPEG غير صالحة")
    pages_base = (pages_base or "").rstrip("/")
    if not pages_base.startswith("https://"):
        raise ValueError("رابط النشر يجب أن يكون https عامًا")
    s_path = os.path.join(repo_dir, STATIC_S_DIR, token + ".html")
    p_path = os.path.join(repo_dir, STATIC_P_DIR, token + ".jpg")
    os.makedirs(os.path.dirname(s_path), exist_ok=True)
    os.makedirs(os.path.dirname(p_path), exist_ok=True)
    page = sharekit.render_share_html(
        title=record.get("title") or "مشاركة",
        description=record.get("description") or "",
        page_url=f"{pages_base}/s/{token}.html",
        preview_url=f"{pages_base}/p/{token}.jpg",
        target=record.get("original_url") or "")
    with open(s_path, "w", encoding="utf-8") as f:
        f.write(page)
    with open(p_path, "wb") as f:
        f.write(jpeg_bytes)
    nojek = os.path.join(repo_dir, ".nojekyll")
    if not os.path.exists(nojek):
        with open(nojek, "w", encoding="utf-8") as f:
            f.write("")
    return s_path, p_path


def _git_run(repo_dir: str, *args, timeout=30):
    env = dict(os.environ)
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "safe.directory"
    env["GIT_CONFIG_VALUE_0"] = repo_dir
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["git", "-C", repo_dir, "-c", "user.name=wpd",
         "-c", "user.email=wpd@localhost"] + list(args),
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, timeout=timeout)


def _git_commit(repo_dir: str, paths, message: str) -> bool:
    """يثبّت المسارات محليًا. يعيد True إذا أُنشئ commit جديد."""
    r = _git_run(repo_dir, "add", "--", *paths)
    if r.returncode != 0:
        raise RuntimeError("git add فشل")
    r = _git_run(repo_dir, "commit", "-m", message)
    out = r.stdout.decode("utf-8", "replace")
    if r.returncode == 0:
        return True
    if "nothing to commit" in out:
        return False
    raise RuntimeError("git commit فشل: " + out.strip().splitlines()[-1][:120])


def _git_push(repo_dir: str, timeout=60):
    """يحاول الدفع دون أي مطالبة تفاعلية. يعيد (نجح؟, ملاحظة)."""
    try:
        r = _git_run(repo_dir, "push", "-u", "origin", "HEAD:main", timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "انتهت مهلة الدفع (60s)"
    out = r.stdout.decode("utf-8", "replace")
    if r.returncode == 0:
        return True, ""
    low = out.lower()
    if "could not read username" in low or "authentication failed" in low or "403" in low:
        return False, ("الدفع يحتاج صلاحية: نفّذ git push مرة واحدة يدويًا "
                       "وأدخل رمز PAT، أو فعّل credential.helper")
    if "no remote" in low or "does not appear to be a git" in low:
        return False, "لا يوجد مستودع بعيد — اربط origin أولًا"
    if "rejected" in low or "fetch first" in low:
        return False, "الفرع البعيد متقدم — اسحب (pull) أولًا ثم أعد النشر"
    last = [ln for ln in out.strip().splitlines() if ln.strip()]
    return False, (last[-1][:150] if last else "فشل الدفع")


def _json(handler, obj, status=200, head_only=False):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if not head_only:
        handler.wfile.write(body)


def _err(handler, message, status=400, head_only=False, **extra):
    payload = {"ok": False, "error": message}
    payload.update(extra)
    _json(handler, payload, status=status, head_only=head_only)


def _read_json(handler):
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        length = 0
    if length <= 0:
        return {}
    if length > BODY_LIMIT:
        raise ValueError("حجم الطلب كبير جدًا")
    raw = handler.rfile.read(length)
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("طلب JSON غير صالح")
    return obj if isinstance(obj, dict) else {}


def _strip_text(s, limit):
    s = re.sub(r"<[^>]*>", " ", str(s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


class Handler(BaseHTTPRequestHandler):
    server_version = "WPD/2.0"
    protocol_version = "HTTP/1.1"  # اتصالات ثابتة لروبوتات المعاينة

    def log_message(self, fmt, *args):  # سجل مختصر
        pass

    # ---------- أدوات ----------
    def _serve_static(self, rel, head_only=False):
        safe = os.path.normpath(rel).lstrip("/")
        full = os.path.join(STATIC_DIR, safe)
        if not full.startswith(STATIC_DIR + os.sep) and full != STATIC_DIR:
            self.send_error(403)
            return
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if not os.path.isfile(full):
            self.send_error(404)
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if full.endswith(".html"):
            ctype = "text/html; charset=utf-8"
        try:
            with open(full, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    # ---------- GET / HEAD ----------
    def do_GET(self):
        return self._route(head_only=False)

    def do_HEAD(self):
        # روبوتات المعاينة (واتساب وغيرها) تبدأ غالبًا بطلب HEAD
        return self._route(head_only=True)

    def _route(self, head_only):
        parts = urllib.parse.urlsplit(self.path)
        path = parts.path or "/"
        try:
            if path in ("/", "/index.html"):
                return self._serve_static("index.html", head_only)
            if path.startswith("/static/"):
                return self._serve_static(path[len("/static/"):], head_only)
            if path == "/robots.txt":
                body = b"User-agent: *\nAllow: /share/\nAllow: /preview/\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if not head_only:
                    self.wfile.write(body)
                return
            if path == "/api/health":
                cfg = config.load_config()
                return _json(self, {"ok": True, "version": VERSION,
                                    "port": config.PORT,
                                    "public_base_url": cfg["public_base_url"],
                                    "is_public": cfg["is_public"]},
                             head_only=head_only)
            if path == "/api/config":
                return _json(self, {"ok": True, **config.load_config()},
                             head_only=head_only)
            if path.startswith("/api/sharecheck/"):
                token = path[len("/api/sharecheck/"):].strip("/")
                return self._serve_sharecheck(token, head_only)
            if path.startswith("/share/"):
                token = path[len("/share/"):].strip("/")
                return self._serve_share(token, head_only)
            if path.startswith("/preview/") and path.endswith(".jpg"):
                token = path[len("/preview/"):-len(".jpg")].strip("/")
                return self._serve_preview(token, head_only)
            if path.startswith("/api/"):
                return _err(self, "مسار غير معروف", status=404,
                            head_only=head_only)
            return self.send_error(404)
        except BrokenPipeError:
            pass
        except Exception as e:
            if path.startswith("/api/"):
                _err(self, f"خطأ داخلي: {e}", status=500,
                     head_only=head_only)
            else:
                self.send_error(500)

    def _serve_sharecheck(self, token, head_only=False):
        """فحص ما يراه الروبوت تمامًا: وسوم OG + الصورة + التحويل، بلا شبكة."""
        if not TOKEN_RE.match(token or ""):
            return _err(self, "رمز غير صالح", status=404,
                        head_only=head_only)
        rec = store.get_share(token)
        if not rec:
            return _err(self, "المشاركة غير موجودة", status=404,
                        head_only=head_only)
        base, is_public = config.effective_base(self.headers.get("Host", ""))
        data = sharekit.assemble_sharecheck(
            record=rec, token=token, base=base, is_public=is_public,
            preview_dir=PREVIEW_DIR)
        data["static_url"] = rec.get("static_url") or ""
        data["static_file_exists"] = os.path.isfile(
            os.path.join(BASE_DIR, STATIC_S_DIR, token + ".html"))
        return _json(self, data, head_only=head_only)

    def _handle_publish(self, obj):
        token = str(obj.get("token") or "").strip()
        if not sharekit.valid_token(token):
            return _err(self, "رمز غير صالح", status=400)
        rec = store.get_share(token)
        if not rec:
            return _err(self, "المشاركة غير موجودة", status=404)
        cfg = config.load_config()
        pages = cfg["pages_base_url"]
        if not pages or not cfg["pages_is_public"]:
            return _err(self, "اضبط رابط النشر الدائم (https عام) أولًا", status=400)
        jpeg = sharekit.read_preview(PREVIEW_DIR, token)
        if not jpeg:
            return _err(self, "صورة المعاينة غير موجودة — أعد التوليد", status=410)
        try:
            _write_static_files(BASE_DIR, token, rec, pages, jpeg)
            committed = _git_commit(BASE_DIR, ["s", "p", ".nojekyll"],
                                    "publish " + token)
        except (ValueError, RuntimeError, OSError) as e:
            return _err(self, f"فشل النشر المحلي: {e}", status=500)
        static_url = f"{pages}/s/{token}.html"
        store.update_share(token, {"static_url": static_url,
                                   "static_published_at": int(time.time()),
                                   "static_committed": bool(committed)})
        pushed, push_note = _git_push(BASE_DIR)
        return _json(self, {"ok": True, "token": token, "static_url": static_url,
                            "static_image_url": f"{pages}/p/{token}.jpg",
                            "committed": bool(committed), "pushed": pushed,
                            "push_note": push_note,
                            "next": ("نُشر ودُفع تلقائيًا — انتظر دقيقة ثم افتح الرابط"
                                     if pushed else
                                     "جُهز محليًا — نفّذ git push ثم افتح الرابط")})

    def _serve_preview(self, token, head_only=False):
        body = sharekit.read_preview(PREVIEW_DIR, token)
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _serve_share(self, token, head_only=False):
        if not TOKEN_RE.match(token or ""):
            self.send_error(404)
            return
        rec = store.get_share(token)
        if not rec:
            self.send_error(404)
            return
        base, _ = config.effective_base(self.headers.get("Host", ""))
        preview_url = f"{base}/preview/{token}.jpg"
        page_url = f"{base}/share/{token}"
        title = rec.get("title") or "مشاركة"
        desc = rec.get("description") or ""
        target = rec.get("original_url") or ""
        page = sharekit.render_share_html(
            title=title, description=desc, page_url=page_url,
            preview_url=preview_url, target=target)
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    # ---------- POST ----------
    def do_POST(self):
        parts = urllib.parse.urlsplit(self.path)
        path = parts.path or "/"
        try:
            if path == "/api/config":
                try:
                    obj = _read_json(self)
                except ValueError as e:
                    return _err(self, str(e), status=400)
                cfg = config.save_config(str(obj.get("public_base_url") or ""),
                                           obj.get("pages_base_url"))
                return _json(self, {"ok": True, **cfg})
            if path == "/api/analyze":
                try:
                    obj = _read_json(self)
                except ValueError as e:
                    return _err(self, str(e), status=400)
                url = str(obj.get("url") or "").strip()
                if not url:
                    return _err(self, "أدخل رابطًا أولًا", status=400)
                try:
                    data = analyzer.analyze_url(url)
                except analyzer.UrlBlockedError as e:
                    return _err(self, str(e), status=400, kind="blocked")
                except analyzer.FetchError as e:
                    return _err(self, str(e), status=502, kind="fetch")
                except Exception as e:
                    return _err(self, f"فشل الفحص: {e}", status=500)
                return _json(self, data)
            if path == "/api/generate":
                try:
                    obj = _read_json(self)
                except ValueError as e:
                    return _err(self, str(e), status=400)
                return self._handle_generate(obj)
            if path == "/api/upload":
                return self._handle_upload()
            if path == "/api/publish":
                try:
                    obj = _read_json(self)
                except ValueError as e:
                    return _err(self, str(e), status=400)
                return self._handle_publish(obj)
            if path.startswith("/api/"):
                return _err(self, "مسار غير معروف", status=404)
            return _err(self, "مسار غير معروف", status=404)
        except BrokenPipeError:
            pass
        except Exception as e:
            _err(self, f"خطأ داخلي: {e}", status=500)

    def _parse_upload(self):
        """يستخرج ملف image من multipart/form-data (محلل يدوي بسيط وآمن)."""
        ctype = (self.headers.get("Content-Type") or "").strip()
        m = re.match(r'^multipart/form-data;\s*boundary=(.+)$', ctype)
        if not m:
            raise ValueError("نوع الطلب يجب أن يكون multipart/form-data")
        boundary = m.group(1).strip().strip('"')
        if not boundary or len(boundary) > 200:
            raise ValueError("حد فاصل غير صالح")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > UPLOAD_BODY_LIMIT:
            raise ValueError("حجم الرفع غير صالح (الحد 8MB)")
        raw = self.rfile.read(length)
        sep = ("--" + boundary).encode("latin-1")
        for part in raw.split(sep)[1:]:
            if part.startswith(b"--"):
                continue  # حد النهاية
            if part.startswith(b"\r\n"):
                part = part[4:]
            elif part.startswith(b"\n"):
                part = part[1:]
            head, gap, content = part.partition(b"\r\n\r\n")
            if not gap:
                continue
            if content.endswith(b"\r\n"):
                content = content[:-2]
            h = head.decode("latin-1", "replace")
            if 'name="image"' not in h:
                continue
            fn = re.search(r'filename="([^"]{0,200})"', h)
            fname = os.path.basename(fn.group(1)) if fn else "image"
            if len(content) == 0 or len(content) > UPLOAD_MAX_BYTES:
                raise ValueError("حجم الصورة غير صالح (الحد 8MB)")
            return content, (fname or "image")[:100]
        raise ValueError('لم يتم إرفاق ملف باسم "image"')

    def _handle_upload(self):
        if _PILImage is None:
            return _err(self, "مكتبة الصور غير مثبتة (Pillow)", status=500)
        try:
            content, fname = self._parse_upload()
        except ValueError as e:
            return _err(self, str(e), status=400)
        except Exception:
            return _err(self, "فشل قراءة الملف المرفوع", status=400)
        try:
            import io as _io
            with _PILImage.open(_io.BytesIO(content)) as im:
                im.load()
                w, h = im.size
            if w <= 0 or h <= 0 or w * h > 40_000_000:
                raise ValueError("bad dims")
        except Exception:
            return _err(self, "الملف المرفوع ليس صورة صالحة", status=422)
        file_id = secrets.token_urlsafe(16)
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        try:
            with open(os.path.join(UPLOAD_DIR, file_id + ".bin"), "wb") as f:
                f.write(content)
        except OSError:
            return _err(self, "فشل حفظ الملف", status=500)
        return _json(self, {"ok": True, "upload_id": file_id,
                            "width": w, "height": h,
                            "bytes": len(content), "name": fname})

    def _handle_generate(self, obj):
        image_url = str(obj.get("image_url") or "").strip()
        upload_id = str(obj.get("upload_id") or "").strip()
        page_url = str(obj.get("page_url") or "").strip()
        mode = str(obj.get("mode") or "cover").strip()
        title = _strip_text(obj.get("title"), 200)
        description = _strip_text(obj.get("description"), 500)
        if not image_url and not upload_id:
            return _err(self, "اختر صورة أولًا", status=400)
        if upload_id and not TOKEN_RE.match(upload_id):
            return _err(self, "معرّف الرفع غير صالح", status=400)
        if mode not in images.VALID_MODES:
            return _err(self, "طريقة العرض غير معروفة", status=400)
        if page_url:
            try:
                analyzer.validate_url(page_url)
            except analyzer.UrlBlockedError as e:
                return _err(self, f"رابط الصفحة: {e}", status=400)
        source_label = image_url
        if upload_id:
            # صورة مرفوعة يدويًا: لا تنزيل من الشبكة إطلاقًا
            full = os.path.join(UPLOAD_DIR, upload_id + ".bin")
            if not os.path.isfile(full):
                return _err(self, "الملف المرفوع غير موجود", status=410)
            try:
                with open(full, "rb") as f:
                    raw = f.read()
                source_label = "upload:" + upload_id
            except OSError:
                return _err(self, "تعذّر قراءة الملف المرفوع", status=500)
        else:
            # تنزيل الصورة المختارة (مرة واحدة) ثم تجهيزها
            try:
                res = analyzer.check_image(image_url, page_url=page_url or None)
            except Exception as e:
                return _err(self, f"تعذّر تنزيل الصورة: {e}", status=502)
            if not res.get("ok"):
                return _err(self, f"الصورة غير صالحة: {res.get('reason')}", status=422)
            try:
                raw, _, _, _ = analyzer.fetch_bytes(
                    image_url, analyzer.IMG_TIMEOUT, analyzer.IMG_MAX_BYTES,
                    "image/*,*/*;q=0.8")
            except (analyzer.FetchError, analyzer.UrlBlockedError) as e:
                return _err(self, f"تعذّر تنزيل الصورة: {e}", status=502)
        try:
            jpg = images.compose_preview(raw, mode)
        except ValueError as e:
            return _err(self, str(e), status=422)
        token = secrets.token_urlsafe(16)
        os.makedirs(PREVIEW_DIR, exist_ok=True)
        with open(os.path.join(PREVIEW_DIR, token + ".jpg"), "wb") as f:
            f.write(jpg)
        base, is_public = config.effective_base(self.headers.get("Host", ""))
        store.save_share(token, {
            "original_url": page_url or image_url or "upload",
            "title": title or page_url or "مشاركة",
            "description": description,
            "mode": mode,
            "source_image": source_label,
            "base_used": base,
        })
        payload = {"ok": True, "token": token,
                   "share_url": f"{base}/share/{token}",
                   "preview_url": f"{base}/preview/{token}.jpg",
                   "width": 1200, "height": 630, "is_public": is_public}
        if not is_public:
            payload["warning"] = ("لا يوجد عنوان HTTPS عام؛ رابط المشاركة يعمل "
                                  "محليًا فقط ولن تظهر معاينته في واتساب بعد. "
                                  "اضبط PUBLIC_BASE_URL عبر نفق HTTPS.")
        return _json(self, payload)


def run():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    addr = ("0.0.0.0", config.PORT)
    srv = ThreadingHTTPServer(addr, Handler)
    print(f"WhatsApp Preview Doctor v{VERSION} on port {config.PORT}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
