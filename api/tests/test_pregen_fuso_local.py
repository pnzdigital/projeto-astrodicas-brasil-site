"""Pré-geração fecha o dia no fuso LOCAL da cliente, não em UTC.

Bug de produção (QA 17/08/2026): renewal._pregen_period_key e
_reading_fresh_for_period fechavam o dia em UTC enquanto o portal
(main.reading_is_current, horoscope_free.local_today) já conta em fuso
local, e o cron roda 06:00 UTC (03:00 BR/AR). Duas consequências:

1. Entre ~21h e meia-noite locais, o dia UTC já virou amanhã enquanto o dia
   local ainda é hoje — uma leitura de hoje (gerada de manhã, mesmo dia
   local) parecia "vencida" pelo corte UTC e a pré-geração duplicava.
2. Entre 00h e ~03h locais (antes do cron rodar), o dia local já virou mas
   o corte UTC (que só rola 3h depois) ainda não — a cliente abria o app e
   via a leitura de ontem.

Este arquivo prova, com horários fixos (nunca "agora"), que o corte agora
acompanha o fuso da cliente (derivado do locale, via
horoscope_free.LOCALE_DEFAULTS) e que rodar a pré-geração duas vezes no
mesmo dia local não duplica.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import horoscope_free
from app import renewal as renewal_module
from app.db import SessionLocal
from app.models import Entitlement, GenerationJob, Reading
from conftest import register as _register

CONTENT_ID = "site:content:horoscopo_diario"
LOCALE = "pt-BR"
PRODUCT_ID = "site:diario_astral"
TZ = ZoneInfo("America/Sao_Paulo")  # BR e AR são ambas UTC-3 hoje


def _local(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=TZ)


def _freeze(monkeypatch, momento_local: datetime):
    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return momento_local.astimezone(tz) if tz else momento_local

    monkeypatch.setattr(renewal_module, "datetime", _Now)
    monkeypatch.setattr(horoscope_free, "datetime", _Now)


def _cria_reading(user_id: str, created_at_local: datetime, status="ready") -> str:
    with SessionLocal() as db:
        reading = Reading(
            user_id=user_id,
            content_id=CONTENT_ID,
            product_id=PRODUCT_ID,
            status=status,
            title="Horóscopo do Dia",
            source="llm",
            input_snapshot={"birth_date": "1990-01-01"},
            sections_total=1,
            sections_done=1 if status == "ready" else 0,
            created_at=created_at_local.astimezone(timezone.utc),
        )
        db.add(reading)
        db.commit()
        db.refresh(reading)
        return reading.id


# ---------------------------------------------------------------------------
# Unidade: o corte de período acompanha o fuso local, não o UTC do servidor
# ---------------------------------------------------------------------------

def test_leitura_gerada_de_manha_continua_fresca_as_23h30_locais(monkeypatch):
    """23h30 local (17/08) == 02h30 UTC do dia 18 — dia UTC já virou, local não.

    Sob o corte antigo (UTC), essa janela achava a leitura de hoje "vencida"
    (criada no dia UTC anterior) e regenerava à toa. Com corte local, a
    leitura de hoje de manhã continua valendo até a meia-noite local.
    """
    momento = _local(2026, 8, 17, 23, 30)
    assert momento.astimezone(timezone.utc).date().isoformat() == "2026-08-18"  # dia UTC já virou
    _freeze(monkeypatch, momento)

    reading_id = _cria_reading("user-1", _local(2026, 8, 17, 8, 0))

    with SessionLocal() as db:
        period_key = renewal_module._pregen_period_key(CONTENT_ID, LOCALE)
        assert period_key == "2026-08-17"
        fresh = renewal_module._reading_fresh_for_period(db, "user-1", CONTENT_ID, period_key, LOCALE)
    assert fresh is True
    assert reading_id


def test_leitura_de_ontem_fica_vencida_as_00h30_locais(monkeypatch):
    """00h30 local (18/08) — dia local já virou, cron (03h local) ainda não rodou.

    Sob o corte UTC antigo essa janela (UTC 03h30, ainda dia 18 UTC — o dia
    UTC só troca às 21h locais do dia anterior) considerava fresca a leitura
    criada na noite anterior, e a cliente via a leitura de ontem até o cron
    rodar. Com corte local, a leitura de ontem já conta como vencida assim
    que o relógio da cliente vira o dia — abrindo espaço pra pré-geração
    (ou pro clique manual em /generate) preencher o dia novo sem esperar 3h.
    """
    momento = _local(2026, 8, 18, 0, 30)
    _freeze(monkeypatch, momento)

    _cria_reading("user-2", _local(2026, 8, 17, 20, 0))

    with SessionLocal() as db:
        period_key = renewal_module._pregen_period_key(CONTENT_ID, LOCALE)
        assert period_key == "2026-08-18"
        fresh = renewal_module._reading_fresh_for_period(db, "user-2", CONTENT_ID, period_key, LOCALE)
    assert fresh is False


def test_leitura_gerada_logo_apos_meia_noite_local_fica_fresca_as_4h(monkeypatch):
    """04h local (18/08), depois do cron recomendado — idempotência dentro do dia novo."""
    momento = _local(2026, 8, 18, 4, 0)
    _freeze(monkeypatch, momento)

    _cria_reading("user-3", _local(2026, 8, 18, 0, 5))

    with SessionLocal() as db:
        period_key = renewal_module._pregen_period_key(CONTENT_ID, LOCALE)
        assert period_key == "2026-08-18"
        fresh = renewal_module._reading_fresh_for_period(db, "user-3", CONTENT_ID, period_key, LOCALE)
    assert fresh is True


def test_job_active_tambem_usa_janela_local(monkeypatch):
    """Job em andamento criado ainda no dia local anterior não deve barrar o dia novo."""
    momento = _local(2026, 8, 18, 0, 30)
    _freeze(monkeypatch, momento)

    with SessionLocal() as db:
        job = GenerationJob(
            user_id="user-4",
            content_id=CONTENT_ID,
            reading_id="reading-antiga",
            status="running",
            locale=LOCALE,
            created_at=_local(2026, 8, 17, 20, 0).astimezone(timezone.utc),
        )
        db.add(job)
        db.commit()

    with SessionLocal() as db:
        active = renewal_module._job_active(db, "user-4", CONTENT_ID, LOCALE)
    assert active is False


# ---------------------------------------------------------------------------
# Integração: rodar a pré-geração duas vezes no mesmo dia local não duplica
# ---------------------------------------------------------------------------

def test_run_daily_pregen_duas_vezes_no_mesmo_dia_local_nao_duplica(client, monkeypatch, skip_auto_run):
    r = _register(client, "diaria@example.com", "senha-segura", name="Cliente Diária", locale=LOCALE)
    assert r.status_code == 200
    client.put(
        "/api/me/profile",
        json={
            "birth_date": "1990-05-20",
            "birth_time": "14:30:00",
            "birth_city": "Recife",
            "birth_country": "BR",
        },
    )
    granted = client.post(
        "/api/webhooks/cakto",
        json={"event_id": "evt-diaria", "email": "diaria@example.com", "product_id": PRODUCT_ID},
    )
    assert granted.status_code == 200, granted.text

    momento = _local(2026, 8, 18, 4, 0)
    _freeze(monkeypatch, momento)

    with SessionLocal() as db:
        ent = db.query(Entitlement).filter(Entitlement.product_id == PRODUCT_ID).first()
        assert ent is not None
        user_id = ent.user_id

    with SessionLocal() as db:
        stats_1 = renewal_module.run_daily_pregen(db)
    with SessionLocal() as db:
        stats_2 = renewal_module.run_daily_pregen(db)

    assert stats_1["enqueued"] >= 1

    with SessionLocal() as db:
        count = (
            db.query(Reading)
            .filter(
                Reading.user_id == user_id,
                Reading.content_id == CONTENT_ID,
                Reading.status.in_(["ready", "in_progress", "fallback"]),
            )
            .count()
        )
    assert count == 1, f"pregen duplicou leitura do dia local: stats1={stats_1} stats2={stats_2}"
