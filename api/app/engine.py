import html
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy import text

from .astrology import astrology_context
from .db import SessionLocal


logger = logging.getLogger(__name__)

# --- Quota instrumentation ------------------------------------------------
#
# Duas cotas distintas na mesma conta MiniMax (ver
# /tmp/claude-1000/roteamento-minimax.md, seção 2): M2.7 é limitada por
# CONTAGEM de requisições/semana (45.000), M3 por VOLUME de tokens/mês (1B).
# Contador em memória (sem banco) só para dar visibilidade de queima de cota
# em log/produção — reinicia a cada deploy, não é fonte de verdade contábil.
_QUOTA_LOCK = threading.Lock()
_QUOTA_COUNTERS = {"m2_7_requests": 0, "m3_tokens": 0, "other_requests": 0, "other_tokens": 0}


def _record_quota_usage(model: str, completion_tokens: int | None) -> None:
    tokens = completion_tokens or 0
    is_m3 = "m3" in (model or "").lower()
    with _QUOTA_LOCK:
        if is_m3:
            _QUOTA_COUNTERS["m3_tokens"] += tokens
            logger.info("minimax_quota bucket=m3_tokens_month delta=%d total=%d", tokens, _QUOTA_COUNTERS["m3_tokens"])
        else:
            _QUOTA_COUNTERS["m2_7_requests"] += 1
            # Rótulo por COTA, não por modelo: o balde é "requisições por semana"
            # e vale para qualquer modelo que não seja o M3 (cobrado em tokens/mês).
            # Dizia "m2_7" e continuou dizendo isso depois da troca para o
            # Text-01 — log que nomeia modelo errado manda o leitor investigar
            # a coisa errada. A chave do dict fica como está para não quebrar
            # quem já lê o snapshot.
            logger.info(
                "minimax_quota bucket=requests_week model=%s delta=1 total=%d",
                model, _QUOTA_COUNTERS["m2_7_requests"],
            )


def get_quota_counters() -> dict:
    """Snapshot dos contadores em memória — exposto para debug/instrumentação
    (não persiste entre deploys/processos)."""
    with _QUOTA_LOCK:
        return dict(_QUOTA_COUNTERS)


def _week_start(when: datetime | None = None) -> date:
    """Segunda-feira 00:00 UTC da semana ISO de ``when`` (default: agora)."""
    moment = when or datetime.now(timezone.utc)
    return (moment - timedelta(days=moment.weekday())).date()


def _persist_quota_usage(model: str, completion_tokens: int | None) -> None:
    """Grava o consumo em ``site_quota_usage`` (UPSERT por semana+modelo).

    CONSTRAINT: geração de conteúdo nunca pode falhar por causa de métrica.
    Qualquer erro de banco aqui vira warning e a chamada segue — perder uma
    linha de instrumentação é aceitável, perder uma venda não é. Chamado de
    dentro do pool de threads de `_generate_reading_sections`: cada chamada
    abre e fecha sua própria sessão/conexão (nunca compartilhada entre
    threads) e o UPSERT atômico evita contenção por leitura-antes-de-escrever.
    """
    tokens = completion_tokens or 0
    week_start = _week_start()
    now = datetime.now(timezone.utc)
    try:
        with SessionLocal() as session:
            session.execute(
                text(
                    """
                    INSERT INTO site_quota_usage (id, week_start, model, request_count, token_count, updated_at)
                    VALUES (:id, :week_start, :model, 1, :tokens, :updated_at)
                    ON CONFLICT (week_start, model) DO UPDATE SET
                        request_count = site_quota_usage.request_count + 1,
                        token_count = site_quota_usage.token_count + :tokens,
                        updated_at = :updated_at
                    """
                ),
                {
                    "id": uuid.uuid4().hex,
                    "week_start": week_start,
                    "model": model or "unknown",
                    "tokens": tokens,
                    "updated_at": now,
                },
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001 - métrica nunca pode derrubar a geração
        logger.warning("quota_usage_persist_failed model=%s error=%s", model, exc)


def get_weekly_quota_snapshot(db=None) -> dict:
    """Consumo persistido da semana ISO corrente, por modelo, contra o teto
    configurável (``MINIMAX_WEEKLY_REQUEST_LIMIT``, default 45000).

    Aceita uma ``Session`` opcional (rota admin já tem uma via Depends); sem
    ela, abre e fecha a própria. Nunca levanta — se o banco falhar, devolve
    consumo zerado com ``available=False`` para o painel sinalizar sem quebrar."""
    limit = int(os.getenv("MINIMAX_WEEKLY_REQUEST_LIMIT", "45000"))
    week_start = _week_start()
    owns_session = db is None
    session = db or SessionLocal()
    try:
        rows = session.execute(
            text("SELECT model, request_count, token_count FROM site_quota_usage WHERE week_start = :week_start"),
            {"week_start": week_start},
        ).fetchall()
        by_model = {
            row.model: {"requests": row.request_count, "tokens": row.token_count}
            for row in rows
        }
        total_requests = sum(v["requests"] for v in by_model.values())
        return {
            "available": True,
            "week_start": week_start.isoformat(),
            "limit": limit,
            "total_requests": total_requests,
            "remaining": max(0, limit - total_requests),
            "percent_used": round((total_requests / limit) * 100, 1) if limit > 0 else 0.0,
            "by_model": by_model,
        }
    except Exception as exc:  # noqa: BLE001 - painel não pode quebrar por causa disso
        logger.warning("quota_snapshot_failed error=%s", exc)
        return {
            "available": False,
            "week_start": week_start.isoformat(),
            "limit": limit,
            "total_requests": 0,
            "remaining": limit,
            "percent_used": 0.0,
            "by_model": {},
        }
    finally:
        if owns_session:
            session.close()

_CONTENT_ID_RE = re.compile(r"Identificador:\s*([\w:]+)")


def _extract_content_id(prompt: str) -> str:
    match = _CONTENT_ID_RE.search(prompt)
    return match.group(1) if match else ""


# Per content-type max_tokens budgets.
#
# Medição real de produção (2026-08-06, 3 mapas via MiniMax) mostrou os budgets
# antigos insuficientes: mapa_astral saiu em 1 parágrafo de 946 palavras (formato
# velho, sem seção), mapa_carreira saiu com 638 palavras TRUNCADAS no meio da
# frase ("transformando-a em motivação para"), mapa_prosperidade caiu em
# fallback. Os dois primeiros são sintoma de max_tokens baixo demais para o
# volume pedido.
#
# Cálculo do budget para os content_ids SECCIONADOS (mapa_astral_completo,
# mapa_da_carreira — ver SECTIONS_BY_CONTENT_ID e o prompt em _prompt()):
#   - conteúdo pedido por seção: 2 a 3 parágrafos de 70 a 110 palavras
#     (ponto médio ~2.5 parágrafos x 90 palavras = 225 palavras/seção)
#   - astral: 15 seções x 225 palavras ≈ 3375 palavras de corpo
#   - carreira: 14 seções x 225 palavras ≈ 3150 palavras de corpo
#   - razão tokens/palavra em pt-BR (acentuação, subword splitting): ~1.6
#     tokens/palavra é a estimativa conservadora usada aqui (inglês puro fica
#     perto de 1.3; pt-BR com acentos e sufixos roda mais alto)
#   - overhead de marcação: cada seção soma "## título" + "### subtítulo" +
#     quebras de linha ≈ 20 tokens extras/seção (15 seções ≈ 300 tokens,
#     14 seções ≈ 280 tokens)
#   - margem de segurança de 20% para o modelo não cortar a última frase
#     antes de bater o limite (é exatamente essa margem que faltava e causou
#     o truncamento observado)
#   astral:   3375 * 1.6 = 5400  + 300  = 5700  * 1.2 ≈ 6840  → arredondado 7000
#   carreira: 3150 * 1.6 = 5040  + 280  = 5320  * 1.2 ≈ 6384  → arredondado 6500
#
# Os demais content_ids continuam no formato de parágrafo corrido (10-14
# parágrafos ~1600-2000 palavras); mapa_do_amor_sinastria e
# mapa_da_prosperidade não entraram nesta medição de truncamento — ficam com
# o valor herdado, mas mapa_da_prosperidade caiu em fallback por vazamento de
# idioma (guard já existente), não por token, então não é resolvido só com
# budget maior; ver relatório.
#
# horoscopo_diario: 1500 → 2500 em 2026-08-12 (commit bb4c964). Mesmo assim
# continuou falhando em produção: log 20:02 e 20:19 mostram completion=2500,
# finish_reason=length, body vazio. O M2.7 expande thinking para preencher o
# budget — 2500 apenas deslocou o limite, não resolveu. Em 2026-08-12 o
# content_id foi migrado para o formato seção-a-seção (SECTIONS_BY_CONTENT_ID);
# o valor abaixo é mantido por histórico mas NUNCA é alcançado na geração
# (caminho de seção usa _SECTION_TOKEN_BUDGET=2500, não TOKEN_BUDGETS).
#
# previsao_semanal: 2400 → 6000. Prompt pede 7 parágrafos (vs. 3 do
# horoscopo_diario); thinking para prompt mais longo é proporcional ou maior.
# Cálculo: 7 × 100 × 1.6 = 1120 tokens de corpo × 1.2 margem = 1344.
# Thinking observado em prompts de similar complexidade: até 2500 tokens.
# Budget seguro: 1344 + 2500 thinking + 20% = ~4600 → arredondado 6000 para
# folga generosa. A cota do M2.7 é por REQUISIÇÃO/semana, não por token —
# aumentar max_tokens custa zero cota. Se 6000 ainda falhar, migrar para
# seção-a-seção (7 seções × 2500 = comprovadamente confiável).
#
# calendario_lunar: 2400 → 6000. Prompt pede 7-9 parágrafos — mesmo cálculo
# e mesma justificativa do previsao_semanal.
#
# guia_dos_retrogrados: 2600 → 6000. Prompt pede 7-9 parágrafos explicando
# planetas retrógrados presentes — conteúdo variável (depende de quais
# planetas estão retrógrados), impossível predefinir seções fixas. Budget 6000
# cobre o pior caso estimado. Se falhar em produção, seção dinâmica com
# Panorama + 1 seção por planeta retrógrado + Conclusão.
#
# Os demais (mapa_*: 7000, 3600, 6500, 3200; guia_do_mes: 2800;
# manual_do_ascendente: 2800) são DEAD CODE: esses content_ids estão em
# SECTIONS_BY_CONTENT_ID e passam pelo caminho seção-a-seção que usa
# _SECTION_TOKEN_BUDGET=2500 por seção. Mantidos aqui para rastreabilidade
# histórica e para o guard de teste (_max_tokens_for ainda é chamado em testes).
TOKEN_BUDGETS = {
    "site:content:horoscopo_diario": 2500,   # dead code — migrado para seção-a-seção
    "site:content:mapa_astral_completo": 7000,  # dead code — seção-a-seção
    "site:content:mapa_do_amor_sinastria": 3600,  # dead code — seção-a-seção
    "site:content:mapa_da_carreira": 6500,   # dead code — seção-a-seção
    "site:content:mapa_da_prosperidade": 3200,  # dead code — seção-a-seção
    "site:content:previsao_semanal": 6000,   # dead code — migrado para seção-a-seção (2026-08-12)
    "site:content:guia_do_mes": 2800,        # dead code — seção-a-seção
    "site:content:calendario_lunar": 6000,   # dead code — migrado para seção-a-seção (2026-08-12)
    "site:content:guia_dos_retrogrados": 6000,  # dead code — migrado para seção-a-seção (2026-08-12)
    "site:content:manual_do_ascendente": 2800,  # dead code — seção-a-seção
}
DEFAULT_TOKEN_BUDGET = 3000


def _max_tokens_for(content_id: str) -> int:
    return TOKEN_BUDGETS.get(content_id, DEFAULT_TOKEN_BUDGET)


# --- Section-by-section generation budgets ------------------------------------
#
# One giant call for all 15 (or 14) sections at once (max_tokens=7000, single
# HTTP request up to MINIMAX_TIMEOUT_SECONDS x MINIMAX_MAX_ATTEMPTS ≈ 6min
# worst case) is what made /generate synchronous and slow. Section-by-section
# generation, run concurrently with a small worker pool, replaces that: each
# call asks for exactly ONE section, with its own retry loop, so a language
# guard rejection or truncation only costs that section's tokens/time — not
# the whole 7000-token document.
#
# Per-section token budget.
#
# O valor original (550) foi calculado só para o corpo do texto e IGNOROU um
# fato do MiniMax-M2.x: são modelos de raciocínio, e o bloco <think>...</think>
# CONTA contra max_tokens — e para M2.x não existe jeito de desligar o
# thinking (confirmado na doc oficial: "For M2.x models, thinking cannot be
# disabled"; a doc recomenda literalmente "if generation stops due to length,
# try increasing max_completion_tokens").
#
# Medido em produção (2026-08-07, 6 chamadas reais de seção via probe direto
# à API com MiniMax-M2.1, mapa_astral_completo): o bloco <think> sozinho
# consumiu de ~700 a ~2130 caracteres (≈530 tokens no pior caso, à razão
# observada de ~4 chars/token em pt-BR) — ou seja, em mais de uma seção o
# raciocínio sozinho já tomou o budget de 550 inteiro, cortando a resposta
# ("finish_reason": "length") antes ou bem no início do corpo. Isso bateu
# exatamente com o padrão visto na regeneração de teste: praticamente TODAS
# as 15 seções truncaram na 1ª tentativa, esgotaram as 2 tentativas e caíram
# no fallback (83% de fallback relatado) — não é falha de rede nem rate limit,
# é o budget nunca ter sobrado para o corpo depois do raciocínio.
#
# Modelo trocado para MiniMax-M2.7 em 2026-08-07 após benchmark de 3 amostras:
#   M2.7 → finish_reason=stop 3/3, 179-254 palavras, 461-1452 completion tokens,
#           zero leak de script CJK/cirílico.
#   M3   → finish_reason=length 3/3, corpo VAZIO, queima os 1800 tokens inteiros
#           em raciocínio — descartado.
#   M2.1 → PROIBIDO: causa raiz do bug de 83% de fallback documentado acima.
#
# Budget 2500: o probe de 15 seções reais (2026-08-07) com M2.7 mostrou que
# 1800 era insuficiente para es-AR — Saturno e Plutão atingiram exatamente
# 1800 completion tokens (finish_reason=length) em todas as 3 tentativas,
# fallback 2/15 = 13.3%. A seção Vênus (es-AR) chegou a 1760 tokens com stop
# — margens de um pixel. Re-testado Saturno e Plutão com max_tokens=2500:
# ambos passaram limpo (Saturno 1154 tokens stop, Plutão 817 tokens stop).
# Budget novo: ~60% de headroom sobre os 1560 tokens do pior caso observado
# → arredondado para 2500.
# 2500 → 5000 em 14/08/2026, com medição (27 chamadas reais, 3 seções × 3 budgets
# × 3 amostras, relatório em /tmp/claude-1000/medicao-thinking.md):
#
#   budget 2500 → 1 corpo vazio em 9 · thinking médio ~805 tokens
#   budget 4000 → 0 vazios          · thinking médio ~629
#   budget 5000 → 0 vazios          · thinking médio ~565
#
# Isso REFUTA o diagnóstico anterior ("M2.7 expande o thinking para preencher o
# budget", registrado acima em TOKEN_BUDGETS): o completion fica em ~700-1300
# tokens seja qual for o teto, e o modelo para sozinho (finish_reason=stop em 21
# das 22 amostras válidas). 2500 não era "teto deslocado" — era teto encostado no
# pior caso do thinking, e quando o raciocínio estourava sozinho os 2500 o corpo
# vinha vazio com finish_reason=length, forçando uma seção inteira de retry com o
# cliente esperando. Observado de novo em produção em 14/08 às 16:37, em três
# content_ids ao mesmo tempo.
#
# Teto maior não custa cota (a do M2.7 é por REQUISIÇÃO) nem latência (que segue
# os tokens realmente gerados) — só remove o piso onde a geração batia.
# Modelo padrão: MiniMax-Text-01, o mesmo que o canal do Telegram usa desde
# sempre — e que nunca teve o problema de idioma que o site tinha.
#
# Medido em 18/08/2026, Mapa Astral Completo, 15 seções por modelo, MESMO prompt:
#   MiniMax-M2.7 → 9 das 15 seções com escrita não-latina no corpo entregue
#     ("模糊ando os contornos", "限制am seu crescimento", "regenerações происходят",
#      "أرض الواقع"), 100% das chamadas gastando tokens em <think>, 348s no total.
#   MiniMax-Text-01 → 0 das 15 (0 em 35 seções contando os dois idiomas),
#     nenhum <think>, 148s no total, 1/4 dos tokens de saída.
# O M2.x é modelo de raciocínio: pensa em vários idiomas dentro do <think> e
# derrapa para o alfabeto do raciocínio no meio da frase em português. Era ISSO
# que enchia a fila de retentativa e empurrava leitura paga para o fallback.
# Nenhuma defesa some por causa disso (guard, retry corretivo e fail_closed
# continuam) — elas só param de ser acionadas o tempo todo.
# Para voltar ao anterior sem tocar em código: MINIMAX_MODEL=MiniMax-M2.7.
_DEFAULT_MODEL = "MiniMax-Text-01"

_SECTION_TOKEN_BUDGET = int(os.getenv("MINIMAX_SECTION_MAX_TOKENS", "5000"))

# Budget per section when primary model is M3. M3 reasoning block (<think>) consumes
# significantly more tokens than M2.7 before producing any body — measured: M3 at 1800
# burned the entire budget in thinking (body empty). 5000 gives ~2500 for reasoning
# + ~2500 for content, matching M2.7 quality at 1000 consumed tokens.
_SECTION_TOKEN_BUDGET_M3 = int(os.getenv("MINIMAX_SECTION_MAX_TOKENS_M3", "5000"))

# Content-ids that benefit from M3 routing: large sectioned output, purchased on-demand
# (low request frequency, high token volume per event).
_LONG_CONTENT_IDS = frozenset({
    "site:content:mapa_astral_completo",
    "site:content:mapa_da_carreira",
    "site:content:guia_do_mes",
})

# Per-section retry.
#
# Depois de corrigir o budget de tokens (ver _SECTION_TOKEN_BUDGET), o log de
# produção (2026-08-07, 2 leituras completas pós-fix) mostrou que truncamento
# sumiu quase por completo, mas o guard de vazamento de script (CJK/cirílico)
# passou a ser a causa dominante de fallback: o modelo pode soltar caractere
# fora do alfabeto latino com frequência notável e independente do budget —
# é estocástico por natureza, não algo que token extra resolve. Com M2.7 o
# benchmark de 3 amostras mostrou zero leak, mas o guard permanece ativo como
# rede de segurança. 2 tentativas ainda deixavam 2 a 4 das 15 seções
# esgotarem (uma má sorte seguida da outra); 3 tentativas reduz essa
# probabilidade sem custo relevante (cada tentativa extra é ~1800 tokens,
# não 7000).
# 10 e não 8 (revisado 17/08/2026, vazamento real em produção: "реали" na
# seção Sol, "добав" na seção Marte, ambos cirílico). Cada tentativa agora
# carrega a correção do erro anterior, então as tentativas extras têm chance
# real de acertar. Custo de requisição não é restrição aqui (decisão da dona,
# textual: "limite de requisição não é problema, o MiniMax tem folga de
# sobra") — perder um mapa pago, sim.
#
# REVISTO em 18/08/2026, de volta para 4. O número alto era curativo: tentativa
# existia porque o M2.7 vazava alfabeto em ~60% das seções. Trocado o modelo
# para MiniMax-Text-01 (ver _DEFAULT_MODEL), o vazamento foi a ZERO em 35 seções
# nos dois idiomas — retentativa passa a cobrir só o que ela deve cobrir: falha
# de rede e azar isolado. Manter 10 aqui esconderia uma regressão futura do
# fornecedor atrás de nove repetições silenciosas, que é exatamente o que
# escondeu essa. Se voltar a errar, o certo é o alerta gritar, não o laço girar.
_SECTION_MAX_ATTEMPTS_DEFAULT = "4"

# Espera entre tentativas quando o fornecedor recusou por volume (429) ou caiu
# (5xx). Índice = número da tentativa que falhou. Curto de propósito: a janela
# da madrugada é apertada, e esperar demais atrasa a leitura que precisa estar
# pronta quando a cliente acorda.
_RETRY_BACKOFF_SECONDS = [1.0, 3.0, 8.0, 15.0]


# ---------------------------------------------------------------------------
# Porteiro do MiniMax: teto GLOBAL de chamadas em voo e ritmo de disparo.
#
# Por que existe: o teto de concorrência morava em dois lugares que se
# MULTIPLICAM — MINIMAX_MAX_CONCURRENCY (16 jobs no worker) vezes
# MINIMAX_SECTION_POOL_SIZE (4 seções por leitura) = até 64 chamadas
# simultâneas ao provedor, e ninguém contava esse produto. Em 18/08/2026 três
# regenerações disparadas juntas derrubaram as três com erro HTTP repetido em
# 1,7s, que é a cara de recusa por volume.
#
# Com 100 clientes e quatro conteúdos cada, esse número só cresce. O teto tem
# que ficar onde a chamada realmente sai, não onde o trabalho é agendado: aqui
# ninguém escapa, venha de job da madrugada, compra na hora ou regeneração
# manual do admin.
#
# NÃO é rate limit da nossa API (isso é ratelimit.py, protege o site de abuso).
# É o contrário: protege o PROVEDOR de nós, para ele não nos recusar.
#
# Limitação conhecida: o contador é do PROCESSO. Hoje o worker é um só
# (ver ARQUITETURA-ESCALA.md, item 2.1), então isso basta. No dia em que rodar
# mais de um worker, este teto precisa virar compartilhado (uma linha no
# Postgres serve; Redis não é necessário para esse volume).
# 8, e não 12 ou 16, porque a evidência aponta baixo: a rajada que derrubou os
# três mapas em 18/08/2026 eram 3 jobs × pool de 4 = ~12 chamadas simultâneas,
# e uma geração sozinha (4 em voo) passou limpa duas vezes seguidas no mesmo
# dia. 16 simultâneas passaram num teste direto contra a API, mas teste que
# passa uma vez não é teto seguro para a madrugada inteira. Subir isto é uma
# variável de ambiente e uma medição; descer depois de perder leitura paga é
# caro. Ver ARQUITETURA-ESCALA.md.
_MINIMAX_MAX_INFLIGHT = max(1, int(os.getenv("MINIMAX_MAX_INFLIGHT", "8")))
# O limite do provedor é RPM — medido em 18/08/2026 contra a conta real, com
# requisições de 1 token para não queimar cota:
#   60 rpm  → 0 recusa em 20
#   120 rpm → 1 recusa em 20
#   180 rpm → 1 recusa em 20
#   240 rpm → 9 recusas em 20
# O erro devolvido é explícito: "rate limit exceeded(RPM) (1002)".
#
# E o balde é da CONTA, não do modelo: com o Text-01 saturado, o M2.7 tomou 429
# nas 6 sondagens seguidas. Trocar de modelo para desviar do limite não
# funciona — só ritmo funciona (ou plano maior / segunda chave).
#
# 60 e não 120: 120 já recusou no teste, e cada recusa em produção custa uma
# tentativa de uma seção de leitura paga. A 60 rpm um Mapa Astral (15 seções)
# leva 15s de piso, e a pré-geração de 100 clientes × 3 seções drena em 5 min —
# folgado dentro da janela da madrugada.
# 60 rpm foi o palpite calibrado no teste isolado, e a produção o desmentiu na
# primeira rajada: 45 chamadas em 91s (~30 rpm de média) ainda tomaram três
# 429. O teto da conta é mais baixo do que a sonda de 20 requisições sugeriu, e
# provavelmente sensível a pico, não só a média.
#
# Por isso o ritmo não é mais um número fixo escolhido por mim: ele se ajusta
# sozinho. Começa no teto configurado, DOBRA o espaçamento a cada recusa por
# volume e volta a acelerar devagar quando o provedor está aceitando. É o
# desenho clássico de controle de congestionamento — desce rápido, sobe devagar
# — e é o que sobrevive a mudança de plano, mudança de limite do fornecedor e
# crescimento de cliente sem ninguém reajustar constante na mão.
_MINIMAX_DEFAULT_MAX_RPM = 60.0
_MINIMAX_MAX_RPM = max(1.0, float(os.getenv("MINIMAX_MAX_RPM", _MINIMAX_DEFAULT_MAX_RPM)))
# Piso de ritmo: nem sob recusa contínua desce abaixo disso, senão a fila da
# madrugada nunca drena e a leitura não fica pronta antes de a cliente acordar.
_MINIMAX_MIN_RPM = max(1.0, float(os.getenv("MINIMAX_MIN_RPM", "6")))
_MINIMAX_BASE_INTERVAL = 60.0 / _MINIMAX_MAX_RPM
_MINIMAX_MAX_INTERVAL = 60.0 / _MINIMAX_MIN_RPM
# Valor VIVO, ajustado em runtime. Módulo mantém o nome antigo porque é o que o
# porteiro lê a cada chamada.
_MINIMAX_MIN_INTERVAL = _MINIMAX_BASE_INTERVAL


def _minimax_slow_down() -> None:
    """Recusa por volume: dobra o espaçamento entre disparos, até o piso."""
    global _MINIMAX_MIN_INTERVAL
    with _MINIMAX_PACE_LOCK:
        antes = _MINIMAX_MIN_INTERVAL
        _MINIMAX_MIN_INTERVAL = min(_MINIMAX_MIN_INTERVAL * 2, _MINIMAX_MAX_INTERVAL)
        mudou = _MINIMAX_MIN_INTERVAL != antes
    if mudou:
        logger.warning(
            "minimax_ritmo diminuiu rpm=%.0f (era %.0f) — provedor recusou por volume",
            60.0 / _MINIMAX_MIN_INTERVAL, 60.0 / antes,
        )


def _minimax_speed_up() -> None:
    """Chamada aceita: recupera 5% do espaçamento, nunca além do teto pedido.

    Devagar de propósito. Voltar de uma vez ao ritmo que acabou de ser recusado
    é reencontrar a mesma recusa no minuto seguinte."""
    global _MINIMAX_MIN_INTERVAL
    with _MINIMAX_PACE_LOCK:
        if _MINIMAX_MIN_INTERVAL <= _MINIMAX_BASE_INTERVAL:
            return
        _MINIMAX_MIN_INTERVAL = max(_MINIMAX_BASE_INTERVAL, _MINIMAX_MIN_INTERVAL * 0.95)
_MINIMAX_GATE = threading.Semaphore(_MINIMAX_MAX_INFLIGHT)
_MINIMAX_PACE_LOCK = threading.Lock()
_MINIMAX_NEXT_SLOT = [0.0]
# Quando o provedor recusa por volume, de nada adianta cada thread descobrir
# isso sozinha: a recusa é da CONTA, não da chamada. Uma thread que toma 429
# fecha a porta para todas até este instante — é o que transforma 64 recusas
# simultâneas em uma espera só.
_MINIMAX_COOLDOWN_UNTIL = [0.0]
# Quanto segurar quando o provedor recusa sem dizer por quanto tempo (sem
# Retry-After). Curto: a janela da madrugada é apertada e a fila drena sozinha.
_MINIMAX_COOLDOWN_DEFAULT_SECONDS = float(os.getenv("MINIMAX_COOLDOWN_SECONDS", "20"))
_MINIMAX_COOLDOWN_LOCK = threading.Lock()


def _minimax_cooldown(seconds: float) -> None:
    """Fecha a porta para TODAS as threads por ``seconds``."""
    until = time.monotonic() + seconds
    with _MINIMAX_COOLDOWN_LOCK:
        if until > _MINIMAX_COOLDOWN_UNTIL[0]:
            _MINIMAX_COOLDOWN_UNTIL[0] = until
            logger.warning(
                "minimax_cooldown segundos=%.1f — provedor recusou por volume; segurando todas as chamadas",
                seconds,
            )


class _MinimaxGate:
    """Context manager: espera a vez, respeita o cooldown e o ritmo."""

    def __enter__(self):
        _MINIMAX_GATE.acquire()
        try:
            while True:
                with _MINIMAX_COOLDOWN_LOCK:
                    falta = _MINIMAX_COOLDOWN_UNTIL[0] - time.monotonic()
                if falta <= 0:
                    break
                time.sleep(min(falta, 5.0))
            # Espaçamento entre disparos: sem isto as 12 vagas saem no mesmo
            # milissegundo e o provedor vê um pico, não um fluxo.
            with _MINIMAX_PACE_LOCK:
                agora = time.monotonic()
                alvo = max(agora, _MINIMAX_NEXT_SLOT[0])
                _MINIMAX_NEXT_SLOT[0] = alvo + _MINIMAX_MIN_INTERVAL
            atraso = alvo - agora
            if atraso > 0:
                time.sleep(atraso)
        except BaseException:
            _MINIMAX_GATE.release()
            raise
        return self

    def __exit__(self, *_exc):
        _MINIMAX_GATE.release()
        return False

# Per-section timeout: medição de 13/08 (27 chamadas reais) mostrou mediana de
# 22s e cauda de 40s para chamadas que completam. 40s cobre todas as respostas
# válidas observadas e falha 20s mais rápido quando a API trava (timeout real,
# não chamada lenta): a economia é 20s × 18,5% × 3 seções/horóscopo ≈ 11s por
# leitura no pior caso. 60s → 40s não gera falsos negativos porque nenhuma
# chamada que retornou conteúdo demorou mais de 40s nos dados coletados.
_SECTION_TIMEOUT_SECONDS_DEFAULT = "40"

# Quantos timeouts consecutivos numa mesma seção antes de desistir do modelo
# atual e passar para o fallback de modelo (ou fallback editorial). Cada timeout
# consecutivo é evidência de que a API está indisponível naquele momento — vale
# menos tentar mais 3× do que falhar rápido. 2 é o mínimo que distingue "fluke
# de rede" (1 timeout isolado) de "API down" (2+ seguidos).
_SECTION_TIMEOUT_CONSECUTIVE_LIMIT_DEFAULT = "2"

# Pool limitado: gerar as 15 seções em paralelo sem limite sobrecarregaria a
# API do MiniMax (rate limit) e o processo local; 4 workers equilibra tempo
# total (15 seções / 4 ≈ 4 rodadas) contra concorrência seguro.
_SECTION_POOL_SIZE_DEFAULT = "4"


# Seções exatas por content_id, portadas de astrodicas-telegram/src/vendas_bot/
# mapa_premium.py (`_SECOES_POR_TIPO["astral"]`) para manter o MESMO produto
# nos dois canais (bot e site). Cada tupla é (título canônico, subtítulo).
#
# horoscopo_diario foi migrado para o formato seccionado em 2026-08-12.
# Motivação: o formato de chamada única esgotava o budget em thinking mesmo a
# 2500 tokens (confirmado em produção: finish_reason=length em 2/3 tentativas,
# body vazio, fallback editorial entregue a cliente pagante). O formato
# seção-a-seção resolve: cada seção é uma tarefa simples (1 parágrafo), o
# bloco <think>…</think> do M2.7 é proporcionalmente menor e o budget de 2500
# por seção passa confortavelmente (mesmo padrão observado nos mapas premium).
# As 3 seções espelham a estrutura já documentada no legacy_rules do prompt:
# identificação emocional → relações/trabalho → direção prática.
SECTIONS_BY_CONTENT_ID: dict[str, list[tuple[str, str]]] = {
    "site:content:horoscopo_diario": [
        ("Identificação", "O dia reflete você"),
        ("Relações e Trabalho", "O que cobra atenção"),
        ("Direção Prática", "Como agir hoje"),
    ],
    "site:content:mapa_astral_completo": [
        ("Introdução", "Seu mapa de alma"),
        ("Sol", "Identidade e propósito"),
        ("Lua", "Emoções e segurança"),
        ("Ascendente", "Como o mundo te vê"),
        ("Mercúrio", "Mente e comunicação"),
        ("Vênus", "Afeto, prazer e valores"),
        ("Marte", "Ação e coragem"),
        ("Júpiter", "Expansão e fé"),
        ("Saturno", "Limite e construção"),
        ("Urano", "Mudança e liberdade"),
        ("Netuno", "Sensibilidade e visão"),
        ("Plutão", "Transformação profunda"),
        ("Casas Astrológicas", "Áreas da vida"),
        ("Aspectos", "Conversa entre planetas"),
        ("Mensagem Final", "Seu caminho"),
    ],
    # Portado de _SECOES_POR_TIPO["carreira"] no bot. Entrou nesta lista porque
    # a medição real (2026-08-06) mostrou o mesmo defeito do mapa_astral_completo
    # antigo: 638 palavras em 1 parágrafo único, truncado no meio da frase.
    "site:content:mapa_da_carreira": [
        ("Introdução à Carreira", "Propósito em ação"),
        ("Vocação Central", "Onde você brilha"),
        ("Talentos Naturais", "Forças de base"),
        ("Mercúrio Profissional", "Mente e comunicação"),
        ("Marte na Carreira", "Execução e ritmo"),
        ("Júpiter Profissional", "Expansão e oportunidades"),
        ("Saturno Profissional", "Estrutura e legado"),
        ("Imagem e Autoridade", "Reputação no mercado"),
        ("Dinheiro e Valor", "Remuneração justa"),
        ("Ambiente de Trabalho", "Onde rende melhor"),
        ("Parcerias e Networking", "Alianças inteligentes"),
        ("Desafios Recorrentes", "Pontos de atenção"),
        ("Plano de Evolução", "Próximos ciclos"),
        ("Mensagem Final", "Carreira com alma"),
    ],
    # Guia do Mês: "os movimentos astrais que vêm" — trânsitos reais do mês
    # calculado contra o mapa natal (context["calculated_chart"]["transits_to_natal"]
    # e ["current_sky"]), não um mês-modelo genérico de revista. Sem seções o
    # guia caía num único parágrafo de ~900 palavras (mesmo defeito medido em
    # mapa_astral_completo); seccionado, cada bloco tem piso de conteúdo próprio.
    "site:content:guia_do_mes": [
        ("Panorama do Mês", "O clima geral"),
        ("Sol do Mês", "Onde a luz aponta"),
        ("Vínculos e Afeto", "Vênus e Marte no seu mapa"),
        ("Comunicação e Decisões", "Mercúrio em ação"),
        ("Trânsitos que Pedem Atenção", "O que cobra ajuste"),
        ("Semanas do Mês", "Quando cada movimento pesa mais"),
        ("Área Sensível", "Onde o cuidado rende mais"),
        ("Mensagem Final", "Como atravessar o mês"),
    ],
    # Portado de _SECOES_POR_TIPO["prosperidade"] no bot. Site vendia isto no
    # formato de parágrafo corrido (8-11 parágrafos ~2500 palavras) contra as
    # 14 seções do mesmo produto no bot (~4500+ palavras) — mesmo produto
    # pago, metade do conteúdo. Ver sections_for() para a escolha de variante.
    "site:content:mapa_da_prosperidade": [
        ("Introdução à Prosperidade", "Abundância integral"),
        ("Relação com Dinheiro", "Crença e comportamento"),
        ("Júpiter Financeiro", "Onde expandir"),
        ("Saturno Financeiro", "Base e proteção"),
        ("Vênus e Valor", "Preço, prazer e equilíbrio"),
        ("Marte e Ação", "Como gerar renda"),
        ("Diversificação", "Múltiplas fontes"),
        ("Reserva e Segurança", "Estabilidade emocional e financeira"),
        ("Padrões de Escassez", "O que cortar"),
        ("Prosperidade e Propósito", "Dinheiro com sentido"),
        ("Parcerias de Crescimento", "Quem soma"),
        ("Ciclos e Timing", "Quando acelerar"),
        ("Plano de Abundância", "Prática mensal"),
        ("Mensagem Final", "Você em fluxo"),
    ],
    # Manual do Ascendente: não veio do bot (produto novo, sem equivalente em
    # astrodicas-telegram/src/vendas_bot/mapa_premium.py), então não é um
    # porte — seções montadas do zero a partir do que o produto realmente
    # cobre (Ascendente calculado, regente, presença, corpo, primeira
    # impressão, casas). Entrou aqui em 2026-08-09 para igualar o mesmo
    # pacote de R$97 que já traz Mapa do Amor e Mapa da Prosperidade
    # seccionados — antes disso o Ascendente vinha em parágrafo corrido
    # (8-10 parágrafos), entrega desigual dentro do mesmo preço. A regra
    # antiga de "8 a 10 parágrafos" em ``legacy_rules`` foi removida (contradiz
    # o formato seccionado); a honestidade "sem Ascendente calculado, explique
    # que falta hora de nascimento" continua garantida por
    # ``_assumed_warning_text``, que se aplica a toda leitura seccionada, não
    # só a esta. NÃO entra em ``_LONG_CONTENT_IDS`` (decisão da dona: roteamento
    # M3 fica desligado para este content_id de propósito).
    "site:content:manual_do_ascendente": [
        ("Introdução ao Ascendente", "O que é e por que importa"),
        ("Ascendente Calculado", "Seu signo no horizonte"),
        ("Regente do Ascendente", "O planeta que comanda sua vida"),
        ("Presença e Postura", "Como você entra numa sala"),
        ("Corpo e Vitalidade", "Energia física e saúde"),
        ("Primeira Impressão", "O que os outros veem primeiro"),
        ("Máscara Social", "Persona e essência"),
        ("Casa 1 em Ação", "Identidade em movimento"),
        ("Casas Angulares", "A estrutura da sua vida"),
        ("Ascendente nos Relacionamentos", "Como você se aproxima"),
        ("Ascendente na Carreira", "Imagem profissional"),
        ("Desafios do Ascendente", "Pontos de tensão a observar"),
        ("Mensagem Final", "Integrando persona e essência"),
    ],
    # previsao_semanal: 7 parágrafos no prompt original (1 panorama + 6 temas/
    # decisões da semana). Migrado de chamada única com TOKEN_BUDGET=6000 para
    # seção-a-seção em 2026-08-12 porque M2.7 expande thinking para preencher o
    # budget disponível — 6000 teria o mesmo destino de horoscopo_diario@2500:
    # thinking ocupa tudo, corpo vazio, finish_reason=length. 7 seções × 2500
    # = comprovadamente confiável (mesmo mecanismo dos mapas).
    "site:content:previsao_semanal": [
        ("Panorama da Semana", "O clima geral dos 7 dias"),
        ("Segunda e Terça", "Início de semana"),
        ("Quarta e Quinta", "Virada da semana"),
        ("Sexta a Domingo", "Encerramento e recarga"),
        ("Área de Atenção", "O que cobra ajuste"),
        ("Oportunidade da Semana", "Onde agir com convicção"),
        ("Mensagem Final", "Como atravessar esta semana"),
    ],
    # calendario_lunar: 7-9 parágrafos no prompt original. Mesmo argumento do
    # previsao_semanal — chamada única com budget grande é não-confiável com M2.7.
    # Seções modelam as quatro fases + trânsito lunar + prática concreta.
    "site:content:calendario_lunar": [
        ("Panorama do Ciclo", "O ritmo lunar do mês"),
        ("Lua Nova", "Semear intenções"),
        ("Lua Crescente", "Construir e expandir"),
        ("Lua Cheia", "Iluminar e colher"),
        ("Lua Minguante", "Liberar e revisar"),
        ("Lua em Trânsito", "Quando o ritmo toca seu mapa"),
        ("Como Usar o Calendário", "Prática mensal concreta"),
    ],
    # guia_dos_retrogrados: 7-9 parágrafos no prompt original. Mesma migração.
    # "Conteúdo variável (depende de quais planetas estão retrógrados)" não é
    # obstáculo — o modelo já lida com isso em todas as seções de mapa natal
    # (ex.: casas sem planetas). A seção "Retrógrados no Céu Atual" recebe o
    # calculated_chart completo e usa só o que está retrógrado; se nada está
    # retrógrado, declara isso com linguagem acolhedora.
    "site:content:guia_dos_retrogrados": [
        ("O que é Retrógrado", "Movimento e significado"),
        ("Retrógrados no Céu Atual", "O que está em revisão agora"),
        ("Impacto no Seu Mapa", "Como esses movimentos tocam seus pontos"),
        ("Área de Vida em Revisão", "O que está sendo reexaminado"),
        ("Como Navegar os Retrógrados", "Prática sem fatalismo"),
        ("Timing de Retomada", "Quando os planetas ficam diretos"),
        ("Mensagem Final", "Revisão como evolução"),
    ],
    # Portado de _SECOES_POR_TIPO["sinastria"] no bot (variante COM dados do
    # parceiro completos). Ver SINASTRIA_SEM_PARCEIRO_SECTIONS para a variante
    # sem parceiro e sections_for() para a escolha entre as duas.
    "site:content:mapa_do_amor_sinastria": [
        ("Introdução à Sinastria", "A dança de duas almas"),
        ("Vênus em Compatibilidade", "Estilo de amar"),
        ("Lua em Compatibilidade", "Segurança emocional"),
        ("Marte e Química", "Desejo, impulso e erotismo"),
        ("Mercúrio e Diálogo", "Como vocês se entendem"),
        ("Júpiter no Casal", "Expansão e bênçãos"),
        ("Saturno no Casal", "Compromisso e maturidade"),
        ("Netuno no Amor", "Encanto e ilusão"),
        ("Plutão e Transformação", "Intensidade do vínculo"),
        ("Casas Ativadas", "Áreas da vida em destaque"),
        ("Pontos de Atrito", "Diferença como evolução"),
        ("Padrões Kármicos", "O que se repete no amor"),
        ("Potencial de Construção", "Projeto de vida a dois"),
        ("Mensagem Final", "Amor com consciência"),
    ],
}

# Portado de _SECOES_POR_TIPO["sinastria_sem"] no bot (variante SEM dados do
# parceiro). Não entra em SECTIONS_BY_CONTENT_ID porque o site usa o MESMO
# content_id ("site:content:mapa_do_amor_sinastria") para as duas variantes —
# a escolha depende do perfil (profile.partner_birth_date), feita em
# sections_for(). Manter separado evita inventar posições planetárias do
# parceiro quando o cliente não informou os dados dele (regra já existia em
# `rules` no ``_prompt``, agora também vale para a lista de seções).
SINASTRIA_SEM_PARCEIRO_SECTIONS: list[tuple[str, str]] = [
    ("Guia Amoroso Pessoal", "Seu mapa sem parceiro"),
    ("Seu Estilo de Amar", "Vênus pessoal"),
    ("Necessidades Emocionais", "Lua pessoal"),
    ("Desejo e Magnetismo", "Marte pessoal"),
    ("Comunicação no Amor", "Mercúrio pessoal"),
    ("Padrões de Repetição", "O que observar"),
    ("Parceiro Compatível", "Perfil que soma"),
    ("Limites Saudáveis", "Amor sem autoabandono"),
    ("Autocuidado Afetivo", "Base da estabilidade"),
    ("Janelas Favoráveis", "Ciclos de abertura"),
    ("Cura de Feridas", "Quíron no amor"),
    ("Amor e Propósito", "Relação que expande"),
    ("Preparação Consciente", "Como atrair melhor"),
    ("Mensagem Final", "Seu coração com direção"),
]

_SINASTRIA_CONTENT_ID = "site:content:mapa_do_amor_sinastria"

# Versões en español rioplatense de todas as seções seccionadas.
# Mantidas separadas do dict pt-BR para não quebrar chamada existente (locale
# default continua pt-BR). Subtítulos do horoscopo_diario DEVEM casar com as
# chaves de _SCOPE_NARROWING em _section_prompt (comparação em lowercase):
#   "El día te refleja"  → "el día te refleja"   (chave já existia)
#   "Lo que pide atención" → "lo que pide atención" (chave já existia)
#   "Cómo actuar hoy"   → "cómo actuar hoy"      (chave adicionada em _section_prompt)
SECTIONS_BY_CONTENT_ID_ES_AR: dict[str, list[tuple[str, str]]] = {
    "site:content:horoscopo_diario": [
        ("Identificación", "El día te refleja"),
        ("Vínculos y Trabajo", "Lo que pide atención"),
        ("Dirección Práctica", "Cómo actuar hoy"),
    ],
    "site:content:mapa_astral_completo": [
        ("Introducción", "Tu mapa de alma"),
        ("Sol", "Identidad y propósito"),
        ("Luna", "Emociones y seguridad"),
        ("Ascendente", "Cómo te ve el mundo"),
        ("Mercurio", "Mente y comunicación"),
        ("Venus", "Afecto, placer y valores"),
        ("Marte", "Acción y coraje"),
        ("Júpiter", "Expansión y fe"),
        ("Saturno", "Límite y construcción"),
        ("Urano", "Cambio y libertad"),
        ("Neptuno", "Sensibilidad y visión"),
        ("Plutón", "Transformación profunda"),
        ("Casas Astrológicas", "Áreas de la vida"),
        ("Aspectos", "Diálogo entre planetas"),
        ("Mensaje Final", "Tu camino"),
    ],
    "site:content:mapa_da_carreira": [
        ("Introducción a la Carrera", "Propósito en acción"),
        ("Vocación Central", "Donde brillás"),
        ("Talentos Naturales", "Fortalezas de base"),
        ("Mercurio Profesional", "Mente y comunicación"),
        ("Marte en la Carrera", "Ejecución y ritmo"),
        ("Júpiter Profesional", "Expansión y oportunidades"),
        ("Saturno Profesional", "Estructura y legado"),
        ("Imagen y Autoridad", "Reputación en el mercado"),
        ("Dinero y Valor", "Remuneración justa"),
        ("Ambiente de Trabajo", "Donde rendís mejor"),
        ("Alianzas y Networking", "Alianzas inteligentes"),
        ("Desafíos Recurrentes", "Puntos de atención"),
        ("Plan de Evolución", "Próximos ciclos"),
        ("Mensaje Final", "Carrera con alma"),
    ],
    "site:content:guia_do_mes": [
        ("Panorama del Mes", "El clima general"),
        ("Sol del Mes", "Hacia dónde apunta la luz"),
        ("Vínculos y Afecto", "Venus y Marte en tu mapa"),
        ("Comunicación y Decisiones", "Mercurio en acción"),
        ("Tránsitos que Piden Atención", "Lo que pide ajuste"),
        ("Semanas del Mes", "Cuándo cada movimiento pesa más"),
        ("Área Sensible", "Donde el cuidado rinde más"),
        ("Mensaje Final", "Cómo atravesar el mes"),
    ],
    "site:content:mapa_da_prosperidade": [
        ("Introducción a la Prosperidad", "Abundancia integral"),
        ("Relación con el Dinero", "Creencia y comportamiento"),
        ("Júpiter Financiero", "Dónde expandir"),
        ("Saturno Financiero", "Base y protección"),
        ("Venus y Valor", "Precio, placer y equilibrio"),
        ("Marte y Acción", "Cómo generar ingresos"),
        ("Diversificación", "Múltiples fuentes"),
        ("Reserva y Seguridad", "Estabilidad emocional y financiera"),
        ("Patrones de Escasez", "Qué cortar"),
        ("Prosperidad y Propósito", "Dinero con sentido"),
        ("Alianzas de Crecimiento", "Quién suma"),
        ("Ciclos y Timing", "Cuándo acelerar"),
        ("Plan de Abundancia", "Práctica mensual"),
        ("Mensaje Final", "Vos en flujo"),
    ],
    "site:content:manual_do_ascendente": [
        ("Introducción al Ascendente", "Qué es y por qué importa"),
        ("Ascendente Calculado", "Tu signo en el horizonte"),
        ("Regente del Ascendente", "El planeta que gobierna tu vida"),
        ("Presencia y Postura", "Cómo entrás a un espacio"),
        ("Cuerpo y Vitalidad", "Energía física y salud"),
        ("Primera Impresión", "Lo que los demás ven primero"),
        ("Máscara Social", "Persona y esencia"),
        ("Casa 1 en Acción", "Identidad en movimiento"),
        ("Casas Angulares", "La estructura de tu vida"),
        ("Ascendente en los Vínculos", "Cómo te acercás"),
        ("Ascendente en la Carrera", "Imagen profesional"),
        ("Desafíos del Ascendente", "Puntos de tensión a observar"),
        ("Mensaje Final", "Integrando persona y esencia"),
    ],
    "site:content:previsao_semanal": [
        ("Panorama de la Semana", "El clima general de los 7 días"),
        ("Lunes y Martes", "Inicio de semana"),
        ("Miércoles y Jueves", "Quiebre de semana"),
        ("Viernes a Domingo", "Cierre y recarga"),
        ("Área de Atención", "Lo que pide ajuste"),
        ("Oportunidad de la Semana", "Dónde actuar con convicción"),
        ("Mensaje Final", "Cómo atravesar esta semana"),
    ],
    "site:content:calendario_lunar": [
        ("Panorama del Ciclo", "El ritmo lunar del mes"),
        ("Luna Nueva", "Sembrar intenciones"),
        ("Luna Creciente", "Construir y expandir"),
        ("Luna Llena", "Iluminar y cosechar"),
        ("Luna Menguante", "Liberar y revisar"),
        ("Luna en Tránsito", "Cuando el ritmo toca tu mapa"),
        ("Cómo Usar el Calendario", "Práctica mensual concreta"),
    ],
    "site:content:guia_dos_retrogrados": [
        ("Qué es Retrógrado", "Movimiento y significado"),
        ("Retrógrados en el Cielo Actual", "Lo que está en revisión ahora"),
        ("Impacto en Tu Mapa", "Cómo estos movimientos tocan tus puntos"),
        ("Área de Vida en Revisión", "Lo que está siendo reexaminado"),
        ("Cómo Navegar los Retrógrados", "Práctica sin fatalismo"),
        ("Timing de Retomada", "Cuándo los planetas se vuelven directos"),
        ("Mensaje Final", "La revisión como evolución"),
    ],
    "site:content:mapa_do_amor_sinastria": [
        ("Introducción a la Sinastría", "La danza de dos almas"),
        ("Venus en Compatibilidad", "Estilo de amar"),
        ("Luna en Compatibilidad", "Seguridad emocional"),
        ("Marte y Química", "Deseo, impulso y erotismo"),
        ("Mercurio y Diálogo", "Cómo se entienden"),
        ("Júpiter en la Pareja", "Expansión y bendiciones"),
        ("Saturno en la Pareja", "Compromiso y madurez"),
        ("Neptuno en el Amor", "Encanto e ilusión"),
        ("Plutón y Transformación", "Intensidad del vínculo"),
        ("Casas Activadas", "Áreas de la vida en destaque"),
        ("Puntos de Fricción", "La diferencia como evolución"),
        ("Patrones Kármicos", "Lo que se repite en el amor"),
        ("Potencial de Construcción", "Proyecto de vida en pareja"),
        ("Mensaje Final", "Amor con conciencia"),
    ],
}

SINASTRIA_SEM_PARCEIRO_SECTIONS_ES_AR: list[tuple[str, str]] = [
    ("Guía Amorosa Personal", "Tu mapa sin pareja"),
    ("Tu Estilo de Amar", "Venus personal"),
    ("Necesidades Emocionales", "Luna personal"),
    ("Deseo y Magnetismo", "Marte personal"),
    ("Comunicación en el Amor", "Mercurio personal"),
    ("Patrones de Repetición", "Lo que observar"),
    ("Pareja Compatible", "Perfil que suma"),
    ("Límites Saludables", "Amor sin autoabandono"),
    ("Autocuidado Afectivo", "Base de la estabilidad"),
    ("Ventanas Favorables", "Ciclos de apertura"),
    ("Sanación de Heridas", "Quirón en el amor"),
    ("Amor y Propósito", "Vínculo que expande"),
    ("Preparación Consciente", "Cómo atraer mejor"),
    ("Mensaje Final", "Tu corazón con dirección"),
]


def sections_for(content_id: str, profile=None, locale: str = "pt-BR") -> list[tuple[str, str]]:
    if content_id == _SINASTRIA_CONTENT_ID and not (profile and getattr(profile, "partner_birth_date", None)):
        if locale == "es-AR":
            return SINASTRIA_SEM_PARCEIRO_SECTIONS_ES_AR
        return SINASTRIA_SEM_PARCEIRO_SECTIONS
    if locale == "es-AR":
        return SECTIONS_BY_CONTENT_ID_ES_AR.get(content_id, SECTIONS_BY_CONTENT_ID.get(content_id, []))
    return SECTIONS_BY_CONTENT_ID.get(content_id, [])


@dataclass
class ReadingResult:
    """Public result of ``generate_reading``.

    The source flag is the contract that makes the fallback honest:
    - ``minimax`` → the buyer's premium paid reading, generated live.
    - ``fallback`` → a generic editorial template, NOT a personalized reading.
      Callers MUST surface this to the buyer (recommended: clear notice +
      offer to retry / contact support) instead of presenting it as if it
      were the paid personalized reading.

    O ``birth_time_assumed`` espelha o mesmo flag do chart da prévia grátis
    (commit 913fcd8): quando a hora não veio, assumimos 00:00 e marcamos aqui
    para que a UI renderize o aviso ao lado do Ascendente calculado. Sem isso,
    o cliente pagaria pela leitura completa e veria um Ascendente "de verdade"
    que na verdade é estimado. ``ascendant_warning`` carrega o texto cru e
    localizado (pt-BR / es-AR) que a UI cola no bloco do Ascendente — mesmo
    texto que já vinha da prévia grátis.
    """

    body_html: str
    source: str  # "fallback" | "minimax"
    warning: str = ""
    birth_time_assumed: bool = False
    ascendant_warning: dict[str, str] | None = None
    # Lista de {"title", "subtitle", "order", "content"} quando o content_id
    # tem seções definidas em SECTIONS_BY_CONTENT_ID; vazio para os content_ids
    # que ainda usam o formato de parágrafo corrido antigo.
    sections: list[dict] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.sections is None:
            self.sections = []


# Backwards-compat shim: existing callers that used ``result.startswith("<p>")``
# still work because ``ReadingResult`` implements ``__str__`` to return the
# body. To detect the source, callers should use ``isinstance(result, ReadingResult)``.


# Nomes de signo em pt-BR (chave usada internamente e no prompt do LLM) mapeados
# para o equivalente em es-AR. O fallback editorial embute esse nome diretamente
# no texto entregue ao cliente, então precisa estar no idioma certo — um nome em
# português dentro de uma leitura es-AR é o mesmo tipo de vazamento de idioma que
# o _has_foreign_script tenta pegar, só que em alfabeto latino (não detectável
# por aquele guard).
SIGN_NAMES_ES_AR = {
    "Aquário": "Acuario", "Peixes": "Piscis", "Áries": "Aries",
    "Touro": "Tauro", "Gêmeos": "Géminis", "Câncer": "Cáncer",
    "Leão": "Leo", "Virgem": "Virgo", "Libra": "Libra",
    "Escorpião": "Escorpio", "Sagitário": "Sagitario", "Capricórnio": "Capricornio",
}


def sun_sign(birth_date: date | None, locale: str = "pt-BR") -> str:
    if not birth_date:
        return "tu signo solar" if locale == "es-AR" else "seu signo solar"
    month_day = (birth_date.month, birth_date.day)
    signs = [
        ((1, 20), "Aquário"), ((2, 19), "Peixes"), ((3, 21), "Áries"),
        ((4, 20), "Touro"), ((5, 21), "Gêmeos"), ((6, 21), "Câncer"),
        ((7, 23), "Leão"), ((8, 23), "Virgem"), ((9, 23), "Libra"),
        ((10, 23), "Escorpião"), ((11, 22), "Sagitário"), ((12, 22), "Capricórnio"),
    ]
    name = "Capricórnio"
    for boundary, sign in reversed(signs):
        if month_day >= boundary:
            name = sign
            break
    if locale == "es-AR":
        return SIGN_NAMES_ES_AR.get(name, name)
    return name


def _profile_context(profile, customer_name: str = "") -> dict:
    # Mesmo flag da prévia grátis: True quando a hora não veio e a API
    # assumiu 00:00 só pra montar o Ascendente. O prompt do LLM precisa saber
    # disso para não afirmar o Ascendente com certeza.
    approximate_time = bool(profile is None or getattr(profile, "birth_time", None) is None)
    return {
        "customer_name": customer_name or "não informado",
        "birth_date": profile.birth_date.isoformat() if profile and profile.birth_date else "não informado",
        "birth_time": profile.birth_time.isoformat() if profile and profile.birth_time else "não informado",
        "birth_city": profile.birth_city if profile and profile.birth_city else "não informado",
        "birth_country": profile.birth_country if profile else "não informado",
        "birth_timezone": profile.birth_timezone if profile else "não informado",
        "birth_time_assumed": approximate_time,
        "sun_sign": sun_sign(profile.birth_date if profile else None),
        "partner_name": profile.partner_name if profile and profile.partner_name else "não informado",
        "partner_birth_date": profile.partner_birth_date.isoformat() if profile and profile.partner_birth_date else "não informado",
        "partner_birth_time": profile.partner_birth_time.isoformat() if profile and profile.partner_birth_time else "não informado",
        "partner_birth_city": profile.partner_birth_city if profile and profile.partner_birth_city else "não informado",
        "partner_birth_country": getattr(profile, "partner_country", "") or "não informado",
        "partner_birth_timezone": getattr(profile, "partner_birth_timezone", "") or "não informado",
        "calculated_chart": astrology_context(profile),
    }


# Seções em que o aviso de hora assumida faz sentido: são as que falam do
# Ascendente (o dado que muda de signo a cada ~2h). Nas outras catorze o aviso
# só ocupa espaço — e pior, CONVIDA meta-texto: medido em 18/08/2026, a seção
# "Sol" gastou o terceiro parágrafo inteiro explicando que o Ascendente não pôde
# ser calculado, em vez de interpretar o Sol. O aviso vira ruído no lugar do
# produto.
_ASCENDANT_SECTION_MARKERS = ("ascendente", "ascendiente", "como o mundo te vê", "cómo te ve el mundo", "introdu", "introducción")


def _assumed_warning_text(locale: str, birth_time_assumed: bool, section_title: str | None = None) -> str:
    """Aviso injetado no prompt quando a hora de nascimento foi assumida.

    Extraído de ``_prompt`` para ser reaproveitado por ``_section_prompt``
    (geração seção-a-seção) sem duplicar o texto.

    ``section_title`` só é passado no caminho seção-a-seção: ali o aviso vai
    apenas para as seções que realmente falam do Ascendente (ver
    ``_ASCENDANT_SECTION_MARKERS``). Sem ele — caminho do documento inteiro —
    o comportamento é o de sempre.
    """
    if not birth_time_assumed:
        return ""
    if section_title is not None:
        alvo = section_title.strip().lower()
        if not any(marker in alvo for marker in _ASCENDANT_SECTION_MARKERS):
            return ""
    if locale == "es-AR":
        return (
            "\n\nATENCIÓN: la hora de nacimiento NO fue informada. El backend asumió 00:00 "
            "solo para poder calcular y mostrar el Ascendente. El Ascendente es el dato más "
            "sensible a la hora en toda la carta (cambia de signo cada ~2h), así que el "
            "Ascendente calculado es una ESTIMACIÓN y probablemente NO es el Ascendente real "
            "del cliente. Cuando hables del Ascendente, declara explícitamente que es estimado "
            "y que podría cambiar si la hora real fuera otra. No lo afirmes como hecho."
        )
    return (
        "\n\nATENÇÃO: a hora de nascimento NÃO foi informada. O backend assumiu 00:00 "
        "apenas para conseguir calcular e mostrar o Ascendente. O Ascendente é o dado "
        "mais sensível à hora no mapa inteiro (troca de signo a cada ~2h), então o "
        "Ascendente calculado é uma ESTIMATIVA e provavelmente NÃO é o Ascendente real "
        "do cliente. Ao falar do Ascendente, declare explicitamente que é estimado e "
        "que pode mudar se a hora real for outra. Não afirme como fato."
    )


def _language_lock_text(locale: str) -> str:
    """Regra de idioma injetada no fim do prompt. Extraído de ``_prompt`` para
    ser reaproveitado por ``_section_prompt`` sem duplicar o texto."""
    language = "espanhol rioplatense natural" if locale == "es-AR" else "português brasileiro natural"
    return (
        "\n\nREGRA DE IDIOMA (crítica, produto pago): escreva do início ao fim estritamente em "
        f"{language}. Isto é uma redação, não uma tradução — pense e escreva direto nesse idioma, "
        "nunca alterne para outro. Proibido usar qualquer palavra em inglês (ex.: 'synthesize', "
        "'nonetheless', 'enthusiasm', 'highlighted', 'harmonic relationships') "
        + (
            "ou em espanhol (ex.: 'intercambio', 'manifestarse', 'también')"
            if locale != "es-AR"
            else "ou em português"
        )
        + " no meio da frase. Termos técnicos de astrologia (Ascendente, retrógrado, orbe, sextil, "
        "trígono, quadratura, nomes de signo) seguem sempre a grafia do idioma da leitura. Revise "
        "mentalmente cada frase antes de escrevê-la: se uma palavra não é claramente desse idioma, troque-a. "
        "Use exclusivamente caracteres do alfabeto latino: nenhum ideograma (chinês, japonês, coreano) "
        "e nenhuma outra escrita (cirílico, árabe, hebraico, grego, tailandês, devanágari) pode aparecer "
        "no texto, nem solto no meio de uma palavra."
    )


def _prompt(content_id: str, title: str, profile, locale: str, customer_name: str = "") -> str:
    context = _profile_context(profile, customer_name)
    language = "espanhol rioplatense natural" if locale == "es-AR" else "português brasileiro natural"
    today = date.today().isoformat()
    # Quando a hora foi assumida (cliente não sabia), instruímos o LLM a tratar
    # o Ascendente como dado estimado: o ponto do mapa mais sensível à hora
    # (troca de signo a cada ~2h). Sem esse aviso no prompt, o texto pago
    # afirmaria o Ascendente com certeza que não tem — bug comercial.
    assumed_warning = _assumed_warning_text(locale, bool(context.get("birth_time_assumed")))
    sections = sections_for(content_id, profile, locale)
    if sections:
        lista_secoes = "\n".join(f"{i:02d}. ## {t} — {s}" for i, (t, s) in enumerate(sections, 1))
        # Aviso de parceiro incompleto: regra herdada do antigo content_rule de
        # parágrafo corrido de mapa_do_amor_sinastria (não pode ficar duplicada
        # em `rules`, senão as duas instruções brigam no prompt — ver histórico
        # do bug em git blame). Só se aplica quando sections == variante sem
        # parceiro, detectável comparando com a lista dedicada.
        partner_caution = (
            " Os dados do parceiro estão incompletos: não invente posições planetárias dele; "
            "esta leitura foca só no mapa do cliente."
            if content_id == _SINASTRIA_CONTENT_ID and sections is SINASTRIA_SEM_PARCEIRO_SECTIONS
            else ""
        )
        content_rule = (
            "Escreva uma leitura natal premium ESTRUTURADA EM SEÇÕES. Responda em markdown, "
            "uma seção por vez, EXATAMENTE nesta ordem e com estes títulos:\n"
            + lista_secoes
            + "\n\nFormato obrigatório de cada seção:\n## <título exato da lista acima>\n"
            "### <subtítulo exato da lista acima>\n<2 a 3 parágrafos de 70 a 110 palavras cada, "
            "separados por linha em branco, cobrindo o tema da seção com base apenas no "
            "calculated_chart>\n\nNão pule nenhuma seção da lista, não invente seções extras e "
            "não troque a ordem. Use apenas posições presentes no calculated_chart. TERMINE "
            "cada frase e cada seção de forma completa — nunca corte uma frase no meio; se estiver "
            "perto do limite, feche a frase atual e encerre a seção em vez de continuar."
            + partner_caution
        )
    else:
        # Regras de parágrafo corrido — só para content_ids QUE NÃO ESTÃO em
        # SECTIONS_BY_CONTENT_ID (nem na variante sem-parceiro da sinastria).
        # horoscopo_diario, mapa_do_amor_sinastria e mapa_da_prosperidade saíram
        # daqui quando entraram seccionados; NÃO reintroduzir suas chaves nesta
        # tabela — um dict.update por cima do content_rule seccionado reativa a
        # contradição de duas instruções de formato no mesmo prompt.
        content_rule = "Escreva uma leitura premium profunda, com 7 a 10 parágrafos."
    language_lock = _language_lock_text(locale)
    markdown_rule = (
        "Responda no formato markdown seccionado pedido acima (## título / ### subtítulo / parágrafos)."
        if sections
        else "Não use markdown, listas, HTML ou título; devolva apenas os parágrafos, separados por uma linha em branco."
    )
    return f"""Você é a astróloga editorial da AstroDicas. Produza a leitura \"{title}\" em {language}.
Data de referência: {today}. Identificador: {content_id}.
Dados autorizados do cliente: {json.dumps(context, ensure_ascii=False)}.

{content_rule}
Use o nome do cliente com naturalidade no máximo duas vezes. Use somente os dados fornecidos. Não invente
Ascendente, Lua, casas, aspectos, trânsitos ou posições planetárias que não tenham sido calculados. Quando faltar
cálculo astronômico, declare a limitação com linguagem acolhedora. Não faça diagnóstico médico, promessa financeira nem previsão
fatalista. Não cite inteligência artificial. {markdown_rule}{language_lock}{assumed_warning}"""


# Um caractere fora do alfabeto latino no meio de uma leitura paga destrói a
# credibilidade do produto inteiro. O MiniMax-M2.1 (modelo anterior, PROIBIDO)
# trocava uma palavra solta pelo equivalente em chinês, árabe ou russo algumas
# vezes por texto — validado em 2026-08-05 sobre 4 leituras reais: "a natureza
# já حساسة do Ascendente", "sugere que成长 pessoal", "estar стимулируя mudanças".
# MiniMax-M2.7 (modelo atual) não exibiu leak nas 3 amostras do benchmark de
# 2026-08-07, mas o guard permanece ativo: é estocástico. Não é falha de
# encoding (o UTF-8 chega íntegro), é o modelo derrapando de idioma.
#
# Permitimos ASCII, Latin-1 suplementar e Latin Extended-A (cobre pt-BR e
# es-AR), mais a pontuação tipográfica que o modelo usa legitimamente (aspas
# curvas, travessão, reticências). Qualquer outra coisa reprova o texto.
_ALLOWED_TEXT = re.compile(r"^[\x09\x0a\x0d\x20-\x7e\xa0-ſ‐-‧‰-⁞]*$")


def _has_foreign_script(text: str) -> bool:
    """True quando o texto tem caractere fora do alfabeto latino esperado."""
    return not _ALLOWED_TEXT.match(text)


def _foreign_sample(text: str, limit: int = 5) -> str:
    """Os caracteres reprovados, para o log dizer o que exatamente derrapou."""
    seen: list[str] = []
    for char in text:
        if not _ALLOWED_TEXT.match(char) and char not in seen:
            seen.append(char)
            if len(seen) >= limit:
                break
    return "".join(seen)


# Quantos caracteres estrangeiros ainda contam como "ruído" e não como "outro
# idioma". Um ideograma solto no meio de um parágrafo pt-BR impecável é lapso
# de tokenização do MiniMax; um texto inteiro em cirílico é outra coisa.
# O limite absoluto é quem decide na prática (uma seção real tem centenas de
# caracteres); o relativo é rede de segurança para respostas muito curtas,
# onde 3 caracteres estrangeiros já seriam parte grande do texto.
_FOREIGN_NOISE_MAX_CHARS = 3
_FOREIGN_NOISE_MAX_RATIO = 0.02


def _sanitize_foreign_script(text: str) -> tuple[str, bool]:
    """Corta caractere estrangeiro isolado em vez de jogar a seção fora.

    Motivo (15/08/2026): o laço de `_generate_section` descartava a seção
    inteira por UM ideograma e refazia com o prompt idêntico — cinco vezes,
    até cair em fallback. Perder um mapa pago por um caractere de ruído é
    caro; perder o idioma é inaceitável. Então cortamos só o ruído.

    Retorna ``(texto, aproveitável)``. Quando ``aproveitável`` é False o
    chamador deve refazer a seção: é outro idioma, não ruído.
    """
    if not _has_foreign_script(text):
        return text, True
    estrangeiros = [c for c in text if not _ALLOWED_TEXT.match(c)]
    total = len(estrangeiros)
    if total > _FOREIGN_NOISE_MAX_CHARS or total > max(1, len(text)) * _FOREIGN_NOISE_MAX_RATIO:
        return text, False
    limpo = "".join(c for c in text if _ALLOWED_TEXT.match(c))
    # O corte deixa espaço duplo quando o caractere estava cercado de espaços.
    limpo = re.sub(r"[ \t]{2,}", " ", limpo)
    limpo = re.sub(r" +([,.;:!?])", r"\1", limpo)
    return limpo.strip(), True


# Segundo guard, complementar ao de cima. _has_foreign_script só pega
# alfabeto errado (cirílico, CJK, árabe) — mas o MiniMax também derrapa
# TROCANDO uma palavra solta por inglês ou espanhol, mantendo alfabeto latino
# ("synthesize", "nonetheless", "enthusiasm", "intercambio", "manifestarse").
# Isso passa batido no guard de script porque são letras latinas normais.
#
# Estratégia: lista curada e pequena de palavras que só existem no idioma
# "errado" e não têm uso legítimo em texto astrológico pt-BR/es-AR. Termos
# técnicos latinos do domínio (orbe, sextil, trígono, quadratura, Ascendente,
# retrógrado, nomes de signo) NÃO entram nessa lista — se entrassem, todo
# texto bom seria reprovado e a taxa de entrega despencaria. O custo dos dois
# lados do erro:
#   - falso negativo (lista curta demais): alguma palavra estrangeira rara
#     escapa e some no texto entregue — ruim, mas já reduzido pelas 3
#     tentativas de regeneração e cobre os casos reais observados.
#   - falso positivo (lista agressiva demais): um texto bom é descartado e
#     regenerado à toa, ou pior, cai no fallback genérico sem necessidade —
#     por isso a lista fica deliberadamente pequena e específica, sem radicais
#     curtos nem palavras que colidem com termos astrológicos ou nomes.
#
# Palavras em inglês reprovam em qualquer locale (nunca são texto legítimo
# aqui). Palavras "só-espanhol" só reprovam quando o locale pedido é pt-BR —
# em es-AR, espanhol é o idioma correto.
_ENGLISH_LEAK_WORDS = frozenset(
    {
        "synthesize", "synthesizes", "synthesizing",
        "nonetheless", "enthusiasm", "enthusiastic",
        "highlighted", "highlights", "highlight",
        "harmonic", "relationship", "relationships",
        "however", "therefore", "moreover", "overall",
        "insight", "insights", "throughout", "meanwhile",
        "although", "whereas", "regarding",
        # Observado em produção (2026-08-07, probe de seção 'Sol'): "posição
        # deste astro essential revela..." — grafia inglesa ("essential") no
        # lugar do português "essencial", não pega no guard de script (letras
        # latinas) e passava batido porque não estava na lista.
        "essential", "essentially",
        # Observado em produção (2026-08-17, Previsão Semanal pt-BR): "Vênus
        # em Libra current traz um alívio..." e "A Lua passing pelo seu
        # cielo...". "current"/"passing" são função gramatical (adjetivo/
        # gerúndio) sem uso legítimo em prosa pt-BR/es-AR — sem colisão com
        # termo astrológico.
        "current", "passing", "through", "during",
        "energy", "journey", "chart", "house", "sign",
    }
)

_SPANISH_ONLY_LEAK_WORDS = frozenset(
    {
        "intercambio", "manifestarse", "tambien", "también",
        "aunque", "sino", "segun", "según", "asimismo",
        "ademas", "además", "porque no",
        # Mesmo caso de produção acima: "cielo" (ES: céu) dentro de frase
        # pt-BR. "energía"/"nacimiento" só existem em ES (pt-BR: "energia"
        # sem acento, "nascimento") — acento/grafia isolam do falso positivo.
        "cielo", "hacia", "siempre", "energía", "nacimiento",
    }
)

# Defeitos pontuais já observados em produção que não são troca de idioma,
# mas token corrompido/malformado (nem pt-BR nem nenhum outro idioma válido).
# Documentado à parte porque a causa é outra (o modelo "gagueja" um sufixo),
# mas o efeito no cliente pagante é o mesmo: texto macarrônico. Tratamos como
# reprovação para forçar regeneração.
_KNOWN_GARBLED_TOKENS = frozenset({"urgeências"})

# Palavras portuguesas que vazam em leituras es-AR. Só inclui palavras que
# (a) não existem em espanhol legítimo e (b) foram observadas em produção ou
# são risco óbvio (nomes de planetas que diferem entre os idiomas).
# "Vênus" e "Plutão" têm ê/ã — capturados pelo guard de caracteres abaixo.
# "Mercúrio" (ES: Mercurio) e "Netuno" (ES: Neptuno) não têm ã/õ/ê, então
# precisam de entrada explícita aqui. "innecesária" foi observado em produção
# (acento português onde o espanhol não usa).
_PORTUGUESE_LEAK_IN_ES_AR = frozenset({
    "mercúrio",
    "netuno",
    "innecesária",
    # "você" já tem ê (guard de caractere pega sozinho) mas "céu" tem só é
    # (não capturado pelo guard ãõê) e não existe em espanhol.
    # "nascimento"/"seu"/"sua" não têm acento nenhum, precisam de entrada
    # explícita — não colidem com palavra espanhola nem termo astrológico.
    # NÃO inclui "através": "a través" é espanhol legítimo (mesma grafia do
    # português sem o acento na primeira sílaba) — entraria como falso
    # positivo em texto es-AR correto.
    "céu", "nascimento", "seu", "sua",
})

# Caracteres tipicamente portugueses que NUNCA aparecem em texto espanhol
# correto: ã (til sobre a), õ (til sobre o), ê (circunflexo sobre e).
# Qualquer ocorrência num texto es-AR é sinal de vazamento do modelo em PT.
# Regex de caractere único — rápido e sem risco de falso positivo.
_PORTUGUESE_CHARS_IN_ES_RE = re.compile(r"[ãõê]")


def _foreign_word_regex(locale: str) -> re.Pattern:
    words = set(_ENGLISH_LEAK_WORDS) | _KNOWN_GARBLED_TOKENS
    if locale != "es-AR":
        words |= _SPANISH_ONLY_LEAK_WORDS
    if locale == "es-AR":
        words |= _PORTUGUESE_LEAK_IN_ES_AR
    pattern = r"\b(?:" + "|".join(re.escape(word) for word in words) + r")\b"
    return re.compile(pattern, re.IGNORECASE)


def _has_foreign_words(text: str, locale: str = "pt-BR") -> bool:
    """True quando alguma palavra do texto vaza de outro idioma (alfabeto latino).

    Para es-AR inclui: caracteres ã/õ/ê (tipicamente portugueses, nunca
    espanhóis) e nomes de planetas em PT que diferem do ES.
    """
    if locale == "es-AR" and _PORTUGUESE_CHARS_IN_ES_RE.search(text):
        return True
    return _foreign_word_regex(locale).search(text) is not None


def _foreign_word_sample(text: str, locale: str = "pt-BR", limit: int = 5) -> str:
    matches = _foreign_word_regex(locale).findall(text)
    seen: list[str] = []
    for match in matches:
        if match not in seen:
            seen.append(match)
            if len(seen) >= limit:
                break
    return ", ".join(seen)


def _has_language_leak(text: str, locale: str = "pt-BR") -> bool:
    """Guard combinado: script errado (CJK/cirílico/árabe) OU palavra vazando de outro idioma."""
    return _has_foreign_script(text) or _has_foreign_words(text, locale)


# Guard de RECUSA/META-TEXTO (achado de QA, 20 gerações no commit 8a79304,
# previsao_semanal pt-BR): o modelo devolveu uma recusa em primeira pessoa
# ("não posso ajudar com isso") e ela foi persistida e entregue como leitura
# paga. Nenhum guard existente pega esse caso: fail_closed só cobre o
# fallback editorial nosso, o guard de script só pega alfabeto errado, o guard
# de palavra vazada só pega troca de idioma — a recusa está em pt-BR/es-AR
# corretos, alfabeto latino correto, só que fala SOBRE a tarefa em vez de
# executá-la.
#
# Falso positivo é o risco real aqui, não o falso negativo: uma leitura
# legítima PODE conter "não posso prever o futuro" como ressalva editorial
# (o próprio prompt pede "declare a limitação com linguagem acolhedora"), e
# "sinto" aparece o tempo todo em sentido astrológico ("você sinte que...").
# Duas defesas contra isso:
#   1. Os padrões de recusa exigem um VERBO de recusa específico logo depois
#      ("não posso ajudar/continuar/gerar/escrever/atender/cumprir/fazer
#      isso"), nunca "não posso" sozinho — "não posso prever o futuro" não
#      bate porque "prever" não está na lista de verbos de recusa.
#   2. Os padrões de meta-comentário ("como IA", "sinto muito, mas", "peço
#      desculpas") só são checados no INÍCIO da seção (primeiros ~400
#      caracteres) — é onde uma recusa real aparece (ela abre a resposta
#      recusando; ela não recusa no parágrafo 3 depois de já ter escrito a
#      leitura). Isso também blinda "sinto" em sentido astrológico, que
#      aparece espalhado pelo texto, não como abertura de frase.
_REFUSAL_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bn[ãa]o posso (ajudar|continuar|prosseguir|realizar|gerar|escrever|criar|atender|cumprir|fazer isso|responder a isso)\b",
        r"\bno puedo (ayudar|continuar|proceguir|realizar|generar|escribir|crear|atender|cumplir|hacer esto|responder a esto)\b",
        r"\bn[ãa]o sou capaz de\b",
        r"\bno soy capaz de\b",
        r"\bcomo (um |uma )?(modelo de linguagem|intelig[êe]ncia artificial|ia)\b",
        r"\bcomo (un |una )?(modelo de lenguaje|inteligencia artificial|ia)\b",
        r"\bcomo assistente( de ia)?\b",
        r"\bcomo asistente( de ia)?\b",
        r"\bsinto muito,? mas\b",
        r"\blo siento,? pero\b",
        r"\bdesculpe,? (mas )?n[ãa]o (posso|consigo)\b",
        r"\bdisculp[ae],? (pero )?no (puedo|consigo)\b",
        r"\bperd[óo]n,? (pero )?no (puedo|consigo)\b",
        r"\bn[ãa]o tenho informa[çc][õo]es suficientes\b",
        r"\bno tengo informaci[óo]n suficiente\b",
        r"\bpe[çc]o desculpas\b",
        r"\bpido disculpas\b",
        # pedido de mais dados ao usuário em vez de executar a tarefa
        r"\bpreciso que voc[êe] (me )?(informe|forne[çc]a|diga|especifique)\b",
        r"\bnecesito que (me )?(proporciones|indiques|especifiques)\b",
        r"\bpoder(ia|iam) (me )?(fornecer|informar) mais (dados|informa[çc][õo]es)\b",
        r"\bpodr[íi]a(s)? (me )?(proporcionar|indicar) m[áa]s (datos|informaci[óo]n)\b",
    ]
]
# Quanto do início da seção é checado pelos padrões de meta-comentário. Uma
# recusa real recusa já na primeira frase; texto legítimo pode mencionar
# "sinto" ou "não posso [ressalva]" mais adiante sem risco de falso positivo.
_REFUSAL_HEAD_CHARS = 400


def _looks_like_refusal(text: str) -> bool:
    """True quando o INÍCIO do texto tem cara de recusa/meta-comentário do assistente."""
    head = (text or "").strip()[:_REFUSAL_HEAD_CHARS]
    return any(p.search(head) for p in _REFUSAL_PATTERNS)


# Piso de tamanho: uma recusa curta ("Desculpe, não posso ajudar com isso.")
# passa pelos guards de idioma (alfabeto e palavras corretos) e pode até
# escapar do guard de padrão acima se for uma formulação não prevista — mas
# ela SEMPRE é muito mais curta que uma seção real. O prompt pede 2 a 3
# parágrafos de 70 a 110 palavras cada (`_section_prompt`/`scope_instruction`),
# ou seja, no mínimo ~140 palavras de corpo. Fixamos o piso bem abaixo disso
# (não em ~140 palavras) porque seções legítimas às vezes saem mais enxutas
# sem serem recusa — o piso é rede de segurança contra corpo anormalmente
# curto, não um verificador de contagem de parágrafo. 100 caracteres cobre
# frases de recusa típicas (30-90 caracteres) com folga e ainda reprova
# qualquer coisa muito aquém de um parágrafo real (~400+ caracteres), sem
# reprovar uma frase legítima isolada um pouco mais longa (130-150 caracteres).
_MIN_SECTION_CHARS = 100


def _looks_too_short(text: str) -> bool:
    """True quando o corpo da seção está anormalmente curto para ser leitura real."""
    return len((text or "").strip()) < _MIN_SECTION_CHARS


# Guard de truncamento: uma leitura cortada no meio de uma frase por limite de
# tokens é entregue a um cliente pagante hoje sem qualquer detecção — tão grave
# quanto o vazamento de idioma acima, e igual a ele em estratégia: detectar e
# regenerar usando o MESMO laço de tentativas (`generate_reading`), só caindo
# no fallback editorial depois de esgotar as tentativas.
#
# Um texto terminado corretamente acaba em pontuação final (. ! ? … " ' » ”)
# opcionalmente seguida de aspas/parênteses de fechamento. Qualquer outra
# coisa — vírgula, preposição pendurada, palavra cortada — é sinal de corte
# por max_tokens. Caso real observado em produção: "...transformando-a em
# motivação para" (termina em preposição, sem ponto).
_SENTENCE_END_RE = re.compile(r"[.!?…”\"'»)\]]\s*$")


def _looks_truncated(text: str) -> bool:
    """True quando o texto não termina de forma gramaticalmente completa."""
    stripped = (text or "").strip()
    if not stripped:
        return True
    return not _SENTENCE_END_RE.search(stripped)


def _system_prompt(locale: str) -> str:
    """Fixa o idioma explicitamente: reduz (não elimina) a derrapagem do modelo."""
    language = "espanhol rioplatense (es-AR)" if locale == "es-AR" else "português do Brasil (pt-BR)"
    return (
        "Siga o briefing editorial com precisão e entregue somente o texto final. "
        f"Escreva integralmente em {language}. Cada palavra do texto deve estar nesse idioma: "
        "nunca insira palavras, caracteres ou ideogramas de outro idioma (chinês, árabe, russo, inglês "
        "ou, fora de es-AR, espanhol) nem no meio de uma frase. Isto é um produto pago: uma única "
        "palavra estrangeira solta no meio do parágrafo já reprova o texto inteiro."
    )


def _call_minimax(
    prompt: str,
    locale: str = "pt-BR",
    max_tokens: int | None = None,
    timeout: float | None = None,
    model: str | None = None,
    section_label: str = "",
) -> str:
    api_key = os.getenv("MINIMAX_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY não configurada")
    base_url = os.getenv("MINIMAX_BASE_URL", os.getenv("LLM_BASE_URL", "https://api.minimax.io/v1")).rstrip("/")
    # ``model`` explícito é usado pelo roteamento M3/M2.7 seção-a-seção (ver
    # _LONG_CONTENT_IDS / _generate_section); quando ausente, cai no modelo
    # padrão da conta inteira.
    model = model or os.getenv("MINIMAX_MODEL", os.getenv("LLM_MODEL_TEXT", _DEFAULT_MODEL))
    # ``max_tokens`` explícito é usado pela geração seção-a-seção (budget bem
    # menor, ~550 tokens, em vez do documento inteiro); quando ausente, cai no
    # comportamento antigo (budget por content_id extraído do próprio prompt).
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": _system_prompt(locale)},
                {"role": "user", "content": prompt},
            ],
            # Medido em 17/08/2026, 50 gerações por configuração, vazamento
            # contado no corpo final (fora do bloco <think>, que o modelo usa
            # para pensar em vários idiomas e nunca chega à cliente):
            #   temp 0.85 sem top_p → 26% das seções com escrita não-latina
            #   temp 0.7 + top_p 0.9 → 18%, e palavra de outro idioma cai de 8% para 4%
            # A cauda da distribuição é onde moram os tokens de outros alfabetos;
            # top_p a corta antes de o sorteio chegar lá. Não elimina o problema
            # (por isso o guard e a retentativa continuam), mas reduz quase um
            # terço das refeitas — cada uma custa segundos na janela em que a
            # leitura precisa ficar pronta antes de a cliente acordar.
            # 0.7 e não 0.6: a temperatura mais baixa não melhorou o vazamento de
            # script e deixa o texto mais parecido entre clientes, o que num
            # produto de leitura personalizada é um estrago pior.
            "temperature": 0.7,
            "top_p": 0.9,
            "max_tokens": max_tokens if max_tokens is not None else _max_tokens_for(_extract_content_id(prompt)),
        }
    ).encode()
    request = Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    effective_timeout = timeout if timeout is not None else float(os.getenv("MINIMAX_TIMEOUT_SECONDS", "120"))
    try:
        # Porteiro global: teto de chamadas em voo + ritmo + cooldown coletivo.
        # Fica AQUI, na saída, e não no agendamento do trabalho, porque é aqui
        # que job da madrugada, compra na hora e regeneração do admin viram a
        # mesma coisa aos olhos do provedor.
        with _MinimaxGate():
            with urlopen(request, timeout=effective_timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
            _minimax_speed_up()
    except (HTTPError, URLError, TimeoutError, ValueError, ConnectionError, OSError) as exc:
        # ConnectionError/OSError cobrem RemoteDisconnected e outros hiccups de
        # rede que NÃO são URLError/TimeoutError — observado em produção
        # (2026-08-07): sem isso, a exceção escapava do try/except do worker
        # e derrubava a geração INTEIRA das 15 seções (a thread quebrava fora
        # do laço de retry de _generate_section), em vez de só essa seção
        # cair no fallback pontual como as demais falhas de rede já tratadas
        # aqui.
        # O nome da exceção sozinho não diz NADA acionável: em 18/08/2026 três
        # mapas caíram em produção com "MiniMax indisponível: HTTPError" repetido,
        # e não dava para saber se era 429 (concorrência alta demais, culpa
        # nossa), 400 (payload/modelo recusado) ou 5xx (fornecedor fora). São três
        # bugs diferentes com três correções diferentes. HTTPError carrega código
        # e corpo — logar os dois é o que separa "esperar passar" de "consertar".
        detalhe = type(exc).__name__
        if isinstance(exc, HTTPError):
            try:
                corpo = exc.read()[:300].decode("utf-8", errors="replace").replace("\n", " ")
            except Exception:  # corpo já consumido ou stream morto
                corpo = ""
            detalhe = f"HTTP {exc.code}" + (f" — {corpo}" if corpo else "")
            # Recusa por volume é da CONTA, não desta chamada: sem fechar a
            # porta para todas as threads, as outras 11 em voo tomam o mesmo
            # 429 no mesmo segundo e gastam as tentativas de todas contra a
            # mesma janela. Retry-After manda quando o provedor o envia.
            if exc.code == 429:
                try:
                    espera = float(exc.headers.get("Retry-After") or 0)
                except (TypeError, ValueError):
                    espera = 0.0
                _minimax_cooldown(espera if espera > 0 else _MINIMAX_COOLDOWN_DEFAULT_SECONDS)
                # Cooldown resolve o AGORA; o ritmo resolve o DEPOIS. Sem
                # diminuir o ritmo, passado o cooldown a fila volta a bater na
                # mesma parede daqui a um minuto.
                _minimax_slow_down()
        raise RuntimeError(f"MiniMax indisponível: {detalhe}") from exc

    usage = result.get("usage") or {} if isinstance(result, dict) else {}
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    # Campos de cache que MiniMax pode devolver (nomes variam por versão da API;
    # logamos todos para evidenciar na produção se o cache está ativo e qual
    # nomenclatura usam). Grep: minimax_usage_full
    cached_tokens = usage.get("cached_tokens") or usage.get("cache_read_input_tokens") or usage.get("prompt_cache_hit_tokens")
    finish_reason = None
    try:
        choice = result["choices"][0]
        finish_reason = choice.get("finish_reason")
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Resposta MiniMax sem conteúdo") from exc

    content_id = _extract_content_id(prompt)
    # Log estruturado de custo/quota — cada chamada real ao MiniMax gera uma
    # linha, seja ela bem-sucedida ou não (empty body inclusive), para que dê
    # pra reconstituir queima de cota via grep em produção (ver relatório
    # /tmp/claude-1000/roteamento-minimax.md, seção "Token logging").
    logger.info(
        "minimax_call model=%s content_id=%s section=%s prompt_tokens=%s completion_tokens=%s cached_tokens=%s finish_reason=%s",
        model, content_id, section_label or "-", prompt_tokens, completion_tokens, cached_tokens, finish_reason,
    )
    # Dump completo de usage uma vez por chamada para descobrir campos novos
    # (incluindo cache fields que a API pode devolver sem documentar na SDK).
    if usage:
        logger.debug("minimax_usage_full model=%s usage=%s", model, usage)
    _record_quota_usage(model, completion_tokens)
    _persist_quota_usage(model, completion_tokens)

    content = re.sub(r"<think>[\s\S]*?</think>\s*", "", content).strip()
    if not content:
        # CONSTRAINT DE PRODUÇÃO (M3, 2026-08-07): com budget apertado M3
        # queima o orçamento inteiro em <think>...</think> e devolve corpo
        # vazio com finish_reason=length. Este RuntimeError é o gatilho que
        # ``_generate_section`` usa para cair automaticamente no modelo de
        # fallback (M2.7) — NUNCA remover sem manter esse fallback em algum
        # lugar do caminho de chamada.
        logger.warning(
            "minimax_empty_body model=%s content_id=%s section=%s finish_reason=%s",
            model, content_id, section_label or "-", finish_reason,
        )
        raise RuntimeError("Resposta MiniMax vazia")
    return content


def _paragraphs_to_html(text: str) -> str:
    clean = re.sub(r"<think>[\s\S]*?</think>\s*", "", text).strip()
    paragraphs = [re.sub(r"\s*\n\s*", " ", part).strip() for part in re.split(r"\n\s*\n", clean) if part.strip()]
    return "".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in paragraphs)


# Parse de markdown seccionado e canonização de títulos, portados de
# astrodicas-telegram/src/vendas_bot/mapa_premium.py (`_parse_markdown_secoes`
# e `_canonizar_titulos`). O modelo às vezes erra o título ("Sol em Leão na
# Casa 7" em vez de "Sol"); como a lista de seções é fixa e pedida em ordem,
# canonizamos por POSIÇÃO — nunca deixamos um título errado do modelo virar
# o título exibido ao cliente pagante.
def _parse_markdown_sections(md: str) -> list[dict]:
    text = re.sub(r"<think>[\s\S]*?</think>\s*", "", (md or "")).replace("\r\n", "\n").strip()
    if not text:
        return []
    lines = text.split("\n")
    h2_count = sum(1 for l in lines if l.strip().startswith("## "))
    h3_count = sum(1 for l in lines if l.strip().startswith("### "))
    use_h3_as_section = h3_count > max(h2_count * 3, 2)

    sections: list[dict] = []
    current: dict | None = None

    def finalize(sec):
        if not sec:
            return
        body = "\n".join(l for l in sec["content"] if l.strip()).strip()
        if sec["title"].strip() and body:
            sections.append({
                "title": sec["title"].strip(),
                "subtitle": sec["subtitle"].strip(),
                "order": len(sections) + 1,
                "content": body,
            })

    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("## "):
            finalize(current)
            current = {"title": line[3:].strip(), "subtitle": "", "content": []}
            continue
        if current is None:
            continue
        if line.startswith("### "):
            if use_h3_as_section:
                finalize(current)
                current = {"title": line[4:].strip(), "subtitle": "", "content": []}
            elif not current["subtitle"]:
                current["subtitle"] = line[4:].strip()
            else:
                current["content"].append(raw_line)
            continue
        current["content"].append(raw_line)
    finalize(current)
    return sections


def _canonicalize_titles(sections: list[dict], expected: list[tuple[str, str]]) -> list[dict]:
    """Reescreve título/subtítulo pelos canônicos, por posição, quando a contagem bate.

    Se o modelo entregou uma quantidade diferente de seções, o pareamento por
    posição não é confiável — mantemos o que veio em vez de arriscar título
    errado (mesma decisão do bot: fallback sensato > adivinhação).
    """
    if len(sections) != len(expected):
        logger.warning(
            "generate_reading: %d seções vs %d esperadas — títulos não canonizados",
            len(sections), len(expected),
        )
        return sections
    for i, (sec, (title, subtitle)) in enumerate(zip(sections, expected), 1):
        sec["title"] = title
        sec["subtitle"] = sec.get("subtitle") or subtitle
        sec["order"] = i
    return sections


def _sections_to_html(sections: list[dict]) -> str:
    parts = []
    for sec in sections:
        parts.append(f"<h2>{html.escape(sec['title'])}</h2>")
        if sec.get("subtitle"):
            parts.append(f"<h3>{html.escape(sec['subtitle'])}</h3>")
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", sec["content"]) if p.strip()]
        for paragraph in paragraphs:
            parts.append(f"<p>{html.escape(re.sub(r'\\s*\\n\\s*', ' ', paragraph))}</p>")
    return "".join(parts)


def _sections_plain_text(sections: list[dict]) -> str:
    """Reconstrói o texto corrido das seções — usado pelo guard de idioma, que
    precisa validar o CONTEÚDO completo, não só os títulos canonizados (que
    são texto nosso, sempre em pt-BR/es-AR corretos por definição)."""
    return "\n\n".join(sec["content"] for sec in sections)


def _fallback_reading(profile, locale: str) -> str:
    sign = sun_sign(profile.birth_date if profile else None, locale)
    city = profile.birth_city if profile and profile.birth_city else "seu lugar de nascimento"
    if locale == "es-AR":
        return (
            f"<p>Tu lectura empieza con tu Sol en {sign}, visto desde {html.escape(city)}. Hay una parte tuya que ya entendió lo que necesita, aunque todavía busque una confirmación afuera. Hoy el cielo funciona como espejo: no define tu destino, pero ilumina esa conversación interna que venís postergando. Prestá atención a lo que te da calma después de decidir, porque ahí suele estar la respuesta más honesta.</p>"
            "<p>En los vínculos y en el trabajo, no confundas intensidad con urgencia. Una charla puede tocar un punto sensible, pero no necesita convertirse en conflicto. Elegí palabras claras y dejá espacio para escuchar. Si alguien te pide más de lo que podés dar, poner un límite también es una forma de cuidar el vínculo y de respetar tu propia energía.</p>"
            "<p>Tu dirección práctica para hoy es simple: cerrá una pendiente pequeña antes de abrir otra, mové el cuerpo y reservá unos minutos sin pantalla. Al final del día, anotá qué situación te hizo sentir más presente. Esa pista vale más que una gran promesa, porque muestra dónde tu energía realmente quiere crecer.</p>"
        )
    return (
        f"<p>Sua leitura começa pelo Sol em {sign}, observado a partir de {html.escape(city)}. Existe uma parte sua que já entendeu o que precisa, embora ainda procure confirmação do lado de fora. Hoje, o céu funciona como espelho: não fecha seu destino, mas ilumina aquela conversa interna que você vem adiando. Repare no que traz calma depois de uma decisão, porque ali costuma morar a resposta mais honesta.</p>"
        "<p>Nos vínculos e no trabalho, não confunda intensidade com urgência. Uma conversa pode tocar num ponto sensível sem precisar virar conflito. Escolha palavras claras e deixe espaço para escutar. Se alguém pedir mais do que você consegue oferecer, colocar limite também é uma forma de cuidar da relação e de respeitar a própria energia.</p>"
        "<p>Sua direção prática para hoje é simples: encerre uma pendência pequena antes de abrir outra, movimente o corpo e reserve alguns minutos sem tela. Ao fim do dia, anote qual situação fez você se sentir mais presente. Essa pista vale mais do que uma grande promessa, porque mostra onde sua energia realmente quer crescer.</p>"
    )


def _fallback_sections(content_id: str, profile, locale: str) -> list[dict]:
    """Versão seccionada do fallback editorial — a mesma qualidade de conteúdo
    do `_fallback_reading`, mas com cada parágrafo alocado numa seção real, para
    que a UI do portal continue mostrando títulos mesmo quando o LLM falhou.
    O texto continua identificável como fallback via ``ReadingResult.source``."""
    template = _fallback_reading(profile, locale)
    paragraphs = re.findall(r"<p>(.*?)</p>", template, re.DOTALL)
    expected = sections_for(content_id, profile, locale)
    if not expected:
        return []
    sections = []
    for i, (title, subtitle) in enumerate(expected, 1):
        body = html.unescape(paragraphs[(i - 1) % len(paragraphs)]) if paragraphs else ""
        sections.append({"title": title, "subtitle": subtitle, "order": i, "content": body})
    return sections


PAID_ASCENDANT_WARNING: dict[str, dict[str, str]] = {
    "pt-BR": (
        "Hora de nascimento não informada — assumimos 00:00 só para mostrar um valor. "
        "O Ascendente é o ponto mais sensível à hora do mapa inteiro (troca de signo a cada ~2h), "
        "então este resultado é uma ESTIMATIVA e provavelmente NÃO é o seu Ascendente real. "
        "Se você souber a hora (mesmo que aproximada), atualize seus dados de nascimento para refazer a leitura."
    ),
    "es-AR": (
        "Hora de nacimiento no informada — asumimos 00:00 solo para mostrar un valor. "
        "El Ascendente es el punto más sensible a la hora de toda la carta (cambia de signo cada ~2h), "
        "así que este resultado es una ESTIMACIÓN y probablemente NO es tu Ascendente real. "
        "Si sabés la hora (aunque sea aproximada), actualizá tus datos de nacimiento para rehacer la lectura."
    ),
}


def _section_prompt(
    content_id: str,
    general_title: str,
    section_title: str,
    subtitle: str,
    order: int,
    total: int,
    sibling_titles: list[str],
    context: dict,
    locale: str,
    correction: str | None = None,
) -> str:
    """Prompt de UMA seção só. Carrega o mesmo ``calculated_chart`` e a lista
    dos títulos irmãos (para não repetir conteúdo entre seções geradas em
    paralelo, já que nenhuma seção vê o texto das outras)."""
    language = "espanhol rioplatense natural" if locale == "es-AR" else "português brasileiro natural"
    today = date.today().isoformat()
    assumed_warning = _assumed_warning_text(
        locale, bool(context.get("birth_time_assumed")), section_title=section_title,
    )
    language_lock = _language_lock_text(locale)
    # Retry corretivo: sem isto o laço de tentativas reenviava o prompt
    # IDÊNTICO depois de reprovar por idioma/truncamento, e o modelo repetia o
    # mesmo erro até estourar as tentativas (caso Luciola, 15/08/2026).
    correction_text = (
        f"\n\nATENÇÃO — sua resposta anterior a este mesmo pedido foi REPROVADA: {correction}. "
        "Reescreva a seção do zero corrigindo exatamente isso."
        if correction
        else ""
    )
    outras = ", ".join(t for t in sibling_titles if t != section_title)
    # O label "natal premium" confunde o modelo para leituras de trânsito.
    # Cada tipo recebe o label correto; o default cobre os mapas natais.
    _TRANSIT_LABELS: dict[str, str] = {
        "site:content:horoscopo_diario": "do horóscopo diário",
        "site:content:previsao_semanal": "da previsão semanal",
        "site:content:calendario_lunar": "do calendário lunar",
        "site:content:guia_dos_retrogrados": "do guia de retrógrados",
    }
    _label = _TRANSIT_LABELS.get(content_id, "da leitura natal premium")
    reading_type = f"{_label} \"{general_title}\""
    # Seções que disparam thinking largo quando o escopo é amplo demais:
    # - "Direção Prática" / "Como agir hoje": exige síntese de todo o mapa →
    #   medido em produção: falha 2/3 com budget 2500 inteiro consumido em thinking.
    # - "Identificação" / "O dia reflete você": convida varredura do mapa inteiro
    #   para "o que no chart ressoa com hoje" → mesmo padrão, observado em
    #   02:38-02:39 UTC 2026-08-13: 2/3 tentativas burn 2500 tokens em thinking.
    # Solução: restringir o ESCOPO do que a seção pede, não o raciocínio
    # (instrução anti-think não funciona: modelo ignora). Instrução diferente por
    # tipo: prática pede conselho concreto, identificação pede ressonância emocional.
    # Mapeamos por subtitle (não por título), que é o que efetivamente guia a tarefa.
    _sub = subtitle.lower()
    _SCOPE_NARROWING: dict[str, str] = {
        # Prática: 1-2 aspectos → conselho direto
        "como agir hoje": (
            "<escolha 1 a 2 aspectos ou trânsitos do calculated_chart e derive deles orientações concretas para o dia — "
            "não percorra o mapa inteiro; vá direto ao conselho prático em 2 parágrafos de 70 a 110 palavras cada>"
        ),
        "how to act today": (
            "<escolha 1 a 2 aspectos ou trânsitos do calculated_chart e derive deles orientações concretas para o dia — "
            "não percorra o mapa inteiro; vá direto ao conselho prático em 2 parágrafos de 70 a 110 palavras cada>"
        ),
        # Identificação: 1 trânsito do dia OU 1 aspecto natal dominante → ressonância emocional
        "o dia reflete você": (
            "<escolha 1 trânsito do dia ou 1 aspecto natal dominante do calculated_chart e mostre como ele ressoa "
            "com o momento emocional da cliente — não percorra o mapa inteiro; "
            "2 parágrafos de 70 a 110 palavras cada>"
        ),
        # "O que cobra atenção" era a única seção do diário sem narrowing — e a
        # única que devolveu corpo vazio na medição de 13/08 (budget 2500 inteiro
        # consumido em thinking). Relações + trabalho num prompt aberto convida a
        # varrer o mapa todo; restringir a 1 aspecto fecha o escopo.
        "o que cobra atenção": (
            "<escolha 1 aspecto ou trânsito do calculated_chart que toque relações OU trabalho e "
            "derive dele o que merece atenção hoje — não percorra o mapa inteiro; "
            "2 parágrafos de 70 a 110 palavras cada>"
        ),
        "lo que pide atención": (
            "<elegí 1 aspecto o tránsito del calculated_chart que toque vínculos O trabajo y "
            "derivá de él lo que merece atención hoy — no recorras la carta entera; "
            "2 párrafos de 70 a 110 palabras cada uno>"
        ),
        "el día te refleja": (
            "<elegí 1 tránsito del día o 1 aspecto natal dominante del calculated_chart y mostrá cómo resuena "
            "con el momento emocional de la cliente — no recorras la carta entera; "
            "2 párrafos de 70 a 110 palabras cada uno>"
        ),
        # es-AR equivalent of "como agir hoje"
        "cómo actuar hoy": (
            "<elegí 1 a 2 aspectos o tránsitos del calculated_chart y derivá de ellos orientaciones concretas para el día — "
            "no recorras la carta entera; directo al consejo práctico en 2 párrafos de 70 a 110 palabras cada uno>"
        ),
    }
    scope_instruction = _SCOPE_NARROWING.get(
        _sub,
        "<2 a 3 parágrafos de 70 a 110 palavras cada, separados por linha em branco, cobrindo apenas o tema desta seção "
        "com base unicamente no calculated_chart>",
    )
    return f"""Você é a astróloga editorial da AstroDicas. Está escrevendo APENAS UMA seção (a seção {order} de {total}) \
{reading_type} em {language}.
Data de referência: {today}. Identificador: {content_id}.
Dados autorizados do cliente: {json.dumps(context, ensure_ascii=False)}.

Título desta seção: {section_title}
Subtítulo desta seção: {subtitle}
As outras seções desta MESMA leitura, que outra chamada já está gerando separadamente (não repita o conteúdo \
delas, escreva só o que pertence à sua seção): {outras}.

Formato obrigatório da resposta — markdown, só esta seção, nada além dela:
## {section_title}
### {subtitle}
{scope_instruction}

Use o nome do cliente com naturalidade no máximo uma vez nesta seção. Use somente os dados fornecidos. Não invente
Ascendente, Lua, casas, aspectos, trânsitos ou posições planetárias que não tenham sido calculados.
POSIÇÕES: quando esta seção tratar de um planeta ou ponto do mapa, cite o signo E a casa dele exatamente como estão
em calculated_chart, e derive a interpretação daquela combinação — não do signo solar genérico. Se a seção tiver
aspecto listado envolvendo esse planeta, use pelo menos um deles. Trocar o signo, o grau ou a casa de um planeta é
erro grave: a cliente vê a roda astrológica calculada ao lado do seu texto e percebe a contradição.
NÃO escreva meta-texto: nada de comentar o que faltou nos dados, o que você não pôde calcular, o que é limitação
desta leitura ou o tamanho da seção — exceto se uma instrução ATENÇÃO abaixo pedir esse aviso explicitamente.
Escreva a interpretação e só ela. Não faça diagnóstico médico, promessa financeira nem previsão
fatalista. Não cite inteligência artificial. TERMINE a última frase de forma completa — nunca corte no meio; se estiver \
perto do limite, feche a frase atual e pare.{language_lock}{correction_text}{assumed_warning}"""


def _fallback_section(content_id: str, profile, locale: str, order: int) -> dict:
    """Fallback de UMA seção só — reaproveita o mesmo texto editorial de
    ``_fallback_sections``, escolhendo só a posição que falhou, para não gerar
    um segundo template incompatível com o resto da leitura."""
    sections = _fallback_sections(content_id, profile, locale)
    if not sections:
        return {"title": "", "subtitle": "", "order": order, "content": ""}
    return sections[(order - 1) % len(sections)]


def _generate_section(
    content_id: str,
    general_title: str,
    section_title: str,
    subtitle: str,
    order: int,
    total: int,
    sibling_titles: list[str],
    context: dict,
    locale: str,
    profile,
) -> tuple[dict, bool]:
    """Gera UMA seção com seu próprio laço de tentativas. Retorna
    ``(secao, caiu_no_fallback)`` — a segunda seção-a-seção do que
    ``generate_reading`` fazia para o documento inteiro, só que aqui o custo
    de uma reprovação (idioma ou truncamento) é ~550 tokens, não 7000."""
    attempts = max(1, int(os.getenv("MINIMAX_SECTION_MAX_ATTEMPTS", _SECTION_MAX_ATTEMPTS_DEFAULT)))
    timeout = float(os.getenv("MINIMAX_SECTION_TIMEOUT_SECONDS", _SECTION_TIMEOUT_SECONDS_DEFAULT))
    def _build_prompt(correction: str | None = None) -> str:
        return _section_prompt(
            content_id, general_title, section_title, subtitle, order, total,
            sibling_titles, context, locale, correction=correction,
        )

    prompt = _build_prompt()

    # Roteamento M3 x M2.7 (ver /tmp/claude-1000/roteamento-minimax.md,
    # seção 3): content_ids "longos" (mapa_astral_completo, mapa_da_carreira,
    # guia_do_mes — compra rara, muito texto) podem ir pro modelo com cota em
    # TOKENS/mês (M3); todo o resto fica no modelo com cota em
    # REQUISIÇÕES/semana (M2.7).
    #
    # DESLIGADO POR PADRÃO (decisão da dona, 2026-08-09): amostra real do M3
    # veio 24% mais curta que o baseline do M2.7 (3.6k vs 4.720 palavras) no
    # Mapa Astral Completo, que é o produto mais caro. Sem
    # MINIMAX_MODEL_LONG setada, produção continua 100% no modelo de sempre.
    # Para ligar: MINIMAX_MODEL_LONG=MiniMax-M3. Para desligar: apagar a env.
    is_long_content = content_id in _LONG_CONTENT_IDS
    default_model = os.getenv("MINIMAX_MODEL", os.getenv("LLM_MODEL_TEXT", _DEFAULT_MODEL))
    modelo_longo = os.getenv("MINIMAX_MODEL_LONG", "").strip()
    if is_long_content and modelo_longo:
        primary_model = modelo_longo
        primary_budget = _SECTION_TOKEN_BUDGET_M3
    else:
        primary_model = (os.getenv("MINIMAX_MODEL_SHORT", "").strip() or default_model)
        primary_budget = _SECTION_TOKEN_BUDGET
    fallback_model = (os.getenv("MINIMAX_MODEL_FALLBACK", "").strip() or default_model)

    def _attempt_with_model(model_name: str, budget: int) -> dict | None:
        # Escalonamento de budget: tentativa 1 usa o budget base; cada falha
        # seguinte adiciona 750 tokens (até _SECTION_TOKEN_BUDGET * 2 = 5000).
        # O thinking do M2.7 é estocástico — às vezes consome 2500 inteiros, às
        # vezes 400. Dar mais espaço nas tentativas seguintes quebra o ciclo de
        # falha sem custar cota extra quando a 1ª tentativa passa limpo.
        _BUDGET_ESCALATION = [0, 750, 1500, 2000, 2500]
        timeout_limit = max(1, int(os.getenv("MINIMAX_SECTION_TIMEOUT_CONSECUTIVE_LIMIT", _SECTION_TIMEOUT_CONSECUTIVE_LIMIT_DEFAULT)))
        consecutive_timeouts = 0
        correction: str | None = None
        # Repetir a mesma correção genérica quando o modelo já falhou 2x pelo
        # MESMO motivo raramente muda o resultado — ele já viu esse aviso e
        # derrapou de novo. Na ÚLTIMA tentativa, se o motivo se repetiu,
        # trocamos por instrução mais explícita e nomeando o idioma-alvo
        # (em vez de reenviar o aviso idêntico pela terceira/quarta vez).
        last_reason: str | None = None
        same_reason_streak = 0
        language_name = "espanhol rioplatense" if locale == "es-AR" else "português do Brasil"
        for attempt in range(1, attempts + 1):
            prompt = _build_prompt(correction)
            escalated_budget = min(
                budget + _BUDGET_ESCALATION[min(attempt - 1, len(_BUDGET_ESCALATION) - 1)],
                budget * 2,
            )
            try:
                raw = _call_minimax(
                    prompt, locale, max_tokens=escalated_budget, timeout=timeout, model=model_name, section_label=section_title,
                )
                consecutive_timeouts = 0  # resposta obtida — reset do contador de timeout
            except RuntimeError as exc:
                is_timeout = "TimeoutError" in str(exc)
                if is_timeout:
                    consecutive_timeouts += 1
                else:
                    consecutive_timeouts = 0
                logger.warning(
                    "MiniMax (%s) falhou na seção '%s' (tentativa %d/%d, budget=%d): %s",
                    model_name, section_title, attempt, attempts, escalated_budget, exc,
                )
                if is_timeout and consecutive_timeouts >= timeout_limit:
                    logger.warning(
                        "minimax_timeout_abort model=%s section=%s consecutive=%d limit=%d — desistindo deste modelo",
                        model_name, section_title, consecutive_timeouts, timeout_limit,
                    )
                    break
                # Backoff quando o fornecedor recusou por volume ou caiu.
                # Sem isto (produção, 18/08/2026) as 4 tentativas de uma seção
                # queimavam em 1,7s — repetir na mesma janela de rate limit é
                # gastar as tentativas todas contra a mesma recusa. Só para
                # 429/5xx: erro de payload não melhora esperando, e timeout já
                # levou o seu tempo na chamada anterior.
                if any(f"HTTP {code}" in str(exc) for code in (429, 500, 502, 503, 504)):
                    espera = _RETRY_BACKOFF_SECONDS[min(attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)]
                    logger.info(
                        "minimax_backoff model=%s section=%s tentativa=%d espera=%.1fs",
                        model_name, section_title, attempt, espera,
                    )
                    time.sleep(espera)
                continue

            parsed = _parse_markdown_sections(raw)
            body_text = parsed[0]["content"] if parsed else raw.strip()

            if _has_foreign_script(body_text):
                observado = _foreign_sample(body_text)
                limpo, aproveitavel = _sanitize_foreign_script(body_text)
                if aproveitavel:
                    # Ruído isolado: cortamos o caractere e seguimos. Descartar
                    # a seção aqui era o que levava mapas pagos ao fallback.
                    logger.info(
                        "MiniMax (%s) trouxe caractere isolado fora do alfabeto latino (%s) na seção '%s'; cortado e texto aproveitado.",
                        model_name, observado, section_title,
                    )
                    body_text = limpo
                else:
                    logger.warning(
                        "MiniMax (%s) devolveu caractere fora do alfabeto latino (%s) na seção '%s', tentativa %d/%d budget=%d; refazendo só esta seção.",
                        model_name, observado, section_title, attempt, attempts, escalated_budget,
                    )
                    same_reason_streak = same_reason_streak + 1 if last_reason == "script" else 1
                    last_reason = "script"
                    if attempt + 1 == attempts and same_reason_streak >= 2:
                        correction = (
                            # Tom descritivo, não acusatório. A versão anterior dizia
                            # "ATENÇÃO, isto já falhou 2x: você insiste em..." — texto
                            # que afirma o que o modelo teria feito antes. Isso tem a
                            # forma de conteúdo injetado ("você disse X"), e num dos
                            # testes de produção o modelo tratou o próprio prompt como
                            # ataque e recusou a tarefa. Pedir o resultado desejado
                            # funciona; repreender o modelo convida a recusa.
                            f"Requisito não atendido nas tentativas anteriores: o texto "
                            f"precisa usar somente o alfabeto latino, e apareceu {observado}. "
                            f"Escreva esta seção inteira em {language_name}, com alfabeto "
                            "latino (a-z e acentos comuns). Havendo dúvida sobre uma "
                            "palavra, prefira um sinônimo simples e comum."
                        )
                    else:
                        correction = (
                            f"o texto veio com caracteres fora do alfabeto latino ({observado}). "
                            "Escreva usando apenas o alfabeto latino"
                        )
                    continue
            if _has_foreign_words(body_text, locale):
                # Palavra estrangeira NÃO é cortada: remover "insights" do meio
                # da frase muda o sentido. Aqui só refazer resolve.
                vazadas = _foreign_word_sample(body_text, locale)
                logger.warning(
                    "MiniMax (%s) vazou palavra de outro idioma (%s) na seção '%s', tentativa %d/%d budget=%d; refazendo só esta seção.",
                    model_name, vazadas, section_title, attempt, attempts, escalated_budget,
                )
                same_reason_streak = same_reason_streak + 1 if last_reason == "words" else 1
                last_reason = "words"
                if attempt + 1 == attempts and same_reason_streak >= 2:
                    correction = (
                        # Mesmo motivo do bloco acima: descrever o requisito, não
                        # acusar o modelo de insistir no erro.
                        f"Requisito não atendido nas tentativas anteriores: apareceram palavras "
                        f"fora de {language_name} ({vazadas}). Escreva esta seção inteira em "
                        f"{language_name}, frase por frase, sem palavras de outro idioma. "
                        "Frases simples e curtas são preferíveis a vocabulário incerto."
                    )
                else:
                    correction = (
                        f"você usou palavras de outro idioma ({vazadas}) no meio do texto. "
                        "Substitua cada uma pelo equivalente natural no idioma da leitura"
                    )
                continue
            if _looks_like_refusal(body_text):
                logger.warning(
                    "MiniMax (%s) devolveu recusa/meta-comentário na seção '%s' (tentativa %d/%d budget=%d); "
                    "refazendo só esta seção. Início observado: %r",
                    model_name, section_title, attempt, attempts, escalated_budget, body_text[:120],
                )
                same_reason_streak = same_reason_streak + 1 if last_reason == "refusal" else 1
                last_reason = "refusal"
                correction = (
                    "você respondeu recusando ou comentando sobre a tarefa em vez de escrevê-la. Não fale "
                    "sobre o pedido, sobre você mesma ou sobre limitações — escreva diretamente o texto da "
                    "seção, começando pela primeira frase da leitura"
                )
                continue
            if _looks_too_short(body_text):
                logger.warning(
                    "MiniMax (%s) devolveu seção anormalmente curta (%d caracteres, piso=%d) em '%s' "
                    "(tentativa %d/%d budget=%d); refazendo só esta seção. Texto: %r",
                    model_name, len(body_text.strip()), _MIN_SECTION_CHARS, section_title,
                    attempt, attempts, escalated_budget, body_text,
                )
                correction = (
                    "sua resposta ficou curta demais para uma seção paga. Escreva os parágrafos completos "
                    "pedidos no formato, com o tamanho pedido"
                )
                continue
            if _looks_truncated(body_text):
                logger.warning(
                    "MiniMax (%s) truncou a seção '%s' (tentativa %d/%d budget=%d); refazendo só esta seção. Final observado: %r",
                    model_name, section_title, attempt, attempts, escalated_budget, body_text[-40:],
                )
                correction = (
                    "o texto foi cortado no meio de uma frase. Escreva menos parágrafos se "
                    "preciso, mas termine todas as frases"
                )
                continue
            if not body_text:
                continue

            return {"title": section_title, "subtitle": subtitle, "order": order, "content": body_text}
        return None

    section = _attempt_with_model(primary_model, primary_budget)
    if section is not None:
        logger.info(
            "minimax_section_model_used content_id=%s section=%s model=%s fallback=false",
            content_id, section_title, primary_model,
        )
        return section, False

    if fallback_model != primary_model:
        # Constraint dura de produção: SEMPRE que o modelo primário (M3 ou
        # não) esgota as tentativas — incluindo o caso de corpo vazio por
        # <think> ter consumido o budget inteiro — cai automaticamente para
        # o modelo de fallback (M2.7) ANTES de desistir e usar o fallback
        # editorial estático.
        logger.warning(
            "model_fallback content_id=%s section=%s primary=%s fallback=%s",
            content_id, section_title, primary_model, fallback_model,
        )
        section = _attempt_with_model(fallback_model, _SECTION_TOKEN_BUDGET)
        if section is not None:
            logger.info(
                "minimax_section_model_used content_id=%s section=%s model=%s fallback=true",
                content_id, section_title, fallback_model,
            )
            return section, False

    logger.error(
        "Seção '%s' esgotou tentativas em %s (idioma/truncamento/falha de rede/corpo vazio); usando fallback pontual só nela.",
        section_title, "primário e fallback" if fallback_model != primary_model else primary_model,
    )
    return _fallback_section(content_id, profile, locale, order), True


def _generate_reading_sections(
    content_id: str, title: str, profile, locale: str, customer_name: str, expected_sections: list[tuple[str, str]],
    on_section_done=None,
) -> list[dict] | None:
    """Gera todas as seções esperadas em paralelo (pool limitado) e devolve a
    lista já ordenada, ou ``None`` se NENHUMA seção saiu — nesse caso o
    chamador cai no fallback completo, igual ao comportamento antigo quando o
    MiniMax falhava por completo."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    context = _profile_context(profile, customer_name)
    sibling_titles = [t for t, _ in expected_sections]
    pool_size = max(1, int(os.getenv("MINIMAX_SECTION_POOL_SIZE", _SECTION_POOL_SIZE_DEFAULT)))
    total = len(expected_sections)
    results: list[dict | None] = [None] * total
    fell_back_indices: list[int] = []
    with ThreadPoolExecutor(max_workers=min(pool_size, total)) as executor:
        futures = {
            executor.submit(
                _generate_section, content_id, title, sec_title, subtitle, i, total, sibling_titles, context, locale, profile,
            ): i - 1
            for i, (sec_title, subtitle) in enumerate(expected_sections, 1)
        }
        for future in as_completed(futures):
            idx = futures[future]
            section, fell_back = future.result()
            results[idx] = section
            if fell_back:
                fell_back_indices.append(idx)
            if on_section_done:
                on_section_done()
    if all(r is None for r in results):
        return None

    # Bug real de produção (reading 5a769308, 2026-08-17): o OR binário antigo
    # marcava a leitura INTEIRA como fallback assim que 1 seção em 15 esgotava
    # as tentativas — o fail_closed então descartava as outras 14 boas junto.
    # Se só ALGUMAS (não todas) caíram no template local, vale a pena dar a
    # cada uma delas uma chance extra isolada — barata (1 seção, não 15) —
    # antes de aceitar a leitura inteira como fallback. Se a seção realmente
    # não sair limpa nem nessa chance extra, o fail_closed continua valendo.
    if fell_back_indices and len(fell_back_indices) < total:
        for idx in list(fell_back_indices):
            sec_title, subtitle = expected_sections[idx]
            section, fell_back = _generate_section(
                content_id, title, sec_title, subtitle, idx + 1, total, sibling_titles, context, locale, profile,
            )
            results[idx] = section
            if not fell_back:
                fell_back_indices.remove(idx)

    return results, bool(fell_back_indices)  # type: ignore[return-value]


def generate_reading(content_id: str, title: str, profile, locale: str = "pt-BR", customer_name: str = "", on_section_done=None) -> ReadingResult:
    # Mesmo flag da prévia grátis (commit 913fcd8): quando a hora não veio,
    # marcamos aqui para a UI renderizar o aviso ao lado do Ascendente
    # calculado. Sem isso, o cliente pagaria pela leitura completa e leria um
    # Ascendente "de verdade" que na verdade é estimado.
    birth_time_assumed = bool(profile is None or getattr(profile, "birth_time", None) is None)
    ascendant_warning: dict[str, str] | None = (
        {"pt-BR": PAID_ASCENDANT_WARNING["pt-BR"], "es-AR": PAID_ASCENDANT_WARNING["es-AR"]}
        if birth_time_assumed
        else None
    )
    expected_sections = sections_for(content_id, profile, locale)
    if os.getenv("MINIMAX_API_KEY", "").strip() and expected_sections:
        # Seção-a-seção, concorrente, com retry por seção (ver
        # ``_generate_reading_sections`` / ``_generate_section``) — substitui a
        # antiga chamada única de 7000 tokens para os 15 (ou 14) blocos.
        outcome = _generate_reading_sections(content_id, title, profile, locale, customer_name, expected_sections, on_section_done=on_section_done)
        if outcome is not None:
            sections, fell_back_any = outcome
            generated = _sections_to_html(sections)
            return ReadingResult(
                body_html=generated,
                source="fallback" if fell_back_any else "minimax",
                warning=(
                    "Leitura gerada por modelo editorial padrão. A leitura personalizada está temporariamente indisponível."
                    if fell_back_any
                    else ""
                ),
                birth_time_assumed=birth_time_assumed,
                ascendant_warning=ascendant_warning,
                sections=sections,
            )
        logger.error("Todas as seções falharam completamente; usando fallback editorial de documento inteiro.")
    elif os.getenv("MINIMAX_API_KEY", "").strip():
        prompt = _prompt(content_id, title, profile, locale, customer_name)
        # O drift de idioma é estocástico: a mesma chamada repetida costuma sair
        # limpa. Preferimos gastar uma segunda chamada a entregar uma leitura
        # paga com ideograma no meio da frase. 5 e não 3 (revisado 17/08/2026,
        # mesmo motivo do _SECTION_MAX_ATTEMPTS_DEFAULT acima): a dona liberou
        # requisição extra sem limite prático de custo.
        attempts = max(1, int(os.getenv("MINIMAX_MAX_ATTEMPTS", "5")))
        for attempt in range(1, attempts + 1):
            try:
                raw = _call_minimax(prompt, locale)
            except RuntimeError as exc:
                logger.warning("MiniMax falhou; usando fallback editorial: %s", exc)
                break

            guard_text = raw

            if _has_foreign_script(guard_text):
                logger.warning(
                    "MiniMax devolveu caractere fora do alfabeto latino (%s) na tentativa %d/%d; refazendo.",
                    _foreign_sample(guard_text),
                    attempt,
                    attempts,
                )
                continue
            if _has_foreign_words(guard_text, locale):
                logger.warning(
                    "MiniMax vazou palavra de outro idioma (%s) na tentativa %d/%d; refazendo.",
                    _foreign_word_sample(guard_text, locale),
                    attempt,
                    attempts,
                )
                continue

            if _looks_like_refusal(guard_text):
                logger.warning(
                    "MiniMax devolveu recusa/meta-comentário na tentativa %d/%d; refazendo. Início observado: %r",
                    attempt, attempts, guard_text.strip()[:120],
                )
                continue
            if _looks_too_short(guard_text):
                logger.warning(
                    "MiniMax devolveu resposta anormalmente curta (%d caracteres, piso=%d) na tentativa %d/%d; refazendo.",
                    len(guard_text.strip()), _MIN_SECTION_CHARS, attempt, attempts,
                )
                continue

            if _looks_truncated(raw):
                logger.warning(
                    "MiniMax truncou a resposta na tentativa %d/%d; refazendo. Final observado: %r",
                    attempt, attempts, raw.strip()[-40:],
                )
                continue

            generated = _paragraphs_to_html(raw)
            if generated:
                return ReadingResult(
                    body_html=generated,
                    source="minimax",
                    birth_time_assumed=birth_time_assumed,
                    ascendant_warning=ascendant_warning,
                )
        else:
            logger.error(
                "MiniMax derrapou de idioma, truncou ou ficou incompleto em todas as %d "
                "tentativas; usando fallback editorial.", attempts
            )
    if expected_sections:
        sections = _fallback_sections(content_id, profile, locale)
        return ReadingResult(
            body_html=_sections_to_html(sections),
            source="fallback",
            warning="Leitura gerada por modelo editorial padrão. A leitura personalizada está temporariamente indisponível.",
            birth_time_assumed=birth_time_assumed,
            ascendant_warning=ascendant_warning,
            sections=sections,
        )
    fallback = _fallback_reading(profile, locale)
    return ReadingResult(
        body_html=fallback,
        source="fallback",
        warning="Leitura gerada por modelo editorial padrão. A leitura personalizada está temporariamente indisponível.",
        birth_time_assumed=birth_time_assumed,
        ascendant_warning=ascendant_warning,
    )
