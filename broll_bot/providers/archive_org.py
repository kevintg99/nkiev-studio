"""Fonte de arquivo histórico: filmes de época do Internet Archive (cinejornais e filmes do governo americano).

Docs: https://archive.org/advancedsearch.php e https://archive.org/developers/md-read.html. Não usa chave.

Só entra o que é domínio público: os cinejornais da Universal (1929–1967, doados ao domínio público), os filmes
do governo americano (FedFlix/usgovfilms: Exército, Signal Corps, Arquivo Nacional; obra do governo dos EUA não
tem direito autoral) e o acervo Prelinger. Item com outra licença declarada (CC BY-NC, CC BY-SA...) fica de fora.

Um filme tem de 1 a 70 minutos e a busca acha o filme, não o trecho. O Internet Archive guarda um quadro do
filme a cada 30–60 s (``<filme>.thumbs/<nome>_000120.jpg`` = segundo 120): cada quadro vira um candidato (o
trecho de ``clip_seconds`` a partir dali) e quem escolhe o trecho certo é o ranking visual e a revisão, olhando
o quadro. O download baixa só esse trecho, sem som.

Liga só no estilo Documentário (``archive.enabled``) ou no canal que pedir: num canal de carros os filmes do
governo dos anos 50 só atrapalham.
"""

from __future__ import annotations

import html
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ..http import HttpClient
from ..models import Candidate, MediaType
from ..text import keywords
from .base import BaseProvider, ProviderError

SEARCH_URL = "https://archive.org/advancedsearch.php"
META_URL = "https://archive.org/metadata/{ident}"
FILE_URL = "https://archive.org/download/{ident}/{name}"
PAGE_URL = "https://archive.org/details/{ident}"
COLLECTIONS = ("universal_newsreels", "FedFlix", "usgovfilms", "prelinger")
# vídeo do item, do melhor para o pior (o original MPEG2/"50mbps" é enorme: o h.264 derivado basta)
VIDEO_FORMATS = ("h.264", "h.264 HD", "MPEG4", "512Kb MPEG4", "h.264 IA")
# palavras de direção de filmagem: a busca do Internet Archive é no título/descrição do filme
SHOT_WORDS = {
    "footage", "archival", "archive", "historical", "historic", "film", "films", "newsreel", "black", "white",
    "vintage", "old", "grainy", "b-roll", "stock", "video", "clip", "shot", "shots", "close", "closeup", "up", "wide",
    "aerial", "view", "cinematic", "slow", "motion", "scene", "scenes", "showing", "the", "a", "an", "of", "in", "on",
    "at", "and", "with", "during", "from", "to", "by", "for", "era", "wartime", "documentary", "photo", "image",
}


def _strip_html(value: Any) -> str:
    if isinstance(value, list):
        value = " ".join(str(v) for v in value)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", str(value or ""))).split())


def archive_query(query: str) -> list[str]:
    """As palavras que identificam o assunto ("1944 american jeep checkpoint snow footage" -> 1944, american, jeep,
    checkpoint)."""
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9\-]*", query.lower()) if w not in SHOT_WORDS and len(w) > 1]
    return list(dict.fromkeys(words))[:4]


def license_ok(doc: dict[str, Any]) -> bool:
    """Domínio público: sem licença declarada (o acervo inteiro é domínio público) ou CC0/Public Domain Mark."""
    lic = str(doc.get("licenseurl") or "").lower()
    if not lic:
        return True
    return "publicdomain" in lic or "/zero/" in lic or "/mark/" in lic


def script_years(text: str) -> tuple[int, int] | None:
    """Os anos de que o roteiro fala ("1944", "1947" -> (1944, 1947)); None quando não cita ano nenhum."""
    years = [int(y) for y in re.findall(r"(?<!\d)(1[89]\d\d|20[0-3]\d)(?!\d)", text or "")]
    return (min(years), max(years)) if years else None


def era_ok(doc: dict[str, Any], years: tuple[int, int] | None) -> bool:
    """O filme é da época do roteiro. Cinejornal é do ano do fato: no máximo 1 ano fora (o "Berlin 1961" entrava num
    vídeo de 1944). Filme do governo pode ser uma retrospectiva feita depois com imagens da época ("Battle of the
    Bulge", 1958): até 25 anos depois. Sem ano no item, passa."""
    if not years:
        return True
    try:
        year = int(str(doc.get("year") or doc.get("date") or "")[:4])
    except ValueError:
        return True
    cols = doc.get("collection") or []
    newsreel = "universal_newsreels" in (cols if isinstance(cols, list) else [cols])
    return years[0] - 1 <= year <= years[1] + (1 if newsreel else 25)


def thumb_seconds(name: str) -> int | None:
    m = re.search(r"_(\d{6})\.jpg$", name)
    return int(m.group(1)) if m else None


def pick_segments(seconds: list[int], duration: float, clip: float, count: int) -> list[int]:
    """Até ``count`` quadros espalhados pelo filme, fora da abertura e do fim (letreiro, contagem, "The End")."""
    lo, hi = min(20.0, duration * 0.1), duration - clip - min(20.0, duration * 0.1)
    ok = sorted(s for s in set(seconds) if lo <= s <= hi)
    if len(ok) <= count:
        return ok
    step = (len(ok) - 1) / max(1, count - 1)
    return sorted({ok[round(k * step)] for k in range(count)})


class ArchiveOrgProvider(BaseProvider):
    name = "archive"
    supported_media = (MediaType.VIDEO,)
    years: tuple[int, int] | None = None  # época do roteiro (``script_years``), posta pelo pipeline

    @property
    def cache_version(self) -> str:  # a busca guardada vale só para a mesma época
        return f"v2{self.years or ''}"

    def __init__(self, http: HttpClient, clip_seconds: float = 8.0, items_per_search: int = 3,
                 segments_per_item: int = 5, collections: tuple[str, ...] | list[str] = COLLECTIONS) -> None:
        self.http = http
        self.clip_seconds = clip_seconds
        self.items_per_search = items_per_search
        self.segments_per_item = segments_per_item
        self.collections = tuple(collections) or COLLECTIONS
        self._meta: dict[str, dict[str, Any] | None] = {}  # o mesmo filme aparece em várias buscas do vídeo
        self._lock = threading.Lock()

    def request_count(self) -> int:
        return self.http.requests_made

    def search(self, query: str, media_type: MediaType, limit: int, language: str = "en") -> list[Candidate]:
        if media_type is not MediaType.VIDEO or not language.startswith("en"):
            return []
        words = archive_query(query)
        if not words:
            return []
        docs = self._search(words)
        if not docs and len(words) > 2:
            docs = self._search(words[:2])
        docs = docs[: self.items_per_search]
        with ThreadPoolExecutor(max_workers=3) as pool:  # a ficha de cada filme ao mesmo tempo (era uma de cada vez)
            list(pool.map(lambda d: self._metadata(str(d["identifier"])), docs))
        out: list[Candidate] = []
        for doc in docs:
            out += self._candidates(doc, query)
            if len(out) >= limit:
                break
        return out[:limit]

    def _search(self, words: list[str]) -> list[dict[str, Any]]:
        terms = " AND ".join(f'"{w}"' if "-" in w else w for w in words)
        cols = " OR ".join(self.collections)
        params = {"q": f"({terms}) AND mediatype:movies AND collection:({cols})",
                  "fl[]": ["identifier", "title", "description", "licenseurl", "creator", "year", "date",
                           "collection"],
                  "rows": max(8, self.items_per_search * 4), "output": "json"}
        data = self.http.get_json(SEARCH_URL, params)
        if "error" in data:
            raise ProviderError(f"archive: {data['error']}")
        docs = (data.get("response") or {}).get("docs") or []
        return [d for d in docs if d.get("identifier") and license_ok(d) and era_ok(d, self.years)]

    def _metadata(self, ident: str) -> dict[str, Any] | None:
        with self._lock:
            if ident in self._meta:
                return self._meta[ident]
        try:
            data = self.http.get_json(META_URL.format(ident=ident))
        except ProviderError:
            data = {}
        meta = data if data.get("files") else None
        with self._lock:
            self._meta[ident] = meta
        return meta

    def _candidates(self, doc: dict[str, Any], query: str) -> list[Candidate]:
        ident = str(doc["identifier"])
        meta = self._metadata(ident)
        if not meta:
            return []
        files = meta.get("files") or []
        videos = [f for f in files if f.get("format") in VIDEO_FORMATS and str(f.get("name", "")).endswith(".mp4")]
        if not videos:
            return []
        video = min(videos, key=lambda f: VIDEO_FORMATS.index(f["format"]))
        try:
            duration = float(video.get("length") or 0)
        except ValueError:
            duration = 0.0
        if duration < self.clip_seconds + 4:
            return []
        thumbs = {s: f["name"] for f in files if f.get("format") == "Thumbnail"
                  and (s := thumb_seconds(str(f.get("name", "")))) is not None}
        width, height = int(video.get("width") or 640), int(video.get("height") or 480)
        title = _strip_html(doc.get("title") or (meta.get("metadata") or {}).get("title"))
        about = _strip_html(doc.get("description"))[:300]
        year = str(doc.get("year") or str(doc.get("date") or "")[:4])
        creator = _strip_html(doc.get("creator"))[:60] or "U.S. National Archives / Universal Newsreel"
        description = " ".join(x for x in (title, year, about) if x)
        tags = list(dict.fromkeys(keywords(f"{title} {about}")))[:20]
        out = []
        for sec in pick_segments(list(thumbs), duration, self.clip_seconds, self.segments_per_item):
            out.append(Candidate(
                provider=self.name,
                media_id=f"{ident}_{sec}",
                media_type=MediaType.VIDEO,
                width=width,
                height=height,
                duration=self.clip_seconds,
                thumbnail_url=FILE_URL.format(ident=ident, name=thumbs[sec]),
                download_url=FILE_URL.format(ident=ident, name=video["name"]),
                page_url=f"{PAGE_URL.format(ident=ident)}?start={sec}",
                description=description,
                tags=tags,
                author=f"{creator} (domínio público) via Internet Archive",
                query=query,
            ))
        return out

    def download(self, candidate: Candidate, dest_dir: Path) -> Path:
        if not candidate.download_url:
            raise ProviderError("candidato sem URL de download")
        start = float(candidate.media_id.rpartition("_")[2] or 0)
        safe = re.sub(r"[^\w.-]", "_", candidate.media_id)
        dest = dest_dir / f"{self.name}_{candidate.media_type.value}_{safe}.mp4"
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        seconds = float(candidate.duration or self.clip_seconds)
        try:
            return self.http.download_section(candidate.download_url, dest, start, seconds)
        except ProviderError:  # o servidor de arquivos do Internet Archive às vezes falha na 1ª leitura
            return self.http.download_section(candidate.download_url, dest, start, seconds)
