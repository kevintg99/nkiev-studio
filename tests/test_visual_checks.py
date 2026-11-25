"""Checagens visuais: prédio num canal de carro, carro batido, outro carro da lista, duplicada."""

from pathlib import Path

import numpy as np

from broll_bot import visual_checks as vc
from broll_bot.config import AppConfig
from broll_bot.models import Candidate, MediaType, Scene, SceneAnalysis
from broll_bot.ranking import Ranker
from broll_bot.selection import Selector

TEXTS = vc.check_texts()
DIM = len(TEXTS) + 4  # um eixo por frase + eixos dos carros de referência


def unit(*weights):
    v = np.zeros(DIM)
    for axis, w in weights:
        v[axis] = w
    v[-1] = 0.2  # componente comum
    return v / np.linalg.norm(v)


C_EMB = np.stack([unit((k, 1.0)) for k in range(len(TEXTS))])
CAR, BUILDING = 0, TEXTS.index("a building facade or an apartment block")
CRASH, INTACT = TEXTS.index(vc.CRASH_PROMPT), TEXTS.index(vc.INTACT_PROMPT)
LOGAN_AXIS, JAZZ_AXIS = DIM - 3, DIM - 2


def cand(i, local=None):
    return Candidate("pexels", str(i), MediaType.IMAGE, 1920, 1080, None, None, None, local_path=local)


def scene(text="La Logan est simple.", queries=("dacia logan mcv rear",), subject="Dacia Logan MCV"):
    return Scene(0, text, 0, 5, SceneAnalysis("dacia logan mcv parked", list(queries), "car", subject=subject))


def run(sc, imgs, refs=None, locals_=()):
    pool = [cand(i, "x.jpg" if i in locals_ else None) for i in range(len(imgs))]
    sem = np.full(len(imgs), 0.5)
    vc.apply_checks(sc, pool, list(range(len(imgs))), np.stack(imgs), C_EMB, refs or {}, True, sem)
    return pool, sem


def test_building_is_vetoed_in_a_car_video_but_not_when_the_scene_asks_for_it():
    car_img = unit((CAR, 1.0), (INTACT, 0.3))
    building = unit((BUILDING, 1.0))
    pool, _ = run(scene(), [car_img, building])
    assert pool[0].veto == "" and pool[1].veto.startswith("não mostra carro")
    # cena sobre a fábrica: prédio liberado
    pool, _ = run(scene("L'usine de Valenciennes.", ("toyota factory building valenciennes",)), [building])
    assert pool[0].veto == ""
    # foto sua nunca é vetada
    pool, _ = run(scene(), [building], locals_=(0,))
    assert pool[0].veto == ""


def test_crashed_car_only_when_the_narration_talks_about_it():
    wreck = unit((CAR, 1.0), (CRASH, 0.9))
    pool, _ = run(scene(), [wreck])
    assert pool[0].veto == "carro batido/destruído"
    pool, _ = run(scene("Ils y ont mis le feu, et il a redémarré."), [wreck])
    assert pool[0].veto == ""


def test_photo_closer_to_another_car_of_the_list_is_vetoed_and_the_right_one_boosted():
    refs = {"Dacia Logan MCV": np.stack([unit((CAR, 0.5), (LOGAN_AXIS, 1.0))]),
            "Honda Jazz": np.stack([unit((CAR, 0.5), (JAZZ_AXIS, 1.0))])}
    looks_jazz = unit((CAR, 0.5), (JAZZ_AXIS, 1.0), (INTACT, 0.1))
    looks_logan = unit((CAR, 0.5), (LOGAN_AXIS, 1.0), (INTACT, 0.1))
    pool, sem = run(scene(), [looks_jazz, looks_logan], refs)
    assert pool[0].veto == "parece outro carro da lista"
    assert pool[1].veto == "" and sem[1] > 0.5


def test_vetoed_candidate_is_never_good_even_if_approved():
    r = Ranker(AppConfig(), None, None)
    c = cand(1)
    c.review, c.veto = "approved", "carro batido/destruído"
    assert not r.is_good(c)


def test_same_photo_from_another_source_is_a_duplicate():
    cfg = AppConfig()
    sel = Selector(cfg, {}, Ranker(cfg, None, None), Path("."), seed=1)
    a, b, other = cand(1), cand(2), cand(3)
    a.embedding = unit((CAR, 1.0), (LOGAN_AXIS, 0.5))
    b.embedding = a.embedding * 0.999 + unit((INTACT, 1.0)) * 0.001
    b.embedding /= np.linalg.norm(b.embedding)
    other.embedding = unit((CAR, 1.0), (JAZZ_AXIS, 0.9))
    sel.recent.append(a)
    assert sel.is_duplicate(b) and not sel.is_duplicate(other)


def test_named_photo_and_generic_car_are_not_vetoed_by_the_references():
    """510 fotos boas barradas: o título já dizia o carro certo, ou a foto só era um carro qualquer
    um pouco mais perto de uma das ~60 referências dos outros carros por acaso."""
    rng = np.random.default_rng(3)
    refs = {"Dacia Logan MCV": np.stack([unit((CAR, 0.5), (LOGAN_AXIS, 1.0))] * 2),
            "Honda Jazz": np.stack([unit((CAR, 0.5), (JAZZ_AXIS, 1.0))] * 2)}
    # 10 carros "genéricos" de referência: cada um com um eixo aleatório fraco
    for k in range(10):
        refs[f"Car {k}"] = np.stack([unit((CAR, 0.6), (int(rng.integers(5, DIM - 4)), 1.0)) for _ in range(6)])
    generic = unit((CAR, 1.0), (INTACT, 0.1))  # um carro qualquer: parecido com todo mundo um pouco
    named = unit((CAR, 0.5), (JAZZ_AXIS, 1.0), (INTACT, 0.1))  # parece o Jazz, mas o título diz Logan
    pool = [cand(0), cand(1)]
    pool[1].description = "Dacia Logan MCV 2010 rear"
    sem = np.full(2, 0.5)
    imgs = np.stack([generic, named])
    r_other = max(float(np.sort(v @ generic)[-2:].mean()) for k, v in refs.items() if k != "Dacia Logan MCV")
    r_own = float((refs["Dacia Logan MCV"] @ generic).max())
    assert r_other - r_own > vc.REF_MARGIN and r_other < vc.REF_SURE  # antes: vetado por acaso
    vc.apply_checks(scene(), pool, [0, 1], imgs, C_EMB, refs, True, sem)
    assert pool[0].veto == "" and pool[1].veto == ""


def test_spoken_generation_filters_photos_of_other_generations():
    from broll_bot.subject import first_generation_era, section_generations, wrong_era, wrong_generation

    texts = ["Numéro 6 : le Citroën Berlingo de première génération, et son jumeau, le Peugeot Partner.",
             "Numéro 7 : le Honda CR-V de troisième génération, entre 2007 et 2012, en 2.0 essence.",
             "Sur la génération 2010–2017, le moteur 1.2 essence est un modèle du genre."]
    gens = section_generations(texts, ["Citroën Berlingo", "Honda CR-V", "Suzuki Swift"])
    assert gens == {"Citroën Berlingo": 1, "Honda CR-V": 3}
    assert wrong_generation("Honda CR-V (fourth generation) Brazil", gens["Honda CR-V"])
    assert not wrong_generation("Honda CR-V third generation 2009", gens["Honda CR-V"])
    assert not wrong_generation("Honda CR-V 2.0 i-VTEC", gens["Honda CR-V"])  # sem geração no título: passa
    eras = first_generation_era(gens, {"Honda CR-V": (2005, 2017)})
    assert eras["Honda CR-V"] == (2005, 2017)  # anos falados continuam valendo
    assert wrong_era("Citroën ë-Berlingo 50 kWh Electric 03-2025", eras["Citroën Berlingo"])
    assert not wrong_era("Citroën Berlingo 2.0 HDI (1999)", eras["Citroën Berlingo"])
