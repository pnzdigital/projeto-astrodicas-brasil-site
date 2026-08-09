import html
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .astrology import astrology_context


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
            logger.info("minimax_quota bucket=m2_7_requests_week delta=1 total=%d", _QUOTA_COUNTERS["m2_7_requests"])


def get_quota_counters() -> dict:
    """Snapshot dos contadores em memória — exposto para debug/instrumentação
    (não persiste entre deploys/processos)."""
    with _QUOTA_LOCK:
        return dict(_QUOTA_COUNTERS)

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
TOKEN_BUDGETS = {
    "site:content:horoscopo_diario": 1500,
    "site:content:mapa_astral_completo": 7000,
    "site:content:mapa_do_amor_sinastria": 3600,
    "site:content:mapa_da_carreira": 6500,
    "site:content:mapa_da_prosperidade": 3200,
    "site:content:previsao_semanal": 2400,
    "site:content:guia_do_mes": 2800,
    "site:content:calendario_lunar": 2400,
    "site:content:guia_dos_retrogrados": 2600,
    "site:content:manual_do_ascendente": 2800,
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
_SECTION_TOKEN_BUDGET = int(os.getenv("MINIMAX_SECTION_MAX_TOKENS", "2500"))

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
_SECTION_MAX_ATTEMPTS_DEFAULT = "3"

# Per-section timeout: a resposta ainda é bem menor que os 7000 tokens do
# documento inteiro, mas o budget subiu para 1800 (ver _SECTION_TOKEN_BUDGET)
# para acomodar o raciocínio do M2.7 — 60s cobre folgadamente o pior caso
# observado de latência de rede + geração + thinking para um bloco desse
# tamanho, sem herdar os 120s dimensionados para o documento inteiro.
_SECTION_TIMEOUT_SECONDS_DEFAULT = "60"

# Pool limitado: gerar as 15 seções em paralelo sem limite sobrecarregaria a
# API do MiniMax (rate limit) e o processo local; 4 workers equilibra tempo
# total (15 seções / 4 ≈ 4 rodadas) contra concorrência seguro.
_SECTION_POOL_SIZE_DEFAULT = "4"


# Seções exatas por content_id, portadas de astrodicas-telegram/src/vendas_bot/
# mapa_premium.py (`_SECOES_POR_TIPO["astral"]`) para manter o MESMO produto
# nos dois canais (bot e site). Cada tupla é (título canônico, subtítulo). O
# site hoje só vende mapa_astral_completo em formato seccionado; os demais
# content_ids seguem no formato de parágrafo corrido antigo até serem
# migrados (lista fica vazia para eles = sem seccionamento).
SECTIONS_BY_CONTENT_ID: dict[str, list[tuple[str, str]]] = {
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


def sections_for(content_id: str, profile=None) -> list[tuple[str, str]]:
    if content_id == _SINASTRIA_CONTENT_ID and not (profile and getattr(profile, "partner_birth_date", None)):
        return SINASTRIA_SEM_PARCEIRO_SECTIONS
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


def _assumed_warning_text(locale: str, birth_time_assumed: bool) -> str:
    """Aviso injetado no prompt quando a hora de nascimento foi assumida.

    Extraído de ``_prompt`` para ser reaproveitado por ``_section_prompt``
    (geração seção-a-seção) sem duplicar o texto.
    """
    if not birth_time_assumed:
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
        "mentalmente cada frase antes de escrevê-la: se uma palavra não é claramente desse idioma, troque-a."
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
    sections = sections_for(content_id, profile)
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
        # mapa_do_amor_sinastria e mapa_da_prosperidade saíram daqui quando
        # entraram seccionados; NÃO reintroduzir suas chaves nesta tabela —
        # um dict.update por cima do content_rule seccionado reativa a
        # contradição de duas instruções de formato no mesmo prompt.
        legacy_rules = {
            "site:content:horoscopo_diario": "Escreva exatamente 3 parágrafos substanciais, com 90 a 130 palavras cada. Use prioritariamente os trânsitos atuais para o mapa natal. O primeiro cria identificação emocional, o segundo aborda relações e trabalho e o terceiro traz direção prática.",
            "site:content:previsao_semanal": "Escreva 7 parágrafos, um para o panorama e seis para temas e decisões da semana, usando os trânsitos atuais calculados.",
            "site:content:calendario_lunar": "Escreva um guia editorial do ciclo lunar atual em 7 a 9 parágrafos. Não invente datas que não estejam nos dados; quando faltarem, trate como guia de uso das fases.",
            "site:content:guia_dos_retrogrados": "Escreva 7 a 9 parágrafos explicando os planetas retrógrados presentes no céu calculado e como atravessar revisões sem fatalismo.",
            "site:content:manual_do_ascendente": "Escreva 8 a 10 parágrafos sobre o Ascendente calculado, seu regente simbólico, presença, corpo e primeira impressão. Se não houver Ascendente calculado, explique que a hora exata é necessária.",
        }
        content_rule = legacy_rules.get(content_id, "Escreva uma leitura premium profunda, com 7 a 10 parágrafos.")
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
    }
)

_SPANISH_ONLY_LEAK_WORDS = frozenset(
    {
        "intercambio", "manifestarse", "tambien", "también",
        "aunque", "sino", "segun", "según", "asimismo",
        "ademas", "además", "porque no",
    }
)

# Defeitos pontuais já observados em produção que não são troca de idioma,
# mas token corrompido/malformado (nem pt-BR nem nenhum outro idioma válido).
# Documentado à parte porque a causa é outra (o modelo "gagueja" um sufixo),
# mas o efeito no cliente pagante é o mesmo: texto macarrônico. Tratamos como
# reprovação para forçar regeneração.
_KNOWN_GARBLED_TOKENS = frozenset({"urgeências"})


def _foreign_word_regex(locale: str) -> re.Pattern:
    words = set(_ENGLISH_LEAK_WORDS) | _KNOWN_GARBLED_TOKENS
    if locale != "es-AR":
        words |= _SPANISH_ONLY_LEAK_WORDS
    pattern = r"\b(?:" + "|".join(re.escape(word) for word in words) + r")\b"
    return re.compile(pattern, re.IGNORECASE)


def _has_foreign_words(text: str, locale: str = "pt-BR") -> bool:
    """True quando alguma palavra do texto vaza de outro idioma (alfabeto latino)."""
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
    model = model or os.getenv("MINIMAX_MODEL", os.getenv("LLM_MODEL_TEXT", "MiniMax-M2.7"))
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
            "temperature": 0.85,
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
        with urlopen(request, timeout=effective_timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError, ConnectionError, OSError) as exc:
        # ConnectionError/OSError cobrem RemoteDisconnected e outros hiccups de
        # rede que NÃO são URLError/TimeoutError — observado em produção
        # (2026-08-07): sem isso, a exceção escapava do try/except do worker
        # e derrubava a geração INTEIRA das 15 seções (a thread quebrava fora
        # do laço de retry de _generate_section), em vez de só essa seção
        # cair no fallback pontual como as demais falhas de rede já tratadas
        # aqui.
        raise RuntimeError(f"MiniMax indisponível: {type(exc).__name__}") from exc

    usage = result.get("usage") or {} if isinstance(result, dict) else {}
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
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
        "minimax_call model=%s content_id=%s section=%s prompt_tokens=%s completion_tokens=%s finish_reason=%s",
        model, content_id, section_label or "-", prompt_tokens, completion_tokens, finish_reason,
    )
    _record_quota_usage(model, completion_tokens)

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
    expected = sections_for(content_id, profile)
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
) -> str:
    """Prompt de UMA seção só. Carrega o mesmo ``calculated_chart`` e a lista
    dos títulos irmãos (para não repetir conteúdo entre seções geradas em
    paralelo, já que nenhuma seção vê o texto das outras)."""
    language = "espanhol rioplatense natural" if locale == "es-AR" else "português brasileiro natural"
    today = date.today().isoformat()
    assumed_warning = _assumed_warning_text(locale, bool(context.get("birth_time_assumed")))
    language_lock = _language_lock_text(locale)
    outras = ", ".join(t for t in sibling_titles if t != section_title)
    return f"""Você é a astróloga editorial da AstroDicas. Está escrevendo APENAS UMA seção (a seção {order} de {total}) \
da leitura natal premium \"{general_title}\" em {language}.
Data de referência: {today}. Identificador: {content_id}.
Dados autorizados do cliente: {json.dumps(context, ensure_ascii=False)}.

Título desta seção: {section_title}
Subtítulo desta seção: {subtitle}
As outras seções desta MESMA leitura, que outra chamada já está gerando separadamente (não repita o conteúdo \
delas, escreva só o que pertence à sua seção): {outras}.

Formato obrigatório da resposta — markdown, só esta seção, nada além dela:
## {section_title}
### {subtitle}
<2 a 3 parágrafos de 70 a 110 palavras cada, separados por linha em branco, cobrindo apenas o tema desta seção \
com base unicamente no calculated_chart>

Use o nome do cliente com naturalidade no máximo uma vez nesta seção. Use somente os dados fornecidos. Não invente
Ascendente, Lua, casas, aspectos, trânsitos ou posições planetárias que não tenham sido calculados. Quando faltar
cálculo astronômico, declare a limitação com linguagem acolhedora. Não faça diagnóstico médico, promessa financeira nem previsão
fatalista. Não cite inteligência artificial. TERMINE a última frase de forma completa — nunca corte no meio; se estiver \
perto do limite, feche a frase atual e pare.{language_lock}{assumed_warning}"""


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
    prompt = _section_prompt(content_id, general_title, section_title, subtitle, order, total, sibling_titles, context, locale)

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
    default_model = os.getenv("MINIMAX_MODEL", os.getenv("LLM_MODEL_TEXT", "MiniMax-M2.7"))
    modelo_longo = os.getenv("MINIMAX_MODEL_LONG", "").strip()
    if is_long_content and modelo_longo:
        primary_model = modelo_longo
        primary_budget = _SECTION_TOKEN_BUDGET_M3
    else:
        primary_model = (os.getenv("MINIMAX_MODEL_SHORT", "").strip() or default_model)
        primary_budget = _SECTION_TOKEN_BUDGET
    fallback_model = (os.getenv("MINIMAX_MODEL_FALLBACK", "").strip() or default_model)

    def _attempt_with_model(model_name: str, budget: int) -> dict | None:
        for attempt in range(1, attempts + 1):
            try:
                raw = _call_minimax(
                    prompt, locale, max_tokens=budget, timeout=timeout, model=model_name, section_label=section_title,
                )
            except RuntimeError as exc:
                logger.warning(
                    "MiniMax (%s) falhou na seção '%s' (tentativa %d/%d): %s",
                    model_name, section_title, attempt, attempts, exc,
                )
                continue

            parsed = _parse_markdown_sections(raw)
            body_text = parsed[0]["content"] if parsed else raw.strip()

            if _has_foreign_script(body_text):
                logger.warning(
                    "MiniMax (%s) devolveu caractere fora do alfabeto latino (%s) na seção '%s', tentativa %d/%d; refazendo só esta seção.",
                    model_name, _foreign_sample(body_text), section_title, attempt, attempts,
                )
                continue
            if _has_foreign_words(body_text, locale):
                logger.warning(
                    "MiniMax (%s) vazou palavra de outro idioma (%s) na seção '%s', tentativa %d/%d; refazendo só esta seção.",
                    model_name, _foreign_word_sample(body_text, locale), section_title, attempt, attempts,
                )
                continue
            if _looks_truncated(body_text):
                logger.warning(
                    "MiniMax (%s) truncou a seção '%s' (tentativa %d/%d); refazendo só esta seção. Final observado: %r",
                    model_name, section_title, attempt, attempts, body_text[-40:],
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
    fell_back_any = False
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
            fell_back_any = fell_back_any or fell_back
            if on_section_done:
                on_section_done()
    if all(r is None for r in results):
        return None
    return results, fell_back_any  # type: ignore[return-value]


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
    expected_sections = sections_for(content_id, profile)
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
        # paga com ideograma no meio da frase.
        attempts = max(1, int(os.getenv("MINIMAX_MAX_ATTEMPTS", "3")))
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
