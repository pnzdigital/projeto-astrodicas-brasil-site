"""Live generation of real readings through the running MiniMax proxy.

Generates one reading per (locale, content_type) for the paid content types
the product sells. Saves to /tmp/astrodicas_live_<id>.txt for human review.

NEVER prints the API key. Catches auth errors and exits loudly.
"""
import os
import sys
import json
import time
from datetime import date, time as dtime
from types import SimpleNamespace

# Verify env is loaded
if not os.getenv("MINIMAX_API_KEY"):
    print("ERROR: MINIMAX_API_KEY not set. Run: set -a && source .env.dev && set +a", file=sys.stderr)
    sys.exit(2)

from app import engine, astrology


def make_profile_pt_BR():
    return SimpleNamespace(
        birth_date=date(1990, 5, 20),
        birth_time=dtime(10, 30),
        birth_city="São Paulo",
        birth_country="BR",
        birth_timezone="America/Sao_Paulo",
        birth_latitude="-23.5505",
        birth_longitude="-46.6333",
        partner_name="Maya",
        partner_birth_date=date(1992, 7, 14),
        partner_birth_time=dtime(18, 0),
        partner_birth_city="Rio de Janeiro",
        partner_country="BR",
        partner_birth_timezone="America/Sao_Paulo",
    )


def make_profile_es_AR():
    return SimpleNamespace(
        birth_date=date(1988, 11, 3),
        birth_time=dtime(7, 45),
        birth_city="Buenos Aires",
        birth_country="AR",
        birth_timezone="America/Argentina/Buenos_Aires",
        birth_latitude="-34.6037",
        birth_longitude="-58.3816",
        partner_name="Carlos",
        partner_birth_date=date(1986, 2, 17),
        partner_birth_time=dtime(20, 30),
        partner_birth_city="Córdoba",
        partner_country="AR",
        partner_birth_timezone="America/Argentina/Buenos_Aires",
    )


CONTENT_TYPES = [
    ("site:content:horoscopo_diario", "Horóscopo diário"),
    ("site:content:previsao_semanal", "Previsão semanal"),
    ("site:content:guia_do_mes", "Guia do mês"),
    ("site:content:calendario_lunar", "Calendário lunar"),
    ("site:content:guia_dos_retrogrados", "Guia dos retrógrados"),
    ("site:content:manual_do_ascendente", "Manual do Ascendente"),
    ("site:content:mapa_astral_completo", "Mapa astral completo"),
    ("site:content:mapa_do_amor_sinastria", "Mapa do amor (sinastria)"),
    ("site:content:mapa_da_carreira", "Mapa da carreira"),
    ("site:content:mapa_da_prosperidade", "Mapa da prosperidade"),
]

# Mirror titles for es-AR translation
TITLE_ES = {
    "site:content:horoscopo_diario": "Horóscopo diario",
    "site:content:previsao_semanal": "Pronóstico semanal",
    "site:content:guia_do_mes": "Guía del mes",
    "site:content:calendario_lunar": "Calendario lunar",
    "site:content:guia_dos_retrogrados": "Guía de los retrógrados",
    "site:content:manual_do_ascendente": "Manual del Ascendente",
    "site:content:mapa_astral_completo": "Mapa astral completo",
    "site:content:mapa_do_amor_sinastria": "Mapa del amor (sinastría)",
    "site:content:mapa_da_carreira": "Mapa de la carrera",
    "site:content:mapa_da_prosperidade": "Mapa de la prosperidad",
}


def run():
    out_dir = "/tmp/astrodicas_live"
    os.makedirs(out_dir, exist_ok=True)
    results = []
    for locale, profile_factory, label in [
        ("pt-BR", make_profile_pt_BR, "pt_BR_SaoPaulo_b1990-05-20"),
        ("es-AR", make_profile_es_AR, "es_AR_BuenosAires_b1988-11-03"),
    ]:
        profile = profile_factory()
        # Sanity: real chart computes
        ctx = astrology.astrology_context(profile)
        print(f"\n[{locale}] profile CTX status={ctx.get('status')}, planets={len(ctx.get('planets', {}))}, houses={'houses' in ctx}")
        for content_id, title_pt in CONTENT_TYPES:
            title = TITLE_ES[content_id] if locale == "es-AR" else title_pt
            customer = "Alex" if locale == "pt-BR" else "Lucía"
            t0 = time.time()
            try:
                result = engine.generate_reading(content_id, title, profile, locale, customer)
            except Exception as e:
                print(f"  ✗ {content_id}: {type(e).__name__}: {e}")
                results.append({"locale": locale, "content": content_id, "status": "ERROR", "error": str(e)})
                continue
            elapsed = time.time() - t0
            html = result.body_html if hasattr(result, "body_html") else result
            source = result.source if hasattr(result, "source") else "unknown"
            fn = f"{out_dir}/{label}__{content_id.replace(':', '_')}.txt"
            with open(fn, "w") as f:
                f.write(f"locale={locale}\ncontent_id={content_id}\ntitle={title}\n")
                f.write(f"source={source}\nelapsed={elapsed:.1f}s\nchars={len(html)}\n\n")
                f.write(html)
            words = len(html.split())
            paragraphs = html.count("<p>")
            print(f"  ✓ {content_id:<45} src={source:<8} para={paragraphs:<3} words={words:<5} {elapsed:.1f}s")
            results.append({"locale": locale, "content": content_id, "source": source, "paras": paragraphs, "words": words, "elapsed": elapsed, "file": fn})
    # Summary
    print("\n=== SUMMARY ===")
    print(f"Total: {len(results)}")
    print(f"minimax: {sum(1 for r in results if r.get('source') == 'minimax')}")
    print(f"fallback: {sum(1 for r in results if r.get('source') == 'fallback')}")
    print(f"errors: {sum(1 for r in results if r.get('status') == 'ERROR')}")
    with open(f"{out_dir}/_summary.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    run()
