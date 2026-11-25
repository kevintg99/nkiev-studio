"""Relevância: piso semântico, negativos por frase, planos B e revisor visual."""

import json
from pathlib import Path

from PIL import Image

from broll_bot.cache import SQLiteCache
from broll_bot.config import AppConfig
from broll_bot.models import Candidate, MediaType, Scene, SceneAnalysis, Shot, Transition
from broll_bot.ranking import Ranker
from broll_bot.review import VisualReviewer, _ReviewOut
from broll_bot.search import SceneSearchResult


def cand(i, tags="car sedan", score=0.8, semantic=0.8, provider="pexels", thumb=None):
    c = Candidate(provider, str(i), MediaType.VIDEO, 1920, 1080, 10.0, None, None, tags=tags.split(),
                  description=tags, thumbnail_path=thumb)
    c.score, c.semantic = score, semantic
    return c


def scene(i=0, negatives=()):
    return Scene(i, "O Corolla XEi usa um motor 2.0 flex.", 0, 8,
                 SceneAnalysis("close-up of a modern car engine bay", ["car engine bay"], "car engine",
                               list(negatives), "technical"))


# ------------------------------------------------------------ piso semântico
def test_low_semantic_is_not_good_even_with_high_score():
    ranker = Ranker(AppConfig(), embedder=object(), thumbs=None)  # "com CLIP"
    assert not ranker.is_good(cand(1, score=0.58, semantic=0.49))  # esportivo vermelho do report
    assert ranker.is_good(cand(2, score=0.8, semantic=0.79))


def test_review_overrides_scores():
    ranker = Ranker(AppConfig(), embedder=object(), thumbs=None)
    approved, rejected = cand(1, score=0.4, semantic=0.4), cand(2, score=0.9, semantic=0.96)
    approved.review, rejected.review = "approved", "rejected"
    assert ranker.is_good(approved)
    assert not ranker.is_good(rejected)  # ex.: Mercedes com score 0.89


def test_without_clip_only_score_counts():
    ranker = Ranker(AppConfig(), embedder=None, thumbs=None)
    assert ranker.is_good(cand(1, score=0.5, semantic=0.2))


def test_negative_term_matches_whole_phrase_only():
    sc = scene(negatives=["sports car", "mercedes"])
    assert Ranker._negative_hit(sc, cand(1, "red sports car side view"))
    assert Ranker._negative_hit(sc, cand(2, "mercedes benz parked"))
    assert not Ranker._negative_hit(sc, cand(3, "white sedan car driving"))  # "car" sozinho não pega


# ----------------------------------------------------------------- planos B
def _selector(tmp_path):
    from broll_bot.selection import Selector

    cfg = AppConfig()
    ranker = Ranker(cfg, embedder=object(), thumbs=None)
    sel = Selector(cfg, {}, ranker, tmp_path, seed=1)
    sel.fetch = lambda c: tmp_path / f"{c.media_id}.mp4"  # sem download/ffprobe no teste
    shots = [Shot(i, 0, i * 4.0, i * 4.0 + 4, 4.1, Transition("cut", 0), i * 120, i * 120 + 120) for i in range(3)]
    return sel, shots


def test_repeats_relevant_clip_instead_of_off_topic_media(tmp_path):
    sel, shots = _selector(tmp_path)
    good = cand("sedan", semantic=0.93)
    plants = cand("plants", "green plants", score=0.22, semantic=0.30)
    results = {0: SceneSearchResult(0, [good, plants], needed=3)}
    sel.assign(shots[:2], results)
    assert shots[0].candidate.media_id == "sedan"
    assert shots[1].candidate.media_id == "sedan"  # outro trecho/enquadramento, nunca as plantas
    assert shots[1].fallback_reason == "reused_with_new_framing"


def test_rejected_candidate_is_never_picked_as_weak_fallback(tmp_path):
    sel, shots = _selector(tmp_path)
    mercedes = cand("mercedes", score=0.89, semantic=0.96)
    mercedes.review = "rejected"
    sel.assign(shots[:1], {0: SceneSearchResult(0, [mercedes], needed=1)})
    assert shots[0].candidate is None or shots[0].candidate.media_id != "mercedes"


# ------------------------------------------------------------------ revisor
def _thumb(tmp_path, name, color):
    path = tmp_path / f"{name}.jpg"
    Image.new("RGB", (320, 180), color).save(path)
    return str(path)


def _reviewer(tmp_path):
    return VisualReviewer(AppConfig(), SQLiteCache(tmp_path / "c.sqlite"), thumbs=None)


def test_pending_sheet_and_verdicts_are_cached(tmp_path):
    rv = _reviewer(tmp_path)
    sc = scene()
    cands = [cand(i, thumb=_thumb(tmp_path, f"t{i}", (40 * i, 80, 120))) for i in range(5)]
    results = {0: SceneSearchResult(0, cands, needed=2)}
    reviews: dict = {}
    items = rv.pending([sc], results, reviews)
    assert len(items) == 1 and len(items[0].candidates) == 5
    sheet = rv.contact_sheet(items[0])
    assert sheet.size == (4 * 384, 44 + 2 * 216)

    plan = _ReviewOut.model_validate({"scenes": [{"scene": 0, "approved": [1, 3], "rejected": [2, 4, 5, 99],
                                                  "notes": "2 é um Mercedes"}]})
    assert rv.apply(items, plan, reviews) == 5  # o 99 não existe e é ignorado
    assert reviews[0][cands[1].key] == "rejected" and reviews[0][cands[0].key] == "approved"
    assert rv.notes[0] == "2 é um Mercedes"
    # próxima execução: vem do cache e a cena já não precisa de revisão
    assert rv.cached(sc, results[0]) == reviews[0]
    assert rv.pending([sc], results, reviews) == []


def test_chat_export_and_import_roundtrip(tmp_path):
    rv = _reviewer(tmp_path)
    sc = scene(3)
    cands = [cand(i, thumb=_thumb(tmp_path, f"t{i}", (200, 30 * i, 0))) for i in range(3)]
    results = {3: SceneSearchResult(3, cands, needed=1)}
    out = tmp_path / "revisao"
    prompt = rv.export(rv.pending([sc], results, {}), out)
    assert (out / "cena_03.jpg").exists() and "Scene 3" in prompt.read_text(encoding="utf-8")
    assert json.loads((out / "mapa.json").read_text())["scenes"]["3"] == [c.key for c in cands]

    (out / "resposta.json").write_text('Claro! ```json\n{"scenes": [{"scene": 3, "approved": [2], '
                                       '"rejected": [1, 3]}]}\n```', encoding="utf-8")
    reviews: dict = {}
    assert rv.import_file(out / "resposta.json", [sc], reviews) == 3
    assert reviews[3] == {cands[0].key: "rejected", cands[1].key: "approved", cands[2].key: "rejected"}
    assert _reviewer(tmp_path).cached(sc, results[3]) == reviews[3]  # vale nas próximas execuções


def test_search_applies_reviews_and_keeps_looking(tmp_path):
    from tests.test_fallback import FakeProvider, make

    local = FakeProvider("local", results=[cand(1, "car engine bay", provider="local")], remote=False)
    remote = FakeProvider("remote", results=[cand(2, "car engine bay", provider="remote")])
    searcher, _ = make(tmp_path, [local, remote], reserve_per_scene=0, strategy="fallback")
    sc = scene()
    first = searcher.search_scene(sc, needed=1, needed_images=0, used_keys=set())
    assert not remote.calls  # o local bastou
    rejected = {first.ranked[0].key: "rejected"}
    again = searcher.search_scene(sc, needed=1, needed_images=0, used_keys=set(), reviews=rejected)
    assert remote.calls  # reprovado pelo revisor -> buscou na próxima fonte
    assert again.ranked[-1].review == "rejected"


def test_candidates_omitted_by_the_ai_count_as_rejected(tmp_path):
    rv = _reviewer(tmp_path)
    sc = scene()
    cands = [cand(i, thumb=_thumb(tmp_path, f"o{i}", (30 * i, 60, 90))) for i in range(4)]
    results = {0: SceneSearchResult(0, cands, needed=2)}
    reviews: dict = {}
    items = rv.pending([sc], results, reviews)
    plan = _ReviewOut.model_validate({"scenes": [{"scene": 0, "approved": [], "rejected": [2, 3]}]})
    rv.apply(items, plan, reviews)
    assert all(v == "rejected" for v in reviews[0].values()) and len(reviews[0]) == 4


def test_neighbor_clip_does_not_come_back_right_away(tmp_path):
    """A, B, A, B em 12 s (o mecânico de azul e a frente do carro): repetição só longe."""
    sel, _ = _selector(tmp_path)
    shots = [Shot(i, 0 if i < 2 else 1, i * 3.0, i * 3.0 + 3, 3.1, Transition("cut", 0), i * 90, i * 90 + 90)
             for i in range(5)]
    a, b = cand("mechanic", "mechanic walking garage", semantic=0.9), cand("front", "car front grille", semantic=0.9)
    c, d = cand("street", "parked cars street", semantic=0.8), cand("keys", "car keys hand", semantic=0.8)
    results = {0: SceneSearchResult(0, [a, b, c, d], needed=2), 1: SceneSearchResult(1, [], needed=3)}
    sel.assign(shots, results)
    ids = [s.candidate.media_id if s.candidate else None for s in shots]
    assert ids[:2] == ["mechanic", "front"]
    assert set(ids[2:4]) == {"street", "keys"}  # sobra nova da cena vizinha antes de repetir
    assert ids[4] not in ids[2:4]  # acabou tudo: gira pelo mais antigo, nunca A, B, A, B
    later = Shot(9, 1, 80.0, 83.0, 3.1, Transition("cut", 0), 2400, 2490)
    sel.assign([later], results)  # bem depois, a repetição volta a valer
    assert later.candidate is not None and later.candidate.media_id in ("mechanic", "front")


def test_photo_without_thumbnail_is_not_trusted_by_its_title():
    """'Contract Signing Ceremony' bate com 'car finance contract' no texto, mas sem miniatura a IA
    não viu a foto: não pode ganhar nota máxima nem entrar como foto boa."""
    ranker = Ranker(AppConfig(), embedder=object(), thumbs=None)
    sc = Scene(0, "Beaucoup de voitures sont conçues pour tenir la durée d'un crédit.", 0, 5,
               SceneAnalysis("car finance contract", ["car finance contract"], "car contract"))
    photo = cand("ceremony", "contract signing ceremony finance", provider="wikimedia")
    photo.media_type = MediaType.IMAGE
    ranked = ranker.rank(sc, [photo])
    assert ranked[0].semantic < AppConfig().search.min_semantic and not ranker.is_good(ranked[0])


def test_own_video_repeats_at_most_twice_in_a_row_of_shots(tmp_path):
    """O mesmo vídeo 3 vezes em 13 s (pixabay 198888 na seção do Berlingo): no máximo 2."""
    sel, _ = _selector(tmp_path)
    shots = [Shot(i, 0, i * 3.0, i * 3.0 + 3, 3.1, Transition("cut", 0), i * 90, i * 90 + 90) for i in range(4)]
    good = cand("van", "white van street", semantic=0.9)
    other = cand("old", "old engine bay", semantic=0.8)
    results = {0: SceneSearchResult(0, [good], needed=4)}
    sel.assign(shots[:2], results)
    sel.recent.insert(0, other)  # algo que apareceu bem antes, para girar
    sel.path_map[other.key] = tmp_path / "old.mp4"
    sel.used_at[other.key], sel.use_times[other.key] = -100.0, [-103.0]
    sel.assign(shots[2:], results)
    ids = [s.candidate.media_id for s in shots]
    assert ids[:2] == ["van", "van"] and ids.count("van") <= 3 and ids[2] == "old"
