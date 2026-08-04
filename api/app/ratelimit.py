"""Rate limiting in-memory por IP, janela deslizante, sem dependência externa.

Escopo: só as rotas sensíveis (login/registro, checkout/order, webhooks).
Não é um WAF nem substitui a assinatura HMAC dos webhooks — é uma segunda
camada contra brute-force de senha e flood, e evita que um IP monopolize o
processo. Escala de um único worker: para múltiplos workers/réplicas em
produção, trocar por um backend compartilhado (Redis) se o tráfego justificar.

``RATE_LIMIT_ENABLED=0`` desliga tudo (default nos testes, via conftest).
``RATE_LIMIT_BYPASS_IPS`` é uma lista separada por vírgula de IPs que nunca
são limitados — útil para fixar os IPs conhecidos do provedor de webhook.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from typing import Callable

from fastapi import HTTPException, Request, status

_RATE_LIMIT_COPY = {
    "pt-BR": "Muitas tentativas. Tente novamente em instantes.",
    "es-AR": "Demasiados intentos. Probá de nuevo en unos instantes.",
}


def _pick_locale(accept_language: str | None) -> str:
    if not accept_language:
        return "pt-BR"
    primary = accept_language.split(",")[0].split(";")[0].strip()
    if primary.startswith("es"):
        return "es-AR"
    if primary.startswith("pt"):
        return "pt-BR"
    return "pt-BR"

_buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def reset_all() -> None:
    """Helper de teste: limpa todos os buckets entre casos de teste."""
    with _lock:
        _buckets.clear()


def _enabled() -> bool:
    return os.getenv("RATE_LIMIT_ENABLED", "1") == "1"


def _bypass_ips() -> set[str]:
    return {ip.strip() for ip in os.getenv("RATE_LIMIT_BYPASS_IPS", "").split(",") if ip.strip()}


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def rate_limit(
    *,
    max_requests_env: str,
    window_seconds_env: str,
    default_max: int,
    default_window_seconds: int,
    key_fn: Callable[[Request], str] = lambda request: request.url.path,
) -> Callable[[Request], None]:
    """Fábrica de dependência FastAPI: uma janela deslizante por (rota, IP)."""

    def dependency(request: Request) -> None:
        if not _enabled():
            return
        ip = _client_ip(request)
        if ip in _bypass_ips():
            return

        max_requests = int(os.getenv(max_requests_env, str(default_max)))
        window = float(os.getenv(window_seconds_env, str(default_window_seconds)))
        bucket_key = (key_fn(request), ip)
        now = time.monotonic()

        with _lock:
            bucket = _buckets[bucket_key]
            while bucket and now - bucket[0] > window:
                bucket.popleft()
            if len(bucket) >= max_requests:
                retry_after = max(1, int(window - (now - bucket[0])) + 1)
                locale = _pick_locale(request.headers.get("accept-language"))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=_RATE_LIMIT_COPY.get(locale, _RATE_LIMIT_COPY["pt-BR"]),
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)

    return dependency


# Autenticação (registro, login do site, login do admin): defende brute-force
# de senha. 10 tentativas/60s por IP é generoso pro usuário legítimo e apertado
# pra automação de força bruta.
auth_rate_limit = rate_limit(
    max_requests_env="RATE_LIMIT_AUTH_MAX",
    window_seconds_env="RATE_LIMIT_AUTH_WINDOW_SECONDS",
    default_max=10,
    default_window_seconds=60,
)

# Abertura de pedido de checkout: protege contra flood de ordens (cada uma é
# um INSERT), sem incomodar quem está de fato comprando (múltiplos produtos
# no carrinho, tentativas com cartão recusado, etc.).
checkout_rate_limit = rate_limit(
    max_requests_env="RATE_LIMIT_CHECKOUT_MAX",
    window_seconds_env="RATE_LIMIT_CHECKOUT_WINDOW_SECONDS",
    default_max=20,
    default_window_seconds=60,
)

# Webhooks (Cakto e Mercado Pago): a assinatura HMAC já impede forjar eventos,
# então este limite existe só contra flood/DoS. Default alto (120/60s = 2 rps
# sustentado por IP) porque o Mercado Pago reenvia notificações em burst
# quando o merchant fica offline por um tempo, e perder um evento legítimo
# custa uma venda não liberada. Ajuste via env; use RATE_LIMIT_BYPASS_IPS se
# precisar fixar os IPs oficiais do provedor sem limite nenhum.
webhook_rate_limit = rate_limit(
    max_requests_env="RATE_LIMIT_WEBHOOK_MAX",
    window_seconds_env="RATE_LIMIT_WEBHOOK_WINDOW_SECONDS",
    default_max=120,
    default_window_seconds=60,
    key_fn=lambda request: f"webhook:{request.path_params.get('provider', 'mercadopago')}",
)


# Prévia grátis do mapa natal: rota pública, sem auth e sem pagamento. Cada
# chamada custa uma consulta de geocoding (Nominatim, com política de uso) e
# um cálculo de efemérides. 12/60s por IP cobre o visitante que erra a cidade
# e tenta de novo algumas vezes, e corta o scraper que quer usar a rota como
# API de efemérides grátis.
preview_rate_limit = rate_limit(
    max_requests_env="RATE_LIMIT_PREVIEW_MAX",
    window_seconds_env="RATE_LIMIT_PREVIEW_WINDOW_SECONDS",
    default_max=12,
    default_window_seconds=60,
)


# Recuperação de senha: protege contra spam de e-mail na caixa do dono.
# 5 por 15min por IP — humano legítimo não precisa de mais; atacante não
# consegue enumerar alvos inundando ninguém. A neutralidade da resposta
# (sempre 200, mesmo com e-mail desconhecido) já blinda enumeração.
password_reset_rate_limit = rate_limit(
    max_requests_env="RATE_LIMIT_PASSWORD_RESET_MAX",
    window_seconds_env="RATE_LIMIT_PASSWORD_RESET_WINDOW_SECONDS",
    default_max=5,
    default_window_seconds=900,
)
