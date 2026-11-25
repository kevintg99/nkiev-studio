"""Checagens visuais com o CLIP, antes de qualquer imagem ir para o vídeo.

* **Não é do nicho:** num canal de carros, prédio, paisagem, retrato, comida ou documento
  sem nenhum carro é vetado (a não ser que a própria cena peça isso: "fábrica", "cidade"...).
* **Carro batido:** carro acidentado, queimado ou destruído é vetado, a não ser que a fala
  seja sobre acidente ("crash", "accidenté", "batida", "wypadek"...).
* **Referência do carro:** no ranking com vários carros, cada carro tem fotos de referência
  (as da sua pasta e, se faltar, as do Wikimedia cujo título cita o modelo). Foto de carro que
  se parece mais com OUTRO carro da lista do que com o da seção é vetada; a que se parece com
  o carro certo ganha pontos.

O CLIP compara a miniatura com frases ("a photo of a car", "a building facade"...): é o
mesmo modelo que já dá a nota de relevância, então não custa nada a mais além de alguns
milissegundos por imagem.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .log import get_logger
from .text import normalize

if TYPE_CHECKING:  # pragma: no cover
    from .models import Candidate, Scene

log = get_logger("visual_checks")

# o que conta como "do nicho" (carro, peça, oficina, estrada com carros, gente dirigindo...)
AUTO_PROMPTS = (
    "a photo of a car", "a car interior with dashboard and steering wheel", "a car engine bay",
    "a car wheel, tire or brake", "a mechanic working on a car in a garage", "cars driving on a road",
    "a car dealership or a parking lot full of cars", "a person driving a car", "a close-up of a car part",
    "a pickup truck or an SUV off-road", "a car factory assembly line",
    "car tires stacked on a rack in a tire shop", "a car driving on a snowy or icy road",
    "a petrol station forecourt with fuel pumps", "a fuel nozzle refuelling a car",
    "rusted metal on a car underbody or wheel arch", "a salt truck spreading road salt on a winter road",
    "a car lifted on a hoist in a repair shop",
)
# o que não é, com as palavras da cena que liberam aquele assunto
# (vídeo explicativo fala de banco, seguradora, analistas, feira, relatório: aí essas imagens valem)
OFF_PROMPTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("a building facade or an apartment block", ("building", "house", "home", "facade", "apartment", "factory", "plant",
                                                 "bank", "office", "offices", "headquarters", "hall", "exhibition",
                                                 "showroom", "showrooms", "dealership", "warehouse", "shop", "store",
                                                 "bureau", "university", "centre", "center")),
    ("a city skyline or architecture", ("city", "skyline", "town", "paris", "architecture", "street", "london",
                                        "shanghai", "tokyo", "britain", "china", "japan", "europe", "texas")),
    ("a landscape or nature without any car", ("landscape", "mountain", "countryside", "field", "farm", "forest", "desert", "sea")),
    ("a portrait of a person", ("person", "man", "woman", "people", "driver", "mechanic", "customer", "family", "farmer",
                                "analyst", "analysts", "engineer", "engineers", "worker", "workers", "team", "staff",
                                "expert", "experts", "director", "dealer", "dealers", "salesman", "seller", "owner",
                                "owners", "businessman", "meeting", "insurer", "insurers", "buyer", "buyers",
                                "employee", "employees", "technician", "technicians", "researcher", "researchers",
                                "delegates", "visitors", "crowd", "audience", "presenter", "host")),
    ("food or a meal", ("food", "meal", "picnic")),
    ("a document, a chart or a phone screenshot", ("document", "paper", "invoice", "phone", "smartphone", "screen", "listing", "chart",
                                                   "report", "book", "survey", "data", "spreadsheet", "terms", "contract",
                                                   "warranty", "policy", "quote", "website", "laptop", "computer", "graph",
                                                   "statistics", "figures", "paperwork", "receipt", "form", "insurance",
                                                   "comparison", "map", "papers")),
    ("an animal", ("animal", "dog", "cat")),
    ("a monument, a church or a statue", ("monument", "church", "statue")),
)
CRASH_PROMPT = "a crashed, wrecked or burned car after an accident"
INTACT_PROMPT = "a clean undamaged car in good condition"
# fala sobre acidente/destruição libera carro batido (pt, fr, pl, en)
ACCIDENT_WORDS = re.compile(
    r"acident|batid|bateu|destru|incendi|fogo|queimad|ferro-velho|sucata|"
    r"accident|epave|detrui|feu|brul|casse|demolition|"
    r"wypad|rozbit|spalon|zniszcz|zlomowisk|"
    r"crash|wreck|burn|destroy|scrap|junkyard|fire|collision|write-?off|written\s+off|damage|knock|salvage|"
    r"choc|sinistr|dommag|colis|dano|danific|kolizj|szkod|"
    r"rouill|ferrug|rdz|rust|corros", re.I)  # ferrugem também libera (foto de lataria enferrujada)

OFF_MARGIN = 0.02      # quanto a frase "não é do nicho" precisa ganhar da melhor frase do nicho (cosseno)
CRASH_MARGIN = 0.025   # idem para carro batido
REF_MARGIN = 0.03      # quanto a foto precisa parecer mais com outro carro da lista para ser vetada
REF_SURE = 0.8         # e o quanto precisa parecer com ele de verdade (cosseno CLIP entre fotos)
CAR_PHOTO = 0.21       # cosseno mínimo com "a photo of a car" para a checagem de referência valer


# o nicho do vídeo segundo o diretor de IA (director.apply_niche): o que é imagem do assunto e o que é fora dele
_NICHE_ON: list[str] = []
_NICHE_OFF: list[str] = []


def set_niche(on: list[str], off: list[str]) -> None:
    _NICHE_ON[:] = [p for p in on if p.strip()]
    _NICHE_OFF[:] = [p for p in off if p.strip()]


def check_texts() -> list[str]:
    return [*AUTO_PROMPTS, *(p for p, _ in OFF_PROMPTS), CRASH_PROMPT, INTACT_PROMPT, *_NICHE_ON, *_NICHE_OFF]


_GENERIC_WORDS = {"photo", "image", "picture", "close", "shot", "view", "with", "without", "the", "and", "of", "on",
                  "in", "or", "any", "some", "person", "people"}


def apply_checks(scene: "Scene", pool: list["Candidate"], idx: list[int], i_emb: np.ndarray, c_emb: np.ndarray,
                 refs: dict[str, np.ndarray], automotive: bool, clip_sem: np.ndarray,
                 off_margin: float = OFF_MARGIN, crash_margin: float = CRASH_MARGIN,
                 ref_margin: float = REF_MARGIN) -> None:
    """``c_emb``: embeddings de ``check_texts()``; ``clip_sem[j]`` é a relevância de ``pool[idx[j]]``.
    Marca ``veto`` nos candidatos e sobe a relevância de quem parece o carro certo."""
    n_auto, n_off = len(AUTO_PROMPTS), len(OFF_PROMPTS)
    sims = i_emb @ c_emb.T
    s_auto = sims[:, :n_auto].max(axis=1)
    s_car = sims[:, 0]
    s_off = sims[:, n_auto:n_auto + n_off]
    base = n_auto + n_off + 2
    s_crash, s_intact = sims[:, base - 2], sims[:, base - 1]
    n_on = len(_NICHE_ON)
    s_on = sims[:, base:base + n_on].max(axis=1) if n_on else None
    s_niche_off = sims[:, base + n_on:base + n_on + len(_NICHE_OFF)]
    if automotive and s_on is not None:  # "winter tire tread", "salt truck": também é imagem do nicho
        s_auto = np.maximum(s_auto, s_on)
    # buscas, descrição e a própria fala (se a narração fala de banco ou de analistas, a foto deles vale)
    scene_words = set(re.findall(r"[a-z]+", normalize(" ".join([*scene.analysis.queries, scene.analysis.visual_summary,
                                                               scene.text]))))
    allowed = np.array([not (scene_words & set(words)) for _, words in OFF_PROMPTS], dtype=bool)
    # nicho que não é carro: o "fora do assunto" é o que o diretor disse (praia num vídeo de finanças), a não ser
    # que a fala da cena cite aquilo ("a viagem à praia")
    niche_allowed = np.array([not (scene_words - _GENERIC_WORDS) & (set(re.findall(r"[a-z]+", normalize(p)))
                                                                      - _GENERIC_WORDS) for p in _NICHE_OFF], dtype=bool)
    accident_talk = bool(ACCIDENT_WORDS.search(normalize(scene.text)))
    from .subject import car_scene, is_component, names_car

    # a fala é de carro: a imagem tem que mostrar carro. Fala de outra coisa (banco, feira, pessoas, bateria):
    # vale o que a fala diz; só bicho, comida e monumento que ninguém citou continuam fora
    talks_car = car_scene(scene)
    misc = np.array([p.startswith(("food", "an animal", "a monument")) for p, _ in OFF_PROMPTS], dtype=bool)

    car = scene.analysis.subject
    own = refs.get(car) if car else None
    others = [v for k, v in refs.items() if k != car] if car else []

    def closeness(ref: np.ndarray, emb: np.ndarray) -> float:
        # média das 2 referências mais parecidas: com o máximo, 60 fotos dos outros carros contra 6
        # do carro certo ganham quase sempre por acaso (510 fotos boas barradas num vídeo)
        top = np.sort(ref @ emb)[-2:]
        return float(top.mean())

    for j, i in enumerate(idx):
        c = pool[i]
        if c.local_path:  # foto sua: você já escolheu
            continue
        if automotive and talks_car and allowed.any():
            worst = float(s_off[j][allowed].max())
            if worst - float(s_auto[j]) > off_margin:
                c.veto = "não mostra carro (fora do nicho)"
                continue
        elif not automotive and s_on is not None and len(_NICHE_OFF) and niche_allowed.any():
            if float(s_niche_off[j][niche_allowed].max()) - float(s_on[j]) > off_margin:
                c.veto = "fora do nicho do vídeo"
                continue
        elif automotive and (allowed & misc).any():
            best_ok = max(float(s_auto[j]), float(s_off[j][~misc].max()))
            if float(s_off[j][allowed & misc].max()) - best_ok > off_margin:
                c.veto = "fora do assunto da fala"
                continue
        if automotive and not accident_talk and float(s_crash[j]) - float(s_intact[j]) > crash_margin:
            c.veto = "carro batido/destruído"
            continue
        if own is not None and len(own) and float(s_car[j]) >= CAR_PHOTO and not is_component(car):
            r_own = closeness(own, i_emb[j])
            r_other = max((closeness(o, i_emb[j]) for o in others if len(o)), default=-1.0)
            # o título/as tags já dizem que é o carro certo ("Citroën Berlingo 2.0 HDi (1999)"): não barra;
            # e só barra quem é MESMO parecido com o outro carro, não qualquer carro genérico
            named = names_car(" ".join([c.description, *c.tags, c.page_url.rsplit("/", 1)[-1]]), car)
            if not named and r_other >= REF_SURE and r_other - r_own > ref_margin:
                c.veto = "parece outro carro da lista"
                continue
            # parece o carro certo: sobe no ranking (0,8 de cosseno já é bem parecido no CLIP)
            clip_sem[j] = min(1.0, clip_sem[j] + max(0.0, r_own - 0.75) * 0.8)


def build_references(cars: list[str], local_entries: list["Candidate"], search_fn, thumbs, embedder,
                     per_car: int = 6) -> dict[str, np.ndarray]:
    """Fotos de referência de cada carro: as da sua pasta (até ``per_car``) e, faltando, as do
    Wikimedia cujo título cita o modelo. ``search_fn(query) -> list[Candidate]``."""
    from .subject import names_car, search_name, wrong_model

    if embedder is None or not cars:
        return {}
    refs: dict[str, np.ndarray] = {}
    for car in cars:
        paths: list[Path] = []
        for c in local_entries:
            if c.media_type.value == "image" and names_car(c.tags, car) and c.local_path:
                paths.append(Path(c.local_path))
            if len(paths) >= per_car:
                break
        if len(paths) < 3 and search_fn is not None:
            try:
                found = search_fn(search_name(car))
            except Exception as exc:  # noqa: BLE001 - referência é um extra
                log.warning("sem referências do Wikimedia", extra={"stage": "references", "car": car, "error": str(exc)})
                found = []
            text = lambda c: " ".join([c.description, *c.tags, c.page_url.rsplit("/", 1)[-1]])  # noqa: E731
            good = [c for c in found if names_car(text(c), car) and not wrong_model(text(c), car)][: per_car - len(paths)]
            if good and thumbs is not None:
                thumbs.fetch([c for c in good if not c.thumbnail_path])
            paths += [Path(c.thumbnail_path) for c in good if c.thumbnail_path and Path(c.thumbnail_path).exists()]
        if paths:
            try:
                refs[car] = embedder.encode_images(paths)
            except Exception as exc:  # noqa: BLE001
                log.warning("referência não pôde ser lida", extra={"stage": "references", "car": car, "error": str(exc)})
    if refs:
        log.info("fotos de referência por carro", extra={"stage": "references",
                                                         "carros": {k: len(v) for k, v in refs.items()}})
    return refs
