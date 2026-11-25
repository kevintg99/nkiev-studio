"""Planejamento da linha do tempo: cortes, durações e transições.

Regras principais:

* Cada clipe renderizado (incluindo a sobreposição das transições) tem no
  máximo ``max_clip_seconds`` (7s) e duração com milissegundos "quebrados"
  (nunca 3.000 ou 4.500), sem repetir valores — o ritmo não parece template.
* Os cortes internos de uma cena caem, quando possível, nas pausas da fala.
* Os pontos de troca são convertidos em frames a partir de posições
  acumuladas, então não há deriva de sincronia por arredondamento.
"""

from __future__ import annotations

import bisect
import math
import random

from .config import TimelineConfig
from .models import MediaType, Scene, Shot, Transition, Word

EFFECTS = {"whip", "punch", "glitch", "leak", "wash", "burn", "blackout"}

ORGANIC_NUDGES = (-7, -3, -1, 1, 3, 7, -9, 9, -13, 13)


def is_round_ms(ms: int) -> bool:
    """Redondo = múltiplo de 10 ms ou colado num décimo de segundo (3.001, 4.498)."""
    return ms % 10 == 0 or min(ms % 100, 100 - ms % 100) < 5


def organic(value: float, rng: random.Random, used: set[int] | None = None) -> float:
    """Arredonda para ms e desloca alguns ms se ficar "redondo" ou repetido."""
    ms = round(value * 1000)
    for _ in range(64):
        if not is_round_ms(ms) and (used is None or ms not in used):
            break
        ms += rng.choice(ORGANIC_NUDGES)
    return ms / 1000


def word_boundaries(words: list[Word]) -> list[tuple[float, float]]:
    """Instantes bons para cortar (meio do intervalo entre palavras) e o tamanho da pausa."""
    out = []
    for a, b in zip(words, words[1:]):
        gap = max(0.0, b.start - a.end)
        out.append(((a.end + b.start) / 2 if gap else b.start, gap))
    return out


def _snap(t: float, boundaries: list[tuple[float, float]], tolerance: float, lo: float, hi: float) -> float:
    """Leva o corte para a maior pausa dentro da tolerância (desempate: a mais próxima).

    Sem pausa perto, usa ao menos uma fronteira entre palavras (nunca corta no meio
    de uma palavra)."""
    if not boundaries:
        return t
    times = [b[0] for b in boundaries]
    i = bisect.bisect_left(times, t - tolerance)
    best: tuple[float, float] | None = None
    while i < len(boundaries) and times[i] <= t + tolerance:
        bt, gap = boundaries[i]
        if lo <= bt <= hi:
            key = (round(gap, 2), -abs(bt - t))
            if best is None or key > best[1]:
                best = (bt, key)  # type: ignore[assignment]
        i += 1
    return best[0] if best else t


def split_scene(start: float, end: float, gmin: float, gmax: float, rng: random.Random,
                boundaries: list[tuple[float, float]], tolerance: float) -> list[float]:
    """Devolve os pontos de corte internos de uma cena."""
    cuts: list[float] = []
    cursor = start
    while end - cursor > gmax:
        remaining = end - cursor
        g = rng.uniform(gmin, gmax)
        if remaining - g < gmin:
            g = remaining / 2
        lo = cursor + gmin
        hi = min(cursor + gmax, end - gmin)
        t = _snap(cursor + g, boundaries, tolerance, lo, hi)
        cuts.append(t)
        cursor = t
    return cuts


def _hold_windows(cuts: list[float], scene_of: list[int], templates: dict[int, str],
                  hold: list[tuple[float, float]], gmin: float, gmax: float,
                  boundaries: list[tuple[float, float]], tolerance: float
                  ) -> tuple[list[float], list[int], dict[int, str], set[int], set[int]]:
    """O avatar em tela cheia vira um shot só, do começo ao fim exato da fala dele. Antes os cortes seguiam as
    cenas: no vídeo da garantia Toyota a abertura de 11,3 s virou 3 shots com transição (clarão branco) entre
    dois pedaços do mesmo avatar, e o último ia até 12,7 s com o rosto parado e a narração seguindo."""
    end = cuts[-1]
    segs = [(cuts[i], cuts[i + 1], scene_of[i], templates.get(i)) for i in range(len(cuts) - 1)]
    windows = sorted((max(0.0, a), min(end, b)) for a, b in hold if b - a > 0.3 and a < end)
    eps = 0.05
    inside = lambda t: any(a + eps < t < b - eps for a, b in windows)  # noqa: E731
    fixed = {0.0, end} | {t for w in windows for t in w}
    new = sorted({t for t in cuts if not inside(t)} | fixed)
    # sobra curta demais logo antes/depois do avatar: junta com o vizinho de fora
    changed = True
    while changed:
        changed = False
        for i in range(1, len(new) - 1):
            t = new[i]
            if t in fixed:
                continue
            if (new[i + 1] in fixed and new[i + 1] - t < gmin) or (new[i - 1] in fixed and t - new[i - 1] < gmin):
                del new[i]
                changed = True
                break
    new = [t for k, t in enumerate(new) if k == 0 or t - new[k - 1] > eps]
    # pedaço fora do avatar que ficou longo demais (os cortes de dentro saíram): divide de novo
    out: list[float] = [new[0]]
    for a, b in zip(new, new[1:]):
        held = any(wa - eps <= a and b <= wb + eps for wa, wb in windows)
        parts = 1 if held else math.ceil((b - a) / gmax)
        for j in range(1, parts):
            t = a + (b - a) * j / parts
            out.append(_snap(t, boundaries, tolerance, max(out[-1] + gmin, t - tolerance), min(b - gmin, t + tolerance)))
        out.append(b)
    new = out
    out_scene, out_tpl, change, held_at = [], {}, set(), set()
    for k in range(len(new) - 1):
        a, b = new[k], new[k + 1]
        mid = (a + b) / 2
        seg = next((s for s in segs if s[0] <= mid < s[1]), segs[-1])
        out_scene.append(seg[2])
        held = any(wa - eps <= a and b <= wb + eps for wa, wb in windows)
        if held:
            held_at.add(k)
        if seg[3] and not held and abs(seg[0] - a) < eps:  # o template só no shot que abre o segmento dele
            out_tpl[k] = seg[3]
        if k > 0 and out_scene[k] != out_scene[k - 1]:
            change.add(k)
    return new, out_scene, out_tpl, change, held_at


def _pick_transition(cfg: TimelineConfig, available: list[str], prev_kind: str | None, scene_change: bool,
                     max_dur: float, rng: random.Random) -> Transition:
    p_cut = cfg.hard_cut_probability_scene_change if scene_change else cfg.hard_cut_probability_in_scene
    if not available or max_dur < 0.12 or rng.random() < p_cut:
        return Transition("cut", 0.0)
    options = [k for k in available if k != prev_kind] or available
    dur = min(rng.uniform(cfg.transition_min, cfg.transition_max), max_dur)
    return Transition(rng.choice(options), organic(dur, rng))


def plan_timeline(
    scenes: list[Scene],
    words: list[Word],
    audio_duration: float,
    cfg: TimelineConfig,
    fps: int,
    rng: random.Random,
    transitions: list[str],
    image_ratio: float = 0.0,
    template_scenes: dict[int, str] | None = None,
    template_min: float = 3.5,
    template_seconds: dict[int, float] | None = None,
    hold: list[tuple[float, float]] | None = None,
) -> list[Shot]:
    """``hold``: trechos que ficam num shot só, cortado no começo e no fim exatos (o apresentador em tela cheia)."""
    gmax = cfg.max_clip_seconds - cfg.transition_max - 0.03
    gmin = cfg.min_clip_seconds
    boundaries = word_boundaries(words) if cfg.snap_to_words else []
    template_scenes = template_scenes or {}

    # 1) pontos de troca
    cuts: list[float] = [0.0]
    scene_of_segment: list[int] = []
    scene_change_at: set[int] = set()
    template_segment: dict[int, str] = {}  # índice do segmento -> template
    for pos, scene in enumerate(scenes):
        start = cuts[-1]
        end = audio_duration if pos == len(scenes) - 1 else max(scene.end, start + 0.1)
        if pos > 0:
            scene_change_at.add(len(cuts) - 1)
        internal: list[float] = []
        if scene.index in template_scenes:
            # o template abre a cena; o resto dela segue com b-roll normal
            template_segment[len(scene_of_segment)] = template_scenes[scene.index]
            length = end - start
            target = (template_seconds or {}).get(scene.index, min(gmax, length))
            # sobra curta demais para um b-roll: o template ocupa a cena inteira
            if length > target + gmin:
                lo = max(min(template_min, target), target - cfg.snap_tolerance)
                hi = min(length - gmin, target + cfg.snap_tolerance)
                first = _snap(start + target, boundaries, cfg.snap_tolerance, start + lo, start + hi)
                internal = [first, *split_scene(first, end, gmin, gmax, rng, boundaries, cfg.snap_tolerance)]
        else:
            internal = split_scene(start, end, gmin, gmax, rng, boundaries, cfg.snap_tolerance)
        for t in internal:
            cuts.append(t)
            scene_of_segment.append(scene.index)
        cuts.append(end)
        scene_of_segment.append(scene.index)
    cuts[-1] = audio_duration
    held: set[int] = set()
    if hold:
        cuts, scene_of_segment, template_segment, scene_change_at, held = _hold_windows(
            cuts, scene_of_segment, template_segment, hold, gmin, gmax, boundaries, cfg.snap_tolerance)
    n = len(cuts) - 1

    # 2) transições (índice k = transição de entrada do shot k)
    trans: list[Transition] = [Transition("cut", 0.0)]
    prev_kind: str | None = None
    prev_effect: str | None = None
    for k in range(1, n):
        g_prev = cuts[k] - cuts[k - 1]
        g_next = cuts[k + 1] - cuts[k]
        t = _pick_transition(cfg, transitions, prev_kind, k in scene_change_at, 0.45 * min(g_prev, g_next), rng)
        # corte seco pode virar um efeito (não nos templates, que têm animação própria)
        touches_template = k in template_segment or (k - 1) in template_segment
        if t.is_cut and cfg.effects and not touches_template and rng.random() < cfg.effect_probability:
            options = [e for e in cfg.effects if e != prev_effect] or list(cfg.effects)
            weights = [max(0.0, float(cfg.effect_weights.get(e, 1.0))) for e in options]
            kind = rng.choices(options, weights=weights)[0] if sum(weights) > 0 else rng.choice(options)
            t = Transition(kind, 0.0)
            prev_effect = t.kind
        trans.append(t)
        if not t.is_cut:
            prev_kind = t.kind

    # 3) durações "orgânicas": ajusta o próximo ponto de troca em poucos ms
    used_ms: set[int] = set()
    durations: list[float] = []
    for i in range(n):
        t_in = trans[i].duration
        t_out = trans[i + 1].duration if i + 1 < n else 0.0
        raw = cuts[i + 1] - cuts[i] + t_in / 2 + t_out / 2
        target = organic(raw, rng, used_ms)
        cuts[i + 1] = round(cuts[i + 1] + (target - raw), 4)
        used_ms.add(round(target * 1000))
        durations.append(target)

    # 4) frames a partir de posições acumuladas (sem deriva)
    shots: list[Shot] = []
    prefer_prev_image = False
    for i in range(n):
        t_in = trans[i].duration
        t_out = trans[i + 1].duration if i + 1 < n else 0.0
        start_frame = round((cuts[i] - t_in / 2) * fps)
        end_frame = round((cuts[i + 1] + t_out / 2) * fps)
        want_image = i in held or (not prefer_prev_image and rng.random() < image_ratio) \
            or template_segment.get(i) in ("showcase", "gallery", "sketch", "dossier", "report",
                                              "videowall", "paperfloat", "paperpair", "newsfloat", "newsrank",
                                              "paperrank", "photostack", "annotate", "exploded")  # foto do carro, não vídeo
        prefer_prev_image = want_image
        shots.append(Shot(
            index=i,
            scene_index=scene_of_segment[i],
            cut_in=round(cuts[i], 3),
            cut_out=round(cuts[i + 1], 3),
            clip_duration=durations[i],
            transition_in=trans[i],
            start_frame=start_frame,
            end_frame=end_frame,
            prefer_media=MediaType.IMAGE if want_image else MediaType.VIDEO,
            template=template_segment.get(i),
        ))
    # transições que viraram 0 frame após quantização viram corte seco
    for prev, cur in zip(shots, shots[1:]):
        if prev.end_frame <= cur.start_frame and not cur.transition_in.is_cut:
            cur.transition_in = Transition("cut", 0.0)
            cur.start_frame = prev.end_frame
        kind = cur.transition_in.kind
        if kind in EFFECTS:
            cur.effect_in = kind
            if kind == "whip":  # o chicote começa no shot que sai
                prev.effect_out = "whip"
    return shots
