"""Arquivo histórico (Internet Archive): filmes de época em domínio público, um candidato por quadro guardado do
filme, só o trecho baixado; liga no estilo Documentário e tira o youtube_any (reivindicação na filmagem da guerra)."""

import subprocess

from broll_bot.config import AppConfig
from broll_bot.http import HttpClient
from broll_bot.looks import job_overrides
from broll_bot.models import Candidate, MediaType
from broll_bot.providers import build_providers
from broll_bot.providers.archive_org import ArchiveOrgProvider, archive_query, license_ok, pick_segments
from broll_bot.ranking import Ranker
from broll_bot.ratelimit import RetryPolicy, TokenBucket

SEARCH = {"response": {"numFound": 3, "docs": [
    {"identifier": "gov.dod.dimoc.20300", "title": "COMBAT BULLETIN NO. 38", "year": 1945,
     "description": "ALLIES COUNTER IN BATTLE 0F BULGE.<br />", "licenseurl": "http://creativecommons.org/publicdomain/zero/1.0/"},
    {"identifier": "someones-remix", "title": "Bulge remix", "licenseurl": "http://creativecommons.org/licenses/by-nc/4.0/"},
    {"identifier": "1945-01-08_Bulge", "title": "Yanks Hold In Belgium, 1945/01/08"},
]}}


def _meta(name, length, every):
    files = [{"name": f"{name}_512kb.mp4", "format": "512Kb MPEG4", "length": str(length), "width": "320",
              "height": "240"},
             {"name": f"{name}.mp4", "format": "h.264", "length": str(length), "width": "640", "height": "480"}]
    files += [{"name": f"{name}.thumbs/{name}_{s:06d}.jpg", "format": "Thumbnail"}
              for s in [1, *range(every, int(length), every)]]
    return {"files": files, "metadata": {"title": name}}


class Http:
    requests_made = 0

    def __init__(self):
        self.calls = []

    def get_json(self, url, params=None):
        self.calls.append(url)
        if "advancedsearch" in url:
            self.query = params["q"]
            return SEARCH
        ident = url.rsplit("/", 1)[1]
        return _meta(ident, 1235.4 if "dimoc" in ident else 412.1, 60 if "dimoc" in ident else 30)


def test_query_keeps_the_subject_and_license_keeps_only_public_domain():
    assert archive_query("1944 American jeep checkpoint snow archival footage") == ["1944", "american", "jeep",
                                                                                     "checkpoint"]
    assert license_ok({}) and license_ok({"licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/"})
    assert not license_ok({"licenseurl": "http://creativecommons.org/licenses/by-sa/4.0/"})
    # quadros espalhados, sem a abertura (letreiro) nem o fim
    assert pick_segments([1, 30, 60, 90, 120, 150, 180], 200.0, 8.0, 3) == [30, 90, 150]


def test_each_saved_frame_of_the_film_becomes_a_candidate_with_its_thumbnail():
    http = Http()
    prov = ArchiveOrgProvider(http, clip_seconds=8, items_per_search=3, segments_per_item=4)
    cands = prov.search("battle of the bulge american soldiers snow", MediaType.VIDEO, 20)
    assert "collection:(universal_newsreels OR FedFlix" in http.query and "mediatype:movies" in http.query
    films = {c.media_id.rsplit("_", 1)[0] for c in cands}
    assert films == {"gov.dod.dimoc.20300", "1945-01-08_Bulge"}  # o remix CC BY-NC ficou de fora
    c = cands[0]
    sec = int(c.media_id.rsplit("_", 1)[1])
    assert c.download_url.endswith("/gov.dod.dimoc.20300.mp4")  # o h.264, não o de 320 px
    assert c.thumbnail_url.endswith(f"_{sec:06d}.jpg") and f"start={sec}" in c.page_url
    assert (c.width, c.duration) == (640, 8) and "domínio público" in c.author
    assert len([x for x in cands if x.media_id.startswith("gov.dod")]) == 4
    # o mesmo filme em outra busca não pede a ficha de novo
    n = len(http.calls)
    prov.search("battle of the bulge tanks", MediaType.VIDEO, 20)
    assert len(http.calls) == n + 1
    assert prov.search("battle of the bulge", MediaType.IMAGE, 5) == []


def test_download_cuts_only_the_section_without_sound(tmp_path):
    from broll_bot.media import probe

    src = tmp_path / "film.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=60",
                    "-f", "lavfi", "-i", "sine=duration=60", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", str(src)], check=True)
    http = HttpClient("archive", TokenBucket(100, 100), RetryPolicy(max_retries=0), timeout=5)
    prov = ArchiveOrgProvider(http)
    cand = Candidate("archive", "film_30", MediaType.VIDEO, 320, 240, 8.0, None, str(src))
    path = prov.download(cand, tmp_path / "media")
    info = probe(path)
    assert 7.5 <= info.duration <= 8.5
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                              str(path)], capture_output=True, text=True).stdout.split()
    assert streams == ["video"]


def test_archive_only_runs_in_the_documentary_style_which_drops_youtube_any(tmp_path):
    cfg = AppConfig()
    cfg.youtube_api_key = "k"
    assert "archive" in cfg.search.provider_order and not cfg.archive.enabled
    assert "archive" not in build_providers(cfg, tmp_path)
    over = job_overrides(style="documentario")
    assert over["archive"] == {"enabled": True} and over["search"] == {"skip_providers": ["youtube_any"]}
    assert "archive" not in job_overrides(style="mix")
    cfg.archive.enabled = True
    cfg.search.skip_providers = ["youtube_any"]
    provs = build_providers(cfg, tmp_path)
    assert "archive" in provs and "youtube_any" not in provs and "youtube_cc" in provs


def test_old_film_is_not_dropped_for_being_640_px():
    cfg = AppConfig()
    rk = Ranker(cfg, None, None)
    film = Candidate("archive", "f_30", MediaType.VIDEO, 640, 480, 8.0, None, "u", description="battle of the bulge")
    stock = Candidate("pexels", "1", MediaType.VIDEO, 640, 360, 8.0, None, "u", description="battle of the bulge")
    from types import SimpleNamespace

    scene = SimpleNamespace(text="", analysis=SimpleNamespace(subject="", queries=["battle of the bulge"],
                                                              visual_summary="", negative_terms=[]))
    assert [c.provider for c in rk._prefilter(scene, [film, stock])] == ["archive"]
    assert rk._quality(film) == 0.75


def test_old_film_look_goes_on_period_film_and_on_some_scenes_of_stock_video(tmp_path):
    from broll_bot.assembler import Assembler
    from broll_bot.models import Shot, Transition

    cfg = AppConfig()
    over = job_overrides(style="documentario")["timeline"]
    assert over["old_film_providers"] == ["archive"] and 0 < over["old_film_chance"] < 0.5
    asm = Assembler(cfg, tmp_path)

    def shot(provider, media=MediaType.VIDEO, scene=0):
        s = Shot(index=0, scene_index=scene, cut_in=0, cut_out=2, clip_duration=2, transition_in=Transition("cut", 0))
        s.candidate = Candidate(provider, "x", media, 640, 480, 8.0, None, "u")
        return s

    assert asm.grade_for(shot("archive")) is None  # fora do Documentário, nada muda
    cfg.timeline.old_film_providers, cfg.timeline.old_film_chance = ["archive"], 0.25
    assert asm.grade_for(shot("archive")) == "oldfilm"
    assert asm.grade_for(shot("wikimedia", MediaType.IMAGE)) is None  # foto colorida de hoje não vira filme
    stock = [asm.grade_for(shot("pexels", scene=k)) for k in range(200)]
    assert 0.15 < stock.count("oldfilm") / 200 < 0.35  # "em alguns trechos"
    assert stock == [asm.grade_for(shot("pexels", scene=k)) for k in range(200)]  # o mesmo a cada renderização


def test_old_film_filter_renders_sepia_grain_with_ffmpeg(tmp_path):
    import numpy as np

    from broll_bot.framing import build_filter

    src, out = tmp_path / "in.mp4", tmp_path / "out.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x480:rate=10:duration=2",
                    "-c:v", "libx264", "-preset", "ultrafast", str(src)], check=True)
    for effect in (None, "burn"):
        filt = build_filter({"name": "full"}, 320, 180, 10, 15, grade="oldfilm", effect_in=effect)
        extra = ["-f", "lavfi", "-i", "color=black:size=320x180:rate=10:duration=2"] if effect else []
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), *extra, "-filter_complex", filt,
                        "-map", "[v]", "-frames:v", "15", str(out)], check=True)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt",
                          "rgb24", "-"], capture_output=True, check=True).stdout
    px = np.frombuffer(raw, np.uint8).reshape(180, 320, 3).astype(int)
    r, g, b = px[..., 0].mean(), px[..., 1].mean(), px[..., 2].mean()
    assert r > g > b  # o colorido do testsrc2 virou sépia


def test_old_film_never_moves_the_frame():
    """Vídeo da Operação Greif: o filme antigo balançava o quadro até 8 px de um quadro para o outro e piscava a luz
    5 vezes por segundo: a tela inteira tremia. O corte é fixo e a luz varia pouco."""
    import re

    import numpy as np

    from broll_bot.framing import OLD_FILM_FLICKER, build_filter, old_film

    chain = old_film(1920, 1080)
    crop = re.search(r"crop=([^,]+)", chain).group(1)
    assert "sin" not in crop and "n*" not in crop and "t*" not in crop  # recorte parado
    assert OLD_FILM_FLICKER <= 0.015
    # mesmo quadro de entrada parado: a saída não anda (fase de correlação entre quadros = 0)
    tmp = __import__("tempfile").mkdtemp()
    src, out = f"{tmp}/in.mp4", f"{tmp}/out.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=1",
                    "-vf", "trim=end_frame=1,loop=29:1:0,setpts=N/30/TB", "-c:v", "libx264", "-preset", "ultrafast",
                    src], check=True)
    filt = build_filter({"name": "full"}, 320, 180, 30, 20, grade="oldfilm")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-filter_complex", filt, "-map", "[v]", "-frames:v",
                    "20", out], check=True)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, 180, 320).astype(np.float32)
    for a, b in zip(fr, fr[1:]):
        A, B = np.fft.fft2(a - a.mean()), np.fft.fft2(b - b.mean())
        R = A * np.conj(B)
        c = np.abs(np.fft.ifft2(R / (np.abs(R) + 1e-6)))
        assert np.unravel_index(c.argmax(), c.shape) == (0, 0)


def test_archive_keeps_to_the_years_of_the_script():
    """O cinejornal "Berlin 1961" entrou num vídeo sobre dezembro de 1944."""
    from broll_bot.providers.archive_org import era_ok, script_years

    years = script_years("Le 17 décembre 1944... En 1947, Otto Skorzeny est jugé à Dachau.")
    assert years == (1944, 1947) and script_years("sem ano nenhum") is None
    assert not era_ok({"year": 1961, "collection": ["universal_newsreels"]}, years)
    assert era_ok({"year": "1945", "collection": ["universal_newsreels"]}, years)
    assert era_ok({"year": 1958, "collection": ["FedFlix"]}, years)  # retrospectiva com imagens da época
    assert not era_ok({"year": 1997, "collection": ["FedFlix"]}, years)
    assert era_ok({}, years) and era_ok({"year": 1961}, None)
    http = Http()
    prov = ArchiveOrgProvider(http)
    before = prov.cache_version
    prov.years = years
    assert prov.cache_version != before  # a busca guardada de outra época não vale


def test_provider_that_stops_answering_pauses_after_one_retry():
    """Openverse: 41 esperas de 20 s (3 tentativas por busca) no vídeo da Operação Greif, 0 imagens usadas."""
    import pytest
    import requests

    from broll_bot.errors import UnreachableError

    class Slow(requests.Session):
        calls = 0

        def get(self, *a, **k):  # type: ignore[override]
            Slow.calls += 1
            raise requests.ReadTimeout("read timed out")

    c = HttpClient("openverse", TokenBucket(100, 100), RetryPolicy(max_retries=3), session=Slow())
    c._sleep_backoff = lambda *a, **k: None
    with pytest.raises(UnreachableError, match="não respondeu"):
        c.get_json("https://api.openverse.org/v1/images/")
    assert Slow.calls == 2
