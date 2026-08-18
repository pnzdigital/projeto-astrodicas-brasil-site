"""Teto GLOBAL de chamadas ao MiniMax — o que faltava para escalar.

O teto morava em dois lugares que se multiplicam: MINIMAX_MAX_CONCURRENCY (16
jobs no worker) × MINIMAX_SECTION_POOL_SIZE (4 seções por leitura) = até 64
chamadas simultâneas, e ninguém contava esse produto. Em 18/08/2026 três
regenerações disparadas juntas morreram com erro HTTP repetido em 1,7s.
"""

import io
import json
import threading
import time
from urllib.error import HTTPError

import pytest

from app import engine


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


_OK = json.dumps({"choices": [{"message": {"content": "## T\n### S\n\ncorpo."}, "finish_reason": "stop"}], "usage": {}}).encode()


@pytest.fixture(autouse=True)
def _sem_ritmo_e_sem_cooldown(monkeypatch):
    """Pacing e cooldown têm teste próprio; aqui atrapalhariam o relógio."""
    monkeypatch.setattr(engine, "_MINIMAX_MIN_INTERVAL", 0.0)
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    with engine._MINIMAX_COOLDOWN_LOCK:
        engine._MINIMAX_COOLDOWN_UNTIL[0] = 0.0
    yield
    with engine._MINIMAX_COOLDOWN_LOCK:
        engine._MINIMAX_COOLDOWN_UNTIL[0] = 0.0


def test_nunca_passa_do_teto_de_chamadas_em_voo(monkeypatch):
    em_voo = [0]
    pico = [0]
    trava = threading.Lock()

    def _fake_urlopen(*_args, **_kwargs):
        with trava:
            em_voo[0] += 1
            pico[0] = max(pico[0], em_voo[0])
        time.sleep(0.02)  # segura a vaga tempo suficiente para haver disputa
        with trava:
            em_voo[0] -= 1
        return _FakeResponse(_OK)

    monkeypatch.setattr(engine, "urlopen", _fake_urlopen)

    threads = [threading.Thread(target=lambda: engine._call_minimax("prompt")) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert pico[0] <= engine._MINIMAX_MAX_INFLIGHT, (
        f"{pico[0]} chamadas simultâneas passaram do teto de {engine._MINIMAX_MAX_INFLIGHT}"
    )
    assert pico[0] > 1, "teste inútil se nunca houve concorrência de verdade"


def test_429_fecha_a_porta_para_todas_as_threads(monkeypatch):
    """A recusa é da CONTA, não da chamada: sem cooldown coletivo as outras
    threads tomam o mesmo 429 no mesmo segundo e queimam as tentativas todas."""

    def _quatro_e_vinte_e_nove(*_args, **_kwargs):
        raise HTTPError("https://api.minimax.io/v1/chat/completions", 429, "Too Many", {}, io.BytesIO(b"rate limit"))

    monkeypatch.setattr(engine, "urlopen", _quatro_e_vinte_e_nove)
    monkeypatch.setattr(engine, "_MINIMAX_COOLDOWN_DEFAULT_SECONDS", 30.0)

    with pytest.raises(RuntimeError, match="HTTP 429"):
        engine._call_minimax("prompt")

    with engine._MINIMAX_COOLDOWN_LOCK:
        restante = engine._MINIMAX_COOLDOWN_UNTIL[0] - time.monotonic()
    assert restante > 25, "429 tem que segurar todas as chamadas, não só a que falhou"


def test_retry_after_do_provedor_manda_no_tempo_de_espera(monkeypatch):
    def _com_retry_after(*_args, **_kwargs):
        raise HTTPError(
            "https://api.minimax.io/v1/chat/completions", 429, "Too Many",
            {"Retry-After": "45"}, io.BytesIO(b"rate limit"),
        )

    monkeypatch.setattr(engine, "urlopen", _com_retry_after)
    monkeypatch.setattr(engine, "_MINIMAX_COOLDOWN_DEFAULT_SECONDS", 5.0)

    with pytest.raises(RuntimeError, match="HTTP 429"):
        engine._call_minimax("prompt")

    with engine._MINIMAX_COOLDOWN_LOCK:
        restante = engine._MINIMAX_COOLDOWN_UNTIL[0] - time.monotonic()
    assert restante > 40, "Retry-After do provedor tem precedência sobre o default"


def test_vaga_e_devolvida_quando_a_chamada_explode(monkeypatch):
    """Semáforo vazado trava a geração inteira para sempre — pior que o 429."""

    def _explode(*_args, **_kwargs):
        raise TimeoutError("estourou")

    monkeypatch.setattr(engine, "urlopen", _explode)
    for _ in range(engine._MINIMAX_MAX_INFLIGHT + 3):
        with pytest.raises(RuntimeError):
            engine._call_minimax("prompt")

    assert engine._MINIMAX_GATE.acquire(timeout=2), "vaga não voltou: semáforo vazou"
    engine._MINIMAX_GATE.release()
