"""Previsão semanal da assinante — validade e fuso.

Cobre:
1. _target_iso_week: semana-alvo muda no sábado (não na segunda).
2. reading_is_current para previsao_semanal usa target_iso_week do snapshot,
   não a semana de criação da leitura.
3. Virada de semana: sexta 23h local ≠ sábado 00h local (fronteira crítica).
4. calendario_lunar usa fuso local, não UTC.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app import main as main_module
from app.models import Reading

CONTENT_SEMANAL = "site:content:previsao_semanal"
CONTENT_LUNAR = "site:content:calendario_lunar"

_SNAPSHOT_BASE = {"birth_date": "1990-05-20"}


def _make_weekly_reading(target_iso_week: str, created_at: datetime | None = None) -> Reading:
    r = Reading(
        user_id="fake",
        content_id=CONTENT_SEMANAL,
        product_id="site:plano_lua",
        status="ready",
        title="Previsão da semana",
        input_snapshot={**_SNAPSHOT_BASE, "target_iso_week": target_iso_week},
        body_html="<p>Semana boa.</p>",
    )
    r.created_at = created_at or datetime(2026, 8, 8, 12, tzinfo=timezone.utc)
    return r


def _freeze(utc_dt: datetime):
    """Retorna uma classe que substitui datetime para congelar `now()`."""

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return utc_dt.astimezone(tz) if tz else utc_dt

    return _Now


# ── _target_iso_week: lógica de fronteira do sábado ────────────────────────

def test_target_iso_week_sexta_aponta_semana_corrente(monkeypatch):
    """Sexta-feira → semana-alvo é a corrente (segunda passada até domingo)."""
    import app.horoscope_free as hf_mod

    # 2026-08-07 é sexta-feira
    sexta_utc = datetime(2026, 8, 7, 22, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(hf_mod, "datetime", _freeze(sexta_utc))

    result = main_module._target_iso_week("pt-BR")
    iso = date(2026, 8, 7).isocalendar()
    assert result == f"{iso[0]}-W{iso[1]:02d}", f"Sexta deve apontar para a semana corrente, obteve {result}"


def test_target_iso_week_sabado_aponta_semana_seguinte(monkeypatch):
    """Sábado → semana-alvo é a seguinte (a que começa na segunda)."""
    import app.horoscope_free as hf_mod

    # 2026-08-08 03:00 UTC = 2026-08-08 00:00 BRT (UTC-3) → já sábado local
    sabado_utc = datetime(2026, 8, 8, 3, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(hf_mod, "datetime", _freeze(sabado_utc))

    result = main_module._target_iso_week("pt-BR")
    # Próxima segunda = 2026-08-10
    proxima_segunda = date(2026, 8, 10)
    iso = proxima_segunda.isocalendar()
    assert result == f"{iso[0]}-W{iso[1]:02d}", f"Sábado deve apontar para a semana seguinte, obteve {result}"


def test_target_iso_week_domingo_aponta_semana_seguinte(monkeypatch):
    """Domingo → semana-alvo é a seguinte (igual ao sábado)."""
    import app.horoscope_free as hf_mod

    # 2026-08-09 é domingo
    domingo_utc = datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(hf_mod, "datetime", _freeze(domingo_utc))

    result = main_module._target_iso_week("pt-BR")
    proxima_segunda = date(2026, 8, 10)
    iso = proxima_segunda.isocalendar()
    assert result == f"{iso[0]}-W{iso[1]:02d}", f"Domingo deve apontar para a semana seguinte, obteve {result}"


# ── Fronteira crítica: sexta 23h local ≠ sábado 00h local em São Paulo ─────

def test_virada_sexta_sabado_21h_utc_e_sexta_local_br(monkeypatch):
    """21h UTC de sexta = 18h em Brasília = ainda sexta → semana corrente."""
    import app.horoscope_free as hf_mod

    # 2026-08-07 21:00 UTC = 2026-08-07 18:00 BRT (UTC-3) → ainda sexta
    sexta_21h_utc = datetime(2026, 8, 7, 21, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(hf_mod, "datetime", _freeze(sexta_21h_utc))

    result = main_module._target_iso_week("pt-BR")
    iso = date(2026, 8, 7).isocalendar()
    assert result == f"{iso[0]}-W{iso[1]:02d}"


def test_virada_sexta_sabado_03h_utc_e_sabado_local_br(monkeypatch):
    """03h UTC de sábado = 00h BRT = já sábado → semana seguinte."""
    import app.horoscope_free as hf_mod

    # 2026-08-08 03:00 UTC = 2026-08-08 00:00 BRT (UTC-3) → sábado local
    sabado_00h_brt_utc = datetime(2026, 8, 8, 3, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(hf_mod, "datetime", _freeze(sabado_00h_brt_utc))

    result = main_module._target_iso_week("pt-BR")
    proxima_segunda = date(2026, 8, 10)
    iso = proxima_segunda.isocalendar()
    assert result == f"{iso[0]}-W{iso[1]:02d}"


def test_virada_domingo_23h_local_aponta_semana_seguinte(monkeypatch):
    """23h local de domingo → ainda aponta para a semana seguinte (não virou segunda)."""
    import app.horoscope_free as hf_mod

    # 2026-08-09 23:00 BRT = 2026-08-10 02:00 UTC — mas local ainda é domingo
    # Usamos 2026-08-10 01:59 UTC = 2026-08-09 22:59 BRT (domingo local)
    domingo_23h_utc = datetime(2026, 8, 10, 1, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(hf_mod, "datetime", _freeze(domingo_23h_utc))

    result = main_module._target_iso_week("pt-BR")
    proxima_segunda = date(2026, 8, 10)
    iso = proxima_segunda.isocalendar()
    assert result == f"{iso[0]}-W{iso[1]:02d}"


# ── reading_is_current para previsao_semanal ────────────────────────────────

def test_leitura_semanal_mesma_semana_alvo_e_current(monkeypatch):
    """Reading com target_iso_week == semana corrente → is_current True."""
    import app.horoscope_free as hf_mod

    # Quarta-feira, semana corrente
    quarta_utc = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(hf_mod, "datetime", _freeze(quarta_utc))

    iso = date(2026, 8, 5).isocalendar()
    target = f"{iso[0]}-W{iso[1]:02d}"
    snapshot = {**_SNAPSHOT_BASE, "target_iso_week": target}
    reading = _make_weekly_reading(target)

    assert main_module.reading_is_current(reading, CONTENT_SEMANAL, snapshot, "pt-BR") is True


def test_leitura_semanal_semana_alvo_antiga_nao_e_current(monkeypatch):
    """Reading com target_iso_week de semana passada → is_current False (nova geração)."""
    import app.horoscope_free as hf_mod

    quarta_utc = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(hf_mod, "datetime", _freeze(quarta_utc))

    iso_atual = date(2026, 8, 5).isocalendar()
    target_atual = f"{iso_atual[0]}-W{iso_atual[1]:02d}"
    target_antigo = f"{iso_atual[0]}-W{iso_atual[1] - 1:02d}"

    snapshot_atual = {**_SNAPSHOT_BASE, "target_iso_week": target_atual}
    reading = _make_weekly_reading(target_antigo)  # gravado com semana passada

    assert main_module.reading_is_current(reading, CONTENT_SEMANAL, snapshot_atual, "pt-BR") is False


def test_leitura_semanal_sem_target_iso_week_no_snapshot_nao_e_current(monkeypatch):
    """Reading antiga (sem target_iso_week) → snapshot difere → is_current False."""
    import app.horoscope_free as hf_mod

    quarta_utc = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(hf_mod, "datetime", _freeze(quarta_utc))

    iso = date(2026, 8, 5).isocalendar()
    snapshot_novo = {**_SNAPSHOT_BASE, "target_iso_week": f"{iso[0]}-W{iso[1]:02d}"}

    # Reading antiga sem target_iso_week
    r = Reading(
        user_id="fake",
        content_id=CONTENT_SEMANAL,
        product_id="site:plano_lua",
        status="ready",
        title="Previsão antiga",
        input_snapshot=_SNAPSHOT_BASE,  # sem target_iso_week
        body_html="<p>Semana velha.</p>",
    )
    r.created_at = datetime(2026, 7, 29, 12, tzinfo=timezone.utc)

    assert main_module.reading_is_current(r, CONTENT_SEMANAL, snapshot_novo, "pt-BR") is False


# ── calendario_lunar usa fuso local ────────────────────────────────────────

def test_calendario_lunar_ultimo_dia_do_mes_21h_local_ainda_e_current(monkeypatch):
    """21h local no último dia do mês ainda é o mesmo mês → is_current True."""
    import app.horoscope_free as hf_mod

    # 2026-08-31 23:59 BRT = 2026-09-01 02:59 UTC — servidor UTC já virou set
    # Mas local (BRT) ainda é 31/ago. 23:59 BRT = 02:59 UTC +1d
    # Usamos 2026-09-01 00:00 UTC = 2026-08-31 21:00 BRT → ainda agosto local
    utc_dt = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(hf_mod, "datetime", _freeze(utc_dt))
    monkeypatch.setattr(main_module, "datetime", _freeze(utc_dt))

    snapshot = _SNAPSHOT_BASE
    r = Reading(
        user_id="fake",
        content_id=CONTENT_LUNAR,
        product_id="site:plano_lua",
        status="ready",
        title="Calendário Lunar",
        input_snapshot=snapshot,
        body_html="<p>Agosto lunar.</p>",
    )
    # Criado em agosto local (19h BRT = 22h UTC)
    r.created_at = datetime(2026, 8, 15, 22, tzinfo=timezone.utc)

    assert main_module.reading_is_current(r, CONTENT_LUNAR, snapshot, "pt-BR") is True


def test_calendario_lunar_virou_mes_local_nao_e_current(monkeypatch):
    """Quando virou setembro no fuso local, o calendário de agosto não é mais current."""
    import app.horoscope_free as hf_mod

    # 2026-09-01 06:00 UTC = 2026-09-01 03:00 BRT → já setembro local
    utc_dt = datetime(2026, 9, 1, 6, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(hf_mod, "datetime", _freeze(utc_dt))
    monkeypatch.setattr(main_module, "datetime", _freeze(utc_dt))

    snapshot = _SNAPSHOT_BASE
    r = Reading(
        user_id="fake",
        content_id=CONTENT_LUNAR,
        product_id="site:plano_lua",
        status="ready",
        title="Calendário Lunar",
        input_snapshot=snapshot,
        body_html="<p>Agosto lunar.</p>",
    )
    r.created_at = datetime(2026, 8, 15, 22, tzinfo=timezone.utc)

    assert main_module.reading_is_current(r, CONTENT_LUNAR, snapshot, "pt-BR") is False
