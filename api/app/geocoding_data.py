"""Coordenadas offline de cidades grandes/capitais do Brasil e da Argentina.

Cobre o destino mais provável do trafego pago (capitais de estado/provincia
e principais polos urbanos) sem depender de rede: sem essa base, toda
resolucao de cidade batia direto no Nominatim (ver astrology.py), o unico
ponto de falha que derrubava o horoscopo gratis quando a cidade nao era
encontrada, a consulta estourava o timeout ou a politica de uso do
Nominatim (1 req/s, sem chave) barrava.

Nao e a lista completa do IBGE (5570 municipios) nem do INDEC argentino:
nao ha pacote de geocoding offline instalado no projeto (pgeocode/geopy)
e adicionar um dataset municipal inteiro e uma dependencia pesada demais
para este problema. As coordenadas abaixo foram obtidas do proprio
Nominatim (mesma fonte do fallback online) e congeladas aqui.

Chave: nome da cidade normalizado por normalize_city_query (sem acento,
minusculo, sem sufixo de UF/pais). Valor: (latitude, longitude).
"""

OFFLINE_CITIES: dict[str, dict[str, tuple[float, float]]] = {
    "BR": {
        "rio branco": (-9.9765362, -67.8220778),
        "maceio": (-9.6476843, -35.7339264),
        "macapa": (0.0401529, -51.0569588),
        "manaus": (-3.1316333, -59.9825041),
        "salvador": (-12.9822499, -38.4812772),
        "fortaleza": (-3.7932167, -38.5280359),
        "brasilia": (-15.7939869, -47.8828),
        "vitoria": (-20.3200917, -40.3376682),
        "goiania": (-16.680882, -49.2532691),
        "sao luis": (-2.5295265, -44.2963942),
        "cuiaba": (-15.5986686, -56.0991301),
        "campo grande": (-20.4640173, -54.6162947),
        "belo horizonte": (-19.9227318, -43.9450948),
        "belem": (-1.45056, -48.4682453),
        "joao pessoa": (-7.1215981, -34.882028),
        "curitiba": (-25.4295963, -49.2712724),
        "recife": (-8.0584933, -34.8848193),
        "teresina": (-5.0874608, -42.8049571),
        "rio de janeiro": (-22.9110137, -43.2093727),
        "natal": (-5.805398, -35.2080905),
        "porto alegre": (-30.0324999, -51.2303767),
        "porto velho": (-8.7494525, -63.8735438),
        "boa vista": (2.8208478, -60.6719582),
        "florianopolis": (-27.5973002, -48.5496098),
        "sao paulo": (-23.5506507, -46.6333824),
        "aracaju": (-10.9162061, -37.0774655),
        "palmas": (-10.1837852, -48.3336423),
        "petrolina": (-9.3817334, -40.4968875),
        "juazeiro": (-9.5137512, -40.3078985),
        "caruaru": (-8.2829702, -35.9722852),
        "campinas": (-22.9056391, -47.059564),
        "guarulhos": (-23.4675941, -46.5277704),
        "sorocaba": (-23.5003451, -47.4582864),
        "ribeirao preto": (-21.1776315, -47.8100983),
        "sao jose dos campos": (-23.1867782, -45.8854538),
        "santo andre": (-23.6533509, -46.5279039),
        "osasco": (-23.5324859, -46.7916801),
        "uberlandia": (-18.9188041, -48.2767837),
        "contagem": (-19.9132749, -44.0840953),
        "feira de santana": (-12.2578934, -38.9598047),
        "joinville": (-26.3044898, -48.8486726),
        "londrina": (-23.4761271, -51.1179013),
        "niteroi": (-22.8884, -43.1147),
        "duque de caxias": (-22.6429163, -43.3021266),
        "nova iguacu": (-22.6955964, -43.4654372),
        "aparecida de goiania": (-16.8226769, -49.2452546),
        "ananindeua": (-1.374035, -48.4016623),
        "caxias do sul": (-29.1685045, -51.1796385),
        "sao bernardo do campo": (-23.7080345, -46.5506747),
        "sao jose do rio preto": (-20.8125851, -49.3804212),
        "mogi das cruzes": (-23.5234284, -46.1926671),
        "jaboatao dos guararapes": (-8.1752476, -34.9468716),
        "vitoria da conquista": (-14.8567487, -40.8414804),
        "uberaba": (-19.750833, -47.936666),
        "anapolis": (-16.3332828, -48.9525756),
        "blumenau": (-26.9195567, -49.0658025),
        "ponta grossa": (-25.0891685, -50.1601812),
        "franca": (-20.5381768, -47.4009795),
        "piracicaba": (-22.725165, -47.6493269),
        "bauru": (-22.3173803, -49.0689778),
        "itaquaquecetuba": (-23.4754492, -46.3514033),
        "montes claros": (-16.7495727, -43.8687268),
    },
    "AR": {
        "buenos aires": (-34.6095579, -58.3887904),
        "cordoba": (-31.4166867, -64.1834193),
        "rosario": (-32.9593609, -60.6617024),
        "mendoza": (-32.8894155, -68.8446177),
        "san miguel de tucuman": (-26.8303703, -65.2038133),
        "la plata": (-34.9206797, -57.9537638),
        "mar del plata": (-37.9976168, -57.5482079),
        "salta": (-25.2269908, -64.5911956),
        "santa fe": (-30.3154739, -61.1645076),
        "san juan": (-30.7054363, -69.1988222),
        "resistencia": (-27.6048829, -59.1932003),
        "neuquen": (-38.8502546, -69.832275),
        "santiago del estero": (-27.6431016, -63.5408542),
        "corrientes": (-29.0177384, -57.8869739),
        "posadas": (-27.3664824, -55.894295),
        "bahia blanca": (-38.7176522, -62.2654871),
        "san salvador de jujuy": (-24.1852569, -65.2994789),
        "parana": (-31.7330145, -60.5298511),
        "formosa": (-24.5955306, -60.4289718),
        "rio cuarto": (-33.1237585, -64.3489782),
        # CABA: apelido comum pra Cidade Autônoma de Buenos Aires, mesmas coordenadas.
        "caba": (-34.6095579, -58.3887904),
        "ciudad autonoma de buenos aires": (-34.6095579, -58.3887904),
        # Grande Buenos Aires (conurbano): concentra boa parte do tráfego pago
        # argentino e não tinha nenhuma cidade do cordão fora da capital.
        "san isidro": (-34.4708225, -58.5271399),
        "vicente lopez": (-34.5266331, -58.4779081),
        "quilmes": (-34.7205196, -58.2703865),
        "lanus": (-34.7058656, -58.3927285),
        "avellaneda": (-34.6626597, -58.3654568),
        "moron": (-34.6534247, -58.6198937),
        "moreno": (-34.6473376, -58.7908956),
        "merlo": (-34.6656672, -58.7285256),
        "tigre": (-34.4263145, -58.5796101),
        "san justo": (-34.6839729, -58.5631452),
        "florencio varela": (-34.8222774, -58.2751877),
        "san nicolas de los arroyos": (-33.335833, -60.213056),
        "concordia": (-31.3930591, -58.0209457),
        "san rafael": (-34.6177153, -68.3301442),
        "villa maria": (-32.4076162, -63.2418432),
        "comodoro rivadavia": (-45.8641876, -67.4964792),
        "rio gallegos": (-51.6229941, -69.2181458),
        "ushuaia": (-54.8069332, -68.3073246),
        "santa rosa": (-36.6166738, -64.2833834),
        "viedma": (-40.813328, -63.0006405),
    },
}

"""Cidades homônimas: mesmo nome, coordenadas bem diferentes conforme o
estado/provincia. "Santa Cruz" existe em varios estados do Brasil (aqui: Rio
Grande do Norte e Pernambuco) e "Cordoba"/"Santa Fe" existem tanto na
Argentina quanto fora dela — por isso o pais ja resolve esse ultimo caso.
Dentro do mesmo pais, so o estado/provincia desambigua.

Chave externa: pais (BR/AR). Chave do meio: sigla da UF (BR, minuscula) ou
provincia normalizada por normalize_city_query (AR). Chave interna: cidade
normalizada, igual a tabela de cima.
"""
OFFLINE_CITIES_BY_STATE: dict[str, dict[str, dict[str, tuple[float, float]]]] = {
    "BR": {
        "rn": {
            "santa cruz": (-6.2280556, -36.0263889),
        },
        "pe": {
            "santa cruz": (-7.9768923, -39.0300758),
        },
    },
    "AR": {},
}
