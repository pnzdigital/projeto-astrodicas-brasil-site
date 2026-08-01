import html
import json
import logging
import os
import re
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .astrology import astrology_context


logger = logging.getLogger(__name__)


def sun_sign(birth_date: date | None) -> str:
    if not birth_date:
        return "seu signo solar"
    month_day = (birth_date.month, birth_date.day)
    signs = [
        ((1, 20), "Aquário"), ((2, 19), "Peixes"), ((3, 21), "Áries"),
        ((4, 20), "Touro"), ((5, 21), "Gêmeos"), ((6, 21), "Câncer"),
        ((7, 23), "Leão"), ((8, 23), "Virgem"), ((9, 23), "Libra"),
        ((10, 23), "Escorpião"), ((11, 22), "Sagitário"), ((12, 22), "Capricórnio"),
    ]
    for boundary, sign in reversed(signs):
        if month_day >= boundary:
            return sign
    return "Capricórnio"


def _profile_context(profile, customer_name: str = "") -> dict:
    return {
        "customer_name": customer_name or "não informado",
        "birth_date": profile.birth_date.isoformat() if profile and profile.birth_date else "não informado",
        "birth_time": profile.birth_time.isoformat() if profile and profile.birth_time else "não informado",
        "birth_city": profile.birth_city if profile and profile.birth_city else "não informado",
        "birth_country": profile.birth_country if profile else "não informado",
        "birth_timezone": profile.birth_timezone if profile else "não informado",
        "sun_sign": sun_sign(profile.birth_date if profile else None),
        "partner_name": profile.partner_name if profile and profile.partner_name else "não informado",
        "partner_birth_date": profile.partner_birth_date.isoformat() if profile and profile.partner_birth_date else "não informado",
        "partner_birth_time": profile.partner_birth_time.isoformat() if profile and profile.partner_birth_time else "não informado",
        "partner_birth_city": profile.partner_birth_city if profile and profile.partner_birth_city else "não informado",
        "calculated_chart": astrology_context(profile),
    }


def _prompt(content_id: str, title: str, profile, locale: str, customer_name: str = "") -> str:
    context = _profile_context(profile, customer_name)
    language = "espanhol rioplatense natural" if locale == "es-AR" else "português brasileiro natural"
    today = date.today().isoformat()
    rules = {
        "site:content:horoscopo_diario": "Escreva exatamente 3 parágrafos substanciais, com 90 a 130 palavras cada. Use prioritariamente os trânsitos atuais para o mapa natal. O primeiro cria identificação emocional, o segundo aborda relações e trabalho e o terceiro traz direção prática.",
        "site:content:mapa_astral_completo": "Escreva uma leitura natal premium com 10 a 14 parágrafos. Cubra tríade principal, planetas pessoais, casas, aspectos dominantes, potenciais e tensões. Use apenas posições presentes no calculated_chart.",
        "site:content:mapa_do_amor_sinastria": "Escreva 9 a 12 parágrafos sobre padrões afetivos do cliente. Se os dados do parceiro estiverem incompletos, explique com delicadeza que a comparação completa depende deles e não invente posições do parceiro.",
        "site:content:mapa_da_carreira": "Escreva 8 a 11 parágrafos sobre talentos, rotina, visibilidade, vocação e decisões profissionais, ancorando cada afirmação nas casas, planetas e aspectos calculados.",
        "site:content:mapa_da_prosperidade": "Escreva 8 a 11 parágrafos sobre recursos, segurança, merecimento e oportunidades, sem prometer ganhos financeiros. Relacione a leitura às posições calculadas.",
        "site:content:previsao_semanal": "Escreva 7 parágrafos, um para o panorama e seis para temas e decisões da semana, usando os trânsitos atuais calculados.",
        "site:content:guia_do_mes": "Escreva 8 a 10 parágrafos com temas do mês, momentos de atenção e práticas concretas, usando o céu atual e o mapa natal.",
        "site:content:calendario_lunar": "Escreva um guia editorial do ciclo lunar atual em 7 a 9 parágrafos. Não invente datas que não estejam nos dados; quando faltarem, trate como guia de uso das fases.",
        "site:content:guia_dos_retrogrados": "Escreva 7 a 9 parágrafos explicando os planetas retrógrados presentes no céu calculado e como atravessar revisões sem fatalismo.",
        "site:content:manual_do_ascendente": "Escreva 8 a 10 parágrafos sobre o Ascendente calculado, seu regente simbólico, presença, corpo e primeira impressão. Se não houver Ascendente calculado, explique que a hora exata é necessária.",
    }
    content_rule = rules.get(content_id, "Escreva uma leitura premium profunda, com 7 a 10 parágrafos.")
    return f"""Você é a astróloga editorial da AstroDicas. Produza a leitura \"{title}\" em {language}.
Data de referência: {today}. Identificador: {content_id}.
Dados autorizados do cliente: {json.dumps(context, ensure_ascii=False)}.

{content_rule}
Use o nome do cliente com naturalidade no máximo duas vezes. Use somente os dados fornecidos. Não invente
Ascendente, Lua, casas, aspectos, trânsitos ou posições planetárias que não tenham sido calculados. Quando faltar
cálculo astronômico, declare a limitação com linguagem acolhedora. Não faça diagnóstico médico, promessa financeira nem previsão
fatalista. Não cite inteligência artificial. Não use markdown, listas, HTML ou título; devolva apenas os
parágrafos, separados por uma linha em branco."""


def _call_minimax(prompt: str) -> str:
    api_key = os.getenv("MINIMAX_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY não configurada")
    base_url = os.getenv("MINIMAX_BASE_URL", os.getenv("LLM_BASE_URL", "https://api.minimax.io/v1")).rstrip("/")
    model = os.getenv("MINIMAX_MODEL", os.getenv("LLM_MODEL_TEXT", "MiniMax-M2.1"))
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": "Siga o briefing editorial com precisão e entregue somente o texto final."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.85,
            "max_tokens": 2400,
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


def _fallback_reading(profile, locale: str) -> str:
    sign = sun_sign(profile.birth_date if profile else None)
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


def generate_reading(content_id: str, title: str, profile, locale: str = "pt-BR", customer_name: str = "") -> str:
    if os.getenv("MINIMAX_API_KEY", "").strip():
        try:
            generated = _paragraphs_to_html(_call_minimax(_prompt(content_id, title, profile, locale, customer_name)))
            if generated:
                return generated
        except RuntimeError as exc:
            logger.warning("MiniMax falhou; usando fallback editorial: %s", exc)
    return _fallback_reading(profile, locale)
