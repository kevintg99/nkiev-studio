"""Fallback entre fontes, orçamento de buscas por cena, cache e circuit breaker."""

import random
from pathlib import Path

from broll_bot.cache import SQLiteCache
from broll_bot.config import AppConfig
from broll_bot.errors import AuthError, ProviderError, RateLimitedError
from broll_bot.models import Candidate, MediaType, Scene, SceneAnalysis
from broll_bot.providers.base import BaseProvider
from broll_bot.ranking import Ranker
from broll_bot.search import BrollSearcher


def cand(provider: str, i: int, tags: str, media=MediaType.VIDEO) -> Candidate:
    return Candidate(provider=provider, media_id=str(i), media_type=media, width=1920, height=1080,
                     duration=None if media is MediaType.IMAGE else 12.0, thumbnail_url=None,
                     download_url=f"https://x/{i}.mp4", tags=tags.split(), description=tags)


class FakeProvider(BaseProvider):
    supported_media = (MediaType.VIDEO, MediaType.IMAGE)

    def __init__(self, name: str, results=None, error: Exception | None = None, remote: bool = True):
        self.name = name  # type: ignore[misc]
        self.is_remote = remote  # type: ignore[misc]
        self.results = results or []
        self.error = error
        self.calls: list[tuple[str, MediaType]] = []

    def search(self, query, media_type, limit, language="en"):
        self.calls.append((query, media_type))
        if self.error:
            raise self.error
        return [c for c in self.results if c.media_type is media_type][:limit]

    def download(self, candidate, dest_dir) -> Path:  # pragma: no cover - não usado aqui
        raise NotImplementedError


def scene(queries=("city night traffic", "night street cars"), fallback="city"):
    return Scene(0, "Na cidade, o trânsito à noite...", 0, 10,
                 SceneAnalysis("city street at night with car traffic", list(queries), fallback, ["cartoon"], "calm"))


def make(tmp_path, providers, **search_overrides):
    cfg = AppConfig()
    cfg.search.provider_order = [p.name for p in providers]
    cfg.search.min_score = 0.3
    for k, v in search_overrides.items():
        setattr(cfg.search, k, v)
    ranker = Ranker(cfg, embedder=None, thumbs=None)
    cache = SQLiteCache(tmp_path / "cache.sqlite")
    return BrollSearcher(cfg, {p.name: p for p in providers}, ranker, cache, random.Random(1)), cache


GOOD = [cand("backup", i, "city night traffic cars street") for i in range(6)]


def test_failing_source_falls_back_to_next(tmp_path):
    broken = FakeProvider("broken", error=ProviderError("HTTP 500"))
    backup = FakeProvider("backup", results=GOOD)
    searcher, _ = make(tmp_path, [broken, backup])
    result = searcher.search_scene(scene(), needed=2, needed_images=0, used_keys=set())
    assert result.ranked and all(c.provider == "backup" for c in result.ranked)
    assert any(a.get("error") for a in result.attempts if a["provider"] == "broken")


def test_budget_limits_remote_calls_per_scene(tmp_path):
    useless = [FakeProvider(f"p{i}", results=[cand(f"p{i}", 1, "banana fruit")]) for i in range(3)]
    searcher, _ = make(tmp_path, useless, max_searches_per_scene=3)
    result = searcher.search_scene(scene(queries=("a b", "c d", "e f", "g h")), needed=3, needed_images=0, used_keys=set())
    total_calls = sum(len(p.calls) for p in useless)
    assert result.budget_used == 3
    assert total_calls == 3


def test_fallback_strategy_stops_at_first_bank_with_enough(tmp_path):
    first = FakeProvider("first", results=GOOD)
    second = FakeProvider("second", results=[cand("second", 9, "city night")])
    searcher, _ = make(tmp_path, [first, second], strategy="fallback")
    searcher.search_scene(scene(), needed=2, needed_images=0, used_keys=set())
    assert len(first.calls) == 1
    assert second.calls == []


def test_compare_strategy_asks_every_bank_and_best_wins(tmp_path):
    weak = FakeProvider("weak", results=[cand("weak", i, "city") for i in range(6)])
    strong = FakeProvider("strong", results=[cand("strong", 9, "city night traffic cars street")])
    searcher, _ = make(tmp_path, [weak, strong])  # padrão: compare
    result = searcher.search_scene(scene(), needed=2, needed_images=0, used_keys=set())
    assert len(weak.calls) == 1 and len(strong.calls) == 1  # mesma consulta nos dois bancos
    assert result.ranked[0].provider == "strong"  # o melhor resultado vence, venha de onde vier
    assert len({c.provider for c in result.ranked}) == 2


def test_cache_hit_does_not_call_provider_again(tmp_path):
    provider = FakeProvider("backup", results=GOOD)
    searcher, _ = make(tmp_path, [provider])
    searcher.search_scene(scene(), needed=2, needed_images=0, used_keys=set())
    calls = len(provider.calls)
    second = searcher.search_scene(scene(), needed=2, needed_images=0, used_keys=set())
    assert len(provider.calls) == calls
    assert second.budget_used == 0
    assert all(a.get("cached") for a in second.attempts if "results" in a)


def test_auth_error_opens_circuit_immediately(tmp_path):
    bad = FakeProvider("bad", error=AuthError("401"))
    backup = FakeProvider("backup", results=GOOD)
    searcher, _ = make(tmp_path, [bad, backup])
    searcher.search_scene(scene(), needed=1, needed_images=0, used_keys=set())
    assert searcher.breakers["bad"].is_open
    searcher.search_scene(scene(queries=("other query",)), needed=1, needed_images=0, used_keys=set())
    assert len(bad.calls) == 1  # não insiste numa fonte com chave inválida


def test_rate_limited_source_is_skipped(tmp_path):
    limited = FakeProvider("limited", error=RateLimitedError("429"))
    backup = FakeProvider("backup", results=GOOD)
    searcher, _ = make(tmp_path, [limited, backup])
    result = searcher.search_scene(scene(), needed=1, needed_images=0, used_keys=set())
    assert result.ranked
    assert searcher.breakers["limited"].is_open


def test_unexpected_exception_never_stops_pipeline(tmp_path):
    buggy = FakeProvider("buggy", error=KeyError("campo novo na API"))
    backup = FakeProvider("backup", results=GOOD)
    searcher, _ = make(tmp_path, [buggy, backup])
    result = searcher.search_scene(scene(), needed=1, needed_images=0, used_keys=set())
    assert result.ranked


def test_local_source_does_not_consume_budget(tmp_path):
    local = FakeProvider("local", results=[cand("local", 1, "banana")], remote=False)
    remote = FakeProvider("remote", results=GOOD)
    searcher, _ = make(tmp_path, [local, remote], max_searches_per_scene=1)
    result = searcher.search_scene(scene(), needed=2, needed_images=0, used_keys=set())
    assert result.budget_used == 1
    assert len(remote.calls) == 1


def test_used_candidates_are_not_counted_as_available(tmp_path):
    provider = FakeProvider("backup", results=GOOD[:2])
    other = FakeProvider("other", results=[cand("other", 7, "city night traffic street")])
    searcher, _ = make(tmp_path, [provider, other])
    used = {c.key for c in GOOD[:2]}
    searcher.search_scene(scene(), needed=1, needed_images=0, used_keys=used)
    assert other.calls  # precisou buscar em outra fonte


def test_ranking_prefers_relevant_metadata(tmp_path):
    provider = FakeProvider("p", results=[cand("p", 1, "banana fruit kitchen"), cand("p", 2, "city night traffic")])
    searcher, _ = make(tmp_path, [provider])
    result = searcher.search_scene(scene(), needed=1, needed_images=0, used_keys=set())
    assert result.ranked[0].media_id == "2"


def test_unreachable_source_is_disabled_after_first_failure(tmp_path):
    from broll_bot.errors import UnreachableError

    offline = FakeProvider("offline", error=UnreachableError("proxy"))
    backup = FakeProvider("backup", results=GOOD)
    searcher, _ = make(tmp_path, [offline, backup])
    for q in ("q1", "q2", "q3"):
        searcher.search_scene(scene(queries=(q,)), needed=1, needed_images=0, used_keys=set())
    assert len(offline.calls) == 1


# ------------------------------------------------------------ seleção/planos B
def _selector(tmp_path, library_files):
    from broll_bot.models import Shot, Transition
    from broll_bot.providers.local_library import LocalLibraryProvider
    from broll_bot.selection import Selector

    cfg = AppConfig()
    lib = LocalLibraryProvider(tmp_path / "lib", tmp_path / "index.json")
    lib._entries = [
        Candidate("mixkit_local", name, MediaType.VIDEO, 1920, 1080, 10.0, None, None,
                  tags=name.split("-"), local_path=str(tmp_path / name))
        for name in library_files
    ]
    sel = Selector(cfg, {"mixkit_local": lib}, Ranker(cfg, None, None), tmp_path / "media", seed=1)
    sel.fetch = lambda c: Path(c.local_path)  # sem ffprobe no teste
    shots = [Shot(i, 0, i * 4.0, i * 4.0 + 4, 4.123, Transition("cut", 0), i * 120, i * 120 + 120) for i in range(6)]
    return sel, shots


def test_unmatched_scene_uses_unused_library_clips_before_repeating(tmp_path):
    sel, shots = _selector(tmp_path, ["office-laptop", "city-night", "coffee-cup"])
    sel.assign(shots[:3], {})
    assert {s.candidate.media_id for s in shots[:3]} == {"office-laptop", "city-night", "coffee-cup"}
    assert all(s.fallback_reason == "library_last_resort" for s in shots[:3])


def test_reuse_rotates_instead_of_ping_pong(tmp_path):
    sel, shots = _selector(tmp_path, ["a-clip", "b-clip", "c-clip"])
    sel.assign(shots, {})
    ids = [s.candidate.media_id for s in shots]
    assert all(x != y for x, y in zip(ids, ids[1:]))  # nunca repete em sequência
    assert sorted(ids[3:]) == ["a-clip", "b-clip", "c-clip"]  # repete todos antes de voltar a um
    assert all(s.fallback_reason == "reused_with_new_framing" for s in shots[3:])


def test_generated_background_only_when_nothing_exists(tmp_path):
    sel, shots = _selector(tmp_path, [])
    sel.assign(shots[:1], {})
    assert shots[0].fallback_reason == "generated_background"


def test_fallbacks_never_take_clips_another_scene_needs(tmp_path):
    from broll_bot.search import SceneSearchResult

    sel, shots = _selector(tmp_path, ["coffee-cup", "office-laptop", "city-night"])
    lib = sel.providers["mixkit_local"].entries
    by_id = {c.media_id: c for c in lib}
    for c in lib:
        c.score = 0.9
    for s in shots[:3]:
        s.scene_index = 0
    for s in shots[3:]:
        s.scene_index = 1
    # cena 0 só tem 1 clipe bom; cena 1 precisa de office e city
    results = {
        0: SceneSearchResult(0, [by_id["coffee-cup"]], needed=3),
        1: SceneSearchResult(1, [by_id["office-laptop"], by_id["city-night"]], needed=3),
    }
    sel.assign(shots[:3], results)
    sel.assign(shots[3:], results)
    assert {s.candidate.media_id for s in shots[:3]} == {"coffee-cup"}  # repete o seu, não pega o dos outros
    assert {s.candidate.media_id for s in shots[3:5]} == {"office-laptop", "city-night"}


def test_403_after_working_requests_is_a_pause_not_a_bad_key(monkeypatch):
    """O Pexels bloqueou com 403 depois de 8 buscas boas: é excesso de pedidos, não chave errada.
    Espera e tenta de novo; só a primeira resposta 403 (chave nunca funcionou) desliga a fonte."""
    import broll_bot.http as http_mod
    from broll_bot.http import HttpClient
    from broll_bot.ratelimit import RetryPolicy, TokenBucket

    class Resp:
        def __init__(self, code):
            self.status_code, self.headers, self.text = code, {}, ""

        def json(self):
            return {"videos": []}

    codes = iter([200, 403, 403, 200])

    class Session:
        headers: dict = {}

        def get(self, *a, **k):
            return Resp(next(codes))

    monkeypatch.setattr(http_mod.time, "sleep", lambda s: None)
    client = HttpClient("pexels", TokenBucket(100, 100), RetryPolicy(max_retries=3), session=Session())
    assert client.get_json("u") == {"videos": []}
    assert client.get_json("u") == {"videos": []}  # 403, 403, 200: esperou e voltou
    fresh = HttpClient("pexels", TokenBucket(100, 100), RetryPolicy(max_retries=3), session=Session())
    codes = iter([403])
    try:
        fresh.get_json("u")
        raise AssertionError("chave que nunca funcionou devia parar")
    except AuthError:
        pass


def test_rate_limit_pauses_the_source_for_a_minute_only():
    from broll_bot.ratelimit import CircuitBreaker

    now = [0.0]
    cb = CircuitBreaker(3, 300.0, clock=lambda: now[0])
    cb.trip(60.0)
    assert cb.is_open
    now[0] = 61.0
    assert not cb.is_open


def test_pexels_is_a_reserve_bank(tmp_path):
    """Pexels (200 buscas/hora) é reserva: só busca quando os outros bancos não trouxeram mídia boa que baste.
    Busca que já está no cache vale sempre (é de graça)."""
    pixabay = FakeProvider("pixabay", results=[cand("pixabay", i, "city night traffic cars street") for i in range(8)])
    pexels = FakeProvider("pexels", results=[cand("pexels", i, "city night traffic cars street") for i in range(8)])
    searcher, _ = make(tmp_path, [pexels, pixabay])  # a ordem do dicionário não importa: a reserva vai por último
    result = searcher.search_scene(scene(), needed=2, needed_images=0, used_keys=set())
    assert not pexels.calls and pixabay.calls
    assert any(a.get("skipped") == "reserva" for a in result.attempts if a["provider"] == "pexels")
    # o Pixabay não tem nada bom: aí o Pexels entra
    weak = FakeProvider("pixabay", results=[cand("pixabay", 1, "banana fruit")])
    pexels2 = FakeProvider("pexels", results=[cand("pexels", i, "city night traffic cars street") for i in range(8)])
    (tmp_path / "b").mkdir()
    searcher, _ = make(tmp_path / "b", [weak, pexels2])
    result = searcher.search_scene(scene(), needed=2, needed_images=0, used_keys=set())
    assert pexels2.calls and any(c.provider == "pexels" for c in result.ranked)
    # sem reserva configurada: todos os bancos em toda busca (como antes)
    pexels3 = FakeProvider("pexels", results=[cand("pexels", i, "city night traffic cars street") for i in range(8)])
    good = FakeProvider("pixabay", results=[cand("pixabay", i, "city night traffic cars street") for i in range(8)])
    (tmp_path / "c").mkdir()
    searcher, _ = make(tmp_path / "c", [good, pexels3], reserve_providers=[])
    searcher.search_scene(scene(), needed=2, needed_images=0, used_keys=set())
    assert pexels3.calls


def test_second_scene_on_the_same_topic_reaches_the_reserve_banks(tmp_path):
    """Pixabay devolve os mesmos poucos vídeos de foguete para toda cena de foguete. A 1ª cena reserva os dela;
    a 2ª não conta com eles e vai ao banco de reserva (antes ficava com sobra da cena vizinha: escritório)."""
    from dataclasses import replace

    pixabay = FakeProvider("pixabay", results=[cand("pixabay", i, "city night traffic cars street") for i in range(3)])
    pexels = FakeProvider("pexels", results=[cand("pexels", i, "city night traffic cars street") for i in range(8)])
    searcher, _ = make(tmp_path, [pixabay, pexels], reserve_per_scene=0)
    first = scene()
    searcher.search_scene(first, needed=3, needed_images=0, used_keys=set())
    assert not pexels.calls
    second = replace(first, index=1)
    result = searcher.search_scene(second, needed=3, needed_images=0, used_keys=set())
    assert pexels.calls and any(c.provider == "pexels" for c in result.ranked)
    # a própria cena continua contando com o que ela reservou (2ª passada depois da revisão)
    pexels.calls.clear()
    searcher.search_scene(first, needed=3, needed_images=0, used_keys=set())
    assert not pexels.calls


def test_profile_photo_must_name_the_person(tmp_path):
    """Perfil do "Gerald Bull": foto de stock de um boi não entra; sem foto com o nome, o perfil usa as iniciais."""
    from broll_bot.motion.planner import TemplatePlanner

    class Searcher:
        def __init__(self, ranked):
            self.ranked = ranked

        def search_scene(self, *a, **k):
            from broll_bot.search import SceneSearchResult
            res = SceneSearchResult(-1, needed=1)
            res.ranked = self.ranked
            return res

    class Selector:
        used_keys: set = set()

        def claim(self, c):
            return tmp_path / f"{c.media_id}.jpg"

    cow = cand("pexels", 1, "a cow with horns standing in the dark", MediaType.IMAGE)
    bull = cand("wikimedia", 2, "Gerald Bull ballistics engineer 1964", MediaType.IMAGE)
    planner = TemplatePlanner.__new__(TemplatePlanner)
    planner.selector, planner.tcfg = Selector(), AppConfig().templates
    item = {"label": "Gerald Bull", "query": "Gerald Bull portrait"}
    planner.searcher = Searcher([cow])
    assert planner._item_media(scene(), item, 0, "profile") is None
    planner.searcher = Searcher([cow, bull])
    got = planner._item_media(scene(), item, 0, "profile")
    assert got is not None and got[0].media_id == "2"


def test_short_clip_loses_to_one_that_covers_the_shot(tmp_path):
    sel, shots = _selector(tmp_path, ["short-clip", "long-clip"])
    lib = sel.providers["mixkit_local"]
    lib._entries[0].duration = 1.0  # 4 s de shot: nem a 1,6x cobre
    sel.assign(shots[:1], {})
    assert shots[0].candidate.media_id == "long-clip"


def test_short_clip_never_loops_it_holds_with_slow_push(tmp_path):
    from broll_bot.framing import build_filter

    sel, shots = _selector(tmp_path, ["short-clip"])
    sel.providers["mixkit_local"]._entries[0].duration = 1.0
    sel.assign(shots[:1], {})
    shot = shots[0]
    assert shot.speed == 1.6 and shot.source_offset == 0.0
    assert shot.layout["name"] not in ("full", "zoom_crop")  # parado ganharia só um quadro congelado
    filt = build_filter({"name": "push", "z0": 1.0, "z1": 1.1, "fx": 0.5, "fy": 0.5}, 1920, 1080, 30, 120,
                        speed=1.6, hold=2.4)
    assert "tpad=stop_mode=clone:stop_duration=2.900" in filt
    assert filt.index("tpad") < filt.index("crop")  # congela antes do enquadramento: o push continua


def test_cached_search_comes_back_even_with_the_source_switched_off(tmp_path):
    """Versão 3 das baterias: o YouTube deu 429, o disjuntor desligou a fonte e ~2.900 buscas que já tinham o
    resultado guardado (da versão 1) foram puladas; os vídeos do YouTube da v1 não voltaram. Busca em cache não fala
    com a fonte: vem sempre."""
    first = FakeProvider("tube", results=GOOD)
    searcher, cache = make(tmp_path, [first])
    searcher.search_scene(scene(), needed=2, needed_images=0, used_keys=set())  # versão 1: busca e guarda
    assert first.calls
    blocked = FakeProvider("tube", error=RateLimitedError("HTTP 429"))
    again, _ = make(tmp_path, [blocked])
    again.breakers["tube"].trip()  # versão 3: a fonte está barrando
    result = again.search_scene(scene(), needed=2, needed_images=0, used_keys=set())
    assert result.ranked and all(c.provider == "backup" for c in result.ranked)  # os da versão 1
    assert not blocked.calls  # e sem pedir nada à fonte
    assert any(a.get("cached") for a in result.attempts)


def test_prefetch_runs_first_queries_in_parallel_and_skips_reserve_banks(tmp_path):
    # 1ª passada das 9 baterias: 18 min, uma busca de rede por vez
    import threading
    import time

    active, peak = [0], [0]
    lock = threading.Lock()

    class Slow(FakeProvider):
        def search(self, query, media_type, limit, language="en"):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            with lock:
                active[0] -= 1
            return super().search(query, media_type, limit, language)

    main = Slow("main", results=GOOD)
    reserve = FakeProvider("pexels", results=GOOD)
    local = FakeProvider("local", results=GOOD, remote=False)
    searcher, _ = make(tmp_path, [main, reserve, local], reserve_providers=["pexels"])
    scenes = [Scene(i, f"cena {i}", i, i + 1, SceneAnalysis("x", [f"query {i}", "other"], "city")) for i in range(8)]
    assert searcher.prefetch(scenes, image_scenes={0}) == 9  # 8 de vídeo + 1 de foto
    assert peak[0] > 1 and not reserve.calls and not local.calls
    assert ("other", MediaType.VIDEO) not in main.calls  # só a 1ª consulta de cada cena
    main.calls.clear()
    result = searcher.search_scene(scenes[3], needed=1, needed_images=0, used_keys=set())
    assert ("query 3", MediaType.VIDEO) not in main.calls and result.ranked  # veio do cache
    assert searcher.prefetch(scenes, image_scenes={0}) == 0  # tudo no cache: nada de novo


def test_selection_prefetch_downloads_likely_picks_in_parallel(tmp_path):
    # v1 das 9 baterias: 23 min baixando ~470 arquivos um por vez antes dos templates
    import threading
    import time

    from broll_bot.models import Shot, Transition
    from broll_bot.search import SceneSearchResult
    from broll_bot.selection import Selector

    active, peak, got = [0], [0], []
    lock = threading.Lock()

    class Bank(FakeProvider):
        def download(self, candidate, dest_dir):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            with lock:
                active[0] -= 1
                got.append(candidate.key)
            return dest_dir / candidate.media_id

    cfg = AppConfig()
    cfg.search.reserve_per_scene = 1
    bank = Bank("pixabay")
    ranker = Ranker(cfg, None, None)
    sel = Selector(cfg, {"pixabay": bank}, ranker, tmp_path, seed=1)
    results = {}
    for idx in range(4):
        ranked = [cand("pixabay", idx * 10 + i, "city night") for i in range(5)]
        for i, c in enumerate(ranked):
            c.score, c.semantic = 0.9 - i * 0.01, 0.9
        ranked[0].review = "rejected"  # reprovado não é baixado
        results[idx] = SceneSearchResult(idx, ranked=ranked, needed=1)
    shots = [Shot(i, i // 2, i * 4.0, i * 4.0 + 4, 4.0, Transition("cut", 0)) for i in range(8)]  # 2 por cena
    ranker.is_good = lambda c: True
    assert sel.prefetch(shots, results) == 12  # 4 cenas x (2 shots + 1 reserva)
    assert peak[0] > 1
    assert "pixabay:video:0" not in got and "pixabay:video:1" in got


def test_prefetch_keeps_wikimedia_serial_and_never_trips_the_breaker(tmp_path):
    import threading
    import time

    active, peak = [0], [0]
    lock = threading.Lock()

    class Wiki(FakeProvider):
        def search(self, query, media_type, limit, language="en"):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.02)
            with lock:
                active[0] -= 1
            return super().search(query, media_type, limit, language)

    wiki = Wiki("wikimedia", results=GOOD)
    busy = FakeProvider("openverse", error=RateLimitedError("limite local"))
    searcher, _ = make(tmp_path, [wiki, busy], reserve_providers=[])
    scenes = [Scene(i, f"cena {i}", i, i + 1, SceneAnalysis("x", [f"query {i}"], "city")) for i in range(6)]
    searcher.prefetch(scenes, image_scenes=set())
    assert peak[0] == 1 and len(wiki.calls) == 6  # etiqueta da Wikimedia: uma chamada por vez
    assert not searcher.breakers["openverse"].is_open  # o adiantamento nunca desliga um banco


def test_prefetch_stops_at_the_time_limit(tmp_path):
    # Openverse sem chave (~20 por minuto) segurava o adiantamento por 8 min: o resto fica para a passada
    import time

    class Slow(FakeProvider):
        def search(self, query, media_type, limit, language="en"):
            time.sleep(0.1)
            return super().search(query, media_type, limit, language)

    slow = Slow("openverse", results=GOOD)
    searcher, _ = make(tmp_path, [slow], reserve_providers=[])
    scenes = [Scene(i, f"cena {i}", i, i + 1, SceneAnalysis("x", [f"query {i}"], "city")) for i in range(20)]
    t = time.monotonic()
    searcher.prefetch(scenes, image_scenes=set(), max_seconds=0.25)
    assert time.monotonic() - t < 1.0 and len(slow.calls) < 20


def test_last_resort_skips_subject_files_the_scene_does_not_talk_about(tmp_path):
    """Vídeo dos disjuntores: o roteiro diz "Brasil" uma vez, e a foto do vendedor de carvão (requires: brasil,
    carvão) entrou na cena da mola do disjuntor como último recurso."""
    import json as _json

    sel, shots = _selector(tmp_path, ["charcoal-vendor-brazil", "office-laptop"])
    (tmp_path / "charcoal-vendor-brazil.json").write_text(_json.dumps({"requires": ["brasil", "carvão"]}))
    sel.scene_text = {0: "E aí uma mola separa os dois contatos de metal. Clac.",
                      1: "No Brasil, o carvão vem do eucalipto."}
    sel.assign(shots[:1], {})
    assert shots[0].candidate.media_id == "office-laptop"  # genérico, sem requires: pode
    shots[1].scene_index = 1
    sel.assign(shots[1:2], {})
    assert shots[1].candidate.media_id == "charcoal-vendor-brazil"  # a cena fala do assunto: pode


def test_scene_targeted_library_photo_beats_any_video(tmp_path):
    """Vídeo dos disjuntores: a foto do fusível queimado (say: fusível) foi aprovada para a cena do fusível, mas
    perdeu para um vídeo de solda só porque o shot preferia vídeo."""
    import json as _json

    from broll_bot.search import SceneSearchResult

    sel, shots = _selector(tmp_path, [])
    lib = sel.providers["mixkit_local"]
    photo = Candidate("mixkit_local", "blown-glass-fuse", MediaType.IMAGE, 1920, 1080, None, None, None,
                      tags=["fuse"], local_path=str(tmp_path / "blown-glass-fuse.jpg"))
    (tmp_path / "blown-glass-fuse.json").write_text(_json.dumps({"say": ["fusível"]}))
    video = Candidate("pexels", "soldering", MediaType.VIDEO, 1920, 1080, 10.0, None, None, tags=["solder"],
                      local_path=str(tmp_path / "soldering.mp4"))  # caminho só para o fetch do teste
    photo.score, photo.review, video.score, video.review = 0.5, "approved", 0.9, "approved"
    lib._entries = [photo]
    sel.providers["pexels"] = lib  # o fetch do teste não baixa nada
    sel.scene_text = {0: "Toda vez que o fusível queimava, precisava trocar."}
    shots[0].prefer_media = MediaType.VIDEO
    sel.assign(shots[:1], {0: SceneSearchResult(0, [video, photo], needed=1)})
    assert shots[0].candidate.media_id == "blown-glass-fuse"


def test_console_without_check_mark_does_not_crash():
    import io

    from broll_bot.cli import safe_console

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")
    safe_console(stream)
    print("✔ pronto", file=stream)
    stream.flush()
    assert raw.getvalue().startswith(b"? pronto")


def test_scene_marked_library_photo_is_a_candidate_even_when_videos_suffice(tmp_path):
    """Vídeo dos disjuntores: a cena da fábrica da ABB já tinha vídeos (de moto) que bastavam; a biblioteca nunca
    era procurada por foto e a foto dos disjuntores ABB (say: abb) nem virava candidata."""
    import json as _json

    from broll_bot.providers.local_library import LocalLibraryProvider

    lib = LocalLibraryProvider(tmp_path / "lib", tmp_path / "index.json")
    photo = Candidate("mixkit_local", "abb-breakers.jpg", MediaType.IMAGE, 1920, 1080, None, None, None,
                      tags=["abb", "circuit", "breakers"], local_path=str(tmp_path / "abb-breakers.jpg"))
    other = Candidate("mixkit_local", "fuse.jpg", MediaType.IMAGE, 1920, 1080, None, None, None,
                      tags=["fuse"], local_path=str(tmp_path / "fuse.jpg"))
    (tmp_path / "abb-breakers.json").write_text(_json.dumps({"say": ["abb"]}))
    (tmp_path / "fuse.json").write_text(_json.dumps({"say": ["fusível"]}))
    lib._entries = [photo, other]
    lib.root.mkdir()
    remote = FakeProvider("backup", results=GOOD)
    searcher, _ = make(tmp_path, [lib, remote])
    sc = scene()
    sc.text = "Segundo a própria ABB, essa fábrica já produziu quase um bilhão."
    result = searcher.search_scene(sc, needed=2, needed_images=0, used_keys=set())
    keys = {c.media_id for c in result.ranked}
    assert "abb-breakers.jpg" in keys and "fuse.jpg" not in keys


def test_scene_marked_library_file_counts_as_good_unless_the_reviewer_rejects(tmp_path):
    import json as _json

    ranker = Ranker(AppConfig(), embedder=None, thumbs=None)
    photo = Candidate("mixkit_local", "abb.jpg", MediaType.IMAGE, 1920, 1080, None, None, None,
                      local_path=str(tmp_path / "abb.jpg"))
    (tmp_path / "abb.json").write_text(_json.dumps({"say": ["abb"]}))
    photo.score = 0.0
    assert ranker.is_good(photo)
    photo.review = "rejected"
    assert not ranker.is_good(photo)


def test_scene_marked_files_do_not_stop_the_search(tmp_path):
    """v4 dos disjuntores: as fotos marcadas contavam como "já basta", a busca parava e, quando uma cena anterior
    usava a foto, sobrava plano B (sobra da cena vizinha) de 19 para 31 shots."""
    import json as _json

    from broll_bot.providers.local_library import LocalLibraryProvider

    lib = LocalLibraryProvider(tmp_path / "lib", tmp_path / "index.json")
    lib._entries = []
    for i in range(4):
        lib._entries.append(Candidate("mixkit_local", f"abb-{i}.jpg", MediaType.IMAGE, 1920, 1080, None, None, None,
                                      tags=["abb"], local_path=str(tmp_path / f"abb-{i}.jpg")))
        (tmp_path / f"abb-{i}.json").write_text(_json.dumps({"say": ["abb"]}))
    lib.root.mkdir()
    remote = FakeProvider("backup", results=GOOD)
    searcher, _ = make(tmp_path, [lib, remote])
    sc = scene()
    sc.text = "Segundo a própria ABB, essa fábrica já produziu quase um bilhão."
    searcher.search_scene(sc, needed=2, needed_images=1, used_keys=set())
    assert any(m is MediaType.IMAGE for _, m in remote.calls)  # buscou foto nos bancos mesmo com 4 marcadas


def test_small_scene_marked_portrait_is_not_dropped(tmp_path):
    """Vídeo do relógio de luz: o retrato de 303 px do Oliver Shallenberger (say: shallenberger) era cortado pela
    largura mínima e o cartão de perfil saiu só com as iniciais."""
    import json as _json

    cfg = AppConfig()
    ranker = Ranker(cfg, embedder=None, thumbs=None)
    small = Candidate("mixkit_local", "oliver-shallenberger.jpg", MediaType.IMAGE, 303, 373, None, None, None,
                      tags=["oliver", "shallenberger"], local_path=str(tmp_path / "oliver-shallenberger.jpg"))
    other = Candidate("mixkit_local", "tiny.jpg", MediaType.IMAGE, 303, 373, None, None, None,
                      tags=["shallenberger"], local_path=str(tmp_path / "tiny.jpg"))
    (tmp_path / "oliver-shallenberger.json").write_text(_json.dumps({"say": ["shallenberger"]}))
    sc = scene()
    sc.text = "Um engenheiro chamado Oliver Shallenberger estava mexendo numa lâmpada."
    ids = {c.media_id for c in ranker.rank(sc, [small, other])}
    assert "oliver-shallenberger.jpg" in ids and "tiny.jpg" not in ids


def test_scene_marked_clip_repeats_far_away_before_neighbor_leftovers(tmp_path):
    """Relógio de luz: no fim ("Virou um disco de alumínio...") entraram vinil e laminação de metal porque os vídeos do
    disco girando já tinham sido usados no começo. Repetir o clipe marcado, bem longe, vem antes da sobra vizinha."""
    import json as _json

    from broll_bot.models import Shot, Transition
    from broll_bot.search import SceneSearchResult

    sel, _ = _selector(tmp_path, [])
    disc = Candidate("mixkit_local", "meter-disc.mp4", MediaType.VIDEO, 1920, 1080, 10.0, None, None,
                     tags=["disc"], local_path=str(tmp_path / "meter-disc.mp4"))
    (tmp_path / "meter-disc.json").write_text(_json.dumps({"say": ["disco"]}))
    vinyl = Candidate("mixkit_local", "vinyl.mp4", MediaType.VIDEO, 1920, 1080, 10.0, None, None,
                      tags=["vinyl"], local_path=str(tmp_path / "vinyl.mp4"))
    extra = [Candidate("mixkit_local", f"vinyl-{i}.mp4", MediaType.VIDEO, 1920, 1080, 10.0, None, None,
                       tags=[f"thing{i}"], local_path=str(tmp_path / f"vinyl-{i}.mp4")) for i in range(2, 6)]
    for c in (disc, vinyl, *extra):
        c.score, c.review = 0.8, "approved"
    sel.scene_text = {0: "Lá dentro tem um disco girando.", 1: "Um toca-fitas antigo.",
                      2: "Virou um disco de alumínio que ficou cem anos no muro."}
    shots = [Shot(0, 0, 0.0, 4.0, 4.0, Transition("cut", 0), 0, 120),
             Shot(1, 1, 100.0, 104.0, 4.0, Transition("cut", 0), 3000, 3120),
             Shot(2, 2, 200.0, 204.0, 4.0, Transition("cut", 0), 6000, 6120)]
    results = {0: SceneSearchResult(0, [disc], needed=1), 1: SceneSearchResult(1, [vinyl, *extra], needed=1),
               2: SceneSearchResult(2, [disc], needed=1)}
    sel.assign(shots, results)
    assert shots[0].candidate.media_id == "meter-disc.mp4"
    assert shots[2].candidate.media_id == "meter-disc.mp4" and shots[2].fallback_reason == "reused_with_new_framing"


def test_requires_min_ignores_a_passing_mention(tmp_path):
    """O vídeo do hidrômetro diz "relógio de luz" duas vezes de passagem: as fotos do relógio de luz (requires_min 3)
    não entram; no vídeo do relógio de luz, que diz isso o tempo todo, entram. Sem requires_min, 1 menção basta."""
    import json as _json

    from broll_bot.providers.local_library import LocalLibraryProvider

    lib = LocalLibraryProvider(tmp_path / "lib", tmp_path / "index.json")
    meter = Candidate("mixkit_local", "meter.jpg", MediaType.IMAGE, 1920, 1080, None, None, None,
                      local_path=str(tmp_path / "meter.jpg"))
    old = Candidate("mixkit_local", "old.jpg", MediaType.IMAGE, 1920, 1080, None, None, None,
                    local_path=str(tmp_path / "old.jpg"))
    (tmp_path / "meter.json").write_text(_json.dumps({"requires": ["relógio de luz"], "requires_min": 3}))
    (tmp_path / "old.json").write_text(_json.dumps({"requires": ["relógio de luz"]}))
    lib.context = "O hidrômetro. Assim como no relógio de luz... o vídeo sobre o relógio de luz."
    assert not lib.allowed(meter) and lib.allowed(old)
    lib.context = "O relógio de luz. " * 5
    assert lib.allowed(meter)


def test_scene_marked_clip_repeats_at_most_max_marked_uses(tmp_path):
    """Hidrômetro: 12 fotos marcadas apareceram 4 ou 5 vezes cada. Com max_marked_uses 3, a 4ª vez não acontece."""
    import json as _json

    from broll_bot.models import Shot, Transition
    from broll_bot.search import SceneSearchResult

    sel, _ = _selector(tmp_path, [])
    sel.cfg.search.max_marked_uses = 3
    meter = Candidate("mixkit_local", "meter.jpg", MediaType.IMAGE, 1920, 1080, None, None, None,
                      tags=["meter"], local_path=str(tmp_path / "meter.jpg"))
    (tmp_path / "meter.json").write_text(_json.dumps({"say": ["hidrômetro"]}))
    meter.score, meter.review = 0.8, "approved"
    n = 10
    shots = [Shot(i, i, i * 100.0, i * 100.0 + 4, 4.0, Transition("cut", 0), i * 3000, i * 3000 + 120) for i in range(n)]
    sel.scene_text = {i: "O hidrômetro gira." if i % 2 == 0 else "Outra coisa." for i in range(n)}
    results = {}
    for i in range(n):
        if i % 2 == 0:
            results[i] = SceneSearchResult(i, [meter], needed=1)
        else:
            other = Candidate("mixkit_local", f"other-{i}.jpg", MediaType.IMAGE, 1920, 1080, None, None, None,
                              tags=[f"thing{i}"], local_path=str(tmp_path / f"other-{i}.jpg"))
            other.score, other.review = 0.8, "approved"
            results[i] = SceneSearchResult(i, [other], needed=1)
    sel.assign(shots, results)
    uses = [bool(s.candidate) and s.candidate.media_id == "meter.jpg" for s in shots]
    assert sum(uses) == 3


def test_profile_prefers_the_marked_library_file_over_a_namesake(tmp_path):
    """Hidrômetro: a Wikimedia devolvia o rebocador "Reinhard Woltman" e ele virava o retrato do engenheiro, à frente
    do molinete original do Woltman marcado na biblioteca. Foto sem o nome continua fora."""
    import json as _json

    from broll_bot.motion.planner import profile_photos

    ship = Candidate("wikimedia", "ship", MediaType.IMAGE, 1920, 1080, None, None, None,
                     description="Reinhard Woltman, Oktober 2020, Foto 2")
    ship.score = 0.9
    meter = Candidate("mixkit_local", "woltman-meter.jpg", MediaType.IMAGE, 1920, 1080, None, None, None,
                      tags=["reinhard woltman", "current meter"], local_path=str(tmp_path / "woltman-meter.jpg"))
    (tmp_path / "woltman-meter.json").write_text(_json.dumps({"say": ["woltman"]}))
    cow = Candidate("pexels", "cow", MediaType.IMAGE, 1920, 1080, None, None, None, description="a cow")
    assert [c.media_id for c in profile_photos([ship, meter, cow], "Reinhard Woltman")] == ["woltman-meter.jpg", "ship"]
