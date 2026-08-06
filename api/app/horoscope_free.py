"""Horóscopo do dia grátis — a isca do tráfego pago.

O visitante cai do anúncio, informa nome, data, hora e cidade de nascimento e
recebe na hora um horóscopo do dia calculado contra o mapa natal dele. Sem
cadastro, sem pagamento. É o primeiro contato com o produto e precisa parecer
escrito para aquela pessoa — por isso o nome dela aparece no texto e os
trânsitos usados são os de hoje contra o mapa dela, não o signo solar genérico
de revista.

Três decisões deliberadas, na mesma linha do ``preview.py``:

1. **Nada de LLM.** Cada visitante anônimo viraria custo variável, e esta rota
   é ilimitada por design — é justamente a boca do funil pago. Todo o texto sai
   das tabelas estáticas abaixo, nos dois idiomas, combinadas pelos trânsitos
   reais. ``tests/test_horoscope_free.py`` trava isso.

2. **A personalização é astronômica, não redacional.** O que muda de pessoa
   para pessoa são os aspectos de hoje contra o Sol, a Lua e o Ascendente
   natais dela; o que muda de dia para dia é a Lua em trânsito, que troca de
   signo a cada ~2,5 dias. Duas pessoas diferentes no mesmo dia recebem textos
   diferentes, e a mesma pessoa em dias diferentes também.

3. **O teto do grátis continua sendo o mesmo.** Aqui entra o dia de hoje. A
   leitura interpretada, o mapa completo e a série dos próximos dias ficam do
   outro lado do cadastro/checkout — ``locked`` sinaliza isso para a UI.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import swisseph as swe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from . import astrology
from .preview import (
    ASCENDANT_WARNING,
    PLANET_LABELS,
    SIGN_LABELS,
    SUPPORTED_LOCALES,
    message,
    pick_locale,
)
from .ratelimit import preview_rate_limit


router = APIRouter(prefix="/api/horoscopo", tags=["horoscopo"])

# Só estes entram na leitura do dia. Urano, Netuno e Plutão levam anos no mesmo
# aspecto: citá-los faria o "horóscopo de hoje" repetir a mesma frase por meses,
# que é exatamente a sensação de texto genérico que esta rota existe para evitar.
TRANSIT_PLANETS = ("Sol", "Lua", "Mercúrio", "Vênus", "Marte", "Júpiter", "Saturno")

# Aspecto -> como o encontro se sente. Conjunção é fusão (intensifica), sextil e
# trígono abrem caminho, quadratura e oposição cobram.
ASPECT_QUALITY = {
    "conjunção": "fusao",
    "sextil": "harmonico",
    "trígono": "harmonico",
    "quadratura": "tenso",
    "oposição": "tenso",
}

# Peso para escolher QUAL aspecto vira o parágrafo do meio. Trânsito rápido com
# orbe fechado descreve o dia; Saturno a 5° de orbe descreve o semestre.
ASPECT_PRIORITY = {"fusao": 0, "tenso": 1, "harmonico": 2}

NATAL_POINTS = ("Sol", "Lua", "Ascendente")

# A Lua em trânsito é o relógio do dia: troca de signo a cada ~2,5 dias e é o
# que faz o texto de hoje não ser o de ontem.
MOON_TRANSIT: dict[str, dict[str, str]] = {
    "pt-BR": {
        "Áries": "A Lua passa hoje por Áries, e a pressa entra no corpo antes de entrar na agenda: o impulso de resolver agora vem forte, e esperar custa mais do que o normal.",
        "Touro": "A Lua passa hoje por Touro, e o dia pede ritmo próprio: as coisas rendem quando você não se apressa, e qualquer mudança brusca é recebida com resistência.",
        "Gêmeos": "A Lua passa hoje por Gêmeos, e a cabeça trabalha em várias frentes: conversa, informação e troca acalmam mais do que silêncio.",
        "Câncer": "A Lua passa hoje por Câncer, e a memória afetiva fica ligada: assuntos de casa, família e passado pesam mais do que pesariam em outro dia.",
        "Leão": "A Lua passa hoje por Leão, e existe uma necessidade legítima de ser visto: o que você faz hoje quer reconhecimento, não só resultado.",
        "Virgem": "A Lua passa hoje por Virgem, e o alívio vem de organizar: resolver pendência pequena limpa a cabeça mais do que qualquer conselho.",
        "Libra": "A Lua passa hoje por Libra, e o clima favorece acordo: o desconforto do dia é com desequilíbrio, não com pessoas.",
        "Escorpião": "A Lua passa hoje por Escorpião, e o que estava por baixo sobe: você percebe o não dito com mais clareza e tem menos paciência com superficialidade.",
        "Sagitário": "A Lua passa hoje por Sagitário, e a vontade é de espaço: o dia pede horizonte, e limitação apertada incomoda mais do que o normal.",
        "Capricórnio": "A Lua passa hoje por Capricórnio, e o humor fica sóbrio e prático: o que não é útil hoje parece perda de tempo.",
        "Aquário": "A Lua passa hoje por Aquário, e você olha a própria vida meio de fora: distância emocional aqui é clareza, não frieza.",
        "Peixes": "A Lua passa hoje por Peixes, e a fronteira entre o que é seu e o que é dos outros fica fina: cansaço sem motivo aparente costuma ser isso.",
    },
    "es-AR": {
        "Áries": "La Luna pasa hoy por Aries, y el apuro entra en el cuerpo antes que en la agenda: el impulso de resolver ya llega fuerte, y esperar cuesta más que de costumbre.",
        "Touro": "La Luna pasa hoy por Tauro, y el día pide ritmo propio: las cosas rinden cuando no te apurás, y cualquier cambio brusco se recibe con resistencia.",
        "Gêmeos": "La Luna pasa hoy por Géminis, y la cabeza trabaja en varios frentes: conversar, informarse e intercambiar calma más que el silencio.",
        "Câncer": "La Luna pasa hoy por Cáncer, y la memoria afectiva queda encendida: los temas de casa, familia y pasado pesan más que en otro día.",
        "Leão": "La Luna pasa hoy por Leo, y hay una necesidad legítima de ser visto: lo que hacés hoy busca reconocimiento, no sólo resultado.",
        "Virgem": "La Luna pasa hoy por Virgo, y el alivio viene de ordenar: resolver un pendiente chico despeja la cabeza más que cualquier consejo.",
        "Libra": "La Luna pasa hoy por Libra, y el clima favorece el acuerdo: la incomodidad del día es con el desequilibrio, no con las personas.",
        "Escorpião": "La Luna pasa hoy por Escorpio, y lo que estaba abajo sube: percibís lo no dicho con más claridad y tenés menos paciencia con lo superficial.",
        "Sagitário": "La Luna pasa hoy por Sagitario, y las ganas son de espacio: el día pide horizonte, y el límite apretado molesta más que de costumbre.",
        "Capricórnio": "La Luna pasa hoy por Capricornio, y el ánimo se pone sobrio y práctico: lo que hoy no sirve parece pérdida de tiempo.",
        "Aquário": "La Luna pasa hoy por Acuario, y mirás tu propia vida un poco desde afuera: acá la distancia emocional es claridad, no frialdad.",
        "Peixes": "La Luna pasa hoy por Piscis, y el borde entre lo tuyo y lo de los demás se afina: el cansancio sin motivo aparente suele ser eso.",
    },
}

# O assunto que cada trânsito traz. Entra no parágrafo do meio junto com a
# qualidade do aspecto.
TRANSIT_THEME: dict[str, dict[str, str]] = {
    "pt-BR": {
        "Sol": "sua visibilidade e o lugar que você ocupa",
        "Lua": "o que você sente e não costuma dizer",
        "Mercúrio": "conversas, combinados e o que precisa ser dito com clareza",
        "Vênus": "afeto, valor e o quanto você se permite receber",
        "Marte": "vontade, disputa e a energia de encarar",
        "Júpiter": "abertura, chance e o tamanho que você se dá",
        "Saturno": "responsabilidade, limite e o que não dá mais para adiar",
    },
    "es-AR": {
        "Sol": "tu visibilidad y el lugar que ocupás",
        "Lua": "lo que sentís y no solés decir",
        "Mercúrio": "conversaciones, acuerdos y lo que necesita decirse con claridad",
        "Vênus": "afecto, valor y cuánto te permitís recibir",
        "Marte": "voluntad, disputa y la energía de enfrentar",
        "Júpiter": "apertura, oportunidad y el tamaño que te das",
        "Saturno": "responsabilidad, límite y lo que ya no se puede postergar",
    },
}

NATAL_POINT_LABELS: dict[str, dict[str, str]] = {
    "pt-BR": {"Sol": "seu Sol", "Lua": "sua Lua", "Ascendente": "seu Ascendente"},
    "es-AR": {"Sol": "tu Sol", "Lua": "tu Luna", "Ascendente": "tu Ascendente"},
}

# ``{theme}`` é o assunto do trânsito, ``{point}`` o ponto natal tocado.
ASPECT_LINE: dict[str, dict[str, str]] = {
    "pt-BR": {
        "fusao": "O assunto de hoje é {theme}, e ele cai direto sobre {point}: não fica no plano de fundo, ocupa o centro do dia. Não é um bom dia para agir no automático nessa área — o que você decidir aqui pesa.",
        "harmonico": "Hoje o dia abre caminho no que envolve {theme}, e faz isso a favor de {point}: o que você tentar nessa direção encontra menos atrito do que encontraria em outro dia. É o tipo de janela que passa despercebida se você não usar.",
        "tenso": "Hoje o dia põe em atrito {theme}, e a fricção bate em {point}: alguma coisa nessa área cobra ajuste, e a tentação é tratar como problema de fora quando a decisão é sua. O desconforto aqui é informação, não sinal de que algo deu errado.",
    },
    "es-AR": {
        "fusao": "El asunto de hoy es {theme}, y cae directo sobre {point}: no queda de fondo, ocupa el centro del día. No es buen día para actuar en automático en esa área — lo que decidas acá pesa.",
        "harmonico": "Hoy el día abre camino en lo que involucra {theme}, y lo hace a favor de {point}: lo que intentes en esa dirección encuentra menos fricción que en otro día. Es el tipo de ventana que pasa desapercibida si no la usás.",
        "tenso": "Hoy el día pone en fricción {theme}, y el roce pega en {point}: algo en esa área pide ajuste, y la tentación es tratarlo como problema de afuera cuando la decisión es tuya. Acá la incomodidad es información, no señal de que algo salió mal.",
    },
}

# Sem aspecto fechado o dia é de fundo, e dizer isso é mais honesto do que
# inventar um evento astrológico que não está acontecendo.
QUIET_DAY: dict[str, str] = {
    "pt-BR": "Hoje nenhum trânsito rápido fecha aspecto exato com os pontos principais do seu mapa. Na prática é um dia de fundo: sem empurrão nem freio de fora, o que acontece é o que você puser em movimento.",
    "es-AR": "Hoy ningún tránsito rápido cierra aspecto exacto con los puntos principales de tu carta. En la práctica es un día de fondo: sin empujón ni freno de afuera, lo que pasa es lo que vos pongas en movimiento.",
}

# Fechamento prático, ancorado no Sol natal — o que a pessoa já reconhece como
# "a cara dela".
PRACTICAL: dict[str, dict[str, str]] = {
    "pt-BR": {
        "Áries": "Como o seu Sol é ariano, o erro do dia não vai ser falta de coragem, e sim gastar o impulso na primeira coisa que aparecer. Escolha uma frente só e leve até o fim.",
        "Touro": "Como o seu Sol é taurino, seu instinto vai ser adiar até ter certeza. Hoje vale dar o primeiro passo com a informação que já existe, mesmo que ela não esteja completa.",
        "Gêmeos": "Como o seu Sol é geminiano, a saída não é buscar mais informação, é decidir com a que você já tem. Fechar um assunto hoje vale mais do que abrir três.",
        "Câncer": "Como o seu Sol é canceriano, você vai sentir antes de entender. Deixe a reação assentar algumas horas antes de responder ao que te incomodou.",
        "Leão": "Como o seu Sol é leonino, o que te move é ser reconhecido no que faz. Hoje peça o reconhecimento diretamente em vez de esperar que percebam sozinhos.",
        "Virgem": "Como o seu Sol é virginiano, o risco do dia é confundir cuidado com autocrítica. Entregue no padrão que resolve, não no padrão que nunca te satisfaz.",
        "Libra": "Como o seu Sol é libriano, você vai pesar o lado dos outros antes do seu. Hoje diga o que você quer antes de perguntar o que o outro prefere.",
        "Escorpião": "Como o seu Sol é escorpiano, você vai enxergar a intenção por trás do gesto. Confira antes de agir sobre a leitura — sua percepção costuma estar certa no fundo e errada no detalhe.",
        "Sagitário": "Como o seu Sol é sagitariano, o impulso é ampliar. Hoje o ganho está em terminar o que já foi começado, não em abrir a próxima frente.",
        "Capricórnio": "Como o seu Sol é capricorniano, você vai medir o dia pelo que produziu. Inclua descanso na conta — hoje ele é parte do resultado, não subtração dele.",
        "Aquário": "Como o seu Sol é aquariano, sua tendência é resolver por fora, no plano das ideias. Hoje a conversa direta com uma pessoa específica resolve mais que qualquer reformulação.",
        "Peixes": "Como o seu Sol é pisciano, você absorve o clima do ambiente. Antes de decidir qualquer coisa hoje, separe o que é seu do que você pegou de outra pessoa.",
    },
    "es-AR": {
        "Áries": "Como tu Sol es ariano, el error del día no va a ser falta de coraje, sino gastar el impulso en la primera cosa que aparezca. Elegí un solo frente y llevalo hasta el final.",
        "Touro": "Como tu Sol es taurino, tu instinto va a ser postergar hasta estar seguro. Hoy conviene dar el primer paso con la información que ya tenés, aunque no esté completa.",
        "Gêmeos": "Como tu Sol es geminiano, la salida no es buscar más información, es decidir con la que ya tenés. Cerrar un tema hoy vale más que abrir tres.",
        "Câncer": "Como tu Sol es canceriano, vas a sentir antes de entender. Dejá que la reacción se asiente unas horas antes de responder a lo que te incomodó.",
        "Leão": "Como tu Sol es leonino, lo que te mueve es que reconozcan lo que hacés. Hoy pedí el reconocimiento directamente en vez de esperar que se den cuenta solos.",
        "Virgem": "Como tu Sol es virginiano, el riesgo del día es confundir cuidado con autocrítica. Entregá en el estándar que resuelve, no en el que nunca te conforma.",
        "Libra": "Como tu Sol es libriano, vas a pesar el lado de los demás antes que el tuyo. Hoy decí lo que querés antes de preguntar qué prefiere el otro.",
        "Escorpião": "Como tu Sol es escorpiano, vas a ver la intención detrás del gesto. Verificá antes de actuar sobre esa lectura — tu percepción suele acertar en el fondo y errar en el detalle.",
        "Sagitário": "Como tu Sol es sagitariano, el impulso es ampliar. Hoy la ganancia está en terminar lo empezado, no en abrir el próximo frente.",
        "Capricórnio": "Como tu Sol es capricorniano, vas a medir el día por lo que produjiste. Incluí el descanso en la cuenta — hoy es parte del resultado, no una resta.",
        "Aquário": "Como tu Sol es acuariano, tu tendencia es resolver por afuera, en el plano de las ideas. Hoy la conversación directa con una persona concreta resuelve más que cualquier reformulación.",
        "Peixes": "Como tu Sol es pisciano, absorbés el clima del ambiente. Antes de decidir cualquier cosa hoy, separá lo que es tuyo de lo que agarraste de otra persona.",
    },
}

TITLE: dict[str, str] = {
    "pt-BR": "Seu horóscopo de hoje, {name}",
    "es-AR": "Tu horóscopo de hoy, {name}",
}

# O que fica do outro lado do cadastro. A UI usa para montar a oferta sem
# inventar o que o produto entrega.
LOCKED_LABEL: dict[str, str] = {
    "pt-BR": "Este é o horóscopo de hoje. Os próximos dias, o mapa astral completo e as leituras interpretadas fazem parte do Plano Lua.",
    "es-AR": "Este es el horóscopo de hoy. Los próximos días, la carta natal completa y las lecturas interpretadas forman parte del Plan Luna.",
}


class HoroscopeBody(BaseModel):
    # O nome entra no texto: é o que faz a leitura parecer dela e não de um
    # signo. Vem sem default de propósito — a landing sempre pergunta.
    name: str = Field(min_length=1, max_length=80)
    birth_date: date
    birth_time: time | None = None
    birth_city: str = Field(min_length=2, max_length=160)
    birth_country: str = Field(default="BR", min_length=2, max_length=2)
    birth_timezone: str = Field(default="America/Sao_Paulo", max_length=64)
    locale: str | None = Field(default=None, max_length=10)


def _first_name(name: str) -> str:
    """Só o primeiro nome: é assim que uma pessoa chama a outra."""
    return name.strip().split()[0][:40] if name.strip() else ""


def _julian_day(moment: datetime) -> float:
    utc = moment.astimezone(timezone.utc)
    hour = utc.hour + utc.minute / 60 + utc.second / 3600
    return swe.julday(utc.year, utc.month, utc.day, hour, swe.GREG_CAL)


def natal_chart(body: HoroscopeBody, locale: str) -> dict:
    """Sol, Lua e Ascendente natais — o mínimo para cruzar com o dia de hoje."""
    coordinates = astrology.resolve_coordinates(body.birth_city, body.birth_country)
    if not coordinates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=message("city_not_found", locale),
        )
    latitude, longitude = coordinates

    assumed_time = body.birth_time is None
    try:
        tz = ZoneInfo(body.birth_timezone)
    except Exception:
        tz = timezone.utc
    local_dt = datetime.combine(body.birth_date, body.birth_time or time(0, 0)).replace(tzinfo=tz)

    try:
        julian_day = _julian_day(local_dt)
        positions = astrology._planet_positions(julian_day)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=message("calculation_failed", locale),
        )

    points = {"Sol": positions["Sol"], "Lua": positions["Lua"]}
    try:
        _, angles = swe.houses_ex(julian_day, latitude, longitude, b"P")
        points["Ascendente"] = astrology._sign_position(angles[0])
    except Exception:
        # Sem Ascendente a leitura continua de pé com Sol e Lua; o que não pode
        # é a rota cair por causa de uma latitude extrema.
        pass

    return {"points": points, "assumed_time": assumed_time}


def strongest_aspect(natal_points: dict, transits: dict) -> dict | None:
    """O aspecto que descreve o dia: orbe mais fechado, desempate por qualidade.

    Percorremos só ``TRANSIT_PLANETS`` contra Sol, Lua e Ascendente natais. O
    orbe de 3° é mais apertado que os 5° do mapa completo de propósito: aqui a
    pergunta é "o que está acontecendo HOJE", e 5° de orbe da Lua cobre mais de
    meio dia de margem.
    """
    best: dict | None = None
    for transit_name in TRANSIT_PLANETS:
        transit = transits.get(transit_name)
        if not transit:
            continue
        for point_name in NATAL_POINTS:
            point = natal_points.get(point_name)
            if not point:
                continue
            distance = abs(transit["longitude"] - point["longitude"]) % 360
            distance = min(distance, 360 - distance)
            for angle, label in astrology.ASPECTS:
                orb = abs(distance - angle)
                if orb > 3.0:
                    continue
                quality = ASPECT_QUALITY[label]
                candidate = {
                    "transit": transit_name,
                    "point": point_name,
                    "aspect": label,
                    "quality": quality,
                    "orb": round(orb, 2),
                }
                key = (round(orb, 2), ASPECT_PRIORITY[quality])
                if best is None or key < (best["orb"], ASPECT_PRIORITY[best["quality"]]):
                    best = candidate
                break
    return best


def compose(name: str, natal: dict, transits: dict, aspect: dict | None, locale: str) -> dict:
    """Monta os três parágrafos. Determinístico: mesma entrada, mesmo texto."""
    first_name = _first_name(name)
    moon_sign = transits["Lua"]["sign"]
    sun_sign = natal["points"]["Sol"]["sign"]

    opening = MOON_TRANSIT[locale][moon_sign]
    # O nome entra uma vez só, na abertura. Repetir em todo parágrafo é o truque
    # de mala direta que denuncia texto automático.
    first = f"{first_name}, {opening[0].lower()}{opening[1:]}" if first_name else opening

    if aspect:
        middle = ASPECT_LINE[locale][aspect["quality"]].format(
            theme=TRANSIT_THEME[locale][aspect["transit"]],
            point=NATAL_POINT_LABELS[locale][aspect["point"]],
        )
    else:
        middle = QUIET_DAY[locale]

    closing = PRACTICAL[locale][sun_sign]

    return {
        "title": TITLE[locale].format(name=first_name) if first_name else TITLE[locale].format(name="").rstrip(", "),
        "paragraphs": [first, middle, closing],
        "body_html": "".join(f"<p>{paragraph}</p>" for paragraph in (first, middle, closing)),
    }


@router.post("/gratis", dependencies=[Depends(preview_rate_limit)])
def free_horoscope(body: HoroscopeBody, request: Request) -> dict:
    locale = pick_locale(body.locale, request.headers.get("accept-language"))

    if body.birth_date > datetime.now(timezone.utc).date():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=message("invalid_date", locale),
        )

    natal = natal_chart(body, locale)
    # Meio-dia UTC: a Lua anda ~13°/dia, então o meio do dia é o valor que
    # melhor representa "hoje" para qualquer fuso do público (BR e AR).
    today = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    transits = astrology._planet_positions(_julian_day(today))

    aspect = strongest_aspect(natal["points"], transits)
    text = compose(body.name, natal, transits, aspect, locale)

    return {
        "locale": locale,
        "date": today.date().isoformat(),
        **text,
        "sun": {
            "sign": natal["points"]["Sol"]["sign"],
            "sign_label": SIGN_LABELS[locale][natal["points"]["Sol"]["sign"]],
        },
        "moon_transit": {
            "sign": transits["Lua"]["sign"],
            "sign_label": SIGN_LABELS[locale][transits["Lua"]["sign"]],
        },
        "aspect": (
            {
                **aspect,
                "transit_label": PLANET_LABELS[locale][aspect["transit"]],
            }
            if aspect
            else None
        ),
        # Hora não informada faz o Ascendente ser chute, e o Ascendente entra na
        # escolha do aspecto do dia. O aviso é o mesmo da prévia natal.
        "birth_time_assumed": natal["assumed_time"],
        "ascendant_warning": ASCENDANT_WARNING[locale] if natal["assumed_time"] else None,
        "locked": True,
        "locked_label": LOCKED_LABEL[locale],
    }
