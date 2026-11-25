"""Fonte Openverse (fotos com licença livre). Docs: https://api.openverse.org/v1/

O Openverse (do WordPress) junta centenas de milhões de fotos Creative Commons e de domínio público de
Flickr, museus, Wikimedia e outros acervos, com autor e licença em cada resultado. É forte onde os bancos de
stock falham: pessoas reais, lugares, fatos históricos e objetos específicos.

Funciona sem chave (limite baixo por hora). Com ``OPENVERSE_CLIENT_ID`` e ``OPENVERSE_CLIENT_SECRET`` no
``.env`` o limite sobe bastante (cadastro grátis: ``python -m broll_bot.providers.openverse --email voce@...``).

Só entram fotos que podem ir num vídeo monetizado sem obrigação além do crédito: CC0, domínio público (PDM)
e CC BY. CC BY-SA fica de fora por padrão, como no Wikimedia (``openverse.allow_share_alike``).
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

import requests

from ..http import HttpClient
from ..models import Candidate, MediaType
from ..text import keywords
from .base import BaseProvider, ProviderError

API = "https://api.openverse.org/v1"
LICENSE_NAMES = {"cc0": "CC0", "pdm": "Domínio público", "by": "CC BY", "by-sa": "CC BY-SA"}
# acervos de imagem que não servem de b-roll (clipart, ícones, fotos de museu em fundo branco ficam pelo CLIP)
SKIP_SOURCES = {"svgsilh", "phylopic", "thingiverse", "sketchfab"}


def license_param(allow_share_alike: bool) -> str:
    return "cc0,pdm,by" + (",by-sa" if allow_share_alike else "")


def openverse_query(query: str) -> str:
    """O Openverse busca por título e tags das fotos: palavras de direção de filmagem ("close up",
    "cinematic", "aerial shot") só atrapalham."""
    from .wikimedia import SHOT_WORDS

    keep = {"aerial", "night", "street", "road", "city", "sunset", "rain"}  # aqui ajudam (tags comuns no Flickr)
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9\-]*", query.lower()) if w not in SHOT_WORDS or w in keep]
    return " ".join(list(dict.fromkeys(words))[:5])


def _thumb(url: str, api_thumb: str) -> str:
    """Miniatura para o CLIP e a revisão: a da API do Openverse (vai com a chave, ver ``auth_headers``). O CDN do
    Flickr passou a recusar (403) e adivinhar o endereço da miniatura falhava; se a da API falhar, o
    ThumbnailFetcher usa a própria foto."""
    return api_thumb


class OpenverseProvider(BaseProvider):
    name = "openverse"
    cache_version = "v4"  # v4: miniatura sempre pela API (o Flickr passou a recusar com 403)
    supported_media = (MediaType.IMAGE,)

    def __init__(self, http: HttpClient, client_id: str | None = None, client_secret: str | None = None,
                 min_width: int = 1000, allow_share_alike: bool = False) -> None:
        self.http = http
        self.client_id = client_id
        self.client_secret = client_secret
        self.min_width = min_width
        self.allow_share_alike = allow_share_alike
        self._token: str | None = None
        self._token_until = 0.0
        self._lock = threading.Lock()

    def request_count(self) -> int:
        return self.http.requests_made

    # ------------------------------------------------------------------ chave (opcional)
    def _auth(self) -> None:
        """Token OAuth (dura ~12 h) quando há chave; sem chave, anônimo."""
        if not (self.client_id and self.client_secret):
            return
        with self._lock:
            if self._token and time.time() < self._token_until:
                return
            try:
                r = requests.post(f"{API}/auth_tokens/token/", timeout=20, data={
                    "grant_type": "client_credentials", "client_id": self.client_id,
                    "client_secret": self.client_secret})
                body = r.json() if r.status_code == 200 else {}
            except (requests.RequestException, ValueError):
                body = {}
            token = body.get("access_token")
            if not token:  # chave errada ou e-mail não confirmado: segue anônimo em vez de parar a busca
                self.client_id = None
                return
            self._token = str(token)
            self._token_until = time.time() + float(body.get("expires_in") or 36000) - 300
            # o token vai só nos pedidos à API do Openverse (headers=), nunca na sessão: na sessão ele ia junto no
            # download da foto (Flickr, Rawpixel, S3...) e alguns sites recusavam com 401

    def auth_headers(self) -> dict[str, str] | None:
        """Cabeçalho da chave para a API (também para a miniatura dela, que tem o mesmo limite)."""
        return {"Authorization": f"Bearer {self._token}"} if self._token else None

    # ------------------------------------------------------------------ busca
    def search(self, query: str, media_type: MediaType, limit: int, language: str = "en") -> list[Candidate]:
        if media_type is not MediaType.IMAGE or not language.startswith("en"):
            return []  # acervo indexado em inglês; busca em português traz qualquer coisa
        q = openverse_query(query) or query
        self._auth()
        params: dict[str, Any] = {
            "q": q[:200], "license": license_param(self.allow_share_alike), "page_size": min(50, max(limit * 2, 10)),
            "mature": "false", "extension": "jpg,jpeg,png,webp",
        }
        data = self.http.get_json(f"{API}/images/", params, headers=self.auth_headers())
        if not isinstance(data, dict) or "results" not in data:
            raise ProviderError(f"openverse: resposta inesperada ({str(data)[:120]})")
        out = [c for item in data.get("results") or [] if (c := self._candidate(item, query))]
        return out[:limit]

    def _candidate(self, item: dict[str, Any], query: str) -> Candidate | None:
        lic = str(item.get("license") or "").lower()
        if lic not in ("cc0", "pdm", "by") and not (lic == "by-sa" and self.allow_share_alike):
            return None
        if str(item.get("source") or "").lower() in SKIP_SOURCES:
            return None
        width, height = int(item.get("width") or 0), int(item.get("height") or 0)
        url = str(item.get("url") or "")
        if not url:
            return None
        if not (width and height):
            width, height = 1024, 683  # muita foto do acervo vem sem tamanho: a do Flickr é a "grande" (1024 px)
        if width < self.min_width:
            return None
        media_id = str(item.get("id") or "")
        if not media_id:
            return None
        title = " ".join(str(item.get("title") or "").split())
        tags = [str(t.get("name", "")) for t in item.get("tags") or [] if isinstance(t, dict) and t.get("name")]
        description = " ".join(x for x in (title, ", ".join(tags[:12])) if x)
        creator = " ".join(str(item.get("creator") or "").split()) or "desconhecido"
        lic_name = LICENSE_NAMES.get(lic, lic.upper())
        version = str(item.get("license_version") or "")
        if lic in ("by", "by-sa") and version:
            lic_name += f" {version}"
        source = str(item.get("source") or item.get("provider") or "").replace("_", " ")
        return Candidate(
            provider=self.name,
            media_id=media_id,
            media_type=MediaType.IMAGE,
            width=width,
            height=height,
            duration=None,
            thumbnail_url=_thumb(url, str(item.get("thumbnail") or f"{API}/images/{media_id}/thumb/")),
            download_url=url,
            page_url=str(item.get("foreign_landing_url") or item.get("detail_url") or ""),
            description=description[:300],
            tags=list(dict.fromkeys([*(t.lower() for t in tags), *keywords(title)]))[:20],
            author=f"{creator[:80]} ({lic_name}) via {source.title() or 'Openverse'}",
            query=query,
        )

    def download(self, candidate: Candidate, dest_dir: Path) -> Path:
        if not candidate.download_url:
            raise ProviderError("candidato sem URL de download")
        ext = self.extension_for(candidate.download_url, ".jpg")
        dest = dest_dir / f"{self.name}_{candidate.media_type.value}_{candidate.media_id}{ext}"
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        return self.http.download(candidate.download_url, dest, max_bytes=60 * 1024 * 1024)


def register(name: str, email: str, description: str = "B-roll de vídeos do YouTube") -> dict[str, Any]:
    """Cadastro grátis da chave (o Openverse manda um e-mail de confirmação; confirme antes de usar)."""
    r = requests.post(f"{API}/auth_tokens/register/", timeout=30,
                      json={"name": name, "description": description, "email": email})
    r.raise_for_status()
    return r.json()


if __name__ == "__main__":  # python -m broll_bot.providers.openverse --email voce@exemplo.com
    import argparse

    ap = argparse.ArgumentParser(description="Cadastra a chave grátis do Openverse")
    ap.add_argument("--email", required=True)
    ap.add_argument("--nome", default="broll-bot-" + str(int(time.time())))
    args = ap.parse_args()
    got = register(args.nome, args.email)
    print("Confirme o e-mail que o Openverse mandou e coloque no .env:")
    print(f"OPENVERSE_CLIENT_ID={got.get('client_id', '')}")
    print(f"OPENVERSE_CLIENT_SECRET={got.get('client_secret', '')}")
