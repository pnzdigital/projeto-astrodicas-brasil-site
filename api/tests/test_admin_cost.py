"""Testes do painel de custo (GET /api/admin/cost)."""
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ["ADMIN_PASSWORD"] = "letmein-test"

# Limpa envs de custo para não vazar entre testes
_COST_ENVS = [
    "MINIMAX_USD_PER_1M_INPUT_TOKENS",
    "MINIMAX_USD_PER_1M_OUTPUT_TOKENS",
    "MINIMAX_EST_INPUT_TOKENS_PER_SECTION",
    "MINIMAX_EST_OUTPUT_TOKENS_PER_SECTION",
    "MINIMAX_PLAN_USD_PER_MONTH",
    "COST_USD_BRL_RATE",
]


@pytest.fixture(autouse=True)
def clear_cost_envs(monkeypatch):
    for k in _COST_ENVS:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture()
def cost_client(clean_database):
    from app.admin import router as admin_router

    app = FastAPI()
    app.include_router(admin_router)
    with TestClient(app) as c:
        r = c.post("/api/admin/login", json={"password": "letmein-test"})
        assert r.status_code == 200
        yield c


# ---------------------------------------------------------------------------
# _content_cost_table() — lógica pura
# ---------------------------------------------------------------------------

def test_content_table_has_all_content_ids():
    from app.admin import _content_cost_table
    from app.engine import SECTIONS_BY_CONTENT_ID

    table = _content_cost_table()
    for cid in SECTIONS_BY_CONTENT_ID:
        assert cid in table, f"content_id ausente: {cid}"


def test_content_req_count_matches_sections():
    from app.admin import _content_cost_table
    from app.engine import SECTIONS_BY_CONTENT_ID

    table = _content_cost_table()
    for cid, sections in SECTIONS_BY_CONTENT_ID.items():
        assert table[cid]["req_per_generation"] == len(sections)


def test_default_cost_per_section_approx_003():
    """Estimativa padrão ≈ US$ 0,003/seção (1500 in × 0.30 + 2000 out × 1.20) / 1M."""
    from app.admin import _cost_per_section_usd

    cost = _cost_per_section_usd()
    # (1500*0.30 + 2000*1.20) / 1_000_000 = 2850/1M = 0.00285
    assert abs(cost - 0.00285) < 1e-7


def test_cost_per_section_uses_env_overrides(monkeypatch):
    monkeypatch.setenv("MINIMAX_USD_PER_1M_INPUT_TOKENS", "1.0")
    monkeypatch.setenv("MINIMAX_USD_PER_1M_OUTPUT_TOKENS", "2.0")
    monkeypatch.setenv("MINIMAX_EST_INPUT_TOKENS_PER_SECTION", "1000")
    monkeypatch.setenv("MINIMAX_EST_OUTPUT_TOKENS_PER_SECTION", "1000")
    from app.admin import _cost_per_section_usd

    # (1000*1.0 + 1000*2.0) / 1M = 3000/1M = 0.003
    cost = _cost_per_section_usd()
    assert abs(cost - 0.003) < 1e-9


def test_horoscopo_diario_cost_with_defaults():
    from app.admin import _content_cost_table

    table = _content_cost_table()
    hd = table["site:content:horoscopo_diario"]
    assert hd["req_per_generation"] == 3
    # 3 seções × 0.00285 ≈ 0.00855
    expected = 3 * 0.00285
    assert abs(hd["est_cost_usd_per_generation"] - expected) < 1e-6


# ---------------------------------------------------------------------------
# cost_catalog() — produtos e margem
# ---------------------------------------------------------------------------

def test_diario_astral_recurring_req_count():
    from app.admin import cost_catalog

    data = cost_catalog()
    da = next(p for p in data["products"] if p["product_id"] == "site:diario_astral")
    # horoscopo×30(=90) + guia_mes×1(=8) + previsao_semanal×4(=28) = 126
    assert da["cycle_req_recurring"] == 3 * 30 + 8 * 1 + 7 * 4
    # brinde mapa_astral_completo = 15 req one-time
    assert da["cycle_req_onetime"] == 15


def test_mapa_astral_onetime_only():
    from app.admin import cost_catalog

    data = cost_catalog()
    ma = next(p for p in data["products"] if p["product_id"] == "site:mapa_astral")
    assert ma["cycle_req_recurring"] == 0
    assert ma["cycle_req_onetime"] == 15


def test_diario_astral_30d_token_cost_approx():
    """Custo de 30 dias do Diário Astral ≈ US$ 0,36 com defaults."""
    from app.admin import cost_catalog

    data = cost_catalog()
    da = next(p for p in data["products"] if p["product_id"] == "site:diario_astral")
    # 126 req × 0.00285 = 0.3591  (recurrente)
    # + 15 × 0.00285 = 0.04275 (one-time mapa astral)
    # ≈ 0.40185 total
    total = da["token_cost_usd_total"]
    assert total > 0.30
    assert total < 0.60  # razoável para o produto


def test_margin_computed_when_brl_rate_set(monkeypatch):
    monkeypatch.setenv("COST_USD_BRL_RATE", "5.5")
    from app.admin import cost_catalog

    data = cost_catalog()
    da = next(p for p in data["products"] if p["product_id"] == "site:diario_astral")
    assert da["margin_token_brl"] is not None
    assert da["margin_token_pct"] is not None
    assert da["margin_token_brl"] > 0  # R$ 27,90 vs custo ~R$ 2


def test_margin_none_without_brl_rate():
    from app.admin import cost_catalog

    data = cost_catalog()
    for p in data["products"]:
        assert p["margin_token_brl"] is None


def test_plan_mode_none_without_env():
    from app.admin import cost_catalog

    data = cost_catalog()
    for p in data["products"]:
        assert p["plan_cost_usd_total"] is None


def test_plan_mode_computed_when_env_set(monkeypatch):
    monkeypatch.setenv("MINIMAX_PLAN_USD_PER_MONTH", "100.0")
    from app.admin import cost_catalog

    data = cost_catalog()
    da = next(p for p in data["products"] if p["product_id"] == "site:diario_astral")
    assert da["plan_cost_usd_total"] is not None
    assert da["plan_cost_usd_total"] > 0


def test_effective_params_defaults():
    from app.admin import cost_catalog

    data = cost_catalog()
    ep = data["effective_params"]
    assert ep["usd_per_1m_input"] == 0.30
    assert ep["usd_per_1m_output"] == 1.20
    assert ep["est_input_tokens_per_section"] == 1500
    assert ep["est_output_tokens_per_section"] == 2000
    assert ep["plan_usd_per_month"] is None
    assert ep["usd_brl_rate"] is None


def test_env_configured_flags():
    from app.admin import cost_catalog

    data = cost_catalog()
    ec = data["env_configured"]
    # Sem envs explícitas, os campos de token estão como False (usam defaults)
    assert ec["MINIMAX_PLAN_USD_PER_MONTH"] is False
    assert ec["COST_USD_BRL_RATE"] is False


def test_pricing_note_present():
    from app.admin import cost_catalog

    data = cost_catalog()
    assert "pricing_note" in data
    assert "token" in data["pricing_note"].lower() or "pay-as-you-go" in data["pricing_note"].lower()


# ---------------------------------------------------------------------------
# Rota HTTP
# ---------------------------------------------------------------------------

def test_cost_route_requires_admin():
    from app.admin import router as admin_router

    app = FastAPI()
    app.include_router(admin_router)
    with TestClient(app) as c:
        r = c.get("/api/admin/cost")
    assert r.status_code == 401


def test_cost_route_returns_structure(cost_client):
    r = cost_client.get("/api/admin/cost")
    assert r.status_code == 200
    data = r.json()
    assert "contents" in data
    assert "products" in data
    assert "weekly_quota" in data
    assert "effective_params" in data
    assert "pricing_note" in data
    assert len(data["contents"]) > 0
    assert len(data["products"]) > 0


def test_cost_route_product_has_price_label(cost_client):
    r = cost_client.get("/api/admin/cost")
    data = r.json()
    for p in data["products"]:
        assert "price_label" in p
        assert "price_brl_minor" in p
        assert p["price_brl_minor"] > 0
        assert "token_cost_usd_total" in p


def test_cost_route_content_has_model(cost_client):
    r = cost_client.get("/api/admin/cost")
    data = r.json()
    for c in data["contents"]:
        assert "model" in c
        assert c["model"] in ("MiniMax-M2.7", "MiniMax-M3")
