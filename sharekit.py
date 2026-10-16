"""منطق المعاينة الخالص: صفحة OG + ملف الصورة + تقرير الروبوت.

بلا أي اعتماديات خارج المكتبة القياسية (Pillow اختياري للأبعاد فقط)،
ليستخدمه كل من server.py (Termux) وapp.py (Hugging Face) بنفس السلوك تمامًا.
"""
import html as htmlmod
import io
import json
import os
import re

try:
    from PIL import Image as _PILImage
except ImportError:  # pragma: no cover
    _PILImage = None

VERSION = "2.1.0"

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

OG_KEYS = ("og:title", "og:description", "og:type", "og:url", "og:image",
           "og:image:width", "og:image:height")

BOT_VIEW_NOTE = "صفحة HTML بالوسوم مباشرة (بلا حاجة لـ JavaScript)"


def valid_token(token) -> bool:
    return bool(token) and bool(TOKEN_RE.match(token))


def read_preview(preview_dir: str, token: str):
    """يعيد بايتات JPEG أو None (رمز خاطئ أو ملف مفقود/تالف المسار)."""
    if not valid_token(token):
        return None
    full = os.path.join(preview_dir or "", token + ".jpg")
    try:
        if not os.path.isfile(full):
            return None
        with open(full, "rb") as f:
            return f.read()
    except OSError:
        return None


def preview_info(preview_dir: str, token: str) -> dict:
    """يصف ملف المعاينة: الوجود والحجم والأبعاد (0 عند غياب Pillow)."""
    info = {"exists": False, "content_type": "image/jpeg",
            "bytes": 0, "width": 0, "height": 0}
    data = read_preview(preview_dir, token)
    if data is None:
        return info
    info["exists"] = True
    info["bytes"] = len(data)
    if _PILImage is not None:
        try:
            with _PILImage.open(io.BytesIO(data)) as im:
                im.load()
                info["width"], info["height"] = im.size
        except Exception:
            pass
    return info


def render_share_html(*, title: str, description: str, page_url: str,
                      preview_url: str, target: str) -> str:
    """يبني صفحة المشاركة: وسوم OG للروبوت + تحويل للمتصفح."""
    e = htmlmod.escape
    title = title or "مشاركة"
    description = description or ""
    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(description)}">
<meta property="og:type" content="website">
<meta property="og:url" content="{e(page_url)}">
<meta property="og:image" content="{e(preview_url)}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{e(title)}">
<meta name="twitter:description" content="{e(description)}">
<meta name="twitter:image" content="{e(preview_url)}">
<meta http-equiv="refresh" content="1;url={e(target)}">
</head>
<body style="font-family:sans-serif;text-align:center;padding:40px">
<p>جارٍ نقلك إلى الصفحة الأصلية…</p>
<p><a href="{e(target)}">اضغط هنا إذا لم يتم نقلك تلقائيًا</a></p>
<img src="{e(preview_url)}" alt="" style="max-width:100%">
<script>setTimeout(function(){{location.href={json.dumps(target)}}},800);</script>
</body>
</html>"""


def assemble_sharecheck(*, record: dict, token: str, base: str,
                        is_public: bool, preview_dir: str) -> dict:
    """يجمّع ما يراه الروبوت: الوسوم + الصورة + التحويل + كشف العنوان القديم."""
    title = record.get("title") or "مشاركة"
    desc = record.get("description") or ""
    preview_url = f"{base}/preview/{token}.jpg"
    page_url = f"{base}/share/{token}"
    og = {"og:title": title, "og:description": desc,
          "og:type": "website", "og:url": page_url,
          "og:image": preview_url, "og:image:width": "1200",
          "og:image:height": "630"}
    generated = record.get("base_used") or ""
    return {"ok": True, "token": token, "og": og,
            "preview": preview_info(preview_dir, token),
            "redirect_target": record.get("original_url") or "",
            "base_used": base, "is_public": is_public,
            "generated_with_base": generated,
            "base_mismatch": bool(generated) and generated != base,
            "bot_view": BOT_VIEW_NOTE}


def format_sharecheck_report(sc: dict) -> str:
    """تقرير عربي من قاموس assemble_sharecheck (للواجهات)."""
    lines = ["== ما يراه روبوت المعاينة =="]
    og = (sc or {}).get("og") or {}
    for k in OG_KEYS:
        v = og.get(k) or ""
        lines.append((("✔ " + k + " = " + v) if v else ("✘ مفقود: " + k)))
    p = (sc or {}).get("preview") or {}
    if p.get("exists"):
        lines.append("الصورة: موجودة (%d×%d — %dKB)" % (
            p.get("width", 0), p.get("height", 0),
            round((p.get("bytes") or 0) / 1024)))
    else:
        lines.append("الصورة: مفقودة!")
    lines.append("التحويل عند الفتح في المتصفح ← " + ((sc or {}).get("redirect_target") or "؟"))
    lines.append("العنوان المستخدم: " + ((sc or {}).get("base_used") or ""))
    if (sc or {}).get("is_public"):
        lines.append("✔ العنوان عام (HTTPS).")
    else:
        lines.append("⚠ العنوان محلي — واتساب لن يصل إليه. اضبط عنوان HTTPS عامًا.")
    if (sc or {}).get("base_mismatch"):
        lines.append("⚠ تحذير: هذا الرابط وُلد بعنوان قديم (%s) — أعد التوليد بالعنوان الحالي وإلا بقيت صورته ميتة." % ((sc or {}).get("generated_with_base") or "?"))
    return "\n".join(lines)
