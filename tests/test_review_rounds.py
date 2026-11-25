"""Rodadas de revisão: só as cenas com veredito novo são buscadas de novo."""

from broll_bot.models import Scene, SceneAnalysis
from broll_bot.pipeline import research_reviewed
from broll_bot.search import SceneSearchResult


def _scenes(n):
    return [Scene(i, f"cena {i}", i * 3.0, i * 3.0 + 3, SceneAnalysis("x", ["car"], "car")) for i in range(n)]


def test_second_round_researches_only_scenes_with_new_verdicts():
    # 9 baterias: cada rodada refazia as 164 cenas (8 min) com só 20 a 45 reprovações novas
    scenes = _scenes(4)
    calls = []

    def search(scene, verdicts):
        calls.append(scene.index)
        return SceneSearchResult(scene.index, needed=1)

    results, searched = {}, {}
    reviews = {0: {"a": "rejected"}, 1: {"b": "approved"}, 2: {}}
    assert research_reviewed(scenes, reviews, searched, results, search) == [0, 1]
    reviews[1]["c"] = "rejected"  # a 2ª rodada só mexeu na cena 1
    calls.clear()
    assert research_reviewed(scenes, reviews, searched, results, search) == [1]
    assert calls == [1]
    assert research_reviewed(scenes, reviews, searched, results, search) == []
