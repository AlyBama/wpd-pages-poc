#!/data/data/com.termux/files/usr/bin/bash
# تشغيل طبيب معاينة واتساب v2 (يفحص المتطلبات ويعطي رسائل عربية واضحة)
set -u
PROJ="/storage/emulated/0/b/wpd-pages-poc"
PORT="${WPD_PORT:-8082}"

# 1) مجلد المشروع (يفشل غالبًا إذا لم تُمنح صلاحية التخزين)
if [ ! -d "$PROJ" ]; then
  echo "خطأ: مجلد المشروع غير موجود: $PROJ"
  echo "نفّذ أولًا: termux-setup-storage  ثم وافق على إذن التخزين من النظام"
  exit 1
fi
cd "$PROJ" || exit 1

# 2) بايثون
if ! command -v python3 >/dev/null 2>&1; then
  echo "خطأ: python3 غير مثبت."
  echo "نفّذ: pkg install -y python"
  exit 1
fi

# 3) Pillow (تُثبَّت تلقائيًا عند غيابها)
if ! python3 -c "import PIL" 2>/dev/null; then
  echo "مكتبة Pillow غير موجودة — جارٍ تثبيتها..."
  pip install pillow || { echo "فشل تثبيت Pillow. نفّذ يدويًا: pip install pillow"; exit 1; }
fi

# 4) المنفذ: هل يرد عليه خادم فعلًا؟ (الاتصال أدق من bind بسبب TIME_WAIT)
if python3 -c "import socket; socket.create_connection(('127.0.0.1', $PORT), timeout=3).close()" 2>/dev/null; then
  echo "خطأ: المنفذ $PORT مشغول (ربما خادم قديم يعمل)."
  echo "أوقف الخادم أولًا: bash $PROJ/stop.sh"
  echo "وإذا كان المشروع القديم (v1) يعمل على نفس المنفذ، أوقفه أيضًا."
  exit 1
fi

# 5) التشغيل في الخلفية
# PUBLIC_BASE_URL يُقرأ من البيئة أو من data/config.json (يُضبط من الواجهة)
export WPD_PORT="$PORT"
if ! command -v nohup >/dev/null 2>&1; then
  echo "خطأ: nohup غير موجود. نفّذ: pkg install -y coreutils"
  exit 1
fi
nohup python3 server.py > data/server.log 2>&1 &
PID=$!
echo "$PID" > data/server.pid
sleep 2

# 6) التحقق أنه يعمل فعلًا (بدون curl — قد لا يكون مثبتًا)
if kill -0 "$PID" 2>/dev/null; then
  if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$PORT/api/health', timeout=10).read()" 2>/dev/null; then
    echo "يعمل الآن على: http://127.0.0.1:$PORT  (رقم العملية $PID)"
    echo "افتحه في المتصفح أو نفّذ: termux-open-url http://127.0.0.1:$PORT"
  else
    echo "العملية بدأت (pid $PID) لكنها لا ترد بعد — انتظر ثوانٍ ثم أعد فتح الرابط."
    echo "لرؤية السجل: tail -20 $PROJ/data/server.log"
  fi
else
  echo "فشل التشغيل. آخر سطور السجل:"
  tail -20 data/server.log 2>/dev/null
  exit 1
fi
