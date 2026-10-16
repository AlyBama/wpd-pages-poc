FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py analyzer.py images.py config.py store.py ./
COPY static ./static
# البيانات (المشاركات/المعاينات/الإعداد) تعيش في مجلد دائم عبر WPD_DATA_DIR
# المنفذ الافتراضي 7860 (معيار Hugging Face) — أي متغير PORT من المنصة
# يغلب عليه تلقائيًا وقت التشغيل، فيعمل نفس الملف على أي منصة.
ENV WPD_DATA_DIR=/data PORT=7860
EXPOSE 7860
CMD ["python3", "server.py"]
