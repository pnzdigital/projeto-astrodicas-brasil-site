# ⚠️ DEPRECATED: This monolithic Dockerfile is not used by compose.yaml.
# Use api/Dockerfile + portal-demo/Dockerfile instead (see compose.yaml).
# Kept for reference only.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl nginx \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY api/app ./app
COPY portal-demo/index.html portal-demo/storefront.html portal-demo/portal-config.js portal-demo/portal-config-ar.js /usr/share/nginx/html/
COPY portal-demo/sales.html /usr/share/nginx/html/sales.html
COPY lp-plano-lua /usr/share/nginx/html/lp-plano-lua
COPY nginx.runtime.conf /etc/nginx/conf.d/default.conf
RUN rm -f /etc/nginx/sites-enabled/default \
    && mkdir -p /app/data /run/nginx

EXPOSE 80
CMD ["sh", "-c", "uvicorn app.main:app --host 127.0.0.1 --port 8000 & exec nginx -g 'daemon off;'"]
