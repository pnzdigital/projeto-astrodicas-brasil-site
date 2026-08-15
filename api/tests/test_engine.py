"""Fallback editorial: se o MiniMax falhar ou faltar chave, o cliente nunca fica sem leitura."""

from datetime import date

import pytest

from app import engine


class _Profile:
    def __init__(self, birth_date=None, birth_city=""):
        self.birth_date = birth_date
        self.birth_time = None
        self.birth_city = birth_city
        self.birth_country = "BR"
        self.birth_timezone = "America/Sao_Paulo"
        self.birth_time = None
        self.birth_latitude = None
        self.birth_longitude = None
        self.partner_name = ""
        self.partner_birth_date = None
        self.partner_birth_time = None
        self.partner_birth_city = ""


def test_generate_reading_uses_fallback_when_no_api_key(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    result = engine.generate_reading("site:content:horoscopo_diario", "Horóscopo diário", _Profile(date(1990, 5, 20)))
    assert "<p>" in result.body_html
    assert "Touro" in result.body_html
    assert result.source == "fallback"


def test_generate_reading_falls_back_when_minimax_raises(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.setattr(engine, "_call_minimax", lambda prompt, locale="pt-BR", **kwargs: (_ for _ in ()).throw(RuntimeError("MiniMax indisponível: URLError")))
    result = engine.generate_reading("site:content:horoscopo_diario", "Horóscopo diário", _Profile(date(1990, 5, 20)))
    assert "<p>" in result.body_html
    assert result.source == "fallback"


def test_generate_reading_falls_back_when_minimax_returns_empty(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.setattr(engine, "_call_minimax", lambda prompt, locale="pt-BR", **kwargs: "")
    result = engine.generate_reading("site:content:horoscopo_diario", "Horóscopo diário", _Profile(date(1990, 5, 20)))
    assert "<p>" in result.body_html
    assert result.source == "fallback"


def test_fallback_reading_es_ar_locale():
    result = engine._fallback_reading(_Profile(date(1990, 5, 20), "Buenos Aires"), "es-AR")
    assert "Buenos Aires" in result
    assert result.startswith("<p>")


def test_fallback_reading_es_ar_translates_sun_sign():
    # Bug reproduzido em QA local (2026-08-06): o fallback es-AR embutia o
    # nome do signo em pt-BR ("Escorpião") porque sun_sign() ignorava locale.
    # O guard de idioma (_has_foreign_script) não pega isso: alfabeto latino
    # em ambos os idiomas. Trava aqui, com os pares mais divergentes.
    result = engine._fallback_reading(_Profile(date(1990, 10, 25), "Buenos Aires"), "es-AR")
    assert "Escorpio" in result
    assert "Escorpião" not in result

    result_br = engine._fallback_reading(_Profile(date(1990, 10, 25), "São Paulo"), "pt-BR")
    assert "Escorpião" in result_br


def test_sun_sign_es_ar_uses_spanish_names():
    assert engine.sun_sign(date(2000, 4, 20), "es-AR") == "Tauro"
    assert engine.sun_sign(date(2000, 1, 1), "es-AR") == "Capricornio"
    assert engine.sun_sign(None, "es-AR") == "tu signo solar"


def test_fallback_reading_without_profile():
    result = engine._fallback_reading(None, "pt-BR")
    assert "seu signo solar" in result or "signo solar" in result


class _FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_call_minimax_success_strips_think_tags(monkeypatch):
    import json as json_module
    from unittest.mock import patch

    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    payload = json_module.dumps(
        {"choices": [{"message": {"content": "<think>raciocínio</think>Primeiro parágrafo real."}}]}
    ).encode()
    with patch("app.engine.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _FakeResponse(payload)
        result = engine._call_minimax("prompt qualquer")
    assert "<think>" not in result
    assert "Primeiro parágrafo real." in result


def test_call_minimax_network_error_raises_runtime_error(monkeypatch):
    from unittest.mock import patch
    import urllib.error

    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    with patch("app.engine.urlopen", side_effect=urllib.error.URLError("unreachable")):
        with pytest.raises(RuntimeError, match="MiniMax indisponível"):
            engine._call_minimax("prompt qualquer")


def test_call_minimax_without_content_raises_runtime_error(monkeypatch):
    import json as json_module
    from unittest.mock import patch

    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    with patch("app.engine.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _FakeResponse(json_module.dumps({"choices": []}).encode())
        with pytest.raises(RuntimeError, match="sem conteúdo"):
            engine._call_minimax("prompt qualquer")


def test_call_minimax_without_key_raises(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="MINIMAX_API_KEY"):
        engine._call_minimax("prompt")


def test_sun_sign_boundaries():
    assert engine.sun_sign(date(2000, 4, 20)) == "Touro"
    assert engine.sun_sign(date(2000, 1, 1)) == "Capricórnio"
    assert engine.sun_sign(None) == "seu signo solar"


def test_paragraphs_to_html_strips_think_tags():
    raw = "<think>internal reasoning</think>\nPrimeiro parágrafo.\n\nSegundo parágrafo."
    result = engine._paragraphs_to_html(raw)
    assert "<think>" not in result
    assert result.count("<p>") == 2


# ---------------------------------------------------------------------------
# Seções es-AR
# ---------------------------------------------------------------------------

def test_sections_for_es_ar_horoscopo_returns_spanish_titles():
    secs = engine.sections_for("site:content:horoscopo_diario", locale="es-AR")
    titles = [t for t, _ in secs]
    subtitles = [s for _, s in secs]
    assert "Identificación" in titles
    assert "Dirección Práctica" in titles
    # Nenhum título em português
    assert "Identificação" not in titles
    assert "Direção Prática" not in titles
    # Subtítulos devem casar com _SCOPE_NARROWING (em lowercase)
    assert "El día te refleja" in subtitles
    assert "Lo que pide atención" in subtitles
    assert "Cómo actuar hoy" in subtitles


def test_sections_for_es_ar_mapa_astral_returns_spanish_titles():
    secs = engine.sections_for("site:content:mapa_astral_completo", locale="es-AR")
    assert len(secs) == 15
    titles = [t for t, _ in secs]
    # Planetas sem acento português
    assert "Venus" in titles
    assert "Neptuno" in titles
    assert "Plutón" in titles
    assert "Vênus" not in titles
    assert "Netuno" not in titles
    assert "Plutão" not in titles
    assert "Mensaje Final" in titles
    assert "Mensagem Final" not in titles


def test_sections_for_pt_br_unchanged():
    secs = engine.sections_for("site:content:horoscopo_diario", locale="pt-BR")
    titles = [t for t, _ in secs]
    assert "Identificação" in titles
    assert "Identificación" not in titles


def test_sections_for_default_locale_is_pt_br():
    secs_default = engine.sections_for("site:content:horoscopo_diario")
    secs_pt = engine.sections_for("site:content:horoscopo_diario", locale="pt-BR")
    assert secs_default == secs_pt


def test_sections_for_sinastria_sem_parceiro_es_ar():
    class _NoPartner:
        partner_birth_date = None
    secs = engine.sections_for("site:content:mapa_do_amor_sinastria", profile=_NoPartner(), locale="es-AR")
    titles = [t for t, _ in secs]
    assert "Guía Amorosa Personal" in titles
    assert "Mensaje Final" in titles
    assert "Guia Amoroso Pessoal" not in titles


def test_sections_for_all_es_ar_content_ids_have_translation():
    pt_ids = set(engine.SECTIONS_BY_CONTENT_ID.keys())
    es_ids = set(engine.SECTIONS_BY_CONTENT_ID_ES_AR.keys())
    missing = pt_ids - es_ids
    assert not missing, f"content_ids sem tradução es-AR: {missing}"


def test_sections_for_es_ar_same_length_as_pt_br():
    for cid in engine.SECTIONS_BY_CONTENT_ID:
        pt = engine.sections_for(cid, locale="pt-BR")
        es = engine.sections_for(cid, locale="es-AR")
        assert len(pt) == len(es), f"{cid}: pt={len(pt)} vs es={len(es)}"


def test_scope_narrowing_matches_es_ar_subtitles():
    """Subtítulos es-AR do horóscopo devem casar com _SCOPE_NARROWING (lowercase)."""
    secs = engine.sections_for("site:content:horoscopo_diario", locale="es-AR")
    # Reconstrói o _SCOPE_NARROWING chamando _section_prompt com um subtítulo
    # conhecido e verificando que o default scope NÃO é retornado (indica que
    # a chave foi encontrada). Fazemos isso indiretamente via inspeção do prompt.
    from datetime import date as _date
    context = {"birth_time_assumed": False, "customer_name": "Test", "birth_date": "1990-01-01",
               "birth_time": "12:00", "birth_city": "BsAs", "birth_country": "AR",
               "birth_timezone": "America/Argentina/Buenos_Aires", "sun_sign": "Capricornio",
               "partner_name": "", "partner_birth_date": "", "partner_birth_time": "",
               "partner_birth_city": "", "partner_birth_country": "", "partner_birth_timezone": "",
               "calculated_chart": {}}
    for title, subtitle in secs:
        prompt = engine._section_prompt(
            "site:content:horoscopo_diario", "Diario Astral", title, subtitle,
            1, 3, [t for t, _ in secs], context, "es-AR",
        )
        # Se o narrowing casou, o prompt NÃO contém o texto default de "2 a 3 parágrafos"
        # (que só aparece quando a chave NÃO foi encontrada no dict)
        assert "2 a 3 parágrafos" not in prompt, (
            f"Subtítulo '{subtitle}' não casou com _SCOPE_NARROWING — narrowing inativo"
        )


# ---------------------------------------------------------------------------
# Guard de idioma es-AR reforçado
# ---------------------------------------------------------------------------

def test_has_foreign_words_catches_venus_pt_in_es_ar():
    assert engine._has_foreign_words("conjunción con Vênus natal", "es-AR")


def test_has_foreign_words_catches_mercurio_pt_in_es_ar():
    assert engine._has_foreign_words("Mercúrio retrógrado afecta la comunicación", "es-AR")


def test_has_foreign_words_catches_netuno_pt_in_es_ar():
    assert engine._has_foreign_words("Netuno en la casa 12 trae sensibilidad", "es-AR")


def test_has_foreign_words_catches_plutao_pt_in_es_ar():
    # "Plutão" tem ã → guard de caractere
    assert engine._has_foreign_words("Plutão transita tu casa 8", "es-AR")


def test_has_foreign_words_catches_innecesaria_pt_in_es_ar():
    assert engine._has_foreign_words("tensión innecesária en el vínculo", "es-AR")


def test_has_foreign_words_does_not_reject_correct_spanish():
    # Texto espanhol correto não deve ser reprovado
    clean = "Venus en Tauro ilumina tus vínculos con profundidad y sensualidad natural."
    assert not engine._has_foreign_words(clean, "es-AR")
    clean2 = "Neptuno en Piscis disuelve los límites del ego y amplía la percepción."
    assert not engine._has_foreign_words(clean2, "es-AR")
    clean3 = "Plutón en Capricornio transforma las estructuras más sólidas de tu vida."
    assert not engine._has_foreign_words(clean3, "es-AR")


def test_has_foreign_words_pt_br_unaffected_by_es_ar_rules():
    # "también" vaza em pt-BR mas não em es-AR
    assert engine._has_foreign_words("también es importante", "pt-BR")
    assert not engine._has_foreign_words("también es importante", "es-AR")
    # "Vênus" NÃO deveria ser checado como erro em pt-BR (é PT correto)
    assert not engine._has_foreign_words("Vênus em Touro", "pt-BR")
