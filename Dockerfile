FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/data/botsocial.db HOST=0.0.0.0
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py personas_extra.py about.md ./
COPY static ./static
RUN mkdir -p /data
EXPOSE 8000
CMD ["sh", "-c", "mkdir -p /data && uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
