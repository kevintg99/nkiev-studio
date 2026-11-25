"""Buscas de b-roll sempre em inglês (a IA às vezes escreve no idioma do roteiro)."""

from broll_bot import query_lang
from broll_bot.cache import SQLiteCache
from broll_bot.config import AnalysisConfig
from broll_bot.models import Scene, SceneAnalysis


def test_foreign_queries_are_detected_but_english_ones_are_not():
    assert query_lang.is_foreign("voiture occasion parking particulier", "fr")
    assert query_lang.is_foreign("dacia logan mcv exterieur break", "fr")
    assert query_lang.is_foreign("carro usado na garagem", "pt")
    assert query_lang.is_foreign("samochód na drodze", "pl")
    for q in ("used car dealership", "dacia logan mcv rear", "mechanic workshop engine", "toyota prius taxi paris"):
        assert not query_lang.is_foreign(q, "fr"), q


def _scene(i, queries, summary="voiture sur la route"):
    return Scene(i, "x", i, i + 1, SceneAnalysis(summary, queries, "voiture", negative_terms=["pneus"]))


def test_plan_in_french_is_translated_once_and_cached(tmp_path, monkeypatch):
    calls = []

    def fake(cfg, system, prompt, **kw):
        items = __import__("json").loads(prompt.split("\n", 1)[1])
        calls.append(items)
        table = {"voiture occasion parking": "used car parking lot", "dacia logan mcv exterieur": "dacia logan mcv exterior",
                 "voiture sur la route": "car on the road", "voiture": "car", "pneus": "tires"}
        return {"t": [table.get(t, t) for t in items]}

    monkeypatch.setattr(query_lang, "_llm_json", fake)
    cfg = AnalysisConfig(provider="openai", script_language="fr")
    cache = SQLiteCache(tmp_path / "c.sqlite")
    scenes = [_scene(0, ["voiture occasion parking", "dacia logan mcv exterieur"]), _scene(1, ["mechanic workshop"])]
    notes = query_lang.ensure_english(scenes, cfg, cache)
    assert notes and len(calls) == 1
    a = scenes[0].analysis
    assert a.queries == ["used car parking lot", "dacia logan mcv exterior"]
    assert a.visual_summary == "car on the road" and a.fallback_query == "car" and a.negative_terms == ["tires"]
    assert scenes[1].analysis.queries == ["mechanic workshop"]
    # plano igual de novo (do cache do plano): a tradução também vem do cache, sem chamar a IA
    again = [_scene(0, ["voiture occasion parking", "dacia logan mcv exterieur"]), _scene(1, ["mechanic workshop"])]
    query_lang.ensure_english(again, cfg, cache)
    assert len(calls) == 1 and again[0].analysis.queries[0] == "used car parking lot"


def test_english_plan_is_left_alone(monkeypatch):
    monkeypatch.setattr(query_lang, "_llm_json", lambda *a: (_ for _ in ()).throw(AssertionError("não devia chamar")))
    cfg = AnalysisConfig(provider="openai", script_language="fr")
    scenes = [_scene(0, ["used car dealership", "dacia logan mcv rear"], "car on a road")]
    assert query_lang.ensure_english(scenes, cfg, None) == []


def test_translation_failure_keeps_the_plan(monkeypatch):
    monkeypatch.setattr(query_lang, "_llm_json", lambda *a: {"t": ["só um"]})  # tamanho errado
    cfg = AnalysisConfig(provider="openai", script_language="fr")
    scenes = [_scene(0, ["voiture occasion parking", "voiture rouge"])]
    assert query_lang.ensure_english(scenes, cfg, None) == []
    assert scenes[0].analysis.queries == ["voiture occasion parking", "voiture rouge"]


def test_long_scene_parts_get_their_own_queries(tmp_path, monkeypatch):
    """A IA fez uma cena de 3 min para o carro inteiro: dividida, cada parte herdava as mesmas 4
    buscas e o vídeo repetia imagens. Agora cada parte ganha buscas sobre o que ela fala."""
    import json

    from broll_bot.cues import set_language
    from broll_bot.plan_fix import split_long_scenes
    from broll_bot.transcription import parse_script

    set_language("fr")
    sents = parse_script("Le moteur 1.4 a une chaîne de distribution. La banquette arrière coulisse. "
                         "La garantie Kia dure sept ans. Il coûte entre 5 000 et 9 000 euros.")
    for k, s in enumerate(sents):
        s.start, s.end = k * 10.0, k * 10.0 + 10.0
    big = Scene(0, " ".join(s.text for s in sents), 0, 40, SceneAnalysis(
        "kia venga", ["kia venga exterior", "kia venga interior"], "car", subject="Kia Venga"))
    parts, _ = split_long_scenes([big], sents, max_seconds=12, target_seconds=10)
    assert len(parts) >= 3 and all(p.analysis.requery for p in parts)

    calls = []

    def fake(cfg, system, prompt, **kw):
        items = json.loads(prompt.split("\n", 1)[1])
        calls.append(items)
        assert all(it["car"] == "Kia Venga" for it in items)
        return {"r": [{"q": [f"kia venga detail {n}", f"mechanic {n}"], "v": f"shot {n}"} for n in range(len(items))]}

    monkeypatch.setattr(query_lang, "_llm_json", fake)
    cfg = AnalysisConfig(provider="openai", script_language="fr")
    cache = SQLiteCache(tmp_path / "c.sqlite")
    notes = query_lang.requery_parts(parts, cfg, cache)
    assert notes and len(calls) == 1
    firsts = [p.analysis.queries[0] for p in parts]
    assert len(set(firsts)) == len(parts)  # cada parte com a sua busca
    assert all(not p.analysis.requery for p in parts)
    # de novo com o mesmo texto: vem do cache, sem chamar a IA
    again, _ = split_long_scenes([Scene(0, big.text, 0, 40, SceneAnalysis(
        "kia venga", ["kia venga exterior"], "car", subject="Kia Venga"))], sents, max_seconds=12, target_seconds=10)
    query_lang.requery_parts(again, cfg, cache)
    assert len(calls) == 1 and again[0].analysis.queries[0] == firsts[0]


def test_cut_requery_batch_is_retried_in_smaller_batches(tmp_path, monkeypatch):
    """No vídeo do Dale a resposta do lote veio cortada pelo limite de tokens e as cenas divididas ficaram
    todas com as buscas genéricas da cena original. Agora o lote é refeito em lotes menores."""
    import json

    scenes = [Scene(k, f"Excerpt number {k} about speed cameras and radar.", k * 10, k * 10 + 10,
                    SceneAnalysis("radar", ["highway traffic"], "car", requery=True)) for k in range(8)]
    calls = []

    def fake(cfg, system, prompt, **kw):
        items = json.loads(prompt.split("\n", 1)[1])
        calls.append(len(items))
        if len(items) > 2:
            raise RuntimeError("resposta cortada pelo limite de tokens (aumente max_tokens)")
        return {"r": [{"i": it["i"], "q": [f"police radar gun {len(calls)}-{it['i']}"], "v": "radar"} for it in items]}

    monkeypatch.setattr(query_lang, "_llm_json", fake)
    query_lang.requery_parts(scenes, AnalysisConfig(provider="openai", script_language="en"),
                             SQLiteCache(tmp_path / "c.sqlite"))
    assert calls == [8, 4, 2, 2, 4, 2, 2]  # corta ao meio até caber
    assert all(s.analysis.queries[0].startswith("police radar gun") for s in scenes)


def test_requery_batches_run_in_parallel_fast_and_with_a_time_limit(tmp_path, monkeypatch):
    """Vídeo dos 9 pneus de inverno: 124 pedaços em lotes de 20, um por vez, com o gpt-5-mini pensando até
    estourar os 8.000 tokens e uma chamada pendurada 10 min = 19 min. Agora lotes de 10, 4 ao mesmo tempo, com
    raciocínio mínimo e tempo máximo por chamada."""
    import json
    import threading
    import time

    scenes = [Scene(k, f"Excerpt {k} about winter tires braking on ice.", k * 10, k * 10 + 10,
                    SceneAnalysis("tires", ["winter tire"], "tire", requery=True)) for k in range(40)]
    sizes, kws, live, peak = [], [], [0], [0]
    lock = threading.Lock()

    def fake(cfg, system, prompt, **kw):
        items = json.loads(prompt.split("\n", 1)[1])
        with lock:
            sizes.append(len(items))
            kws.append(kw)
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.15)
        with lock:
            live[0] -= 1
        return {"r": [{"i": it["i"], "q": [f"ice braking {it['i']}"], "v": "ice"} for it in items]}

    monkeypatch.setattr(query_lang, "_llm_json", fake)
    query_lang.requery_parts(scenes, AnalysisConfig(provider="openai", script_language="en"),
                             SQLiteCache(tmp_path / "c.sqlite"))
    assert sizes == [10, 10, 10, 10] and peak[0] > 1  # ao mesmo tempo
    assert all(kw.get("effort") == "minimal" and 0 < kw.get("timeout", 0) <= 180 for kw in kws)
    assert all(not s.analysis.requery and s.analysis.queries[0].startswith("ice braking") for s in scenes)


def test_openai_client_with_time_limit_does_not_wait_ten_minutes(monkeypatch):
    import sys
    import types

    made = []
    fake_openai = types.SimpleNamespace(OpenAI=lambda **kw: made.append(kw) or object())
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    from broll_bot.llm_openai import OpenAIJSON

    OpenAIJSON("gpt-5-mini", "minimal", timeout=120)
    OpenAIJSON("gpt-5")
    assert made == [{"timeout": 120, "max_retries": 1}, {}]
