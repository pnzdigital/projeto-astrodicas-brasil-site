"""Quem tem acesso a quê, e até quando.

Até aqui todo entitlement era vitalício: comprou o mapa astral, tem para sempre.
``Entitlement.expires_at`` existia na tabela mas ninguém lia — nenhum produto
vencia, então a coluna nunca importou.

O Plano Lua por assinatura muda isso. Ele começa em 3 dias grátis e continua
mensal enquanto o cartão pagar; se a pessoa cancelar ou o pagamento falhar, o
acesso precisa acabar de fato. Sem este módulo o trial expirado continuaria
liberando horóscopo diário para sempre, e a assinatura não teria como ser
cancelada de verdade.

Duas regras, e são as duas todas:

- entitlement sem ``expires_at`` é vitalício (compra avulsa) — nada mudou para
  quem já comprou;
- entitlement com ``expires_at`` vale até aquele instante, inclusive.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import Entitlement


def is_active(entitlement: Entitlement | None, now: datetime | None = None) -> bool:
    if entitlement is None or entitlement.status != "available":
        return False
    if entitlement.expires_at is None:
        return True
    moment = now or datetime.now(timezone.utc)
    expires_at = entitlement.expires_at
    # SQLite devolve datetime ingênuo mesmo em coluna timezone-aware. Comparar
    # com um datetime aware levantaria TypeError em produção, na hora do gate.
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at >= moment


def active(db: Session, user_id: str, product_id: str, now: datetime | None = None) -> Entitlement | None:
    """O entitlement válido AGORA para este produto, ou nada."""
    moment = now or datetime.now(timezone.utc)
    return db.scalar(
        select(Entitlement).where(
            Entitlement.user_id == user_id,
            Entitlement.product_id == product_id,
            Entitlement.status == "available",
            or_(Entitlement.expires_at.is_(None), Entitlement.expires_at >= moment),
        )
    )
