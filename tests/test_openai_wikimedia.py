"""OpenAI (plano de cenas e revisão visual) e Wikimedia Commons, com respostas simuladas."""

import json
from types import SimpleNamespace

import pytest
from PIL import Image

from broll_bot.cache import SQLiteCache
from broll_bot.config import AppConfig, _validate, review_provider
from broll_bot.models import Candidate, MediaType, Scene, SceneAnalysis, Sentence


# ------------------------------------------------------------------ OpenAI
class FakeOpenAI:
    """Imita client.chat.completions.create e guarda os argumentos recebidos."""

    def __init__(self, payload, finish="stop"):
        self.payload, self.finish, self.calls = payload, finish, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        msg = SimpleNamespace(content=json.dumps(self.payload), refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=self.finish)])


def _sentences():
    return [Sentence(0, "O Corolla XEi tem 177 cavalos.", 0, 0.0, 4.0),
            Sentence(1, "E faz 12 km/l na estrada.", 0, 4.0, 8.0)]


PLAN = {"scenes": [{"first_sentence": 0, "last_sentence": 1, "visual_summary": "white corolla driving",
                    "queries": ["toyota corolla driving"], "fallback_query": "sedan", "negative_terms": ["bmw"],
                    "tone": "confident", "style": "headline", "template_seconds": 2, "caption": "Corolla XEi",
                    "overlay": {"kind": "badge", "title": "12", "detail": "km/l"}}]}


def test_openai_scene_plan_uses_json_mode_and_reasoning_effort(tmp_path):
    from broll_bot.scenes import OpenAISceneAnalyzer

    cfg = AppConfig().analysis
    fake = FakeOpenAI(PLAN)
    scenes = OpenAISceneAnalyzer(cfg, SQLiteCache(tmp_path / "c.sqlite"), client=fake).analyze(_sentences(), 8.0)
    assert len(scenes) == 1 and scenes[0].analysis.style == "headline"
    assert scenes[0].analysis.overlay["kind"] == "badge"
    call = fake.calls[0]
    assert call["model"] == cfg.openai_model and call["response_format"] == {"type": "json_object"}
    assert call["reasoning_effort"] == cfg.openai_effort and "max_completion_tokens" in call
    # segunda vez vem do cache (não chama a API)
    OpenAISceneAnalyzer(cfg, SQLiteCache(tmp_path / "c.sqlite"), client=fake).analyze(_sentences(), 8.0)
    assert len(fake.calls) == 1


def test_openai_non_reasoning_model_gets_no_effort():
    from broll_bot.llm_openai import OpenAIJSON

    fake = FakeOpenAI({"ok": True})
    assert OpenAIJSON("gpt-4o", "low", client=fake).complete("sys", "diga JSON") == {"ok": True}
    assert "reasoning_effort" not in fake.calls[0]


def test_openai_truncated_answer_is_an_error():
    from broll_bot.llm_openai import OpenAIError, OpenAIJSON

    with pytest.raises(OpenAIError):
        OpenAIJSON("gpt-5", client=FakeOpenAI({}, finish="length")).complete("sys", "x")


def test_openai_visual_review_sends_images_and_applies_verdicts(tmp_path):
    from broll_bot.review import VisualReviewer
    from broll_bot.search import SceneSearchResult

    cfg = AppConfig()
    cfg.analysis.provider = "openai"
    thumbs = []
    for i in range(2):
        p = tmp_path / f"t{i}.jpg"
        Image.new("RGB", (320, 180), (40 * i, 90, 120)).save(p)
        c = Candidate("pexels", str(i), MediaType.VIDEO, 1920, 1080, 8.0, None, None, thumbnail_path=str(p))
        c.score = c.semantic = 0.8
        thumbs.append(c)
    scene = Scene(0, "O Corolla XEi", 0, 5, SceneAnalysis("white corolla", ["corolla"], "sedan"))
    rv = VisualReviewer(cfg, SQLiteCache(tmp_path / "c.sqlite"), thumbs=None)
    items = rv.pending([scene], {0: SceneSearchResult(0, thumbs, needed=1)}, {})
    fake = FakeOpenAI({"scenes": [{"scene": 0, "approved": [1], "rejected": [2], "notes": "2 é um BMW"}]})
    reviews: dict = {}
    assert rv.review_openai(items, reviews, client=fake) == 2
    assert reviews[0] == {thumbs[0].key: "approved", thumbs[1].key: "rejected"}
    parts = fake.calls[0]["messages"][1]["content"]
    assert any(p["type"] == "image_url" and p["image_url"]["url"].startswith("data:image/jpeg;base64,") for p in parts)


def test_review_provider_follows_scene_plan_by_default():
    cfg = AppConfig()
    assert review_provider(cfg) == "anthropic"
    cfg.analysis.provider = "openai"
    assert review_provider(cfg) == "openai"
    cfg.review.provider = "anthropic"
    assert review_provider(cfg) == "anthropic"
    cfg.analysis.provider = "chatgpt"
    with pytest.raises(ValueError):
        _validate(cfg)


# --------------------------------------------------------------- Wikimedia
def _page(pageid, title, lic, width=4000, mime="image/jpeg", artist="<a href='x'>João</a>"):
    return {"pageid": pageid, "title": f"File:{title}", "index": pageid,
            "imageinfo": [{"mime": mime, "width": width, "height": width * 2 // 3,
                           "url": f"https://upload.wikimedia.org/a/{title}",
                           "thumburl": f"https://upload.wikimedia.org/thumb/a/{title}/1920px-{title}",
                           "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{title}",
                           "extmetadata": {"LicenseShortName": {"value": lic}, "Artist": {"value": artist},
                                           "ImageDescription": {"value": "Toyota <b>Corolla</b> E100 sedan"}}}]}


class FakeHttp:
    def __init__(self, data):
        self.data, self.params, self.requests_made = data, None, 0

    def get_json(self, url, params=None):
        self.params, self.requests_made = params, self.requests_made + 1
        return self.data


def test_wikimedia_keeps_only_safe_licenses_and_big_photos():
    from broll_bot.providers.wikimedia import WikimediaProvider

    pages = [_page(3, "Corolla_sa.jpg", "CC BY-SA 4.0"), _page(1, "Corolla_E100.jpg", "CC BY 2.0"),
             _page(2, "Corolla_pd.jpg", "Public domain"), _page(4, "Corolla_small.jpg", "CC0", width=800),
             _page(5, "Corolla_nc.jpg", "CC BY-NC 2.0"), _page(6, "Corolla.svg", "CC0", mime="image/svg+xml")]
    http = FakeHttp({"query": {"pages": pages}})
    wm = WikimediaProvider("eu@exemplo.com", http)  # type: ignore[arg-type]
    found = wm.search("toyota corolla e100", MediaType.IMAGE, 10)
    assert [c.media_id for c in found] == ["1", "2"]  # na ordem da busca, só licenças seguras e grandes
    c = found[0]
    assert c.author == "João (CC BY 2.0) via Wikimedia Commons"
    assert c.thumbnail_url.endswith("/500px-Corolla_E100.jpg") and c.download_url.endswith("/1920px-Corolla_E100.jpg")
    assert (c.width, c.height) == (1920, 1280) and "corolla" in c.tags
    assert "filetype:bitmap" in http.params["gsrsearch"]
    assert wm.search("x", MediaType.VIDEO, 5) == []  # só fotos


def test_reference_photos_ignore_license_and_size():
    """Referência só é comparada (nunca entra no vídeo): CC BY-SA e foto menor também valem."""
    from broll_bot.providers.wikimedia import WikimediaProvider

    pages = [_page(3, "Corolla_sa.jpg", "CC BY-SA 4.0"), _page(4, "Corolla_small.jpg", "CC0", width=800),
             _page(7, "Corolla_tiny.jpg", "CC BY-SA 3.0", width=200), _page(6, "Corolla.svg", "CC0", mime="image/svg+xml")]
    wm = WikimediaProvider("eu@exemplo.com", FakeHttp({"query": {"pages": pages}}))  # type: ignore[arg-type]
    assert [c.media_id for c in wm.search_reference("toyota corolla")] == ["3", "4"]
    assert wm.search("toyota corolla", MediaType.IMAGE, 10) == []  # no vídeo, a regra de licença continua


def test_wikimedia_share_alike_is_opt_in_and_needs_contact():
    from broll_bot.providers.wikimedia import WikimediaProvider, license_ok

    assert license_ok("CC BY-SA 4.0", True) and not license_ok("CC BY-SA 4.0", False)
    assert license_ok("CC0", False) and not license_ok("GFDL", False) and not license_ok("CC BY-ND 4.0", False)
    assert not WikimediaProvider(None, FakeHttp({})).available()  # type: ignore[arg-type]


def test_wikimedia_is_built_with_contact_user_agent(tmp_path, monkeypatch):
    from broll_bot.config import load_config
    from broll_bot.providers import build_providers

    monkeypatch.setenv("WIKIMEDIA_CONTACT", "eu@exemplo.com")
    cfg = load_config(None, None)
    wm = build_providers(cfg, tmp_path)["wikimedia"]
    assert wm.available() and "eu@exemplo.com" in wm.http.session.headers["User-Agent"]


def test_plan_tolerates_loose_formats_from_other_models():
    from broll_bot.scenes import _ScenePlan

    loose = [{"first_sentence": 0, "last_sentence": 1, "visual_summary": "x", "queries": "sedan road, car",
              "fallback_query": None, "negative_terms": ["bmw"], "tone": "calm", "style": "checklist",
              "caption": None, "points": ["Consumo baixo", "Manutenção cara"], "number": "110 mil",
              "template_seconds": "9", "rank": "1", "overlay": None, "items": ["Corolla"]}]
    sc = _ScenePlan.model_validate(loose).scenes[0]  # lista solta no lugar de {"scenes": [...]}
    assert [(p.text, p.positive) for p in sc.points] == [("Consumo baixo", True), ("Manutenção cara", False)]
    assert sc.number.value == 110 and sc.queries == ["sedan road", "car"] and sc.template_seconds == 9
    assert sc.items[0].query == "Corolla" and sc.overlay.kind == ""


def test_openai_plan_gets_one_repair_attempt(tmp_path):
    from broll_bot.scenes import OpenAISceneAnalyzer

    class TwoAnswers(FakeOpenAI):
        def _create(self, **kwargs):
            self.calls.append(kwargs)
            payload = {"scenes": [{"first_sentence": "zero"}]} if len(self.calls) == 1 else PLAN
            msg = SimpleNamespace(content=json.dumps(payload), refusal=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])

    fake = TwoAnswers(None)
    scenes = OpenAISceneAnalyzer(AppConfig().analysis, None, client=fake).analyze(_sentences(), 8.0)
    assert len(fake.calls) == 2 and scenes[0].analysis.style == "headline"
    assert "did not match the format" in fake.calls[1]["messages"][1]["content"]


def test_openai_review_sends_batches_in_parallel_and_one_failure_keeps_the_rest(tmp_path):
    # 1ª rodada da revisão das 9 baterias: 5 min com os lotes um de cada vez
    import threading
    import time

    from broll_bot.review import VisualReviewer
    from broll_bot.search import SceneSearchResult

    cfg = AppConfig()
    cfg.review.scenes_per_request = 1
    p = tmp_path / "t.jpg"
    Image.new("RGB", (320, 180), (40, 90, 120)).save(p)
    scenes, results = [], {}
    for i in range(4):
        c = Candidate("pexels", str(i), MediaType.VIDEO, 1920, 1080, 8.0, None, None, thumbnail_path=str(p))
        c.score = c.semantic = 0.8
        scenes.append(Scene(i, f"cena {i}", i, i + 1, SceneAnalysis("x", ["car"], "car")))
        results[i] = SceneSearchResult(i, [c], needed=1)
    active, peak = [0], [0]
    lock = threading.Lock()

    class Parallel:
        def __init__(self):
            self.chat = self
            self.completions = self

        def create(self, **kw):
            text = " ".join(part.get("text", "") for part in kw["messages"][1]["content"])
            n = int(text.split("Scene ")[1].split(" ")[0])
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            with lock:
                active[0] -= 1
            if n == 2:
                raise RuntimeError("timeout")
            return FakeOpenAI({"scenes": [{"scene": n, "approved": [1], "rejected": []}]})._create(**kw)

    rv = VisualReviewer(cfg, SQLiteCache(tmp_path / "c.sqlite"), thumbs=None)
    reviews: dict = {}
    assert rv.review_openai(rv.pending(scenes, results, {}), reviews, client=Parallel()) == 3
    assert peak[0] > 1 and sorted(reviews) == [0, 1, 3] and rv.errors
