import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import swisseph as swe

from .geocoding_data import OFFLINE_CITIES


SIGNS = (
    "Áries", "Touro", "Gêmeos", "Câncer", "Leão", "Virgem",
    "Libra", "Escorpião", "Sagitário", "Capricórnio", "Aquário", "Peixes",
)
PLANETS = (
    ("Sol", swe.SUN), ("Lua", swe.MOON), ("Mercúrio", swe.MERCURY),
    ("Vênus", swe.VENUS), ("Marte", swe.MARS), ("Júpiter", swe.JUPITER),
    ("Saturno", swe.SATURN), ("Urano", swe.URANUS), ("Netuno", swe.NEPTUNE),
    ("Plutão", swe.PLUTO),
)
ASPECTS = ((0, "conjunção"), (60, "sextil"), (90, "quadratura"), (120, "trígono"), (180, "oposição"))


# Cidade pode chegar como "Petrolina/PE", "Petrolina - PE" ou "Petrolina, Brasil":
# a landing não força formato. Isolamos só o nome da cidade (primeiro token
# antes do separador) tanto para bater na tabela offline quanto para montar
# uma query online mais limpa.
_CITY_SEPARATORS = re.compile(r"\s*(?:/|-|,)\s*")


def normalize_city_query(raw: str) -> str:
    city = _CITY_SEPARATORS.split(raw.strip(), maxsplit=1)[0]
    stripped = unicodedata.normalize("NFD", city)
    without_accents = "".join(ch for ch in stripped if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-zA-Z ]", " ", without_accents)).strip().lower()


# Nominatim é de uso público e cobrado por chamada (política de 1 req/s sem
# chave): repetir a mesma cidade em cada acesso ao horóscopo grátis desperdiça
# a cota e reintroduz o mesmo risco que a tabela offline resolve. Cache de
# processo é suficiente aqui — não é dado sensível e o processo reinicia a
# cada deploy.
_ONLINE_CACHE: dict[tuple[str, str], tuple[float, float] | None] = {}


def _geocode_online(city: str, country: str) -> tuple[float, float] | None:
    query = urlencode({"q": f"{city}, {country}", "format": "jsonv2", "limit": 1})
    request = Request(
        f"https://nominatim.openstreetmap.org/search?{query}",
        headers={"User-Agent": "AstroDicas/1.0 (portal astrodicas.pnzdigital.com.br)"},
    )
    try:
        with urlopen(request, timeout=float(os.getenv("GEOCODING_TIMEOUT_SECONDS", "8"))) as response:
            rows = json.loads(response.read().decode("utf-8"))
        if rows:
            return float(rows[0]["lat"]), float(rows[0]["lon"])
    except (OSError, ValueError, KeyError, IndexError):
        return None
    return None


def resolve_coordinates(city: str, country: str) -> tuple[float, float] | None:
    """Cidade -> (lat, lon), sem depender só do Nominatim.

    Ordem: tabela offline das capitais/polos urbanos do BR e da AR (rápida,
    sem rede, cobre a maior parte do tráfego pago) e só então o Nominatim
    como reforço para cidades fora da tabela — nunca como via única, e com
    cache de processo para não repetir a mesma chamada.
    """
    if not city or os.getenv("GEOCODING_ENABLED", "1") != "1":
        return None
    country = (country or "").upper()
    normalized = normalize_city_query(city)
    offline = OFFLINE_CITIES.get(country, {}).get(normalized)
    if offline:
        return offline

    cache_key = (normalized, country)
    if cache_key in _ONLINE_CACHE:
        return _ONLINE_CACHE[cache_key]
    result = _geocode_online(city, country)
    _ONLINE_CACHE[cache_key] = result
    return result


def _sign_position(longitude: float) -> dict:
    value = longitude % 360
    index = int(value // 30)
    return {"sign": SIGNS[index], "degree": round(value % 30, 2), "longitude": round(value, 4)}


def _planet_positions(julian_day: float) -> dict:
    flags = swe.FLG_MOSEPH | swe.FLG_SPEED
    result = {}
    for name, planet_id in PLANETS:
        values, _ = swe.calc_ut(julian_day, planet_id, flags)
        result[name] = {**_sign_position(values[0]), "retrograde": values[3] < 0}
    return result


def _house_for(longitude: float, cusps: tuple[float, ...]) -> int:
    """Return the 1-12 house for ``longitude`` given 12 Placidus cusps.

    Convention: a planet at a cusp belongs to the house that starts there.
    Cusps may wrap around 0° (e.g. polar Placidus when ``cusps[11] > cusps[0]``),
    so we handle both monotonic and wrapped ranges. The non-wrap branch is
    half-open ``[start, end)``; the wrap branch is ``[start, 360) ∪ [0, end)``
    so the cusp value is counted only once — at the start of the next house.
    """
    value = longitude % 360
    for index, start in enumerate(cusps):
        end = cusps[(index + 1) % 12]
        if start <= end:
            if start <= value < end:
                return index + 1
        else:  # wrap around 0°
            if value >= start or value < end:
                return index + 1
    return 1


def _aspects(left: dict, right: dict | None = None, orb_limit: float = 5.0) -> list[dict]:
    right = right or left
    same_set = right is left
    matches = []
    for left_index, (left_name, left_data) in enumerate(left.items()):
        for right_index, (right_name, right_data) in enumerate(right.items()):
            if same_set and right_index <= left_index:
                continue
            distance = abs(left_data["longitude"] - right_data["longitude"]) % 360
            distance = min(distance, 360 - distance)
            for angle, label in ASPECTS:
                orb = abs(distance - angle)
                if orb <= orb_limit:
                    matches.append({"from": left_name, "aspect": label, "to": right_name, "orb": round(orb, 2)})
                    break
    return sorted(matches, key=lambda item: item["orb"])


def astrology_context(profile) -> dict:
    if not profile or not profile.birth_date:
        return {"status": "missing_birth_date"}
    latitude = float(profile.birth_latitude) if profile.birth_latitude not in (None, "") else None
    longitude = float(profile.birth_longitude) if profile.birth_longitude not in (None, "") else None
    geocoding_status = "not_required"
    geocoding_source = None
    if latitude is None or longitude is None:
        if os.getenv("GEOCODING_ENABLED", "1") != "1":
            geocoding_status = "disabled"
        else:
            resolved = resolve_coordinates(profile.birth_city, profile.birth_country)
            if resolved:
                latitude, longitude = resolved
                geocoding_status = "resolved"
                geocoding_source = "nominatim"
            else:
                geocoding_status = "unresolved"
    coord_error = _validate_coordinates(latitude, longitude)
    if coord_error:
        chart = {
            "status": "invalid_coordinates",
            "coordinate_error": coord_error,
            "birth_utc": None,
            "coordinates": {"latitude": latitude, "longitude": longitude},
            "houses_status": "invalid_coordinates",
            "geocoding_status": geocoding_status,
            "geocoding_source": geocoding_source,
        }
        return chart
    local_time = profile.birth_time
    approximate_time = local_time is None
    # Sem hora informada: assumimos 00:00 do fuso local explicitamente (não 12:00
    # silencioso). Mesma decisão aplicada na prévia grátis — Ascendente é
    # calculado e marcado como estimado, em vez de devolvido null. A leitura
    # completa (paga) passa a receber um Ascendente num chart que o LLM pode
    # descrever com honestidade, em vez de um chart sem Ascendente que
    # instruiria o LLM a omitir o tema inteiro silenciosamente.
    hour = local_time.hour if local_time else 0
    minute = local_time.minute if local_time else 0
    second = local_time.second if local_time else 0
    try:
        tz = ZoneInfo(profile.birth_timezone or "UTC")
    except Exception:
        tz = timezone.utc
    local_dt = datetime.combine(profile.birth_date, datetime.min.time()).replace(hour=hour, minute=minute, second=second, tzinfo=tz)
    utc_dt = local_dt.astimezone(timezone.utc)
    decimal_hour = utc_dt.hour + utc_dt.minute / 60 + utc_dt.second / 3600
    natal_jd = swe.julday(utc_dt.year, utc_dt.month, utc_dt.day, decimal_hour, swe.GREG_CAL)
    natal = _planet_positions(natal_jd)
    chart = {
        "status": "calculated",
        "calculation_engine": "Swiss Ephemeris / Moshier",
        "birth_utc": utc_dt.isoformat(),
        "coordinates": {"latitude": latitude, "longitude": longitude},
        "birth_time_approximate": approximate_time,
        "birth_time_assumed": approximate_time,
        "planets": natal,
        "natal_aspects": _aspects(natal, orb_limit=6.0)[:18],
        "geocoding_status": geocoding_status,
        "geocoding_source": geocoding_source,
    }
    if latitude is not None and longitude is not None:
        # Casas/Ascendente são calculados com ou sem hora. Quando a hora foi
        # assumida, marcamos o status específico para a UI saber que precisa
        # mostrar aviso e o LLM saber que o Ascendente não pode ser afirmado
        # com certeza.
        cusps, angles = swe.houses_ex(natal_jd, latitude, longitude, b"P")
        chart["ascendant"] = _sign_position(angles[0])
        chart["midheaven"] = _sign_position(angles[1])
        chart["houses"] = [{"house": index + 1, **_sign_position(cusp)} for index, cusp in enumerate(cusps)]
        for data in natal.values():
            data["house"] = _house_for(data["longitude"], cusps)
        chart["houses_status"] = "calculated_with_assumed_midnight" if approximate_time else "calculated"
    else:
        chart["houses_status"] = "coordinates_missing"
    now = datetime.now(timezone.utc)
    now_hour = now.hour + now.minute / 60 + now.second / 3600
    transit_jd = swe.julday(now.year, now.month, now.day, now_hour, swe.GREG_CAL)
    transits = _planet_positions(transit_jd)
    chart["reference_utc"] = now.isoformat()
    chart["current_sky"] = transits
    chart["transits_to_natal"] = _aspects(transits, natal, orb_limit=3.0)[:16]
    return chart


def _validate_coordinates(latitude: float | None, longitude: float | None) -> str | None:
    """Return a user-facing explanation if coords are outside the legal ranges,
    so the caller can short-circuit before swe.houses_ex raises."""
    if latitude is None or longitude is None:
        return None
    if not (-90.0 <= latitude <= 90.0):
        return f"latitude {latitude} outside [-90, 90]"
    if not (-180.0 <= longitude <= 180.0):
        return f"longitude {longitude} outside [-180, 180]"
    return None
