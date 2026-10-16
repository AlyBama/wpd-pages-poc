/* واجهة طبيب معاينة واتساب */
const $ = (id) => document.getElementById(id);
const state = { data: null, selected: null, uploadId: "", token: "", shareUrl: "", previewUrl: "" };

function setStatus(el, msg, cls) {
  el.textContent = msg || "";
  el.className = "status" + (cls ? " " + cls : "");
}

async function api(path, body) {
  const r = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const txt = await r.text();
  let obj;
  try { obj = JSON.parse(txt); }
  catch { throw new Error("استجابة غير صالحة من الخادم (HTTP " + r.status + ")"); }
  if (!r.ok || obj.ok === false) throw new Error(obj.error || ("خطأ HTTP " + r.status));
  return obj;
}

function ratio(w, h) {
  if (!w || !h) return "—";
  const g = (a, b) => (b ? g(b, a % b) : a);
  const d = g(w, h) || 1;
  return w / h >= 1 ? (w / h).toFixed(2) + ":1" : "1:" + (h / w).toFixed(2);
}

function thumbMeta(im) {
  const dim = im.ok ? `${im.width}×${im.height} • ${ratio(im.width, im.height)}` : "فشل التحميل";
  const src = { og: "og:image", twitter: "تويتر", icon: "أيقونة", img: "صورة", structured: "منظمة", poster: "ملصق" }[im.source] || im.source || "";
  return `${src}\n${dim}`;
}

/* الإعداد العام */
async function loadBase() {
  try {
    const c = await api("/api/config");
    $("baseUrl").value = c.public_base_url || "";
    $("baseState").textContent = c.public_base_url
      ? (c.is_public ? "✔ عنوان عام صالح (HTTPS)." : "⚠ هذا العنوان ليس HTTPS عامًا؛ المشاركة الخارجية لن تعمل.")
      : "⚠ لا يوجد عنوان عام بعد؛ روابط المشاركة محلية فقط.";
  } catch (e) { $("baseState").textContent = "تعذّر قراءة الإعداد: " + e.message; }
}
$("saveBase").onclick = async () => {
  try {
    const c = await api("/api/config", { public_base_url: $("baseUrl").value.trim() });
    $("baseState").textContent = c.is_public ? "✔ تم الحفظ؛ العنوان عام وصالح." : "⚠ تم الحفظ لكنه ليس HTTPS عامًا.";
  } catch (e) { $("baseState").textContent = "خطأ: " + e.message; }
};

/* الفحص */
$("scan").onclick = async () => {
  const btn = $("scan");
  const url = $("url").value.trim();
  if (!url) { setStatus($("status"), "أدخل رابطًا أولًا", "err"); return; }
  btn.disabled = true;
  setStatus($("status"), "جارٍ الفحص…", "");
  ["pageCard", "galCard", "modeCard", "shareCard"].forEach((id) => $(id).classList.add("hidden"));
  $("gallery").innerHTML = ""; $("defBox").innerHTML = ""; $("ogOut").textContent = "";
  try {
    const d = await api("/api/analyze", { url });
    state.data = d;
    renderResult(d);
    setStatus($("status"), `اكتمل الفحص: ${d.counts.valid} صالحة من ${d.counts.candidates}`, "ok");
  } catch (e) {
    setStatus($("status"), "فشل الفحص: " + e.message + " — يمكنك رفع صورة يدويًا من القسم 4 بالأسفل.", "err");
    state.data = null; state.selected = null;
    $("modeCard").classList.remove("hidden"); // الرفع اليدوي متاح رغم فشل الفحص
  } finally {
    btn.disabled = false; // لا يتجمد الزر أبدًا
  }
};

function renderResult(d) {
  $("pageCard").classList.remove("hidden");
  $("pTitle").textContent = d.title || "—";
  $("pDesc").textContent = d.description || "—";
  $("pFinal").textContent = d.final_url || d.page_url;
  $("defNote").textContent = d.default_note || "";
  const box = $("defBox");
  box.innerHTML = "";
  if (d.default) {
    const img = document.createElement("img");
    img.src = d.default.url; img.alt = "الصورة الافتراضية";
    box.appendChild(img);
    state.selected = d.default.url;
  } else {
    box.innerHTML = '<p class="hint">لا توجد صورة افتراضية صالحة.</p>';
    state.selected = null;
  }
  const g = $("gallery");
  g.innerHTML = "";
  const alts = d.images.filter((im) => !d.default || im.url !== d.default.url);
  $("galCount").textContent = `(${d.images.filter((i) => i.ok).length} صالحة)`;
  if (!alts.length) g.innerHTML = '<p class="hint">لا توجد صور بديلة.</p>';
  for (const im of alts) {
    const b = document.createElement("button");
    b.className = "thumb" + (im.ok ? "" : " bad");
    b.disabled = !im.ok;
    b.title = im.ok ? im.url : (im.url + "\n" + (im.reason || ""));
    const t = document.createElement("img");
    t.loading = "lazy"; t.src = im.url; t.alt = "";
    t.onerror = () => { b.classList.add("bad"); b.disabled = true; };
    const m = document.createElement("div");
    m.className = "meta"; m.innerText = thumbMeta(im);
    b.appendChild(t); b.appendChild(m);
    if (im.ok) b.onclick = () => {
      state.selected = im.url; state.uploadId = "";
      $("upState").textContent = "";
      [...g.children].forEach((x) => x.classList.remove("sel"));
      b.classList.add("sel");
      const note = d.default && im.url !== d.default.url ? " (اختيار بديل)" : "";
      setStatus($("status"), "تم اختيار صورة" + note, "ok");
      // تحديث الصورة الافتراضية المعروضة لتعكس الاختيار
      box.innerHTML = "";
      const c = document.createElement("img"); c.src = im.url; box.appendChild(c);
    };
    g.appendChild(b);
  }
  if (d.images.some((i) => i.ok)) $("galCard").classList.remove("hidden");
  if (state.selected) $("modeCard").classList.remove("hidden");
  renderDiag(d);
}

function renderDiag(d) {
  const rows = d.images.map((im, i) =>
    `<tr><td>${i + 1}</td><td dir="ltr">${im.url.replace(/</g, "&lt;").slice(0, 90)}</td>` +
    `<td>${im.ok ? "صالحة" : "مرفوضة"}</td><td>${im.ok ? im.width + "×" + im.height : (im.reason || "").replace(/</g, "&lt;")}</td>` +
    `<td>${im.ok ? im.score : "—"}</td></tr>`).join("");
  $("diagBody").innerHTML =
    `<p class="hint">المرشحون: ${d.counts.candidates} • صالحة: ${d.counts.valid} • فاشلة: ${d.counts.failed}</p>` +
    `<table class="diag"><tr><th>#</th><th>الرابط</th><th>الحالة</th><th>الأبعاد/السبب</th><th>الدرجة</th></tr>${rows}</table>`;
}
$("diagToggle").onclick = () => {
  const d = $("diag");
  d.classList.toggle("hidden");
  $("diagToggle").textContent = d.classList.contains("hidden") ? "إظهار" : "إخفاء";
};

/* الرفع اليدوي — يتجاوز حجب الموقع تمامًا */
$("upBtn").onclick = async () => {
  const f = ($("upFile").files || [])[0];
  if (!f) { $("upState").textContent = "اختر ملف صورة أولًا."; return; }
  if (f.size > 8 * 1024 * 1024) { $("upState").textContent = "الملف أكبر من 8MB."; return; }
  $("upState").textContent = "جارٍ الرفع…";
  try {
    const fd = new FormData();
    fd.append("image", f, f.name || "image");
    const r = await fetch("/api/upload", { method: "POST", body: fd });
    const txt = await r.text();
    let o;
    try { o = JSON.parse(txt); }
    catch { throw new Error("استجابة غير صالحة (HTTP " + r.status + ")"); }
    if (!r.ok || o.ok === false) throw new Error(o.error || ("خطأ HTTP " + r.status));
    state.uploadId = o.upload_id;
    state.selected = "upload:" + o.upload_id;
    $("upState").textContent = `✔ تم الرفع: ${o.width}×${o.height} — ستُستخدم هذه الصورة عند التجهيز.`;
    setStatus($("genState"), "", "");
  } catch (e) { $("upState").textContent = "فشل الرفع: " + e.message; }
};

/* التجهيز */
$("gen").onclick = async () => {
  const btn = $("gen");
  if (!state.selected && !state.uploadId) { setStatus($("genState"), "اختر صورة أو ارفع واحدة أولًا", "err"); return; }
  const mode = (document.querySelector('input[name=mode]:checked') || {}).value || "cover";
  const pageUrl = state.data ? state.data.final_url : $("url").value.trim();
  btn.disabled = true;
  setStatus($("genState"), "جارٍ تجهيز المعاينة 1200×630…", "");
  try {
    const r = await api("/api/generate", {
      image_url: state.uploadId ? "" : state.selected,
      upload_id: state.uploadId || "",
      page_url: pageUrl,
      title: state.data ? state.data.title : pageUrl,
      description: state.data ? state.data.description : "",
      mode,
    });
    state.token = r.token; state.shareUrl = r.share_url; state.previewUrl = r.preview_url;
    $("finalPrev").innerHTML = `<img src="${r.preview_url}" alt="المعاينة النهائية">`;
    $("shareCard").classList.remove("hidden");
    $("shareUrl").value = r.share_url;
    $("shareWarn").textContent = r.warning || "";
    setStatus($("genState"), `تم إنشاء معاينة ${r.width}×${r.height}`, "ok");
  } catch (e) {
    setStatus($("genState"), "فشل التجهيز: " + e.message, "err");
  } finally { btn.disabled = false; }
};

$("copyBtn").onclick = async () => {
  try { await navigator.clipboard.writeText(state.shareUrl); $("shareWarn").textContent = "تم النسخ ✔"; }
  catch { $("shareUrl").select(); document.execCommand("copy"); }
};
$("openBtn").onclick = () => { if (state.shareUrl) open(state.shareUrl, "_blank"); };
$("testBtn").onclick = async () => {
  // فحص من جهة الخادم لما يراه الروبوت بالضبط (يتجاوز قيود CORS في المتصفح)
  if (!state.token) return;
  $("ogOut").textContent = "جارٍ فحص ما يراه الروبوت…";
  try {
    const c = await api("/api/sharecheck/" + state.token);
    const lines = ["== ما يراه روبوت المعاينة =="];
    for (const k of ["og:title", "og:description", "og:type", "og:url", "og:image", "og:image:width", "og:image:height"]) {
      const v = (c.og && c.og[k]) || "";
      lines.push((v ? "✔ " : "✘ مفقود: ") + k + (v ? " = " + v : ""));
    }
    const p = c.preview || {};
    lines.push(`الصورة: ${p.exists ? `موجودة (${p.width}×${p.height} — ${Math.round((p.bytes || 0) / 1024)}KB)` : "مفقودة!"}`);
    lines.push("التحويل عند الفتح في المتصفح ← " + (c.redirect_target || "؟"));
    lines.push("العنوان المستخدم: " + (c.base_used || ""));
    lines.push(c.is_public ? "✔ العنوان عام (HTTPS)." : "⚠ العنوان محلي — واتساب لن يصل إليه. اضبط عنوان HTTPS عامًا.");
    if (c.base_mismatch) {
      lines.push("⚠ تحذير: هذا الرابط وُلد بعنوان قديم (" + (c.generated_with_base || "?") + ") — أعد التوليد بالعنوان الحالي وإلا بقيت صورته ميتة.");
    }
    $("ogOut").textContent = lines.join("\n");
  } catch (e) { $("ogOut").textContent = "فشل الاختبار: " + e.message; }
};

loadBase();
