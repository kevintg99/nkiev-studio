"""Dados de mapa (Natural Earth, domínio público) para o template ``map`` e as bandeiras do ``flagversus``.

* ``assets/geo/countries.json.gz`` — os 258 países da escala 1:10m (contorno simplificado em ~1 km), com o nome em
  várias línguas (en, pl, fr, pt, es, de, it, nl), o código ISO de 2 letras e o ponto do rótulo.
* ``assets/geo/places.csv.gz`` — 7.342 cidades e lugares (nome, nome ascii, nome alternativo, lat, lon, país,
  população).

As bandeiras vêm do flagcdn.com (PNG, domínio público) e ficam em ``.cache/flags``.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..log import get_logger

log = get_logger("geo")
GEO_DIR = Path(__file__).resolve().parents[2] / "assets" / "geo"
FLAG_URL = "https://flagcdn.com/w640/{iso}.png"


def fold(text: str) -> str:
    """Minúsculas sem acento nem pontuação: "Łódź" -> "lodz", "Côte d'Ivoire" -> "cote d ivoire". Apóstrofo e hífen
    separam palavras: a elisão do francês ("l'Allemagne", "d'Italie") e "États-Unis" = "etats unis"."""
    text = str(text).replace("ł", "l").replace("Ł", "L").replace("ø", "o").replace("Ø", "O")
    text = re.sub(r"['’ʼ`\-‐‑–]", " ", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^\w\s]", "", text).split())


class Country:
    __slots__ = ("name", "iso2", "a3", "names", "local", "label", "rings", "bbox")

    def __init__(self, data: dict, q: float) -> None:
        self.name, self.iso2, self.a3 = data["name"], data["iso2"], data["a3"]
        self.names = list(data["names"])
        self.local = dict(data.get("local") or {})  # nome em cada língua: {"fr": "Allemagne", "pl": "Niemcy"}
        self.label = tuple(data["label"])
        self.rings = [np.asarray(r, np.float32).reshape(-1, 2) / q for r in data["rings"]]
        if self.rings:
            allp = np.concatenate(self.rings)
            self.bbox = (float(allp[:, 0].min()), float(allp[:, 1].min()), float(allp[:, 0].max()),
                         float(allp[:, 1].max()))
        else:
            self.bbox = (self.label[0], self.label[1], self.label[0], self.label[1])


def name_in(c: Country, lang: str) -> str:
    """Nome do país na língua do vídeo ("fr" -> "Allemagne"), ou em inglês."""
    return c.local.get((lang or "en").split("-")[0].lower()) or c.local.get("en") or c.name


@lru_cache(maxsize=1)
def countries() -> list[Country]:
    with gzip.open(GEO_DIR / "countries.json.gz", "rb") as fh:
        data = json.loads(fh.read().decode("utf-8"))
    return [Country(c, data["q"]) for c in data["countries"]]


@lru_cache(maxsize=1)
def _country_index() -> dict[str, Country]:
    index: dict[str, Country] = {}
    for c in countries():
        for n in [*c.names, c.iso2, c.a3]:
            if n:
                index.setdefault(fold(n), c)
    # nomes comuns que o Natural Earth escreve de outro jeito
    for alias, name in (("usa", "United States of America"), ("us", "United States of America"),
                        ("america", "United States of America"), ("united states", "United States of America"),
                        ("uk", "United Kingdom"), ("britain", "United Kingdom"), ("great britain", "United Kingdom"),
                        ("england", "United Kingdom"), ("soviet union", "Russia"), ("ussr", "Russia"),
                        ("holland", "Netherlands"), ("czech republic", "Czechia")):
        hit = index.get(fold(name))
        if hit is not None:
            index.setdefault(alias, hit)
    return index


def find_country(name: str) -> Country | None:
    return _country_index().get(fold(name)) if name else None


@lru_cache(maxsize=1)
def places() -> list[tuple[str, str, str, float, float, str, int]]:
    with gzip.open(GEO_DIR / "places.csv.gz", "rb") as fh:
        rows = list(csv.DictReader(io.StringIO(fh.read().decode("utf-8"))))
    return [(r["name"], r["ascii"], r["alt"], float(r["lat"]), float(r["lon"]), r["iso2"], int(r["pop"] or 0))
            for r in rows]


@lru_cache(maxsize=1)
def _place_index() -> dict[str, list[tuple[str, str, str, float, float, str, int]]]:
    index: dict[str, list] = {}
    for p in places():
        for n in {fold(p[0]), fold(p[1]), *(fold(a) for a in p[2].split("|") if a)}:
            if n:
                index.setdefault(n, []).append(p)
    return index


def find_place(name: str, country: str = "") -> tuple[float, float] | None:
    """(lat, lon) da cidade/lugar pelo nome (o mais populoso; com ``country``, só desse país)."""
    key = fold(name)
    if not key:
        return None
    c = find_country(country) if country else None
    iso = c.iso2 if c else ""
    hits = _place_index().get(key, [])
    if iso:
        hits = [p for p in hits if p[5] == iso] or hits
    if not hits:
        return None
    best = max(hits, key=lambda p: p[6])
    return best[3], best[4]


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distância entre dois (lat, lon) em km."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


def locate(name: str, country: str = "", lat: object = None, lon: object = None) -> tuple[float, float] | None:
    """Onde fica o lugar: o da base de cidades, se bater com o que a IA disse (até 250 km); a base não tem o
    lugar (praia, ponte, batalha): vale o da IA."""
    found = find_place(name, country)
    try:
        given = (float(lat), float(lon)) if lat is not None and lon is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        given = None
    if given is not None and not (-90 <= given[0] <= 90 and -180 <= given[1] <= 180):
        given = None
    if found and (given is None or km(found, given) <= 250):
        return found
    return given or found


def flag_path(iso2: str, cache_dir: Path, session=None) -> Path | None:
    """PNG da bandeira do país (baixa uma vez para ``cache_dir/flags``)."""
    iso = (iso2 or "").strip().lower()
    if not re.fullmatch(r"[a-z]{2}", iso):
        return None
    dest = cache_dir / "flags" / f"{iso}.png"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    try:
        import requests

        resp = (session or requests).get(FLAG_URL.format(iso=iso), timeout=20)
        if resp.status_code != 200 or not resp.content.startswith(b"\x89PNG"):
            raise ValueError(f"HTTP {resp.status_code}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return dest
    except Exception as exc:  # noqa: BLE001 - sem bandeira o card desenha só o nome
        log.warning("bandeira não baixou", extra={"stage": "templates", "pais": iso, "error": str(exc)[:120]})
        return None
