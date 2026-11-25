"""Escolha final de cada shot: baixa só os vencedores (+ reserva), valida e aplica planos B."""

from __future__ import annotations

import hashlib
import random
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from dataclasses import dataclass, field
from pathlib import Path

from .config import AppConfig
from .errors import ProviderError
from .framing import choose_layout
from .log import get_logger
from .media import FFmpegError, probe
from .motion.planner import MEDIA_FREE
from .models import Candidate, MediaType, Shot
from .providers.base import BaseProvider
from .providers.local_library import LocalLibraryProvider, _cites, targeted
from .ranking import Ranker, _slug
from .search import SceneSearchResult
from .subject import BRANDS, names_car, other_brands

log = get_logger("selection")

MAX_SLOW = 1.6  # clipe curto: até 1,6x mais lento; mais que isso, segura o último quadro (nunca loop)


def too_short(cand: Candidate, seconds: float) -> bool:
    """Vídeo que não cobre o shot nem em câmera lenta (ficaria parado no último quadro)."""
    return cand.media_type is MediaType.VIDEO and 0 < (cand.duration or 0) * MAX_SLOW < seconds


def file_fingerprint(path: Path) -> str:
    """Hash rápido (tamanho + primeiros 4 MB) para deduplicar downloads."""
    h = hashlib.sha1(str(path.stat().st_size).encode())
    with open(path, "rb") as fh:
        h.update(fh.read(4 * 1024 * 1024))
    return h.hexdigest()


@dataclass
class Selector:
    cfg: AppConfig
    providers: dict[str, BaseProvider]
    ranker: Ranker
    media_dir: Path
    seed: int
    used_keys: set[str] = field(default_factory=set)
    used_hashes: dict[str, str] = field(default_factory=dict)
    recent: list[Candidate] = field(default_factory=list)
    reserves: dict[int, Candidate] = field(default_factory=dict)
    failed_keys: set[str] = field(default_factory=set)
    path_map: dict[str, Path] = field(default_factory=dict)
    downloads: int = 0
    # downloads que falharam por fonte (o relatório mostra o motivo: yt-dlp sem Deno, vídeo removido...)
    fail_counts: dict[str, int] = field(default_factory=dict)
    fail_reasons: dict[str, list[str]] = field(default_factory=dict)
    prev_layout: str | None = None
    # vídeo de ranking: cena -> carro da seção (as sobras de toda a seção servem de plano B)
    section_of: dict[int, str] = field(default_factory=dict)
    # fala de cada cena: sobra de outra cena não pode mostrar uma marca que esta fala não cita
    scene_text: dict[int, str] = field(default_factory=dict)
    # quando cada mídia apareceu por último (s): repetição só longe da anterior
    used_at: dict[str, float] = field(default_factory=dict)
    use_times: dict[str, list[float]] = field(default_factory=dict)
    # carro do vídeo (quando é um só): a pasta dele na biblioteca local pode entrar como plano B
    subject: str = ""

    # --------------------------------------------------------- download
    def prefetch(self, shots: list[Shot], results: dict[int, SceneSearchResult], workers: int = 6) -> int:
        """Baixa em paralelo o que cada cena provavelmente vai usar (os melhores aprovados, um por shot + a
        reserva), antes da distribuição, que baixa um arquivo por vez. Na v1 das 9 baterias essa etapa levou
        23 min com ~470 downloads. A distribuição continua igual: o arquivo já baixado só é reaproveitado
        (todos os bancos devolvem o arquivo que já está na pasta). YouTube no máximo 2 por vez (dá 429)."""
        per_scene: dict[int, int] = {}
        for shot in shots:
            if shot.candidate is None and shot.template not in MEDIA_FREE:
                per_scene[shot.scene_index] = per_scene.get(shot.scene_index, 0) + 1
        jobs: dict[str, Candidate] = {}
        for idx, n in per_scene.items():
            res = results.get(idx)
            if res is None:
                continue
            good = [c for c in res.ranked if self.ranker.is_good(c) and c.review != "rejected" and not c.veto
                    and c.key not in self.failed_keys]
            for c in sorted(good, key=lambda c: -c.score)[: n + self.cfg.search.reserve_per_scene]:
                provider = self.providers.get(c.provider)
                if provider is not None and provider.is_remote:
                    jobs.setdefault(c.key, c)
        if not jobs:
            return 0
        youtube = threading.Semaphore(2)

        def run(cand: Candidate) -> None:
            provider = self.providers[cand.provider]
            gate = youtube if cand.provider.startswith("youtube") else None
            try:
                if gate is not None:
                    with gate:
                        provider.download(cand, self.media_dir)
                else:
                    provider.download(cand, self.media_dir)
            except Exception:  # noqa: BLE001 - a distribuição tenta de novo e registra o motivo
                pass

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(run, jobs.values()))
        log.info("downloads adiantados em paralelo", extra={"stage": "download", "arquivos": len(jobs)})
        return len(jobs)

    def fetch(self, cand: Candidate) -> Path | None:
        """Baixa (ou reaproveita), valida com ffprobe e deduplica por hash."""
        if cand.key in self.failed_keys:
            return None
        provider = self.providers.get(cand.provider)
        if provider is None:
            return None
        try:
            path = provider.download(cand, self.media_dir)
            info = probe(path)
        except (ProviderError, FFmpegError, OSError) as exc:
            self.failed_keys.add(cand.key)
            self.fail_counts[cand.provider] = self.fail_counts.get(cand.provider, 0) + 1
            reasons = self.fail_reasons.setdefault(cand.provider, [])
            if len(reasons) < 3 and str(exc)[:200] not in reasons:
                reasons.append(str(exc)[:200])
            log.warning("mídia descartada", extra={"stage": "download", "candidate": cand.key, "error": str(exc)})
            return None
        if cand.media_type is MediaType.VIDEO:
            cand.duration = info.duration
        cand.width, cand.height = info.width, info.height
        fp = file_fingerprint(path)
        owner = self.used_hashes.get(fp)
        if owner and owner != cand.key:
            self.failed_keys.add(cand.key)
            log.info("arquivo duplicado ignorado", extra={"stage": "download", "candidate": cand.key, "same_as": owner})
            return None
        self.used_hashes[fp] = cand.key
        self.downloads += provider.is_remote
        return path

    # ---------------------------------------------------------- escolha
    def _acceptable(self, cand: Candidate, good_only: bool) -> bool:
        if cand.key in self.used_keys or cand.key in self.failed_keys:
            return False
        if good_only and not self.ranker.is_good(cand):
            return False
        if self.is_duplicate(cand):
            return False
        return not any(self.ranker.too_similar(cand, prev) for prev in self.recent[-3:])

    def is_duplicate(self, cand: Candidate) -> bool:
        """A mesma foto já entrou no vídeo vinda de outra fonte (Wikimedia e Pexels, 2 tamanhos
        do mesmo arquivo...): quase idêntica no CLIP. Repetir de propósito é outra coisa (plano B)."""
        if cand.embedding is None:
            return False
        limit = self.cfg.search.duplicate_threshold
        return any(prev.embedding is not None and prev.key != cand.key
                   and float(np.dot(prev.embedding, cand.embedding)) >= limit for prev in self.recent)

    def _pick(self, pools: list[list[Candidate]], prefer: MediaType, good_only: bool,
              seconds: float = 0.0) -> tuple[Candidate, Path] | None:
        for pool in pools:
            # um clipe que cobre o shot vem antes de um curto demais (que ficaria parado no fim); o arquivo seu
            # marcado para esta cena ("say" no .json) vem antes da preferência por vídeo
            ordered = sorted(pool, key=lambda c: (c.review == "rejected" or bool(c.veto), too_short(c, seconds),
                                                  not targeted(c), c.media_type is not prefer, -c.score))
            for cand in ordered:
                if not self._acceptable(cand, good_only):
                    continue
                path = self.fetch(cand)
                if path is not None:
                    return cand, path
        return None

    def assign(self, shots: list[Shot], results: dict[int, SceneSearchResult]) -> None:
        for shot in shots:
            if shot.candidate is not None:  # já resolvido (ex.: itens do quadro comparativo)
                continue
            if shot.template in MEDIA_FREE:  # título de destaque: não usa mídia
                continue
            own = results.get(shot.scene_index, SceneSearchResult(shot.scene_index)).ranked
            seconds = shot.frames / self.cfg.output.fps
            choice = self._pick([own], shot.prefer_media, good_only=True, seconds=seconds)
            reason = None
            # arquivo seu marcado para esta fala, já mostrado bem antes, vem antes da sobra da cena vizinha: no fim do
            # vídeo do relógio de luz ("Virou um disco de alumínio...", "olhar pela janelinha") entraram vinil e
            # laminação de metal porque os 6 vídeos do disco girando já tinham sido usados no começo
            if choice is None and (again := self._reuse_marked(shot, own)) is not None:
                choice, reason = (again, self.path_map[again.key]), "reused_with_new_framing"
            # planos B, do mais relevante para o menos. Repetir um clipe que combina
            # (com outro enquadramento) vem antes de mostrar algo fora do assunto,
            # e nenhum plano B tira de outra cena um clipe que ela vai usar.
            if choice is None and (choice := self._pick([self._neighbor_surplus(shot, results)], shot.prefer_media, True,
                                                        seconds)):
                reason = "neighbor_scene_surplus"
            if choice is None and (reuse := self._reuse_candidate(shot, results, relevant_only=True)) is not None:
                choice, reason = (reuse, self.path_map[reuse.key]), "reused_with_new_framing"
            if choice is None:
                # mídia fraca da própria cena, mas nunca muito fora do assunto
                claimed = self._claimed(shot, results)
                floor = 0.7 * self.cfg.search.min_semantic if self.ranker.embedder is not None else 0.0
                weak_own = [c for c in own if c.key not in claimed and c.review != "rejected" and not c.veto
                            and c.semantic >= floor]
                if choice := self._pick([weak_own], shot.prefer_media, False, seconds):
                    reason = "below_min_score"
            if choice is None and (choice := self._library_last_resort(shot, results, allow_claimed=False)):
                reason = "library_last_resort"
            if choice is None and (reuse := self._reuse_candidate(shot, results, relevant_only=False)) is not None:
                choice, reason = (reuse, self.path_map[reuse.key]), "reused_with_new_framing"
            if choice is None and (choice := self._library_last_resort(shot, results, allow_claimed=True)):
                reason = "library_last_resort"
            if choice is None:
                shot.fallback_reason = "generated_background"
                shot.layout = {"name": "generated"}
                log.warning("sem mídia para o shot; usando fundo gerado", extra={"stage": "selection", "shot": shot.index})
                continue
            cand, path = choice
            if self.reserves.get(shot.scene_index) is cand:
                del self.reserves[shot.scene_index]
            self.apply(shot, cand, path, reason)
            if reason:
                log.info("plano B no shot", extra={"stage": "selection", "shot": shot.index, "reason": reason,
                                                   "candidate": cand.key})

            # reserva: garante 1 candidato extra baixado por cena
            if shot.scene_index not in self.reserves and self.cfg.search.reserve_per_scene > 0:
                self._download_reserve(shot.scene_index, own)

    def apply(self, shot: Shot, cand: Candidate, path: Path, reason: str | None) -> None:
        """Associa a mídia ao shot e sorteia enquadramento e ponto de entrada."""
        rng = random.Random(self.seed * 100003 + shot.index)
        self._register(cand, path)
        self.used_at[cand.key] = shot.cut_out
        self.use_times.setdefault(cand.key, []).append(shot.cut_in)
        shot.candidate, shot.media_path, shot.fallback_reason = cand, str(path), reason
        shot.layout = choose_layout(cand.media_type, cand.aspect, self.cfg.timeline.video_layouts,
                                    self.cfg.timeline.image_layouts, rng, self.prev_layout,
                                    out_aspect=self.cfg.output.width / max(1, self.cfg.output.height))
        self.prev_layout = shot.layout["name"]
        self._set_offset(shot, rng)

    def _claimed(self, shot: Shot, results: dict[int, SceneSearchResult]) -> set[str]:
        """Candidatos que as PRÓXIMAS cenas vão usar (os melhores até o que cada uma precisa). As
        cenas de antes já escolheram as delas (os shots saem em ordem): o que sobrou é sobra mesmo."""
        claimed: set[str] = set()
        for idx, res in results.items():
            if idx <= shot.scene_index:
                continue
            good = [c for c in res.ranked if self.ranker.is_good(c) and c.key not in self.used_keys]
            claimed.update(c.key for c in good[: res.needed or len(good)])
        return claimed

    def _neighbor_surplus(self, shot: Shot, results: dict[int, SceneSearchResult]) -> list[Candidate]:
        """Sobras das cenas vizinhas; no ranking, de todas as cenas do mesmo carro (as mais próximas
        primeiro). Uma foto nova do carro certo é melhor que repetir a mesma com outro enquadramento."""
        claimed = self._claimed(shot, results)
        idx = shot.scene_index
        near = [idx - 1, idx + 1]
        car = self.section_of.get(idx)
        if not car:  # introdução/fechamento: sobras de até 3 cenas de distância antes de repetir algo
            near += [idx - 2, idx + 2, idx - 3, idx + 3]
        if car:
            near += sorted((i for i, c in self.section_of.items() if c == car and abs(i - idx) > 1),
                           key=lambda i: abs(i - idx))
        return [c for i in near if i in results for c in results[i].ranked
                if c.key not in claimed and not self._foreign(c, idx) and self._said_here(c, idx)]

    def _said_here(self, cand: Candidate, scene_index: int) -> bool:
        """Foto de produto ("say" no .json da biblioteca) só na cena que fala dele, também no plano B."""
        from .providers.local_library import fits_scene

        return fits_scene(cand, self.scene_text.get(scene_index, ""), self.section_of.get(scene_index, ""))

    def _foreign(self, cand: Candidate, scene_index: int) -> bool:
        """A mídia foi achada/descrita com outra marca que a fala desta cena não cita: a foto do
        Peugeot 208 da comparação não pode ir para a cena dos bancos do Honda Jazz."""
        if scene_index not in self.scene_text:
            return False
        text = " ".join([cand.query, cand.description, *cand.tags, _slug(cand.page_url)])
        return bool(other_brands(text, self.scene_text[scene_index], self.section_of.get(scene_index, "")))

    def _library_last_resort(self, shot: Shot, results: dict[int, SceneSearchResult],
                             allow_claimed: bool) -> tuple[Candidate, Path] | None:
        """Nada casou com a cena: melhor um clipe da biblioteca local do usuário
        (ainda não usado) do que um fundo gerado. Clipes que são bons para outras
        cenas ficam por último, para não tirar a mídia certa de outra cena."""
        libs = [p for p in self.providers.values() if isinstance(p, LocalLibraryProvider)]
        pool = [(c, p) for p in libs for c in p.usable]
        text = self.scene_text.get(shot.scene_index)
        # arquivo de um assunto (com ``requires``) só na cena que fala dele: no vídeo dos disjuntores o roteiro
        # dizia "Brasil" uma vez e a foto do vendedor de carvão (requires: brasil, carvão...) entrou na cena do
        # "Clac" da mola do disjuntor. Os genéricos (sem ``requires``) continuam servindo em qualquer cena.
        pool = [c for c, p in pool if c.key not in self.used_keys and c.key not in self.failed_keys
                and self._said_here(c, shot.scene_index)
                and (text is None or not (words := p.requires_words(c)) or _cites(words, text))]
        car = self.section_of.get(shot.scene_index) or self.subject
        # só a pasta do próprio carro ou pastas sem marca (oficina, estrada...); nunca a de outro carro
        # (o motor da Logan apareceu na seção do Mazda 6; a Logan e o Honda Jazz no vídeo do Range Rover)
        pool = [c for c in pool if not any(t in BRANDS for t in c.tags) or (car and names_car(c.tags, car))]
        if not pool:
            return None
        claimed = self._claimed(shot, results)
        if not allow_claimed:
            pool = [c for c in pool if c.key not in claimed]
        rng = random.Random(self.seed * 7919 + shot.index)
        rng.shuffle(pool)
        # a pasta do próprio carro primeiro
        pool.sort(key=lambda c: (bool(car) and not names_car(c.tags, car), c.key in claimed,
                                 too_short(c, shot.frames / self.cfg.output.fps), c.media_type is not shot.prefer_media))
        for cand in pool:  # ordem acima vale mais que a preferência de mídia de _pick
            if self._acceptable(cand, False) and (path := self.fetch(cand)) is not None:
                return cand, path
        return None

    def _reuse_marked(self, shot: Shot, own: list[Candidate]) -> Candidate | None:
        """O arquivo marcado ("say") desta cena que já apareceu há mais de ``min_reuse_gap_seconds`` e menos de
        ``max_marked_uses`` vezes; entre eles, o que apareceu há mais tempo."""
        gap = self.cfg.search.min_reuse_gap_seconds
        cap = self.cfg.search.max_marked_uses  # vídeo do hidrômetro: 12 fotos apareceram 4 ou 5 vezes cada
        last = self.recent[-1].key if self.recent else None
        options = [c for c in own if targeted(c) and c.key in self.path_map and c.key != last
                   and len(self.use_times.get(c.key, [])) < cap
                   and c.review != "rejected" and not c.veto and self._said_here(c, shot.scene_index)
                   and shot.cut_in - self.used_at.get(c.key, -1e9) >= gap]
        return min(options, key=lambda c: self.used_at.get(c.key, -1e9)) if options else None

    def _reuse_candidate(self, shot: Shot, results: dict[int, SceneSearchResult],
                         relevant_only: bool) -> Candidate | None:
        """Repete um clipe já baixado (com outro enquadramento e ponto de entrada).
        Prioriza os que são bons para esta cena, depois os das cenas vizinhas e, entre
        eles, o menos usado e há mais tempo. O mesmo do shot anterior só se for
        relevante e não houver outro."""
        last_use: dict[str, int] = {}
        uses: dict[str, int] = {}
        for pos, c in enumerate(self.recent):
            last_use[c.key] = pos
            uses[c.key] = uses.get(c.key, 0) + 1
        by_key = {c.key: c for c in reversed(self.recent) if c.key in self.path_map
                  and self._said_here(c, shot.scene_index)}
        last = self.recent[-1].key if self.recent else None

        def good_keys(idx: int) -> set[str]:
            res = results.get(idx)
            return {c.key for c in res.ranked if self.ranker.is_good(c)} if res else set()

        own_keys = good_keys(shot.scene_index)
        near_keys = good_keys(shot.scene_index - 1) | good_keys(shot.scene_index + 1)
        # o que acabou de aparecer não volta logo em seguida (A, B, A, B se nota na hora). Exceção:
        # outro trecho do próprio vídeo da cena (parece um plano mais longo, não uma repetição)
        gap = self.cfg.search.min_reuse_gap_seconds

        def recent_uses(k: str) -> int:
            return sum(shot.cut_in - t < gap for t in self.use_times.get(k, []))

        # vídeo da própria cena: no máximo 2 aparições a cada ``gap`` s (não 3 vezes em 13 s)
        far = {k for k in by_key if shot.cut_in - self.used_at.get(k, -1e9) >= gap
               or (k in own_keys and by_key[k].media_type is MediaType.VIDEO and recent_uses(k) < 2)}
        options = [k for k in by_key if k != last and k in far]
        if not relevant_only and not options:  # último plano B antes do fundo gerado: o mais antigo,
            # nunca um dos 2 últimos (A, B, A, B); com 3+ clipes, gira entre eles
            last_two = {c.key for c in self.recent[-2:]}
            options = sorted((k for k in by_key if k not in last_two), key=lambda k: self.used_at.get(k, -1e9))[:1] \
                or [k for k in by_key if k in own_keys and by_key[k].media_type is MediaType.VIDEO][:1]
            # (sem nada: o vídeo da própria cena, antes de tirar de outra cena a mídia dela)
        if relevant_only:
            # repetir em sequência um clipe que combina (outro trecho/enquadramento)
            # ainda é melhor que mostrar algo fora do assunto — mas com limite de vezes
            cap = max(1, self.cfg.search.max_uses_per_clip)
            # foto repetida se reconhece na hora (vídeo com outro trecho, não): foto no máximo 2 vezes
            limit = {k: min(cap, 2) if by_key[k].media_type is MediaType.IMAGE else cap for k in by_key}
            relevant = [k for k in by_key if (k in own_keys or (k in near_keys and not self._foreign(by_key[k], shot.scene_index)))
                        and uses[k] < limit[k] and k in far]
            options = [k for k in relevant if k != last] or relevant
        if not options:
            return None
        best = min(options, key=lambda k: (k not in own_keys, k not in near_keys, uses[k], last_use[k]))
        return by_key[best]

    def claim(self, cand: Candidate) -> Path | None:
        """Baixa e reserva uma mídia extra (ex.: cards do comparativo), sem usar um shot."""
        if cand.key in self.used_keys or cand.key in self.failed_keys:
            return None
        path = self.fetch(cand)
        if path is not None:
            self._register(cand, path)
        return path

    def _register(self, cand: Candidate, path: Path) -> None:
        self.path_map[cand.key] = path
        self.used_keys.add(cand.key)
        self.recent.append(cand)

    def _download_reserve(self, scene_index: int, pool: list[Candidate]) -> None:
        for cand in sorted(pool, key=lambda c: -c.score):
            if cand.key in self.used_keys or cand.key in self.failed_keys:
                continue
            if not self.ranker.is_good(cand):
                continue
            if not any(self.ranker.too_similar(cand, prev) for prev in self.recent[-3:]) and self.fetch(cand):
                self.reserves[scene_index] = cand
                return

    def take_reserve(self, scene_index: int) -> tuple[Candidate, Path] | None:
        """Usado quando a renderização de um shot falha."""
        cand = self.reserves.pop(scene_index, None)
        if cand is None or cand.key in self.used_keys:
            return None
        path = self.fetch(cand)
        return (cand, path) if path else None

    def _set_offset(self, shot: Shot, rng: random.Random) -> None:
        """Ponto de entrada variado no vídeo (não começa sempre no 0s)."""
        cand = shot.candidate
        if cand is None or cand.media_type is MediaType.IMAGE:
            return
        fps = self.cfg.output.fps
        needed = shot.frames / fps
        src = cand.duration or 0.0
        if src >= needed + 0.2:
            slack = src - needed - 0.1
            shot.source_offset = round(rng.uniform(min(0.25, slack), slack), 3)
            shot.speed = 1.0
        elif src * MAX_SLOW >= needed and src > 0:
            # clipe curto: câmera lenta leve em vez de repetir o trecho
            shot.speed = round(min(MAX_SLOW, needed / src * 1.02), 4)
            shot.source_offset = 0.0
        else:
            # curto demais até em câmera lenta: câmera lenta máxima e o último quadro segura (o assembler
            # congela); enquadramento parado ganha um push lento, senão a imagem fica estática
            shot.speed = MAX_SLOW if src > 0 else 1.0
            shot.source_offset = 0.0
            if src > 0 and shot.layout.get("name") in ("full", "zoom_crop"):
                z0 = round(rng.uniform(1.0, 1.04), 3)
                shot.layout = {"name": "push", "z0": z0, "z1": round(z0 + rng.uniform(0.08, 0.14), 3),
                               "fx": 0.5, "fy": 0.5}
        if shot.fallback_reason == "reused_with_new_framing" and src > needed:
            shot.source_offset = round(rng.uniform(0, max(0.0, src - needed - 0.05)), 3)
