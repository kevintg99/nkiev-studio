"""Mapa e país x país (estilo Documentário; dados de ``geo.py``, Natural Earth, domínio público):

* ``map``        — mapa da região que a fala cita: mar azul-ardósia, terra cáqui com relevo, fronteiras finas; os
  países citados acendem em vermelho, cada lugar ganha o ponto e o nome, as rotas (ataque, viagem, travessia) são
  setas curvas que se desenham, e a pergunta/frase da fala (``keyword``) entra numa tarja escura inclinada. A câmera
  aproxima devagar do destino da primeira rota. ``elements``: ``{"t": "country", "name"}``, ``{"t": "place",
  "name", "lat", "lon"}``, ``{"t": "route", "from", "to"}`` (nomes de lugares), cada um com ``say`` opcional (entra
  quando a fala diz a palavra).
* ``flagversus`` — dois países frente a frente: as bandeiras tremulando entram dos lados, o nome de cada um, o "VS"
  vermelho e, se houver, as linhas da comparação (``stats``: ``label``, ``value`` do 1º e ``value_b`` do 2º).
  ``labels`` = os 2 países; ``tiles`` = os PNG das bandeiras (o planner baixa).
"""

from __future__ import annotations

import math
import re
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import geo, gfx
from .templates import Template, TemplateSpec

Cue = tuple[float, str, float]
SEA = (44, 56, 70)
LAND = (132, 128, 100)
BORDER = (214, 210, 190)
ROUTE = (238, 58, 88)
WHITE = (250, 250, 246)
LABEL_BG = (18, 20, 26)
MAP_TYPES = ("country", "place", "route")
Z_END = 1.1  # aproximação da câmera no fim


def clean_map_elements(raw: object) -> list[dict]:
    """Países, lugares e rotas do mapa nos tipos certos (rota só entre lugares da lista)."""
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for el in raw[:14]:
        if not isinstance(el, dict) or el.get("t") not in MAP_TYPES:
            continue
        e: dict = {"t": el["t"]}
        if el.get("say"):
            e["say"] = " ".join(str(el["say"]).split())[:30]
        if el["t"] in ("country", "place"):
            name = " ".join(str(el.get("name") or "").split())[:40]
            if not name:
                continue
            e["name"] = name
            if el.get("label"):
                e["label"] = " ".join(str(el["label"]).split())[:40]
            if el["t"] == "place":
                for k in ("lat", "lon"):
                    try:
                        e[k] = float(el[k])
                    except (KeyError, TypeError, ValueError):
                        pass
                if el.get("country"):
                    e["country"] = " ".join(str(el["country"]).split())[:40]
        else:
            a, b = (" ".join(str(el.get(k) or "").split())[:40] for k in ("from", "to"))
            if not a or not b or a == b:
                continue
            e["from"], e["to"] = a, b
        out.append(e)
    names = {e["name"] for e in out if e["t"] == "place"}
    return [e for e in out if e["t"] != "route" or (e["from"] in names and e["to"] in names)][:14]


def resolve(elements: list[dict]) -> list[dict]:
    """Coordenadas de cada lugar (base de cidades ou a da IA) e o país de cada "country"; o que não acha sai."""
    out = []
    for el in clean_map_elements(elements):
        if el["t"] == "place":
            where = geo.locate(el["name"], el.get("country", ""), el.get("lat"), el.get("lon"))
            if where is None:
                continue
            el = {**el, "lat": where[0], "lon": where[1]}
        elif el["t"] == "country" and geo.find_country(el["name"]) is None:
            continue
        out.append(el)
    places = {e["name"] for e in out if e["t"] == "place"}
    return [e for e in out if e["t"] != "route" or (e["from"] in places and e["to"] in places)]


def _norm(s: str) -> str:
    return re.sub(r"[^\w]", "", s.lower())


MIN_SPAN = 6.5  # altura mínima do mapa, em graus de latitude


@lru_cache(maxsize=4)
def _relief(w: int, h: int, seed: int) -> np.ndarray:
    """Sombreado de relevo de mentira (ruído fractal iluminado de noroeste): 0,8–1,15 multiplica a terra."""
    rng = np.random.default_rng(seed)
    acc = np.zeros((h, w), np.float32)
    for k, amp in ((5, 1.0), (14, 0.5), (36, 0.22)):
        small = rng.normal(0, 1, (max(2, h * k // 1000), max(2, w * k // 1000))).astype(np.float32)
        img = Image.fromarray(small).resize((w, h), Image.BICUBIC)
        acc += np.asarray(img, np.float32) * amp
    gy, gx = np.gradient(acc)
    shade = 1.0 + np.clip(-(gx + gy) * 3.5, -0.05, 0.045)  # morros suaves, luz de noroeste (mais forte parecia camuflagem)
    return shade.astype(np.float32)


class Map(Template):
    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.dur = spec.frames / self.fps
        self.els = resolve(spec.elements)
        self.places = {e["name"]: e for e in self.els if e["t"] == "place"}
        self._view()
        self.f_place = gfx.font("serif", round(40 * u))
        self.f_label = gfx.font("serif", round(46 * u))
        self.f_context = gfx.font("serif", round(30 * u))
        self._place_labels()
        self.title = self._title_card(" ".join((spec.keyword or "").split()))
        self.title_at = self._title_spot()
        self.base = self._base()
        self.starts = self._timing()
        self.tints = {e["name"]: self._tint(e["name"]) for e in self.els if e["t"] == "country"}

    # ---------------------------------------------------------- enquadramento
    def _view(self) -> None:
        pts = [(e["lon"], e["lat"]) for e in self.places.values()]
        for e in self.els:
            if e["t"] == "country":
                c = geo.find_country(e["name"])
                x0, y0, x1, y1 = c.bbox
                if x1 - x0 > 60:  # país enorme (Rússia, EUA com o Alasca): só o ponto do rótulo e arredores
                    pts += [(c.label[0] - 8, c.label[1] - 5), (c.label[0] + 8, c.label[1] + 5)]
                else:
                    pts += [(x0, y0), (x1, y1)]
        if not pts:
            pts = [(-10, 35), (30, 60)]
        lons, lats = [p[0] for p in pts], [p[1] for p in pts]
        lat0 = (min(lats) + max(lats)) / 2
        self.kx = max(0.2, math.cos(math.radians(lat0)))
        # no mínimo uma região inteira (uns 700 km): com 1 ou 2 cidades perto (Saint-Vith e Bastogne) o mapa virava um
        # pedaço de terra sem país nenhum para situar
        span_x = max((max(lons) - min(lons)) * self.kx * 1.5, MIN_SPAN * 16 / 9)
        span_y = max((max(lats) - min(lats)) * 1.6, MIN_SPAN)
        aspect = self.w / self.h
        if span_x / span_y < aspect:
            span_x = span_y * aspect
        else:
            span_y = span_x / aspect
        self.cx, self.cy = (min(lons) + max(lons)) / 2, lat0
        self.scale = self.w / span_x  # px por grau (já corrigido pela latitude)
        # a câmera vai do quadro inteiro até o destino da 1ª rota (ou o 1º lugar)
        route = next((e for e in self.els if e["t"] == "route"), None)
        focus = self.places[route["to"]] if route else next(iter(self.places.values()), None)
        self.focus = self.px(focus["lon"], focus["lat"]) if focus else (self.w / 2, self.h / 2)

    def _label_box(self, text: str, x: float, y: float, fnt) -> tuple[float, float, float, float]:
        tw = gfx.text_width(gfx.safe_text(text), fnt)
        asc, desc = fnt.getmetrics()
        return x - tw / 2 - 8 * self.u, y - 10 * self.u, x + tw / 2 + 8 * self.u, y + asc + desc + 6 * self.u

    def _place_labels(self) -> None:
        """Nome de cada lugar embaixo do ponto; se encostar no nome de outro (Paris e Versailles), vai para cima, para
        a direita ou para a esquerda do ponto."""
        u = self.u
        # os pontos dos lugares também são obstáculo (o ponto de Bastogne caía embaixo do nome de Saint-Vith)
        dots = [(x - 12 * u, y - 12 * u, x + 12 * u, y + 12 * u)
                for x, y in (self.px(e["lon"], e["lat"]) for e in self.places.values())]
        # e o nome grande do país destacado ("BELGIQUE" caía em cima de "Anvers")
        for e in self.els:
            if e["t"] == "country":
                x, y = self.px(*geo.find_country(e["name"]).label)
                text = " ".join((e.get("label") or e["name"]).upper())
                tw = gfx.text_width(gfx.safe_text(text), self.f_label)
                asc, desc = self.f_label.getmetrics()
                dots.append((x - tw / 2 - 8 * u, y - 6 * u, x + tw / 2 + 8 * u, y + asc + desc + 6 * u))
        taken: list[tuple[float, float, float, float]] = []
        for e in self.places.values():
            x, y = self.px(e["lon"], e["lat"])
            text = e.get("label") or e["name"]
            tw = gfx.text_width(gfx.safe_text(text), self.f_place)
            asc, desc = self.f_place.getmetrics()
            spots = [(0, 22 * u), (0, -22 * u - asc - desc), (tw / 2 + 22 * u, -(asc + desc) / 2),
                     (-tw / 2 - 22 * u, -(asc + desc) / 2)]
            best = spots[0]
            for dx, dy in spots:
                box = self._label_box(text, x + dx, y + dy, self.f_place)
                if not any(not (box[2] <= b[0] or b[2] <= box[0] or box[3] <= b[1] or b[3] <= box[1])
                           for b in taken + dots):
                    best = (dx, dy)
                    break
            e["_off"] = best
            taken.append(self._label_box(text, x + best[0], y + best[1], self.f_place))
        self.label_boxes = taken

    def px(self, lon: float, lat: float) -> tuple[float, float]:
        return (self.w / 2 + (lon - self.cx) * self.kx * self.scale, self.h / 2 - (lat - self.cy) * self.scale)

    def _rings_px(self, c: geo.Country):
        lon_lo = self.cx - self.w / 2 / (self.kx * self.scale) - 2
        lon_hi = self.cx + self.w / 2 / (self.kx * self.scale) + 2
        lat_lo = self.cy - self.h / 2 / self.scale - 2
        lat_hi = self.cy + self.h / 2 / self.scale + 2
        for ring in c.rings:
            if ring[:, 0].max() < lon_lo or ring[:, 0].min() > lon_hi or ring[:, 1].max() < lat_lo \
                    or ring[:, 1].min() > lat_hi or ring[:, 0].max() - ring[:, 0].min() > 300:
                continue
            xs = self.w / 2 + (ring[:, 0] - self.cx) * self.kx * self.scale
            ys = self.h / 2 - (ring[:, 1] - self.cy) * self.scale
            yield list(zip(xs.tolist(), ys.tolist()))

    def _base(self) -> Image.Image:
        """Mar, terra com relevo e fronteiras (desenhado uma vez)."""
        w, h, u = self.w, self.h, self.u
        yy, xx = np.mgrid[0:h, 0:w]
        r = np.hypot((xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2))
        sea = np.asarray(SEA, np.float32)[None, None, :] * (1.06 - 0.32 * np.clip(r - 0.2, 0, 1))[..., None]
        mask = Image.new("L", (w, h), 0)
        md = ImageDraw.Draw(mask)
        borders: list[list[tuple[float, float]]] = []
        for c in geo.countries():
            for pts in self._rings_px(c):
                if len(pts) >= 3:
                    md.polygon(pts, fill=255)
                    borders.append(pts)
        land = np.asarray(LAND, np.float32)[None, None, :] * _relief(w, h, self.s.seed % 7)[..., None]
        m = np.asarray(mask.filter(ImageFilter.GaussianBlur(0.6)), np.float32)[..., None] / 255
        img = Image.fromarray((sea * (1 - m) + land * m).clip(0, 255).astype(np.uint8), "RGB").convert("RGBA")
        lines = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ld = ImageDraw.Draw(lines)
        for pts in borders:
            ld.line(pts + pts[:1], fill=(*BORDER, 120), width=max(1, round(1.6 * u)), joint="curve")
        img.alpha_composite(lines)
        self._context_names(img)
        return img

    def _context_names(self, img: Image.Image) -> None:
        """Nome dos países do quadro, pequeno e apagado, na língua do vídeo ("BELGIQUE", "ALLEMAGNE"): é o que diz
        onde fica Malmedy. Fora o país já destacado (o nome dele entra grande), o país pequeno demais para o quadro e
        o nome que cairia em cima de um lugar."""
        u, w, h = self.u, self.w, self.h
        lit = {geo.find_country(e["name"]).iso2 for e in self.els if e["t"] == "country"}
        boxes = list(self.label_boxes) + [(x - 30 * u, y - 30 * u, x + 30 * u, y + 30 * u)
                                          for x, y in (self.px(e["lon"], e["lat"]) for e in self.places.values())]
        if self.title is not None:  # nem embaixo da tarja do título
            tx, ty = self.title_at
            tw, th = self.title.width / 2 + 20 * u, self.title.height / 2 + 20 * u
            boxes.append((tx - tw, ty - th, tx + tw, ty + th))
        d = ImageDraw.Draw(img)
        self.context_names: list[str] = []
        for c in geo.countries():
            x, y = self.px(*c.label)
            bw = (c.bbox[2] - c.bbox[0]) * self.kx * self.scale
            if c.iso2 in lit or not (0.08 * w < x < 0.92 * w and 0.08 * h < y < 0.92 * h) or bw < 140 * u:
                continue
            text = " ".join(gfx.safe_text(geo.name_in(c, self.s.lang)).upper())
            tw = gfx.text_width(text, self.f_context)
            asc, desc = self.f_context.getmetrics()
            box = (x - tw / 2, y - (asc + desc) / 2, x + tw / 2, y + (asc + desc) / 2)
            # a câmera aproxima 10% no fim: nome perto da borda sairia cortado ("SLOVAQUI")
            if box[0] < 0.08 * w or box[2] > 0.92 * w or box[1] < 0.08 * h or box[3] > 0.92 * h or any(
                    not (box[2] <= b[0] or b[2] <= box[0] or box[3] <= b[1] or b[3] <= box[1]) for b in boxes):
                continue
            boxes.append(box)
            self.context_names.append(text.replace(" ", ""))
            d.text((box[0], box[1]), text, font=self.f_context, fill=(*WHITE, 120))

    def _tint(self, name: str) -> Image.Image:
        c = geo.find_country(name)
        mask = Image.new("L", (self.w, self.h), 0)
        md = ImageDraw.Draw(mask)
        for pts in self._rings_px(c):
            md.polygon(pts, fill=255)
        layer = Image.new("RGBA", (self.w, self.h), (*ROUTE, 0))
        layer.putalpha(mask.point(lambda v: v * 95 // 255))
        return layer

    def _title_card(self, text: str) -> Image.Image | None:
        if not text:
            return None
        u = self.u
        f = gfx.font("serif", round(44 * u))
        lines = gfx.wrap(text.upper(), f, round(self.w * 0.4), 3)
        asc, desc = f.getmetrics()
        lh = round((asc + desc) * 1.05)
        tw = max(gfx.text_width(x, f) for x in lines)
        pad = round(26 * u)
        img = Image.new("RGBA", (tw + 2 * pad, lh * len(lines) + 2 * pad), (*LABEL_BG, 240))
        d = ImageDraw.Draw(img)
        d.rectangle([2, 2, img.width - 3, img.height - 3], outline=(*WHITE, 230), width=max(2, round(3 * u)))
        for k, line in enumerate(lines):
            x = pad
            q_end = line.endswith("?") and k == len(lines) - 1
            body = line[:-1] if q_end else line
            d.text((x, pad + k * lh), gfx.safe_text(body), font=f, fill=(*WHITE, 255))
            if q_end:
                d.text((x + gfx.text_width(body, f), pad + k * lh), "?", font=f, fill=(*ROUTE, 255))
        img = img.rotate(3.0, expand=True, resample=Image.BICUBIC)
        return gfx.with_shadow(img, 0.0, round(10 * u), (round(4 * u), round(8 * u)), 150)

    def _title_spot(self) -> tuple[float, float]:
        """Onde a tarja do título fica: em cima, embaixo ou num canto, onde não tampa nenhum lugar nem nome."""
        w, h = self.w, self.h
        if self.title is None:
            return w / 2, h * 0.14
        tw, th = self.title.width, self.title.height
        marks = [self.px(e["lon"], e["lat"]) for e in self.places.values()]
        for e in self.els:
            if e["t"] == "country":
                marks.append(self.px(*geo.find_country(e["name"]).label))
        best, best_score = (w / 2, h * 0.14), None
        for cx, cy in ((w / 2, h * 0.14), (w / 2, h * 0.86), (tw / 2 + 40 * self.u, h * 0.14),
                       (w - tw / 2 - 40 * self.u, h * 0.14), (tw / 2 + 40 * self.u, h * 0.86),
                       (w - tw / 2 - 40 * self.u, h * 0.86)):
            pad = 70 * self.u  # o nome fica embaixo do ponto
            hits = sum(1 for x, y in marks if cx - tw / 2 - pad < x < cx + tw / 2 + pad
                       and cy - th / 2 - pad < y < cy + th / 2 + pad)
            if best_score is None or hits < best_score:
                best, best_score = (cx, cy), hits
            if hits == 0:
                break
        if best_score:  # não coube sem tampar: a tarja fica menor no canto escolhido
            self.title = gfx.scaled(self.title, 0.75)
        return best

    # ---------------------------------------------------------- tempo
    def _timing(self) -> list[float]:
        n = max(1, len(self.els))
        span = max(1.5, min(self.dur * 0.6, n * 1.0))
        caps = [(c[0], _norm(c[2])) for c in self.s.captions]
        out = []
        for k, el in enumerate(self.els):
            at = 0.5 + span * k / n
            for word in (el.get("say"), el.get("label"), el.get("name"), el.get("to")):
                key = _norm(word or "")
                hit = next((t0 for t0, txt in caps if key and key in txt), None) if key else None
                if hit is not None:
                    at = max(0.4, min(self.dur - 1.2, hit))
                    break
            out.append(at)
        return out

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        n_routes = sum(1 for e in clean_map_elements(spec.elements) if e["t"] == "route")
        return [(0.0, "whoosh_soft", 0.35)] + [(0.9 + k * 0.8, "swipe", 0.3) for k in range(n_routes)]

    # ---------------------------------------------------------- quadro
    def _camera(self, t: float) -> tuple[float, float, float]:
        z = 1.0 + (Z_END - 1.0) * gfx.ease_in_out(gfx.progress(t, 0.3, max(1.0, self.dur - 0.3)))
        fx = self.w / 2 + (self.focus[0] - self.w / 2) * (z - 1) / (Z_END - 1 or 1) * 0.6
        fy = self.h / 2 + (self.focus[1] - self.h / 2) * (z - 1) / (Z_END - 1 or 1) * 0.6
        # o recorte nunca sai do mapa (aparecia faixa preta na borda)
        hw, hh = self.w / z / 2, self.h / z / 2
        return z, min(max(fx, hw), self.w - hw), min(max(fy, hh), self.h - hh)

    def _to_screen(self, p: tuple[float, float], cam: tuple[float, float, float]) -> tuple[float, float]:
        z, fx, fy = cam
        return (self.w / 2 + (p[0] - fx) * z, self.h / 2 + (p[1] - fy) * z)

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        cam = self._camera(t)
        z, fx, fy = cam
        frame = self.base.copy()
        for el, t0 in zip(self.els, self.starts):
            if el["t"] == "country":
                q = gfx.progress(t, t0, 0.5)
                if q > 0:
                    tint = self.tints[el["name"]]
                    if q < 1:
                        tint = gfx.fade(tint, q)
                    frame.alpha_composite(tint)
        # câmera: recorte em volta do foco, ampliado
        cw, ch = w / z, h / z
        x0, y0 = fx - cw / 2, fy - ch / 2
        frame = frame.transform((w, h), Image.EXTENT, (x0, y0, x0 + cw, y0 + ch), Image.BILINEAR)
        d = ImageDraw.Draw(frame)
        for el, t0 in zip(self.els, self.starts):
            q = gfx.ease_out_cubic(gfx.progress(t, t0, 0.9 if el["t"] == "route" else 0.4))
            if q <= 0:
                continue
            if el["t"] == "route":
                a, b = self.places[el["from"]], self.places[el["to"]]
                self._route(d, self._to_screen(self.px(a["lon"], a["lat"]), cam),
                            self._to_screen(self.px(b["lon"], b["lat"]), cam), q)
        for el, t0 in zip(self.els, self.starts):  # nomes por cima das setas
            q = gfx.ease_out_cubic(gfx.progress(t, t0, 0.4))
            if q <= 0 or el["t"] == "route":
                continue
            if el["t"] == "place":
                x, y = self._to_screen(self.px(el["lon"], el["lat"]), cam)
                r = 7 * u * q
                d.ellipse([x - r, y - r, x + r, y + r], fill=(*WHITE, 255), outline=(*LABEL_BG, 200),
                          width=max(1, round(2 * u)))
                dx, dy = el.get("_off", (0, 22 * u))
                self._name(frame, el.get("label") or el["name"], x + dx * z, y + dy * z, q, self.f_place)
            else:
                c = geo.find_country(el["name"])
                x, y = self._to_screen(self.px(*c.label), cam)
                self._name(frame, (el.get("label") or el["name"]).upper(), x, y, q, self.f_label, spaced=True)
        if self.title is not None:
            q = gfx.ease_out_back(gfx.progress(t, 0.25, 0.45), 1.3)
            if q > 0:
                gfx.paste_center(frame, gfx.scaled(self.title, max(0.05, 0.9 + 0.1 * q)), *self.title_at,
                                 alpha=min(1.0, q * 1.5))
        return frame

    def _name(self, frame: Image.Image, text: str, x: float, y: float, q: float, fnt, spaced: bool = False) -> None:
        txt = gfx.safe_text(text)
        tw = gfx.text_width(txt, fnt)
        asc, desc = fnt.getmetrics()
        layer = Image.new("RGBA", (tw + 16, asc + desc + 16), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((8, 8), txt, font=fnt, fill=(*WHITE, 255), stroke_width=max(1, round(2 * self.u)),
                                   stroke_fill=(20, 22, 28, 200))
        layer = gfx.with_shadow(layer, 0.0, round(6 * self.u), (0, round(3 * self.u)), 140)
        gfx.paste_center(frame, layer, x, y + asc * 0.5, alpha=q)

    def _route(self, d: ImageDraw.ImageDraw, a: tuple[float, float], b: tuple[float, float], q: float) -> None:
        """Seta curva (bezier) se desenhando de ``a`` até ``b``, com a ponta no fim."""
        u = self.u
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1.0
        bend = 0.22 * L
        cx, cy = mx - dy / L * bend, my + dx / L * bend
        n = 48
        pts = []
        for k in range(n + 1):
            s = k / n * q
            pts.append(((1 - s) ** 2 * a[0] + 2 * (1 - s) * s * cx + s ** 2 * b[0],
                        (1 - s) ** 2 * a[1] + 2 * (1 - s) * s * cy + s ** 2 * b[1]))
        width = max(3, round(9 * u))
        d.line(pts, fill=(120, 20, 40, 160), width=width + max(2, round(4 * u)), joint="curve")
        d.line(pts, fill=(*ROUTE, 255), width=width, joint="curve")
        if q > 0.9 and len(pts) > 2:
            (x1, y1), (x2, y2) = pts[-3], pts[-1]
            ang = math.atan2(y2 - y1, x2 - x1)
            hl = 30 * u
            tip = (x2 + math.cos(ang) * hl * 0.3, y2 + math.sin(ang) * hl * 0.3)
            left = (tip[0] - hl * math.cos(ang - 0.5), tip[1] - hl * math.sin(ang - 0.5))
            right = (tip[0] - hl * math.cos(ang + 0.5), tip[1] - hl * math.sin(ang + 0.5))
            d.polygon([tip, left, right], fill=(*ROUTE, 255))


# ------------------------------------------------------------------ país x país
def _wave(flag: Image.Image, t: float, amp: float) -> Image.Image:
    """Bandeira tremulando: colunas deslocadas por uma onda e sombreadas nas dobras."""
    arr = np.asarray(flag.convert("RGBA"), np.float32)
    h, w = arr.shape[:2]
    out = np.zeros((h + 2 * int(amp) + 2, w, 4), np.float32)
    xs = np.arange(w)
    phase = xs / w * 2.6 * math.pi - t * 5.0
    off = (np.sin(phase) * amp * (xs / w)).astype(int) + int(amp) + 1
    shade = 1.0 + 0.16 * np.cos(phase) * (xs / w)
    for x in range(w):
        out[off[x]:off[x] + h, x, :3] = arr[:, x, :3] * shade[x]
        out[off[x]:off[x] + h, x, 3] = arr[:, x, 3]
    return Image.fromarray(out.clip(0, 255).astype(np.uint8), "RGBA")


class FlagVersus(Template):
    def __init__(self, spec: TemplateSpec) -> None:
        super().__init__(spec)
        u, w, h = self.u, self.w, self.h
        self.dur = spec.frames / self.fps
        yy, xx = np.mgrid[0:h, 0:w]
        r = np.hypot((xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2))
        bg = np.asarray(SEA, np.float32)[None, None, :] * (1.05 - 0.4 * np.clip(r - 0.2, 0, 1))[..., None]
        self.bg = Image.fromarray(bg.clip(0, 255).astype(np.uint8), "RGB").convert("RGBA")
        names = [str(x) for x in spec.labels[:2]] + [""] * 2
        self.names = names[:2]
        fw = round(w * 0.27)
        self.flags: list[Image.Image | None] = []
        for k in range(2):
            path = spec.tiles[k] if k < len(spec.tiles) else ""
            img = None
            if path:
                try:
                    img = Image.open(path).convert("RGBA")
                    img = img.resize((fw, max(1, round(fw * img.height / img.width))), Image.LANCZOS)
                except OSError:
                    img = None
            self.flags.append(img)
        self.f_name = gfx.font("serif", round(64 * u))
        self.f_vs = gfx.font("black", round(120 * u))
        self.f_row = gfx.font("semibold", round(34 * u))
        self.f_val = gfx.font("black", round(46 * u))
        self.f_cap = gfx.font("serif", round(58 * u))
        self.rows = [(str(s.get("label", "")), str(s.get("value", "")), str(s.get("value_b", "")))
                     for s in spec.stats[:4] if s.get("value") or s.get("value_b")]

    @classmethod
    def cues(cls, spec: TemplateSpec) -> list[Cue]:
        return [(0.0, "whoosh", 0.4), (0.35, "whoosh", 0.4), (0.8, "impact", 0.45)] + \
               [(1.4 + k * 0.5, "tick", 0.25) for k in range(min(4, len(spec.stats)))]

    def frame(self, i: int) -> Image.Image:
        t = i / self.fps
        u, w, h = self.u, self.w, self.h
        frame = self.bg.copy()
        top = h * (0.36 if self.rows else 0.42)
        for k in range(2):
            q = gfx.ease_out_cubic(gfx.progress(t, 0.1 + k * 0.3, 0.55))
            if q <= 0:
                continue
            cx = w * (0.26 if k == 0 else 0.74)
            side = -1 if k == 0 else 1
            x = cx + side * (1 - q) * w * 0.35
            flag = self.flags[k]
            if flag is not None:
                waved = gfx.with_shadow(_wave(flag, t + k, 9 * u), 0.0, round(14 * u), (0, round(10 * u)), 150)
                gfx.paste_center(frame, waved, x, top, alpha=q)
                ny = top + flag.height / 2 + 60 * u
            else:
                ny = top
            name = gfx.safe_text(self.names[k].upper())
            if name:
                layer = Image.new("RGBA", (gfx.text_width(name, self.f_name) + 20, round(self.f_name.size * 1.4)),
                                  (0, 0, 0, 0))
                ImageDraw.Draw(layer).text((10, 4), name, font=self.f_name, fill=(*WHITE, 255))
                gfx.paste_center(frame, layer, x, ny, alpha=q)
        qv = gfx.ease_out_back(gfx.progress(t, 0.8, 0.35), 1.8)
        if qv > 0:
            vs = Image.new("RGBA", (gfx.text_width("VS", self.f_vs) + 30, round(self.f_vs.size * 1.3)), (0, 0, 0, 0))
            ImageDraw.Draw(vs).text((15, 0), "VS", font=self.f_vs, fill=(*ROUTE, 255),
                                    stroke_width=max(2, round(4 * u)), stroke_fill=(20, 10, 14, 255))
            gfx.paste_center(frame, gfx.scaled(vs, max(0.05, qv)), w / 2, top, alpha=min(1.0, qv))
        if self.s.keyword:
            qc = gfx.progress(t, 1.0, 0.4)
            if qc > 0:
                cap = gfx.safe_text(self.s.keyword.upper())
                layer = Image.new("RGBA", (gfx.text_width(cap, self.f_cap) + 20, round(self.f_cap.size * 1.4)),
                                  (0, 0, 0, 0))
                ImageDraw.Draw(layer).text((10, 4), cap, font=self.f_cap, fill=(*WHITE, 230))
                gfx.paste_center(frame, layer, w / 2, h * 0.1, alpha=qc)
        d = ImageDraw.Draw(frame)
        y = h * 0.66
        for k, (label, va, vb) in enumerate(self.rows):
            q = gfx.ease_out_cubic(gfx.progress(t, 1.4 + k * 0.5, 0.4))
            if q <= 0:
                continue
            a = round(255 * q)
            d.line([(w * 0.12, y + 64 * u), (w * 0.88, y + 64 * u)], fill=(*BORDER, round(60 * q)),
                   width=max(1, round(2 * u)))
            lab = gfx.safe_text(label)
            d.text((w / 2 - gfx.text_width(lab, self.f_row) / 2, y), lab, font=self.f_row, fill=(*BORDER, a))
            d.text((w * 0.26 - gfx.text_width(va, self.f_val) / 2, y - 8 * u), gfx.safe_text(va), font=self.f_val,
                   fill=(*WHITE, a))
            d.text((w * 0.74 - gfx.text_width(vb, self.f_val) / 2, y - 8 * u), gfx.safe_text(vb), font=self.f_val,
                   fill=(*WHITE, a))
            y += 78 * u
        return frame


MAP_TEMPLATES: dict[str, type[Template]] = {"map": Map, "flagversus": FlagVersus}


def map_cues(kind: str, spec: TemplateSpec) -> list[Cue] | None:
    cls = MAP_TEMPLATES.get(kind)
    return cls.cues(spec) if cls is not None else None
