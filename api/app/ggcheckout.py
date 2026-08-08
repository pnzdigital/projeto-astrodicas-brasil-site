"""Adapter de webhook para o GG Checkout (mercado BR).

Autenticação: secret em ``Authorization: Bearer <secret>`` OU ``x-secret: <secret>``,
comparado em tempo constante via hmac.compare_digest. NÃO é HMAC do corpo.

Payload aninhado:
    payment.id, payment.status, customer.email,
    product.id, products[].id, webhook.id, webhook.businessId, UTMs.

Mapeamento GG product_id → nosso product_id via GG_PRODUCT_MAP (JSON):
    GG_PRODUCT_MAP='{"gg-uuid-abc": "site:plano_lua"}'
Adicionar produto: só editar a env var.
"""

from __future__ import annotations

import hmac
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import checkout as site_checkout
from . import pricing
from .db import get_db
from .models import Order, WebhookEvent
from .ratelimit import webhook_rate_limit

logger = logging.getLogger(__name__)
router = APIRouter()

PAID_STATUSES = {"paid"}
REVOKE_STATUSES = {"refunded", "charged_back"}


def _gg_secret() -> str:
    return os.getenv("GG_CHECKOUT_SECRET", "").strip()


def _extract_secret(request: Request) -> str:
    """Extrai o secret do header Authorization (Bearer) ou x-secret."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return request.headers.get("x-secret", "").strip()


def _verify_secret(provided: str) -> bool:
    secret = _gg_secret()
    if not secret:
        return False
    return hmac.compare_digest(secret.encode(), provided.encode())


def gg_product_map() -> dict[str, str]:
    """Mapa product.id do GG → product_id nosso.

    Configurar: GG_PRODUCT_MAP='{"gg-uuid": "site:plano_lua"}'
    Adicionar produto: só editar a env var.
    """
    raw = os.getenv("GG_PRODUCT_MAP", "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error("GG_PRODUCT_MAP não é JSON válido — mapeamento de produtos indisponível")
        return {}


def resolve_gg_product(payload: dict) -> str | None:
    """Traduz product.id(s) do GG para nosso product_id.

    Tenta product.id (produto primário) primeiro, depois products[] (lista).
    Retorna None se nenhum mapeamento for encontrado.
    """
    pmap = gg_product_map()
    gg_id = ((payload.get("product") or {}).get("id") or "").strip()
    if gg_id and gg_id in pmap:
        return pmap[gg_id]
    for prod in payload.get("products") or []:
        pid = (prod.get("id") or "").strip()
        if pid and pid in pmap:
            return pmap[pid]
    return None


@router.post("/api/webhooks/ggcheckout", dependencies=[Depends(webhook_rate_limit)])
async def ggcheckout_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    env = os.getenv("ENV", "development")
    allow_insecure = os.getenv("ALLOW_INSECURE_DEV", "0") == "1"

    secret = _gg_secret()
    if not secret:
        if env == "production" or not allow_insecure:
            raise HTTPException(
                status_code=503,
                detail="Webhook GG Checkout indisponível: GG_CHECKOUT_SECRET não configurado.",
            )
    else:
        if not _verify_secret(_extract_secret(request)):
            raise HTTPException(status_code=401, detail="Assinatura inválida.")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Payload inválido.")

    payment = payload.get("payment") or {}
    payment_id = str(payment.get("id") or "").strip()
    status = str(payment.get("status") or "").strip().lower()
    # GG nests customer inside payment; fall back to top-level for forward compat
    customer = payment.get("customer") or payload.get("customer") or {}
    email = str(customer.get("email") or "").strip().lower()

    if not payment_id or not email:
        logger.warning(
            "GG Checkout webhook sem payment.id ou customer.email; campos presentes: %s",
            list(payload.keys()),
        )
        return {"ok": True, "ignored": "payload incompleto"}

    event_id = f"gg:{payment_id}"

    # Idempotência: GG retenta até 3×; a segunda chamada com mesmo payment.id
    # encontra o evento e retorna sem reprocessar.
    if db.scalar(
        select(WebhookEvent).where(
            WebhookEvent.provider == "ggcheckout",
            WebhookEvent.event_id == event_id,
        )
    ):
        return {"ok": True, "duplicate": True}

    webhook_id = str((payload.get("webhook") or {}).get("id") or payment_id)
    event = WebhookEvent(
        provider="ggcheckout",
        event_id=event_id,
        payload={"payment_id": payment_id, "status": status, "email": email, "webhook_id": webhook_id},
    )
    db.add(event)

    if status in REVOKE_STATUSES:
        # Revogação de entitlement não existe hoje na base de código.
        # Registramos o evento como mínimo necessário; quando a revogação for
        # implementada (Entitlement.status = "revoked"), adicionar aqui.
        logger.warning(
            "GG Checkout %s: payment_id=%s email=%s — acesso não revogado (mecanismo pendente de implementação)",
            status,
            payment_id,
            email,
        )
        db.commit()
        return {"ok": True, "status": status, "action": "logged"}

    if status not in PAID_STATUSES:
        db.commit()
        return {"ok": True, "status": status, "action": "ignored"}

    # status == "paid"
    product_id = resolve_gg_product(payload)
    if not product_id:
        # Falha de CONFIGURAÇÃO, não de payload. Não persistir o evento para que
        # a retentativa do GG funcione depois de GG_PRODUCT_MAP ser corrigido.
        # (Payload genuinamente inválido — sem payment_id/email — já foi descartado
        # acima e nunca chega aqui.)
        gg_ids = sorted(
            {((payload.get("product") or {}).get("id") or "").strip()}
            | {(p.get("id") or "").strip() for p in (payload.get("products") or [])}
            - {""}
        )
        logger.error(
            "GG Checkout pagamento %s: UIDs %r não estão em GG_PRODUCT_MAP — "
            "adicione-os ao mapa para liberar o acesso. Evento NÃO salvo; retentativa será processada.",
            payment_id,
            gg_ids,
        )
        db.expunge(event)  # remove da sessão sem commitar; preserva retentativas
        raise HTTPException(
            status_code=422,
            detail=f"Produto não mapeado. Configure GG_PRODUCT_MAP. UIDs recebidos: {gg_ids}",
        )

    customer_name = (
        (customer.get("name") or "")
        or f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
    )
    order = Order(
        provider="ggcheckout",
        external_id=payment_id,
        product_id=product_id,
        status="paid",
        amount_minor=pricing.amount_minor(product_id, "pt-BR"),
        currency="BRL",
        locale="pt-BR",
        market="BR",
        customer_email=email,
        raw_payload={**payload, "name": customer_name},
    )
    db.add(order)
    db.flush()

    # fulfill_order comita event + order + user + entitlements de uma vez.
    site_checkout.fulfill_order(db, order)
    return {"ok": True, "status": "paid", "product_id": product_id}
