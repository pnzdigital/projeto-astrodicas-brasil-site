"""Roteamento M3 (cota de tokens/mês) x M2.7 (cota de requisições/semana),
por content_id, na geração seção-a-seção.

Ver /tmp/claude-1000/roteamento-minimax.md para a análise de consumo e a
tabela de roteamento. Regras cobertas aqui:

  (a) content_id "longo" (mapa_astral_completo, mapa_da_carreira,
      guia_do_mes) usa o modelo M3 e o budget M3 (_SECTION_TOKEN_BUDGET_M3).
  (b) content_id normal continua em M2.7 com o budget de sempre, sem
      qualquer mudança de comportamento.
  (c) CONSTRAINT DURA: se M3 devolve corpo vazio (finish_reason=length,
      queimou o budget todo em <think>), a seção cai automaticamente para
      M2.7 antes de esgotar para o fallback editorial — nunca pode ficar
      só no fallback estático quando M2.7 teria funcionado.
  (d) instrumentação de custo/quota registra o modelo usado por chamada.
"""

from datetime import date

from app import engine


class _Profile:
    def __init__(self, birth_date=None, birth_city="São Paulo"):
        self.birth_date = birth_date
        self.birth_time = None
        self.birth_city = birth_city
        self.birth_country = "BR"
        self.birth_timezone = "America/Sao_Paulo"
        self.birth_latitude = None
        self.birth_longitude = None
        self.partner_name = ""
        self.partner_birth_date = None
        self.partner_birth_time = None
        self.partner_birth_city = ""


def _profile():
    return _Profile(date(1990, 3, 15))


def _section_markdown(title: str, subtitle: str) -> str:
    body = (
        f"Primeiro parágrafo sobre {title} considerando o mapa calculado da pessoa. "
        "Um trígono de água conecta a Lua ao Ascendente em Peixes, trazendo sensibilidade.\n\n"
        f"Segundo parágrafo aprofunda {title} com base nas posições reais fornecidas. "
        "Esse movimento fecha o tema com uma orientação prática e concreta."
    )
    return f"## {title}\n### {subtitle}\n{body}"


def test_long_content_ids_estao_no_conjunto_de_roteamento():
    assert engine._LONG_CONTENT_IDS == frozenset({
        "site:content:mapa_astral_completo",
        "site:content:mapa_da_carreira",
        "site:content:guia_do_mes",
    })


def test_content_id_longo_roteia_para_m3_com_budget_m3(monkeypatch):
    """(a) mapa_astral_completo (longo) deve chamar _call_minimax com
    model=MiniMax-M3 (default) e max_tokens=_SECTION_TOKEN_BUDGET_M3."""
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    monkeypatch.setenv("MINIMAX_MODEL_LONG", "MiniMax-M3")
    content_id = "site:content:mapa_astral_completo"
    expected = engine.sections_for(content_id)
    calls = []

    def _fake(prompt, locale="pt-BR", max_tokens=None, timeout=None, model=None, section_label=""):
        calls.append({"model": model, "max_tokens": max_tokens})
        title = section_label
        subtitle = next(s for t, s in expected if t == title)
        return _section_markdown(title, subtitle)

    monkeypatch.setattr(engine, "_call_minimax", _fake)
    result = engine.generate_reading(content_id, "Mapa Astral Completo", _profile())

    assert result.source == "minimax"
    assert len(calls) == len(expected)
    for call in calls:
        assert call["model"] == "MiniMax-M3"
        assert call["max_tokens"] == engine._SECTION_TOKEN_BUDGET_M3


def test_mapa_do_amor_e_seccionado_mas_fora_de_m3(monkeypatch):
    """mapa_do_amor_sinastria é seccionado (paridade com o bot), mas continua
    fora de _LONG_CONTENT_IDS: roteia seção-a-seção pelo M2.7 de sempre, não
    pelo M3 (routing M3 desligado por decisão da dona)."""
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    content_id = "site:content:mapa_do_amor_sinastria"
    assert content_id not in engine._LONG_CONTENT_IDS

    expected = engine.sections_for(content_id, _profile())
    calls = []

    def _fake(prompt, locale="pt-BR", max_tokens=None, timeout=None, model=None, section_label=""):
        calls.append(model)
        subtitle = next(s for t, s in expected if t == section_label)
        return _section_markdown(section_label, subtitle)

    monkeypatch.setattr(engine, "_call_minimax", _fake)
    result = engine.generate_reading(content_id, "Mapa do Amor", _profile())
    assert result.source == "minimax"
    assert len(calls) == len(expected)
    assert all(m != "MiniMax-M3" for m in calls)


def test_carreira_e_guia_do_mes_tambem_roteiam_m3(monkeypatch):
    """(a) confirma que os outros dois content_ids longos da tabela
    (mapa_da_carreira, guia_do_mes) também roteiam para M3, não só
    mapa_astral_completo."""
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    monkeypatch.setenv("MINIMAX_MODEL_LONG", "MiniMax-M3")
    for content_id in ("site:content:mapa_da_carreira", "site:content:guia_do_mes"):
        expected = engine.sections_for(content_id)
        calls = []

        def _fake(prompt, locale="pt-BR", max_tokens=None, timeout=None, model=None, section_label="", _expected=expected, _calls=calls):
            _calls.append(model)
            subtitle = next(s for t, s in _expected if t == section_label)
            return _section_markdown(section_label, subtitle)

        monkeypatch.setattr(engine, "_call_minimax", _fake)
        result = engine.generate_reading(content_id, "Título", _profile())
        assert result.source == "minimax"
        assert calls and all(m == "MiniMax-M3" for m in calls), f"{content_id} devia rotear 100% para M3"


def test_m3_corpo_vazio_cai_automaticamente_para_m2_7(monkeypatch):
    """(c) CONSTRAINT DURA: M3 devolve corpo vazio (RuntimeError da
    guarda de body vazio em _call_minimax) em todas as tentativas
    primárias; a seção deve cair para o modelo de fallback (M2.7) e
    SUCEDER lá, sem nunca precisar do fallback editorial estático."""
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    monkeypatch.setenv("MINIMAX_SECTION_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("MINIMAX_MODEL_LONG", "MiniMax-M3")
    monkeypatch.delenv("MINIMAX_MODEL_FALLBACK", raising=False)
    monkeypatch.setenv("MINIMAX_MODEL", "MiniMax-M2.7")
    content_id = "site:content:mapa_astral_completo"
    expected = engine.sections_for(content_id)
    calls_by_model: dict[str, int] = {}

    def _fake(prompt, locale="pt-BR", max_tokens=None, timeout=None, model=None, section_label=""):
        calls_by_model[model] = calls_by_model.get(model, 0) + 1
        if model == "MiniMax-M3":
            # Reproduz o incidente real: M3 queima o budget todo em <think>
            # e o corpo vem vazio -> _call_minimax levanta RuntimeError.
            raise RuntimeError("Resposta MiniMax vazia")
        title = section_label
        subtitle = next(s for t, s in expected if t == title)
        return _section_markdown(title, subtitle)

    monkeypatch.setattr(engine, "_call_minimax", _fake)
    result = engine.generate_reading(content_id, "Mapa Astral Completo", _profile())

    assert result.source == "minimax", "fallback M2.7 devia ter salvado a leitura sem cair no editorial estático"
    assert len(result.sections) == len(expected)
    assert calls_by_model.get("MiniMax-M3", 0) == len(expected) * 2, "cada seção deveria ter esgotado as 2 tentativas em M3"
    assert calls_by_model.get("MiniMax-M2.7", 0) == len(expected), "cada seção deveria ter sido resolvida no fallback M2.7 na 1ª tentativa"


def test_m3_falha_total_incluindo_fallback_ainda_cai_no_editorial(monkeypatch):
    """Se M3 E o fallback M2.7 falharem os dois, o comportamento antigo
    (fallback editorial pontual por seção) continua valendo — nada quebra."""
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    monkeypatch.setenv("MINIMAX_SECTION_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("MINIMAX_MODEL_LONG", "MiniMax-M3")
    monkeypatch.delenv("MINIMAX_MODEL_FALLBACK", raising=False)
    content_id = "site:content:mapa_astral_completo"

    def _fake(prompt, locale="pt-BR", max_tokens=None, timeout=None, model=None, section_label=""):
        raise RuntimeError("Resposta MiniMax vazia")

    monkeypatch.setattr(engine, "_call_minimax", _fake)
    result = engine.generate_reading(content_id, "Mapa Astral Completo", _profile())

    assert result.source == "fallback"
    assert len(result.sections) == len(engine.sections_for(content_id))


def test_instrumentacao_de_quota_registra_modelo_por_chamada(monkeypatch):
    """(d) smoke test: get_quota_counters() reflete chamadas M3 (tokens) e
    M2.7 (requisições) feitas via _call_minimax (contando de forma real,
    passando pelo caminho real de _record_quota_usage — sem mockar
    _call_minimax aqui)."""
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    before = engine.get_quota_counters()

    class _FakeResponse:
        def __init__(self, payload: bytes):
            self._payload = payload

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    import json as _json

    def _fake_urlopen(request, timeout=None):
        return _FakeResponse(_json.dumps({
            "choices": [{"message": {"content": "Texto de teste."}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 250},
        }).encode())

    monkeypatch.setattr(engine, "urlopen", _fake_urlopen)
    engine._call_minimax("prompt qualquer Identificador: site:content:horoscopo_diario", model="MiniMax-M3")

    after = engine.get_quota_counters()
    assert after["m3_tokens"] == before["m3_tokens"] + 250
    assert after["m2_7_requests"] == before["m2_7_requests"]


def test_sem_env_roteamento_fica_desligado(monkeypatch):
    """Default de produção: sem MINIMAX_MODEL_LONG, conteúdo longo continua
    no modelo de sempre (M2.7). O M3 entregou texto 24% mais curto na
    amostra real, então ligar é decisão explícita por env, nunca por deploy."""
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    monkeypatch.delenv("MINIMAX_MODEL_LONG", raising=False)
    monkeypatch.delenv("MINIMAX_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODEL_TEXT", raising=False)
    content_id = "site:content:mapa_astral_completo"
    expected = engine.sections_for(content_id)
    calls = []

    def _fake(prompt, locale="pt-BR", max_tokens=None, timeout=None, model=None, section_label=""):
        calls.append({"model": model, "max_tokens": max_tokens})
        subtitle = next(s for t, s in expected if t == section_label)
        return _section_markdown(section_label, subtitle)

    monkeypatch.setattr(engine, "_call_minimax", _fake)
    engine.generate_reading(content_id, "Mapa Astral Completo", _profile())

    assert calls, "nenhuma seção gerada"
    for call in calls:
        assert call["model"] == engine._DEFAULT_MODEL, "sem env, não pode escapar pro M3"
        assert call["max_tokens"] == engine._SECTION_TOKEN_BUDGET
