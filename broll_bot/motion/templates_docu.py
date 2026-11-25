"""Família Documentário (estilo "Documentário" do app): papel branco rasgado sobre fundo carvão, letra de máquina
de escrever e anotações à mão em vermelho, como nos canais de história/guerra explicada.

* ``docphrase`` — a frase de impacto (``body``/``keyword``) datilografada num cartão de papel rasgado, com clipe,
  traço vermelho e uma linha se desenhando. As palavras de ``labels`` saem em vermelho. No lugar da frase na tela
  preta e do título de destaque.
* ``docbars``   — o gráfico de barras (``stats``) no papel: título datilografado, cada linha com o rótulo, a barra
  vermelha crescendo e o valor; ``subtitle`` vira a nota de rodapé. No lugar do ``chart``.
* ``diagram``   — esquema explicativo desenhado (``elements``, do diretor de IA): chão, caixas, bonecos, setas,
  explosão em raios, cota de medida e anotações à mão, entrando um a um (quando a fala diz a palavra ``say`` de
  cada um, ou em sequência). Título (``keyword``) e subtítulo datilografados no alto.

Paleta fixa (papel, tinta, vermelho), como o Dossiê. A tela do esquema vai de 0 a 100 na horizontal e de 0 a 56
na vertical (y para baixo).
"""

from __future__ import annotations

import math
import re

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import gfx
from .i18n import text as tr
from .templates import Template, TemplateSpec

Cue = tuple[float, str, float]
PAPER = (250, 249, 245)
INK = (38, 30, 26)
GREY = (122, 114, 104)
RED = (222, 64, 56)
CHARCOAL = (30, 30, 32)
SS = 2  # o esquema é desenhado em dobro e reduzido (traços suaves)
CANVAS_W, CANVAS_H = 100.0, 56.0
ELEMENT_TYPES = ("ground", "line", "box", "circle", "person", "arrow", "burst", "measure", "text", "vehicle")
# veículos prontos (a IA desenhava o Panther com dois retângulos): comprimento padrão em unidades da tela (100 de largura)
VEHICLE_H = {"tank": 0.45, "jeep": 0.55, "truck": 0.62, "car": 0.46, "plane": 0.3, "ship": 0.42, "cannon": 0.48}
PAPER_C = (246, 243, 236)  # roda: furo da cor do papel
VEHICLES = {"tank": 24.0, "jeep": 15.0, "truck": 22.0, "car": 15.0, "plane": 28.0, "ship": 40.0, "cannon": 13.0}


# ------------------------------------------------------------------ peças
def charcoal(w: int, h: int, seed: int) -> Image.Image:
    """Fundo carvão com trama fina e vinheta (o fundo escuro atrás do papel)."""
    rng = np.random.default_rng(seed)
    base = np.full((h, w), CHARCOAL[0], np.float32)
    base += rng.normal(0, 5, (h, w)).astype(np.float32)
    base += (np.arange(h)[:, None] % 4 < 2) * 3.0  # trama horizontal
    blot = Image.fromarray(rng.normal(0, 1, (max(2, h // 60), max(2, w // 60))).astype(np.float32) * 40 + 128)
    blot = np.asarray(blot.convert("L").resize((w, h), Image.BICUBIC), np.float32) - 128
    base += blot * 0.05
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot((xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2))
    base += 14 * np.clip(1 - r, 0, 1) ** 2  # luz suave no meio
    base *= (1 - 0.5 * np.clip(r - 0.4, 0, 1))
    rgb = np.stack([base, base, base * 1.03], -1)
    return Image.fromarray(rgb.clip(0, 255).astype(np.uint8), "RGB").convert("RGBA")


def torn_paper(w: int, h: int, rng: np.random.Generator, u: float, stain: bool = True) -> Image.Image:
    """Folha branca com a borda rasgada (serrilhado miúdo e alguns dentes maiores) e grão de papel."""
    pts: list[tuple[float, float]] = []
    step = max(4, round(7 * u))
    amp = 3.2 * u

    def jitter(k: int) -> float:
        big = rng.random() < 0.035
        return float(rng.uniform(0, amp * (4 if big else 1)))

    for x in range(0, w + 1, step):
        pts.append((x, jitter(x)))
    for y in range(0, h + 1, step):
        pts.append((w - jitter(y), y))
    for x in range(w, -1, -step):
        pts.append((x, h - jitter(x)))
    for y in range(h, -1, -step):
        pts.append((jitter(y), y))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).polygon(pts, fill=255)
    grain = np.full((h, w, 3), PAPER, np.float32) + rng.normal(0, 2.2, (h, w, 1)).astype(np.float32)
    sheet = Image.fromarray(grain.clip(0, 255).astype(np.uint8), "RGB").convert("RGBA")
    if stain:  # mancha de dedo/café num canto
        blot = Image.new("L", (w, h), 0)
        cx, cy = w * float(rng.uniform(0.84, 0.94)), h * float(rng.uniform(0.78, 0.9))
        r = 14 * u
        ImageDraw.Draw(blot).ellipse([cx - r, cy - r * 0.8, cx + r, cy + r * 0.9], fill=70)
        blot = blot.filter(ImageFilter.GaussianBlur(6 * u))
        grey = Image.new("RGBA", (w, h), (120, 112, 104, 255))
        grey.putalpha(blot)
        sheet.alpha_composite(grey)
    sheet.putalpha(mask)
    return sheet


def paperclip(u: float) -> Image.Image:
    """Clipe de metal (duas voltas) em pé, para prender no canto do cartão."""
    s = SS
    w, h = round(34 * u * s), round(100 * u * s)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lw = max(2, round(3 * u * s))
    col = (196, 198, 202, 255)
    d.rounded_rectangle([lw, lw, w - lw, h - lw], radius=w // 2, outline=col, width=lw)
    d.rounded_rectangle([w * 0.26, h * 0.18, w * 0.74, h * 0.84], radius=round(w * 0.24), outline=col, width=lw)
    img = img.resize((w // s, h // s), Image.LANCZOS)
    return gfx.with_shadow(img, -8.0, max(2, round(3 * u)), (round(2 * u), round(3 * u)), 90)


def _ease(x: float) -> float:
    return gfx.ease_out_cubic(gfx.clamp01(x))


# ------------------------------------------------------------------ base
class _DocBase(Template):
    """Fundo carvão, o cartão de papel subindo e um leve "respiro" da câmera."""

    def __init__(self, spec: TemplateSpec, pw: float, ph: float, clip: bool = True) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.dur = spec.frames / self.fps
        self.bg = charcoal(w, h, spec.seed)
        self.pw, self.ph = round(w * pw), round(h * ph)
        self.sheet = torn_paper(self.pw, self.ph, self.rng, u)
        self.clip = paperclip(u) if clip else None

    def card(self, page: Image.Image, t: float, cy: float | None = None) -> Image.Image:
        u, w, h = self.u, self.w, self.h
        canvas = self.bg.copy()
        flick = 0.97 + 0.03 * math.sin(t * 7.3) * math.sin(t * 2.1)  # tremor leve de projetor
        if abs(flick - 1) > 0.004:
            canvas = Image.eval(canvas, lambda v: round(v * flick))
        q = _ease(gfx.progress(t, 0.0, 0.45))
        layer = gfx.with_shadow(page, 0.0, round(22 * u), (0, round(12 * u)), 150)
        y = (cy if cy is not None else h / 2) + (1 - q) * 60 * u + math.sin(t * 0.9) * 1.5 * u
        gfx.paste_center(canvas, layer, w / 2, y, alpha=min(1.0, q * 1.5))
        if self.clip is not None and q > 0.6:
            x0 = w / 2 - page.width / 2 + 40 * u
            y0 = y - page.height / 2 - self.clip.height * 0.35
            gfx.paste_at(canvas, self.clip, x0, y0, alpha=min(1.0, (q - 0.6) * 3))
        return canvas


# ------------------------------------------------------------------ frase
def _mark_key(word: str) -> str:
    """A palavra para comparar com os ``labels``: sem caixa, pontuação, aspas, guillemets nem a elisão do francês
    ("l'Allemagne" casa com "Allemagne", "«millions»" com "millions")."""
    word = word.lower().strip(".,!?:;\"'«»“”‘’()")
    return re.sub(r"^(?:l|d|qu|j|n|s|c|m|t)['’]", "", word)


class DocPhrase(_DocBase):
    CPS = 22.0

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec, 0.68, 0.6)
        u = self.u
        self.text = " ".join((spec.body or spec.keyword or "").split())
        size = 104
        inner = self.pw - round(160 * u)
        f = gfx.font("typewriter", round(size * u))
        lines = gfx.wrap(self.text, f, inner, 3)
        while len(" ".join(lines)) < len(self.text) and size > 56:
            size -= 8
            f = gfx.font("typewriter", round(size * u))
            lines = gfx.wrap(self.text, f, inner, 3)
        self.f, self.lines = f, lines
        asc, desc = f.getmetrics()
        self.lh = round((asc + desc) * 1.18)
        self.x0 = round(90 * u)
        block = self.lh * len(lines)
        self.y0 = round(self.ph * 0.42 - block / 2)
        marks = {_mark_key(m) for lb in spec.labels for m in str(lb).split()} - {""}
        self.red = marks
        self.rule_y = min(self.ph - round(60 * u), self.y0 + block + round(70 * u))
        self.done_t = 0.45 + len(self.text) / self.CPS

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        text = " ".join((spec.body or spec.keyword or "").split())
        return [(0.0, "whoosh_soft", 0.35), (0.4, "snap", 0.25), (0.45, f"typing:{max(0.3, len(text) / cls.CPS):.3f}", 0.5)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u = self.u
        page = self.sheet.copy()
        d = ImageDraw.Draw(page)
        shown = max(0, int((t - 0.45) * self.CPS))
        left = shown
        y = self.y0
        for line in self.lines:
            x = self.x0
            for wd in line.split():
                if left <= 0:
                    break
                part = wd[:left]
                col = RED if _mark_key(wd) in self.red else INK
                d.text((x, y), gfx.safe_text(part), font=self.f, fill=(*col, 255))
                left -= len(wd) + 1
                x += gfx.text_width(wd + " ", self.f)
            y += self.lh
        # traço vermelho curto debaixo do começo e a linha de caneta embaixo
        q = _ease(gfx.progress(t, self.done_t, 0.35))
        if q > 0:
            uy = self.y0 + self.lh * len(self.lines) - round(self.lh * 0.12)
            d.rectangle([self.x0, uy, self.x0 + round(64 * u * q), uy + max(2, round(4 * u))], fill=(*RED, 255))
        q2 = _ease(gfx.progress(t, 0.5, 1.2))
        if q2 > 0:
            x1 = self.pw - round(70 * u)
            d.line([(round(70 * u), self.rule_y), (round(70 * u) + (x1 - round(70 * u)) * q2, self.rule_y)],
                   fill=(*INK, 220), width=max(1, round(2 * u)))
        return self.card(page, t)


# ------------------------------------------------------------------ barras
class DocBars(_DocBase):
    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec, 0.7, 0.64, clip=False)
        u = self.u
        rows = []
        for st in spec.stats[:5]:
            parsed = gfx.parse_number(str(st.get("value", "")))
            if parsed is None:
                continue
            value, dec = parsed
            label = " ".join(str(st.get("label", "")).split())
            unit = str(st.get("unit", "") or "")
            rows.append((label, value, gfx.fmt_number(value, dec) + (f" {unit}" if unit and len(unit) > 1 else unit)))
        self.rows = rows
        top = max((r[1] for r in rows), default=1.0) or 1.0
        self.frac = [max(0.04, r[1] / top) for r in rows]
        self.f_title = gfx.font("typewriter", round(56 * u))
        self.f_label = gfx.font("typewriter", round(30 * u))
        self.f_value = gfx.font("typewriter", round(32 * u))
        self.f_note = gfx.font("typewriter", round(20 * u))
        self.title = gfx.wrap(" ".join((spec.keyword or "").split()).upper(), self.f_title, self.pw - round(160 * u), 2)
        n = max(1, len(rows))
        # cartão do tamanho do conteúdo
        ta, td = self.f_title.getmetrics()
        self.row_h = round(118 * u)
        ph = round(70 * u) + len(self.title) * (ta + td) + round(50 * u) + n * self.row_h + round(90 * u)
        self.ph = min(round(self.h * 0.86), ph)
        self.sheet = torn_paper(self.pw, self.ph, self.rng, u)
        self.step = min(0.7, max(0.3, (self.dur * 0.5) / n))

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = min(5, len(spec.stats))
        return [(0.0, "whoosh_soft", 0.35)] + [(0.8 + k * 0.45, "tick", 0.25) for k in range(n)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u = self.u
        page = self.sheet.copy()
        d = ImageDraw.Draw(page)
        x0, x1 = round(80 * u), self.pw - round(80 * u)
        y = round(70 * u)
        ta, td = self.f_title.getmetrics()
        for line in self.title:
            d.text((x0, y), gfx.safe_text(line), font=self.f_title, fill=(*INK, 255))
            y += ta + td
        y += round(16 * u)
        d.line([(x0, y), (x1, y)], fill=(*GREY, 200), width=max(1, round(2 * u)))
        y += round(34 * u)
        bar_w = (x1 - x0) * 0.78
        for k, (label, _v, txt) in enumerate(self.rows):
            t0 = 0.7 + k * self.step
            q = _ease(gfx.progress(t, t0, 0.7))
            if gfx.progress(t, t0 - 0.2, 0.2) > 0:
                d.text((x0, y), gfx.safe_text(label), font=self.f_label, fill=(*INK, 255))
            by = y + round(46 * u)
            if q > 0:
                d.rectangle([x0, by, x0 + bar_w * self.frac[k] * q, by + round(16 * u)], fill=(*RED, 255))
                d.text((x1 - gfx.text_width(txt, self.f_value), by - round(8 * u)), gfx.safe_text(txt),
                       font=self.f_value, fill=(*INK, round(255 * q)))
            y += self.row_h
        if self.s.subtitle:
            note = f"{tr('source', self.s.lang)}: {self.s.subtitle}"
            d.text((x0, self.ph - round(56 * u)), gfx.safe_text(note), font=self.f_note, fill=(*GREY, 200))
        return self.card(page, t)


# ------------------------------------------------------------------ esquema
def clean_elements(raw: object) -> list[dict]:
    """Elementos do esquema que o diretor mandou, nos tipos e limites certos (o resto sai)."""
    out: list[dict] = []
    if not isinstance(raw, list):
        return out

    def num(v: object, lo: float, hi: float, default: float = 0.0) -> float:
        try:
            return max(lo, min(hi, float(v)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    def point(v: object) -> list[float] | None:
        if isinstance(v, (list, tuple)) and len(v) == 2:
            return [num(v[0], 0, CANVAS_W), num(v[1], 0, CANVAS_H)]
        return None

    for el in raw[:16]:
        if not isinstance(el, dict) or el.get("t") not in ELEMENT_TYPES:
            continue
        kind = el["t"]
        e: dict = {"t": kind, "color": "red" if el.get("color") == "red" else "ink"}
        if el.get("say"):
            e["say"] = " ".join(str(el["say"]).split())[:30]
        if kind == "ground":
            e["y"] = num(el.get("y"), 5, CANVAS_H, 42)
        elif kind == "line":
            pts = [p for p in (point(x) for x in el.get("pts") or []) if p][:12]
            if len(pts) < 2:
                continue
            e["pts"], e["w"] = pts, num(el.get("w"), 1, 6, 2)
        elif kind in ("box", "circle"):
            e["x"], e["y"] = num(el.get("x"), 0, CANVAS_W), num(el.get("y"), 0, CANVAS_H)
            if kind == "box":
                e["w"], e["h"] = num(el.get("w"), 0.5, CANVAS_W, 6), num(el.get("h"), 0.5, CANVAS_H, 4)
            else:
                e["r"] = num(el.get("r"), 0.5, 25, 2)
            e["fill"] = el.get("fill") if el.get("fill") in ("grey", "ink", "red", "none") else "grey"
        elif kind == "person":
            e["x"], e["y"] = num(el.get("x"), 0, CANVAS_W, 70), num(el.get("y"), 0, CANVAS_H, 42)
            e["h"] = num(el.get("h"), 4, 40, 16)
            e["pose"] = el.get("pose") if el.get("pose") in ("stand", "walk", "crawl", "point", "fall") else "stand"
            e["face"] = -1 if el.get("face") in ("left", -1) else 1
        elif kind == "arrow":
            a, b = point(el.get("from")), point(el.get("to"))
            if not a or not b:
                continue
            e["from"], e["to"] = a, b
            e["color"] = "ink" if el.get("color") == "ink" else "red"
        elif kind == "burst":
            e["x"], e["y"] = num(el.get("x"), 0, CANVAS_W, 50), num(el.get("y"), 0, CANVAS_H, 25)
            e["r"] = num(el.get("r"), 2, 30, 12)
            e["color"] = "ink" if el.get("color") == "ink" else "red"
        elif kind == "measure":
            e["x"] = num(el.get("x"), 0, CANVAS_W, 60)
            e["y1"], e["y2"] = num(el.get("y1"), 0, CANVAS_H, 42), num(el.get("y2"), 0, CANVAS_H, 30)
            e["label"] = " ".join(str(el.get("label") or "").split())[:30]
            e["color"] = "ink" if el.get("color") == "ink" else "red"
        elif kind == "vehicle":
            vk = str(el.get("kind") or "").lower()
            vk = {"m10": "tank", "panther": "tank", "sherman": "tank", "lorry": "truck", "aircraft": "plane",
                  "glider": "plane", "boat": "ship", "gun": "cannon", "artillery": "cannon"}.get(vk, vk)
            if vk not in VEHICLES:
                continue
            e["kind"] = vk
            e["x"], e["y"] = num(el.get("x"), 0, CANVAS_W, 50), num(el.get("y"), 0, CANVAS_H, 42)
            e["w"] = num(el.get("w"), 6, 60, VEHICLES[vk])
            e["face"] = -1 if el.get("face") in ("left", -1) else 1
            e["fill"] = el.get("fill") if el.get("fill") in ("grey", "ink", "red", "none") else "grey"
        elif kind == "text":
            text = " ".join(str(el.get("text") or "").split())[:40]
            if not text:
                continue
            e["text"], e["x"], e["y"] = text, num(el.get("x"), 0, CANVAS_W), num(el.get("y"), 0, CANVAS_H)
            e["size"] = el.get("size") if el.get("size") in ("s", "m", "l") else "m"
            e["font"] = "type" if el.get("font") == "type" else "hand"
        out.append(e)
    return out


def element_texts(elements: list[dict]) -> list[str]:
    """O que o esquema escreve na tela (para a checagem "só o que a fala diz")."""
    return [e.get("text") or e.get("label") or "" for e in elements if e.get("text") or e.get("label")]


def _norm(s: str) -> str:
    return re.sub(r"[^\w]", "", s.lower())


class Diagram(_DocBase):
    DRAW = 0.6  # s para cada elemento se desenhar

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec, 0.94, 0.94, clip=False)
        u = self.u
        self.els = clean_elements(spec.elements)
        self.f_title = gfx.font("typewriter", round(84 * u * SS))
        self.f_sub = gfx.font("typewriter", round(32 * u * SS))
        self.fonts = {(f, s): gfx.font("typewriter" if f == "type" else "hand",
                                       round({"s": 34, "m": 46, "l": 62}[s] * u * SS * (0.92 if f == "type" else 1)))
                      for f in ("type", "hand") for s in ("s", "m", "l")}
        # área do desenho dentro do papel (abaixo do título)
        self.mx, self.my = 70 * u * SS, 200 * u * SS
        self.cw = self.pw * SS - 2 * self.mx
        self.ch = self.ph * SS - self.my - 50 * u * SS
        self.lw = max(2, round(5.5 * u * SS))
        self.moved = self._layout()
        self.starts = self._timing()
        self._cache: tuple[tuple, Image.Image] | None = None

    def _timing(self) -> list[float]:
        """Quando cada elemento começa: na palavra ``say`` da fala, se houver; senão em sequência."""
        n = max(1, len(self.els))
        span = max(1.5, min(self.dur * 0.65, n * 0.9))
        seq = [0.6 + span * k / n for k in range(n)]
        caps = [(c[0], _norm(c[2])) for c in self.s.captions]
        out = []
        for k, el in enumerate(self.els):
            at = seq[k]
            say = _norm(el.get("say", ""))
            if say:
                hit = next((t0 for t0, txt in caps if say in txt), None)
                if hit is not None:
                    at = max(0.5, min(self.dur - 1.0, hit))
            out.append(at)
        return out

    # ---------------------------------------------------------- posição dos textos
    def _text_box(self, el: dict, x: float, y: float) -> tuple[float, float, float, float]:
        fnt = self.fonts[(el["font"], el["size"])]
        asc, desc = fnt.getmetrics()
        return x, y - (asc + desc) / 2, x + gfx.text_width(gfx.safe_text(el["text"]), fnt), y + (asc + desc) / 2

    def _box(self, el: dict) -> tuple[float, float, float, float] | None:
        """Retângulo que a peça ocupa na página (em dobro), com folga para o traço."""
        t, m = el["t"], self.lw * 1.5
        if t == "ground":
            _, y = self.P(0, el["y"])
            return self.mx * 0.6, y - m, self.mx + self.cw + self.mx * 0.4, y + m
        if t in ("line", "arrow"):
            pts = [self.P(*p) for p in (el["pts"] if t == "line" else [el["from"], el["to"]])]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            return min(xs) - m * 2, min(ys) - m * 2, max(xs) + m * 2, max(ys) + m * 2
        if t == "box":
            x, y = self.P(el["x"], el["y"])
            return x - m, y - m, x + self.S(el["w"]) + m, y + el["h"] / CANVAS_H * self.ch + m
        if t in ("circle", "burst"):
            x, y = self.P(el["x"], el["y"])
            r = self.S(el["r"]) * (0.72 if t == "burst" else 1.0)  # a ponta dos raios pode passar perto do texto
            return x - r - m, y - r - m, x + r + m, y + r + m
        if t == "person":
            x, y = self.P(el["x"], el["y"])
            h = el["h"] / CANVAS_H * self.ch
            if el["pose"] == "crawl":
                return x - h * 1.2, y - h * 0.35, x + h * 0.5, y + m
            return x - h * 0.45, y - h * 1.05, x + h * 0.45, y + m
        if t == "measure":
            x, y1 = self.P(el["x"], el["y1"])
            _, y2 = self.P(el["x"], el["y2"])
            return x - 3 * self.lw, min(y1, y2) - m, x + 3 * self.lw, max(y1, y2) + m
        if t == "vehicle":
            x, y = self.P(el["x"], el["y"])
            L = self.S(el["w"])
            if el["kind"] == "plane":  # no ar: y é o meio do avião
                return x - L / 2 - m, y - L * 0.3 - m, x + L / 2 + m, y + L * 0.2 + m
            reach = 1.25 if el["kind"] == "tank" else 1.0  # o cano passa da frente
            x0, x1 = (x - L / 2, x - L / 2 + L * reach) if el["face"] > 0 else (x + L / 2 - L * reach, x + L / 2)
            return x0 - m, y - L * VEHICLE_H[el["kind"]] - m, x1 + m, y + m
        return None

    def _layout(self) -> int:
        """Textos (e rótulo da cota) sem encostar no desenho nem uns nos outros, e dentro do papel: cada um vai
        para o lugar livre mais perto de onde a IA pôs (a IA às vezes escrevia por cima do boneco ou da explosão).
        Devolve quantos mudaram de lugar."""
        x_lo, x_hi = self.mx * 0.6, self.mx + self.cw + self.mx * 0.4
        y_lo, y_hi = self.my - 10 * self.u * SS, self.ph * SS - 30 * self.u * SS
        taken = [b for b in (self._box(e) for e in self.els) if b is not None]
        texts: list[dict] = []
        for el in self.els:
            if el["t"] == "text":
                texts.append(el)
            elif el["t"] == "measure" and el.get("label"):
                lab = {"t": "text", "text": el["label"], "x": el["x"] + 2.0, "y": (el["y1"] + el["y2"]) / 2,
                       "size": "m", "font": "hand", "color": el.get("color")}
                el["_label"] = lab
                lab["_alt"] = el["x"] - 2.0  # do outro lado da linha da cota, alinhado à direita
                texts.append(lab)

        def free(b: tuple[float, float, float, float]) -> bool:
            if b[0] < x_lo or b[2] > x_hi or b[1] < y_lo or b[3] > y_hi:
                return False
            pad = 10 * self.u * SS
            return all(b[2] + pad <= o[0] or o[2] + pad <= b[0] or b[3] + pad <= o[1] or o[3] + pad <= b[1]
                       for o in taken)

        moved = 0
        for el in texts:
            while True:  # texto mais largo que o papel: letra menor
                x0, y0 = self.P(el["x"], el["y"])
                b = self._text_box(el, x0, y0)
                if b[2] - b[0] <= (x_hi - x_lo) * 0.9 or el["size"] == "s":
                    break
                el["size"] = {"l": "m", "m": "s"}[el["size"]]
            w, h = b[2] - b[0], b[3] - b[1]
            x0 = min(max(x0, x_lo), x_hi - w)  # dentro do papel
            best = None
            step_y, step_x = h * 0.55, self.cw * 0.04
            cands = sorted(((dx, dy) for dx in range(-8, 9) for dy in range(-10, 11)),
                           key=lambda c: (c[0] * step_x) ** 2 + (c[1] * step_y) ** 2)
            anchors = [x0]
            if "_alt" in el:
                anchors.append(self.P(el["_alt"], 0)[0] - w)
            for dx, dy in cands:
                for ax in anchors:
                    cand = self._text_box(el, ax + dx * step_x, y0 + dy * step_y)
                    if free(cand):
                        best = cand
                        break
                if best is not None:
                    break
            if best is None:  # sem lugar livre: fica onde está, só dentro do papel
                best = self._text_box(el, x0, y0)
            if abs(best[0] - self.P(el["x"], el["y"])[0]) > 1 or abs((best[1] + best[3]) / 2 - y0) > 1:
                moved += 1
            el["_px"], el["_py"] = best[0], (best[1] + best[3]) / 2
            taken.append(best)
        return moved

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = len(clean_elements(spec.elements))
        out: list[Cue] = [(0.0, "whoosh_soft", 0.35), (0.25, f"typing:{max(0.3, len(spec.keyword) / 20):.3f}", 0.4)]
        if n:
            out.append((0.6, f"pencil:{min(6.0, 0.9 * n):.3f}", 0.3))
        return out

    # coordenadas do esquema -> pixels da página (em dobro)
    def P(self, x: float, y: float) -> tuple[float, float]:
        return self.mx + x / CANVAS_W * self.cw, self.my + y / CANVAS_H * self.ch

    def S(self, v: float) -> float:
        return v / CANVAS_W * self.cw

    def _color(self, el: dict) -> tuple:
        return (*(RED if el.get("color") == "red" else INK), 255)

    def _polyline(self, d: ImageDraw.ImageDraw, pts: list[tuple[float, float]], q: float, col: tuple, w: int) -> None:
        segs = list(zip(pts, pts[1:]))
        total = sum(math.dist(a, b) for a, b in segs) or 1.0
        left = total * q
        for a, b in segs:
            seg = math.dist(a, b)
            if left <= 0:
                break
            k = min(1.0, left / seg) if seg else 1.0
            d.line([a, (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k)], fill=col, width=w, joint="curve")
            left -= seg

    def _vehicle(self, d: ImageDraw.ImageDraw, el: dict, q: float) -> None:
        """Silhueta de lado (preenchida, com contorno de tinta): tanque, jeep, caminhão, carro, avião, navio, canhão.
        ``x`` é o meio, ``y`` o chão (no avião, o meio dele); ``face`` vira o desenho."""
        cx, gy = self.P(el["x"], el["y"])
        L, f, k = self.S(el["w"]), el["face"], el["kind"]
        a = round(255 * q)
        fill = {"grey": (126, 118, 108), "ink": INK, "red": RED}.get(el["fill"])
        fill_c = (*fill, a) if fill is not None else None
        line = (*INK, a)
        lw = max(2, round(self.lw * 0.8))

        def P(u: float, v: float) -> tuple[float, float]:  # u: 0 = traseira .. 1 = frente; v: altura (em L)
            return cx + f * (u - 0.5) * L, gy - v * L

        def poly(pts: list[tuple[float, float]], filled: bool = True) -> None:
            d.polygon([P(*p) for p in pts], fill=fill_c if filled else None, outline=line, width=lw)

        def wheel(u: float, v: float, r: float) -> None:
            x, y = P(u, v)
            d.ellipse([x - r * L, y - r * L, x + r * L, y + r * L], fill=(*PAPER_C, a), outline=line, width=lw)

        if k == "tank":
            d.rounded_rectangle(sorted_box(P(0.02, 0.0), P(0.98, 0.15)), radius=0.07 * L, fill=(*INK, a))
            for u in (0.12, 0.27, 0.42, 0.58, 0.73, 0.88):
                wheel(u, 0.075, 0.05)
            poly([(0.0, 0.15), (1.0, 0.15), (0.94, 0.3), (0.06, 0.3)])
            poly([(0.3, 0.3), (0.72, 0.3), (0.67, 0.45), (0.35, 0.45)])
            poly([(0.7, 0.355), (1.25, 0.355), (1.25, 0.395), (0.7, 0.395)])
        elif k in ("jeep", "car"):
            if k == "jeep":
                poly([(0.0, 0.12), (1.0, 0.12), (1.0, 0.3), (0.62, 0.32), (0.0, 0.32)])
                d.line([P(0.6, 0.32), P(0.55, 0.55)], fill=line, width=lw * 2)  # para-brisa
                d.line([P(0.08, 0.32), P(0.08, 0.5), P(0.5, 0.5)], fill=line, width=lw)  # santantônio/capota
                wheel(-0.02, 0.24, 0.09)  # estepe atrás
            else:
                poly([(0.0, 0.12), (1.0, 0.12), (1.0, 0.27), (0.75, 0.3), (0.62, 0.46), (0.3, 0.46), (0.18, 0.3),
                      (0.0, 0.28)])
            wheel(0.2, 0.12, 0.12)
            wheel(0.8, 0.12, 0.12)
        elif k == "truck":
            poly([(0.0, 0.15), (0.7, 0.15), (0.7, 0.55), (0.66, 0.62), (0.04, 0.62), (0.0, 0.55)])  # lona
            poly([(0.72, 0.13), (1.0, 0.13), (1.0, 0.32), (0.92, 0.36), (0.88, 0.5), (0.72, 0.5)])
            poly([(0.76, 0.34), (0.86, 0.34), (0.86, 0.46), (0.76, 0.46)], filled=False)  # janela
            for u in (0.14, 0.3, 0.86):
                wheel(u, 0.1, 0.1)
        elif k == "plane":
            d.ellipse(sorted_box(P(0.0, 0.05), P(1.0, -0.07)), fill=fill_c, outline=line, width=lw)
            poly([(0.38, 0.0), (0.62, 0.0), (0.5, -0.05)])
            d.ellipse(sorted_box(P(0.3, 0.025), P(0.66, -0.035)), fill=(*INK, a))  # asa
            poly([(0.0, 0.0), (0.12, 0.0), (0.04, 0.2), (0.0, 0.2)])  # leme
            d.line([P(1.01, 0.12), P(1.01, -0.14)], fill=line, width=lw)  # hélice
        elif k == "ship":
            poly([(0.0, 0.18), (1.0, 0.18), (0.9, 0.0), (0.06, 0.0)])
            poly([(0.3, 0.18), (0.66, 0.18), (0.62, 0.3), (0.34, 0.3)])
            poly([(0.44, 0.3), (0.52, 0.3), (0.53, 0.42), (0.45, 0.42)])
        elif k == "cannon":
            poly([(0.1, 0.22), (1.0, 0.42), (1.0, 0.48), (0.08, 0.3)])
            d.line([P(0.25, 0.2), P(-0.05, 0.0)], fill=line, width=lw * 2)  # conteira
            wheel(0.3, 0.2, 0.2)

    def _person(self, d: ImageDraw.ImageDraw, el: dict, q: float) -> None:
        x, y = self.P(el["x"], el["y"])
        hgt = el["h"] / CANVAS_H * self.ch  # altura na escala vertical da tela
        f = el.get("face", 1)
        col = (*INK, round(255 * q))
        w = max(3, round(self.lw * 2.2))
        if el["pose"] == "crawl":  # deitado de bruços, cabeça para a frente
            L = hgt * 1.1
            d.line([(x - f * L, y - hgt * 0.06), (x, y - hgt * 0.1)], fill=col, width=round(w * 1.8))
            r = hgt * 0.1
            d.ellipse([x + f * r * 0.6 - r, y - hgt * 0.1 - 2 * r, x + f * r * 0.6 + r, y - hgt * 0.1], outline=col,
                      width=w)
            d.line([(x, y - hgt * 0.08), (x + f * hgt * 0.35, y - hgt * 0.02)], fill=col, width=w)
            return
        if el["pose"] == "fall":
            hgt *= 0.8
        r = hgt * 0.1
        hip = (x, y - hgt * 0.45)
        neck = (x + (f * hgt * 0.12 if el["pose"] == "fall" else 0), y - hgt * 0.8)
        d.ellipse([neck[0] - r, neck[1] - 2 * r, neck[0] + r, neck[1]], fill=col)
        d.line([neck, hip], fill=col, width=round(w * 1.3))
        spread = hgt * (0.2 if el["pose"] in ("walk", "point") else 0.09)
        d.line([hip, (x - spread, y)], fill=col, width=w)
        d.line([hip, (x + spread, y)], fill=col, width=w)
        arm = (neck[0], neck[1] + hgt * 0.1)
        if el["pose"] == "point":  # braços para a frente (arma, apontando)
            d.line([arm, (arm[0] + f * hgt * 0.38, arm[1] + hgt * 0.02)], fill=col, width=w)
        else:
            d.line([arm, (arm[0] - hgt * 0.15, arm[1] + hgt * 0.28)], fill=col, width=w)
            d.line([arm, (arm[0] + hgt * 0.15, arm[1] + hgt * 0.28)], fill=col, width=w)

    def _text(self, page: Image.Image, el: dict, q: float) -> None:
        fnt = self.fonts[(el["font"], el["size"])]
        txt = gfx.safe_text(el["text"])
        col = RED if el.get("color") == "red" else INK
        tw = gfx.text_width(txt, fnt)
        asc, desc = fnt.getmetrics()
        layer = Image.new("RGBA", (tw + 8, asc + desc + 8), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((4, 4), txt, font=fnt, fill=(*col, 255))
        cut = max(1, round(layer.width * q))  # escrita da esquerda para a direita
        layer = layer.crop((0, 0, cut, layer.height))
        x, y = (el["_px"], el["_py"]) if "_px" in el else self.P(el["x"], el["y"])
        page.alpha_composite(layer, (round(x), round(y - (asc + desc) / 2)))

    def _draw(self, page: Image.Image, el: dict, q: float) -> None:
        d = ImageDraw.Draw(page)
        col = self._color(el)
        t = el["t"]
        if t == "ground":
            _, y = self.P(0, el["y"])
            self._polyline(d, [(self.mx * 0.6, y), (self.mx + self.cw + self.mx * 0.4, y)], q, (*INK, 255),
                           max(2, round(self.lw * 0.6)))
        elif t == "line":
            self._polyline(d, [self.P(*p) for p in el["pts"]], q, col, max(2, round(self.lw * el["w"] / 2)))
        elif t == "box":
            x, y = self.P(el["x"], el["y"])
            w, h = self.S(el["w"]), el["h"] / CANVAS_H * self.ch
            fill = {"grey": (126, 118, 108), "ink": INK, "red": RED}.get(el["fill"])
            a = round(255 * q)
            if fill is not None:
                d.rectangle([x, y, x + w, y + h], fill=(*fill, a))
            else:
                d.rectangle([x, y, x + w, y + h], outline=(*INK, a), width=self.lw)
        elif t == "circle":
            x, y = self.P(el["x"], el["y"])
            r = self.S(el["r"]) * q
            fill = {"grey": (126, 118, 108), "ink": INK, "red": RED}.get(el["fill"])
            if r > 1:
                if fill is not None:
                    d.ellipse([x - r, y - r, x + r, y + r], fill=(*fill, 255))
                else:
                    d.ellipse([x - r, y - r, x + r, y + r], outline=(*INK, 255), width=self.lw)
        elif t == "person":
            self._person(d, el, q)
        elif t == "vehicle":
            self._vehicle(d, el, q)
        elif t == "arrow":
            a, b = self.P(*el["from"]), self.P(*el["to"])
            tip = (a[0] + (b[0] - a[0]) * q, a[1] + (b[1] - a[1]) * q)
            d.line([a, tip], fill=col, width=self.lw)
            if q > 0.85:
                ang = math.atan2(b[1] - a[1], b[0] - a[0])
                L = 4.5 * self.lw
                for s in (-0.45, 0.45):
                    d.line([tip, (tip[0] - L * math.cos(ang + s), tip[1] - L * math.sin(ang + s))], fill=col,
                           width=self.lw)
        elif t == "burst":
            cx, cy = self.P(el["x"], el["y"])
            R = self.S(el["r"])
            rng = np.random.default_rng(int(el["x"] * 31 + el["y"] * 7))
            for k in range(28):
                ang = 2 * math.pi * k / 28 + float(rng.uniform(-0.08, 0.08))
                r0 = R * 0.25
                r1 = R * float(rng.uniform(0.75, 1.05)) * q
                if r1 > r0:
                    d.line([(cx + r0 * math.cos(ang), cy + r0 * math.sin(ang)),
                            (cx + r1 * math.cos(ang), cy + r1 * math.sin(ang))], fill=col,
                           width=max(1, round(self.lw * 0.6)))
        elif t == "measure":
            x, y1 = self.P(el["x"], el["y1"])
            _, y2 = self.P(el["x"], el["y2"])
            yq = y1 + (y2 - y1) * q
            d.line([(x, y1), (x, yq)], fill=col, width=max(1, round(self.lw * 0.5)))
            tick = 2.2 * self.lw
            d.line([(x - tick, y1), (x + tick, y1)], fill=col, width=max(1, round(self.lw * 0.5)))
            if q > 0.95:
                d.line([(x - tick, y2), (x + tick, y2)], fill=col, width=max(1, round(self.lw * 0.5)))
            if el.get("_label") and q > 0.5:  # o rótulo já com o lugar livre (_layout)
                self._text(page, el["_label"], (q - 0.5) * 2)
        elif t == "text":
            self._text(page, el, q)

    def _page(self, t: float) -> Image.Image:
        qs = tuple(round(_ease(gfx.progress(t, s, self.DRAW)), 2) for s in self.starts)
        tq = round(gfx.progress(t, 0.25, 0.9), 2)
        key = (qs, tq)
        if self._cache is not None and self._cache[0] == key:
            return self._cache[1]
        u = self.u
        big = self.sheet.resize((self.pw * SS, self.ph * SS), Image.BICUBIC)
        d = ImageDraw.Draw(big)
        title = gfx.safe_text(" ".join((self.s.keyword or "").split()).upper())
        if title and tq > 0:
            n = max(1, round(len(title) * tq))
            d.text((self.mx, 50 * u * SS), title[:n], font=self.f_title, fill=(*INK, 255))
        if self.s.subtitle and tq >= 1:
            d.text((self.mx, 50 * u * SS + self.f_title.size * 1.15), gfx.safe_text(self.s.subtitle),
                   font=self.f_sub, fill=(*INK, 200))
        for el, q in zip(self.els, qs):
            if q > 0:
                self._draw(big, el, q)
        page = big.resize((self.pw, self.ph), Image.LANCZOS)
        self._cache = (key, page)
        return page

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        return self.card(self._page(t), t)


def sorted_box(a: tuple[float, float], b: tuple[float, float]) -> list[float]:
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]


DOCU_TEMPLATES: dict[str, type[Template]] = {"docphrase": DocPhrase, "docbars": DocBars, "diagram": Diagram}


def docu_cues(kind: str, spec: TemplateSpec) -> list[Cue] | None:
    cls = DOCU_TEMPLATES.get(kind)
    return cls.cues(spec) if cls is not None else None
