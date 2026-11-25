"""Busca de b-rolls por cena: poucas consultas, cache, fallback entre fontes."""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from .cache import SQLiteCache, make_key
from .config import AppConfig
from .errors import AuthError, ProviderError, RateLimitedError, UnreachableError
from .log import get_logger
from .models import Candidate, MediaType, Scene
from .providers.base import BaseProvider
from .ranking import Ranker
from .ratelimit import CircuitBreaker

log = get_logger("search")


@dataclass
class SceneSearchResult:
    scene_index: int
    ranked: list[Candidate] = field(default_factory=list)
    needed: int = 0  # shots da cena (+ reserva): o que passar disso é sobra
    attempts: list[dict[str, Any]] = field(default_factory=list)
    budget_used: int = 0

    def best_query(self) -> str | None:
        ok = [a for a in self.attempts if a.get("results")]
        return ok[0]["query"] if ok else None


class BrollSearcher:
    def __init__(self, cfg: AppConfig, providers: dict[str, BaseProvider], ranker: Ranker,
                 cache: SQLiteCache, rng: random.Random) -> None:
        self.cfg = cfg
        self.providers = providers
        self.ranker = ranker
        self.cache = cache
        self.rng = rng
        cb = cfg.search.circuit_breaker
        self.breakers = {name: CircuitBreaker(cb.failure_threshold, cb.cooldown_seconds) for name in providers}
        self._unavailable_logged: set[str] = set()
        # mídia boa já "reservada" por uma cena pesquisada antes (chave -> cena). O mesmo banco devolve os mesmos
        # 15 vídeos de foguete para toda cena de foguete: sem isso, a 2ª cena achava que já tinha o bastante,
        # pulava os bancos de reserva e, na distribuição, ficava com sobra da cena vizinha (escritório, calendário)
        self.claimed: dict[str, int] = {}

    # -------------------------------------------------------------- infra
    def _ordered(self, media_type: MediaType) -> list[BaseProvider]:
        out = []
        for name, provider in self.providers.items():
            if not provider.supports(media_type):
                continue
            if not provider.available():
                if name not in self._unavailable_logged:
                    self._unavailable_logged.add(name)
                    log.warning("fonte indisponível (sem chave ou vazia); pulando", extra={"stage": "search", "source": name})
                continue
            out.append(provider)
        return out

    def _search_key(self, provider: BaseProvider, query: str, media_type: MediaType, language: str) -> str:
        s = self.cfg.search
        # cache_version: quando a fonte muda o jeito de buscar, o cache antigo (ex.: 0 resultados) não vale mais
        return make_key(provider.name + getattr(provider, "cache_version", ""), media_type.value,
                        query.lower().strip(), s.per_page, s.orientation, language)

    def _skip_busy(self, provider: BaseProvider, query: str, media_type: MediaType, language: str,
                   scene: Scene) -> bool:
        """Pula o banco com a fila cheia (Pexels: 200 pedidos/hora) em vez de esperar a vez:
        * busca que cita o modelo exato ("dacia logan mcv rear"): o Pexels quase nunca tem o modelo e
          devolve carros genéricos (que as checagens barram);
        * qualquer busca depois de ``max_queue_wait_minutes`` de espera somada no vídeo (o vídeo de 22 min
          do Bernard ficou 1 hora na busca esperando o Pexels).
        Busca do cache sempre vale."""
        from .subject import names_car

        s = self.cfg.search
        limiter = getattr(getattr(provider, "http", None), "limiter", None)
        if limiter is None or limiter.wait_time() <= 0.5:
            return False
        if self.cache.get("search", self._search_key(provider, query, media_type, language)) is not None:
            return False
        if getattr(limiter, "waited", 0.0) >= s.max_queue_wait_minutes * 60:  # já esperou demais neste vídeo: só com a fila livre
            return True
        car = scene.analysis.subject
        return provider.name in s.opportunistic_model_providers and bool(car) and names_car(query, car)

    def _cached_search(self, provider: BaseProvider, query: str, media_type: MediaType,
                       language: str) -> tuple[list[Candidate], bool]:
        s = self.cfg.search
        if not provider.is_remote:  # busca local é barata; não precisa de cache
            return provider.search(query, media_type, s.per_page, language), True
        key = self._search_key(provider, query, media_type, language)
        hit = self.cache.get("search", key)
        if hit is not None:
            return [Candidate.from_dict(d) for d in hit], True
        results = provider.search(query, media_type, s.per_page, language)
        self.cache.set("search", key, [c.to_dict() for c in results], ttl_seconds=s.cache_ttl_hours * 3600)
        return results, False

    def _attempt(self, provider: BaseProvider, query: str, media_type: MediaType, language: str,
                 result: SceneSearchResult) -> list[Candidate]:
        breaker = self.breakers[provider.name]
        record: dict[str, Any] = {"provider": provider.name, "query": query, "media": media_type.value}
        if provider.is_remote and (hit := self.cache.get("search", self._search_key(provider, query, media_type,
                                                                                     language))) is not None:
            # busca já feita (versão anterior do vídeo): vem do cache mesmo com a fonte desligada pelo disjuntor. Na
            # versão 3 das baterias o YouTube deu 429 e ~2.900 buscas com resultado guardado foram puladas: os vídeos
            # do YouTube da versão 1 não voltaram e sobrou plano B
            record.update(results=len(hit), cached=True)
            result.attempts.append(record)
            return [Candidate.from_dict(d) for d in hit]
        if breaker.is_open:
            record["skipped"] = "circuit_open"
            result.attempts.append(record)
            return []
        try:
            found, cached = self._cached_search(provider, query, media_type, language)
            breaker.record_success()
            record.update(results=len(found), cached=cached)
            if provider.is_remote and not cached:
                result.budget_used += 1
            return found
        except AuthError as exc:
            breaker.trip()
            result.budget_used += 1
            record["error"] = str(exc)
        except RateLimitedError as exc:
            breaker.trip(60.0)  # limite/bloqueio temporário: volta a tentar em 1 min, não em 5
            result.budget_used += 1
            record["error"] = str(exc)
        except UnreachableError as exc:
            breaker.trip()
            result.budget_used += 1
            record["error"] = str(exc)
        except ProviderError as exc:
            breaker.record_failure()
            result.budget_used += 1
            record["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001 - fonte com bug não derruba o pipeline
            breaker.record_failure()
            record["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            result.attempts.append(record)
        log.warning("fonte falhou; tentando a próxima", extra={"stage": "search", **record})
        return []

    # chamadas ao mesmo tempo por banco no adiantamento; a Wikimedia pede uma por vez (etiqueta da API)
    PREFETCH_WORKERS = {"wikimedia": 1, "openverse": 1}

    def prefetch(self, scenes: list[Scene], image_scenes: set[int], workers: int = 4,
                 max_seconds: float = 120.0) -> int:
        """Antes da passada cena a cena, a 1ª consulta de cada cena vai aos bancos principais e o resultado fica no
        cache. São buscas que a passada faria de qualquer jeito (no modo "compare" a 1ª consulta vai a todos os
        bancos), só que uma de cada vez: a rede esperava sozinha (11 s por cena; a Wikimedia sozinha era 62% da
        espera). Aqui os bancos trabalham ao mesmo tempo, cada um no seu limite (``PREFETCH_WORKERS``). Bancos de
        reserva (Pexels, YouTube) ficam de fora. Falha aqui não liga o disjuntor: a passada tenta de novo. Depois
        de ``max_seconds`` o resto fica para a passada: sem chave, o Openverse aceita ~20 buscas por minuto e
        segurava as 164 cenas por 8 min."""
        reserve = set(self.cfg.search.reserve_providers)
        jobs: dict[str, tuple[BaseProvider, str, MediaType, str]] = {}
        for scene in scenes:
            query = next((q for q in [*scene.analysis.queries, scene.analysis.fallback_query] if q), "")
            if not query:
                continue
            lang = scene.analysis.query_language
            medias = [MediaType.VIDEO] + ([MediaType.IMAGE] if scene.index in image_scenes else [])
            for media in medias:
                for provider in self._ordered(media):
                    if not provider.is_remote or provider.name in reserve:
                        continue
                    key = self._search_key(provider, query, media, lang)
                    if key not in jobs and self.cache.get("search", key) is None:
                        jobs[key] = (provider, query, media, lang)
        if not jobs:
            return 0

        deadline = time.monotonic() + max_seconds

        def run(job: tuple[BaseProvider, str, MediaType, str]) -> None:
            provider, query, media, lang = job
            if self.breakers[provider.name].is_open or time.monotonic() > deadline:
                return
            try:
                self._cached_search(provider, query, media, lang)
            except Exception:  # noqa: BLE001 - só adiantamento: a passada cena a cena registra o erro
                pass

        by_provider: dict[str, list[tuple[BaseProvider, str, MediaType, str]]] = {}
        for job in jobs.values():
            by_provider.setdefault(job[0].name, []).append(job)
        pools = [ThreadPoolExecutor(max_workers=self.PREFETCH_WORKERS.get(name, max(1, workers)))
                 for name in by_provider]
        try:
            futures = [f for pool, group in zip(pools, by_provider.values()) for f in map(pool.submit, [run] * len(group), group)]
            for f in futures:
                f.result()
        finally:
            for pool in pools:
                pool.shutdown(wait=True)
        log.info("buscas adiantadas em paralelo", extra={"stage": "search", "buscas": len(jobs),
                                                          "por_banco": {k: len(v) for k, v in by_provider.items()}})
        return len(jobs)

    # ------------------------------------------------------------- search
    def search_scene(self, scene: Scene, needed: int, needed_images: int, used_keys: set[str],
                     budget: int | None = None, reviews: dict[str, str] | None = None) -> SceneSearchResult:
        """reviews: vereditos do revisor visual para esta cena (chave -> approved/rejected).
        Numa segunda passada, as buscas já feitas vêm do cache (não gastam orçamento)
        e a busca só avança se os aprovados não bastarem."""
        s = self.cfg.search
        max_calls = (s.max_searches_per_scene + max(0, needed - 3)) if budget is None else budget
        result = SceneSearchResult(scene.index, needed=needed + s.reserve_per_scene)
        pool: dict[str, Candidate] = {}
        lang = scene.analysis.query_language
        queries = list(dict.fromkeys([*scene.analysis.queries, scene.analysis.fallback_query]))

        reviews = reviews or {}

        def rerank() -> None:
            ranked = self.ranker.rank(scene, list(pool.values()))
            for c in ranked:
                c.review = reviews.get(c.key, "")
            # reprovados pelo revisor vão para o fim (só servem de último recurso)
            result.ranked = sorted(ranked, key=lambda c: c.review == "rejected" or bool(c.veto))

        def free(c: Candidate) -> bool:
            owner = self.claimed.get(c.key)
            return c.key not in used_keys and (owner is None or owner == scene.index)

        from .providers.local_library import LocalLibraryProvider, targeted

        def counts() -> tuple[int, int]:
            # arquivo marcado ("say") não conta como "já basta": a mesma foto casa com várias falas e quem usa é a
            # primeira cena; contando, a busca parava cedo e as cenas seguintes ficavam com sobra da vizinha (v4
            # dos disjuntores: plano B de 19 para 31 shots)
            good = [c for c in result.ranked if self.ranker.is_good(c) and free(c) and not targeted(c)]
            return (sum(c.media_type is MediaType.VIDEO for c in good), sum(c.media_type is MediaType.IMAGE for c in good))

        def budget_left(provider: BaseProvider) -> bool:
            counts_budget = provider.is_remote or s.local_counts_toward_budget
            return not counts_budget or result.budget_used < max_calls

        compare_all = s.strategy == "compare"

        reserve = set(s.reserve_providers)

        def run_pass(media: MediaType, target: int, query_list: list[str]) -> None:
            slot = 0 if media is MediaType.VIDEO else 1
            ordered = self._ordered(media)
            # bancos de reserva (Pexels) por último, e só se os outros não bastaram para esta consulta
            ordered = [p for p in ordered if p.name not in reserve] + [p for p in ordered if p.name in reserve]
            for query in query_list:
                # "compare": a mesma consulta vai para TODOS os bancos e o ranking escolhe o
                # melhor entre eles; só passa para a próxima consulta se ainda faltar mídia boa
                if compare_all and counts()[slot] >= target:
                    return
                for provider in ordered:
                    if provider.name in reserve and counts()[slot] >= target and \
                            self.cache.get("search", self._search_key(provider, query, media, lang)) is None:
                        result.attempts.append({"provider": provider.name, "query": query, "media": media.value,
                                                "skipped": "reserva"})
                        continue  # os outros bancos já bastaram: a busca do Pexels fica para quem precisa
                    if not compare_all and counts()[slot] >= target:
                        return
                    if not budget_left(provider):
                        continue
                    if self._skip_busy(provider, query, media, lang, scene):
                        result.attempts.append({"provider": provider.name, "query": query, "media": media.value,
                                                "skipped": "fila cheia"})
                        continue
                    found = self._attempt(provider, query, media, lang, result)
                    new = [c for c in found if c.key not in pool]
                    if new:
                        for c in new:
                            pool[c.key] = c
                        rerank()

        # arquivos seus marcados para esta fala ("say" no .json) entram sempre, sem depender das buscas
        for provider in self.providers.values():
            if isinstance(provider, LocalLibraryProvider) and provider.available():
                marked = [c for c in provider.marked_for(scene.text, scene.analysis.subject or "",
                                                         queries[0] if queries else "") if c.key not in pool]
                if marked:
                    result.attempts.append({"provider": provider.name, "query": "(say)", "media": "any",
                                            "results": len(marked)})
                    for c in marked:
                        pool[c.key] = c
                    rerank()

        target_videos = max(0, needed - needed_images) + s.reserve_per_scene
        run_pass(MediaType.VIDEO, target_videos, queries)
        if needed_images:
            run_pass(MediaType.IMAGE, needed_images, queries[:1] + queries[-1:])
        videos, images = counts()
        if videos + images < needed + s.reserve_per_scene:
            # completa com o tipo que faltar, ainda dentro do orçamento
            run_pass(MediaType.IMAGE if needed_images == 0 else MediaType.VIDEO, needed + s.reserve_per_scene, queries)

        videos, images = counts()
        if scene.index >= 0:  # cena de verdade (os itens de template usam índice negativo): reserva o que vai usar
            mine = [c for c in result.ranked if self.ranker.is_good(c) and free(c) and c.review != "rejected"]
            for c in mine[:needed]:
                self.claimed.setdefault(c.key, scene.index)
        log.info(
            "cena pesquisada",
            extra={"stage": "search", "scene": scene.index, "needed": needed, "good_videos": videos,
                   "good_images": images, "api_calls": result.budget_used,
                   "top_score": result.ranked[0].score if result.ranked else None},
        )
        return result


def source_funnel(results: dict[int, SceneSearchResult], shots: list, ranker: Ranker,
                  fail_counts: dict[str, int] | None = None,
                  fail_reasons: dict[str, list[str]] | None = None) -> dict[str, dict[str, Any]]:
    """Por fonte, onde os candidatos ficaram: achados -> com miniatura -> bons -> aprovados/reprovados pela IA ->
    download falhou -> usados. Mostra por que um banco que acha muita coisa quase não entra no vídeo."""
    from pathlib import Path

    seen: dict[str, Candidate] = {}
    for res in results.values():
        for c in res.ranked:
            seen.setdefault(c.key, c)
    out: dict[str, dict[str, Any]] = {}
    for c in seen.values():
        row = out.setdefault(c.provider, {"found": 0, "thumb": 0, "good": 0, "approved": 0, "rejected": 0,
                                          "vetoed": 0, "download_failed": 0, "used": 0})
        row["found"] += 1
        row["thumb"] += bool(c.local_path or (c.thumbnail_path and Path(c.thumbnail_path).exists()))
        row["good"] += ranker.is_good(c)
        row["approved"] += c.review == "approved"
        row["rejected"] += c.review == "rejected"
        row["vetoed"] += bool(c.veto)
    for sh in shots:
        if sh.candidate is not None:
            out.setdefault(sh.candidate.provider, {"found": 0, "thumb": 0, "good": 0, "approved": 0, "rejected": 0,
                                                   "vetoed": 0, "download_failed": 0, "used": 0})["used"] += 1
    for name, n in (fail_counts or {}).items():
        out.setdefault(name, {"found": 0, "thumb": 0, "good": 0, "approved": 0, "rejected": 0, "vetoed": 0,
                              "download_failed": 0, "used": 0})["download_failed"] = n
    for name, reasons in (fail_reasons or {}).items():
        if name in out and reasons:
            out[name]["errors"] = reasons
    return out
