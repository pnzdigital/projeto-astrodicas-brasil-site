#!/usr/bin/env python3
"""
scripts/medir_latencia_budgets.py
Mede latência p90/p95 das chamadas que COMPLETAM por budget.

Contexto: budget base passou para 5000 com escalonamento até 7500 (tentativas
2-5 usam 5750, 6500, 7000, 7500). Medição de 13/08 usava budgets 2500-5000,
então não cobre a cauda atual. Esta medição decide o timeout de seção seguro.

Uso:
  cd astrodicas-site
  .venv/bin/python scripts/medir_latencia_budgets.py --dry-run
  .venv/bin/python scripts/medir_latencia_budgets.py --sim
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import date, time as dtime
from pathlib import Path
from urllib.request import Request, urlopen
import re

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

_ENV_FILE = ROOT / ".env.dev"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

import app.engine as engine  # noqa: E402

RELATORIO = Path("/tmp/claude-1000/medicao-latencia-budgets.md")

# Budgets reais do escalonamento em produção (tentativas 1-5 com base 5000)
BUDGETS_PRODUCAO = [5000, 5750, 6500, 7000, 7500]

# Timeout longo: não queremos cortar chamadas na medição — só observar latência real
TIMEOUT_MEDICAO = 90.0


class _PerfilFalso:
    birth_date = date(1990, 3, 14)
    birth_time = dtime(14, 30)
    birth_city = "São Paulo"
    birth_state = "SP"
    birth_latitude = -23.5505
    birth_longitude = -46.6333
    birth_country = "BR"
    birth_timezone = "America/Sao_Paulo"
    partner_name = ""
    partner_birth_date = None
    partner_birth_time = None
    partner_birth_city = ""
    partner_country = ""
    partner_birth_timezone = ""


def _chamar(prompt: str, locale: str, max_tokens: int, model: str) -> dict:
    api_key = os.getenv("MINIMAX_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("MINIMAX_API_KEY não configurada")
    base_url = os.getenv("MINIMAX_BASE_URL", os.getenv("LLM_BASE_URL", "https://api.minimax.io/v1")).rstrip("/")
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": engine._system_prompt(locale)},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.85,
        "max_tokens": max_tokens,
    }).encode()
    request = Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    inicio = time.monotonic()
    try:
        with urlopen(request, timeout=TIMEOUT_MEDICAO) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {"erro": f"{type(exc).__name__}: {exc}", "latencia": time.monotonic() - inicio}

    latencia = time.monotonic() - inicio
    usage = result.get("usage") or {}
    try:
        choice = result["choices"][0]
        bruto = choice["message"]["content"]
        finish_reason = choice.get("finish_reason")
    except (KeyError, IndexError, TypeError):
        return {"erro": "resposta sem conteúdo", "latencia": latencia}

    corpo = re.sub(r"<think>[\s\S]*?</think>\s*", "", bruto).strip()
    completion = usage.get("completion_tokens") or 0
    return {
        "finish_reason": finish_reason,
        "completion_tokens": completion,
        "corpo_vazio": not corpo,
        "latencia": latencia,
    }


def percentil(vals: list[float], p: float) -> float:
    if not vals:
        return float("nan")
    vals = sorted(vals)
    idx = p / 100 * (len(vals) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(vals) - 1)
    return vals[lo] + (idx - lo) * (vals[hi] - vals[lo])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--content-id", default="site:content:horoscopo_diario")
    parser.add_argument("--amostras", type=int, default=3)
    parser.add_argument("--budgets", default=",".join(str(b) for b in BUDGETS_PRODUCAO))
    parser.add_argument("--max-chamadas", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sim", action="store_true")
    args = parser.parse_args()

    budgets = [int(b) for b in args.budgets.split(",") if b.strip()]
    secoes = engine.SECTIONS_BY_CONTENT_ID.get(args.content_id, [])
    if not secoes:
        print(f"content_id sem seções: {args.content_id}", file=sys.stderr)
        return 1

    total_chamadas = len(secoes) * len(budgets) * args.amostras
    modelo = os.getenv("MINIMAX_MODEL", os.getenv("LLM_MODEL_TEXT", "MiniMax-M2.7"))

    print(f"content_id : {args.content_id}")
    print(f"modelo     : {modelo}")
    print(f"seções     : {len(secoes)}")
    print(f"budgets    : {budgets}")
    print(f"amostras   : {args.amostras} por (seção × budget)")
    print(f"CHAMADAS   : {total_chamadas}")
    print(f"timeout    : {TIMEOUT_MEDICAO}s (longo — medição, não produção)")

    if total_chamadas > args.max_chamadas:
        print(f"\nABORTADO: {total_chamadas} > teto {args.max_chamadas}", file=sys.stderr)
        return 2
    if args.dry_run:
        print("\n--dry-run: nada chamado.")
        return 0
    if not args.sim:
        resposta = input("\nGastar essas requisições? [s/N] ").strip().lower()
        if resposta not in {"s", "sim", "y"}:
            print("cancelado.")
            return 0

    context = engine._profile_context(_PerfilFalso(), "Ana")
    titulos = [t for t, _ in secoes]
    geral = "Horóscopo do Dia"
    linhas: list[dict] = []

    for i, (titulo, subtitulo) in enumerate(secoes, 1):
        prompt = engine._section_prompt(
            args.content_id, geral, titulo, subtitulo, i, len(secoes), titulos, context, "pt-BR"
        )
        for budget in budgets:
            for amostra in range(1, args.amostras + 1):
                r = _chamar(prompt, "pt-BR", budget, modelo)
                r.update(secao=titulo, budget=budget, amostra=amostra)
                linhas.append(r)
                if "erro" in r:
                    print(f"  {titulo:<20} budget={budget} #{amostra}  ERRO {r['erro']!r}  lat={r['latencia']:.1f}s")
                else:
                    marca = "VAZIO" if r["corpo_vazio"] else "ok   "
                    print(
                        f"  {titulo:<20} budget={budget} #{amostra}  {marca} "
                        f"completion={r['completion_tokens']} finish={r['finish_reason']} lat={r['latencia']:.1f}s"
                    )

    # Relatório de latência por budget — só chamadas que completam (sem erro, sem body vazio)
    validos = [l for l in linhas if "erro" not in l and not l.get("corpo_vazio")]
    erros = [l for l in linhas if "erro" in l]
    vazios = [l for l in linhas if "erro" not in l and l.get("corpo_vazio")]

    print("\n=== LATÊNCIA POR BUDGET (chamadas que completaram) ===")
    print(f"{'budget':>8}  {'n':>4}  {'min':>6}  {'med':>6}  {'p75':>6}  {'p90':>6}  {'p95':>6}  {'max':>6}")
    resultados_budget: dict[int, list[float]] = {}
    for budget in budgets:
        lats = [l["latencia"] for l in validos if l["budget"] == budget]
        resultados_budget[budget] = lats
        if not lats:
            print(f"  {budget:>6}  {'—':>4}")
            continue
        print(
            f"  {budget:>6}  {len(lats):>4}  "
            f"{min(lats):>5.1f}s  {statistics.median(lats):>5.1f}s  "
            f"{percentil(lats, 75):>5.1f}s  {percentil(lats, 90):>5.1f}s  "
            f"{percentil(lats, 95):>5.1f}s  {max(lats):>5.1f}s"
        )

    todas_lats = [l["latencia"] for l in validos]
    if todas_lats:
        p90_global = percentil(todas_lats, 90)
        p95_global = percentil(todas_lats, 95)
        print(f"\nGLOBAL  n={len(todas_lats)}  p90={p90_global:.1f}s  p95={p95_global:.1f}s  max={max(todas_lats):.1f}s")
        print(f"\nTimeout recomendado: acima de p95={p95_global:.1f}s → sugestão: {max(40, int(p95_global) + 10)}s")

    print(f"\nErros de rede: {len(erros)}/{len(linhas)}")
    print(f"Corpo vazio:   {len(vazios)}/{len(linhas)}")

    # Salva relatório
    RELATORIO.parent.mkdir(parents=True, exist_ok=True)
    out = [
        "# Medição de latência por budget — MiniMax",
        "",
        f"- content_id: `{args.content_id}`  ·  modelo: `{modelo}`",
        f"- timeout de medição: {TIMEOUT_MEDICAO}s  ·  amostras: {args.amostras} por (seção × budget)",
        f"- chamadas: {len(linhas)}  ·  erros: {len(erros)}  ·  vazios: {len(vazios)}",
        "",
        "## Latência por budget (só chamadas que completaram)",
        "",
        "| budget | n | min | median | p75 | p90 | p95 | max |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for budget in budgets:
        lats = resultados_budget.get(budget, [])
        if not lats:
            out.append(f"| {budget} | 0 | — | — | — | — | — | — |")
        else:
            out.append(
                f"| {budget} | {len(lats)} | {min(lats):.1f}s | {statistics.median(lats):.1f}s | "
                f"{percentil(lats, 75):.1f}s | {percentil(lats, 90):.1f}s | "
                f"{percentil(lats, 95):.1f}s | {max(lats):.1f}s |"
            )
    if todas_lats:
        out += [
            "",
            f"**Global p90={p90_global:.1f}s  p95={p95_global:.1f}s  max={max(todas_lats):.1f}s**",
            f"**Timeout recomendado: {max(40, int(p95_global) + 10)}s** (p95 + 10s de margem, mínimo 40s)",
        ]
    out += [
        "",
        "## Amostras cruas",
        "",
        "```json",
        json.dumps(linhas, ensure_ascii=False, indent=2, default=str),
        "```",
    ]
    RELATORIO.write_text("\n".join(out), encoding="utf-8")
    print(f"\nrelatório: {RELATORIO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
