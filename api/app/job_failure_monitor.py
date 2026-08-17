"""Detector de taxa anormal de falha na geração de leituras.

Cenário real que isso cobre: o MiniMax cai (ex.: 3 da manhã), a pré-geração
tenta gerar para todas as clientes ativas, cada job falha depois de esgotar
os `MAX_JOB_ATTEMPTS` retries, e com `HOROSCOPE_FAIL_CLOSED` ligado ninguém
recebe nem o texto genérico de fallback — a Reading fica em "failed" travada
até um admin ou a cliente reclamando notar. Sem isso, uma queda em massa do
provedor de IA é silenciosa: cada falha some individualmente no log, e nada
soma essas falhas para dizer "isso não é uma cliente com problema, é o
provedor inteiro fora do ar".

O que este módulo NÃO faz: não manda e-mail, Slack ou qualquer notificação —
só registra em nível ERROR, com números, no logger do processo do worker.
Ver o `README`/relatório da tarefa para o que falta pra virar alerta de
verdade (rota de e-mail administrativo em `app/mailer.py`).

Estado em memória, por processo: cada processo de worker roda sozinho (ver
`worker_loop`), não há múltiplos workers dividindo essa janela, então uma
janela em memória (sem tabela nova, sem coordenação entre processos) é
suficiente e mais simples que persistir contadores no banco.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import deque

logger = logging.getLogger(__name__)

# Quantos resultados recentes (sucesso ou falha) entram na janela de cálculo
# da taxa. Passado esse tamanho, o resultado mais antigo cai fora — é uma
# janela deslizante, não um contador que só cresce.
WINDOW_SIZE = int(os.getenv("JOB_FAILURE_ALERT_WINDOW", "20"))
# Taxa de falha (0.0-1.0) dentro da janela que dispara o alerta.
ALERT_RATE = float(os.getenv("JOB_FAILURE_ALERT_RATE", "0.5"))
# Não dispara com poucas amostras: 2 falhas em 2 jobs é 100% mas não prova
# nada sobre uma queda em massa do provedor.
MIN_SAMPLES = int(os.getenv("JOB_FAILURE_ALERT_MIN_SAMPLES", "5"))


class JobFailureMonitor:
    """Janela deslizante de resultados de job (sucesso/falha), thread-safe.

    Um job de geração roda numa thread própria (`run_job`, disparada por
    `worker_loop`), então `record_success`/`record_failure` podem ser
    chamados de threads concorrentes — o lock evita que dois jobs terminando
    ao mesmo tempo corrompam a janela ou disparem o alerta duas vezes por
    engano (ver `_alerted_this_streak`).
    """

    def __init__(self, window_size: int = WINDOW_SIZE, alert_rate: float = ALERT_RATE, min_samples: int = MIN_SAMPLES) -> None:
        self._window_size = window_size
        self._alert_rate = alert_rate
        self._min_samples = min_samples
        self._results: deque[bool] = deque(maxlen=window_size)  # True = falhou
        self._last_error: str | None = None
        self._lock = threading.Lock()
        # Evita logar o mesmo alerta a cada falha subsequente enquanto a taxa
        # segue ruim — só reloga quando a taxa melhora e piora de novo.
        self._alerted_this_streak = False

    def reset(self) -> None:
        with self._lock:
            self._results.clear()
            self._last_error = None
            self._alerted_this_streak = False

    def record_success(self) -> None:
        with self._lock:
            self._results.append(False)
            self._alerted_this_streak = False

    def record_failure(self, error: str = "") -> None:
        with self._lock:
            self._results.append(True)
            self._last_error = error
            self._check_and_log_locked()

    def _check_and_log_locked(self) -> None:
        total = len(self._results)
        if total < self._min_samples:
            return
        failures = sum(self._results)
        rate = failures / total
        if rate < self._alert_rate:
            self._alerted_this_streak = False
            return
        if self._alerted_this_streak:
            return
        self._alerted_this_streak = True
        logger.error(
            "ALERTA: taxa de falha anormal na geração de leituras — "
            "%d/%d jobs falharam (%.0f%%) na janela recente. Último erro: %s",
            failures,
            total,
            rate * 100,
            self._last_error or "desconhecido",
        )


# Instância única do processo do worker — mesma lógica de estado global que
# `_LOCK_ID`/`_stop_event` em `worker.py`.
monitor = JobFailureMonitor()
