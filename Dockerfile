# HybridP2P Messenger — röle sunucusu + web istemcisi.
# TLS'i önündeki Caddy yapar (docker-compose.yml); bu konteyner yalnızca iç
# ağda düz HTTP/WS dinler ve dışarıya port açmaz.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements-server.txt .
RUN pip install -r requirements-server.txt

# Yalnızca sunucunun ihtiyaç duyduğu dosyalar (masaüstü kodu imaja girmez)
COPY server.py crypto_utils.py ./
COPY server ./server
COPY static ./static
COPY deploy/gen_cert.py ./deploy/gen_cert.py

# Root olmayan kullanıcı; veritabanı /data volume'unda (dosyalar da SQLite içinde)
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin hybridp2p \
    && mkdir -p /data /certs \
    && chown hybridp2p:hybridp2p /data /certs
USER hybridp2p

ENV HYBRIDP2P_HOST=0.0.0.0 \
    HYBRIDP2P_PORT=8000 \
    HYBRIDP2P_DB_PATH=/data/relay_server.db \
    HYBRIDP2P_RELOAD=0

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["python", "server.py"]
