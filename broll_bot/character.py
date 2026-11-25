"""Imagens do personagem do canal (o mecânico) em cena, geradas pela OpenAI a partir da imagem dele.

Logo depois de cada aparição do avatar e em momentos fortes do roteiro ("a correia se desfaz", "o motor quebra",
"a conta passa de £2.000"), entra uma foto do MESMO personagem fazendo o que a fala diz: segurando a peça,
apontando para o motor, olhando a conta. Entra como um b-roll normal (com o zoom suave das fotos).

A imagem de referência é a do avatar do canal (``avatar.photo``): cada canal tem as imagens do seu personagem.
As imagens ficam no cache (``.cache/personagem``): refazer o vídeo não gasta de novo. Se a OpenAI falhar, o trecho
fica com o b-roll que já tinha (nunca derruba o vídeo).

Biblioteca do canal (``canais/personagem/<canal>/``): toda imagem gerada fica guardada com a fala que ela ilustra.
Nos próximos vídeos do canal, um momento parecido com uma imagem já feita ("olhando a conta", "apontando o motor")
reaproveita a imagem em vez de gerar outra; com a biblioteca cheia (``library_max``) não gera mais nada, só
reaproveita (as menos usadas primeiro). Cada canal tem a sua (o personagem é outro); trocar a imagem do personagem
começa uma biblioteca nova. Dá para apagar da pasta a imagem que não ficou boa.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import usage
from .config import CharacterConfig
from .log import get_logger
from .models import Candidate, MediaType, Sentence, Shot
from .rhythm import is_cta

log = get_logger("character")

LIBRARY_DIR = "canais/personagem"  # biblioteca de imagens do personagem de cada canal (``CharacterLibrary``)

# US$ por imagem 1536x1024 (tabela da OpenAI; muda em costs.images do config.yaml)
IMAGE_PRICES: dict[str, float] = {
    "gpt-image-1/low": 0.016, "gpt-image-1/medium": 0.063, "gpt-image-1/high": 0.25,
    "gpt-image-1-mini/low": 0.006, "gpt-image-1-mini/medium": 0.015, "gpt-image-1-mini/high": 0.052,
}

# momento forte: o motor/a peça quebrando (peso 3) ou palavra de prejuízo mais fraca (peso 1)
_HARD = re.compile(
    r"\b(?:destroy\w*|wreck\w*|snap(?:s|ped)?|starv\w*|seiz\w*|blow(?:s|n)?\s+up|scrap\b|dead\b|death\b|"
    r"catastroph\w*|warning\s+light|too\s+late|shed(?:s|ding)?\s+rubber|crash\w*|skid\w*|slid(?:e|es|ing)\b|"
    r"ditch\b|collision\w*|rear-?end\w*|get\s+hurt|accidents?\b|fire\s+risk|battery\s+fire|caught\s+fire|regret\w*|frozen\b|ices?\s+over|"
    r"cass[ée]e?s?\b|casse\b|d[ée]truit\w*|(?<!point\s)mort\b|catastroph\w*|trop\s+tard|"
    r"quebr\w*|destr[óo]i\w*|morre\w*|tarde\s+demais)", re.I)
_SOFT = re.compile(r"\b(?:fail(?:s|ed|ure|ures)?|nightmare|horror|disaster\w*|ruin\w*|broke(?:n)?\b|"
                   r"panne\w*|l[âa]che\w*|cauchemar|ruine\w*|estrag\w*|pesadelo)\b", re.I)
_MONEY = re.compile(r"(?:£|€|\$|R\$)\s?\d|\b\d[\d,. ]*\s?(?:pounds?|euros?|dollars?|reais)\b|"
                    r"\b(?:hundred|thousand)\s+(?:pounds|euros|dollars)\b|\bmille\s+euros\b", re.I)
_STAY = re.compile(r"\b(?:stick\s+around|stay\s+with\s+me|keep\s+watching|restez\s+avec\s+moi|fica\s+comigo|"
                   r"continue\s+assistindo)\b", re.I)
_LIST = re.compile(r"^\s*(?:and\s+|et\s+|e\s+)?(?:number|num[ée]ro|n[uú]mero)\b", re.I)

PROMPT = ("Use the person in the reference image as the same character: same face, hair, beard, skin tone, "
          "clothes and build. Photorealistic cinematic still, 16:9, natural workshop or roadside lighting, shallow "
          "depth of field, he is the main subject. Illustrate this moment of a car video (the narration may be in "
          "English or French): \"{text}\". {topic}Show him acting it out with a clear expression (holding or pointing "
          "at the relevant car part, inspecting the engine, looking at an invoice, reacting to the problem). "
          "No text, no captions, no logos, no watermarks.")
# vídeo de outro nicho (o diretor de IA disse "personal finance", "cooking"...): cenário e gestos do assunto
PROMPT_NICHE = ("Use the person in the reference image as the same character: same face, hair, beard, skin tone, "
                "clothes and build. Photorealistic cinematic still, 16:9, natural lighting in a setting that fits "
                "the topic, shallow depth of field, he is the main subject. Illustrate this moment of a video about "
                "{niche} (the narration may be in another language): \"{text}\". {topic}Show him acting it out with "
                "a clear expression (holding or pointing at the thing being discussed, reacting to it). "
                "No text, no captions, no logos, no watermarks.")


def prompt_for(text: str, topic: str, niche: str = "") -> str:
    from .subject import is_automotive

    if not niche or is_automotive(niche):
        return PROMPT.format(text=text[:400], topic=f"The car or engine being discussed: {topic}. " if topic else "")
    return PROMPT_NICHE.format(niche=niche, text=text[:400], topic=f"The subject being discussed: {topic}. "
                               if topic else "")


@dataclass
class CharacterMoment:
    kind: str  # "after_avatar" | "strong"
    at: float  # segundo do vídeo onde a imagem entra
    text: str  # a fala daquele momento (vira a cena da imagem)
    topic: str = ""  # carro/motor da seção ("Ford 1.0 EcoBoost")
    path: str = ""
    status: str = "pending"  # pending | ok | cache | reused | failed | timeout | unused
    error: str = ""
    usd: float = 0.0
    shot: int = -1


# ------------------------------------------------------------------ momentos
def _spoken(sentences: list[Sentence]) -> list[Sentence]:
    return [s for s in sentences if s.end > s.start]


def strong_score(text: str) -> int:
    return 3 * len(_HARD.findall(text)) + len(_SOFT.findall(text)) + (1 if _MONEY.search(text) else 0)


def pick_moments(sentences: list[Sentence], avatar_windows: list[tuple[float, float, str]], cfg: CharacterConfig,
                 subjects: list[str] | None = None) -> list[CharacterMoment]:
    """Depois de cada aparição do avatar (menos a do fim) e nos momentos fortes, espaçados."""
    spoken = _spoken(sentences)
    subj = {id(s): (subjects[k] if subjects and k < len(subjects) else "") for k, s in enumerate(sentences)}
    out: list[CharacterMoment] = []
    end = spoken[-1].end if spoken else 0.0
    near_avatar = lambda t, pad: any(a - pad <= t <= b + pad for a, b, _ in avatar_windows)  # noqa: E731
    if cfg.after_avatar:
        for a, b, moment in avatar_windows:
            if moment == "outro" or b > end - 8:
                continue
            nxt = [s for s in spoken if s.start >= b - 0.3]
            if not nxt:
                continue
            first = nxt[0]
            text = first.text if len(first.text.split()) >= 6 or len(nxt) < 2 else f"{first.text} {nxt[1].text}"
            out.append(CharacterMoment("after_avatar", max(first.start, b), text, subj.get(id(first), "")))
    if cfg.strong_max > 0:
        cands = []
        for s in spoken:
            score = strong_score(s.text)
            if score < 3 or len(s.text.split()) < 6 or is_cta(s.text) or _LIST.match(s.text) or _STAY.search(s.text):
                continue
            if s.start < 20 or s.end > end - 15 or near_avatar(s.start, 8.0):
                continue
            cands.append((score, s))
        cands.sort(key=lambda x: (-x[0], x[1].start))
        taken = [m.at for m in out]
        strong: list[CharacterMoment] = []
        for score, s in cands:
            if len(strong) >= cfg.strong_max:
                break
            if any(abs(s.start - t) < cfg.min_gap for t in taken):
                continue
            strong.append(CharacterMoment("strong", s.start, s.text, subj.get(id(s), "")))
            taken.append(s.start)
        out += strong
    return sorted(out, key=lambda m: m.at)


# ------------------------------------------------------------------ geração
def image_key(ref_digest: str, prompt: str, cfg: CharacterConfig) -> str:
    raw = "|".join((ref_digest, prompt, cfg.model, cfg.quality, cfg.size, "v1"))
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def image_price(cfg: CharacterConfig, prices: dict[str, float] | None = None) -> float:
    table = {**IMAGE_PRICES, **{k: float(v) for k, v in (prices or {}).items()}}
    return table.get(f"{cfg.model}/{cfg.quality}", table.get(f"gpt-image-1/{cfg.quality}", 0.063))


class OpenAIImages:
    """Edição de imagem com referência (images.edit): o personagem igual, numa cena nova."""

    def __init__(self, client: Any = None) -> None:
        if client is None:
            import openai

            client = openai.OpenAI()
        self.client = client

    def make(self, ref: Path, prompt: str, cfg: CharacterConfig, dest: Path) -> Path:
        kwargs: dict[str, Any] = {"model": cfg.model, "prompt": prompt, "size": cfg.size, "quality": cfg.quality,
                                  "n": 1}
        with open(ref, "rb") as fh:
            try:
                resp = self.client.images.edit(image=fh, input_fidelity="high", **kwargs)  # rosto mais fiel
            except TypeError:
                fh.seek(0)
                resp = self.client.images.edit(image=fh, **kwargs)
        data = resp.data[0]
        raw = base64.b64decode(data.b64_json) if getattr(data, "b64_json", None) else None
        if raw is None and getattr(data, "url", None):
            import requests

            raw = requests.get(data.url, timeout=60).content
        if not raw:
            raise RuntimeError("a OpenAI não devolveu a imagem")
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(raw)
        tmp.replace(dest)
        return dest


# ------------------------------------------------------------------ biblioteca do canal
_STOP = set("""about above after again against also because been before being below between both could does doing
down during each from further have having here into itself just more most much must only other over same should some
such than that their them then there these they this those through under until very what when where which while
will with would your yours you're it's that's there's here's i've i'm don't doesn't won't can't avec dans pour
plus mais elle sont cette tout tous fait faire comme leur nous vous votre être avoir""".split())


def keywords(text: str) -> set[str]:
    words = re.findall(r"[a-zà-ÿ][a-zà-ÿ'’-]{3,}", (text or "").lower())
    return {w.rstrip("s") for w in words if w not in _STOP}


class CharacterLibrary:
    """As imagens já geradas do personagem de um canal (``canais/personagem/<canal>/``, índice em
    ``imagens.json``). Só valem as feitas com a imagem de referência atual (``digest``)."""

    INDEX = "imagens.json"

    def __init__(self, folder: Path, digest: str) -> None:
        self.folder = Path(folder)
        self.digest = digest
        try:
            self.entries: list[dict[str, Any]] = json.loads((self.folder / self.INDEX).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.entries = []

    def usable(self) -> list[dict[str, Any]]:
        """As do personagem atual que ainda estão na pasta (a imagem apagada na mão sai da lista)."""
        return [e for e in self.entries if e.get("digest") == self.digest and (self.folder / e.get("file", "")).is_file()]

    def pick(self, text: str, taken: set[str], cfg: CharacterConfig) -> Path | None:
        """A imagem que melhor ilustra ``text`` (palavras em comum com a fala dela). Só reaproveita se parecer
        o bastante (``reuse_match``) ou se a biblioteca já está cheia (aí a mais parecida, menos usada)."""
        usable = self.usable()
        free = [e for e in usable if e["file"] not in taken]
        if not free:
            return None
        want = keywords(text)

        def score(e: dict[str, Any]) -> float:
            return len(want & keywords(e.get("text", ""))) / max(1, len(want))

        best = max(free, key=lambda e: (round(score(e), 2), -int(e.get("uses", 0)), -float(e.get("last", 0))))
        if score(best) >= cfg.reuse_match or len(usable) >= cfg.library_max:
            return self.folder / best["file"]
        return None

    def used(self, path: Path) -> None:
        for e in self.entries:
            if e.get("file") == path.name:
                e["uses"], e["last"] = int(e.get("uses", 0)) + 1, round(time.time())

    def add(self, src: Path, text: str, kind: str) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        n = 1 + max([int(m.group(1)) for e in self.entries if (m := re.search(r"(\d+)", e.get("file", "")))] or [0])
        dest = self.folder / f"{n:03d}_{kind}.png"
        shutil.copyfile(src, dest)
        self.entries.append({"file": dest.name, "text": text[:300], "kind": kind, "digest": self.digest,
                             "created": round(time.time()), "uses": 1, "last": round(time.time())})
        return dest

    def save(self) -> None:
        if self.entries:
            self.folder.mkdir(parents=True, exist_ok=True)
            (self.folder / self.INDEX).write_text(json.dumps(self.entries, ensure_ascii=False, indent=1),
                                                  encoding="utf-8")


class CharacterJob:
    """Gera as imagens em paralelo com o resto do pipeline (como o avatar)."""

    def __init__(self, cfg: CharacterConfig, ref: Path, moments: list[CharacterMoment], cache_dir: Path,
                 maker: Any = None, prices: dict[str, float] | None = None, library_dir: Path | None = None) -> None:
        self.cfg = cfg
        self.ref = ref
        self.moments = moments
        self.cache_dir = cache_dir
        self.maker = maker
        self.prices = prices or {}
        self.library_dir = library_dir
        self.niche = ""  # nicho do vídeo (diretor de IA): muda o cenário das imagens fora do nicho de carros
        self._thread: threading.Thread | None = None
        self._deadline = 0.0

    def start(self) -> "CharacterJob":
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._deadline = time.monotonic() + self.cfg.wait_minutes * 60
        self._thread = threading.Thread(target=self._run, name="personagem", daemon=True)
        self._thread.start()
        log.info("imagens do personagem: gerando em segundo plano",
                 extra={"stage": "character", "momentos": [f"{m.kind} {m.at:.0f}s" for m in self.moments]})
        return self

    def _run(self) -> None:
        digest = hashlib.sha1(self.ref.read_bytes()).hexdigest()[:16]
        lib = CharacterLibrary(self.library_dir, digest) if self.library_dir and self.cfg.reuse else None
        todo: list[CharacterMoment] = []
        taken: set[str] = set()
        for m in self.moments:  # primeiro o que a biblioteca do canal já tem (de graça)
            hit = lib.pick(f"{m.text} {m.topic}", taken, self.cfg) if lib else None
            if hit is not None:
                m.path, m.status = str(hit), "reused"
                taken.add(hit.name)
                lib.used(hit)
            else:
                todo.append(m)
        try:
            if todo and self.maker is None:
                self.maker = OpenAIImages()
        except Exception as exc:  # noqa: BLE001 - sem o pacote/chave da OpenAI
            for m in todo:
                m.status, m.error = "failed", f"OpenAI indisponível: {str(exc)[-200:]}"
            todo = []
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda m: self._one(m, digest), todo))
        if lib is not None:
            for m in todo:
                if m.status in ("ok", "cache") and m.path:
                    try:
                        lib.add(Path(m.path), m.text, m.kind)
                    except OSError as exc:
                        log.warning("imagem do personagem não foi para a biblioteca do canal",
                                    extra={"stage": "character", "motivo": str(exc)})
            lib.save()

    def _one(self, m: CharacterMoment, digest: str) -> None:
        prompt = prompt_for(m.text, m.topic, self.niche)
        dest = self.cache_dir / f"{image_key(digest, prompt, self.cfg)}.png"
        if dest.is_file() and dest.stat().st_size > 0:
            m.path, m.status = str(dest), "cache"
            return
        try:
            self.maker.make(self.ref, prompt, self.cfg, dest)
            m.path, m.status = str(dest), "ok"
            m.usd = round(image_price(self.cfg, self.prices), 4)
            usage.charge("imagens do personagem", m.usd, f"OpenAI {self.cfg.model} · {self.cfg.quality}",
                         f"{m.kind} {m.at:.0f}s")
        except Exception as exc:  # noqa: BLE001 - a imagem é um extra
            m.status, m.error = "failed", str(exc)[-300:]

    def result(self) -> list[CharacterMoment]:
        if self._thread is not None:
            self._thread.join(max(0.0, self._deadline - time.monotonic()) + 10)
            if self._thread.is_alive():
                for m in self.moments:
                    if m.status == "pending":
                        m.status, m.error = "timeout", "a OpenAI ainda não terminou"
        for m in self.moments:
            if m.status in ("failed", "timeout"):
                log.warning("imagem do personagem não saiu", extra={"stage": "character", "momento": m.kind,
                                                                   "em": round(m.at), "motivo": m.error})
        return [m for m in self.moments if m.path and m.status in ("ok", "cache", "reused")]

    def report(self) -> list[dict[str, Any]]:
        out = []
        for m in self.moments:
            d = asdict(m)
            d.pop("path", None)
            d["at"] = round(m.at, 2)
            d["text"] = m.text[:120]
            out.append(d)
        return out


def start_character(cfg: Any, sentences: list[Sentence], avatar_windows: list[tuple[float, float, str]],
                    cache_dir: Path, base_dir: Path, subjects: list[str] | None = None,
                    maker: Any = None) -> CharacterJob | None:
    """Liga quando o canal tem a imagem do personagem (a do avatar) e o ``.env`` tem a chave da OpenAI."""
    ch: CharacterConfig = cfg.character
    if not ch.enabled or not cfg.avatar.photo:
        return None
    ref = Path(cfg.avatar.photo)
    ref = ref if ref.is_absolute() else base_dir / ref
    if not ref.is_file():
        return None
    if maker is None and not os.getenv("OPENAI_API_KEY"):
        log.warning("imagens do personagem: falta a OPENAI_API_KEY (Configurações)", extra={"stage": "character"})
        return None
    moments = pick_moments(sentences, avatar_windows, ch, subjects)
    if not moments:
        return None
    slug = cfg.channel.slug or ref.stem  # cada canal com a sua biblioteca (o personagem é outro)
    job = CharacterJob(ch, ref, moments, cache_dir / "personagem", maker=maker, prices=cfg.costs.images,
                       library_dir=base_dir / LIBRARY_DIR / slug)
    job.niche = cfg.analysis.niche
    return job.start()


# ------------------------------------------------------------------ montagem
def place(shots: list[Shot], moments: list[CharacterMoment], fps: int, seed: int = 0) -> int:
    """Cada imagem entra no shot de b-roll onde o momento cai (ou no seguinte): sem template, sem avatar."""
    from .framing import choose_layout

    used: set[int] = set()
    placed = 0
    for m in moments:
        target = None
        for shot in shots:
            t0, t1 = shot.start_frame / fps, shot.end_frame / fps
            if t1 <= m.at - 0.2 or shot.index in used or shot.template or shot.avatar:
                continue
            if t0 > m.at + 12:  # pula shots com avatar/template, mas não vai longe da fala
                break
            target = shot
            break
        if target is None:
            m.status = "unused"
            continue
        cand = Candidate("character", Path(m.path).stem, MediaType.IMAGE, 1536, 1024, None, None, None,
                         local_path=m.path, query=f"personagem: {m.kind}", description=m.text[:80])
        cand.review = "approved"
        target.candidate, target.media_path, target.fallback_reason = cand, m.path, None
        rng = random.Random(seed * 7919 + target.index)
        target.layout = choose_layout(MediaType.IMAGE, cand.aspect, {}, {"kenburns": 1.0}, rng, None)
        used.add(target.index)
        m.shot = target.index
        placed += 1
    return placed
