"""Detector de taxa anormal de falha na geração (app/job_failure_monitor.py).

Cenário coberto: MiniMax cai, N jobs de geração falham em sequência (cada um
já esgotou os retries) — o worker precisa gritar em ERROR com números em vez
de deixar cada falha sumir individualmente no log.
"""

from __future__ import annotations

import logging

from app.job_failure_monitor import JobFailureMonitor


def test_falhas_em_sequencia_disparam_alerta_error(caplog):
    monitor = JobFailureMonitor(window_size=20, alert_rate=0.5, min_samples=5)

    with caplog.at_level(logging.ERROR):
        for i in range(6):
            monitor.record_failure(f"minimax timeout {i}")

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors, "esperava um log ERROR de alerta"
    # dispara já na amostra mínima (5), não relogando a cada falha extra
    # enquanto a taxa continuar ruim — por isso "5/5", não "6/6"
    message = errors[-1].getMessage()
    assert "5/5" in message
    assert "100%" in message
    assert "minimax timeout 4" in message


def test_jobs_saudaveis_nao_disparam_alerta(caplog):
    monitor = JobFailureMonitor(window_size=20, alert_rate=0.5, min_samples=5)

    with caplog.at_level(logging.ERROR):
        for _ in range(10):
            monitor.record_success()

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert not errors


def test_poucas_amostras_nao_disparam_mesmo_com_100_por_cento_de_falha(caplog):
    monitor = JobFailureMonitor(window_size=20, alert_rate=0.5, min_samples=5)

    with caplog.at_level(logging.ERROR):
        monitor.record_failure("erro 1")
        monitor.record_failure("erro 2")

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert not errors, "2 falhas em 2 jobs não deve provar queda em massa"


def test_taxa_baixa_de_falha_nao_dispara():
    monitor = JobFailureMonitor(window_size=20, alert_rate=0.5, min_samples=5)
    logger = logging.getLogger("app.job_failure_monitor")
    triggered = []
    original = logger.error
    logger.error = lambda *a, **kw: triggered.append((a, kw))
    try:
        for _ in range(8):
            monitor.record_success()
        for _ in range(2):
            monitor.record_failure("erro pontual")
    finally:
        logger.error = original

    assert not triggered, "1 em 5 (20%) fica abaixo do limiar de 50% e não deve alertar"


def test_janela_deslizante_reloga_apos_recuperar_e_cair_de_novo(caplog):
    monitor = JobFailureMonitor(window_size=10, alert_rate=0.5, min_samples=5)

    with caplog.at_level(logging.ERROR):
        for i in range(5):
            monitor.record_failure(f"erro {i}")
        first_alert_count = sum(1 for r in caplog.records if r.levelno == logging.ERROR)
        assert first_alert_count == 1

        # segue falhando: não deve duplicar o alerta a cada falha nova
        monitor.record_failure("erro 6")
        assert sum(1 for r in caplog.records if r.levelno == logging.ERROR) == first_alert_count

        # recupera com sucessos suficientes para expulsar as falhas da janela
        for _ in range(10):
            monitor.record_success()

        # e piora de novo -> deve alertar de novo
        for i in range(5):
            monitor.record_failure(f"erro segunda onda {i}")

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 2, "deveria realertar exatamente uma vez numa segunda queda"
