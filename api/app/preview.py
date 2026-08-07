"""Prévia grátis do mapa natal — porta de entrada pública do produto.

Por que existe: até aqui o site não entregava nada antes da compra. Esta rota
calcula Sol, Lua, Ascendente e as posições planetárias na hora, sem cadastro e
sem pagamento, e para exatamente aí: casas, aspectos e a leitura interpretada
continuam sendo o produto pago.

Duas decisões deliberadas:

1. **Nada de LLM.** Os parágrafos de Sol/Lua/Ascendente vêm da tabela estática
   ``SIGN_TEXTS`` abaixo, nos dois idiomas. Chamar a MiniMax aqui transformaria
   cada visitante anônimo em custo variável, e a prévia é ilimitada por design.
   ``tests/test_preview.py::test_preview_does_not_call_the_llm`` trava isso.

2. **A matemática é reaproveitada, não reescrita.** ``app.astrology`` já é
   validado por testes; aqui usamos ``resolve_coordinates`` e
   ``_planet_positions`` dele e só acrescentamos o Ascendente (``swe.houses_ex``).
   Não chamamos ``astrology_context`` inteiro porque ele calcula trânsitos e
   aspectos que a prévia descarta — seria custo de CPU por visitante sem uso.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import swisseph as swe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from . import astrology
from .ratelimit import preview_rate_limit


router = APIRouter(prefix="/api/preview", tags=["preview"])

SUPPORTED_LOCALES = {"pt-BR", "es-AR"}

# Rótulo do signo na língua do visitante. A chave continua sendo o nome
# canônico em pt-BR usado por ``astrology.SIGNS`` — assim o backend tem uma
# única identidade de signo e a UI recebe o texto certo.
SIGN_LABELS: dict[str, dict[str, str]] = {
    "pt-BR": {sign: sign for sign in astrology.SIGNS},
    "es-AR": {
        "Áries": "Aries",
        "Touro": "Tauro",
        "Gêmeos": "Géminis",
        "Câncer": "Cáncer",
        "Leão": "Leo",
        "Virgem": "Virgo",
        "Libra": "Libra",
        "Escorpião": "Escorpio",
        "Sagitário": "Sagitario",
        "Capricórnio": "Capricornio",
        "Aquário": "Acuario",
        "Peixes": "Piscis",
    },
}

PLANET_LABELS: dict[str, dict[str, str]] = {
    "pt-BR": {name: name for name, _ in astrology.PLANETS},
    "es-AR": {
        "Sol": "Sol",
        "Lua": "Luna",
        "Mercúrio": "Mercurio",
        "Vênus": "Venus",
        "Marte": "Marte",
        "Júpiter": "Júpiter",
        "Saturno": "Saturno",
        "Urano": "Urano",
        "Netuno": "Neptuno",
        "Plutão": "Plutón",
    },
}

# Um parágrafo curto por signo, por luminar, por idioma. Texto editorial fixo:
# descreve o arquétipo do posicionamento, sem prometer previsão nem substituir
# a leitura paga (que é personalizada e cruza casas e aspectos).
SIGN_TEXTS: dict[str, dict[str, dict[str, str]]] = {
    "pt-BR": {
        "Áries": {
            "sun": "Com o Sol em Áries, sua identidade se organiza em torno da iniciativa: você entende quem é fazendo, não esperando. Costuma decidir rápido, encarar o começo das coisas sem muito ensaio e perder o interesse quando o assunto vira rotina.",
            "moon": "A Lua em Áries pede reação imediata: você sente primeiro e explica depois. A emoção chega quente e passa rápido, e o que mais incomoda é ter que engolir uma resposta ou esperar o tempo dos outros.",
            "ascendant": "Ascendente em Áries entrega uma presença direta — as pessoas percebem em você energia e franqueza antes de qualquer apresentação. A primeira impressão costuma ser de alguém que toma a frente.",
        },
        "Touro": {
            "sun": "Com o Sol em Touro, sua identidade se firma no que dá para sustentar: você constrói devagar e não gosta de refazer. Segurança, prazer concreto e constância pesam mais nas suas decisões do que novidade.",
            "moon": "A Lua em Touro precisa de previsibilidade para se acalmar: rotina, conforto físico e vínculos que não mudam de temperatura. Você demora a se abalar, mas também demora a soltar o que já machucou.",
            "ascendant": "Ascendente em Touro passa serenidade e firmeza — o mundo te lê como alguém difícil de apressar. A primeira impressão é de presença estável, mesmo quando por dentro nada está estável.",
        },
        "Gêmeos": {
            "sun": "Com o Sol em Gêmeos, sua identidade se constrói pela troca: você existe conversando, lendo, comparando versões. Precisa de variedade para não murchar e costuma ter mais de um assunto sério ao mesmo tempo.",
            "moon": "A Lua em Gêmeos processa sentimento em palavras: enquanto você não nomeia o que sente, aquilo fica girando. Inquietação e curiosidade são também formas de autocuidado aqui.",
            "ascendant": "Ascendente em Gêmeos dá uma entrada leve e comunicativa — você puxa assunto com facilidade e é lido como alguém curioso e acessível, mesmo em ambientes novos.",
        },
        "Câncer": {
            "sun": "Com o Sol em Câncer, sua identidade passa por pertencimento: quem você é tem endereço, memória e gente. Você protege o que é seu e mede o valor das coisas pelo vínculo que elas criam.",
            "moon": "A Lua em Câncer sente muito e guarda tudo: sua memória emocional é longa e detalhada. Cuidar dos outros é um jeito legítimo de se acalmar, mas cobra que alguém também cuide de você.",
            "ascendant": "Ascendente em Câncer transmite acolhimento e certa reserva — as pessoas se aproximam de você buscando abrigo, e você só abre a porta inteira depois de sentir segurança.",
        },
        "Leão": {
            "sun": "Com o Sol em Leão, sua identidade quer ser vista inteira: você funciona melhor quando o que faz tem sua assinatura. Generosidade e orgulho andam juntos, e reconhecimento não é vaidade, é combustível.",
            "moon": "A Lua em Leão precisa de calor afetivo declarado: gestos, presença, resposta. O silêncio de quem você ama dói mais do que uma discussão franca.",
            "ascendant": "Ascendente em Leão dá presença luminosa — você é notado antes de falar. A primeira impressão costuma ser de alguém confiante, mesmo em dias em que a confiança não está lá.",
        },
        "Virgem": {
            "sun": "Com o Sol em Virgem, sua identidade se organiza pelo aperfeiçoamento: você repara no detalhe que os outros deixam passar e sente que sua contribuição precisa ser útil, não só bonita.",
            "moon": "A Lua em Virgem se acalma resolvendo: arrumar, listar, ajustar. O risco é transformar ansiedade em autocrítica e cobrar de si um padrão que você jamais exigiria de ninguém.",
            "ascendant": "Ascendente em Virgem passa discrição e competência — as pessoas confiam tarefas a você rapidamente. A primeira impressão é de alguém observador, que fala pouco e nota muito.",
        },
        "Libra": {
            "sun": "Com o Sol em Libra, sua identidade se define na relação: você se conhece pelo espelho do outro. Justiça, estética e equilíbrio orientam suas escolhas, e decidir sozinho é o exercício difícil.",
            "moon": "A Lua em Libra fica desconfortável no conflito aberto e busca acordo antes de sentir raiva. Cuidado com adiar a própria vontade em nome da paz — ela volta cobrada.",
            "ascendant": "Ascendente em Libra dá uma entrada agradável e diplomática — você desarma ambientes tensos. A primeira impressão é de alguém elegante e fácil de conviver.",
        },
        "Escorpião": {
            "sun": "Com o Sol em Escorpião, sua identidade se aprofunda em ciclos: você morre e renasce em temas que outras pessoas só tangenciam. Intensidade e privacidade convivem — você entrega tudo, mas para poucos.",
            "moon": "A Lua em Escorpião sente em profundidade e desconfia do raso. Você percebe o que não foi dito e guarda; perdoar exige de você uma decisão consciente, não acontece sozinho.",
            "ascendant": "Ascendente em Escorpião passa magnetismo e reserva — as pessoas sentem que há mais por trás do que você mostra. A primeira impressão raramente é neutra.",
        },
        "Sagitário": {
            "sun": "Com o Sol em Sagitário, sua identidade precisa de horizonte: sentido, viagem, estudo, crença. Você se entedia no fechado e organiza a vida em torno de para onde ela está indo.",
            "moon": "A Lua em Sagitário se acalma quando enxerga saída: um plano, uma explicação maior, uma viagem marcada. Sentir-se preso emocionalmente é o desconforto mais difícil aqui.",
            "ascendant": "Ascendente em Sagitário dá uma entrada franca e expansiva — você chega com humor e opinião. A primeira impressão é de alguém que anima o ambiente e diz o que pensa.",
        },
        "Capricórnio": {
            "sun": "Com o Sol em Capricórnio, sua identidade se prova no tempo: você constrói para durar e mede a si mesmo por responsabilidade cumprida. Amadureceu cedo em alguma área e cobra de si um resultado concreto.",
            "moon": "A Lua em Capricórnio contém a emoção antes de mostrá-la. Você se sente seguro quando está no controle, e pedir ajuda soa quase como falha — mas é justamente o que alivia.",
            "ascendant": "Ascendente em Capricórnio transmite seriedade e autocontrole — as pessoas te tratam como referência antes de te conhecer. A primeira impressão é de alguém confiável e um pouco distante.",
        },
        "Aquário": {
            "sun": "Com o Sol em Aquário, sua identidade se afirma na diferença: você pensa fora do combinado e não aceita uma regra só porque sempre foi assim. Pertencer sem se dissolver no grupo é sua equação.",
            "moon": "A Lua em Aquário precisa de espaço para sentir: cobrança emocional afasta, liberdade aproxima. Você entende a própria emoção melhor de longe do que no meio dela.",
            "ascendant": "Ascendente em Aquário dá uma entrada original e um pouco imprevisível — você é lido como alguém independente. A primeira impressão costuma ser de originalidade antes de intimidade.",
        },
        "Peixes": {
            "sun": "Com o Sol em Peixes, sua identidade é porosa: você capta o clima do ambiente antes de entender por quê. Sensibilidade, imaginação e compaixão são seus recursos — e também o que precisa de limite.",
            "moon": "A Lua em Peixes absorve o que está em volta e nem sempre distingue o que é seu. Solidão escolhida, arte e silêncio funcionam como filtro, não como fuga.",
            "ascendant": "Ascendente em Peixes passa doçura e uma certa névoa — as pessoas projetam bastante em você. A primeira impressão é de alguém acolhedor e difícil de definir.",
        },
    },
    "es-AR": {
        "Áries": {
            "sun": "Con el Sol en Aries, tu identidad se organiza alrededor de la iniciativa: entendés quién sos haciendo, no esperando. Decidís rápido, encarás los comienzos sin demasiado ensayo y perdés interés cuando el asunto se vuelve rutina.",
            "moon": "La Luna en Aries pide reacción inmediata: primero sentís y después explicás. La emoción llega caliente y pasa rápido, y lo que más te incomoda es tener que tragarte una respuesta.",
            "ascendant": "Ascendente en Aries entrega una presencia directa: la gente percibe energía y franqueza antes de cualquier presentación. La primera impresión suele ser la de alguien que toma la delantera.",
        },
        "Touro": {
            "sun": "Con el Sol en Tauro, tu identidad se afirma en lo que se puede sostener: construís despacio y no te gusta rehacer. Seguridad, placer concreto y constancia pesan más en tus decisiones que la novedad.",
            "moon": "La Luna en Tauro necesita previsibilidad para calmarse: rutina, confort físico y vínculos que no cambian de temperatura. Tardás en alterarte, pero también tardás en soltar lo que ya dolió.",
            "ascendant": "Ascendente en Tauro transmite serenidad y firmeza: el mundo te lee como alguien difícil de apurar. La primera impresión es de presencia estable, aunque por dentro nada lo esté.",
        },
        "Gêmeos": {
            "sun": "Con el Sol en Géminis, tu identidad se construye en el intercambio: existís conversando, leyendo, comparando versiones. Necesitás variedad para no apagarte y solés tener más de un tema serio a la vez.",
            "moon": "La Luna en Géminis procesa el sentimiento en palabras: mientras no nombrás lo que sentís, eso sigue girando. La inquietud y la curiosidad también son formas de cuidarte acá.",
            "ascendant": "Ascendente en Géminis da una entrada liviana y comunicativa: arrancás conversaciones con facilidad y te leen como alguien curioso y accesible, incluso en ambientes nuevos.",
        },
        "Câncer": {
            "sun": "Con el Sol en Cáncer, tu identidad pasa por la pertenencia: quien sos tiene dirección, memoria y gente. Protegés lo tuyo y medís el valor de las cosas por el vínculo que crean.",
            "moon": "La Luna en Cáncer siente mucho y guarda todo: tu memoria emocional es larga y detallada. Cuidar a los demás te calma, pero pide que alguien también te cuide a vos.",
            "ascendant": "Ascendente en Cáncer transmite refugio y cierta reserva: la gente se acerca buscando abrigo, y vos abrís la puerta entera recién cuando sentís seguridad.",
        },
        "Leão": {
            "sun": "Con el Sol en Leo, tu identidad quiere ser vista entera: funcionás mejor cuando lo que hacés lleva tu firma. Generosidad y orgullo van juntos, y el reconocimiento no es vanidad, es combustible.",
            "moon": "La Luna en Leo necesita calor afectivo declarado: gestos, presencia, respuesta. El silencio de quien querés duele más que una discusión franca.",
            "ascendant": "Ascendente en Leo da presencia luminosa: te notan antes de que hables. La primera impresión suele ser la de alguien confiado, incluso en días en que la confianza no está.",
        },
        "Virgem": {
            "sun": "Con el Sol en Virgo, tu identidad se organiza por el perfeccionamiento: notás el detalle que otros dejan pasar y sentís que tu aporte tiene que ser útil, no solo lindo.",
            "moon": "La Luna en Virgo se calma resolviendo: ordenar, listar, ajustar. El riesgo es convertir la ansiedad en autocrítica y exigirte un estándar que jamás le pedirías a otro.",
            "ascendant": "Ascendente en Virgo transmite discreción y competencia: te confían tareas rápido. La primera impresión es la de alguien observador, que habla poco y registra mucho.",
        },
        "Libra": {
            "sun": "Con el Sol en Libra, tu identidad se define en el vínculo: te conocés en el espejo del otro. Justicia, estética y equilibrio guían tus elecciones, y decidir en soledad es el ejercicio difícil.",
            "moon": "La Luna en Libra se incomoda en el conflicto abierto y busca acuerdo antes de sentir bronca. Cuidado con postergar tu propia voluntad en nombre de la paz: vuelve con factura.",
            "ascendant": "Ascendente en Libra da una entrada amable y diplomática: desarmás ambientes tensos. La primera impresión es la de alguien elegante y fácil de tratar.",
        },
        "Escorpião": {
            "sun": "Con el Sol en Escorpio, tu identidad se profundiza en ciclos: morís y renacés en temas que otros apenas rozan. Intensidad y privacidad conviven: entregás todo, pero a pocos.",
            "moon": "La Luna en Escorpio siente en profundidad y desconfía de lo superficial. Percibís lo que no se dijo y lo guardás; perdonar te exige una decisión consciente, no ocurre solo.",
            "ascendant": "Ascendente en Escorpio transmite magnetismo y reserva: sienten que hay más detrás de lo que mostrás. La primera impresión rara vez es neutra.",
        },
        "Sagitário": {
            "sun": "Con el Sol en Sagitario, tu identidad necesita horizonte: sentido, viaje, estudio, creencia. Te aburre lo cerrado y ordenás la vida alrededor de hacia dónde va.",
            "moon": "La Luna en Sagitario se calma cuando ve salida: un plan, una explicación más grande, un viaje con fecha. Sentirte atrapado emocionalmente es acá la incomodidad más difícil.",
            "ascendant": "Ascendente en Sagitario da una entrada franca y expansiva: llegás con humor y opinión. La primera impresión es la de alguien que levanta el ambiente y dice lo que piensa.",
        },
        "Capricórnio": {
            "sun": "Con el Sol en Capricornio, tu identidad se prueba en el tiempo: construís para durar y te medís por responsabilidad cumplida. Maduraste temprano en alguna área y te exigís un resultado concreto.",
            "moon": "La Luna en Capricornio contiene la emoción antes de mostrarla. Te sentís seguro con el control en la mano, y pedir ayuda suena casi a falla, cuando es justo lo que alivia.",
            "ascendant": "Ascendente en Capricornio transmite seriedad y autocontrol: te tratan como referencia antes de conocerte. La primera impresión es la de alguien confiable y algo distante.",
        },
        "Aquário": {
            "sun": "Con el Sol en Acuario, tu identidad se afirma en la diferencia: pensás fuera de lo pactado y no aceptás una regla solo porque siempre fue así. Pertenecer sin disolverte en el grupo es tu ecuación.",
            "moon": "La Luna en Acuario necesita espacio para sentir: la exigencia emocional aleja, la libertad acerca. Entendés tu propia emoción mejor desde afuera que en el medio de ella.",
            "ascendant": "Ascendente en Acuario da una entrada original y algo imprevisible: te leen como alguien independiente. La primera impresión suele ser de originalidad antes que de intimidad.",
        },
        "Peixes": {
            "sun": "Con el Sol en Piscis, tu identidad es porosa: captás el clima del ambiente antes de entender por qué. Sensibilidad, imaginación y compasión son tus recursos, y también lo que necesita límite.",
            "moon": "La Luna en Piscis absorbe lo que hay alrededor y no siempre distingue qué es tuyo. La soledad elegida, el arte y el silencio funcionan como filtro, no como huida.",
            "ascendant": "Ascendente en Piscis transmite dulzura y cierta niebla: la gente proyecta bastante en vos. La primera impresión es la de alguien acogedor y difícil de definir.",
        },
    },
}

PREVIEW_MESSAGES: dict[str, dict[str, str]] = {
    "city_not_found": {
        "pt-BR": "Não encontramos essa cidade. Confira o nome ou tente a capital mais próxima.",
        "es-AR": "No encontramos esa ciudad. Revisá el nombre o probá con la capital más cercana.",
    },
    "invalid_date": {
        "pt-BR": "Informe uma data de nascimento válida (dia, mês e ano).",
        "es-AR": "Ingresá una fecha de nacimiento válida (día, mes y año).",
    },
    "invalid_time": {
        "pt-BR": "Informe um horário de nascimento válido, no formato 14:30.",
        "es-AR": "Ingresá una hora de nacimiento válida, en formato 14:30.",
    },
    "invalid_city": {
        "pt-BR": "Informe a cidade de nascimento.",
        "es-AR": "Ingresá la ciudad de nacimiento.",
    },
    "invalid_input": {
        "pt-BR": "Confira os dados de nascimento e tente de novo.",
        "es-AR": "Revisá los datos de nacimiento y probá de nuevo.",
    },
    "calculation_failed": {
        "pt-BR": "Não conseguimos calcular seu mapa com esses dados. Tente novamente.",
        "es-AR": "No pudimos calcular tu carta con esos datos. Probá de nuevo.",
    },
}

# Aviso que o frontend cola no bloco do Ascendente quando a hora não foi
# informada. Texto é deliberadamente firme: o Ascendente é o ponto do mapa
# mais sensível à hora (troca de signo a cada ~2h), então a estimativa com
# 00:00 provavelmente NÃO é o Ascendente real. Se a pessoa souber a hora,
# reabrir a prévia com o campo preenchido é o único jeito de corrigir.
ASCENDANT_WARNING: dict[str, str] = {
    "pt-BR": (
        "Hora de nascimento não informada — assumimos 00:00 só para mostrar um valor. "
        "O Ascendente é o ponto mais sensível à hora do mapa inteiro (troca de signo a cada ~2h), "
        "então este resultado é uma ESTIMATIVA e provavelmente NÃO é o seu Ascendente real. "
        "Se você souber a hora (mesmo que aproximada), refaça a prévia preenchendo o campo."
    ),
    "es-AR": (
        "Hora de nacimiento no informada — asumimos 00:00 solo para mostrar un valor. "
        "El Ascendente es el punto más sensible a la hora de toda la carta (cambia de signo cada ~2h), "
        "así que este resultado es una ESTIMACIÓN y probablemente NO es tu Ascendente real. "
        "Si sabés la hora (aunque sea aproximada), rehacé la vista previa completando el campo."
    ),
}


def pick_locale(locale: str | None, accept_language: str | None = None) -> str:
    if locale and locale in SUPPORTED_LOCALES:
        return locale
    if accept_language:
        primary = accept_language.split(",")[0].split(";")[0].strip()
        if primary in SUPPORTED_LOCALES:
            return primary
        if primary.startswith("es"):
            return "es-AR"
    return "pt-BR"


def message(key: str, locale: str) -> str:
    table = PREVIEW_MESSAGES.get(key) or PREVIEW_MESSAGES["invalid_input"]
    return table.get(locale, table["pt-BR"])


def validation_detail(errors, locale: str) -> str:
    """Traduz o primeiro erro do Pydantic numa frase só, na língua do visitante.

    Mesma política das rotas de auth: o cliente nunca recebe o array de erros
    do Pydantic (vaza estrutura interna e é ilegível para o usuário final).
    """
    for error in errors:
        field = (error.get("loc") or [""])[-1]
        if field == "birth_date":
            return message("invalid_date", locale)
        if field == "birth_time":
            return message("invalid_time", locale)
        if field == "birth_city":
            return message("invalid_city", locale)
    return message("invalid_input", locale)


class PreviewBody(BaseModel):
    birth_date: date
    birth_time: time | None = None
    birth_city: str = Field(min_length=2, max_length=160)
    birth_country: str = Field(default="BR", min_length=2, max_length=2)
    birth_timezone: str = Field(default="America/Sao_Paulo", max_length=64)
    locale: str | None = Field(default=None, max_length=10)

    @field_validator("birth_time", mode="before")
    @classmethod
    def _blank_time_to_none(cls, value):
        return None if value == "" else value


def _luminary(position: dict, kind: str, locale: str) -> dict:
    sign = position["sign"]
    return {
        "sign": sign,
        "sign_label": SIGN_LABELS[locale][sign],
        "degree": position["degree"],
        "text": SIGN_TEXTS[locale][sign][kind],
    }


@router.post("/natal", dependencies=[Depends(preview_rate_limit)])
def natal_preview(body: PreviewBody, request: Request) -> dict:
    locale = pick_locale(body.locale, request.headers.get("accept-language"))

    # Data futura não é nascimento. Barramos antes de gastar geocoding.
    if body.birth_date > datetime.now(timezone.utc).date():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message("invalid_date", locale))

    coordinates = astrology.resolve_coordinates(body.birth_city, body.birth_country)
    if not coordinates:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message("city_not_found", locale))
    latitude, longitude = coordinates

    approximate_time = body.birth_time is None
    # Sem hora informada, assumimos 00:00 local explícito. Antes a API usava
    # 12:00 silencioso (mantido para não regredir quem já dependia), mas o
    # Ascendente varia ~30° a cada 4h de hora — usar 12:00 é uma escolha que
    # não significa nada para o visitante. 00:00 é documentável como
    # "início do dia" e vem com aviso explicando que a estimativa provavelmente
    # muda se a hora real for outra.
    local_time = body.birth_time or time(0, 0)
    try:
        tz = ZoneInfo(body.birth_timezone)
    except Exception:
        tz = timezone.utc
    local_dt = datetime.combine(body.birth_date, local_time).replace(tzinfo=tz)
    utc_dt = local_dt.astimezone(timezone.utc)
    decimal_hour = utc_dt.hour + utc_dt.minute / 60 + utc_dt.second / 3600

    try:
        julian_day = swe.julday(utc_dt.year, utc_dt.month, utc_dt.day, decimal_hour, swe.GREG_CAL)
        positions = astrology._planet_positions(julian_day)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=message("calculation_failed", locale),
        )

    # Ascendente é o dado do mapa mais sensível à hora: troca de signo a cada
    # ~2h. Quando o visitante não sabe a hora, calculamos igual usando 00:00
    # (acima) e devolvemos o sinal ``birth_time_assumed`` + texto localizado
    # explicando que o Ascendente é ESTIMADO — o cliente não pode levar como
    # verdade. Quando a hora foi informada, simplesmente devolvemos o cálculo.
    ascendant_warning: dict[str, str] | None = None
    try:
        _, angles = swe.houses_ex(julian_day, latitude, longitude, b"P")
        ascendant = _luminary(astrology._sign_position(angles[0]), "ascendant", locale)
        if approximate_time:
            ascendant_warning = ASCENDANT_WARNING
    except Exception:
        ascendant = None

    return {
        "locale": locale,
        # A prévia é o teto do grátis: casas, aspectos e a leitura interpretada
        # ficam do outro lado do checkout. A UI usa esta flag para montar o CTA.
        "locked": True,
        # Mantido para retrocompat: hoje a prévia sempre calcula Ascendente
        # (mesmo que com hora assumida), então ``birth_time_approximate`` continua
        # descrevendo se a hora veio do usuário. A nova flag ``birth_time_assumed``
        # deixa explícito que a API escolheu uma hora por ele.
        "birth_time_approximate": approximate_time,
        "birth_time_assumed": approximate_time,
        "sun": _luminary(positions["Sol"], "sun", locale),
        "moon": _luminary(positions["Lua"], "moon", locale),
        "ascendant": ascendant,
        "ascendant_warning": ascendant_warning,
        "planets": [
            {
                "name": name,
                "label": PLANET_LABELS[locale][name],
                "sign": positions[name]["sign"],
                "sign_label": SIGN_LABELS[locale][positions[name]["sign"]],
                "degree": positions[name]["degree"],
                "retrograde": positions[name]["retrograde"],
            }
            for name, _ in astrology.PLANETS
        ],
    }
