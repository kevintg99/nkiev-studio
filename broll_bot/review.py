"""Revisão visual dos b-rolls pelo Claude.

O CLIP mede se a imagem "parece" com a descrição, mas não sabe marca/modelo nem
contexto: para ele um Mercedes prata é um ótimo "sedan prata antigo". Aqui o
Claude olha as miniaturas de verdade (uma grade numerada por cena) junto com o
trecho narrado e aprova ou reprova cada candidato.

Dois modos:
- API (review.mode: api / --review): uma requisição para até 10 cenas, com cache
  por cena+clipe (rodar de novo não paga de novo).
- Chat (--export-review / --review-file): o bot salva as grades e um prompt;
  você anexa as imagens no claude.ai, salva a resposta e roda de novo.

Os vereditos ficam no cache e valem para as próximas execuções nos dois modos.
"""

from __future__ import annotations

import base64
import io
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from pydantic import BaseModel, Field, ValidationError

from .cache import SQLiteCache, make_key
from .config import AppConfig
from .log import get_logger
from .models import Candidate, Scene
from .motion.gfx import cover, font
from .ranking import ThumbnailFetcher
from .search import SceneSearchResult

log = get_logger("review")

CELL_W, CELL_H, COLS, HEADER = 384, 216, 4, 44
TTL = 90 * 86400

REVIEW_SYSTEM = """You are a demanding video editor checking stock b-roll for a narrated video.
For each scene you get the narration, the shot the viewer should see, and a contact sheet of numbered candidate thumbnails.
Approve a thumbnail only if a viewer hearing that narration would find it fitting. Reject it when:
- it shows a clearly different make, model or category than what the narration talks about (e.g. the narration is about a Toyota Corolla sedan and the image shows a Mercedes, a BMW, a Porsche, a sports car, an SUV or a pickup);
- it is off-topic for the sentence (unrelated people, plants, nature, objects, places);
- the main subject is something else than what is being said (engine talk but the image is a car exterior, headlight talk but it is a whole car far away);
- it contradicts the moment (old generation vs brand-new car, interior vs exterior, night vs day) when the narration is specific;
- it shows the part of a different kind of vehicle or device than the narration (a motorcycle battery, AA batteries or a phone battery when the narration is about a car's battery; a motorbike engine for a car engine);
- it has big visible text, watermarks or other brands' logos;
- it shows no vehicle and nothing automotive (a building, a street without cars, a landscape, a monument) in an automotive video, unless the narration of that scene talks about that thing (a bank, an office, insurers, an exhibition, a battery factory, people, a document): then the image must show THAT, and a car is not required;
- it shows a crashed, wrecked, burned or destroyed car, unless the narration of that scene talks about an accident or destruction.
Generic shots whose brand can't be identified are fine when the vehicle type and the context match.
The "suggested shot" is only an idea: never reject a thumbnail just because the setting, angle, lighting, weather
or time of day differs from it (a Corolla in a parking lot is fine for "a Corolla on a coastal road"). Judge the
thumbnail against the NARRATION and the main subject. When a scene has several acceptable candidates, approve all
of them, so the video has enough footage.
Judge only what you can see; if in doubt between two, approve the one closer to the narration.
Every number on a contact sheet must appear in exactly one list (approved or rejected)."""

CHAT_FORMAT = """Answer with ONLY a JSON object (no explanations, no markdown), exactly in this format:
{
  "scenes": [
    {"scene": 0, "approved": [1, 3], "rejected": [2, 4], "notes": "2 is a Mercedes, 4 shows plants"}
  ]
}"""


class _SceneVerdict(BaseModel):
    scene: int = Field(description="Scene number as given in the request")
    approved: list[int] = Field(description="Thumbnail numbers that fit the narration")
    rejected: list[int] = Field(description="Thumbnail numbers that do not fit")
    notes: str = Field(default="", description="Very short reason for the rejections")


class _ReviewOut(BaseModel):
    scenes: list[_SceneVerdict]


@dataclass
class SheetItem:
    scene: Scene
    candidates: list[Candidate]  # posição i -> número i + 1 na grade


class VisualReviewer:
    def __init__(self, cfg: AppConfig, cache: SQLiteCache, thumbs: ThumbnailFetcher | None) -> None:
        self.cfg = cfg
        self.cache = cache
        self.thumbs = thumbs
        self.notes: dict[int, str] = {}
        # o que aconteceu com a revisão (vai para o report e para o Diagnóstico do app)
        self.scenes_sent = 0
        self.verdicts = 0
        self.errors: list[str] = []
        # carro -> geração/anos falados ("3rd generation, 2005-2017"): reprova a geração errada
        self.car_detail: dict[str, str] = {}

    def context(self) -> str:
        """Nicho + assunto do vídeo: com isso a IA reprova carro de outra marca mesmo quando
        a frase da cena não cita o nome ("ele vai de zero a cem...")."""
        a = self.cfg.analysis
        text = f"Channel niche: {a.niche}."
        if a.sections_hint:
            text += (" This video ranks several car models, one per section; each scene below says which model it "
                     "is about. Reject vehicles that are clearly another brand or model than that scene's car, unless "
                     "the narration of that scene names the other car (a comparison).")
        elif a.subject:
            text += (f" Main subject of the video: {a.subject}. In scenes about it, reject vehicles that are "
                     f"clearly another brand, model or category (sports cars, luxury cars, SUVs, pickups), even "
                     f"when the sentence does not repeat the name.")
        from .subject import is_automotive

        if is_automotive(a.niche):
            text += (" The narration may be in Portuguese, French or Polish. Words have their automotive meaning "
                     "(also 'génération'/'generacja' = car generation, 'boîte'/'skrzynia' = gearbox, "
                     "'entretien'/'serwis' = car service): 'geração' is a generation of the CAR MODEL (reject "
                     "images of people or 'generation Z'), 'câmbio' is the gearbox (reject money), 'banco' is a "
                     "car seat (reject banks).")
        return text

    # ------------------------------------------------------------- cache
    def _key(self, scene: Scene, cand_key: str) -> str:
        # v4: o carro e a geração dele entram na pergunta (e na chave: outro carro, outro veredito)
        car = scene.analysis.subject
        return make_key("review-v4", scene.text.strip(), scene.analysis.visual_summary.strip(),
                        f"{car}|{self.car_detail.get(car, '')}", cand_key)

    def cached(self, scene: Scene, result: SceneSearchResult) -> dict[str, str]:
        out: dict[str, str] = {}
        for c in result.ranked:
            verdict = self.cache.get("review", self._key(scene, c.key))
            if verdict in ("approved", "rejected"):
                out[c.key] = verdict
        return out

    def _store(self, scene: Scene, verdicts: dict[str, str]) -> None:
        for key, verdict in verdicts.items():
            self.cache.set("review", self._key(scene, key), verdict, ttl_seconds=TTL)

    # ---------------------------------------------------------- escolha
    def pending(self, scenes: list[Scene], results: dict[int, SceneSearchResult],
                reviews: dict[int, dict[str, str]]) -> list[SheetItem]:
        """Cenas que ainda não têm aprovados suficientes, com os melhores candidatos
        ainda não revisados (até review.candidates_per_scene)."""
        k = self.cfg.review.candidates_per_scene
        items = []
        for scene in scenes:
            res = results.get(scene.index)
            if res is None or not res.ranked:
                continue
            verdicts = reviews.get(scene.index, {})
            approved = sum(verdicts.get(c.key) == "approved" for c in res.ranked)
            if approved >= max(1, res.needed):
                continue
            fresh = [c for c in res.ranked if c.key not in verdicts and not c.veto][: k * 2]
            if self.thumbs is not None:
                self.thumbs.fetch([c for c in fresh if not c.thumbnail_path])
            fresh = [c for c in fresh if c.thumbnail_path and Path(c.thumbnail_path).exists()][:k]
            if fresh:
                items.append(SheetItem(scene, fresh))
        return items

    # ------------------------------------------------------------ grades
    @staticmethod
    def contact_sheet(item: SheetItem) -> Image.Image:
        n = len(item.candidates)
        cols = max(1, min(COLS, n))
        rows = (n + cols - 1) // cols
        sheet = Image.new("RGB", (CELL_W * cols, HEADER + CELL_H * rows), (18, 18, 22))
        draw = ImageDraw.Draw(sheet)
        draw.text((12, 9), f"CENA {item.scene.index}", font=font("black", 24), fill=(255, 214, 0))
        badge = font("black", 30)
        for i, cand in enumerate(item.candidates):
            x, y = (i % cols) * CELL_W, HEADER + (i // cols) * CELL_H
            try:
                with Image.open(cand.thumbnail_path) as img:  # type: ignore[arg-type]
                    thumb = cover(img.convert("RGB"), CELL_W - 6, CELL_H - 6)
                sheet.paste(thumb, (x + 3, y + 3))
            except OSError:
                pass
            draw.rectangle((x + 3, y + 3, x + 53, y + 49), fill=(255, 214, 0))
            draw.text((x + 28, y + 26), str(i + 1), font=badge, fill=(0, 0, 0), anchor="mm")
        return sheet

    def scene_text(self, item: SheetItem) -> str:
        s = item.scene
        detail = self.car_detail.get(s.analysis.subject, "")
        return (f"Scene {s.index} — narration ({s.start:.1f}s-{s.end:.1f}s): \"{s.text.strip()}\"\n"
                + (f"Car model of this scene: {s.analysis.subject}" + (f" ({detail}; reject other generations)"
                                                                       if detail else "") + "\n"
                   if s.analysis.subject else "")
                + f"Suggested shot (just an idea, not a requirement): {s.analysis.visual_summary}\n"
                f"Thumbnails on the sheet: 1 to {len(item.candidates)}")

    # ---------------------------------------------------------- vereditos
    def apply(self, items: list[SheetItem], plan: _ReviewOut, reviews: dict[int, dict[str, str]]) -> int:
        """Traduz os números da resposta para chaves de candidato; guarda no cache."""
        by_scene = {it.scene.index: it for it in items}
        count = 0
        for sv in plan.scenes:
            item = by_scene.get(sv.scene)
            if item is None:
                continue
            verdicts: dict[str, str] = {}
            for numbers, verdict in ((sv.rejected, "rejected"), (sv.approved, "approved")):
                for n in numbers:
                    if 1 <= n <= len(item.candidates):
                        verdicts[item.candidates[n - 1].key] = verdict
            if sv.approved or sv.rejected:
                # número que a IA não citou não foi aprovado: não entra sem revisão
                for cand in item.candidates:
                    verdicts.setdefault(cand.key, "rejected")
            self._store(item.scene, verdicts)
            reviews.setdefault(sv.scene, {}).update(verdicts)
            if sv.notes:
                self.notes[sv.scene] = sv.notes
            count += len(verdicts)
            log.info("cena revisada", extra={"stage": "review", "scene": sv.scene,
                                              "approved": sum(v == "approved" for v in verdicts.values()),
                                              "rejected": sum(v == "rejected" for v in verdicts.values()),
                                              "notes": sv.notes})
        return count

    # --------------------------------------------------------------- API
    def review_api(self, items: list[SheetItem], reviews: dict[int, dict[str, str]]) -> int:
        self.scenes_sent += len(items)
        n = self._review_api(items, reviews)
        self.verdicts += n
        return n

    def status(self) -> dict[str, Any]:
        from .config import review_provider

        return {"mode": self.cfg.review.mode, "provider": review_provider(self.cfg), "scenes_sent": self.scenes_sent,
                "verdicts": self.verdicts, "errors": self.errors[-3:]}

    def _review_api(self, items: list[SheetItem], reviews: dict[int, dict[str, str]]) -> int:
        from .config import review_provider

        if review_provider(self.cfg) == "openai":
            return self.review_openai(items, reviews)
        try:
            import anthropic  # import tardio: só é necessário neste modo

            client = anthropic.Anthropic()
        except Exception as exc:  # noqa: BLE001 - sem chave/SDK: segue só com o CLIP
            log.warning("revisão visual pela API indisponível (ANTHROPIC_API_KEY no .env?); seguindo só com o CLIP",
                        extra={"stage": "review", "error": str(exc)})
            self.errors.append(f"Claude indisponível (chave ANTHROPIC_API_KEY?): {str(exc)[-200:]}")
            return 0
        rc = self.cfg.review
        total = 0
        for start in range(0, len(items), max(1, rc.scenes_per_request)):
            chunk = items[start:start + rc.scenes_per_request]
            content: list[dict[str, Any]] = [{"type": "text", "text": self.context()}]
            for item in chunk:
                content.append({"type": "text", "text": self.scene_text(item)})
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                            "data": _jpeg_b64(self.contact_sheet(item))}})
            content.append({"type": "text", "text": "Review every thumbnail of every scene above."})
            try:
                response = client.beta.messages.parse(
                    model=rc.model,
                    max_tokens=rc.max_tokens,
                    system=REVIEW_SYSTEM,
                    messages=[{"role": "user", "content": content}],
                    output_format=_ReviewOut,
                    output_config={"effort": rc.effort},
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
            except Exception as exc:  # noqa: BLE001 - API fora/sem chave nunca derruba o vídeo
                log.warning("revisão visual pela API falhou; seguindo só com o CLIP",
                            extra={"stage": "review", "error": str(exc)})
                self.errors.append(f"a API do Claude falhou: {str(exc)[-200:]}")
                return total
            from . import usage

            usage.from_anthropic(response, getattr(response, "model", None) or rc.model, "revisão das imagens")
            if response.stop_reason == "refusal" or response.parsed_output is None:
                log.warning("revisão visual sem resposta utilizável; seguindo só com o CLIP",
                            extra={"stage": "review", "stop_reason": response.stop_reason})
                self.errors.append(f"o Claude não devolveu avaliações ({response.stop_reason})")
                continue
            total += self.apply(chunk, response.parsed_output, reviews)
        return total

    def review_openai(self, items: list[SheetItem], reviews: dict[int, dict[str, str]],
                      client: Any = None) -> int:
        """Mesma revisão pela API da OpenAI (ChatGPT com visão): grades como imagens, resposta em JSON."""
        from .llm_openai import OpenAIError, OpenAIJSON, image_part, text_part

        rc = self.cfg.review
        try:
            llm = OpenAIJSON(rc.openai_model, rc.effort, rc.max_tokens, client=client, step="revisão das imagens")
        except Exception as exc:  # noqa: BLE001 - sem chave/SDK: segue só com o CLIP
            log.warning("revisão visual pela OpenAI indisponível (OPENAI_API_KEY no .env?); seguindo só com o CLIP",
                        extra={"stage": "review", "error": str(exc)})
            self.errors.append(f"OpenAI indisponível (chave OPENAI_API_KEY?): {str(exc)[-200:]}")
            return 0
        chunks = [items[i:i + rc.scenes_per_request] for i in range(0, len(items), max(1, rc.scenes_per_request))]

        def ask(chunk: list[SheetItem]) -> _ReviewOut | Exception:
            content: list[dict[str, Any]] = [text_part(self.context())]
            for item in chunk:
                content.append(text_part(self.scene_text(item)))
                content.append(image_part(self.contact_sheet(item)))
            content.append(text_part("Review every thumbnail of every scene above.\n" + CHAT_FORMAT))
            try:
                return _ReviewOut.model_validate(llm.complete(REVIEW_SYSTEM, content))
            except (OpenAIError, ValidationError) as exc:
                return exc

        # lotes em paralelo (4 por vez): um de cada vez, a 1ª rodada do vídeo das baterias levou 5 min
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(chunks)))) as pool:
            answers = list(pool.map(ask, chunks))
        total = 0
        for chunk, plan in zip(chunks, answers):
            if isinstance(plan, Exception):
                log.warning("revisão visual pela OpenAI falhou num lote; essas cenas seguem só com o CLIP",
                            extra={"stage": "review", "error": str(plan)[-300:]})
                self.errors.append(f"a API da OpenAI falhou: {str(plan)[-200:]}")
                continue
            total += self.apply(chunk, plan, reviews)
        return total

    # -------------------------------------------------------------- chat
    def export(self, items: list[SheetItem], out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        for old in out_dir.glob("cena_*.jpg"):
            old.unlink()
        mapping: dict[str, list[str]] = {}
        blocks = []
        for item in items:
            self.contact_sheet(item).save(out_dir / f"cena_{item.scene.index:02d}.jpg", quality=88)
            mapping[str(item.scene.index)] = [c.key for c in item.candidates]
            blocks.append(self.scene_text(item) + f"  (image: cena_{item.scene.index:02d}.jpg)")
        (out_dir / "mapa.json").write_text(json.dumps({"scenes": mapping}, indent=1), encoding="utf-8")
        prompt = (REVIEW_SYSTEM + f"\n\n{self.context()}\n"
                  f"I attached {len(items)} contact sheets (one image per scene, the scene number is written on top).\n\n"
                  + "\n\n".join(blocks) + "\n\n" + CHAT_FORMAT + "\n")
        prompt_path = out_dir / "prompt_revisao.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        return prompt_path

    def import_file(self, path: Path, scenes: list[Scene], reviews: dict[int, dict[str, str]]) -> int:
        """Lê a resposta do chat (JSON) usando o mapa salvo pelo --export-review."""
        map_path = path.parent / "mapa.json"
        if not map_path.exists():
            raise ReviewError(f"não achei {map_path}: salve a resposta na mesma pasta das imagens da revisão")
        mapping = json.loads(map_path.read_text(encoding="utf-8"))["scenes"]
        text = path.read_text(encoding="utf-8")
        start, end = text.find("{"), text.rfind("}")
        try:
            plan = _ReviewOut.model_validate(json.loads(text[start:end + 1]))
        except (ValueError, ValidationError) as exc:
            raise ReviewError(f"{path.name} não é uma resposta de revisão válida ({exc.__class__.__name__}); "
                              "cole só o JSON da resposta do Claude") from exc
        scene_map = {s.index: s for s in scenes}
        items = []
        for idx, keys in mapping.items():
            scene = scene_map.get(int(idx))
            if scene is not None:
                # candidatos "fantasmas" só com a chave: é o que apply() precisa
                items.append(SheetItem(scene, [_KeyOnly(k) for k in keys]))  # type: ignore[misc]
        return self.apply(items, plan, reviews)


class _KeyOnly:
    def __init__(self, key: str) -> None:
        self.key = key


class ReviewError(RuntimeError):
    pass


def _jpeg_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")
