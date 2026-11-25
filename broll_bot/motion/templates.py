"""Templates de motion graphics. Cada um desenha os frames de um shot inteiro.

* ``comparison`` — quadro comparativo: 2–3 cards inclinados com sombra, linha do
  tempo que se desenha e etiquetas com a fala (fundo claro ou blueprint escuro).
* ``mosaic``     — parede 3×3 com tom azul, um quadro em destaque e palavra-chave.
* ``hud``        — imagem revelada sobre grade escura, linha de scan e marcação HUD.
* ``card``       — card com moldura branca e brilho neon, aberração cromática e glitch.
* ``vhs``        — filmadora antiga: PLAY, SP, timecode, granulado e tracking.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from . import gfx
from .sources import FrameSource, open_source


@dataclass
class MediaRef:
    path: str
    duration: float | None = None
    offset: float = 0.0
    speed: float = 1.0


@dataclass
class TemplateSpec:
    kind: str
    theme: str
    w: int
    h: int
    fps: int
    frames: int
    seed: int = 0
    media: list[MediaRef] = field(default_factory=list)
    tiles: list[str] = field(default_factory=list)
    captions: list[tuple[float, float, str]] = field(default_factory=list)  # (início, fim, texto) em s
    keyword: str = ""
    show_captions: bool = True
    # dados dos templates automotivos (ver scenes._SceneOut)
    subtitle: str = ""
    labels: list[str] = field(default_factory=list)
    stats: list[dict[str, Any]] = field(default_factory=list)
    number: dict[str, Any] = field(default_factory=dict)
    points: list[dict[str, Any]] = field(default_factory=list)
    rank: int = 0
    index: int = 0  # ex.: número do capítulo
    notes: list[str] = field(default_factory=list)  # linha do tempo: detalhe de cada item ("E10", "1ª geração")
    body: str = ""  # texto longo: manchete, comentário
    lang: str = "pt"  # idioma dos rótulos fixos (ver motion/i18n.py)
    skin: str = ""  # cara dos templates de retenção conforme o estilo do vídeo (templates_magnet.py)
    elements: list[dict[str, Any]] = field(default_factory=list)  # diagram: o esquema desenhado (templates_docu.py)
    tone: str = ""  # "news": a fala é notícia/reportagem (Broadcast: "BREAKING NEWS", "AO VIVO"); "": análise

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TemplateSpec":
        from ..spoken import screen_text

        data = dict(d)
        data["media"] = [MediaRef(**m) for m in d.get("media", [])]
        data["captions"] = [tuple(c) for c in d.get("captions", [])]
        # texto tirado da fala da voz de IA: "seventy-six point four per cent" vai para a tela como "76.4%"
        lang = str(data.get("lang") or "pt")
        for key in ("keyword", "subtitle", "body"):
            if isinstance(data.get(key), str):
                data[key] = screen_text(data[key], lang)
        for key in ("labels", "notes"):
            if isinstance(data.get(key), list):
                data[key] = [screen_text(x, lang) if isinstance(x, str) else x for x in data[key]]
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


VERTICAL_U = 1.15  # escala dos templates numa tela em pé de 1080 de largura


class Template:
    def __init__(self, spec: TemplateSpec) -> None:
        self.s = spec
        self.w, self.h, self.fps = spec.w, spec.h, spec.fps
        self.u = spec.h / 1080  # unidade: tudo escala com a resolução
        if spec.h > spec.w:  # tela em pé: pela altura a letra ficaria larga demais para a tela estreita
            self.u = spec.w / 1080 * VERTICAL_U
        self.rng = np.random.default_rng(spec.seed)
        self.th = gfx.THEMES[spec.theme]
        self.sources: list[FrameSource] = []
        self.render_size = (spec.w, spec.h)  # templates podem desenhar menor e o FFmpeg amplia
        gfx.set_number_locale(spec.lang)

    def src(self, ref: MediaRef, w: int, h: int, **kw: Any) -> FrameSource:
        s = open_source(ref.path, w, h, self.fps, self.s.frames, offset=ref.offset, duration=ref.duration,
                        speed=ref.speed, **kw)
        self.sources.append(s)
        return s

    def caption_at(self, t: float) -> tuple[int, str]:
        idx, text = -1, ""
        for i, (t0, _t1, txt) in enumerate(self.s.captions):
            if t >= t0 - 0.05:
                idx, text = i, txt
        return idx, text

    def frame(self, i: int) -> Image.Image:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:
        for s in self.sources:
            s.close()


# --------------------------------------------------------------- comparison
class Comparison(Template):
    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.n = max(2, min(3, len(spec.media)))
        self.cell = round(64 * u)
        self.bg = gfx.background(spec.theme, w, h, self.cell, extra=self.cell).convert("RGBA")
        cw = round(w * (0.235 if self.n == 3 else 0.32))
        ch = round(cw * 0.62)
        self.border = max(4, round(9 * u))
        xs = [0.2, 0.5, 0.8] if self.n == 3 else [0.3, 0.7]
        self.centers = [(w * x, h * 0.47) for x in xs]
        self.angles = [float(self.rng.uniform(1.2, 3.0)) * (1 if k % 2 else -1) for k in range(self.n)]
        media = (spec.media * 3)[: self.n]
        self.cards = [self.src(m, cw - 2 * self.border, ch - 2 * self.border, zoom=1.1,
                               pan=(float(self.rng.uniform(-1, 1)), 0.0)) for m in media]
        self.ch = ch
        total = spec.frames / self.fps
        caps = list(spec.captions)[: self.n]
        while len(caps) < self.n:  # sem fala suficiente: divide o tempo igualmente
            k = len(caps)
            caps.append((total * k / self.n, total * (k + 1) / self.n, ""))
        self.slots = [max(0.0, c[0]) for c in caps]
        self.slots[0] = 0.0
        self.caps = caps
        self.line_y = self.centers[0][1] + ch / 2 + 0.065 * h
        chip_font = gfx.font("bold", round(29 * u))
        self.chips = [
            gfx.chip(txt, chip_font, self.th["accent"], self.th["chip_text"], round(w * 0.3), round(14 * u), round(9 * u))
            if txt and spec.show_captions else None for _, _, txt in caps
        ]
        tag_font = gfx.font("black", round(30 * u))
        self.tags = [gfx.chip(lb.upper(), tag_font, (18, 18, 20), (255, 255, 255), round(w * 0.2), round(14 * u), round(8 * u))
                     if lb else None for lb in (spec.labels + [""] * 3)[: self.n]]
        title_font = gfx.font("black", round(88 * u))
        shadow = 3 if spec.theme != "paper" else 0
        self.titles = [
            gfx.text_block(gfx.wrap(txt, title_font, round(w * 0.84)), title_font, self.th["text"], shadow=shadow)
            if txt and spec.show_captions else None for _, _, txt in caps
        ]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = gfx.drift(self.bg, t, w, h, self.cell, speed=8 * u)
        d = ImageDraw.Draw(canvas)
        accent = self.th["accent"]

        # linha do tempo: vai do 1º ponto até o ponto do card mais recente
        visible = [k for k in range(self.n) if t >= self.slots[k]]
        head = visible[-1] if visible else 0
        x0 = self.centers[0][0]
        if head > 0:
            prev_x, next_x = self.centers[head - 1][0], self.centers[head][0]
            p = gfx.ease_in_out(gfx.progress(t, self.slots[head], 0.7))
            x_end = prev_x + (next_x - prev_x) * p
        else:
            x_end = x0
        lw = max(3, round(6 * u))
        if x_end > x0 + 1:
            d.line([(x0, self.line_y), (x_end, self.line_y)], fill=(*accent, 255), width=lw)
        for k in range(self.n):
            p = gfx.ease_out_back(gfx.progress(t, self.slots[k], 0.35))
            r = 13 * u * p
            if r > 0.5:
                cx = self.centers[k][0]
                d.ellipse([cx - r, self.line_y - r, cx + r, self.line_y + r], fill=(*accent, 255))

        # cards: entram subindo, girando e com leve "overshoot"
        for k in visible:
            p = gfx.progress(t, self.slots[k], 0.55)
            e = gfx.ease_out_back(p)
            content = self.cards[k].frame(i)
            card = gfx.bordered(content, self.border, self.th["border"])
            card = gfx.scaled(card, 0.82 + 0.18 * e)
            angle = self.angles[k] * (1 + 2.5 * (1 - e))
            layer = gfx.with_shadow(card, angle, round(14 * u), (round(6 * u), round(12 * u)), self.th["shadow"])
            cx, cy = self.centers[k]
            gfx.paste_center(canvas, layer, cx, cy + (1 - gfx.ease_out_cubic(p)) * 70 * u,
                             alpha=gfx.ease_out_cubic(p * 1.8))
            tag = self.tags[k]
            if tag is not None:  # rótulo do item (ex.: modelo ou ano) no canto do card
                q = gfx.ease_out_back(gfx.progress(t, self.slots[k] + 0.25, 0.3))
                gfx.paste_center(canvas, gfx.scaled(tag, max(0.01, q)), cx - self.ch * 0.55, cy - self.ch / 2 - 6 * u)

        # etiquetas com a fala de cada trecho
        for k in visible:
            chip = self.chips[k]
            if chip is None:
                continue
            p = gfx.progress(t, self.slots[k] + 0.15, 0.35)
            x = min(self.centers[k][0] - 0.035 * w, w - chip.width - 0.03 * w)
            x = max(0.03 * w, x)
            gfx.paste_at(canvas, gfx.scaled(chip, 0.9 + 0.1 * gfx.ease_out_back(p)),
                         x, self.line_y + 0.03 * h + (1 - gfx.ease_out_cubic(p)) * 18 * u, alpha=p)

        # título: a frase que está sendo falada agora
        idx = max(visible) if visible else 0
        title = self.titles[idx]
        if title is not None:
            p = gfx.ease_out_cubic(gfx.progress(t, self.slots[idx], 0.3))
            gfx.paste_at(canvas, title, 0.07 * w, 0.055 * h + (1 - p) * 26 * u, alpha=p)
        return canvas


# ------------------------------------------------------------------ mosaic
class Mosaic(Template):
    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.over = 1.1  # o mosaico é maior que a tela; a câmera se aproxima devagar
        W, H = round(w * self.over), round(h * self.over)
        gap = round(22 * u)
        self.tw, self.th_ = (W - 4 * gap) // 3, (H - 4 * gap) // 3
        self.rects = [(gap + c * (self.tw + gap), gap + r * (self.th_ + gap)) for r in range(3) for c in range(3)]
        tiles = [p for p in spec.tiles if p] or [m.path for m in spec.media]
        tiles = (tiles * 9)[:9]
        base = Image.new("RGB", (W, H), (6, 10, 30))
        tint = np.array([22, 40, 125], np.float32)
        self.plain: list[Image.Image] = []
        for k, (x, y) in enumerate(self.rects):
            try:
                with Image.open(tiles[k]) as im:
                    tile = gfx.cover(im.convert("RGB"), self.tw, self.th_)
            except (OSError, ValueError):
                tile = Image.new("RGB", (self.tw, self.th_), (30, 40, 90))
            self.plain.append(tile)
            arr = np.asarray(tile, np.float32) * 0.42 + tint * 0.58 * 0.75
            base.paste(Image.fromarray(arr.clip(0, 255).astype(np.uint8)), (x, y))
        # grade fina por cima de tudo, como no exemplo
        grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(grid)
        cell = round(58 * u)
        for x in range(0, W, cell):
            d.line([(x, 0), (x, H)], fill=(200, 215, 255, 38), width=1)
        for y in range(0, H, cell):
            d.line([(0, y), (W, y)], fill=(200, 215, 255, 38), width=1)
        self.base = Image.alpha_composite(base.convert("RGBA"), grid)
        self.live = self.src(spec.media[0], self.tw, self.th_, zoom=1.12) if spec.media else None
        order = [4] + [int(k) for k in self.rng.permutation([0, 1, 2, 3, 5, 6, 7, 8])]
        self.order = order
        self.hold = 1.35
        kw = spec.keyword if spec.show_captions else ""
        f = gfx.font("black", round(136 * u))
        self.kw = gfx.text_block(gfx.wrap(kw, f, round(w * 0.8)), f, (245, 245, 245), shadow=5, shadow_blur=10) if kw else None

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = self.base.copy()
        step = int(t // self.hold)
        k = self.order[step % len(self.order)]
        x, y = self.rects[k]
        tile = self.live.frame(i) if (self.live is not None and k == 4) else self.plain[k]
        p = gfx.ease_out_back(gfx.progress(t, step * self.hold, 0.35))
        card = gfx.bordered(tile, max(3, round(5 * u)), (214, 243, 74))
        card = gfx.scaled(card, 0.96 + 0.06 * p)
        layer = gfx.with_shadow(card, 0, round(18 * u), (0, round(8 * u)), 200)
        gfx.paste_center(canvas, layer, x + self.tw / 2, y + self.th_ / 2)
        # câmera: zoom lento e contínuo
        z = 1 + (self.over - 1.0) * gfx.ease_in_out(i / max(1, self.s.frames - 1)) * 0.6
        cw, ch = canvas.width / z, canvas.height / z
        cx, cy = canvas.width / 2, canvas.height / 2
        frame = canvas.convert("RGB").resize((w, h), Image.BILINEAR, box=(cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2))
        if self.kw is not None:
            q = gfx.ease_out_cubic(gfx.progress(t, 0.3, 0.45))
            out = frame.convert("RGBA")
            gfx.paste_at(out, self.kw, 0.09 * w - (1 - q) * 50 * u, h * 0.83 - self.kw.height, alpha=q)
            frame = out.convert("RGB")
        return frame


# --------------------------------------------------------------------- hud
class Hud(Template):
    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.cell = round(76 * u)
        self.bg = gfx.background(spec.theme, w, h, self.cell, extra=self.cell).convert("RGBA")
        self.iw, self.ih = round(w * 0.76), round(h * 0.74)
        self.ix, self.iy = (w - self.iw) // 2, round(h * 0.13)
        self.content = self.src(spec.media[0], self.iw, self.ih, zoom=1.07) if spec.media else None
        self.target = (float(self.rng.uniform(0.55, 0.75)), float(self.rng.uniform(0.3, 0.45)))
        self.dur = spec.frames / self.fps
        kw = spec.keyword if spec.show_captions else ""
        self.tag = gfx.chip(kw.upper(), gfx.font("bold", round(26 * u)), self.th["accent"], self.th["chip_text"],
                            round(w * 0.3), round(12 * u), round(7 * u)) if kw else None

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = gfx.drift(self.bg, t, w, h, self.cell, speed=6 * u)
        img = self.content.frame(i) if self.content else Image.new("RGB", (self.iw, self.ih), (40, 40, 40))
        # revelação da esquerda para a direita com uma faixa clara na borda
        p = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.6))
        vis = max(1, round(self.iw * p))
        canvas.paste(img.crop((0, 0, vis, self.ih)), (self.ix, self.iy))
        if p < 1:
            band = round(self.iw * 0.12)
            x0 = max(0, vis - band)
            strip = Image.new("RGBA", (vis - x0, self.ih), (255, 255, 255, 70))
            canvas.alpha_composite(strip, (self.ix + x0, self.iy))
        d = ImageDraw.Draw(canvas)
        acc = (*self.th["accent"], 255)
        # linha de scan que percorre a imagem
        if t > 0.5:
            q = gfx.ease_in_out(gfx.progress(t, 0.5, max(0.6, self.dur - 0.8)))
            sx = self.ix + self.iw * (0.08 + 0.84 * q)
            glow = Image.new("RGBA", (round(18 * u), self.ih), (*self.th["accent"], 45))
            canvas.alpha_composite(glow, (round(sx - 9 * u), self.iy))
            d.line([(sx, self.iy), (sx, self.iy + self.ih)], fill=acc, width=max(2, round(3 * u)))
        # marcação tipo HUD apontando um detalhe
        tx, ty = self.ix + self.iw * self.target[0], self.iy + self.ih * self.target[1]
        q = gfx.ease_out_cubic(gfx.progress(t, 0.85, 0.5))
        if q > 0:
            lw = max(2, round(3 * u))
            x_start = tx - self.iw * 0.14
            x_end = x_start + (self.iw * 0.2) * q
            d.line([(x_start, ty), (x_end, ty)], fill=acc, width=lw)
            for k in range(1, 9):  # marcas de régua
                xx = x_start + k * self.iw * 0.02
                if xx < x_end:
                    d.line([(xx, ty), (xx, ty + 8 * u)], fill=acc, width=max(1, lw - 1))
            q2 = gfx.ease_out_cubic(gfx.progress(t, 1.25, 0.35))
            if q2 > 0:
                d.line([(x_end, ty), (x_end, ty + self.ih * 0.14 * q2)], fill=acc, width=lw)
            q3 = gfx.progress(t, 1.5, 0.3)
            if self.tag is not None and q3 > 0:
                gfx.paste_at(canvas, self.tag, x_end + 10 * u, ty + self.ih * 0.14 - self.tag.height / 2, alpha=q3)
        # cantoneiras da moldura
        c = round(34 * u)
        for (x, y, sx_, sy_) in ((self.ix, self.iy, 1, 1), (self.ix + self.iw, self.iy, -1, 1),
                                 (self.ix, self.iy + self.ih, 1, -1), (self.ix + self.iw, self.iy + self.ih, -1, -1)):
            d.line([(x, y), (x + sx_ * c, y)], fill=(255, 255, 255, 220), width=max(2, round(3 * u)))
            d.line([(x, y), (x, y + sy_ * c)], fill=(255, 255, 255, 220), width=max(2, round(3 * u)))
        return canvas


# -------------------------------------------------------------------- card
class Card(Template):
    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.cell = round(100 * u)
        self.bg = gfx.background(spec.theme, w, h, self.cell, extra=self.cell).convert("RGBA")
        self.cw, self.chh = round(w * 0.34), round(h * 0.6)
        self.border = max(6, round(16 * u))
        self.content = self.src(spec.media[0], self.cw, self.chh, zoom=1.1) if spec.media else None
        self.glitches = {int(x) for x in self.rng.choice(max(2, spec.frames), size=max(1, spec.frames // 38), replace=False)}
        f = gfx.font("bold", round(46 * u))
        self.caps = [gfx.text_block(gfx.wrap(txt, f, round(w * 0.8)), f, (250, 250, 250), shadow=3, shadow_blur=8)
                     if txt and spec.show_captions else None for _, _, txt in spec.captions]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        canvas = gfx.drift(self.bg, t, w, h, self.cell, speed=7 * u)
        content = self.content.frame(i) if self.content else Image.new("RGB", (self.cw, self.chh), (50, 50, 60))
        p = gfx.progress(t, 0.0, 0.55)
        e = gfx.ease_out_back(p)
        shift = round(16 * u * (1 - gfx.ease_out_cubic(p)))
        glitch = any(abs(i - g) <= 1 for g in self.glitches) and p >= 1
        if glitch:
            shift = round(12 * u)
            content = gfx.slice_glitch(content, self.rng, bands=5, max_shift=round(26 * u))
        card = gfx.bordered(content, self.border, self.th["border"])
        card = gfx.chroma_shift(card.convert("RGB"), max(shift, round(3 * u))).convert("RGBA")
        card = gfx.glow(card, (180, 200, 255), round(22 * u), 0.55)
        card = gfx.scaled(card, 0.55 + 0.45 * e)
        float_y = math.sin(t * 1.7) * 7 * u
        gfx.paste_center(canvas, card, w / 2, h * 0.45 + float_y, alpha=gfx.ease_out_cubic(p * 1.6))
        idx, _ = self.caption_at(t)
        if 0 <= idx < len(self.caps) and self.caps[idx] is not None:
            cap = self.caps[idx]
            q = gfx.ease_out_cubic(gfx.progress(t, max(0.3, self.s.captions[idx][0]), 0.25))
            gfx.paste_center(canvas, cap, w / 2, h * 0.9 + (1 - q) * 14 * u, alpha=q)
        return canvas


# --------------------------------------------------------------------- vhs
class Vhs(Template):
    """Desenhado em meia resolução (o visual VHS é "mole" de propósito); o FFmpeg amplia."""

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        self.hw, self.hh = spec.w // 2 // 2 * 2, spec.h // 2 // 2 * 2
        self.render_size = (self.hw, self.hh)
        u = self.u / 2  # tudo desenhado na metade do tamanho
        self.hu = u
        self.content = self.src(spec.media[0], self.hw, self.hh, zoom=1.06) if spec.media else None
        self.grain = [self.rng.normal(0, 11, (self.hh, self.hw, 1)).astype(np.float32) for _ in range(6)]
        self.tint = np.array([1.04, 0.98, 1.06], np.float32) * 0.72
        scan = np.ones((self.hh, 1, 1), np.float32)
        scan[::2] = 0.9  # linhas de varredura (viram 2 px depois de ampliar)
        self.scan = scan
        self.osd = gfx.font("semibold", round(44 * u))
        self.tracking = {int(x) for x in self.rng.choice(max(2, spec.frames), size=max(1, spec.frames // 45), replace=False)}
        self.start_sec = int(self.rng.integers(0, 50))
        f = gfx.font("bold", round(44 * u))
        self.caps = [gfx.text_block(gfx.wrap(txt, f, round(self.hw * 0.8)), f, (250, 250, 240), shadow=2, shadow_blur=3)
                     if txt and spec.show_captions else None for _, _, txt in spec.captions]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.hu, self.hw, self.hh
        img = self.content.frame(i) if self.content else Image.new("RGB", (w, h), (30, 30, 30))
        arr = np.asarray(img, np.float32)
        gray = arr.mean(axis=2, keepdims=True)
        arr = (arr * self.tint + gray * 0.28 + 10 + self.grain[i % len(self.grain)]) * self.scan
        out = arr.clip(0, 255).astype(np.uint8)
        out[:, :, 0] = np.roll(out[:, :, 0], 2, axis=1)  # vazamento de cor
        if any(abs(i - k) <= 2 for k in self.tracking):  # faixa de tracking
            y0 = int(self.rng.integers(0, h - h // 10))
            y1 = y0 + int(self.rng.integers(h // 40, h // 12))
            out[y0:y1] = np.roll(out[y0:y1], int(self.rng.integers(5, 20)), axis=1)
            out[y0:y1] = np.clip(out[y0:y1].astype(np.int16) + 35, 0, 255).astype(np.uint8)
        frame = Image.fromarray(out).convert("RGBA")
        d = ImageDraw.Draw(frame)
        white = (240, 240, 240, 235)
        x, y = 0.075 * w, 0.08 * h
        d.text((x, y), "PLAY", font=self.osd, fill=white)
        sz = 30 * u
        tx = x + gfx.text_width("PLAY", self.osd) + 16 * u
        if int(t * 2) % 2 == 0 or t < 1:  # triângulo piscando
            d.polygon([(tx, y + 8 * u), (tx, y + 8 * u + sz), (tx + sz * 0.9, y + 8 * u + sz / 2)], fill=white)
        d.text((x, 0.85 * h), "SP", font=self.osd, fill=white)
        secs = self.start_sec + int(t)
        tc = f"00:{secs // 60:02d}:{secs % 60:02d}"
        d.text((w - 0.075 * w - gfx.text_width(tc, self.osd), 0.85 * h), tc, font=self.osd, fill=white)
        idx, _ = self.caption_at(t)
        if 0 <= idx < len(self.caps) and self.caps[idx] is not None:
            gfx.paste_center(frame, self.caps[idx], w / 2, h * 0.75)
        return frame


TEMPLATES: dict[str, type[Template]] = {
    "comparison": Comparison, "mosaic": Mosaic, "hud": Hud, "card": Card, "vhs": Vhs,
}


def render_template(spec: TemplateSpec, dest: Path, encode_args: list[str]) -> Path:
    from .templates_vertical import make_template  # vídeo em pé: versão nativa, o próprio ou cartão

    tpl = make_template(spec)
    writer = gfx.FrameWriter(dest, spec.w, spec.h, spec.fps, encode_args, input_size=tpl.render_size)
    try:
        for i in range(spec.frames):
            writer.write(tpl.frame(i))
    except BaseException:
        writer.abort()
        raise
    finally:
        tpl.close()
    writer.close()
    return dest
