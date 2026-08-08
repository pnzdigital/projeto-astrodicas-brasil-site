# Imagem única do runtime: API (uvicorn) atrás do nginx que serve as páginas.
# É esta que o Coolify constrói — o compose.yaml de dois serviços é para o
# ambiente local. Mantê-la de pé importa: quando ela quebra, produção congela
# na última build que passou, e nada no site avisa.

# ── build ───────────────────────────────────────────────────────────────────
# pyswisseph não publica wheel para linux/amd64: o pip precisa compilar a
# libswe em C. A imagem slim não traz compilador, e sem este estágio a build
# morre em "command 'gcc' failed" — foi o que segurou todo deploy desde julho.
# O compilador fica aqui e não viaja para a imagem final.
FROM python:3.12-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY api/requirements.txt ./requirements.txt
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ── runtime ─────────────────────────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl nginx \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY api/requirements.txt ./requirements.txt
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

COPY api/app ./app

# A pasta inteira, de propósito. A lista manual de páginas ficou para trás toda
# vez que uma página nova nasceu, e o nginx.runtime.conf já aponta para arquivos
# (checkout.html, termos.html, suporte.html, horoscopo-gratis.html) que a lista
# não copiava — o visitante recebia 404 numa rota que existe na configuração.
COPY portal-demo/ /usr/share/nginx/html/
COPY lp-plano-lua /usr/share/nginx/html/lp-plano-lua
RUN rm -f /usr/share/nginx/html/Dockerfile /usr/share/nginx/html/*.test.mjs

COPY nginx.runtime.conf /etc/nginx/conf.d/default.conf
RUN rm -f /etc/nginx/sites-enabled/default \
    && mkdir -p /app/data /run/nginx

COPY start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 80
# start.sh sobe uvicorn + worker de geração + nginx.
# Se qualquer processo morrer, o script encerra com exit 1 para que a
# restart policy do Coolify/Docker reinicie o container inteiro — nunca
# fica a fila parada sem supervisão.
# Ver start.sh para detalhes de supervisão e graceful shutdown.
CMD ["/start.sh"]
