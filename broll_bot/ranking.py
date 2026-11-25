"""Ranqueamento semântico dos candidatos.

Com CLIP (open_clip) compara o resumo visual da cena com a thumbnail real do
candidato — é isso que garante que o b-roll combina com a narração, e não só
com uma palavra-chave. Sem CLIP instalado, cai para similaridade lexical entre
consultas/resumo e tags/descrição.
"""

from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import requests

from .config import AppConfig
from .log import get_logger
from .models import Candidate, MediaType, Scene
from .text import keywords, overlap_score

log = get_logger("ranking")

TEXT_PROMPT = "an image with text, captions, letters, a logo or a watermark"
CLEAN_PROMPT = "a clean photo with no text or watermark"


class Embedder(Protocol):
    def encode_texts(self, texts: list[str]) -> np.ndarray: ...
    def encode_images(self, paths: list[Path]) -> np.ndarray: ...


class ClipEmbedder:
    """Embeddings imagem-texto com open_clip; imagens cacheadas em disco."""

    def __init__(self, model_name: str, pretrained: str, device: str, cache_dir: Path) -> None:
        import open_clip  # type: ignore[import-not-found]
        import torch  # type: ignore[import-not-found]

        self.torch = torch
        self.device = ("cuda" if torch.cuda.is_available() else "cpu") if device == "auto" else device
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(model_name, pretrained=pretrained)
        self.model.eval().to(self.device)
        self.tokenizer = open_clip.get_tokenizer(model_name)
        self.tag = f"{model_name}-{pretrained}"
        self.cache_dir = cache_dir / "embeddings" / self.tag
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._text_cache: dict[str, np.ndarray] = {}

    def encode_texts(self, texts: list[str]) -> np.ndarray:
        missing = [t for t in dict.fromkeys(texts) if t not in self._text_cache]
        if missing:
            with self.torch.no_grad():
                feats = self.model.encode_text(self.tokenizer(missing).to(self.device)).float()
                feats = feats / feats.norm(dim=-1, keepdim=True)
            for text, vec in zip(missing, feats.cpu().numpy()):
                self._text_cache[text] = vec
        return np.stack([self._text_cache[t] for t in texts])

    def _cache_file(self, path: Path) -> Path:
        stat = path.stat()
        digest = hashlib.sha1(f"{path.resolve()}|{stat.st_size}|{stat.st_mtime}".encode()).hexdigest()
        return self.cache_dir / f"{digest}.npy"

    def encode_images(self, paths: list[Path]) -> np.ndarray:
        from PIL import Image

        out: list[np.ndarray | None] = []
        todo: list[tuple[int, Path]] = []
        for i, path in enumerate(paths):
            cached = self._cache_file(path)
            if cached.exists():
                out.append(np.load(cached))
            else:
                out.append(None)
                todo.append((i, path))
        for start in range(0, len(todo), 16):
            batch = todo[start:start + 16]
            images = []
            for _, path in batch:
                with Image.open(path) as img:
                    images.append(self.preprocess(img.convert("RGB")))
            with self.torch.no_grad():
                feats = self.model.encode_image(self.torch.stack(images).to(self.device)).float()
                feats = feats / feats.norm(dim=-1, keepdim=True)
            for (i, path), vec in zip(batch, feats.cpu().numpy()):
                out[i] = vec
                np.save(self._cache_file(path), vec)
        return np.stack(out) if out else np.zeros((0, 1))  # type: ignore[arg-type]


def load_embedder(cfg: AppConfig, cache_dir: Path) -> Embedder | None:
    if not cfg.ranking.use_clip:
        return None
    try:
        embedder = ClipEmbedder(cfg.ranking.clip_model, cfg.ranking.clip_pretrained, cfg.ranking.device, cache_dir)
    except Exception as exc:  # noqa: BLE001 - CLIP é opcional
        log.warning("CLIP indisponível; usando ranking lexical", extra={"stage": "ranking", "error": str(exc)})
        return None
    log.info("CLIP carregado", extra={"stage": "ranking", "model": embedder.tag, "device": embedder.device})
    return embedder


class ThumbnailFetcher:
    """Baixa thumbnails (CDN, não conta no rate limit da API) com cache em disco."""

    def __init__(self, cache_dir: Path, max_workers: int = 6, timeout: float = 15.0,
                 user_agent: str | None = None) -> None:
        from .http import USER_AGENT

        self.dir = cache_dir / "thumbs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent or USER_AGENT  # a Wikimedia recusa agentes genéricos
        self.max_workers = max_workers
        self.timeout = timeout
        self.failures: dict[str, int] = {}
        self.reasons: dict[str, list[str]] = {}  # por fonte: por que a miniatura falhou (vai para o funil)
        # cabeçalho por endereço (a chave do Openverse na miniatura da API dele): {"api.openverse.org": fn}
        self.host_headers: dict[str, Any] = {}
        self._warned: set[str] = set()

    def _failed(self, cand: Candidate, why: str) -> None:
        self.failures[cand.provider] = self.failures.get(cand.provider, 0) + 1
        reasons = self.reasons.setdefault(cand.provider, [])
        host = re.sub(r"^https?://([^/]+).*$", r"\1", cand.thumbnail_url or "")
        if len(reasons) < 4 and f"{why} ({host})" not in reasons:
            reasons.append(f"{why} ({host})")
        if cand.provider not in self._warned:  # avisa uma vez por fonte (sem miniatura a IA não vê a foto)
            self._warned.add(cand.provider)
            log.warning("miniatura não baixou; a foto fica sem checagem visual",
                        extra={"stage": "ranking", "provider": cand.provider, "motivo": why,
                               "url": (cand.thumbnail_url or "")[:160]})

    def _one(self, cand: Candidate) -> None:
        if cand.thumbnail_path and Path(cand.thumbnail_path).exists():
            return
        if not cand.thumbnail_url:
            return
        safe_id = hashlib.sha1(cand.key.encode()).hexdigest()[:20]
        dest = self.dir / f"{cand.provider}_{safe_id}.jpg"
        if not dest.exists():
            urls = [cand.thumbnail_url]
            # foto sem miniatura que baixe (Openverse anônimo recusa a da API pelo limite; endereço errado): a
            # própria foto serve, reduzida — sem miniatura a IA não vê a foto e ela nunca entra no vídeo
            if cand.media_type is MediaType.IMAGE and cand.download_url and cand.download_url != cand.thumbnail_url:
                urls.append(cand.download_url)
            why = ""
            for url in urls:
                host = re.sub(r"^https?://([^/]+).*$", r"\1", url)
                extra = self.host_headers.get(host)
                try:
                    resp = self.session.get(url, timeout=self.timeout, headers=(extra() if callable(extra) else extra))
                except requests.RequestException as exc:
                    why = type(exc).__name__
                    continue
                if resp.status_code != 200 or not resp.content:
                    why = f"HTTP {resp.status_code}"
                    continue
                dest.write_bytes(_small_jpeg(resp.content) if url != cand.thumbnail_url else resp.content)
                break
            else:
                self._failed(cand, why or "sem resposta")
                return
        cand.thumbnail_path = str(dest)

    def fetch(self, candidates: list[Candidate]) -> None:
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            list(pool.map(self._one, candidates))


def _small_jpeg(data: bytes, side: int = 640) -> bytes:
    """Foto inteira usada como miniatura: reduzida para não encher o cache."""
    import io

    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((side, side))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=85)
        return out.getvalue()
    except Exception:  # noqa: BLE001 - formato que o Pillow não lê: guarda como veio
        return data


def _norm_clip(sim: np.ndarray | float) -> np.ndarray:
    """Cosseno CLIP típico (~0.15 irrelevante … ~0.32 ótimo) -> 0..1."""
    return np.clip((np.asarray(sim) - 0.15) / 0.17, 0.0, 1.0)


def _slug(url: str) -> str:
    """ "https://www.pexels.com/photo/dacia-duster-on-dirt-road-19923014/" -> "dacia-duster-on-dirt-road-19923014"."""
    parts = [p for p in (url or "").split("/") if p]
    return parts[-1] if len(parts) > 2 else ""


# acervos de época (filme de 640 px): a largura mínima não vale para eles
ARCHIVAL = {"archive"}


class Ranker:
    def __init__(self, cfg: AppConfig, embedder: Embedder | None, thumbs: ThumbnailFetcher | None) -> None:
        self.cfg = cfg
        self.embedder = embedder
        self.thumbs = thumbs
        self.eras: dict[str, tuple[int, int]] = {}  # ranking: carro -> anos aceitos nas fotos
        self.gens: dict[str, int] = {}  # ranking: carro -> geração falada ("troisième génération" -> 3)
        self.refs: dict[str, np.ndarray] = {}  # carro -> embeddings das fotos de referência
        from .subject import is_automotive

        self.automotive = is_automotive(cfg.analysis.niche)

    # --------------------------------------------------------------- scores
    def _quality(self, c: Candidate) -> float:
        if c.provider in ARCHIVAL:  # filme de 1944 nunca é HD: vale pelo que mostra, não pela resolução
            return 0.75
        return float(min(1.0, c.width / max(1, self.cfg.output.width)))

    def _duration_fit(self, c: Candidate) -> float:
        if c.media_type is MediaType.IMAGE:
            return 0.7
        return float(min(1.0, (c.duration or 0) / 5.0))

    def _orientation(self, c: Candidate) -> float:
        if self.cfg.output.height > self.cfg.output.width:  # vídeo em pé: mídia em pé preenche sem cortar
            return 1.0 if c.aspect <= 0.8 else 0.75 if c.aspect <= 1.05 else 0.6
        return 1.0 if c.aspect >= 1.3 else 0.6 if c.aspect >= 1.0 else 0.3

    @staticmethod
    def _lexical(scene: Scene, c: Candidate) -> float:
        doc = keywords(" ".join(c.tags) + " " + c.description)
        best_query = max((overlap_score(keywords(q), doc) for q in scene.analysis.queries), default=0.0)
        summary = overlap_score(keywords(scene.analysis.visual_summary)[:12], doc)
        return float(min(1.0, best_query + 0.3 * summary))

    @staticmethod
    def _negative_hit(scene: Scene, c: Candidate) -> bool:
        """Um termo negativo casa só se TODAS as palavras dele estão nas tags
        ("sports car" pega "red sports car", mas não qualquer "car")."""
        doc = set(keywords(" ".join(c.tags) + " " + c.description))
        for term in scene.analysis.negative_terms:
            words = set(keywords(term))
            if words and words <= doc:
                return True
        return False

    def is_good(self, c: Candidate) -> bool:
        """Bom o bastante para ir ao vídeo. O revisor visual (Claude) tem a
        palavra final; sem ele, exige score E relevância visual mínima. Veto das checagens
        visuais (prédio num canal de carro, carro batido, outro carro da lista) vale sempre."""
        if c.review == "rejected" or c.veto:
            return False
        if c.review == "approved":
            return True
        from .providers.local_library import targeted

        if targeted(c):  # arquivo seu marcado para esta fala ("say"): escolhido a dedo, só o revisor barra
            return True
        if c.score < self.cfg.search.min_score:
            return False
        return self.embedder is None or c.semantic >= self.cfg.search.min_semantic

    def _prefilter(self, scene: Scene, candidates: list[Candidate]) -> list[Candidate]:
        from .subject import other_brands, wrong_era, wrong_generation, wrong_model

        from .providers.local_library import fits_scene, targeted

        min_w = self.cfg.search.min_width * 0.75
        # arquivo seu marcado para a fala ("say") passa mesmo pequeno: o retrato de 303 px do Oliver Shallenberger
        # (o único que existe) era descartado e o cartão de perfil saiu só com as iniciais
        ok = [c for c in candidates if (c.width >= min_w or c.provider in ARCHIVAL or targeted(c))
              and (c.media_type is MediaType.IMAGE or (c.duration or 0) >= 1.5)]
        # foto de produto ("say" no .json) só na cena dele (na seção do ranking, só a do produto da seção)
        ok = [c for c in ok if fits_scene(c, scene.text, scene.analysis.subject or "")]
        car = scene.analysis.subject
        if car:  # ranking com vários carros: fora o outro modelo da mesma marca (Duster na seção da Logan)
            era = self.eras.get(car)

            def off(c: Candidate) -> bool:
                text = " ".join([*c.tags, c.description, _slug(c.page_url)])
                if c.local_path:  # foto sua: só não pode ser da pasta de outro carro (a geração você escolheu)
                    return wrong_model(text, car)
                # outra marca que esta fala não cita (busca "peugeot 208" herdada por um trecho sobre o Jazz)
                if other_brands(f"{text} {c.query}", scene.text, car):
                    return True
                return wrong_model(text, car) or wrong_era(text, era) or wrong_generation(text, self.gens.get(car))

            ok = [c for c in ok if not off(c)]
        # o arquivo marcado para esta cena ("say") nunca fica de fora do corte de candidatos
        ok.sort(key=lambda c: (targeted(c), self._lexical(scene, c) + 0.2 * self._quality(c)), reverse=True)
        return ok[: self.cfg.search.max_rank_candidates]

    def rank(self, scene: Scene, candidates: list[Candidate]) -> list[Candidate]:
        pool = self._prefilter(scene, candidates)
        if not pool:
            return []
        w = self.cfg.ranking.weights
        semantic = np.array([self._lexical(scene, c) for c in pool], dtype=float)
        text_pen = np.zeros(len(pool))
        neg_pen = np.array([1.0 if self._negative_hit(scene, c) else 0.0 for c in pool])

        if self.embedder is not None:
            if self.thumbs is not None:
                self.thumbs.fetch([c for c in pool if not c.thumbnail_path])
            with_thumb = [i for i, c in enumerate(pool) if c.thumbnail_path and Path(c.thumbnail_path).exists()]
            if with_thumb:
                try:
                    self._clip_scores(scene, pool, with_thumb, semantic, text_pen, neg_pen)
                except Exception as exc:  # noqa: BLE001 - ranking nunca derruba o pipeline
                    log.warning("falha no CLIP; mantendo score lexical", extra={"stage": "ranking", "error": str(exc)})

        if self.embedder is not None:
            # sem miniatura a IA não vê a foto: o título sozinho ("Contract Signing", "Underwater
            # archaeology") dava nota máxima. Fica abaixo do mínimo — só entra como último recurso
            cap = 0.6 * self.cfg.search.min_semantic
            for i, c in enumerate(pool):
                if not c.local_path and not (c.thumbnail_path and Path(c.thumbnail_path).exists()):
                    semantic[i] = min(semantic[i], cap)
        for i, c in enumerate(pool):
            if c.veto:
                neg_pen[i] = max(neg_pen[i], 1.0)
            c.semantic = round(float(semantic[i]), 4)
            c.text_penalty = round(float(text_pen[i]), 4)
            c.score = round(
                w.semantic * semantic[i] + w.quality * self._quality(c) + w.duration * self._duration_fit(c)
                + w.orientation * self._orientation(c) - w.text_penalty * text_pen[i] - w.negative_penalty * neg_pen[i]
                + (self.cfg.ranking.local_bonus if c.local_path else 0.0)
                + self.cfg.ranking.provider_bonus.get(c.provider, 0.0),
                4,
            )
        pool.sort(key=lambda c: c.score, reverse=True)
        return pool

    def _clip_scores(self, scene: Scene, pool: list[Candidate], idx: list[int], semantic: np.ndarray,
                     text_pen: np.ndarray, neg_pen: np.ndarray) -> None:
        assert self.embedder is not None
        a = scene.analysis
        summary_prompt = f"a {a.tone} stock footage shot: {a.visual_summary}"[:300]
        query_prompts = [f"a photo of {q}" for q in a.queries]
        neg_prompts = [f"a photo of {t}" for t in a.negative_terms[:8]]
        texts = [summary_prompt, *query_prompts, *neg_prompts, TEXT_PROMPT, CLEAN_PROMPT]
        t_emb = self.embedder.encode_texts(texts)
        i_emb = self.embedder.encode_images([Path(pool[i].thumbnail_path) for i in idx])  # type: ignore[arg-type]
        sims = i_emb @ t_emb.T  # (n_img, n_text)

        n_q, n_neg = len(query_prompts), len(neg_prompts)
        s_summary = sims[:, 0]
        s_query = sims[:, 1:1 + n_q].max(axis=1) if n_q else s_summary
        s_neg = sims[:, 1 + n_q:1 + n_q + n_neg].max(axis=1) if n_neg else np.full(len(idx), -1.0)
        logits = 100.0 * sims[:, -2:]
        p_text = np.exp(logits[:, 0]) / np.exp(logits).sum(axis=1)

        clip_sem = _norm_clip(0.6 * s_summary + 0.4 * s_query)
        from .visual_checks import apply_checks, check_texts

        for i in idx:
            pool[i].veto = ""
        rc = self.cfg.ranking
        if rc.visual_checks:
            apply_checks(scene, pool, idx, i_emb, self.embedder.encode_texts(check_texts()), self.refs,
                         self.automotive, clip_sem, rc.off_topic_margin, rc.crash_margin, rc.reference_margin)
        for j, i in enumerate(idx):
            pool[i].embedding = i_emb[j]
            # mistura com o lexical (metadados ajudam a desempatar)
            semantic[i] = 0.85 * clip_sem[j] + 0.15 * semantic[i]
            text_pen[i] = max(0.0, (p_text[j] - 0.5) * 2)
            if s_neg[j] > s_summary[j]:
                neg_pen[i] = max(neg_pen[i], min(1.0, (s_neg[j] - s_summary[j]) / 0.05))

    # ------------------------------------------------------- similaridade
    def text_image_scores(self, query: str, candidates: list[Candidate]) -> list[float]:
        """Usado pela biblioteca local para busca semântica (0..1)."""
        if self.embedder is None:
            return [0.0] * len(candidates)
        idx = [i for i, c in enumerate(candidates) if c.thumbnail_path and Path(c.thumbnail_path).exists()]
        scores = [0.0] * len(candidates)
        if not idx:
            return scores
        t = self.embedder.encode_texts([f"a photo of {query}"])
        imgs = self.embedder.encode_images([Path(candidates[i].thumbnail_path) for i in idx])  # type: ignore[arg-type]
        sims = _norm_clip(imgs @ t[0])
        for j, i in enumerate(idx):
            scores[i] = float(sims[j])
        return scores

    def too_similar(self, a: Candidate, b: Candidate) -> bool:
        if a.key == b.key:
            return True
        if a.embedding is not None and b.embedding is not None:
            return float(np.dot(a.embedding, b.embedding)) >= self.cfg.search.similarity_threshold
        ta, tb = set(a.tags), set(b.tags)
        return bool(ta and tb) and a.provider == b.provider and len(ta & tb) / len(ta | tb) > 0.85
