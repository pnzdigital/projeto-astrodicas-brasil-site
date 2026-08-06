"""Adaptador Mercado Pago (checkout transparente) do canal site.

Somente a Argentina usa este adaptador hoje. O portal continua falando em
`site:*` e não conhece a API do provedor: quem traduz é este módulo.

Nenhuma credencial vive no repositório. Configure no ambiente:

- ``MP_ACCESS_TOKEN``      access token privado da conta Mercado Pago (AR)
- ``MP_PUBLIC_KEY``        chave pública usada pelo Payment Brick no navegador
- ``MP_WEBHOOK_SECRET``    clave secreta do painel > Webhooks (assinatura x-signature)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

API_BASE = os.getenv("MP_API_BASE", "https://api.mercadopago.com").rstrip("/")


class MercadoPagoError(RuntimeError):
    """Falha ao falar com o Mercado Pago."""


def access_token() -> str:
    token = os.getenv("MP_ACCESS_TOKEN", "").strip()
    if not token:
        raise MercadoPagoError("MP_ACCESS_TOKEN não configurado.")
    return token


def public_key() -> str:
    return os.getenv("MP_PUBLIC_KEY", "").strip()


def is_enabled() -> bool:
    return bool(os.getenv("MP_ACCESS_TOKEN", "").strip())


def _request(method: str, path: str, payload: dict | None = None, idempotency_key: str = "") -> dict:
    headers = {
        "Authorization": f"Bearer {access_token()}",
        "Content-Type": "application/json",
    }
    if idempotency_key:
        headers["X-Idempotency-Key"] = idempotency_key
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(f"{API_BASE}{path}", data=data, headers=headers, method=method)
    timeout = float(os.getenv("MP_TIMEOUT_SECONDS", "20"))
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        logger.warning("Mercado Pago %s %s falhou: %s %s", method, path, exc.code, body)
        raise MercadoPagoError(f"Mercado Pago respondeu {exc.code}: {body[:400]}") from exc
    except (URLError, TimeoutError, ValueError) as exc:
        logger.warning("Mercado Pago indisponível: %s", exc)
        raise MercadoPagoError(f"Mercado Pago indisponível: {type(exc).__name__}") from exc


def create_payment(
    *,
    amount: float,
    description: str,
    order_id: str,
    payer_email: str,
    form_data: dict,
    notification_url: str = "",
) -> dict:
    """Cria o pagamento. O valor vem sempre do servidor, nunca do navegador."""
    body: dict = {
        "transaction_amount": amount,
        "description": description,
        "external_reference": order_id,
        "statement_descriptor": os.getenv("MP_STATEMENT_DESCRIPTOR", "ASTRODICAS"),
        "payment_method_id": form_data.get("payment_method_id"),
        "installments": int(form_data.get("installments") or 1),
        "payer": {"email": payer_email},
    }
    if form_data.get("token"):
        body["token"] = form_data["token"]
    if form_data.get("issuer_id"):
        body["issuer_id"] = int(form_data["issuer_id"])
    payer = form_data.get("payer") or {}
    identification = payer.get("identification") or {}
    if identification.get("number"):
        body["payer"]["identification"] = {
            "type": identification.get("type"),
            "number": identification.get("number"),
        }
    if payer.get("first_name"):
        body["payer"]["first_name"] = payer["first_name"]
    # O Mercado Pago recusa notification_url que não seja HTTPS público
    # (localhost/127.0.0.1 devolvem 400). Em desenvolvimento, seguimos sem ela.
    if notification_url.startswith("https://") and "localhost" not in notification_url and "127.0.0.1" not in notification_url:
        body["notification_url"] = notification_url
    return _request("POST", "/v1/payments", body, idempotency_key=order_id)


def get_payment(payment_id: str | int) -> dict:
    return _request("GET", f"/v1/payments/{payment_id}")


def create_preapproval(
    *,
    amount: float,
    currency: str,
    reason: str,
    payer_email: str,
    card_token_id: str,
    external_reference: str,
    trial_days: int,
    back_url: str,
    notification_url: str = "",
) -> dict:
    """Assinatura mensal que começa com ``trial_days`` grátis.

    O período grátis é do Mercado Pago, não nosso: ``free_trial`` faz o provedor
    autorizar o cartão hoje e só cobrar a primeira mensalidade no fim do prazo.
    Fazer a contagem do nosso lado significaria ter que disparar a primeira
    cobrança sozinhos, e é justamente a parte que não pode falhar.

    ``status: authorized`` é o que cria a assinatura já valendo com o cartão
    tokenizado; sem isso o Mercado Pago devolve ``pending`` e espera o cliente
    autorizar numa página deles, o que quebraria o funil dentro do site.
    """
    body: dict = {
        "reason": reason,
        "external_reference": external_reference,
        "payer_email": payer_email,
        "card_token_id": card_token_id,
        "back_url": back_url,
        "status": "authorized",
        "auto_recurring": {
            "frequency": 1,
            "frequency_type": "months",
            "transaction_amount": amount,
            "currency_id": currency,
            "free_trial": {"frequency": trial_days, "frequency_type": "days"},
        },
    }
    if notification_url.startswith("https://") and "localhost" not in notification_url and "127.0.0.1" not in notification_url:
        body["notification_url"] = notification_url
    return _request("POST", "/preapproval", body, idempotency_key=external_reference)


def get_preapproval(preapproval_id: str) -> dict:
    return _request("GET", f"/preapproval/{preapproval_id}")


def cancel_preapproval(preapproval_id: str) -> dict:
    """Cancela a assinatura no provedor.

    ``cancelled`` é terminal no Mercado Pago: não existe "despausar". O portal
    trata o cancelamento como definitivo justamente por isso — voltar significa
    assinar de novo.
    """
    return _request("PUT", f"/preapproval/{preapproval_id}", {"status": "cancelled"})


def webhook_secret(env_var: str = "MP_WEBHOOK_SECRET") -> str:
    """A clave secreta da aplicação que assina esta rota.

    Cada aplicação do Mercado Pago tem a sua. A rota argentina lê
    ``MP_WEBHOOK_SECRET_AR`` e cai em ``MP_WEBHOOK_SECRET`` enquanto a
    variável nova não estiver no ambiente, para o deploy não derrubar
    notificações no intervalo entre subir o código e configurar o segredo.
    """
    secret = os.getenv(env_var, "").strip()
    if not secret and env_var != "MP_WEBHOOK_SECRET":
        secret = os.getenv("MP_WEBHOOK_SECRET", "").strip()
    return secret


def verify_signature(
    signature_header: str, request_id: str, data_id: str, env_var: str = "MP_WEBHOOK_SECRET"
) -> bool:
    """Valida o header ``x-signature`` da notificação.

    Sem segredo configurado em produção a verificação falha (503 deve ser
    levantado pelo chamador). Em desenvolvimento, aceita sem verificação
    apenas quando ``ALLOW_INSECURE_DEV=1`` estiver definido.
    """
    secret = webhook_secret(env_var)
    if not secret:
        env = os.getenv("ENV", "development")
        allow_insecure = os.getenv("ALLOW_INSECURE_DEV", "0") == "1"
        if env == "production" or not allow_insecure:
            logger.warning("%s ausente: verificação recusada.", env_var)
            return False
        logger.warning("%s ausente: verificação pulada (dev).", env_var)
        return True
    parts = dict(
        piece.strip().split("=", 1)
        for piece in signature_header.split(",")
        if "=" in piece
    )
    timestamp, received = parts.get("ts", ""), parts.get("v1", "")
    if not timestamp or not received:
        return False
    # Doc oficial exige data.id em lowercase no manifest (payment id é sempre
    # numérico hoje, mas normalizamos aqui para bater com a especificação).
    manifest = f"id:{data_id.lower()};request-id:{request_id};ts:{timestamp};"
    expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


# Mercado Pago -> vocabulário interno do site.
STATUS_MAP = {
    "approved": "paid",
    "authorized": "pending",
    "in_process": "pending",
    "in_mediation": "pending",
    "pending": "pending",
    "rejected": "failed",
    "cancelled": "cancelled",
    "refunded": "refunded",
    "charged_back": "refunded",
}


def internal_status(mp_status: str | None) -> str:
    return STATUS_MAP.get(mp_status or "", "pending")
