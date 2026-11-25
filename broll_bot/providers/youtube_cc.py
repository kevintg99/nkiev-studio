"""Fonte YouTube Creative Commons (vídeos CC BY). Docs: https://developers.google.com/youtube/v3/docs/search/list

A busca usa a API oficial do YouTube com ``videoLicense=creativeCommon`` (chave grátis do Google Cloud em
``YOUTUBE_API_KEY``). Cada vídeo encontrado vira UM trecho curto, do meio do vídeo: é o mesmo quadro que o
YouTube gera sozinho como miniatura (``hq2.jpg``, sem título nem rosto de clickbait), então a imagem que o
CLIP e a revisão por IA conferem é a que entra no vídeo. Só esse trecho é baixado (``yt-dlp``), nunca o vídeo
inteiro.

Riscos que o código reduz (a decisão de usar é do dono do canal):
* vídeo marcado como CC sem ser (reenvio de TV, filme, clipe): exige canal com um mínimo de inscritos e
  descarta títulos de filme/episódio/clipe/notícia/ao vivo/entrevista;
* cota da chave grátis (cada busca gasta 100 de 10.000 unidades por dia): entra como banco de reserva (só
  quando os outros não bastaram) e com um teto de buscas por vídeo.
O crédito (canal + CC BY + link) vai para o ``report.json`` e para a descrição.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any

from ..http import HttpClient
from ..log import get_logger
from ..models import Candidate, MediaType
from ..text import keywords
from .base import BaseProvider, ProviderError

log = get_logger("youtube_cc")

API = "https://www.googleapis.com/youtube/v3"
# títulos de vídeo que quase nunca são do próprio autor (ou não servem de b-roll: gente falando)
BLOCKED_TITLE = re.compile(
    r"\b(full movie|filme completo|episode|episódio|ep\.?\s?\d|trailer|official (?:music )?video|music video|"
    r"clipe oficial|lyrics?|letra|live|ao vivo|podcast|interview|entrevista|reaction|react|news|jornal|"
    r"highlights|gameplay|walkthrough|#shorts|shorts)\b", re.I)
_DURATION = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def iso_seconds(value: str) -> float:
    """"PT4M13S" -> 253; "PT1H2M" -> 3720."""
    m = _DURATION.fullmatch(value or "")
    if not m:
        return 0.0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return float(d * 86400 + h * 3600 + mi * 60 + s)


def is_vertical(player: dict[str, Any]) -> bool:
    """Short/vídeo em pé (embedHeight > embedWidth): num vídeo deitado vira uma faixa no meio com borrão."""
    try:
        return float(player.get("embedHeight") or 0) > float(player.get("embedWidth") or 0) > 0
    except (TypeError, ValueError):
        return False


def section_for(duration: float, clip_seconds: float) -> tuple[float, float]:
    """Trecho do meio do vídeo (onde fica o quadro hq2.jpg que foi conferido)."""
    length = min(clip_seconds, max(4.0, duration - 2.0))
    start = max(0.0, duration * 0.5 - length / 2)
    return round(start, 1), round(start + length, 1)


class YouTubeCCProvider(BaseProvider):
    name = "youtube_cc"
    cache_version = "v2"  # v2: descarta vídeo em pé (o cache antigo trazia Shorts)
    supported_media = (MediaType.VIDEO,)
    license = "creativeCommon"  # filtro da busca e conferido em cada vídeo; "" = qualquer licença
    credit = "CC BY"
    blocked_categories: frozenset[str] = frozenset()  # categorias do YouTube que nunca entram

    def __init__(self, api_key: str | None, http: HttpClient, clip_seconds: float = 25.0,
                 min_subscribers: int = 1000, max_searches: int = 30, max_height: int = 1080,
                 cookies_from_browser: str = "", cookies_file: str = "",
                 exclude_channels: list[str] | tuple[str, ...] = (), allow_vertical: bool = False) -> None:
        self.api_key = api_key
        self.allow_vertical = allow_vertical  # vídeo curto em pé (9:16): aí o vídeo vertical serve
        # canais que nunca entram (ex.: concorrente que fez o mesmo vídeo: seria reaproveitar a edição dele)
        self.exclude_channels = {" ".join(c.lower().split()) for c in exclude_channels if c.strip()}
        # "Sign in to confirm you're not a bot": com os cookies de um navegador logado no YouTube o download passa
        self.cookies_from_browser = (cookies_from_browser or "").strip().lower()
        self.cookies_file = (cookies_file or "").strip()
        self.blocked = ""  # o YouTube barrou como robô: o resto do vídeo nem tenta (nem gasta cota buscando)
        self.http = http
        self.clip_seconds = clip_seconds
        self.min_subscribers = min_subscribers
        self.max_searches = max_searches
        self.max_height = max_height
        self.searches = 0

    def available(self) -> bool:
        if not self.api_key:
            return False
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            return False
        return True

    def unavailable_reason(self) -> str:
        if not self.api_key:
            return "sem YOUTUBE_API_KEY no .env"
        return "yt-dlp não instalado (rode o instalar_app.bat de novo)"

    def request_count(self) -> int:
        return self.http.requests_made

    def search(self, query: str, media_type: MediaType, limit: int, language: str = "en") -> list[Candidate]:
        if media_type is not MediaType.VIDEO or not language.startswith("en"):
            return []
        if self.searches >= self.max_searches or self.blocked:
            return []  # teto do vídeo (cota diária da chave grátis) ou o YouTube barrou os downloads
        self.searches += 1
        params = {"part": "snippet", "type": "video", "videoDefinition": "high", "q": query[:120],
                  "maxResults": min(25, max(limit, 10)), "safeSearch": "strict", "relevanceLanguage": "en",
                  "key": self.api_key}
        if self.license:
            params["videoLicense"] = self.license
        found = self.http.get_json(f"{API}/search", params)
        self._check(found)
        ids = [str((it.get("id") or {}).get("videoId") or "") for it in found.get("items") or []]
        ids = [i for i in ids if i]
        if not ids:
            return []
        # "player" com maxWidth traz a proporção do vídeo: Short em pé (outro criador falando) não serve de b-roll
        videos = self.http.get_json(f"{API}/videos", {"part": "snippet,contentDetails,status,player", "maxWidth": 480,
                                                      "id": ",".join(ids),
                                                      "key": self.api_key})
        self._check(videos)
        items = [v for v in videos.get("items") or [] if self._usable(v)]
        subs = self._subscribers({str((v.get("snippet") or {}).get("channelId") or "") for v in items})
        out = []
        for v in items:
            channel = str((v.get("snippet") or {}).get("channelId") or "")
            if subs.get(channel, 0) < self.min_subscribers:
                continue
            if (c := self._candidate(v, query)) is not None:
                out.append(c)
        return out[:limit]

    @staticmethod
    def _check(data: dict[str, Any]) -> None:
        err = data.get("error") if isinstance(data, dict) else None
        if err:
            raise ProviderError(f"youtube: {err.get('message', err) if isinstance(err, dict) else err}")

    def _usable(self, v: dict[str, Any]) -> bool:
        sn, cd, st = v.get("snippet") or {}, v.get("contentDetails") or {}, v.get("status") or {}
        if self.license and str(st.get("license") or "") != self.license:
            return False
        if str(sn.get("liveBroadcastContent") or "none") != "none":
            return False
        if BLOCKED_TITLE.search(str(sn.get("title") or "")):
            return False
        if " ".join(str(sn.get("channelTitle") or "").lower().split()) in self.exclude_channels:
            return False
        if not self.allow_vertical and is_vertical(v.get("player") or {}):
            return False
        if str(sn.get("categoryId") or "") in self.blocked_categories:
            return False
        duration = iso_seconds(str(cd.get("duration") or ""))
        return 20.0 <= duration <= 3 * 3600 and str(cd.get("definition") or "hd") == "hd"

    def _subscribers(self, channels: set[str]) -> dict[str, int]:
        channels = {c for c in channels if c}
        if not channels or self.min_subscribers <= 0:
            return {c: 10**9 for c in channels}
        data = self.http.get_json(f"{API}/channels", {"part": "statistics", "id": ",".join(sorted(channels)),
                                                      "key": self.api_key})
        self._check(data)
        out = {}
        for ch in data.get("items") or []:
            stats = ch.get("statistics") or {}
            hidden = bool(stats.get("hiddenSubscriberCount"))
            out[str(ch.get("id"))] = 0 if hidden else int(stats.get("subscriberCount") or 0)
        return out

    def _candidate(self, v: dict[str, Any], query: str) -> Candidate | None:
        vid = str(v.get("id") or "")
        sn = v.get("snippet") or {}
        duration = iso_seconds(str((v.get("contentDetails") or {}).get("duration") or ""))
        if not vid or not duration:
            return None
        start, end = section_for(duration, self.clip_seconds)
        title = " ".join(str(sn.get("title") or "").split())
        tags = [str(t) for t in sn.get("tags") or []][:15]
        description = " ".join(x for x in (title, ", ".join(tags)) if x)
        return Candidate(
            provider=self.name,
            media_id=f"{vid}_{int(start)}",
            media_type=MediaType.VIDEO,
            width=1920 if self.max_height >= 1080 else 1280,
            height=1080 if self.max_height >= 1080 else 720,
            duration=round(end - start, 1),
            thumbnail_url=f"https://i.ytimg.com/vi/{vid}/hq2.jpg",  # quadro do meio, gerado pelo YouTube
            download_url=f"https://www.youtube.com/watch?v={vid}",
            page_url=f"https://www.youtube.com/watch?v={vid}",
            description=description[:300],
            tags=list(dict.fromkeys([*(t.lower() for t in tags), *keywords(title)]))[:20],
            author=f"{' '.join(str(sn.get('channelTitle') or 'canal').split())[:80]} ({self.credit}) via YouTube",
            query=query,
        )

    def download(self, candidate: Candidate, dest_dir: Path) -> Path:
        """Baixa só o trecho (sem áudio: o b-roll entra mudo) com o yt-dlp."""
        try:
            import yt_dlp
            from yt_dlp.utils import download_range_func
        except ImportError as exc:
            raise ProviderError("yt-dlp não instalado (pip install yt-dlp)") from exc
        if self.blocked:
            raise ProviderError(self.blocked)
        vid, _, start_s = candidate.media_id.rpartition("_")
        start = float(start_s or 0)
        end = start + float(candidate.duration or self.clip_seconds)
        dest = dest_dir / f"{self.name}_{candidate.media_type.value}_{candidate.media_id}.mp4"
        cached = [p for p in dest_dir.glob(f"{dest.stem}.*") if p.suffix != ".part" and p.stat().st_size > 0]
        if cached:
            return cached[0]
        dest_dir.mkdir(parents=True, exist_ok=True)
        opts = {
            "format": f"bv*[height<={self.max_height}][ext=mp4]/bv*[height<={self.max_height}]",
            "outtmpl": str(dest.with_suffix(".%(ext)s")),
            "download_ranges": download_range_func(None, [(start, end)]),
            "force_keyframes_at_cuts": True,
            "merge_output_format": "mp4",
            "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
            "logger": _QuietLogger(),
        }
        if self.cookies_file:
            opts["cookiefile"] = self.cookies_file
        elif self.cookies_from_browser:
            opts["cookiesfrombrowser"] = (self.cookies_from_browser,)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={vid}"])
        except Exception as exc:  # noqa: BLE001 - vídeo removido, bloqueio, rede: o selector tenta o próximo
            msg = str(exc)
            if re.search(r"confirm you.?re not a bot|sign in to confirm", msg, re.I):
                self.blocked = ("youtube: o YouTube pediu login (anti-robô) — "
                                + ("os cookies do navegador não bastaram" if self.cookies_from_browser or self.cookies_file
                                   else "configure youtube_cc.cookies_from_browser no config.yaml (ex.: firefox)"))
                raise ProviderError(self.blocked) from exc
            if not shutil.which("deno") and re.search(r"javascript|js runtime|deno|signature|n challenge|"
                                                      r"Requested format is not available", msg, re.I):
                msg = "o yt-dlp precisa do Deno para o YouTube (rode o instalar_app.bat de novo) — " + msg
            raise ProviderError(f"youtube: download falhou ({type(exc).__name__}: {msg[:160]})") from exc
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        got = [p for p in sorted(dest_dir.glob(f"{dest.stem}.*")) if p.suffix != ".part" and p.stat().st_size > 0]
        if not got:
            raise ProviderError("youtube: download vazio")
        return got[0]  # sem mp4 até 1080p, vem webm (o ffmpeg lê igual)


class YouTubeAnyProvider(YouTubeCCProvider):
    """Qualquer vídeo do YouTube, não só os CC: a filmagem real de empresas e lugares que os bancos grátis não têm
    (o lançamento da SpinLaunch, o tambor-forno da Eco-Fuel). Mesma busca e mesmo trecho do meio, mais curto.

    Os vídeos têm direitos reservados: usar alguns segundos não é uma regra que libera (Content ID pode reclamar
    e o YouTube pode tratar como conteúdo reutilizado). Por isso entra só como reserva, depois do YouTube CC, e o
    crédito diz de quem é. A decisão de ligar (``providers.youtube_any.enabled``) é do dono do canal.
    """

    name = "youtube_any"
    license = ""
    credit = "© do canal"
    # TV e entretenimento (o Content ID mais pega e não é b-roll): Film & Animation, Music, Sports, Gaming,
    # Comedy, Entertainment, News & Politics, Movies, Shows, Trailers. Ficam educação, ciência, viagem, carros...
    blocked_categories = frozenset({"1", "10", "17", "20", "23", "24", "25", "30", "43", "44"})


class _QuietLogger:
    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        log.debug(msg)

    def error(self, msg: str) -> None:
        log.log(logging.WARNING, msg[:200])
