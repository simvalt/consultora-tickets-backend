FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN useradd -r -u 10001 api && chown -R api /srv
USER api
EXPOSE 8000
# --proxy-headers: respeta X-Forwarded-* de nginx/Cloudflare (IP real y esquema https).
# FORWARDED_ALLOW_IPS limita qué proxies son de confianza (por defecto "*", restringir en producción).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=${FORWARDED_ALLOW_IPS:-*}"]
