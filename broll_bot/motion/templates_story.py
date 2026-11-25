"""Templates de "reportagem investigativa": quem disse, quanto cresceu, o que a garantia cobre, o passo a
passo, o que ninguém confirmou e o carro desmontado. Todos com a cara do estilo do vídeo (``skin``, as
mesmas peles de ``templates_magnet``: papel no Dossiê, estúdio no Broadcast, grade nos Dados...).

* ``source``     — a fonte do dado: "SOURCE" + monograma, o nome (``keyword``: "UBS", "Thatcham Research"),
  o que ela é (``subtitle``: "Swiss investment bank · 2023") e o trecho da fala entre aspas (``body``).
  No papel vira um recorte de jornal com carimbo "FONTE".
* ``fromto``     — crescimento/queda: "from five showrooms to over 140" (``number``: ``from`` -> ``value``),
  o número contando de um ao outro, as duas barras e o "×28".
* ``coverage``   — o que a garantia cobre: uma barra por item (``points``: text, value = anos, detail =
  milhas/km) crescendo numa régua de anos.
* ``steps``      — dica numerada do roteiro ("One. Get the insurance quote before you sign."): o número
  gigante, "PASSO 1 DE 5", a barra de progresso e a caixinha que ganha o visto.
* ``unverified`` — honestidade do roteiro ("I have not been able to confirm…", "we do not know yet"): o
  que não se sabe (``keyword``) e o carimbo "NÃO CONFIRMADO" / "SEM DADOS AINDA" / "ESTIMATIVA" batendo.
* ``exploded``   — desmontagem ("taken apart down to the last bolt"): a foto do carro se separando em
  pedaços, com as peças citadas (``points``) puxadas por linhas.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw

from . import gfx
from .i18n import text as tr
from .templates import TemplateSpec
from .templates_magnet import WHITE, Magnet, Skin, big_number, label

Cue = tuple[float, str, float]
RED = (214, 40, 40)
PAPER_RED = (196, 46, 34)
AMBER = (240, 160, 20)


def _num(spec: TemplateSpec, key: str = "value", default: float = 0.0) -> float:
    try:
        return float((spec.number or {}).get(key) or default)
    except (TypeError, ValueError):
        return default


def chip(skin: Skin, text: str, u: float, size: float = 34) -> Image.Image:
    """Rótulo de seção ("FONTE", "PASSO 1 DE 5") no jeito da pele."""
    text = skin.txt(text)
    if skin.name == "paper":
        return rubber_stamp(text, u, PAPER_RED, size * 1.8)
    f = gfx.font("oswald_bold" if skin.name in ("tv", "data", "cinema") else skin.title, round(size * u))
    bg = RED if skin.name == "tv" else skin.accent
    fg = WHITE if skin.name in ("tv",) or sum(bg) < 400 else (12, 12, 12)
    if skin.name == "cinema":
        bg, fg = (0, 0, 0), skin.accent
    return gfx.chip(text, f, bg, fg, round(900 * u), round(18 * u), round(8 * u))


def rubber_stamp(text: str, u: float, color: tuple[int, int, int], size: float = 120) -> Image.Image:
    """Carimbo com moldura e falhas de tinta, na cor pedida (o de ``templates_paper`` é sempre vermelho)."""
    f = gfx.font("oswald_bold", round(size * u))
    tw = gfx.text_width(text, f)
    asc, desc = f.getmetrics()
    pad = round(26 * u * size / 120)
    img = Image.new("RGBA", (tw + 2 * pad, asc + desc + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lw = max(3, round(9 * u * size / 120))
    d.rounded_rectangle([lw / 2, lw / 2, img.width - lw / 2, img.height - lw / 2], radius=round(14 * u),
                        outline=(*color, 235), width=lw)
    d.text((pad, pad - desc * 0.4), text, font=f, fill=(*color, 235))
    rng = np.random.default_rng(len(text) * 13 + round(size))
    alpha = np.asarray(img.getchannel("A"), np.float32)
    alpha[rng.random(alpha.shape) < 0.12] *= 0.35
    img.putalpha(Image.fromarray(alpha.astype(np.uint8)))
    return img


def _panel(skin: Skin, w: int, h: int, u: float) -> Image.Image:
    """Caixa de fundo dos textos: recorte de jornal no papel, vidro escuro na TV, caixa da pele no resto."""
    if skin.name == "paper":
        img = Image.new("RGBA", (w, h), (246, 241, 228, 255))
        d = ImageDraw.Draw(img)
        rng = np.random.default_rng(w + h)
        step = max(6, round(14 * u))
        for edge_y, sign in ((0, 1), (h, -1)):  # borda rasgada em cima e embaixo
            pts = [(x, edge_y + sign * float(rng.uniform(0, 7)) * u) for x in range(0, w + step, step)]
            poly = [(0, edge_y), *pts, (w, edge_y)]
            d.polygon(poly, fill=(0, 0, 0, 0))
        return gfx.with_shadow(img, 0, round(14 * u), (round(6 * u), round(12 * u)), 110)
    fill = skin.panel if skin.name != "kinetic" else (16, 16, 16, 240)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=round((4 if skin.name in ("tv", "data") else 18) * u), fill=fill)
    if skin.name in ("tv", "blueprint", "data"):
        d.rectangle([0, 0, max(4, round(8 * u)), h], fill=(*skin.accent, 255))
    if skin.name == "tv":
        d.rectangle([0, h - max(3, round(6 * u)), w, h], fill=(*skin.accent, 255))
    return img


def _words_layout(text: str, f, max_w: int, max_lines: int) -> tuple[list[tuple[str, float, int]], int]:
    """Palavras com (x, linha) para aparecerem uma a uma."""
    lines = gfx.wrap(text, f, max_w, max_lines)
    out: list[tuple[str, float, int]] = []
    space = gfx.text_width(" ", f)
    for k, ln in enumerate(lines):
        x = 0.0
        for wd in ln.split():
            out.append((wd, x, k))
            x += gfx.text_width(wd, f) + space
    return out, len(lines)


def _initials(name: str) -> str:
    words = [w for w in name.replace("-", " ").split() if w[:1].isalnum()]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:3].upper() if words[0].isupper() else words[0][:1].upper()
    caps = [w[0].upper() for w in words if w[0].isupper()] or [w[0].upper() for w in words]
    return "".join(caps[:3])


# ------------------------------------------------------------------ source
class Source(Magnet):
    """A fonte do dado: rótulo "FONTE", monograma com as iniciais, o nome (``keyword``), o que ela é
    (``subtitle``) e o trecho da fala entre aspas (``body``) aparecendo palavra por palavra."""

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, sk = self.u, self.w, self.sk
        self.pw = round(w * 0.8)
        self.pad = round(60 * u)
        name = " ".join((spec.keyword or "").split())
        mono_r = round(92 * u)
        self.mono = Image.new("RGBA", (2 * mono_r, 2 * mono_r), (0, 0, 0, 0))
        md = ImageDraw.Draw(self.mono)
        ring = PAPER_RED if sk.name == "paper" else sk.accent
        md.ellipse([0, 0, 2 * mono_r - 1, 2 * mono_r - 1], fill=(*ring, 255))
        ini = _initials(name)
        fm = gfx.font("black" if sk.name != "paper" else "typewriter", round((70 if len(ini) < 3 else 52) * u))
        asc, desc = fm.getmetrics()
        dark = sum(ring) > 450
        md.text((mono_r - gfx.text_width(ini, fm) / 2, mono_r - (asc + desc) / 2), ini, font=fm,
                fill=(12, 12, 12, 255) if dark else (*WHITE, 255))
        ink = sk.ink if sk.name != "paper" else (40, 34, 30)
        tx = self.pad + 2 * mono_r + round(40 * u)
        avail = self.pw - tx - self.pad
        size = 96
        fn = sk.font("title", size, u)
        while gfx.text_width(sk.txt(name), fn) > avail and size > 52:
            size -= 6
            fn = sk.font("title", size, u)
        self.name = gfx.text_block(gfx.wrap(sk.txt(name), fn, avail, 2), fn, ink, shadow=0) if name else None
        fs = sk.font("body", 36, u)
        sub = " ".join((spec.subtitle or "").split())
        self.sub = gfx.text_block(gfx.wrap(sub, fs, avail, 1), fs, sk.muted if sk.name != "paper" else (110, 98, 88),
                                  shadow=0) if sub else None
        self.tx = tx
        self.head_h = max(2 * mono_r, (self.name.height if self.name else 0) + (self.sub.height + 14 * u if self.sub else 0))
        body = " ".join((spec.body or "").split())
        if body and not body.startswith(("“", '"', "«")):
            body = f"“{body.rstrip('.')}”"
        self.f_body = gfx.font("typewriter" if sk.name == "paper" else ("montserrat" if sk.name != "kinetic" else "bold"),
                               round(54 * u))
        self.words, nl = _words_layout(body, self.f_body, self.pw - 2 * self.pad, 3)
        asc, desc = self.f_body.getmetrics()
        self.lh = round((asc + desc) * 1.18)
        # o painel do tamanho do conteúdo (cabeçalho, filete e a citação)
        self.ph = round(2 * self.pad + self.head_h + (70 * u + nl * self.lh if self.words else 0))
        self.base = _panel(sk, self.pw, self.ph, u)
        self.ink = ink
        self.tag = chip(sk, tr("source", spec.lang), u)
        n = max(1, len(self.words))
        self.step = min(0.12, max(0.03, (self.dur * 0.45) / n))

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = len((spec.body or "").split())
        return [(0.0, "whoosh_soft", 0.35), (0.45, "impact_soft", 0.3), (0.8, f"typing:{min(2.0, 0.08 * n):.3f}", 0.25)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h, sk = self.u, self.w, self.h, self.sk
        c = self.canvas(i)
        q = gfx.ease_out_cubic(gfx.progress(t, 0.05, 0.45))
        panel = self.base.copy()
        d = ImageDraw.Draw(panel)
        qm = gfx.ease_out_back(gfx.progress(t, 0.3, 0.4), 1.6)
        top = self.pad
        if qm > 0:
            mono = gfx.scaled(self.mono, max(0.05, qm))
            panel.alpha_composite(mono, (round(self.pad + self.mono.width / 2 - mono.width / 2),
                                         round(top + self.mono.height / 2 - mono.height / 2)))
        qn = gfx.ease_out_cubic(gfx.progress(t, 0.45, 0.35))
        y = top + (self.head_h - (self.name.height if self.name else 0) - (self.sub.height + 14 * u if self.sub else 0)) / 2
        if self.name is not None and qn > 0:
            gfx.paste_at(panel, self.name, self.tx - (1 - qn) * 40 * u, y, alpha=qn)
            y += self.name.height + 14 * u
        if self.sub is not None and qn > 0:
            gfx.paste_at(panel, self.sub, self.tx, y, alpha=gfx.progress(t, 0.6, 0.3))
        ly = top + self.head_h + 34 * u
        ql = gfx.ease_out_cubic(gfx.progress(t, 0.6, 0.4))
        if ql > 0:
            col = (*(PAPER_RED if sk.name == "paper" else sk.accent), 255)
            d.rectangle([self.pad, ly, self.pad + (self.pw - 2 * self.pad) * ql, ly + max(2, round(4 * u))], fill=col)
        by = ly + 36 * u
        for k, (wd, x, line) in enumerate(self.words):
            qw = gfx.progress(t, 0.8 + k * self.step, 0.14)
            if qw <= 0:
                break
            d.text((self.pad + x, by + line * self.lh + (1 - qw) * 10 * u), gfx.safe_text(wd), font=self.f_body,
                   fill=(*self.ink, round(255 * qw)))
        angle = -1.2 if sk.name == "paper" else 0.0
        if angle:
            panel = panel.rotate(angle, expand=True, resample=Image.BICUBIC)
        gfx.paste_center(c, panel, w / 2, h * 0.53 + (1 - q) * 60 * u, alpha=q)
        qt = gfx.ease_out_back(gfx.progress(t, 0.25 if sk.name != "paper" else 1.1, 0.3), 1.4)
        if qt > 0:
            tag = self.tag if sk.name != "paper" else gfx.scaled(self.tag.rotate(8, expand=True), 1.5 - 0.5 * qt)
            x = w / 2 - self.pw / 2 if sk.name != "paper" else w / 2 + self.pw / 2 - tag.width * 0.8
            yt = h * 0.53 - self.ph / 2 - tag.height - 16 * u if sk.name != "paper" else h * 0.53 - self.ph / 2 - tag.height / 2
            gfx.paste_at(c, tag, x, yt, alpha=min(1.0, qt * 2))
        return self.done(c, t)


# ------------------------------------------------------------------ fromto
class FromTo(Magnet):
    """Crescimento/queda dito na fala: o número contando de ``number.from`` até ``number.value``, as duas
    barras (antes/depois) e o multiplicador; ``keyword`` diz o que é ("showrooms")."""

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, sk = self.u, self.w, self.sk
        self.a, self.b = _num(spec, "from"), _num(spec)
        n = spec.number or {}
        self.prefix, self.suffix = str(n.get("prefix") or ""), str(n.get("suffix") or "")
        self.up = self.b >= self.a
        hi = max(self.a, self.b, 1.0)
        self.ra, self.rb = self.a / hi, self.b / hi
        if self.up and self.a > 0 and self.b / self.a >= 2:
            mult = self.b / self.a
            self.badge = f"×{mult:.0f}" if mult >= 10 else f"×{mult:.1f}".replace(".0", "")
        elif self.a > 0:
            self.badge = f"{(self.b - self.a) / self.a * 100:+.0f}%"
        else:
            self.badge = ""
        self.lab = label(sk, spec.keyword, u, w * 0.42, 42)
        self.sub = label(sk, spec.subtitle, u, w * 0.42, 30) if spec.subtitle else None
        self.f_from = sk.font("title", 64, u)
        self.head = chip(sk, tr("growth" if self.up else "drop", spec.lang), u, 30)

    def _fmt(self, v: float) -> str:
        return gfx.fmt_number(round(v), 0, self.prefix, self.suffix)

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        return [(0.0, "whoosh_soft", 0.35), (0.3, "tick", 0.2), (0.7, "tick", 0.2), (1.1, "tick", 0.2),
                (1.45, "impact_soft", 0.45)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h, sk = self.u, self.w, self.h, self.sk
        c = self.canvas(i)
        x0 = w * 0.07
        qh = gfx.ease_out_cubic(gfx.progress(t, 0.05, 0.3))
        gfx.paste_at(c, self.head, x0 - (1 - qh) * 50 * u, h * 0.14, alpha=qh)
        # "5 →" pequeno, riscado quando o número grande chega
        qf = gfx.progress(t, 0.15, 0.3)
        ink = sk.muted if sk.name != "paper" else (110, 98, 88)
        small = gfx.text_block([self._fmt(self.a)], self.f_from, ink, shadow=0)
        sy = h * 0.14 + self.head.height + 30 * u
        gfx.paste_at(c, small, x0, sy, alpha=qf)
        if qf > 0:  # seta desenhada (a fonte não tem "→")
            d = ImageDraw.Draw(c)
            ax, ay = x0 + small.width + 24 * u, sy + small.height * 0.55
            col = (*ink, round(255 * qf))
            d.line([(ax, ay), (ax + 70 * u, ay)], fill=col, width=max(3, round(6 * u)))
            d.polygon([(ax + 86 * u, ay), (ax + 62 * u, ay - 16 * u), (ax + 62 * u, ay + 16 * u)], fill=col)
        qs = gfx.progress(t, 1.45, 0.25)
        if qs > 0:
            d = ImageDraw.Draw(c)
            ym = sy + small.height * 0.55
            d.line([(x0, ym), (x0 + gfx.text_width(self._fmt(self.a), self.f_from) * qs, ym)],
                   fill=(*(PAPER_RED if sk.name == "paper" else RED), 255), width=max(3, round(7 * u)))
        q = gfx.ease_in_out(gfx.progress(t, 0.3, 1.15))
        num = big_number(sk, self._fmt(self.a + (self.b - self.a) * q), u, 200)
        ny = sy + small.height + 6 * u
        gfx.paste_at(c, num, x0, ny, alpha=gfx.progress(t, 0.25, 0.2))
        ly = ny + num.height + 26 * u
        if self.lab is not None:
            ql = gfx.ease_out_cubic(gfx.progress(t, 0.7, 0.35))
            gfx.paste_at(c, self.lab, x0 - (1 - ql) * 50 * u, ly, alpha=ql)
            ly += self.lab.height + 14 * u
        if self.sub is not None:
            gfx.paste_at(c, self.sub, x0, ly, alpha=gfx.progress(t, 0.9, 0.3))
        # as duas barras (antes, depois) à direita
        base_y, top_y = h * 0.8, h * 0.2
        bw = w * 0.11
        xs = (w * 0.6, w * 0.78)
        d = ImageDraw.Draw(c)
        axis = (*sk.ink, 120) if sk.name != "paper" else (52, 44, 38, 160)
        d.line([(w * 0.56, base_y), (w * 0.94, base_y)], fill=axis, width=max(2, round(3 * u)))
        for k, (r, x) in enumerate(zip((self.ra, self.rb), xs)):
            qb = gfx.ease_out_cubic(gfx.progress(t, 0.2 + k * 0.4, 0.9 if k else 0.4))
            bh = max(6 * u, (base_y - top_y) * r * qb)
            col = (*sk.accent, 255) if k == 1 else ((*sk.muted, 200) if sk.name != "paper" else (150, 140, 128, 220))
            if sk.name == "paper":
                col = (*PAPER_RED, 235) if k == 1 else col
            d.rectangle([x, base_y - bh, x + bw, base_y], fill=col)
            if qb > 0.95:
                f = sk.font("body", 32, u)
                txt = self._fmt(self.a if k == 0 else self.b)
                d.text((x + bw / 2 - gfx.text_width(txt, f) / 2, base_y - bh - 44 * u), txt, font=f,
                       fill=(*(sk.ink if sk.name != "paper" else (52, 44, 38)), 255))
        if self.badge:
            qb = gfx.ease_out_back(gfx.progress(t, 1.5, 0.35), 1.8)
            if qb > 0:
                badge = gfx.chip(self.badge, sk.font("title", 58, u), RED if sk.name == "tv" else sk.accent,
                                 WHITE if sk.name == "tv" or sum(sk.accent) < 400 else (12, 12, 12),
                                 round(400 * u), round(20 * u), round(10 * u))
                # entre as duas barras, na altura do topo da maior
                big_top = base_y - (base_y - top_y) * max(self.ra, self.rb)
                gfx.paste_center(c, gfx.scaled(badge, max(0.05, qb)), (xs[0] + bw + xs[1]) / 2,
                                 big_top + badge.height * 0.8)
        return self.done(c, t)


# ---------------------------------------------------------------- coverage
def shield(size: int, color: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size
    d.polygon([(s * 0.5, s * 0.04), (s * 0.92, s * 0.2), (s * 0.86, s * 0.6), (s * 0.5, s * 0.96), (s * 0.14, s * 0.6),
               (s * 0.08, s * 0.2)], fill=(*color, 255))
    lw = max(2, s // 10)
    d.line([(s * 0.3, s * 0.5), (s * 0.45, s * 0.66), (s * 0.72, s * 0.34)], fill=(255, 255, 255, 255), width=lw)
    return img


class Coverage(Magnet):
    """O que a garantia cobre: uma barra por item (``points``: ``text``, ``value`` = anos, ``detail`` =
    milhas/km) crescendo numa régua de anos, com o escudo no fim; ``keyword`` no título."""

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, sk = self.u, self.w, self.sk
        rows = []
        for p in (spec.points or [])[:4]:
            try:
                years = float(p.get("value") or 0)
            except (TypeError, ValueError):
                years = 0.0
            if years > 0:
                rows.append((str(p.get("text") or ""), years, str(p.get("detail") or "")))
        self.rows = rows
        self.max_y = max([r[1] for r in rows] + [1.0])
        self.span = math.ceil(self.max_y) + (1 if self.max_y < 10 else 0)
        title = " ".join((spec.keyword or tr("warranty", spec.lang)).split())
        self.title = chip(sk, title, u, 40)
        self.f_row = sk.font("body", 36, u)
        self.f_val = sk.font("title", 44, u)
        self.f_det = gfx.font("montserrat" if sk.name != "paper" else "typewriter", round(30 * u))
        self.f_tick = sk.font("body", 26, u)
        self.years = tr("years", spec.lang)
        self.lx = w * 0.07
        self.bx0, self.bx1 = w * 0.3, w * 0.9

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = min(4, len(spec.points or []))
        return [(0.0, "whoosh_soft", 0.35)] + [(0.5 + k * 0.45, "swipe", 0.25) for k in range(n)] + \
               [(0.9 + k * 0.45, "pop", 0.2) for k in range(n)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, h, sk = self.u, self.h, self.sk
        c = self.canvas(i)
        qt = gfx.ease_out_cubic(gfx.progress(t, 0.05, 0.35))
        gfx.paste_at(c, self.title, self.lx - (1 - qt) * 60 * u, h * 0.12, alpha=qt)
        n = max(1, len(self.rows))
        top = h * 0.3
        row_h = min(170 * u, (h * 0.58) / n)
        ink = sk.ink if sk.name != "paper" else (52, 44, 38)
        muted = sk.muted if sk.name != "paper" else (110, 98, 88)
        over = Image.new("RGBA", c.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(over)
        qa = gfx.progress(t, 0.2, 0.4)
        span_w = self.bx1 - self.bx0
        for y in range(self.span + 1):  # régua de anos
            x = self.bx0 + span_w * y / self.span
            od.line([(x, top - 20 * u), (x, top + row_h * n)], fill=(*ink, round(40 * qa)), width=1)
            txt = str(y)
            od.text((x - gfx.text_width(txt, self.f_tick) / 2, top + row_h * n + 10 * u), txt, font=self.f_tick,
                    fill=(*muted, round(220 * qa)))
        c.alpha_composite(over)
        d = ImageDraw.Draw(c)
        for k, (text, years, detail) in enumerate(self.rows):
            y = top + k * row_h
            bh = min(64 * u, row_h * 0.5)
            q = gfx.ease_out_cubic(gfx.progress(t, 0.5 + k * 0.45, 0.6))
            if q <= 0:
                continue
            lab = sk.txt(text)
            lines = gfx.wrap(lab, self.f_row, round(self.bx0 - self.lx - 30 * u), 2)
            asc, desc = self.f_row.getmetrics()
            for j, ln in enumerate(lines):
                d.text((self.lx, y + bh / 2 - (asc + desc) * len(lines) / 2 + j * (asc + desc)), ln, font=self.f_row,
                       fill=(*ink, round(255 * min(1.0, q * 2))))
            bw = span_w * years / self.span * q
            col = PAPER_RED if sk.name == "paper" else sk.accent
            d.rounded_rectangle([self.bx0, y, self.bx0 + max(bh, bw), y + bh], radius=round(bh / 2), fill=(*col, 255))
            if q >= 0.6:
                val = f"{years:g} {self.years}"
                a = round(255 * gfx.progress(q, 0.6, 0.4))
                vx = self.bx0 + max(bh, bw) - gfx.text_width(val, self.f_val) - bh * 1.1
                fg = (12, 12, 12) if sum(col) > 450 else WHITE
                if vx > self.bx0 + 10 * u:
                    d.text((vx, y + bh / 2 - 30 * u), val, font=self.f_val, fill=(*fg, a))
                sh = shield(round(bh * 1.3), col if sk.name != "kinetic" else (40, 190, 90))
                gfx.paste_center(c, sh, self.bx0 + max(bh, bw), y + bh / 2, alpha=a / 255)
                if detail:
                    d.text((self.bx0, y + bh + 10 * u), detail.upper() if sk.upper else detail, font=self.f_det,
                           fill=(*muted, a))
        return self.done(c, t)


# ------------------------------------------------------------------- steps
class Steps(Magnet):
    """Dica numerada: o número gigante (``number.value`` de ``number.total``), "PASSO 1 DE 5", a barra de
    progresso, a dica (``keyword``) palavra por palavra e a caixinha que ganha o visto."""

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, sk = self.u, self.w, self.sk
        self.k = int(max(1, round(_num(spec))))
        self.n = int(max(self.k, round(_num(spec, "total", self.k))))
        label = (spec.subtitle or "").strip().upper() or tr("step", spec.lang)  # "ROUND", "LAYER", "TIP"...
        self.head = chip(sk, f"{label} {self.k} {tr('of', spec.lang)} {self.n}", u, 34)
        text = " ".join((spec.keyword or "").split()).rstrip(".")
        size = 76 if len(text) < 60 else 62
        self.f = sk.font("title", size, u) if sk.name != "paper" else gfx.font("typewriter", round(size * 0.8 * u))
        self.tx = w * 0.42
        self.words, self.nl = _words_layout(sk.txt(text) if sk.name == "kinetic" else text, self.f,
                                            round(w * 0.5), 4)
        asc, desc = self.f.getmetrics()
        self.lh = round((asc + desc) * 1.08)
        self.step = min(0.1, max(0.03, self.dur * 0.35 / max(1, len(self.words))))
        self.ink = sk.ink if sk.name != "paper" else (40, 34, 30)

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = len((spec.keyword or "").split())
        return [(0.0, "whoosh_soft", 0.35), (0.2, "impact_soft", 0.45)] + \
               [(0.55 + k * 0.06, "tick", 0.1) for k in range(min(n, 8))] + [(1.5, "pencil:0.20", 0.3)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h, sk = self.u, self.w, self.h, self.sk
        c = self.canvas(i)
        x0 = w * 0.07
        qh = gfx.ease_out_cubic(gfx.progress(t, 0.05, 0.3))
        gfx.paste_at(c, self.head, x0 - (1 - qh) * 50 * u, h * 0.15, alpha=qh)
        qn = gfx.ease_out_back(gfx.progress(t, 0.15, 0.4), 1.5)
        num = big_number(sk, str(self.k), u, 380)
        if qn > 0:
            gfx.paste_center(c, gfx.scaled(num, max(0.05, qn)), x0 + num.width / 2, h * 0.5)
        f_tot = sk.font("title", 90, u)
        muted = sk.muted if sk.name != "paper" else (130, 118, 106)
        tot = gfx.text_block([f"/{self.n}"], f_tot, muted, shadow=0)
        gfx.paste_at(c, tot, x0 + num.width + 4 * u, h * 0.5 + num.height * 0.12, alpha=gfx.progress(t, 0.35, 0.3))
        # progresso: um segmento por passo
        d = ImageDraw.Draw(c)
        seg_w, gap, yb = w * 0.3 / self.n, 10 * u, h * 0.8
        for j in range(self.n):
            qs = gfx.progress(t, 0.3 + j * 0.06, 0.25)
            if qs <= 0:
                continue
            lit = j < self.k
            col = (PAPER_RED if sk.name == "paper" else sk.accent) if lit else muted
            a = 255 if lit else 110
            xs = x0 + j * seg_w
            d.rectangle([xs, yb, xs + (seg_w - gap) * qs, yb + 14 * u], fill=(*col, a))
        # a dica
        top = h * 0.5 - self.nl * self.lh / 2
        for k, (wd, x, line) in enumerate(self.words):
            qw = gfx.progress(t, 0.55 + k * self.step, 0.18)
            if qw <= 0:
                break
            e = gfx.ease_out_cubic(qw)
            d.text((self.tx + x, top + line * self.lh + (1 - e) * 24 * u), gfx.safe_text(wd), font=self.f,
                   fill=(*self.ink, round(255 * qw)))
        # caixinha com o visto
        bs = round(70 * u)
        bx, by = self.tx - bs - 40 * u, top + 6 * u
        qb = gfx.progress(t, 0.45, 0.25)
        if qb > 0:
            box_col = (*self.ink, round(230 * qb))
            d.rounded_rectangle([bx, by, bx + bs, by + bs], radius=round(10 * u), outline=box_col,
                                width=max(3, round(6 * u)))
        qv = gfx.ease_out_cubic(gfx.progress(t, 1.5, 0.3))
        if qv > 0:
            tick = PAPER_RED if sk.name == "paper" else (60, 200, 100)
            p0, p1, p2 = (bx + bs * 0.18, by + bs * 0.52), (bx + bs * 0.42, by + bs * 0.78), (bx + bs * 1.05, by - bs * 0.1)
            lw = max(5, round(11 * u))
            if qv < 0.4:
                qq = qv / 0.4
                d.line([p0, (p0[0] + (p1[0] - p0[0]) * qq, p0[1] + (p1[1] - p0[1]) * qq)], fill=(*tick, 255), width=lw)
            else:
                qq = (qv - 0.4) / 0.6
                d.line([p0, p1], fill=(*tick, 255), width=lw)
                d.line([p1, (p1[0] + (p2[0] - p1[0]) * qq, p1[1] + (p2[1] - p1[1]) * qq)], fill=(*tick, 255), width=lw)
        return self.done(c, t)


# -------------------------------------------------------------- unverified
VERDICTS = ("unconfirmed", "no_data", "estimate")


class Unverified(Magnet):
    """O roteiro admite o que não sabe: o que é (``keyword``) num painel com o "?" e o carimbo do veredito
    (``subtitle``: unconfirmed | no_data | estimate) batendo torto, com tremida."""

    STAMP = 0.95

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h, sk = self.u, self.w, self.h, self.sk
        verdict = spec.subtitle if spec.subtitle in VERDICTS else "unconfirmed"
        color = AMBER if verdict == "estimate" else (PAPER_RED if sk.name == "paper" else RED)
        self.stamp = rubber_stamp(tr(verdict, spec.lang), u, color, 130)
        self.head = chip(sk, tr("fact_check", spec.lang), u, 34) if sk.name != "paper" else None
        self.pw, self.ph = round(w * 0.74), round(h * 0.38)
        self.panel = _panel(sk, self.pw, self.ph, u)
        pd = ImageDraw.Draw(self.panel)
        r = round(self.ph * 0.3)
        cx, cy = round(70 * u) + r, self.ph // 2
        ring = sk.muted if sk.name != "paper" else (150, 140, 128)
        pd.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*ring, 255), width=max(4, round(8 * u)))
        fq = gfx.font("black", round(r * 1.3))
        asc, desc = fq.getmetrics()
        pd.text((cx - gfx.text_width("?", fq) / 2, cy - (asc + desc) / 2), "?", font=fq, fill=(*ring, 255))
        text = " ".join((spec.keyword or "").split())
        f = sk.font("body", 50, u) if sk.name != "paper" else gfx.font("typewriter", round(46 * u))
        tx = cx + r + round(50 * u)
        lines = gfx.wrap(sk.txt(text) if sk.name in ("tv", "kinetic") else text, f, self.pw - tx - round(50 * u), 4)
        asc, desc = f.getmetrics()
        lh = round((asc + desc) * 1.12)
        ink = sk.ink if sk.name != "paper" else (40, 34, 30)
        y0 = cy - lh * len(lines) / 2
        for k, ln in enumerate(lines):
            pd.text((tx, y0 + k * lh), ln, font=f, fill=(*ink, 255))
        self.angle = float(self.rng.uniform(-11, -6))

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        return [(0.0, "whoosh_soft", 0.35), (0.25, "swipe", 0.25), (cls.STAMP, "impact", 0.6)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        c = self.canvas(i)
        if self.head is not None:
            qh = gfx.ease_out_cubic(gfx.progress(t, 0.05, 0.3))
            gfx.paste_at(c, self.head, w / 2 - self.pw / 2 - (1 - qh) * 50 * u, h * 0.17, alpha=qh)
        q = gfx.ease_out_cubic(gfx.progress(t, 0.15, 0.4))
        shake = 0.0
        qs = gfx.progress(t, self.STAMP, 0.18)
        if qs >= 1:
            shake = math.sin((t - self.STAMP) * 60) * 10 * u * max(0.0, 1 - (t - self.STAMP - 0.18) / 0.25)
        gfx.paste_center(c, self.panel, w / 2 + shake, h * 0.52 + (1 - q) * 50 * u, alpha=q)
        if qs > 0:
            sc = 1.9 - 0.9 * gfx.ease_out_cubic(qs)
            st = gfx.scaled(self.stamp.rotate(self.angle, expand=True, resample=Image.BICUBIC), sc)
            # na borda de baixo do painel, sem cobrir o texto
            gfx.paste_center(c, st, w / 2 + self.pw * 0.3 + shake, h * 0.52 + self.ph * 0.56, alpha=min(1.0, qs * 2))
        return self.done(c, t)


# ---------------------------------------------------------------- exploded
class Exploded(Magnet):
    """Vista explodida: a foto (``media[0]``) se separa em pedaços que se afastam do centro, e as peças
    citadas (``points``) entram puxadas por linhas; ``keyword`` (o carro) no rótulo "DESMONTADO"."""

    COLS, ROWS = 3, 2

    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, sk = self.u, self.w, self.sk
        self.iw, self.ih = round(w * 0.46), round(w * 0.46 * 9 / 16)
        self.img = self.src(spec.media[0], self.iw, self.ih, zoom=1.04) if spec.media else None
        self.parts = [str(p.get("text") or "") for p in (spec.points or []) if str(p.get("text") or "").strip()][:4]
        head = tr("teardown", spec.lang) + (f" · {spec.keyword}" if spec.keyword else "")
        self.head = chip(sk, head, u, 34)
        self.labels = [label(sk, p, u, w * 0.24, 32) for p in self.parts]
        rng = self.rng
        self.spin = [float(rng.uniform(-6, 6)) for _ in range(self.COLS * self.ROWS)]

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n = min(4, len(spec.points or []))
        return [(0.0, "whoosh_soft", 0.35), (0.55, "impact_soft", 0.45), (0.6, "whoosh", 0.35)] + \
               [(1.3 + k * 0.25, "pop", 0.22) for k in range(n)]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h, sk = self.u, self.w, self.h, self.sk
        c = self.canvas(i)
        qh = gfx.ease_out_cubic(gfx.progress(t, 0.05, 0.3))
        gfx.paste_at(c, self.head, w * 0.06 - (1 - qh) * 60 * u, h * 0.08, alpha=qh)
        cx, cy = w / 2, h * 0.53
        if self.img is not None:
            photo = self.img.frame(i).convert("RGBA")
            qa = gfx.ease_out_cubic(gfx.progress(t, 0.0, 0.35))
            qe = gfx.ease_out_cubic(gfx.progress(t, 0.55, 0.9))
            tw, th = self.iw / self.COLS, self.ih / self.ROWS
            border = (250, 250, 250) if sk.name in ("paper", "blueprint", "kinetic") else sk.accent
            for r in range(self.ROWS):
                for col in range(self.COLS):
                    k = r * self.COLS + col
                    box = (round(col * tw), round(r * th), round((col + 1) * tw), round((r + 1) * th))
                    tile = photo.crop(box)
                    if qe > 0:
                        tile = gfx.bordered(tile, max(2, round(4 * u)), border)
                        tile = tile.rotate(self.spin[k] * qe, expand=True, resample=Image.BICUBIC)
                    dx = (col - (self.COLS - 1) / 2) * tw
                    dy = (r - (self.ROWS - 1) / 2) * th
                    push = 0.3 * qe
                    gfx.paste_center(c, tile, cx + dx * (1 + push), cy + dy * (1 + push * 1.6), alpha=qa)
        # peças citadas, puxadas por linhas dos cantos
        # cada peça aponta para um pedaço do canto (o centro do pedaço já afastado)
        tw, th = self.iw / self.COLS, self.ih / self.ROWS
        corner = [(-1, -1), (1, -1), (-1, 1), (1, 1)]
        d = ImageDraw.Draw(c)
        line_col = (*(PAPER_RED if sk.name == "paper" else sk.accent), 255)
        for k, lab in enumerate(self.labels):
            if lab is None:
                continue
            ql = gfx.ease_out_cubic(gfx.progress(t, 1.3 + k * 0.25, 0.35))
            if ql <= 0:
                continue
            sx, sy = corner[k]
            lx = w / 2 + sx * (w * 0.5 - 40 * u - lab.width / 2)
            ly = cy + sy * h * 0.3
            tx, ty = cx + sx * tw * 1.3, cy + sy * th * 0.5 * 1.48
            ex, ey = tx + (lx - tx) * ql, ty + (ly - ty) * ql
            d.line([(tx, ty), (ex, ey)], fill=line_col, width=max(2, round(4 * u)))
            d.ellipse([tx - 9 * u, ty - 9 * u, tx + 9 * u, ty + 9 * u], fill=line_col)
            gfx.paste_center(c, lab, ex, ey, alpha=ql)
        return self.done(c, t)


STORY_TEMPLATES = {"source": Source, "fromto": FromTo, "coverage": Coverage, "steps": Steps,
                   "unverified": Unverified, "exploded": Exploded}
# desenham sem foto (a fala dá os dados); o exploded precisa da foto do carro
STORY_DATA = ("source", "fromto", "coverage", "steps", "unverified")


def story_cues(kind: str, spec: TemplateSpec) -> list[Cue] | None:
    cls = STORY_TEMPLATES.get(kind)
    return cls.cues(spec) if cls is not None else None
