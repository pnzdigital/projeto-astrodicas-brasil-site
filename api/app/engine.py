from datetime import date


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


def generate_reading(content_id: str, title: str, profile, locale: str = "pt-BR") -> str:
    sign = sun_sign(profile.birth_date if profile else None)
    city = profile.birth_city or "seu lugar de nascimento" if profile else "seu lugar de nascimento"
    if locale == "es-AR":
        return (
            f"<p>Esta leitura começa pelo seu Sol em {sign}, observado a partir de {city}. "
            "Use estas palavras como um espelho: elas não fecham o seu destino, mas ajudam a perceber "
            "o padrão que está pedindo atenção agora.</p>"
            f"<p>O tema de hoje é presença. Há algo que você já sabe sobre si, mas ainda está tentando "
            "negociar com as expectativas dos outros. Escolha uma atitude pequena e concreta que respeite "
            "o seu ritmo.</p>"
            f"<p>Volte a esta leitura quando o céu mudar. A sua experiência em {city} e o seu Sol em {sign} "
            "são pontos de partida para leituras mais profundas que podem ser adicionadas ao seu portal.</p>"
        )
    return (
        f"<p>Esta leitura começa pelo seu Sol em {sign}, observado a partir de {city}. "
        "Use estas palavras como um espelho: elas não fecham o seu destino, mas ajudam a perceber "
        "o padrão que está pedindo atenção agora.</p>"
        f"<p>O tema de hoje é presença. Há algo que você já sabe sobre si, mas ainda está tentando "
        "negociar com as expectativas dos outros. Escolha uma atitude pequena e concreta que respeite "
        "o seu ritmo.</p>"
        f"<p>Volte a esta leitura quando o céu mudar. A sua experiência em {city} e o seu Sol em {sign} "
        "são pontos de partida para leituras mais profundas que podem ser adicionadas ao seu portal.</p>"
    )
