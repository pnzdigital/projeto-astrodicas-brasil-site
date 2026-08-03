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
    assert result.startswith("<p>")
    assert "Touro" in result


def test_generate_reading_falls_back_when_minimax_raises(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.setattr(engine, "_call_minimax", lambda prompt: (_ for _ in ()).throw(RuntimeError("MiniMax indisponível: URLError")))
    result = engine.generate_reading("site:content:horoscopo_diario", "Horóscopo diário", _Profile(date(1990, 5, 20)))
    assert result.startswith("<p>")


def test_generate_reading_falls_back_when_minimax_returns_empty(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.setattr(engine, "_call_minimax", lambda prompt: "")
    result = engine.generate_reading("site:content:horoscopo_diario", "Horóscopo diário", _Profile(date(1990, 5, 20)))
    assert result.startswith("<p>")


def test_fallback_reading_es_ar_locale():
    result = engine._fallback_reading(_Profile(date(1990, 5, 20), "Buenos Aires"), "es-AR")
    assert "Buenos Aires" in result
    assert result.startswith("<p>")


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
