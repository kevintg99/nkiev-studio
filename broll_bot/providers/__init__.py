"""Fontes de b-roll e fábrica configurável."""

from __future__ import annotations

from pathlib import Path

from ..config import AppConfig, ProviderConfig
from ..http import HttpClient, user_agent
from ..ratelimit import HourLedger, RetryPolicy, TokenBucket, hourly_bucket
from .archive_org import ArchiveOrgProvider
from .base import AuthError, BaseProvider, ProviderError, RateLimitedError
from .local_library import LocalLibraryProvider, SemanticFn
from .openverse import OpenverseProvider
from .pexels import PexelsProvider
from .pixabay import PixabayProvider
from .wikimedia import WikimediaProvider
from .youtube_cc import YouTubeAnyProvider, YouTubeCCProvider

__all__ = [
    "ArchiveOrgProvider", "AuthError", "BaseProvider", "ProviderError", "RateLimitedError",
    "LocalLibraryProvider", "OpenverseProvider", "PexelsProvider", "PixabayProvider", "WikimediaProvider",
    "YouTubeAnyProvider", "YouTubeCCProvider", "build_providers",
]


def _http(name: str, pcfg: ProviderConfig, headers: dict[str, str] | None = None,
          cache_dir: Path | None = None) -> HttpClient:
    if pcfg.per_hour > 0 and cache_dir is not None:  # cota por hora (Pexels): conta as rodadas anteriores
        limiter = hourly_bucket(pcfg.per_hour, HourLedger(cache_dir / f"ratelimit_{name}.json"))
    else:
        limiter = TokenBucket(rate_per_second=pcfg.requests_per_minute / 60.0, capacity=pcfg.burst)
    return HttpClient(name, limiter, RetryPolicy(max_retries=pcfg.max_retries),
                      max_wait_seconds=pcfg.max_wait_seconds, timeout=pcfg.timeout_seconds, headers=headers,
                      min_interval=pcfg.min_interval_seconds)


def build_providers(cfg: AppConfig, base_dir: Path, semantic: SemanticFn | None = None) -> dict[str, BaseProvider]:
    """Instancia só as fontes habilitadas, na ordem de ``search.provider_order``.

    Para adicionar uma fonte nova: implemente ``BaseProvider`` e registre aqui.
    """
    providers: dict[str, BaseProvider] = {}
    cache_dir = base_dir / cfg.paths.cache_dir
    for name in cfg.search.provider_order:
        pcfg = cfg.providers.get(name, ProviderConfig())
        if not pcfg.enabled or name in cfg.search.skip_providers:
            continue
        if name == "archive":
            if cfg.archive.enabled:
                ac = cfg.archive
                providers[name] = ArchiveOrgProvider(_http(name, pcfg, {"User-Agent": user_agent(cfg.wikimedia_contact)}),
                                                     clip_seconds=ac.clip_seconds, items_per_search=ac.items_per_search,
                                                     segments_per_item=ac.segments_per_item,
                                                     collections=ac.collections)
            continue
        if name == "pexels":
            providers[name] = PexelsProvider(cfg.pexels_api_key, _http(name, pcfg, cache_dir=cache_dir),
                                             orientation=cfg.search.orientation,
                                             target_width=cfg.output.width, locale=pcfg.locale,
                                             head_seconds=cfg.search.video_head_seconds)
        elif name == "pixabay":
            providers[name] = PixabayProvider(cfg.pixabay_api_key, _http(name, pcfg),
                                              min_width=cfg.search.min_width,
                                              target_width=cfg.output.width, lang=pcfg.locale,
                                              orientation="all" if cfg.search.orientation == "portrait" else "horizontal",
                                              head_seconds=cfg.search.video_head_seconds)
        elif name == "wikimedia":
            ua = user_agent(cfg.wikimedia_contact)
            providers[name] = WikimediaProvider(cfg.wikimedia_contact,
                                                _http(name, pcfg, {"User-Agent": ua, "Api-User-Agent": ua}),
                                                target_width=cfg.output.width, min_width=cfg.wikimedia.min_width,
                                                allow_share_alike=cfg.wikimedia.allow_share_alike)
        elif name == "openverse":
            providers[name] = OpenverseProvider(_http(name, pcfg), cfg.openverse_client_id, cfg.openverse_client_secret,
                                                min_width=cfg.openverse.min_width,
                                                allow_share_alike=cfg.openverse.allow_share_alike)
        elif name == "youtube_cc":
            yc = cfg.youtube_cc
            providers[name] = YouTubeCCProvider(cfg.youtube_api_key, _http(name, pcfg), clip_seconds=yc.clip_seconds,
                                                min_subscribers=yc.min_subscribers,
                                                max_searches=yc.max_searches_per_video, max_height=yc.max_height,
                                                cookies_from_browser=yc.cookies_from_browser,
                                                cookies_file=yc.cookies_file,
                                                allow_vertical=cfg.search.orientation == "portrait")
        elif name == "youtube_any":  # mesma chave e mesmo navegador logado do YouTube CC
            ya, yc = cfg.youtube_any, cfg.youtube_cc
            providers[name] = YouTubeAnyProvider(cfg.youtube_api_key, _http(name, pcfg), clip_seconds=ya.clip_seconds,
                                                 min_subscribers=ya.min_subscribers,
                                                 max_searches=ya.max_searches_per_video, max_height=ya.max_height,
                                                 cookies_from_browser=yc.cookies_from_browser,
                                                 cookies_file=yc.cookies_file, exclude_channels=ya.exclude_channels,
                                                 allow_vertical=cfg.search.orientation == "portrait")
        elif name == "mixkit_local":
            providers[name] = LocalLibraryProvider(base_dir / cfg.paths.local_library,
                                                   cache_dir / f"index_{name}.json", name=name, semantic=semantic)
    return providers
