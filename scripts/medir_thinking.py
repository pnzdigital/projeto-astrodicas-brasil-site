#!/usr/bin/env python3
"""
scripts/medir_thinking.py
Medição offline do custo real de thinking do MiniMax por seção.

Responde, com número em vez de palpite:
  - quais seções realmente devolvem corpo vazio (thinking come o budget inteiro)
  - quanto do budget vira thinking em cada uma
  - se aumentar o budget resolve ou só desloca o teto (o modelo expande o
    thinking para preencher o espaço, comportamento já observado em 1500→2500)

Nenhum cliente na frente: chama a API direto, fora do fluxo do site, com
teto de chamadas explícito e estimativa impressa ANTES de gastar.

Uso:
  cd astrodicas-site
  .venv/bin/python scripts/medir_thinking.py --dry-run
  .venv/bin/python scripts/medir_thinking.py --amostras 3 --sim

Flags:
  --content-id   default site:content:horoscopo_diario (maior volume diário)
  --budgets      lista de max_tokens a comparar (default 2500,4000,5000)
  --amostras     repetições por (seção × budget) — default 3
  --secao        limita a uma seção pelo título (repetível)
  --max-chamadas teto duro de requisições (default 60); aborta se estourar
  --dry-run      só imprime o plano e a estimativa, não chama a API
  --sim          confirma o gasto sem perguntar (para rodar não-interativo)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from datetime import date, time as dtime
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

# .env.dev carrega a MINIMAX_API_KEY sem exigir export manual.
_ENV_FILE = ROOT / ".env.dev"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

import app.engine as engine  # noqa: E402

RELATORIO = Path("/tmp/claude-1000/medicao-thinking.md")


class _PerfilFalso:
    """Perfil determinístico — mesma pessoa em todas as amostras, para que a
    variação medida seja do modelo e não dos dados de entrada."""

    birth_date = date(1990, 3, 14)
    birth_time = dtime(14, 30)
    birth_city = "São Paulo"
    birth_state = "SP"
    # Coordenadas fixas: sem elas o astrology_context chama o Nominatim, e uma
    # geocodificação lenta ou fora do ar viraria ruído dentro da medição.
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


def _chamar(prompt: str, locale: str, max_tokens: int, model: str, timeout: float) -> dict:
    """Cópia instrumentada de engine._call_minimax: mesmo payload, mas devolve
    o usage cru e o texto ANTES de remover o <think>, que é o que precisamos
    medir. Não reusa a função original porque ela descarta os dois."""
    api_key = os.getenv("MINIMAX_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("MINIMAX_API_KEY não configurada (esperada em .env.dev)")
    base_url = os.getenv("MINIMAX_BASE_URL", os.getenv("LLM_BASE_URL", "https://api.minimax.io/v1")).rstrip("/")
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": engine._system_prompt(locale)},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.85,
            "max_tokens": max_tokens,
        }
    ).encode()
    request = Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    inicio = time.monotonic()
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # rede/HTTP/JSON — a amostra vira "erro", não derruba a medição
        return {"erro": f"{type(exc).__name__}: {exc}", "latencia": time.monotonic() - inicio}

    latencia = time.monotonic() - inicio
    usage = result.get("usage") or {}
    try:
        choice = result["choices"][0]
        bruto = choice["message"]["content"]
        finish_reason = choice.get("finish_reason")
    except (KeyError, IndexError, TypeError):
        return {"erro": "resposta sem conteúdo", "latencia": latencia, "usage": usage}

    think = "".join(re.findall(r"<think>([\s\S]*?)</think>", bruto))
    corpo = re.sub(r"<think>[\s\S]*?</think>\s*", "", bruto).strip()
    completion = usage.get("completion_tokens") or 0
    # Sem tokenizer do MiniMax, a fatia de thinking é estimada por proporção de
    # caracteres — suficiente para comparar seções entre si e ver se o
    # thinking cresce junto com o budget.
    total_chars = len(think) + len(corpo)
    think_tokens = round(completion * len(think) / total_chars) if total_chars else 0
    return {
        "finish_reason": finish_reason,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": completion,
        "cached_tokens": usage.get("cached_tokens")
        or usage.get("cache_read_input_tokens")
        or usage.get("prompt_cache_hit_tokens"),
        "think_chars": len(think),
        "corpo_chars": len(corpo),
        "think_tokens_est": think_tokens,
        "corpo_vazio": not corpo,
        "latencia": latencia,
        "usage_full": usage,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--content-id", default="site:content:horoscopo_diario")
    parser.add_argument("--budgets", default="2500,4000,5000")
    parser.add_argument("--amostras", type=int, default=3)
    parser.add_argument("--secao", action="append", default=[])
    parser.add_argument("--locale", default="pt-BR")
    parser.add_argument("--max-chamadas", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sim", action="store_true")
    args = parser.parse_args()

    budgets = [int(b) for b in args.budgets.split(",") if b.strip()]
    secoes = engine.SECTIONS_BY_CONTENT_ID.get(args.content_id, [])
    if not secoes:
        print(f"content_id sem seções definidas: {args.content_id}", file=sys.stderr)
        return 1
    if args.secao:
        alvo = {s.lower() for s in args.secao}
        secoes = [s for s in secoes if s[0].lower() in alvo or s[1].lower() in alvo]
        if not secoes:
            print("nenhuma seção casou com --secao", file=sys.stderr)
            return 1

    total_chamadas = len(secoes) * len(budgets) * args.amostras
    modelo = os.getenv("MINIMAX_MODEL", os.getenv("LLM_MODEL_TEXT", "MiniMax-M2.7"))

    print(f"content_id : {args.content_id}")
    print(f"modelo     : {modelo}")
    print(f"seções     : {len(secoes)} → {', '.join(t for t, _ in secoes)}")
    print(f"budgets    : {budgets}")
    print(f"amostras   : {args.amostras} por (seção × budget)")
    print(f"CHAMADAS   : {total_chamadas}  (cota do M2.7 é por REQUISIÇÃO/semana)")
    print(f"teto saída : ~{sum(budgets) * len(secoes) * args.amostras:,} tokens no pior caso")

    if total_chamadas > args.max_chamadas:
        print(f"\nABORTADO: {total_chamadas} chamadas passa do teto de {args.max_chamadas}.", file=sys.stderr)
        print("Reduza --amostras/--budgets ou suba --max-chamadas conscientemente.", file=sys.stderr)
        return 2
    if args.dry_run:
        print("\n--dry-run: nada foi chamado.")
        return 0
    if not args.sim:
        resposta = input("\nGastar essas requisições? [s/N] ").strip().lower()
        if resposta not in {"s", "sim", "y"}:
            print("cancelado.")
            return 0

    context = engine._profile_context(_PerfilFalso(), "Ana")
    titulos = [t for t, _ in secoes]
    timeout = float(os.getenv("MINIMAX_SECTION_TIMEOUT_SECONDS", "60"))
    geral = engine.CONTENT_TITLES.get(args.content_id, args.content_id) if hasattr(engine, "CONTENT_TITLES") else "Horóscopo do Dia"

    linhas: list[dict] = []
    for indice, (titulo, subtitulo) in enumerate(secoes, start=1):
        prompt = engine._section_prompt(
            args.content_id, geral, titulo, subtitulo, indice, len(secoes), titulos, context, args.locale
        )
        narrowed = subtitulo.lower() in {
            "como agir hoje", "how to act today", "o dia reflete você", "el día te refleja",
        }
        for budget in budgets:
            for amostra in range(1, args.amostras + 1):
                r = _chamar(prompt, args.locale, budget, modelo, timeout)
                r.update(secao=titulo, subtitulo=subtitulo, budget=budget, amostra=amostra, narrowed=narrowed)
                linhas.append(r)
                if "erro" in r:
                    print(f"  {titulo:<20} budget={budget} #{amostra}  ERRO {r['erro']}")
                else:
                    marca = "VAZIO" if r["corpo_vazio"] else "ok   "
                    print(
                        f"  {titulo:<20} budget={budget} #{amostra}  {marca} "
                        f"completion={r['completion_tokens']} think≈{r['think_tokens_est']} "
                        f"finish={r['finish_reason']} cache={r['cached_tokens']} {r['latencia']:.1f}s"
                    )

    _relatorio(args, modelo, budgets, secoes, linhas)
    return 0


def _relatorio(args, modelo, budgets, secoes, linhas: list[dict]) -> None:
    validos = [l for l in linhas if "erro" not in l]
    RELATORIO.parent.mkdir(parents=True, exist_ok=True)
    out: list[str] = [
        "# Medição de thinking — MiniMax",
        "",
        f"- content_id: `{args.content_id}`  ·  modelo: `{modelo}`  ·  locale: {args.locale}",
        f"- amostras: {args.amostras} por (seção × budget)  ·  chamadas: {len(linhas)}"
        f"  ·  erros de rede: {len(linhas) - len(validos)}",
        "",
        "## Taxa de corpo vazio por seção × budget",
        "",
        "| Seção | narrowed | budget | vazio | completion médio | think≈ médio | % do budget em think |",
        "|---|---|---|---|---|---|---|",
    ]
    for titulo, subtitulo in secoes:
        for budget in budgets:
            grupo = [l for l in validos if l["secao"] == titulo and l["budget"] == budget]
            if not grupo:
                continue
            vazios = sum(1 for l in grupo if l["corpo_vazio"])
            comp = statistics.mean(l["completion_tokens"] for l in grupo)
            think = statistics.mean(l["think_tokens_est"] for l in grupo)
            out.append(
                f"| {titulo} | {'sim' if grupo[0]['narrowed'] else 'não'} | {budget} | "
                f"{vazios}/{len(grupo)} | {comp:.0f} | {think:.0f} | {100 * think / budget:.0f}% |"
            )

    cacheados = [l for l in validos if l["cached_tokens"]]
    out += [
        "",
        "## Cache de prompt",
        "",
        f"Chamadas com campo de cache preenchido: {len(cacheados)}/{len(validos)}.",
        "Se for 0, o cache do MiniMax não é automático nesta conta — precisa de parâmetro no payload"
        " ou não se aplica; decidir com base nisto, não na tabela de preços.",
        "",
        "## Amostras cruas",
        "",
        "```json",
        json.dumps(linhas, ensure_ascii=False, indent=2, default=str),
        "```",
    ]
    RELATORIO.write_text("\n".join(out), encoding="utf-8")
    print(f"\nrelatório: {RELATORIO}")


if __name__ == "__main__":
    raise SystemExit(main())
