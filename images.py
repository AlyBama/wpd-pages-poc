"""تجهيز صورة المعاينة 1200×630 بثلاثة أوضاع: cover / contain / blur."""
import io

try:
    from PIL import Image, ImageFilter, ImageStat
except ImportError:  # pragma: no cover
    Image = None

TARGET_W, TARGET_H = 1200, 630
VALID_MODES = ("cover", "contain", "blur")
# سقف حجم الملف لتنزيل الزواحف عبر الأنفاق البطيئة (بتخفيض الجودة تدريجيًا)
MAX_PREVIEW_BYTES = 512_000
QUALITIES = (85, 70, 60, 50)


def _avg_color(img):
    small = img.convert("RGB").resize((16, 16))
    st = ImageStat.Stat(small)
    return tuple(int(c) for c in st.mean)


def compose_preview(raw: bytes, mode: str = "cover") -> bytes:
    if Image is None:
        raise ValueError("مكتبة الصور غير مثبتة (Pillow)")
    if mode not in VALID_MODES:
        raise ValueError("طريقة العرض غير معروفة")
    with Image.open(io.BytesIO(raw)) as im:
        img = im.convert("RGB")
    W, H = TARGET_W, TARGET_H

    if mode == "cover":
        scale = max(W / img.width, H / img.height)
        nw, nh = int(img.width * scale + 0.5), int(img.height * scale + 0.5)
        resized = img.resize((nw, nh), Image.LANCZOS)
        x = (nw - W) // 2
        y = (nh - H) // 2
        canvas = resized.crop((x, y, x + W, y + H))
    elif mode == "contain":
        bg = _avg_color(img)
        canvas = Image.new("RGB", (W, H), bg)
        scale = min(W / img.width, H / img.height)
        nw = max(1, int(img.width * scale))
        nh = max(1, int(img.height * scale))
        fitted = img.resize((nw, nh), Image.LANCZOS)
        canvas.paste(fitted, ((W - nw) // 2, (H - nh) // 2))
    else:  # blur
        scale = max(W / img.width, H / img.height)
        bg = img.resize((int(img.width * scale + 0.5),
                         int(img.height * scale + 0.5)), Image.LANCZOS)
        x = (bg.width - W) // 2
        y = (bg.height - H) // 2
        bg = bg.crop((x, y, x + W, y + H)).filter(ImageFilter.GaussianBlur(25))
        # تعتيم خفيف للتباين
        dark = Image.new("RGB", (W, H), (0, 0, 0))
        bg = Image.blend(bg, dark, 0.25)
        canvas = bg
        scale2 = min(W / img.width, H / img.height)
        # في وضع الخلفية المموهة نعرض الصورة بحجم مناسب (بحد أقصى 90%)
        scale2 = min(scale2, min(W * 0.92 / img.width, H * 0.92 / img.height))
        nw = max(1, int(img.width * scale2))
        nh = max(1, int(img.height * scale2))
        fg = img.resize((nw, nh), Image.LANCZOS)
        canvas.paste(fg, ((W - nw) // 2, (H - nh) // 2))

    buf = io.BytesIO()
    data = b""
    for q in QUALITIES:
        buf = io.BytesIO()
        canvas.save(buf, "JPEG", quality=q, optimize=True)
        data = buf.getvalue()
        if len(data) <= MAX_PREVIEW_BYTES:
            break
    return data
