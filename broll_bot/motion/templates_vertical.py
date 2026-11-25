"""Templates para o vídeo curto em pé (1080x1920: Shorts, Reels, TikTok).

Os templates do vídeo deitado foram desenhados para 16:9; numa tela em pé uns ficam bons (só com a letra
na escala da largura, ver ``Template.u``), outros cortam texto ou espremem a foto. Aqui ficam:

* as versões nativas em pé das famílias que mais aparecem, com o conteúdo na metade de cima (a legenda
  animada mora logo abaixo do meio e os botões do app ficam embaixo):

  - ``VNumber``  — o número gigante contando (stat, counter, price, score, gauge, speedometer...)
  - ``VRank``    — "#3" / "1/5" enorme com o nome e a foto (ranking, paperrank, steps)
  - ``VVersus``  — duas (ou três) fotos empilhadas com "VS" no meio (comparison, versus, beforeafter...)
  - ``VList``    — título e itens entrando um a um, com ✓/✗ ou número (proscons, specs, timeline, flow...)
  - ``VStack``   — fotos em pilha, a da frente sai e a próxima chega (gallery, showcase, filmstrip, hud...)
  - ``VQuote``   — foto redonda, nome e a frase entre aspas (quote, comment, source, profile, warning...)

* ``Card``: qualquer outro template desenhado em 16:9 e posto como um cartão na parte de cima, sobre a
  própria imagem desfocada (nunca uma faixa preta).

``make_template`` decide qual usar.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import gfx
from .templates import TEMPLATES, Template, TemplateSpec
from .templates_plus import WHITE, _Base, _ease

# ficam como estão (o desenho já funciona em pé com a letra na escala da largura)
VERTICAL_SAFE = {"agenda", "definition", "fromto", "headline", "mosaic", "news", "newsrank", "opener", "paperfloat",
                 "peoplegrid", "percentbar", "photostack", "statement", "wordpop", "yearflash", "report", "chapter",
                 "counter", "bulletin", "textmask", "source", "paperstatement"}
NATIVE: dict[str, str] = {
    **{k: "number" for k in ("stat", "price", "score", "gauge", "speedometer", "receipt", "downtime")},
    **{k: "rank" for k in ("ranking", "paperrank", "steps")},
    **{k: "versus" for k in ("comparison", "versus", "beforeafter", "newscompare", "papercompare", "splitpanels",
                             "triptych", "paperpair")},
    **{k: "list" for k in ("proscons", "checklist", "specs", "datasheet", "timeline", "newstimeline", "papertimeline",
                           "flow", "mythfact", "coverage", "poll", "chart")},
    **{k: "stack" for k in ("gallery", "showcase", "sketch", "scrapbook", "videowall", "hud", "card", "vhs",
                            "magnifier", "annotate", "exploded", "blueprint", "dossier", "newsfloat", "filmstrip")},
    **{k: "quote" for k in ("quote", "comment", "profile", "unverified", "warning")},
}
DARK = (14, 16, 22)


def _round_mask(w: int, h: int, r: int) -> Image.Image:
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    return m


def _rounded(img: Image.Image, r: int) -> Image.Image:
    out = img.convert("RGBA")
    out.putalpha(_round_mask(out.width, out.height, r))
    return out


def _shadow(card: Image.Image, blur: int, dy: int, opacity: int = 150) -> Image.Image:
    pad = blur * 2 + abs(dy)
    out = Image.new("RGBA", (card.width + 2 * pad, card.height + 2 * pad), (0, 0, 0, 0))
    sh = Image.new("RGBA", card.size, (0, 0, 0, opacity))
    sh.putalpha(card.getchannel("A").point(lambda a: a * opacity // 255))
    out.alpha_composite(sh, (pad, pad + dy))
    out = out.filter(ImageFilter.GaussianBlur(blur))
    out.alpha_composite(card, (pad, pad))
    return out


def _chip(text: str, size: int, bg: tuple, fg: tuple = DARK) -> Image.Image:
    f = gfx.font("heavy", size)
    t = gfx.safe_text(text)
    w = gfx.text_width(t, f)
    asc, desc = f.getmetrics()
    px, py = round(size * 0.55), round(size * 0.28)
    img = Image.new("RGBA", (w + 2 * px, asc + desc + 2 * py), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=round(size * 0.35), fill=(*bg, 255))
    d.text((px, py), t, font=f, fill=(*fg, 255))
    return img


def _title(text: str, size: int, max_w: int, lines: int = 2, fill: tuple = WHITE) -> Image.Image | None:
    if not (text or "").strip():
        return None
    f = gfx.font("heavy", size)
    rows = gfx.wrap(text, f, max_w, lines)
    blocks = [gfx.outlined_text(r, f, (*fill, 255), max(3, size // 14), (0, 0, 0, 255)) for r in rows]
    w = max(b.width for b in blocks)
    h = sum(b.height for b in blocks) - (len(blocks) - 1) * round(size * 0.18)
    out = Image.new("RGBA", (w, max(1, h)), (0, 0, 0, 0))
    y = 0
    for b in blocks:
        out.alpha_composite(b, ((w - b.width) // 2, y))
        y += b.height - round(size * 0.18)
    return out


class _V(_Base):
    """Base dos verticais: fundo da cena desfocado, a cor do canal e os tempos de entrada."""

    DIM = 0.45

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        self.U = self.w / 1080  # unidade própria: 1 = tela de 1080 de largura
        self.total = spec.frames / self.fps

    def pop(self, t: float, start: float, dur: float = 0.42) -> float:
        return gfx.ease_out_back(gfx.progress(t, start, dur), 1.5)

    def media_card(self, k: int, w: int, h: int, zoom: float = 1.1):
        if k >= len(self.s.media):
            return None
        return self.src(self.s.media[k], w, h, zoom=zoom, pan=(float(self.rng.uniform(-0.6, 0.6)), 0.0))


# ----------------------------------------------------------------------------------------------- número
class VNumber(_V):
    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        U, w, h = self.U, self.w, self.h
        num = dict(spec.number or {})
        stats = list(spec.stats or [])
        if "value" not in num and stats:
            parsed = gfx.parse_number(str(stats[0].get("value", "")))
            if parsed:
                num = {"value": parsed[0], "decimals": parsed[1], "suffix": " " + str(stats[0].get("unit", "")).strip(),
                       "label": stats[0].get("label", "")}
        self.value = float(num.get("value") or 0)
        self.decimals = int(num.get("decimals") or (0 if self.value == int(self.value) else 1))
        self.prefix = str(num.get("prefix") or "")
        suffix = str(num.get("suffix") or "")
        if spec.kind == "score" and not suffix:
            suffix = "/10"
        self.suffix = suffix.rstrip()
        label = str(num.get("label") or spec.subtitle or "")
        self.card_w, self.card_h = round(w * 0.86), round(h * 0.25)
        self.card = self.media_card(0, self.card_w, self.card_h)
        self.cy_num = h * (0.43 if self.card is not None else 0.32)
        final = gfx.fmt_number(self.value, self.decimals, self.prefix, self.suffix)
        size = round(min(250 * U, w * 0.86 / max(3, len(final)) * 1.75))
        self.f_num = gfx.font("heavy", size)
        self.size = size
        self.cache: dict[str, Image.Image] = {}
        self.label = _title(label.upper(), round(52 * U), round(w * 0.84), 2, (225, 230, 240)) if label else None
        kw = (spec.keyword or "").upper()
        self.chip = _chip(kw[:28], round(40 * U), self.acc) if kw else None

    def _num(self, text: str) -> Image.Image:
        img = self.cache.get(text)
        if img is None:
            img = self.cache[text] = gfx.outlined_text(text, self.f_num, (*WHITE, 255), max(4, self.size // 16),
                                                       (0, 0, 0, 255))
        return img

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        U, w, h = self.U, self.w, self.h
        canvas = self.base(i, t)
        d = ImageDraw.Draw(canvas)
        if self.chip is not None:
            q = self.pop(t, 0.05)
            gfx.paste_center(canvas, gfx.scaled(self.chip, max(0.01, 0.6 + 0.4 * q)), w / 2, h * 0.085,
                             alpha=min(1.0, q))
        if self.card is not None:
            q = _ease(t, 0.0, 0.5)
            card = _shadow(_rounded(self.card.frame(i), round(36 * U)), round(18 * U), round(14 * U))
            gfx.paste_center(canvas, card, w / 2, h * 0.24 + (1 - q) * 120 * U, alpha=q)
        q = gfx.ease_out_cubic(gfx.progress(t, 0.25, 0.9))
        shown = gfx.fmt_number(self.value * q, self.decimals if q >= 1 else (self.decimals if self.value < 20 else 0),
                               self.prefix, self.suffix)
        scale = 0.7 + 0.3 * gfx.ease_out_back(gfx.progress(t, 0.25, 0.4), 1.6)
        layer = self._num(shown)
        gfx.paste_center(canvas, gfx.scaled(layer, scale), w / 2, self.cy_num, alpha=min(1.0, t / 0.2))
        # barra que enche junto com o número
        bw = w * 0.62
        y = self.cy_num + layer.height / 2 + 18 * U
        d.rounded_rectangle([w / 2 - bw / 2, y, w / 2 + bw / 2, y + 16 * U], radius=round(8 * U),
                            fill=(255, 255, 255, 60))
        d.rounded_rectangle([w / 2 - bw / 2, y, w / 2 - bw / 2 + bw * max(0.02, q), y + 16 * U], radius=round(8 * U),
                            fill=(*self.acc, 255))
        if self.label is not None:
            ql = _ease(t, 0.7, 0.45)
            gfx.paste_center(canvas, self.label, w / 2, y + 40 * U + self.label.height / 2 + (1 - ql) * 30 * U,
                             alpha=ql)
        return canvas


# ------------------------------------------------------------------------------------------------ rank
class VRank(_V):
    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        U, w, h = self.U, self.w, self.h
        num = spec.number or {}
        if spec.kind == "steps":
            k = int(max(1, round(float(num.get("value") or spec.rank or 1))))
            n = int(max(k, round(float(num.get("total") or k))))
            big = f"{k}/{n}"
        else:
            big = f"#{spec.rank or int(num.get('value') or 1)}"
        self.big = gfx.outlined_text(big, gfx.font("heavy", round(300 * U)), (*self.acc, 255), round(14 * U),
                                     (0, 0, 0, 255))
        self.name = _title(spec.keyword or "", round(96 * U), round(w * 0.86), 3)
        sub = (spec.subtitle or "").upper() if spec.kind != "steps" else ""
        self.sub = _title(sub, round(42 * U), round(w * 0.8), 1, (215, 220, 232)) if sub else None
        self.card_w, self.card_h = round(w * 0.78), round(h * 0.2)
        self.card = self.media_card(0, self.card_w, self.card_h)

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        U, w, h = self.U, self.w, self.h
        canvas = self.base(i, t)
        q = self.pop(t, 0.0, 0.5)
        gfx.paste_center(canvas, gfx.scaled(self.big, max(0.01, 1.6 - 0.6 * min(1.0, q))), w / 2, h * 0.15,
                         alpha=min(1.0, q * 1.4))
        y = h * 0.15 + self.big.height / 2 + 10 * U
        if self.name is not None:
            qn = _ease(t, 0.3, 0.45)
            gfx.paste_center(canvas, self.name, w / 2, y + self.name.height / 2 + (1 - qn) * 40 * U, alpha=qn)
            y += self.name.height + 6 * U
        if self.sub is not None:
            gfx.paste_center(canvas, self.sub, w / 2, y + self.sub.height / 2, alpha=_ease(t, 0.5, 0.4))
            y += self.sub.height
        if self.card is not None:
            qc = _ease(t, 0.45, 0.5)
            card = _shadow(_rounded(self.card.frame(i), round(30 * U)), round(16 * U), round(12 * U))
            gfx.paste_center(canvas, card, w / 2, max(y + self.card_h / 2 + 24 * U, h * 0.44) + (1 - qc) * 90 * U,
                             alpha=qc)
        return canvas


# ---------------------------------------------------------------------------------------------- versus
class VVersus(_V):
    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        U, w, h = self.U, self.w, self.h
        self.n = max(2, min(3, len(spec.media) or 2))
        top, bottom = h * 0.05, h * 0.56
        gap = 18 * U
        self.ph = round((bottom - top - gap * (self.n - 1)) / self.n)
        self.pw = round(w * 0.9)
        self.ys = [top + k * (self.ph + gap) for k in range(self.n)]
        self.panels = [self.media_card(k, self.pw, self.ph, 1.08) for k in range(self.n)]
        labels = list(spec.labels or [])
        stats = spec.stats or []
        if spec.kind == "versus" and stats and len(labels) >= 2:  # o número de cada um junto do nome
            st = stats[0]
            unit = str(st.get("unit", "")).strip()
            vals = [str(st.get("value", "")).strip(), str(st.get("value_b", "")).strip()]
            labels = [f"{lb} · {v} {unit}".strip() if v else lb for lb, v in zip(labels, vals)] + labels[2:]
        self.chips = [_chip(str(lb).upper()[:26], round(44 * U), self.acc if k == 0 else WHITE) if lb else None
                      for k, lb in enumerate(labels[: self.n])] + [None] * self.n
        f = gfx.font("heavy", round(70 * U))
        self.vs = gfx.outlined_text("VS", f, (*WHITE, 255), round(5 * U), (0, 0, 0, 255))

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        U, w, h = self.U, self.w, self.h
        canvas = self.base(i, t)
        for k in range(self.n):
            q = _ease(t, 0.05 + 0.18 * k, 0.5)
            src = self.panels[k]
            img = src.frame(i) if src is not None else Image.new("RGB", (self.pw, self.ph), (30, 34, 46))
            card = _shadow(_rounded(img, round(30 * U)), round(14 * U), round(10 * U))
            side = -1 if k % 2 == 0 else 1
            cx = w / 2 + side * (1 - q) * w * 0.9
            cy = self.ys[k] + self.ph / 2
            gfx.paste_center(canvas, card, cx, cy, alpha=min(1.0, 0.2 + q))
            chip = self.chips[k]
            if chip is not None:
                qc = _ease(t, 0.35 + 0.18 * k, 0.35)
                gfx.paste_at(canvas, chip, (w - self.pw) / 2 + 26 * U, self.ys[k] + self.ph - chip.height - 22 * U,
                             alpha=qc)
        if self.n >= 2:
            qv = self.pop(t, 0.45, 0.4)
            cy = self.ys[1] - 9 * U
            r = 82 * U
            disc = Image.new("RGBA", (round(2 * r), round(2 * r)), (0, 0, 0, 0))
            ImageDraw.Draw(disc).ellipse([0, 0, disc.width - 1, disc.height - 1], fill=(*self.acc, 255),
                                         outline=(0, 0, 0, 255), width=max(3, round(6 * U)))
            gfx.paste_center(disc, self.vs, r, r)
            gfx.paste_center(canvas, gfx.scaled(disc, max(0.01, min(1.15, qv))), w / 2, cy, alpha=min(1.0, qv * 2))
        return canvas


# ------------------------------------------------------------------------------------------------ lista
class VList(_V):
    MAX = 5

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        U, w, h = self.U, self.w, self.h
        items: list[tuple[str, str, bool | None]] = []  # (texto, valor à direita, positivo?)
        for p in spec.points or []:
            items.append((str(p.get("text", "")), "", p.get("positive") if "positive" in p else None))
        if not items:
            for st in spec.stats or []:
                val = f"{st.get('value', '')} {st.get('unit', '')}".strip()
                items.append((str(st.get("label", "")), val, None))
        if not items:
            notes = list(spec.notes or [])
            for k, lb in enumerate(spec.labels or []):
                items.append((str(lb), notes[k] if k < len(notes) else "", None))
        self.items = [x for x in items if x[0] or x[1]][: self.MAX]
        title = spec.keyword or ""
        self.title = _title(title.upper(), round(78 * U), round(w * 0.86), 2)
        self.f_item = gfx.font("heavy_x", round(50 * U))
        self.f_val = gfx.font("heavy", round(54 * U))
        self.f_num = gfx.font("heavy", round(42 * U))
        self.row_h = round(min(150 * U, (h * 0.42) / max(1, len(self.items))))
        self.row_w = round(w * 0.88)
        self.top = h * 0.07 + (self.title.height + 34 * U if self.title is not None else 0)
        self.rows = [self._row(k, *it) for k, it in enumerate(self.items)]
        self.starts = self.times(len(self.rows), 0.3, 0.55)

    def _row(self, k: int, text: str, value: str, positive: bool | None) -> Image.Image:
        U = self.U
        rh, rw = self.row_h - round(16 * U), self.row_w
        img = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, rw - 1, rh - 1], radius=round(26 * U), fill=(12, 14, 20, 205))
        r = rh * 0.32
        cx, cy = 22 * U + r, rh / 2
        if positive is None:
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*self.acc, 255))
            num = str(k + 1)
            tw = gfx.text_width(num, self.f_num)
            asc, desc = self.f_num.getmetrics()
            d.text((cx - tw / 2, cy - (asc + desc) / 2), num, font=self.f_num, fill=(*DARK, 255))
        else:
            col = (64, 200, 110) if positive else (235, 70, 70)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*col, 255))
            lw = max(3, round(7 * U))
            if positive:
                d.line([(cx - r * 0.45, cy), (cx - r * 0.1, cy + r * 0.38), (cx + r * 0.5, cy - r * 0.4)],
                       fill=(255, 255, 255, 255), width=lw, joint="curve")
            else:
                d.line([(cx - r * 0.4, cy - r * 0.4), (cx + r * 0.4, cy + r * 0.4)], fill=(255, 255, 255, 255), width=lw)
                d.line([(cx - r * 0.4, cy + r * 0.4), (cx + r * 0.4, cy - r * 0.4)], fill=(255, 255, 255, 255), width=lw)
        x0 = cx + r + 22 * U
        val_w = gfx.text_width(value, self.f_val) + 30 * U if value else 0
        lines = gfx.wrap(text, self.f_item, round(rw - x0 - val_w - 20 * U), 2)
        asc, desc = self.f_item.getmetrics()
        lh = (asc + desc) * 1.02
        y = rh / 2 - lh * len(lines) / 2
        for line in lines:
            d.text((x0, y), gfx.safe_text(line), font=self.f_item, fill=(*WHITE, 255))
            y += lh
        if value:
            asc, desc = self.f_val.getmetrics()
            d.text((rw - val_w, rh / 2 - (asc + desc) / 2), gfx.safe_text(value), font=self.f_val, fill=(*self.acc, 255))
        return img

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        U, w = self.U, self.w
        canvas = self.base(i, t)
        if self.title is not None:
            q = _ease(t, 0.0, 0.4)
            gfx.paste_center(canvas, self.title, w / 2, self.h * 0.07 + self.title.height / 2 + (1 - q) * 30 * U,
                             alpha=q)
        for k, row in enumerate(self.rows):
            q = _ease(t, self.starts[k], 0.4)
            if q <= 0:
                continue
            x = (w - self.row_w) / 2 + (1 - q) * 160 * U
            gfx.paste_at(canvas, row, x, self.top + k * self.row_h, alpha=q)
        return canvas


# ----------------------------------------------------------------------------------------------- pilha
class VStack(_V):
    SLIDE = 0.45

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        U, w, h = self.U, self.w, self.h
        self.n = max(1, min(3, len(spec.media)))
        self.cw, self.ch = round(w * 0.84), round(h * 0.34)
        self.cards = [self.media_card(k, self.cw, self.ch, 1.08) for k in range(self.n)]
        self.angles = [float(self.rng.uniform(-4, 4)) for _ in range(self.n)]
        self.slots = [self.total * k / self.n for k in range(self.n)]
        kw = (spec.keyword or "").upper()
        self.title = _title(kw, round(84 * U), round(w * 0.86), 2) if kw else None
        sub = spec.subtitle or ""
        self.sub = _title(sub, round(46 * U), round(w * 0.82), 2, (225, 230, 240)) if sub else None

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        U, w, h = self.U, self.w, self.h
        canvas = self.base(i, t)
        cy = h * 0.32
        front = max(k for k in range(self.n) if t >= self.slots[k] - 0.01)
        # cartas de trás (as próximas) aparecendo inclinadas; a da frente sai voando para cima na troca
        for k in range(self.n - 1, -1, -1):
            src = self.cards[k]
            if src is None:
                continue
            if k < front:
                out = _ease(t, self.slots[k + 1] - 0.05, self.SLIDE)
                if out >= 1:
                    continue
                dy, alpha, ang = -out * h * 0.5, 1 - out, self.angles[k] - 10 * out
            elif k == front:
                q = _ease(t, self.slots[k], self.SLIDE) if k > 0 else _ease(t, 0.0, 0.5)
                dy, alpha, ang = (1 - q) * 80 * U, min(1.0, 0.4 + q), self.angles[k] * (1 - q) * 0.4
            else:
                depth = k - front
                dy, alpha, ang = -depth * 26 * U, 0.75, self.angles[k]
            img = _rounded(src.frame(i), round(34 * U))
            card = _shadow(img, round(18 * U), round(14 * U))
            if abs(ang) > 0.2:
                card = card.rotate(ang, resample=Image.BICUBIC, expand=True)
            scale = 1.0 if k <= front else 0.94 ** (k - front)
            gfx.paste_center(canvas, gfx.scaled(card, scale), w / 2, cy + dy, alpha=alpha)
        y = cy - self.ch / 2 - 30 * U
        if self.title is not None:
            q = _ease(t, 0.15, 0.4)
            gfx.paste_center(canvas, self.title, w / 2, max(self.title.height / 2 + 40 * U, y - self.title.height / 2),
                             alpha=q)
        if self.sub is not None:
            q = _ease(t, 0.4, 0.4)
            gfx.paste_center(canvas, self.sub, w / 2, cy + self.ch / 2 + 40 * U + self.sub.height / 2, alpha=q)
        return canvas


# ----------------------------------------------------------------------------------------------- frase
class VQuote(_V):
    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        U, w, h = self.U, self.w, self.h
        self.r = round(150 * U)
        self.photo = self.media_card(0, 2 * self.r, 2 * self.r, 1.05) if spec.kind != "warning" else None
        text = spec.body or " ".join(spec.labels or []) or spec.subtitle or ""
        if spec.kind == "quote" and spec.labels:
            text = " ".join(x for x in [spec.keyword, *spec.labels] if x)
            name = spec.subtitle or ""
        else:
            name = spec.keyword or ""
        self.name = _title(name.upper(), round(62 * U), round(w * 0.84), 2) if name else None
        f = gfx.font("serif", round(66 * U))
        self.f_q = f
        self.lines = gfx.wrap(f"“{text.strip()}”" if text and spec.kind != "warning" else text, f, round(w * 0.84), 5)
        self.words = [ln.split() for ln in self.lines]
        self.warn = spec.kind in ("warning", "unverified")
        self.badge = _chip({"warning": "⚠ ATENÇÃO", "unverified": "NÃO CONFIRMADO"}.get(spec.kind, ""),
                           round(42 * U), (235, 70, 70), WHITE) if self.warn else None

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        U, w, h = self.U, self.w, self.h
        canvas = self.base(i, t)
        cy = h * 0.14
        r = self.r
        q = self.pop(t, 0.0, 0.5)
        if self.photo is not None:
            disc = self.photo.frame(i).convert("RGBA")
            m = Image.new("L", disc.size, 0)
            ImageDraw.Draw(m).ellipse([0, 0, disc.width - 1, disc.height - 1], fill=255)
            disc.putalpha(m)
            ring = Image.new("RGBA", (2 * r + 24, 2 * r + 24), (0, 0, 0, 0))
            ring.alpha_composite(disc, (12, 12))
            ImageDraw.Draw(ring).ellipse([4, 4, 2 * r + 19, 2 * r + 19], outline=(*self.acc, 255),
                                         width=max(4, round(9 * U)))
            gfx.paste_center(canvas, gfx.scaled(ring, max(0.01, min(1.1, q))), w / 2, cy, alpha=min(1.0, q * 1.5))
            y = cy + r + 30 * U
        else:
            y = h * 0.08
        if self.badge is not None:
            gfx.paste_center(canvas, self.badge, w / 2, y + self.badge.height / 2, alpha=_ease(t, 0.1, 0.3))
            y += self.badge.height + 24 * U
        if self.name is not None:
            gfx.paste_center(canvas, self.name, w / 2, y + self.name.height / 2, alpha=_ease(t, 0.25, 0.4))
            y += self.name.height + 30 * U
        # a frase entra palavra por palavra
        total_words = sum(len(x) for x in self.words) or 1
        shown = int(total_words * gfx.clamp01((t - 0.45) / max(0.6, min(2.4, self.total * 0.55))) + 0.999)
        asc, desc = self.f_q.getmetrics()
        lh = (asc + desc) * 1.08
        d = ImageDraw.Draw(canvas)
        count = 0
        for line_words in self.words:
            parts = []
            for wd in line_words:
                if count < shown:
                    parts.append(wd)
                count += 1
            if parts:
                full = " ".join(line_words)
                x0 = w / 2 - gfx.text_width(full, self.f_q) / 2
                d.text((x0, y), gfx.safe_text(" ".join(parts)), font=self.f_q, fill=(*WHITE, 255),
                       stroke_width=max(2, round(3 * U)), stroke_fill=(0, 0, 0, 255))
            y += lh
        return canvas


# ------------------------------------------------------------------------------------------------ cartão
class Card(Template):
    """Template de 16:9 como cartão na parte de cima da tela em pé, sobre a própria imagem desfocada."""

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        self.U = self.w / 1080
        iw = self.w
        ih = round(iw * 9 / 16) // 2 * 2
        inner = TemplateSpec.from_dict({**spec.__dict__, "media": [m.__dict__ for m in spec.media], "w": iw, "h": ih})
        self.inner = TEMPLATES[spec.kind](inner)
        self.iw, self.ih = iw, ih
        self.radius = round(28 * self.U)

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        fr = self.inner.frame(i)
        if fr.size != (self.iw, self.ih):
            fr = fr.resize((self.iw, self.ih), Image.BILINEAR)
        fr = fr.convert("RGB")
        # fundo: o próprio quadro, pequeno, desfocado e ampliado (barato), escurecido
        small = fr.resize((max(8, self.iw // 12), max(8, self.ih // 12)), Image.BILINEAR)
        sw = small.height * self.w / self.h
        x0 = (small.width - sw) / 2
        small = small.crop((round(x0), 0, round(x0 + sw), small.height)).filter(ImageFilter.GaussianBlur(2))
        bg = Image.fromarray((np.asarray(small, np.float32) * 0.45).clip(0, 255).astype(np.uint8))
        canvas = bg.resize((self.w, self.h), Image.BILINEAR).convert("RGBA")
        q = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.35))
        card = _shadow(_rounded(fr, self.radius), round(18 * self.U), round(12 * self.U))
        gfx.paste_center(canvas, gfx.scaled(card, 0.96 + 0.04 * q), self.w / 2, self.h * 0.31 + (1 - q) * 60 * self.U,
                         alpha=min(1.0, 0.3 + q))
        return canvas

    def close(self) -> None:
        self.inner.close()
        super().close()


CLASSES = {"number": VNumber, "rank": VRank, "versus": VVersus, "list": VList, "stack": VStack, "quote": VQuote}


def vertical_kind(kind: str) -> str:
    """Como o template entra num vídeo em pé: "safe" (o próprio), "native:<família>" ou "card"."""
    if kind in VERTICAL_SAFE:
        return "safe"
    if kind in NATIVE:
        return "native:" + NATIVE[kind]
    return "card"


def make_template(spec: TemplateSpec) -> Template:
    if spec.h <= spec.w:
        return TEMPLATES[spec.kind](spec)
    how = vertical_kind(spec.kind)
    if how == "safe":
        return TEMPLATES[spec.kind](spec)
    if how.startswith("native:"):
        return CLASSES[how.split(":", 1)[1]](spec)
    return Card(spec)

