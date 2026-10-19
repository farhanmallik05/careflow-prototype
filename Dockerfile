FROM python:3.12.12-slim@sha256:f3fa41d74a768c2fce8016b98c191ae8c1bacd8f1152870a3f9f87d350920b7c

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN python -m pip install --no-cache-dir --requirement /app/requirements.lock \
    && addgroup --system careflow \
    && adduser --system --ingroup careflow careflow \
    && mkdir -p /data \
    && chown -R careflow:careflow /data

COPY careflow /app/careflow

WORKDIR /data
VOLUME ["/data"]
EXPOSE 8000
USER careflow

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; response=urllib.request.urlopen('http://127.0.0.1:8000/', timeout=2); body=response.read(); raise SystemExit(0 if response.status == 200 and b'Demo Lab Service' in body else 1)"]

CMD ["sh", "-c", "if [ ! -f /data/careflow.db ]; then python -m careflow.seed --db /data/careflow.db; fi; exec python -m uvicorn careflow.api:app --host 0.0.0.0 --port 8000"]
