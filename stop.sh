#!/data/data/com.termux/files/usr/bin/bash
# إيقاف خادم v2 فقط (لا يمس المشروع القديم) — يعمل مهما كانت طريقة بدء الخادم
set -u
PROJ="/storage/emulated/0/b/wpd-pages-poc"
PORT="${WPD_PORT:-8082}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "خطأ: python3 غير مثبت. نفّذ: pkg install -y python"
  exit 1
fi
if [ ! -f "$PROJ/tools/stopserver.py" ]; then
  echo "خطأ: ملف tools/stopserver.py مفقود في $PROJ"
  exit 1
fi

RES=$(python3 "$PROJ/tools/stopserver.py" "$PROJ" "$PORT")
echo "$RES"
if echo "$RES" | grep -q PORT-FREE; then
  echo "المنفذ $PORT حر الآن."
  exit 0
fi

echo "المنفذ $PORT ما زال مشغولًا — الذي يشغله ليس خادم v2."
echo "الغالب أنه المشروع القديم (v1) أو برنامج آخر."
echo "اختر حلًا:"
echo "  1) أوقف البرنامج الآخر ثم أعد: bash $PROJ/run.sh"
echo "  2) أو شغّل v2 على منفذ مختلف: WPD_PORT=8082 bash $PROJ/run.sh"
echo "     ثم افتح: http://127.0.0.1:8082"
exit 1
