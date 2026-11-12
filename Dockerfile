# API only. The React app is a separate static site (see render.yaml).
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
ENV PORT=10000
# --no-access-log: the app writes its own structured request log (request id, user id, duration).
# One worker on purpose: analysis jobs and the concurrency limiter live in this process (see services/jobs.py).
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1 --no-access-log --proxy-headers --forwarded-allow-ips='*'"]
