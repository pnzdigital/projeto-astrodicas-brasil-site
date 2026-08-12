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

import logging
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import swisseph as swe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

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
logger = logging.getLogger(__name__)

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

# Regente do dia da semana — camada clássica independente do mapa da pessoa,
# muda todo dia e dá o clima geral antes de entrar nos trânsitos individuais.
# datetime.weekday(): 0=segunda ... 6=domingo.
WEEKDAY_RULER = ("Lua", "Marte", "Mercúrio", "Júpiter", "Vênus", "Saturno", "Sol")

DAY_RULER_TEXT: dict[str, dict[str, str]] = {
    "pt-BR": {
        "Sol": "Hoje é dia regido pelo Sol: o clima geral favorece o que precisa de exposição e decisão de quem está no comando — inclusive a sua, sobre a própria vida.",
        "Lua": "Hoje é dia regido pela Lua: o clima geral favorece o que é íntimo, doméstico e emocional, e pede menos exposição do que outros dias.",
        "Marte": "Hoje é dia regido por Marte: o clima geral favorece iniciativa e confronto direto, mas também deixa a mecha mais curta que o normal.",
        "Mercúrio": "Hoje é dia regido por Mercúrio: o clima geral favorece comunicação, negociação e resolver por trâmite em vez de força.",
        "Júpiter": "Hoje é dia regido por Júpiter: o clima geral favorece expansão, aprendizado e pedir mais do que você pediria em outro dia.",
        "Vênus": "Hoje é dia regido por Vênus: o clima geral favorece vínculo, prazer e negociação através do afeto em vez do embate.",
        "Saturno": "Hoje é dia regido por Saturno: o clima geral favorece estrutura, prazo e o tipo de trabalho que não dá resultado visível na hora.",
    },
    "es-AR": {
        "Sol": "Hoy es día regido por el Sol: el clima general favorece lo que necesita exposición y la decisión de quien está al mando — también la tuya, sobre tu propia vida.",
        "Lua": "Hoy es día regido por la Luna: el clima general favorece lo íntimo, lo doméstico y lo emocional, y pide menos exposición que otros días.",
        "Marte": "Hoy es día regido por Marte: el clima general favorece la iniciativa y el enfrentamiento directo, pero también deja la mecha más corta que lo normal.",
        "Mercúrio": "Hoy es día regido por Mercurio: el clima general favorece la comunicación, la negociación y resolver por trámite en vez de por fuerza.",
        "Júpiter": "Hoy es día regido por Júpiter: el clima general favorece la expansión, el aprendizaje y pedir más de lo que pedirías otro día.",
        "Vênus": "Hoy es día regido por Venus: el clima general favorece el vínculo, el placer y negociar a través del afecto en vez del choque.",
        "Saturno": "Hoy es día regido por Saturno: el clima general favorece la estructura, el plazo y el tipo de trabajo que no da resultado visible al toque.",
    },
}

# Fase lunar — ciclo de ~29,5 dias, camada mais lenta que o signo da Lua
# (~2,5 dias) e mais rápida que o trânsito de planetas pesados. Bucket de 90°
# em torno das quatro fases cardeais é aproximação honesta sem precisar de
# tabela de fases exatas.
MOON_PHASE_TEXT: dict[str, dict[str, str]] = {
    "pt-BR": {
        "nova": "A Lua está em fase nova: é território de começo baixo, sem plateia — o que você planta agora ainda não precisa ser mostrado a ninguém.",
        "crescente": "A Lua está em fase crescente: é fase de construir em cima do que já foi decidido, não de recomeçar do zero.",
        "cheia": "A Lua está em fase cheia: é fase de coisa aparecer — o que estava sendo evitado tende a virar visível hoje, com força maior que o normal.",
        "minguante": "A Lua está em fase minguante: é fase de soltar e fechar, não de iniciar — o que resiste em terminar pede mais atenção do que o que quer começar.",
    },
    "es-AR": {
        "nova": "La Luna está en fase nueva: es territorio de comienzo bajo, sin público — lo que plantás ahora todavía no necesita mostrarse a nadie.",
        "crescente": "La Luna está en fase creciente: es momento de construir sobre lo ya decidido, no de arrancar de cero.",
        "cheia": "La Luna está en fase llena: es momento en que las cosas aparecen — lo que se venía evitando tiende a volverse visible hoy, con más fuerza que de costumbre.",
        "minguante": "La Luna está en fase menguante: es momento de soltar y cerrar, no de iniciar — lo que se resiste a terminar pide más atención que lo que quiere empezar.",
    },
}

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

# Segundo aspecto mais fechado do dia — mesma lógica do ASPECT_LINE, fraseado
# como camada adicional em vez de repetir "o assunto de hoje é" duas vezes.
SECOND_ASPECT_LINE: dict[str, dict[str, str]] = {
    "pt-BR": {
        "fusao": "Tem outro fio puxando junto: o tema de {theme} também toca {point} hoje, reforçando o primeiro ponto em vez de disputar espaço com ele.",
        "harmonico": "Ainda corre a favor um segundo sinal: o assunto de {theme} encontra {point} sem atrito, uma abertura menor que a primeira, mas real.",
        "tenso": "Um segundo ponto de tensão aparece: o tema de {theme} também cobra de {point}, então o ajuste do dia não fica numa frente só.",
    },
    "es-AR": {
        "fusao": "Hay otro hilo tirando junto: el tema de {theme} también toca {point} hoy, reforzando el primer punto en vez de disputarle espacio.",
        "harmonico": "Todavía corre a favor una segunda señal: el asunto de {theme} encuentra a {point} sin fricción, una apertura menor que la primera, pero real.",
        "tenso": "Aparece un segundo punto de tensión: el tema de {theme} también le pide algo a {point}, así que el ajuste del día no queda en un solo frente.",
    },
}

# Anexado à frase do aspecto quando o planeta em trânsito está retrógrado —
# muda o conselho de "decidir" para "revisar", que é a leitura correta do
# fenômeno.
RETROGRADE_NOTE: dict[str, str] = {
    "pt-BR": " {transit} está retrógrado agora, e isso muda o conselho: esse assunto pede revisão do que já foi feito, não decisão nova.",
    "es-AR": " {transit} está retrógrado ahora, y eso cambia el consejo: este tema pide revisar lo que ya hiciste, no una decisión nueva.",
}

# Só entra quando a hora de nascimento foi assumida. Diz com honestidade o que
# não está sendo lido em vez de fingir precisão que a rota não tem.
BIRTH_TIME_HONESTY: dict[str, str] = {
    "pt-BR": "Um detalhe importante: você não informou o horário de nascimento, então o Ascendente usado aqui é estimado e pode errar o signo se você tiver nascido perto da virada. O resto da leitura — Sol, Lua e os trânsitos de hoje — não depende disso.",
    "es-AR": "Un detalle importante: no indicaste la hora de nacimiento, así que el Ascendente que se usó acá es estimado y puede errar el signo si naciste cerca del cambio. El resto de la lectura — Sol, Luna y los tránsitos de hoy — no depende de eso.",
}

# Sem aspecto fechado o dia é de fundo, e dizer isso é mais honesto do que
# inventar um evento astrológico que não está acontecendo. O texto precisa ser
# substantivo mesmo sem aspecto: dias de fundo têm uso próprio, e nomear esse
# uso é o que faz a leitura valer a leitura — não só o céu agitado.
QUIET_DAY: dict[str, str] = {
    "pt-BR": "Hoje nenhum trânsito rápido fecha aspecto exato com os pontos principais do seu mapa. Na prática é um dia de fundo: sem empurrão nem freio de fora, o que acontece é o que você puser em movimento. Dias assim costumam passar sem marca, mas têm um uso próprio: são ótimos para avançar no que estava parado por falta de clareza, revisar o que foi decidido nos dias mais carregados e deixar assentar o que ainda está no ar. O ritmo é seu — use isso.",
    "es-AR": "Hoy ningún tránsito rápido cierra aspecto exacto con los puntos principales de tu carta. En la práctica es un día de fondo: sin empujón ni freno de afuera, lo que pasa es lo que vos pongas en movimiento. Días así suelen pasar sin marca, pero tienen un uso propio: son buenos para avanzar en lo que estaba frenado por falta de claridad, revisar lo que se decidió en días más cargados y dejar reposar lo que todavía está en el aire. El ritmo es tuyo — usalo.",
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

# País e fuso padrão de cada vitrine, usados quando a landing não manda os
# campos. É a suposição menos errada: quem chega no /es/ nasceu, na esmagadora
# maioria, na Argentina.
LOCALE_DEFAULTS: dict[str, tuple[str, str]] = {
    "pt-BR": ("BR", "America/Sao_Paulo"),
    "es-AR": ("AR", "America/Argentina/Buenos_Aires"),
}

TITLE: dict[str, str] = {
    "pt-BR": "Seu horóscopo de hoje, {name}",
    "es-AR": "Tu horóscopo de hoy, {name}",
}

# O que fica do outro lado do cadastro. A UI usa para montar a oferta sem
# inventar o que o produto entrega.
LOCKED_LABEL: dict[str, str] = {
    "pt-BR": "Este é o horóscopo de hoje. Os próximos dias, o mapa astral completo e as leituras interpretadas fazem parte do Diário Astral.",
    "es-AR": "Este es el horóscopo de hoy. Los próximos días, la carta natal completa y las lecturas interpretadas forman parte del Diario Astral.",
}


class HoroscopeBody(BaseModel):
    # O nome entra no texto: é o que faz a leitura parecer dela e não de um
    # signo. Vem sem default de propósito — a landing sempre pergunta.
    name: str = Field(min_length=1, max_length=80)
    birth_date: date
    birth_time: time | None = None
    birth_city: str = Field(min_length=2, max_length=160)
    # Sem default fixo de propósito. "Córdoba" existe na Argentina e na Espanha,
    # "Santa Fe" na Argentina e nos EUA: assumir BR para uma visitante argentina
    # geocodificaria a cidade no país errado e o mapa inteiro sairia errado. Na
    # ausência do campo, o país e o fuso saem do idioma da vitrine.
    birth_country: str | None = Field(default=None, min_length=2, max_length=2)
    # UF (BR) ou provincia (AR). Cidades homônimas dentro do mesmo país —
    # "Santa Cruz" existe em vários estados do Brasil — só se resolvem certo
    # com isso. Opcional de propósito: requisição sem o campo (todo o
    # tráfego que já está no ar) cai no comportamento atual, sem quebrar.
    birth_state: str | None = Field(default=None, max_length=64)
    birth_timezone: str | None = Field(default=None, max_length=64)
    locale: str | None = Field(default=None, max_length=10)

    @field_validator("birth_time", mode="before")
    @classmethod
    def _blank_time_to_none(cls, value):
        return None if value == "" else value


def _first_name(name: str) -> str:
    """Só o primeiro nome: é assim que uma pessoa chama a outra."""
    return name.strip().split()[0][:40] if name.strip() else ""


def _julian_day(moment: datetime) -> float:
    utc = moment.astimezone(timezone.utc)
    hour = utc.hour + utc.minute / 60 + utc.second / 3600
    return swe.julday(utc.year, utc.month, utc.day, hour, swe.GREG_CAL)


def natal_chart(body: HoroscopeBody, locale: str) -> dict:
    """Sol, Lua e Ascendente natais — o mínimo para cruzar com o dia de hoje."""
    default_country, default_timezone = LOCALE_DEFAULTS[locale]
    country = (body.birth_country or default_country).upper()
    timezone_name = body.birth_timezone or default_timezone
    coordinates = astrology.resolve_coordinates(body.birth_city, country, body.birth_state)
    if not coordinates:
        logger.warning(
            "horoscopo/gratis: cidade nao resolvida city=%r state=%r country=%r locale=%s",
            body.birth_city, body.birth_state, country, locale,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=message("city_not_found", locale),
        )
    latitude, longitude = coordinates

    assumed_time = body.birth_time is None
    try:
        tz = ZoneInfo(timezone_name)
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


def _closed_aspects(natal_points: dict, transits: dict) -> list[dict]:
    """Todo aspecto fechado (orbe <= 3°) entre trânsito rápido e ponto natal,
    ordenado por orbe e depois por qualidade — o mesmo critério de
    :func:`strongest_aspect`, só que devolvendo a lista inteira em vez de só
    o primeiro item, para permitir um segundo parágrafo de trânsito real.
    """
    candidates: list[dict] = []
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
                candidates.append(
                    {
                        "transit": transit_name,
                        "point": point_name,
                        "aspect": label,
                        "quality": quality,
                        "orb": round(orb, 2),
                    }
                )
                break
    candidates.sort(key=lambda item: (item["orb"], ASPECT_PRIORITY[item["quality"]]))
    return candidates


def strongest_aspect(natal_points: dict, transits: dict) -> dict | None:
    """O aspecto que descreve o dia: orbe mais fechado, desempate por qualidade.

    Percorremos só ``TRANSIT_PLANETS`` contra Sol, Lua e Ascendente natais. O
    orbe de 3° é mais apertado que os 5° do mapa completo de propósito: aqui a
    pergunta é "o que está acontecendo HOJE", e 5° de orbe da Lua cobre mais de
    meio dia de margem.
    """
    candidates = _closed_aspects(natal_points, transits)
    return candidates[0] if candidates else None


def secondary_aspect(natal_points: dict, transits: dict, primary: dict | None) -> dict | None:
    """O segundo aspecto mais fechado do dia, distinto do primeiro.

    Duas pessoas raramente têm só um trânsito fechado no mesmo dia; usar os
    dois é sinal real a mais sem inventar nada — o segundo mais próximo do
    orbe exato ainda é astronomia, só é menos prioritário que o primeiro.
    """
    candidates = _closed_aspects(natal_points, transits)
    for candidate in candidates:
        if primary and candidate["transit"] == primary["transit"] and candidate["point"] == primary["point"]:
            continue
        return candidate
    return None


def moon_phase(transits: dict) -> str:
    """Fase lunar a partir do ângulo Sol-Lua em trânsito, em bucket de 90°.

    0-90 nova, 90-180 crescente, 180-270 cheia, 270-360 minguante — ciclo de
    ~29,5 dias, mais lento que o signo da Lua e mais rápido que os trânsitos
    de planetas pesados, então dá mais uma camada de variação real ao longo
    do mês sem depender de tabela de fases exatas.
    """
    angle = (transits["Lua"]["longitude"] - transits["Sol"]["longitude"]) % 360
    if angle < 90:
        return "nova"
    if angle < 180:
        return "crescente"
    if angle < 270:
        return "cheia"
    return "minguante"


def compose(
    name: str,
    natal: dict,
    transits: dict,
    aspect: dict | None,
    locale: str,
    weekday: int = 0,
    second_aspect: dict | None = None,
) -> dict:
    """Monta a leitura do dia. Determinístico: mesma entrada, mesmo texto.

    Estrutura: abertura personalizada (Lua em trânsito + nome), clima geral
    do dia (regente da semana + fase lunar), 1-2 blocos de trânsito real
    (aspecto mais fechado, e o segundo quando existir, com nota de
    retrógrado quando for o caso), honestidade sobre o Ascendente quando a
    hora foi assumida, e fechamento acionável ancorado no Sol natal.
    """
    first_name = _first_name(name)
    moon_sign = transits["Lua"]["sign"]
    sun_sign = natal["points"]["Sol"]["sign"]

    opening = MOON_TRANSIT[locale][moon_sign]
    # O nome entra uma vez só, na abertura. Repetir em todo parágrafo é o truque
    # de mala direta que denuncia texto automático.
    first = f"{first_name}, {opening[0].lower()}{opening[1:]}" if first_name else opening

    day_ruler = WEEKDAY_RULER[weekday % 7]
    climate = f"{DAY_RULER_TEXT[locale][day_ruler]} {MOON_PHASE_TEXT[locale][moon_phase(transits)]}"

    paragraphs = [first, climate]

    if aspect:
        primary = ASPECT_LINE[locale][aspect["quality"]].format(
            theme=TRANSIT_THEME[locale][aspect["transit"]],
            point=NATAL_POINT_LABELS[locale][aspect["point"]],
        )
        if transits[aspect["transit"]]["retrograde"]:
            primary += RETROGRADE_NOTE[locale].format(transit=PLANET_LABELS[locale][aspect["transit"]])
        paragraphs.append(primary)

        if second_aspect:
            secondary = SECOND_ASPECT_LINE[locale][second_aspect["quality"]].format(
                theme=TRANSIT_THEME[locale][second_aspect["transit"]],
                point=NATAL_POINT_LABELS[locale][second_aspect["point"]],
            )
            if transits[second_aspect["transit"]]["retrograde"]:
                secondary += RETROGRADE_NOTE[locale].format(
                    transit=PLANET_LABELS[locale][second_aspect["transit"]]
                )
            paragraphs.append(secondary)
    else:
        paragraphs.append(QUIET_DAY[locale])

    if natal["assumed_time"]:
        paragraphs.append(BIRTH_TIME_HONESTY[locale])

    paragraphs.append(PRACTICAL[locale][sun_sign])

    return {
        "title": TITLE[locale].format(name=first_name) if first_name else TITLE[locale].format(name="").rstrip(", "),
        "paragraphs": paragraphs,
        "body_html": "".join(f"<p>{paragraph}</p>" for paragraph in paragraphs),
    }


def local_today(locale: str) -> date:
    """O "hoje" da visitante, não o do servidor.

    O público está em UTC-3 e o servidor conta em UTC: das 21h à meia-noite no
    Brasil e na Argentina já é o dia seguinte em UTC. Contando por UTC, quem
    abrisse o site às 22h receberia o horóscopo de amanhã — e a validação de
    data de nascimento aceitaria uma data que ainda não chegou.
    """
    return datetime.now(ZoneInfo(LOCALE_DEFAULTS[locale][1])).date()


@router.post("/gratis", dependencies=[Depends(preview_rate_limit)])
def free_horoscope(body: HoroscopeBody, request: Request) -> dict:
    locale = pick_locale(body.locale, request.headers.get("accept-language"))

    if body.birth_date > local_today(locale):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=message("invalid_date", locale),
        )

    natal = natal_chart(body, locale)
    # Meio-dia do dia LOCAL dela: a Lua anda ~13°/dia, então o meio do dia é o
    # instante que melhor representa "hoje" — mas o dia tem que ser o dela.
    dia = local_today(locale)
    today = datetime(dia.year, dia.month, dia.day, 12, tzinfo=timezone.utc)
    transits = astrology._planet_positions(_julian_day(today))

    aspect = strongest_aspect(natal["points"], transits)
    second_aspect = secondary_aspect(natal["points"], transits, aspect) if aspect else None
    text = compose(
        body.name,
        natal,
        transits,
        aspect,
        locale,
        weekday=today.weekday(),
        second_aspect=second_aspect,
    )

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
        "second_aspect": (
            {
                **second_aspect,
                "transit_label": PLANET_LABELS[locale][second_aspect["transit"]],
            }
            if second_aspect
            else None
        ),
        # Hora não informada faz o Ascendente ser chute, e o Ascendente entra na
        # escolha do aspecto do dia. O aviso é o mesmo da prévia natal.
        "birth_time_assumed": natal["assumed_time"],
        "ascendant_warning": ASCENDANT_WARNING[locale] if natal["assumed_time"] else None,
        "locked": True,
        "locked_label": LOCKED_LABEL[locale],
    }
