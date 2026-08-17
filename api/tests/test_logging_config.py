"""Logging da aplicação — TAREFA: log sumia porque nada configurava o root logger.

Contrato:
- configure_logging() com LOG_LEVEL padrão (INFO) deixa logger.info(...) de um
  módulo qualquer da app passar.
- configure_logging() com LOG_LEVEL=WARNING silencia INFO.
"""

from __future__ import annotations

import logging

import pytest

from app.logging_config import configure_logging


@pytest.fixture(autouse=True)
def _reset_root_logger():
    root = logging.getLogger()
    original_level = root.level
    original_handlers = list(root.handlers)
    yield
    root.setLevel(original_level)
    root.handlers[:] = original_handlers


def test_configure_logging_default_lets_info_through(monkeypatch, caplog):
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    configure_logging()

    logger = logging.getLogger("app.checkout")
    with caplog.at_level(logging.INFO):
        logger.info("pedido_confirmado order=abc123")

    assert "pedido_confirmado order=abc123" in caplog.text


def test_configure_logging_log_level_warning_silences_info(monkeypatch, caplog):
    monkeypatch.setenv("LOG_LEVEL", "WARNING")

    configure_logging()

    logger = logging.getLogger("app.checkout")
    with caplog.at_level(logging.NOTSET, logger="app.checkout"):
        logger.info("mensagem_que_nao_deve_aparecer")

    assert "mensagem_que_nao_deve_aparecer" not in caplog.text
    assert logger.getEffectiveLevel() == logging.WARNING
