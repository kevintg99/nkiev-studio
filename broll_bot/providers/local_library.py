"""Biblioteca local (ex.: clipes do Mixkit baixados manualmente).

O Mixkit não tem API pública e os termos não permitem scraping, então os
clipes são baixados manualmente para uma pasta. Este módulo indexa essa pasta
(nome, tags, duração, resolução e thumbnail) e expõe a mesma interface das
fontes remotas. Embeddings visuais são calculados e cacheados pelo ranking.

Metadados extras opcionais: um arquivo ``<nome>.json`` ao lado do clipe com
``{"tags": [...], "description": "...", "page_url": "...", "credit": "autor (licença)", "requires": [...]}``.
Editar o ``.json`` também reindexa o arquivo. ``requires``: o arquivo só entra em vídeo cujo roteiro cite
uma dessas palavras (o lançamento da SpinLaunch não vira "pó caindo" num vídeo sobre carvão).
``say``: o arquivo só entra na CENA que diz uma dessas palavras (ou na seção do ranking com esse nome): a
foto da Optima YellowTop só onde a fala é sobre ela, não em qualquer frase sobre bateria.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable

from ..log import get_logger
from ..media import IMAGE_EXTS, VIDEO_EXTS, FFmpegError, extract_frame, probe
from ..models import Candidate, MediaType
from ..text import FILENAME_NOISE, keywords, overlap_score
from .base import BaseProvider, ProviderError

log = get_logger("library")

INDEX_VERSION = 3

SemanticFn = Callable[[str, list[Candidate]], list[float]]


def tags_from_filename(stem: str) -> list[str]:
    """'toyota-corolla-2020-xei-white_driving' -> toyota corolla 2020 xei white driving.
    Mantém anos e códigos de geração (2020, e100, 2.0 vira "2" e "0" e sai); ignora ids do Mixkit."""
    words = re.split(r"[^a-z0-9]+", stem.lower())
    out = []
    for w in words:
        if w in FILENAME_NOISE or len(w) < 2:
            continue
        if w.isdigit() and not (len(w) == 4 and 1900 <= int(w) <= 2100):
            continue  # números soltos (ids como "4211") não ajudam; anos sim
        out.append(w)
    return out


_SAY: dict[str, tuple[list[str], list[str]]] = {}


def said_words(local_path: str) -> tuple[list[str], list[str]]:
    """(``say``, ``say_not``) do .json ao lado do arquivo (vazios = vale em qualquer cena)."""
    if local_path not in _SAY:
        say: list[str] = []
        say_not: list[str] = []
        sidecar = Path(local_path).with_suffix(".json")
        if sidecar.exists():
            try:
                meta = json.loads(sidecar.read_text(encoding="utf-8"))
                say = [str(w) for w in meta.get("say", [])]
                say_not = [str(w) for w in meta.get("say_not", [])]
            except (OSError, ValueError):
                pass
        _SAY[local_path] = (say, say_not)
    return _SAY[local_path]


def _cites(words: list[str], text: str) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text or "", re.I) for w in words)


def targeted(cand: Candidate) -> bool:
    """Arquivo seu com ``say`` no .json: foi escolhido a dedo para as cenas que dizem aquelas palavras (o filtro
    ``fits_scene`` já garante que a fala desta cena cita uma delas). Vem antes de qualquer outra mídia da cena,
    mesmo quando o shot prefere vídeo: no vídeo dos disjuntores as fotos do fusível queimado e da tirinha
    bimetálica foram aprovadas, mas perderam para vídeos de solda e do YouTube só por serem foto."""
    return bool(cand.local_path) and bool(said_words(cand.local_path)[0])


def fits_scene(cand: Candidate, scene_text: str, section: str = "") -> bool:
    """Foto de um produto só na cena que fala dele. No vídeo das 9 baterias, a foto da Optima foi parar em
    "Number four, the type" e a da Kirkland em "cold cranking amps" (o CLIP acha qualquer bateria parecida com
    qualquer frase sobre bateria); gastas ali, faltaram na seção de cada uma. ``say_not`` tira a foto da cena
    de um produto parecido (a Kirkland comum não entra na seção "Kirkland Signature AGM"). Dentro de uma seção
    do ranking (``section``), só a foto do produto da seção: a O-E Plus citada na seção da MotoMaster comum
    ("the next MotoMaster up, the O-E Plus") era gasta ali e faltava na seção #2."""
    if not cand.local_path:
        return True
    say, say_not = said_words(cand.local_path)
    text = f"{scene_text} {section}"
    if say_not and _cites(say_not, text):
        return False
    if say and section:
        return _cites(say, section)
    return not say or _cites(say, text)


class LocalLibraryProvider(BaseProvider):
    name = "mixkit_local"
    is_remote = False
    supported_media = (MediaType.VIDEO, MediaType.IMAGE)

    def __init__(self, root: Path, index_path: Path, name: str = "mixkit_local",
                 semantic: SemanticFn | None = None, min_semantic: float = 0.35) -> None:
        self.root = root
        self.index_path = index_path
        self.name = name  # type: ignore[misc]
        self.semantic = semantic
        self.min_semantic = min_semantic
        self._entries: list[Candidate] | None = None
        self._context = ""  # roteiro do vídeo (para o "requires" do .json); vazio = tudo liberado
        self._requires: dict[str, list[str]] = {}
        self._requires_min: dict[str, int] = {}
        self._allowed: dict[str, bool] = {}

    @property
    def context(self) -> str:
        return self._context

    @context.setter
    def context(self, text: str) -> None:
        self._context = text
        self._allowed.clear()

    def available(self) -> bool:
        return self.root.is_dir() and bool(self.entries)

    def unavailable_reason(self) -> str:
        return f"pasta vazia ({self.root.name}): coloque vídeos/fotos lá para usar"

    # ---------------------------------------------------------------- index
    @property
    def entries(self) -> list[Candidate]:
        if self._entries is None:
            self._entries = self.build_index()
        return self._entries

    def reindex(self) -> None:
        self._entries = self.build_index(force=True)

    @property
    def usable(self) -> list[Candidate]:
        """Os arquivos que podem entrar neste vídeo (ver ``allowed``)."""
        return [c for c in self.entries if self.allowed(c)]

    def allowed(self, cand: Candidate) -> bool:
        """Arquivo com ``requires`` no .json só entra se o roteiro citar uma das palavras. A resposta fica guardada:
        procurar as palavras no roteiro inteiro a cada busca levava 62% do tempo da busca (250 arquivos x 4.288
        palavras, em cada uma das ~1.000 buscas na biblioteca do vídeo das 9 baterias)."""
        if not self.context or not cand.local_path:
            return True
        hit = self._allowed.get(cand.local_path)
        if hit is None:
            hit = self._allowed[cand.local_path] = self._check_requires(cand)
        return hit

    def requires_words(self, cand: Candidate) -> list[str]:
        """As palavras do ``requires`` do .json (vazio = arquivo genérico, serve em qualquer vídeo)."""
        if not cand.local_path:
            return []
        self._check_requires(cand)
        return self._requires.get(cand.local_path, [])

    def _check_requires(self, cand: Candidate) -> bool:
        """``requires_min`` no .json (padrão 1): quantas vezes, somando, o roteiro precisa citar as palavras. Uma pasta
        de assunto usa 3: o vídeo do hidrômetro diz "relógio de luz" duas vezes, de passagem, e não pode puxar as
        fotos do relógio de luz (no do relógio de luz, a frase "Lembra do vídeo do disjuntor" puxou as de disjuntor)."""
        if cand.local_path not in self._requires:
            words: list[str] = []
            need = 1
            sidecar = Path(cand.local_path).with_suffix(".json")
            if sidecar.exists():
                try:
                    meta = json.loads(sidecar.read_text(encoding="utf-8"))
                    words = [str(w) for w in meta.get("requires", [])]
                    need = max(1, int(meta.get("requires_min", 1)))
                except (OSError, ValueError, TypeError):
                    words = []
            self._requires[cand.local_path] = words
            self._requires_min[cand.local_path] = need
        words = self._requires[cand.local_path]
        if not words:
            return True
        need = self._requires_min.get(cand.local_path, 1)
        hits = sum(len(re.findall(rf"(?<!\w){re.escape(w)}(?!\w)", self.context, re.I)) for w in words)
        return hits >= need

    def build_index(self, force: bool = False) -> list[Candidate]:
        if not self.root.is_dir():
            return []
        old: dict[str, dict] = {}
        if self.index_path.exists() and not force:
            try:
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                if data.get("version") == INDEX_VERSION:
                    old = {e["local_path"]: e for e in data.get("entries", [])}
            except (OSError, ValueError):
                old = {}

        entries: list[dict] = []
        thumbs_dir = self.index_path.parent / "thumbs" / self.name
        for path in sorted(self.root.rglob("*")):
            ext = path.suffix.lower()
            if ext not in VIDEO_EXTS | IMAGE_EXTS or not path.is_file():
                continue
            stat = path.stat()
            sidecar = path.with_suffix(".json")
            side_mtime = sidecar.stat().st_mtime if sidecar.exists() else None
            cached = old.get(str(path))
            if (cached and cached.get("_mtime") == stat.st_mtime and cached.get("_size") == stat.st_size
                    and cached.get("_side_mtime") == side_mtime):
                entries.append(cached)
                continue
            try:
                info = probe(path)
            except FFmpegError as exc:
                log.warning("arquivo ignorado na biblioteca", extra={"stage": "library", "file": path.name, "error": str(exc)})
                continue
            is_image = ext in IMAGE_EXTS
            # a pasta também vira tag: library/mixkit/toyota-corolla-2020/frente.mp4
            # pasta com "_" na frente (_auto, das fotos baixadas sozinhas) não vira etiqueta
            folders = [tags_from_filename(part) for part in path.relative_to(self.root).parent.parts
                       if not part.startswith("_")]
            tags = list(dict.fromkeys([t for f in folders for t in f] + tags_from_filename(path.stem)))
            description = " ".join(tags)
            page_url = ""
            author = ""
            if sidecar.exists():
                try:
                    meta = json.loads(sidecar.read_text(encoding="utf-8"))
                    tags = list(dict.fromkeys([*meta.get("tags", []), *tags]))
                    description = meta.get("description", description)
                    page_url = meta.get("page_url", "")
                    author = meta.get("credit", "")  # autor e licença: vão para os créditos do report.json
                except (OSError, ValueError):
                    pass
            thumb = path if is_image else thumbs_dir / f"{hashlib.sha1(str(path).encode()).hexdigest()[:16]}.jpg"
            if not is_image:
                try:
                    extract_frame(path, thumb, at=max(0.0, info.duration * 0.35))
                except FFmpegError:
                    thumb = None  # type: ignore[assignment]
            cand = Candidate(
                provider=self.name,
                media_id=str(path.relative_to(self.root)),
                media_type=MediaType.IMAGE if is_image else MediaType.VIDEO,
                width=info.width,
                height=info.height,
                duration=None if is_image else info.duration,
                thumbnail_url=None,
                download_url=None,
                page_url=page_url,
                tags=tags,
                description=description,
                author=author,
                local_path=str(path),
                thumbnail_path=str(thumb) if thumb else None,
            )
            record = cand.to_dict()
            record.update({"thumbnail_path": cand.thumbnail_path, "_mtime": stat.st_mtime, "_size": stat.st_size,
                           "_side_mtime": side_mtime})
            entries.append(record)

        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps({"version": INDEX_VERSION, "entries": entries}, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
        result = []
        for record in entries:
            cand = Candidate.from_dict(record)
            cand.thumbnail_path = record.get("thumbnail_path")
            result.append(cand)
        log.info("biblioteca local indexada", extra={"stage": "library", "source": self.name, "items": len(result)})
        return result

    # --------------------------------------------------------------- search
    def search(self, query: str, media_type: MediaType, limit: int, language: str = "en") -> list[Candidate]:
        pool = [c for c in self.usable if c.media_type is media_type]
        if not pool:
            return []
        terms = keywords(query)
        lexical = [overlap_score(terms, keywords(" ".join(c.tags) + " " + c.description)) for c in pool]
        semantic = [0.0] * len(pool)
        if self.semantic is not None:
            try:
                semantic = self.semantic(query, pool)
            except Exception as exc:  # noqa: BLE001 - busca local nunca derruba o pipeline
                log.warning("busca semântica local falhou", extra={"stage": "library", "error": str(exc)})
        scored = []
        for cand, lex, sem in zip(pool, lexical, semantic):
            if lex <= 0 and sem < self.min_semantic:
                continue
            scored.append((0.5 * lex + sem, cand))
        scored.sort(key=lambda item: item[0], reverse=True)
        out = []
        for _, cand in scored[:limit]:
            copy = Candidate.from_dict(cand.to_dict())
            copy.thumbnail_path = cand.thumbnail_path
            copy.query = query
            out.append(copy)
        return out

    def marked_for(self, scene_text: str, section: str = "", query: str = "") -> list[Candidate]:
        """Os arquivos com ``say`` que casa com a fala da cena, achados ou não pelas buscas. No vídeo dos
        disjuntores a cena da fábrica da ABB já tinha vídeos (de moto) que bastavam, a biblioteca nunca era
        procurada por foto e a foto dos disjuntores ABB nem virava candidata."""
        out = []
        for cand in self.usable:
            if targeted(cand) and fits_scene(cand, scene_text, section):
                copy = Candidate.from_dict(cand.to_dict())
                copy.thumbnail_path = cand.thumbnail_path
                copy.query = query
                out.append(copy)
        return out

    def download(self, candidate: Candidate, dest_dir: Path) -> Path:
        if not candidate.local_path or not Path(candidate.local_path).exists():
            raise ProviderError(f"arquivo local sumiu: {candidate.local_path}")
        return Path(candidate.local_path)
