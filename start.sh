#!/bin/bash
# Ponto de entrada do container de produção.
# Sobe uvicorn + worker de geração + nginx como processos filhos.
#
# Regra de supervisão: se QUALQUER processo morrer, o container encerra com
# código 1 — a restart policy do Coolify/Docker reinicia o container inteiro,
# garantindo que nunca fique um uvicorn sem worker (ou vice-versa) rodando
# silenciosamente com a stack pela metade.
#
# Graceful shutdown: SIGTERM/INT encaminha kill para os três filhos e espera.
# O worker pode demorar até 5 min para terminar jobs ativos (SIGTERM -> _stop_event).
# Configure o stop_timeout do Coolify para ≥ 330 s.
set -e

_cleanup() {
    echo "[start] Sinal recebido — encerrando processos…" >&2
    kill "$U" "$W" "$N" 2>/dev/null || true
    wait 2>/dev/null || true
}
trap '_cleanup; exit 0' TERM INT

# API (porta 8000, apenas loopback — nginx faz o proxy)
uvicorn app.main:app \
    --host 127.0.0.1 --port 8000 \
    --proxy-headers --forwarded-allow-ips='*' &
U=$!

# Worker de geração (processo separado — não disputa o event loop do uvicorn)
# NÃO use `python -m app.worker`: com -m o módulo vira __main__, e o import
# tardio de main.py dentro de run_job carrega uma SEGUNDA cópia como
# app.worker, re-executando o topo do arquivo dentro de uma thread de job.
# Importando pelo nome real, o módulo é único e o cache do Python resolve.
python -c "from app.worker import worker_loop; worker_loop()" &
W=$!

# Proxy reverso público (porta 80)
nginx -g 'daemon off;' &
N=$!

echo "[start] uvicorn pid=$U  worker pid=$W  nginx pid=$N" >&2

# bash 4.3+: retorna quando QUALQUER filho encerrar
wait -n
STATUS=$?
echo "[start] Um processo encerrou (status $STATUS) — derrubando container" >&2
kill "$U" "$W" "$N" 2>/dev/null || true
wait 2>/dev/null || true
exit 1
