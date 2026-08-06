import html
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .astrology import astrology_context


logger = logging.getLogger(__name__)

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
}


def sections_for(content_id: str) -> list[tuple[str, str]]:
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


def _prompt(content_id: str, title: str, profile, locale: str, customer_name: str = "") -> str:
    context = _profile_context(profile, customer_name)
    language = "espanhol rioplatense natural" if locale == "es-AR" else "português brasileiro natural"
    today = date.today().isoformat()
    # Quando a hora foi assumida (cliente não sabia), instruímos o LLM a tratar
    # o Ascendente como dado estimado: o ponto do mapa mais sensível à hora
    # (troca de signo a cada ~2h). Sem esse aviso no prompt, o texto pago
    # afirmaria o Ascendente com certeza que não tem — bug comercial.
    assumed_warning = ""
    if context.get("birth_time_assumed"):
        if locale == "es-AR":
            assumed_warning = (
                "\n\nATENCIÓN: la hora de nacimiento NO fue informada. El backend asumió 00:00 "
                "solo para poder calcular y mostrar el Ascendente. El Ascendente es el dato más "
                "sensible a la hora en toda la carta (cambia de signo cada ~2h), así que el "
                "Ascendente calculado es una ESTIMACIÓN y probablemente NO es el Ascendente real "
                "del cliente. Cuando hables del Ascendente, declara explícitamente que es estimado "
                "y que podría cambiar si la hora real fuera otra. No lo afirmes como hecho."
            )
        else:
            assumed_warning = (
                "\n\nATENÇÃO: a hora de nascimento NÃO foi informada. O backend assumiu 00:00 "
                "apenas para conseguir calcular e mostrar o Ascendente. O Ascendente é o dado "
                "mais sensível à hora no mapa inteiro (troca de signo a cada ~2h), então o "
                "Ascendente calculado é uma ESTIMATIVA e provavelmente NÃO é o Ascendente real "
                "do cliente. Ao falar do Ascendente, declare explicitamente que é estimado e "
                "que pode mudar se a hora real for outra. Não afirme como fato."
            )
    sections = sections_for(content_id)
    if sections:
        lista_secoes = "\n".join(f"{i:02d}. ## {t} — {s}" for i, (t, s) in enumerate(sections, 1))
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
        )
        rules = {content_id: content_rule}
    else:
        rules = {}
    rules.update({
        "site:content:horoscopo_diario": "Escreva exatamente 3 parágrafos substanciais, com 90 a 130 palavras cada. Use prioritariamente os trânsitos atuais para o mapa natal. O primeiro cria identificação emocional, o segundo aborda relações e trabalho e o terceiro traz direção prática.",
        "site:content:mapa_do_amor_sinastria": "Escreva 9 a 12 parágrafos sobre padrões afetivos do cliente. Se os dados do parceiro estiverem incompletos, explique com delicadeza que a comparação completa depende deles e não invente posições do parceiro.",
        "site:content:mapa_da_prosperidade": "Escreva 8 a 11 parágrafos sobre recursos, segurança, merecimento e oportunidades, sem prometer ganhos financeiros. Relacione a leitura às posições calculadas.",
        "site:content:previsao_semanal": "Escreva 7 parágrafos, um para o panorama e seis para temas e decisões da semana, usando os trânsitos atuais calculados.",
        "site:content:guia_do_mes": "Escreva 8 a 10 parágrafos com temas do mês, momentos de atenção e práticas concretas, usando o céu atual e o mapa natal.",
        "site:content:calendario_lunar": "Escreva um guia editorial do ciclo lunar atual em 7 a 9 parágrafos. Não invente datas que não estejam nos dados; quando faltarem, trate como guia de uso das fases.",
        "site:content:guia_dos_retrogrados": "Escreva 7 a 9 parágrafos explicando os planetas retrógrados presentes no céu calculado e como atravessar revisões sem fatalismo.",
        "site:content:manual_do_ascendente": "Escreva 8 a 10 parágrafos sobre o Ascendente calculado, seu regente simbólico, presença, corpo e primeira impressão. Se não houver Ascendente calculado, explique que a hora exata é necessária.",
    })
    content_rule = rules.get(content_id, content_rule if sections else "Escreva uma leitura premium profunda, com 7 a 10 parágrafos.")
    language_lock = (
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
# credibilidade do produto inteiro. O MiniMax-M2.1 troca uma palavra solta pelo
# equivalente em chinês, árabe ou russo algumas vezes por texto — validado em
# 2026-08-05 sobre 4 leituras reais: "a natureza já حساسة do Ascendente",
# "sugere que成长 pessoal", "estar стимулируя mudanças". Não é falha de encoding
# (o UTF-8 chega íntegro), é o próprio modelo derrapando de idioma.
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


def _call_minimax(prompt: str, locale: str = "pt-BR") -> str:
    api_key = os.getenv("MINIMAX_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY não configurada")
    base_url = os.getenv("MINIMAX_BASE_URL", os.getenv("LLM_BASE_URL", "https://api.minimax.io/v1")).rstrip("/")
    model = os.getenv("MINIMAX_MODEL", os.getenv("LLM_MODEL_TEXT", "MiniMax-M2.1"))
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": _system_prompt(locale)},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.85,
            "max_tokens": _max_tokens_for(_extract_content_id(prompt)),
        }
    ).encode()
    request = Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=float(os.getenv("MINIMAX_TIMEOUT_SECONDS", "120"))) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError(f"MiniMax indisponível: {type(exc).__name__}") from exc
    try:
        content = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Resposta MiniMax sem conteúdo") from exc
    content = re.sub(r"<think>[\s\S]*?</think>\s*", "", content).strip()
    if not content:
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
    expected = sections_for(content_id)
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


def generate_reading(content_id: str, title: str, profile, locale: str = "pt-BR", customer_name: str = "") -> ReadingResult:
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
    expected_sections = sections_for(content_id)
    if os.getenv("MINIMAX_API_KEY", "").strip():
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

            if expected_sections:
                parsed = _parse_markdown_sections(raw)
                guard_text = _sections_plain_text(parsed)
            else:
                parsed = []
                guard_text = raw

            # Guard de idioma continua rodando sobre o texto COMPLETO reconstruído
            # (não pula validação por causa do seccionamento).
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

            if expected_sections:
                min_sections = max(10, len(expected_sections) - 1)  # tolera 1 seção perdida no parse
                if len(parsed) < min_sections:
                    logger.warning(
                        "MiniMax retornou poucas seções parseáveis (%d de %d esperadas) na "
                        "tentativa %d/%d; refazendo (possível truncamento).",
                        len(parsed), len(expected_sections), attempt, attempts,
                    )
                    continue
                # Truncamento silencioso: a resposta pode ter todas as seções mas a
                # ÚLTIMA foi cortada no meio da frase pelo limite de max_tokens (é
                # sempre a última seção que sofre, porque é a última coisa gerada).
                last_content = (parsed[-1].get("content") or "") if parsed else ""
                if _looks_truncated(last_content):
                    logger.warning(
                        "MiniMax truncou a última seção ('%s') na tentativa %d/%d; refazendo. "
                        "Final observado: %r",
                        parsed[-1].get("title", "?") if parsed else "?",
                        attempt, attempts, last_content[-40:],
                    )
                    continue
                parsed = _canonicalize_titles(parsed, expected_sections)
                generated = _sections_to_html(parsed)
                if generated:
                    return ReadingResult(
                        body_html=generated,
                        source="minimax",
                        birth_time_assumed=birth_time_assumed,
                        ascendant_warning=ascendant_warning,
                        sections=parsed,
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
