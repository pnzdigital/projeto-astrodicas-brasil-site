"""Configuração central de logging — chamada uma vez por processo.

Sem isso o root logger fica em WARNING por padrão (Python) e todo
`logger.info(...)` dos módulos da app some, mesmo o uvicorn imprimindo
normalmente (ele configura só os loggers dele, não o root). uvicorn e
worker são processos separados (ver start.sh), cada um chama isto no
próprio ponto de entrada.
"""

from __future__ import annotations

import logging
import os


def configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        level = logging.INFO

    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level)
        return

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
