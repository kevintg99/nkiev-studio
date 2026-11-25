"""Fonte Wikimedia Commons (fotos). Docs: https://www.mediawiki.org/wiki/API:Search

Não usa chave. A política da Wikimedia pede um User-Agent que identifique quem
consulta (nome do programa + contato); o contato vem de ``WIKIMEDIA_CONTACT`` no
``.env``. O Commons é forte justamente onde os bancos de stock falham: fotos do
modelo exato de um carro (geração, ano, versão).

Só entram fotos com licença que permite uso no vídeo sem obrigações extras além do
crédito: domínio público, CC0 e CC BY. CC BY-SA ("compartilhar igual") fica de fora
por padrão (``wikimedia.allow_share_alike``). O crédito (autor + licença + link)
vai para o ``report.json``.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from ..http import HttpClient
from ..models import Candidate, MediaType
from ..text import keywords
from .base import BaseProvider, ProviderError

API_URL = "https://commons.wikimedia.org/w/api.php"
IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp"}
META_FIELDS = "LicenseShortName|Artist|ImageDescription|ObjectName|Credit"


def _strip_html(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value or "")).split())


def license_ok(short_name: str, allow_share_alike: bool) -> bool:
    lic = (short_name or "").strip().lower()
    if not lic:
        return False
    if lic in {"public domain", "pd", "cc0", "cc-zero", "cc0 1.0"} or lic.startswith(("pd-", "public domain")):
        return True
    if lic.startswith("cc by-sa") or lic.startswith("cc-by-sa"):
        return allow_share_alike
    if lic.startswith(("cc by", "cc-by")):
        return "nc" not in lic and "nd" not in lic
    return False


# o Wikimedia só serve miniaturas nessas larguras para robôs (outra largura volta erro 429)
THUMB_STEPS = (120, 250, 330, 500, 960, 1280, 1920, 3840)


def thumb_step(width: int) -> int:
    """Menor largura padrão que cobre ``width`` (1920 -> 1920, 1280 -> 1280, 1000 -> 1280)."""
    return next((s for s in THUMB_STEPS if s >= width), THUMB_STEPS[-1])


def _thumb(url: str, width: int) -> str:
    """URL de miniatura em outra largura (padrão .../thumb/.../1920px-Arquivo.jpg)."""
    return re.sub(r"/(\d+)px-", f"/{width}px-", url, count=1) if "/thumb/" in url else url


# palavras de direção de filmagem: o Commons busca pela descrição das fotos (todas as palavras
# precisam aparecer), então "toyota corolla driving golden hour" não acha nada e "toyota corolla
# 2014" acha a geração certa
SHOT_WORDS = {
    "driving", "drive", "drives", "road", "highway", "street", "city", "urban", "golden", "hour", "sunset", "sunrise",
    "night", "day", "close", "closeup", "up", "shot", "shots", "footage", "cinematic", "slow", "motion", "tracking",
    "three", "quarter", "profile", "view", "low", "angle", "beauty", "aerial", "background", "bokeh", "rain", "wet",
    "parked", "moving", "fast", "speed", "smooth", "scenic", "coastal", "countryside", "detail", "details", "b-roll",
    "stock", "video", "clip", "camera", "pan", "zoom", "wide", "medium", "evening", "morning", "lit", "lights", "on",
    "modern", "new", "brand", "showroom", "dealership", "press", "event", "sedan", "car", "vehicle", "the", "a", "of",
    "with", "in", "and", "at", "vs", "under", "hood", "trunk", "badge",
}


def commons_query(query: str) -> str:
    """Só o que identifica o carro: marca, modelo, ano/geração e a peça ("toyota corolla 2014",
    "toyota corolla interior")."""
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9\-]*", query.lower()) if w not in SHOT_WORDS]
    return " ".join(list(dict.fromkeys(words))[:4])


def _minimal(query: str) -> str:
    """Última tentativa: marca + modelo (+ ano)."""
    words = query.split()
    years = [w for w in words if re.fullmatch(r"(19|20)\d\d", w)]
    base = [w for w in words if w not in years and w not in ("classic", "vintage", "old", "retro", "antique")][:2]
    return " ".join(base + years[:1])


class WikimediaProvider(BaseProvider):
    name = "wikimedia"
    cache_version = "v3"  # v3: miniatura em largura padrão (500 px)
    supported_media = (MediaType.IMAGE,)

    def __init__(self, contact: str | None, http: HttpClient, target_width: int = 1920, min_width: int = 1200,
                 allow_share_alike: bool = False) -> None:
        self.contact = contact
        self.http = http
        self.target_width = target_width
        self.min_width = min_width
        self.allow_share_alike = allow_share_alike

    def available(self) -> bool:
        return bool(self.contact)

    def unavailable_reason(self) -> str:
        return "sem WIKIMEDIA_CONTACT no .env"

    def request_count(self) -> int:
        return self.http.requests_made

    def search(self, query: str, media_type: MediaType, limit: int, language: str = "en") -> list[Candidate]:
        # o Commons é buscado em inglês; consulta em português (modo sem IA) traz prédios e
        # monumentos com o mesmo nome — melhor não usar
        if media_type is not MediaType.IMAGE or not language.startswith("en"):
            return []
        simple = commons_query(query) or query
        pages = self._search(simple, limit)
        if not pages and _minimal(simple) != simple:
            pages = self._search(_minimal(simple), limit)
        out = [c for page in pages if (c := self._candidate(page, query))]
        return out[:limit]

    def search_reference(self, query: str, limit: int = 30) -> list[Candidate]:
        """Fotos de REFERÊNCIA do carro (só a miniatura é comparada com as candidatas; nunca vão
        para o vídeo): qualquer licença e qualquer tamanho, senão quase tudo do Commons (CC BY-SA)
        fica de fora e o carro fica sem referência."""
        pages = self._search(commons_query(query) or query, limit)
        out = [c for page in pages if (c := self._candidate(page, query, reference=True))]
        return out[:limit]

    def _search(self, query: str, limit: int) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "action": "query", "format": "json", "formatversion": 2,
            "generator": "search", "gsrsearch": f"{query[:120]} filetype:bitmap", "gsrnamespace": 6,
            "gsrlimit": max(1, min(limit * 2, 50)),  # metade costuma cair no filtro de licença/tamanho
            "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": thumb_step(self.target_width), "iiextmetadatafilter": META_FIELDS, "iiextmetadatalanguage": "en",
        }
        data = self.http.get_json(API_URL, params)
        if "error" in data:
            raise ProviderError(f"wikimedia: {data['error'].get('info', data['error'])}")
        return sorted((data.get("query") or {}).get("pages", []), key=lambda p: p.get("index", 0))

    def _candidate(self, page: dict[str, Any], query: str, reference: bool = False) -> Candidate | None:
        info = (page.get("imageinfo") or [{}])[0]
        if info.get("mime") not in IMAGE_MIMES:
            return None
        width, height = int(info.get("width") or 0), int(info.get("height") or 0)
        if width < (300 if reference else self.min_width) or not height:
            return None
        meta = info.get("extmetadata") or {}
        value = lambda key: _strip_html(str((meta.get(key) or {}).get("value", "")))  # noqa: E731
        lic = value("LicenseShortName")
        if not reference and not license_ok(lic, self.allow_share_alike):
            return None
        title = str(page.get("title", "")).removeprefix("File:")
        name = Path(title).stem.replace("_", " ")
        description = " ".join(x for x in (value("ObjectName") or name, value("ImageDescription")[:200]) if x)
        artist = value("Artist") or value("Credit") or "desconhecido"
        download = info.get("thumburl") or info.get("url")
        if not download:
            return None
        scale = min(1.0, thumb_step(self.target_width) / width)
        return Candidate(
            provider=self.name,
            media_id=str(page.get("pageid") or title),
            media_type=MediaType.IMAGE,
            width=round(width * scale),
            height=round(height * scale),
            duration=None,
            # miniatura na largura padrão de 500 px (640 px dava 429 e a foto ia sem a IA olhar)
            thumbnail_url=_thumb(str(download), 500) if width > 500 else str(info.get("url") or download),
            download_url=str(download),
            page_url=str(info.get("descriptionurl") or ""),
            description=description,
            tags=list(dict.fromkeys(keywords(description)))[:20],
            author=f"{artist[:80]} ({lic}) via Wikimedia Commons",
            query=query,
        )

    def download(self, candidate: Candidate, dest_dir: Path) -> Path:
        if not candidate.download_url:
            raise ProviderError("candidato sem URL de download")
        ext = self.extension_for(candidate.download_url, ".jpg")
        dest = dest_dir / f"{self.name}_{candidate.media_type.value}_{candidate.media_id}{ext}"
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        return self.http.download(candidate.download_url, dest)
