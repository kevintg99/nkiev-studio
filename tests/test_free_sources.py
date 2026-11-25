"""Openverse (fotos CC) e YouTube Creative Commons, com respostas simuladas."""

from __future__ import annotations

from pathlib import Path

from broll_bot.models import MediaType


class FakeHttp:
    def __init__(self, routes):
        self.routes, self.calls, self.requests_made = routes, [], 0

    def get_json(self, url, params=None, headers=None):
        self.calls.append((url, dict(params or {})))
        self.headers = headers
        self.requests_made += 1
        for key, payload in self.routes.items():
            if url.endswith(key):
                return payload(params) if callable(payload) else payload
        raise AssertionError(url)


def _ov_item(**kw):
    item = {"id": "abc-1", "title": "Solar bottle bulb in a slum roof", "url": "https://live.staticflickr.com/x/1_o.jpg",
            "thumbnail": "https://api.openverse.org/v1/images/abc-1/thumb/", "width": 3000, "height": 2000,
            "creator": "Liter of Light", "license": "by", "license_version": "2.0", "source": "flickr",
            "foreign_landing_url": "https://www.flickr.com/photos/x/1", "tags": [{"name": "solar bottle"}, {"name": "roof"}]}
    item.update(kw)
    return item


def test_openverse_keeps_free_licenses_and_credits_the_author():
    from broll_bot.providers.openverse import OpenverseProvider

    results = [_ov_item(), _ov_item(id="sa", license="by-sa"), _ov_item(id="nc", license="by-nc"),
               _ov_item(id="small", width=640, height=480), _ov_item(id="pd", license="pdm", creator=""),
               _ov_item(id="clip", source="svgsilh")]
    http = FakeHttp({"/images/": {"result_count": 6, "results": results}})
    ov = OpenverseProvider(http)  # type: ignore[arg-type]
    got = ov.search("solar bottle bulb roof close up cinematic", MediaType.IMAGE, 10)
    assert [c.media_id for c in got] == ["abc-1", "pd"]
    assert got[0].author == "Liter of Light (CC BY 2.0) via Flickr" and got[0].page_url.startswith("https://www.flickr")
    assert got[0].thumbnail_url.startswith("https://api.openverse.org/")
    assert "Domínio público" in got[1].author and "desconhecido" in got[1].author
    params = http.calls[0][1]
    assert params["license"] == "cc0,pdm,by" and params["q"] == "solar bottle bulb roof"  # sem "close up cinematic"
    assert ov.search("x", MediaType.VIDEO, 5) == [] and ov.search("garrafa", MediaType.IMAGE, 5, "pt") == []


def test_openverse_share_alike_only_when_allowed():
    from broll_bot.providers.openverse import OpenverseProvider

    http = FakeHttp({"/images/": {"results": [_ov_item(id="sa", license="by-sa")]}})
    assert [c.media_id for c in OpenverseProvider(http, allow_share_alike=True).search(  # type: ignore[arg-type]
        "bottle", MediaType.IMAGE, 5)] == ["sa"]
    assert http.calls[0][1]["license"] == "cc0,pdm,by,by-sa"


def _yt_routes(subs=50_000, license="creativeCommon", title="Solar bottle light installation", duration="PT4M10S"):
    return {
        "/search": {"items": [{"id": {"videoId": "vid1"}}, {"id": {"videoId": "vid2"}}]},
        "/videos": {"items": [
            {"id": "vid1", "snippet": {"title": title, "channelId": "ch1", "channelTitle": "Liter of Light",
                                       "tags": ["solar", "bottle"], "liveBroadcastContent": "none"},
             "contentDetails": {"duration": duration, "definition": "hd"}, "status": {"license": license}},
            {"id": "vid2", "snippet": {"title": "Full Movie HD", "channelId": "ch2", "channelTitle": "X"},
             "contentDetails": {"duration": "PT1H30M", "definition": "hd"}, "status": {"license": "creativeCommon"}}]},
        "/channels": {"items": [{"id": "ch1", "statistics": {"subscriberCount": str(subs)}},
                                {"id": "ch2", "statistics": {"subscriberCount": "900000"}}]},
    }


def test_youtube_cc_finds_one_middle_clip_per_video_with_credit(monkeypatch):
    from broll_bot.providers.youtube_cc import YouTubeCCProvider

    http = FakeHttp(_yt_routes())
    yt = YouTubeCCProvider("KEY", http, clip_seconds=20, max_searches=2)  # type: ignore[arg-type]
    got = yt.search("solar bottle light", MediaType.VIDEO, 10)
    assert [c.media_id for c in got] == ["vid1_115"]  # meio de 250 s: 115–135 s; "Full Movie" fica de fora
    c = got[0]
    assert c.duration == 20 and c.thumbnail_url == "https://i.ytimg.com/vi/vid1/hq2.jpg"
    assert c.author == "Liter of Light (CC BY) via YouTube" and c.page_url.endswith("watch?v=vid1")
    search_params = http.calls[0][1]
    assert search_params["videoLicense"] == "creativeCommon" and search_params["type"] == "video"
    yt.search("again", MediaType.VIDEO, 10)
    assert yt.search("third", MediaType.VIDEO, 10) == []  # teto de buscas do vídeo (cota da chave grátis)
    assert yt.search("x", MediaType.IMAGE, 5) == []


def test_youtube_cc_skips_small_channels_and_non_cc():
    from broll_bot.providers.youtube_cc import YouTubeCCProvider

    small = YouTubeCCProvider("KEY", FakeHttp(_yt_routes(subs=200)))  # type: ignore[arg-type]
    assert small.search("bottle", MediaType.VIDEO, 5) == []
    not_cc = YouTubeCCProvider("KEY", FakeHttp(_yt_routes(license="youtube")))  # type: ignore[arg-type]
    assert not_cc.search("bottle", MediaType.VIDEO, 5) == []
    live = YouTubeCCProvider("KEY", FakeHttp(_yt_routes(title="Ao vivo: telhado")))  # type: ignore[arg-type]
    assert live.search("bottle", MediaType.VIDEO, 5) == []
    assert not YouTubeCCProvider(None, FakeHttp({})).available()  # type: ignore[arg-type]


def test_youtube_cc_download_asks_yt_dlp_only_for_the_section(monkeypatch, tmp_path):
    import sys
    import types

    from broll_bot.providers.youtube_cc import YouTubeCCProvider

    seen = {}

    class FakeYDL:
        def __init__(self, opts):
            seen["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def download(self, urls):
            seen["urls"] = urls
            Path(seen["opts"]["outtmpl"].replace("%(ext)s", "mp4")).write_bytes(b"x" * 10)

    fake = types.ModuleType("yt_dlp")
    fake.YoutubeDL = FakeYDL
    utils = types.ModuleType("yt_dlp.utils")
    utils.download_range_func = lambda chapters, ranges: ("ranges", ranges)
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    monkeypatch.setitem(sys.modules, "yt_dlp.utils", utils)
    yt = YouTubeCCProvider("KEY", FakeHttp(_yt_routes()), clip_seconds=20)  # type: ignore[arg-type]
    cand = yt.search("solar bottle light", MediaType.VIDEO, 5)[0]
    path = yt.download(cand, tmp_path)
    assert path.exists() and path.suffix == ".mp4"
    assert seen["urls"] == ["https://www.youtube.com/watch?v=vid1"]
    assert seen["opts"]["download_ranges"] == ("ranges", [(115.0, 135.0)])
    assert "height<=1080" in seen["opts"]["format"]


def test_free_sources_come_first_and_youtube_is_a_reserve():
    from broll_bot.config import load_config

    cfg = load_config(Path(__file__).resolve().parent.parent / "config.yaml", env_file=None)
    order = cfg.search.provider_order
    assert order[:2] == ["openverse", "wikimedia"] and "youtube_cc" in order
    assert "youtube_cc" in cfg.search.reserve_providers and cfg.ranking.provider_bonus["openverse"] > 0


def test_old_config_yaml_still_gets_the_new_banks(tmp_path):
    from broll_bot.config import load_config

    old = tmp_path / "config.yaml"
    old.write_text("search:\n  provider_order: [mixkit_local, pexels, pixabay, wikimedia]\n", encoding="utf-8")
    order = load_config(old, env_file=None).search.provider_order
    assert order[:4] == ["mixkit_local", "pexels", "pixabay", "wikimedia"] and {"openverse", "youtube_cc"} <= set(order)


def test_build_providers_creates_the_new_banks(tmp_path):
    from broll_bot.config import AppConfig
    from broll_bot.providers import OpenverseProvider, YouTubeCCProvider, build_providers

    cfg = AppConfig()
    cfg.providers["openverse"].enabled = True  # desligado por padrão; ligado à mão (com a chave) ainda funciona
    providers = build_providers(cfg, tmp_path)
    assert isinstance(providers["openverse"], OpenverseProvider) and providers["openverse"].available()
    assert isinstance(providers["youtube_cc"], YouTubeCCProvider) and not providers["youtube_cc"].available()
    assert list(providers)[:2] == ["openverse", "wikimedia"]


def test_openverse_thumbnail_from_flickr_cdn_saves_the_api_limit():
    from broll_bot.providers.openverse import _thumb

    # pela API (com a chave); o CDN do Flickr recusa com 403 (a foto em si fica de 2ª opção no ThumbnailFetcher)
    assert _thumb("https://live.staticflickr.com/7/123_abc_b.jpg", "API") == "API"
    assert _thumb("https://upload.wikimedia.org/a.jpg", "API") == "API"


def test_openverse_accepts_the_1024px_flickr_photo_and_missing_sizes():
    """Primeiro vídeo com o Openverse: 114 buscas e 1 foto usada; a foto do Flickr que ele entrega tem 1024 px
    e o mínimo era 1200 (e foto sem tamanho informado também caía)."""
    from broll_bot.config import AppConfig
    from broll_bot.providers.openverse import OpenverseProvider

    results = [_ov_item(id="flickr", width=1024, height=683), _ov_item(id="nosize", width=None, height=None),
               _ov_item(id="tiny", width=500, height=375)]
    ov = OpenverseProvider(FakeHttp({"/images/": {"results": results}}),  # type: ignore[arg-type]
                           min_width=AppConfig().openverse.min_width)
    assert [c.media_id for c in ov.search("plastic bricks wall", MediaType.IMAGE, 10)] == ["flickr", "nosize"]


def test_report_says_when_a_bank_was_left_out():
    from studio.relatorio import diagnose

    rep = {"scenes": [], "sources_off": {"youtube_cc": "yt-dlp não instalado (rode o instalar_app.bat de novo)",
                                          "mixkit_local": "sem chave no .env"}}
    texts = [d["text"] for d in diagnose(rep)]
    assert any(t.startswith("YouTube Creative Commons não foi usado: yt-dlp") for t in texts)
    assert not any("mixkit" in t.lower() for t in texts)


def test_youtube_reason_names_what_is_missing():
    from broll_bot.providers.youtube_cc import YouTubeCCProvider

    assert "YOUTUBE_API_KEY" in YouTubeCCProvider(None, FakeHttp({})).unavailable_reason()  # type: ignore[arg-type]
    assert "yt-dlp" in YouTubeCCProvider("K", FakeHttp({})).unavailable_reason()  # type: ignore[arg-type]


def test_funnel_shows_where_each_bank_loses_its_candidates(tmp_path):
    """Vídeo do tijolo (2ª versão): o YouTube devolveu 10–14 vídeos por busca e nenhum entrou; sem o funil não
    dava para saber se era miniatura, revisão ou download."""
    from types import SimpleNamespace

    from broll_bot.models import Candidate
    from broll_bot.search import SceneSearchResult, source_funnel

    thumb = tmp_path / "t.jpg"
    thumb.write_bytes(b"x")

    def cand(provider, mid, review="", with_thumb=True):
        c = Candidate(provider, mid, MediaType.VIDEO, 1920, 1080, 25, "u", "d")
        c.review, c.score, c.semantic = review, 0.8, 0.7
        c.thumbnail_path = str(thumb) if with_thumb else None
        return c

    res = SceneSearchResult(0)
    res.ranked = [cand("youtube_cc", "a", "approved"), cand("youtube_cc", "b", "rejected"),
                  cand("youtube_cc", "c", with_thumb=False), cand("pexels", "p", "approved")]
    ranker = SimpleNamespace(is_good=lambda c: c.review != "rejected")
    shots = [SimpleNamespace(candidate=res.ranked[3])]
    f = source_funnel({0: res}, shots, ranker, {"youtube_cc": 1}, {"youtube_cc": ["youtube: download falhou (x)"]})
    yt = f["youtube_cc"]
    assert (yt["found"], yt["thumb"], yt["approved"], yt["rejected"], yt["download_failed"], yt["used"]) == \
        (3, 2, 1, 1, 1, 0)
    assert yt["errors"] == ["youtube: download falhou (x)"] and f["pexels"]["used"] == 1


def test_report_explains_a_bank_that_found_a_lot_and_was_not_used():
    from studio.relatorio import diagnose

    rep = {"scenes": [], "sources_funnel": {
        "youtube_cc": {"found": 120, "thumb": 118, "good": 60, "approved": 30, "rejected": 10, "vetoed": 0,
                       "download_failed": 25, "used": 0,
                       "errors": ["youtube: download falhou (o yt-dlp precisa do Deno para o YouTube — x)"]},
        "openverse": {"found": 400, "thumb": 390, "good": 50, "approved": 5, "rejected": 40, "vetoed": 0,
                      "download_failed": 0, "used": 2}}}
    texts = [d["text"] for d in diagnose(rep)]
    assert any(t.startswith("YouTube Creative Commons: 25 downloads falharam (instale o Deno") for t in texts)
    assert any(t.startswith("Openverse: 400 achados, 40 reprovados pela revisão") for t in texts)


def test_youtube_download_error_points_to_deno(monkeypatch, tmp_path):
    import sys
    import types

    from broll_bot.errors import ProviderError
    from broll_bot.providers import youtube_cc
    from broll_bot.providers.youtube_cc import YouTubeCCProvider

    class FailYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def download(self, urls):
            raise RuntimeError("Requested format is not available")

    fake = types.ModuleType("yt_dlp")
    fake.YoutubeDL = FailYDL
    utils = types.ModuleType("yt_dlp.utils")
    utils.download_range_func = lambda chapters, ranges: ranges
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    monkeypatch.setitem(sys.modules, "yt_dlp.utils", utils)
    monkeypatch.setattr(youtube_cc.shutil, "which", lambda name: None)
    yt = YouTubeCCProvider("KEY", FakeHttp(_yt_routes()))  # type: ignore[arg-type]
    cand = yt.search("solar bottle light", MediaType.VIDEO, 5)[0]
    try:
        yt.download(cand, tmp_path)
    except ProviderError as exc:
        assert "Deno" in str(exc)
    else:
        raise AssertionError("devia falhar")


def _fake_ydl(monkeypatch, error=None, seen=None):
    import sys
    import types

    class YDL:
        def __init__(self, opts):
            if seen is not None:
                seen.append(opts)
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def download(self, urls):
            if error:
                raise RuntimeError(error)
            Path(self.opts["outtmpl"].replace("%(ext)s", "mp4")).write_bytes(b"x" * 10)

    fake = types.ModuleType("yt_dlp")
    fake.YoutubeDL = YDL
    utils = types.ModuleType("yt_dlp.utils")
    utils.download_range_func = lambda chapters, ranges: ranges
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    monkeypatch.setitem(sys.modules, "yt_dlp.utils", utils)


def test_youtube_bot_check_stops_downloads_and_searches_for_the_rest_of_the_video(monkeypatch, tmp_path):
    """3º vídeo do tijolo: 17 downloads seguidos com "Sign in to confirm you're not a bot"."""
    from broll_bot.errors import ProviderError
    from broll_bot.providers.youtube_cc import YouTubeCCProvider

    _fake_ydl(monkeypatch, error="ERROR: [youtube] abc: Sign in to confirm you\u2019re not a bot. Use --cookies")
    http = FakeHttp(_yt_routes())
    yt = YouTubeCCProvider("KEY", http)  # type: ignore[arg-type]
    cand = yt.search("solar bottle light", MediaType.VIDEO, 5)[0]
    for _ in range(2):
        try:
            yt.download(cand, tmp_path)
        except ProviderError as exc:
            assert "cookies_from_browser" in str(exc)
    calls = len(http.calls)
    assert yt.blocked and yt.search("other", MediaType.VIDEO, 5) == [] and len(http.calls) == calls


def test_youtube_uses_browser_cookies_when_configured(monkeypatch, tmp_path):
    from broll_bot.config import AppConfig
    from broll_bot.providers.youtube_cc import YouTubeCCProvider

    assert AppConfig().youtube_cc.cookies_from_browser == ""
    seen: list = []
    _fake_ydl(monkeypatch, seen=seen)
    yt = YouTubeCCProvider("KEY", FakeHttp(_yt_routes()), cookies_from_browser="Firefox")  # type: ignore[arg-type]
    yt.download(yt.search("solar bottle light", MediaType.VIDEO, 5)[0], tmp_path)
    assert seen[0]["cookiesfrombrowser"] == ("firefox",)
    seen.clear()
    yt2 = YouTubeCCProvider("KEY", FakeHttp(_yt_routes()), cookies_file="C:/c.txt")  # type: ignore[arg-type]
    yt2.download(yt2.search("solar bottle light", MediaType.VIDEO, 5)[0], tmp_path / "b")
    assert seen[0]["cookiefile"] == "C:/c.txt" and "cookiesfrombrowser" not in seen[0]


def test_report_points_to_cookies_when_youtube_asks_for_login():
    from studio.relatorio import diagnose

    rep = {"scenes": [], "sources_funnel": {"youtube_cc": {
        "found": 67, "thumb": 67, "good": 18, "approved": 15, "rejected": 27, "vetoed": 0, "download_failed": 17,
        "used": 0, "errors": ["youtube: download falhou (DownloadError: ERROR: [youtube] x: Sign in to confirm you're "
                              "not a bot. Use --cookies-from-browser)"]}}}
    text = next(d["text"] for d in diagnose(rep) if d["text"].startswith("YouTube"))
    assert "Firefox" in text and "cookies_from_browser" in text and "Deno" not in text


def test_thumbnail_falls_back_to_the_photo_itself(monkeypatch, tmp_path):
    """Openverse: 50 fotos achadas, 3 com miniatura (a da API recusava pelo limite anônimo)."""
    import io

    from PIL import Image

    from broll_bot.models import Candidate
    from broll_bot.ranking import ThumbnailFetcher

    buf = io.BytesIO()
    Image.new("RGB", (1600, 1000), "red").save(buf, "JPEG")
    big = buf.getvalue()

    class Resp:
        def __init__(self, code, content=b""):
            self.status_code, self.content = code, content

    tf = ThumbnailFetcher(tmp_path)
    monkeypatch.setattr(tf.session, "get", lambda url, timeout=None, headers=None: Resp(429) if "thumb" in url else Resp(200, big))
    c = Candidate("openverse", "x1", MediaType.IMAGE, 1600, 1000, None, "https://api.openverse.org/v1/images/x1/thumb/",
                  "https://example.org/photo.jpg")
    tf.fetch([c])
    assert c.thumbnail_path and Image.open(c.thumbnail_path).size[0] <= 640


def test_openverse_token_goes_only_to_the_api_never_to_photo_downloads(monkeypatch):
    """Vídeo do Dale: "download negado: HTTP 401" — o token do Openverse estava na sessão e ia junto no
    download da foto (Flickr, S3...)."""
    from broll_bot.providers.openverse import OpenverseProvider

    http = FakeHttp({"/images/": {"results": [_ov_item()]}})
    http.session = type("S", (), {"headers": {}})()
    ov = OpenverseProvider(http, "id", "secret")  # type: ignore[arg-type]

    class R:
        status_code = 200

        @staticmethod
        def json():
            return {"access_token": "TOKEN", "expires_in": 36000}

    monkeypatch.setattr("broll_bot.providers.openverse.requests.post", lambda *a, **k: R())
    ov.search("bottle", MediaType.IMAGE, 5)
    assert http.headers == {"Authorization": "Bearer TOKEN"}
    assert "Authorization" not in http.session.headers
    assert ov.auth_headers() == {"Authorization": "Bearer TOKEN"}


def test_thumbnail_failures_are_reported_with_the_host(tmp_path):
    from broll_bot.models import Candidate
    from broll_bot.ranking import ThumbnailFetcher

    class Resp:
        status_code, content = 403, b""

    tf = ThumbnailFetcher(tmp_path)
    tf.session.get = lambda url, timeout=None, headers=None: Resp()  # type: ignore[assignment]
    c = Candidate("openverse", "z", MediaType.IMAGE, 1600, 1000, None, "https://cdn.example.org/t.jpg",
                  "https://cdn.example.org/p.jpg")
    tf.fetch([c])
    assert tf.reasons["openverse"] == ["HTTP 403 (cdn.example.org)"]


def test_youtube_any_takes_any_license_short_clip_and_credits_the_channel():
    from broll_bot.providers.youtube_cc import YouTubeAnyProvider

    http = FakeHttp(_yt_routes(license="youtube"))
    yt = YouTubeAnyProvider("KEY", http, clip_seconds=8)  # type: ignore[arg-type]
    got = yt.search("solar bottle light", MediaType.VIDEO, 10)
    assert [c.media_id for c in got] == ["vid1_121"]  # meio de 250 s: 121–129 s; "Full Movie" continua de fora
    assert got[0].provider == "youtube_any" and got[0].duration == 8
    assert got[0].author == "Liter of Light (© do canal) via YouTube"
    assert "videoLicense" not in http.calls[0][1]
    small = YouTubeAnyProvider("KEY", FakeHttp(_yt_routes(subs=200, license="youtube")))  # type: ignore[arg-type]
    assert small.search("bottle", MediaType.VIDEO, 5) == []


def test_youtube_any_is_the_last_reserve_and_off_by_default_in_code():
    from broll_bot.config import AppConfig, load_config
    from broll_bot.providers import YouTubeAnyProvider, build_providers

    cfg = AppConfig()
    assert not cfg.providers["youtube_any"].enabled and cfg.search.provider_order[-1] == "youtube_any"
    assert "youtube_any" in cfg.search.reserve_providers
    yaml_cfg = load_config("config.yaml", env_file=None)
    assert yaml_cfg.providers["youtube_any"].enabled and yaml_cfg.youtube_any.clip_seconds == 8
    providers = build_providers(yaml_cfg, Path("."))
    assert isinstance(providers["youtube_any"], YouTubeAnyProvider)
    assert list(providers)[-1] == "youtube_any"


def test_youtube_any_skips_excluded_channels():
    from broll_bot.providers.youtube_cc import YouTubeAnyProvider

    yt = YouTubeAnyProvider("KEY", FakeHttp(_yt_routes(license="youtube")),  # type: ignore[arg-type]
                            exclude_channels=["liter  OF light"])
    assert yt.search("bottle", MediaType.VIDEO, 5) == []


def test_youtube_skips_vertical_shorts_unless_the_video_is_vertical():
    from broll_bot.providers.youtube_cc import YouTubeAnyProvider

    def routes():
        r = _yt_routes(license="youtube")
        r["/videos"]["items"][0]["player"] = {"embedWidth": "270", "embedHeight": "480"}
        return r

    http = FakeHttp(routes())
    assert YouTubeAnyProvider("KEY", http).search("hearing aid", MediaType.VIDEO, 5) == []  # type: ignore[arg-type]
    assert http.calls[1][1]["part"].endswith(",player") and http.calls[1][1]["maxWidth"] == 480
    tall = YouTubeAnyProvider("KEY", FakeHttp(routes()), clip_seconds=8, allow_vertical=True)  # type: ignore[arg-type]
    assert [c.media_id for c in tall.search("hearing aid", MediaType.VIDEO, 5)] == ["vid1_121"]


def test_youtube_any_skips_tv_news_and_entertainment_categories():
    from broll_bot.providers.youtube_cc import YouTubeAnyProvider, YouTubeCCProvider

    def routes(category):
        r = _yt_routes(license="youtube")
        r["/videos"]["items"][0]["snippet"]["categoryId"] = category
        return r

    for tv in ("25", "24", "43"):  # News & Politics, Entertainment, Shows
        assert YouTubeAnyProvider("KEY", FakeHttp(routes(tv))).search("rate cap", MediaType.VIDEO, 5) == []  # type: ignore[arg-type]
    edu = YouTubeAnyProvider("KEY", FakeHttp(routes("27")), clip_seconds=8)  # type: ignore[arg-type]
    assert [c.media_id for c in edu.search("kiln", MediaType.VIDEO, 5)] == ["vid1_121"]
    assert YouTubeAnyProvider.cache_version != "v1" and YouTubeCCProvider.blocked_categories == frozenset()


def test_openverse_is_off_by_default(tmp_path):
    """Operação Greif: o Openverse esperou 41 vezes 20 s sem responder e não deu nenhuma imagem; fica desligado
    (o código continua, para quem cadastrar a chave)."""
    from broll_bot.config import AppConfig, load_config
    from broll_bot.providers import build_providers

    assert not AppConfig().providers["openverse"].enabled
    cfg = load_config("config.yaml", env_file=None)
    assert not cfg.providers["openverse"].enabled
    assert "openverse" not in build_providers(cfg, tmp_path)
