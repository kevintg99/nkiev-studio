"""Avatar da HeyGen: momentos (abertura, inscrição, final), cliente com HTTP falso, cache e o recorte do verde."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from broll_bot.avatar import AvatarJob, AvatarSegment, HeyGen, attach, avatar_filter, composite, pick_segments
from broll_bot.channels import apply_channel, apply_overrides
from broll_bot.config import AppConfig, AvatarConfig
from broll_bot.looks import job_overrides
from broll_bot.models import Sentence, Shot, Transition
from studio import canais, chaves

ROOT = Path(__file__).resolve().parent.parent
HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _sentences(spec):
    return [Sentence(i, text, 0, a, b) for i, (text, a, b) in enumerate(spec)]


SCRIPT = _sentences([
    ("A Chinese electric car, taken apart.", 0.2, 3.0),
    ("That is how this story starts.", 3.2, 5.0),
    ("These cars are everywhere now.", 5.2, 8.0),
    ("You see them at traffic lights.", 8.2, 11.0),
    ("Lots of detail here.", 11.2, 60.0),
    ("If you like this, subscribe and hit the bell.", 60.5, 64.0),
    ("More detail.", 64.5, 110.0),
    ("I'm Jhonny Walt.", 110.5, 112.0),
    ("Look after your car, and it will look after you.", 112.2, 115.0),
])


def test_moments_intro_cta_outro():
    segs = pick_segments(SCRIPT, AvatarConfig(), 116.0)
    assert [g.moment for g in segs] == ["intro", "cta", "outro"]
    intro, cta, outro = segs
    assert intro.start == pytest.approx(0.08) and 6 <= intro.duration <= 12.5  # frases inteiras
    assert intro.end == pytest.approx(8.3)
    assert cta.start == pytest.approx(60.38) and cta.end == pytest.approx(64.3)
    assert outro.start == pytest.approx(110.38) and outro.end == pytest.approx(115.3)


def test_moments_respect_choice_and_budget():
    cfg = AvatarConfig(moments=["intro"])
    assert [g.moment for g in pick_segments(SCRIPT, cfg, 116.0)] == ["intro"]
    cfg = AvatarConfig(max_seconds=10)
    segs = pick_segments(SCRIPT, cfg, 116.0)
    assert sum(g.duration for g in segs) <= 10.01 and segs[0].moment == "intro"
    # sem frase de inscrição: só abertura e final
    plain = [s for s in SCRIPT if "subscribe" not in s.text]
    assert [g.moment for g in pick_segments(plain, AvatarConfig(), 116.0)] == ["intro", "outro"]


def test_cta_inside_intro_is_not_repeated():
    sents = _sentences([("Subscribe now, this is the best car ever.", 0.0, 4.0), ("Body.", 4.2, 80.0),
                        ("Bye.", 80.2, 84.0)])
    moments = [g.moment for g in pick_segments(sents, AvatarConfig(), 84.0)]
    assert moments.count("cta") == 0 and moments[0] == "intro"


def test_attach_marks_overlapping_shots():
    shots = [Shot(i, 0, i * 2.0, i * 2.0 + 2, 2.0, Transition("cut", 0), start_frame=i * 60, end_frame=i * 60 + 60)
             for i in range(6)]
    segs = [AvatarSegment("intro", 0.1, 5.0, path="a.mp4"), AvatarSegment("cta", 9.0, 11.0, path="b.mp4")]
    assert attach(shots, segs, 30) == 5
    assert [len(s.avatar) for s in shots] == [1, 1, 1, 0, 1, 1]
    assert shots[2].avatar[0]["shot_start"] == 4.0 and shots[4].avatar[0]["moment"] == "cta"


def test_filter_full_screen_covers_frame():
    items = [{"path": "a.mp4", "start": 1.0, "end": 6.0, "shot_start": 0.0, "moment": "intro"}]
    f = avatar_filter(items, AvatarConfig(), 1920, 1080, 30)
    assert "chromakey" not in f and "scale=1920:1080:force_original_aspect_ratio=increase" in f
    assert "overlay=x=0:y=0" in f and "fade=" not in f  # entra e sai pela transição dos shots
    assert "tpad=start_mode=clone:start_duration=1.000" in f  # boca sincronizada: quadro parado até a fala


def test_full_screen_avatar_takes_whole_shots():
    # abertura 0,08–6,32 s; o shot 1 começa em 6,02 (a transição dele cruza o fim do trecho): fica sem avatar
    shots = [Shot(0, 0, 0.0, 6.021, 6.1, Transition("cut", 0), start_frame=0, end_frame=184),
             Shot(1, 1, 6.021, 10.76, 5.0, Transition("slideleft", 0.21), start_frame=177, end_frame=330),
             Shot(2, 2, 10.76, 16.46, 6.0, Transition("cut", 0), start_frame=323, end_frame=494)]
    segs = [AvatarSegment("intro", 0.08, 6.32, path="a.mp4")]
    assert attach(shots, segs, 30, full=True) == 1
    assert shots[0].avatar and not shots[1].avatar
    assert attach(shots, segs, 30) == 2  # no canto: entra e sai com fade dentro dos shots


def test_filter_delays_and_fades():
    items = [{"path": "a.mp4", "start": 1.0, "end": 6.0, "shot_start": 0.0, "moment": "intro"}]
    f = avatar_filter(items, AvatarConfig(layout="corner"), 1920, 1080, 30)
    assert "chromakey=0x00FF00" in f and "setpts=PTS+1.000/TB" in f and "fade=t=in" in f
    assert "x=W-w-48:y=H-h" in f and "scale=-2:540" in f
    # shot no meio do trecho: sem entrada suave, sem atraso, à esquerda
    items[0]["shot_start"] = 3.0
    f = avatar_filter(items, AvatarConfig(layout="corner", position="left"), 1920, 1080, 30)
    assert "fade=t=in" not in f and "PTS+" not in f and "x=48:" in f and "trim=end=3.000" in f


class FakeResp:
    def __init__(self, status=200, data=None, content=b""):
        self.status_code, self._data, self.content, self.text = status, data, content, json.dumps(data)

    def json(self):
        return self._data

    def iter_content(self, n):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeHTTP:
    def __init__(self, statuses=("processing", "completed")):
        self.calls = []
        self.statuses = list(statuses)

    def post(self, url, data=None, json=None, headers=None, timeout=None):
        self.calls.append(("POST", url, json))
        assert headers["X-Api-Key"] == "k"
        if "talking_photo" in url:
            assert headers["Content-Type"] in ("image/jpeg", "image/png")
            return FakeResp(data={"code": 100, "data": {"talking_photo_id": "tp9"}})
        if "upload" in url:
            assert headers["Content-Type"] in ("audio/mpeg", "audio/x-wav")
            return FakeResp(data={"code": 100, "data": {"id": "asset1"}})
        return FakeResp(data={"error": None, "data": {"video_id": "vid1"}})

    def get(self, url, params=None, headers=None, timeout=None, stream=False):
        self.calls.append(("GET", url, params))
        if "video_status" in url:
            st = self.statuses.pop(0) if self.statuses else "completed"
            return FakeResp(data={"code": 100, "data": {"status": st, "video_url": "https://x/v.mp4"}})
        if "avatars" in url:
            return FakeResp(data={"error": None, "data": {
                "avatars": [{"avatar_id": "Anna_public", "avatar_name": "Anna", "preview_image_url": "p.jpg"}],
                "talking_photos": [{"talking_photo_id": "tp1", "talking_photo_name": "Eu"}]}})
        return FakeResp(content=b"MP4DATA")


def test_client_generate_body_and_wait():
    http = FakeHTTP()
    hg = HeyGen("k", session=http)
    vid = hg.generate("asset1", AvatarConfig(avatar_id="Anna_public", layout="corner"))
    body = http.calls[-1][2]
    char = body["video_inputs"][0]["character"]
    assert vid == "vid1" and char == {"type": "avatar", "avatar_id": "Anna_public", "avatar_style": "normal"}
    assert body["video_inputs"][0]["voice"] == {"type": "audio", "audio_asset_id": "asset1"}
    assert body["video_inputs"][0]["background"]["value"] == "#00FF00"  # no canto: verde para recortar
    hg.generate("asset1", AvatarConfig(avatar_id="tp1", kind="photo", layout="corner"))
    assert http.calls[-1][2]["video_inputs"][0]["character"] == {"type": "talking_photo", "talking_photo_id": "tp1",
                                                                 "talking_style": "expressive", "matting": True}
    # tela cheia com a imagem do personagem: a cena é a da imagem (sem fundo trocado, sem recorte)
    hg.generate("asset1", AvatarConfig(), 1920, 1080, photo_id="tp9")
    inp = http.calls[-1][2]["video_inputs"][0]
    assert inp["character"] == {"type": "talking_photo", "talking_photo_id": "tp9", "talking_style": "expressive"}
    assert "background" not in inp
    hg.generate("asset1", AvatarConfig(), 1920, 1080, photo_id="tp9", engine="avatar_iv")
    assert http.calls[-1][2]["video_inputs"][0]["character"] == {"type": "talking_photo", "talking_photo_id": "tp9",
                                                                 "use_avatar_iv_model": True}
    import time

    assert hg.wait("vid1", time.monotonic() + 60, poll=0, sleep=lambda s: None) == "https://x/v.mp4"
    assert [a["kind"] for a in hg.avatars()] == ["avatar", "photo"]


def test_client_errors():
    class Bad(FakeHTTP):
        def get(self, url, **kw):
            return FakeResp(status=401, data={"error": {"message": "bad key"}})

    from broll_bot.avatar import HeyGenError

    with pytest.raises(HeyGenError, match="recusada"):
        HeyGen("k", session=Bad()).avatars()


def test_wait_survives_a_dropped_connection():
    """A conexão caiu no meio da espera (ConnectionResetError 10054): o vídeo segue na HeyGen, então a espera
    pergunta de novo em vez de perder o trecho (o "checkin" do Dale saiu sem avatar assim)."""
    import time

    import requests

    class Flaky(FakeHTTP):
        drops = 1

        def get(self, url, params=None, headers=None, timeout=None, stream=False):
            if "video_status" in url and Flaky.drops:
                Flaky.drops -= 1
                raise requests.ConnectionError("Connection aborted.")
            return super().get(url, params=params, headers=headers, timeout=timeout, stream=stream)

    hg = HeyGen("k", session=Flaky())
    assert hg.wait("vid1", time.monotonic() + 60, poll=0, sleep=lambda s: None) == "https://x/v.mp4"


@pytest.mark.skipif(not HAS_FFMPEG, reason="precisa do ffmpeg")
def test_slow_heygen_is_awaited_while_the_video_is_still_being_made(tmp_path):
    """A HeyGen levou mais que ``wait_minutes``, mas o vídeo ainda estava na busca: o bot desistia no prazo
    contado do início e os 4 clipes (já pagos) ficavam de fora. Agora só desiste depois que o vídeo pede."""
    import threading
    import time

    audio = tmp_path / "n.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=10", str(audio)], check=True)
    ready = threading.Event()

    class Slow(FakeHTTP):
        def get(self, url, params=None, headers=None, timeout=None, stream=False):
            if "video_status" in url:
                st = "completed" if ready.is_set() else "processing"
                return FakeResp(data={"code": 100, "data": {"status": st, "video_url": "https://x/v.mp4"}})
            return super().get(url, params=params, headers=headers, timeout=timeout, stream=stream)

    cfg = AvatarConfig(avatar_id="Anna_public", wait_minutes=0.001, layout="corner")  # prazo de 0,06 s
    job = AvatarJob(cfg, "k", audio, [AvatarSegment("intro", 0.0, 6.0)], tmp_path / "cache", tmp_path / "work",
                    client=HeyGen("k", session=Slow()))
    job.client.wait = lambda vid, deadline, **kw: HeyGen.wait(job.client, vid, deadline, poll=0.01)
    job.start()
    time.sleep(0.4)  # o vídeo ainda buscando b-roll, bem depois do prazo
    ready.set()  # a HeyGen termina
    assert [g.status for g in job.result()] == ["ok"]


@pytest.mark.skipif(not HAS_FFMPEG, reason="precisa do ffmpeg")
def test_job_generates_once_then_uses_cache(tmp_path):
    audio = tmp_path / "n.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=20", str(audio)], check=True)
    cfg = AvatarConfig(avatar_id="Anna_public", wait_minutes=1, layout="corner")

    def run_job():
        http = FakeHTTP()
        segs = [AvatarSegment("intro", 0.0, 6.0), AvatarSegment("outro", 14.0, 19.0)]
        job = AvatarJob(cfg, "k", audio, segs, tmp_path / "cache", tmp_path / "work", client=HeyGen("k", session=http))
        job.client.wait = lambda vid, deadline, **kw: "https://x/v.mp4"
        return job.start().result(), http

    ready, http = run_job()
    assert [g.status for g in ready] == ["ok", "ok"]
    assert sum(1 for c in http.calls if "generate" in c[1]) == 2
    assert Path(ready[0].path).read_bytes() == b"MP4DATA"
    ready, http = run_job()  # mesmo áudio, mesmo avatar: não paga de novo
    assert [g.status for g in ready] == ["cache", "cache"] and not http.calls


@pytest.mark.skipif(not HAS_FFMPEG, reason="precisa do ffmpeg")
def test_composite_keys_out_green(tmp_path):
    av, base, out = tmp_path / "av.mp4", tmp_path / "base.mp4", tmp_path / "out.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x00FF00:s=640x360:r=25:d=4",
                    "-vf", "drawbox=x=260:y=100:w=120:h=260:color=red@1:t=fill", "-pix_fmt", "yuv420p", str(av)],
                   check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x0000FF:s=320x180:r=30:d=2",
                    "-pix_fmt", "yuv420p", str(base)], check=True)
    items = [{"path": str(av), "start": 0.5, "end": 3.5, "shot_start": 0.0, "moment": "intro"}]
    composite(base, items, out, AvatarConfig(layout="corner"), 320, 180, 30, 60,
              ["-c:v", "libx264", "-pix_fmt", "yuv420p"])
    frame = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1.5", "-i", str(out), "-frames:v", "1", "-f", "rawvideo",
                            "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    px = lambda x, y: frame[(y * 320 + x) * 3:(y * 320 + x) * 3 + 3]  # noqa: E731
    # avatar: 80x90 no canto de baixo à direita (x 232–312); o corpo vermelho fica em x 257–287, y 115–180
    r, g, b = px(272, 170)
    assert r > 150 and g < 90
    r, g, b = px(240, 150)  # dentro da caixa do avatar, fora do corpo: o verde sumiu, aparece o azul do shot
    assert b > 150 and g < 90
    r, g, b = px(20, 20)
    assert b > 150


def test_channel_avatar_config_and_video_switch(tmp_path):
    (tmp_path / "canais").mkdir()
    ch = tmp_path / "canais" / "teste.yaml"
    ch.write_text(yaml.safe_dump({"canal": {"nome": "Teste", "idioma": "en"},
                                  "avatar": {"avatar_id": "Anna_public", "position": "left", "name": "Anna"}}),
                  encoding="utf-8")
    cfg = apply_channel(AppConfig(), ch)
    assert cfg.avatar.avatar_id == "Anna_public" and cfg.avatar.position == "left" and cfg.avatar.enabled
    over = tmp_path / "ajustes.yaml"
    over.write_text(yaml.safe_dump(job_overrides("mix", avatar=False)), encoding="utf-8")
    apply_overrides(cfg, over)
    assert cfg.avatar.enabled is False and cfg.avatar.avatar_id == "Anna_public"
    assert "avatar" not in job_overrides("mix")


def test_app_saves_channel_avatar(tmp_path):
    canais.create(tmp_path, "Canal X", "en", "£")
    stem = canais.list_all(tmp_path)[0]["file"]
    c = canais.update(tmp_path, stem, "Canal X", "en", "£",
                      avatar={"id": "tp1", "kind": "photo", "position": "left", "name": "Eu", "preview": "p.jpg"})
    assert c["avatar"] == {"id": "tp1", "kind": "photo", "layout": "full", "photo": "", "engine": "avatar_iv",
                           "position": "left",
                           "name": "Eu", "preview": "p.jpg"}
    cfg = apply_channel(AppConfig(), tmp_path / "canais" / f"{stem}.yaml")
    assert cfg.avatar.kind == "photo" and cfg.avatar.avatar_id == "tp1"
    c = canais.update(tmp_path, stem, "Canal X", "en", "£", avatar={"photo": "canais/avatar/canal_x.jpg"})
    assert c["avatar"]["photo"] == "canais/avatar/canal_x.jpg" and c["avatar"]["layout"] == "full"
    cfg = apply_channel(AppConfig(), tmp_path / "canais" / f"{stem}.yaml")
    assert cfg.avatar.photo == "canais/avatar/canal_x.jpg" and cfg.avatar.layout == "full"
    c = canais.update(tmp_path, stem, "Canal X", "en", "£", avatar={"id": ""})  # tirou o avatar
    assert c["avatar"]["id"] == ""
    c = canais.update(tmp_path, stem, "Canal X", "en", "£")  # editar sem mexer no avatar: continua sem
    assert c["avatar"]["id"] == ""


def test_heygen_key_listed():
    assert any(k["env"] == "HEYGEN_API_KEY" for k in chaves.KEYS)
    assert chaves.test_key("HEYGEN_API_KEY", "") == (False, "Falta preencher")


def test_existing_channels_have_no_avatar_by_default():
    cfg = apply_channel(AppConfig(), ROOT / "tests" / "fixtures" / "canais" / "jhonny_walt.yaml")
    assert cfg.avatar.avatar_id == ""


def _photo(path, color=(120, 80, 40)):
    from PIL import Image

    Image.new("RGB", (1920, 1080), color).save(path)
    return path


@pytest.mark.skipif(not HAS_FFMPEG, reason="precisa do ffmpeg")
def test_full_job_animates_the_character_image(tmp_path):
    audio = tmp_path / "n.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=8", str(audio)], check=True)
    photo = _photo(tmp_path / "mecanico.jpg")

    def run_job():
        http = FakeHTTP()
        job = AvatarJob(AvatarConfig(photo=str(photo)), "k", audio, [AvatarSegment("intro", 0.0, 6.0)],
                        tmp_path / "cache", tmp_path / "work", client=HeyGen("k", session=http), photo=photo)
        job.client.wait = lambda vid, deadline, **kw: "https://x/v.mp4"
        return job.start().result(), http

    ready, http = run_job()
    assert [g.status for g in ready] == ["ok"]
    assert sum("talking_photo" in c[1] for c in http.calls) == 1  # a imagem sobe uma vez
    gen = [c for c in http.calls if "generate" in c[1]][0][2]
    inp = gen["video_inputs"][0]
    assert inp["character"] == {"type": "talking_photo", "talking_photo_id": "tp9", "use_avatar_iv_model": True}
    assert "background" not in inp and gen["dimension"] == {"width": 1920, "height": 1080}
    assert ready[0].engine == "avatar_iv" and not ready[0].note
    ready, http = run_job()  # outro vídeo do canal com o mesmo trecho: nada sobe, nada é gerado
    assert [g.status for g in ready] == ["cache"] and not http.calls


def test_start_needs_image_or_id_and_key(tmp_path):
    from broll_bot.avatar import start_avatar

    cfg = AppConfig()
    assert start_avatar(cfg, tmp_path / "a.wav", SCRIPT, 116, tmp_path, tmp_path, tmp_path) is None  # nada no canal
    cfg.avatar.photo = "canais/avatar/nao_existe.jpg"
    cfg.heygen_api_key = "k"
    assert start_avatar(cfg, tmp_path / "a.wav", SCRIPT, 116, tmp_path, tmp_path, tmp_path) is None  # imagem sumiu
    (tmp_path / "canais" / "avatar").mkdir(parents=True)
    _photo(tmp_path / "canais" / "avatar" / "jw.jpg")
    cfg.avatar.photo, cfg.heygen_api_key = "canais/avatar/jw.jpg", None
    assert start_avatar(cfg, tmp_path / "a.wav", SCRIPT, 116, tmp_path, tmp_path, tmp_path) is None  # sem chave


@pytest.mark.skipif(not HAS_FFMPEG, reason="precisa do ffmpeg")
def test_full_screen_avatar_under_subscribe_overlay(tmp_path):
    from broll_bot.assembler import Assembler
    from broll_bot.models import Candidate, MediaType

    av, img = tmp_path / "av.mp4", tmp_path / "car.jpg"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0xC81E1E:s=640x360:r=25:d=4",
                    "-pix_fmt", "yuv420p", str(av)], check=True)
    from PIL import Image

    Image.new("RGB", (640, 360), (20, 40, 200)).save(img)
    cfg = AppConfig()
    cfg.output.encoder, cfg.output.width, cfg.output.height, cfg.output.fps = "cpu", 320, 180, 30
    asm = Assembler(cfg, tmp_path / "r")
    cand = Candidate("local", "1", MediaType.IMAGE, 640, 360, None, None, None)
    shot = Shot(0, 0, 0, 2, 2, Transition("cut", 0), start_frame=0, end_frame=60, candidate=cand,
                media_path=str(img), layout={"name": "full"},
                overlay={"kind": "subscribe", "title": "", "detail": "", "title_b": "", "detail_b": "", "lines": [],
                         "start": 0.0})
    shot.avatar = [{"path": str(av), "start": 0.0, "end": 4.0, "shot_start": 0.0, "moment": "cta"}]
    out = asm.render_shot(shot, tmp_path / "shot.mp4")
    frame = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1.0", "-i", str(out), "-frames:v", "1", "-f", "rawvideo",
                            "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    r, g, b = frame[(10 * 320 + 10) * 3:(10 * 320 + 10) * 3 + 3]
    assert r > 150 and b < 90  # o avatar cobre a tela (no lugar do b-roll azul)
    assert shot.avatar and shot.overlay  # e o botão de inscrição continua por cima


@pytest.mark.skipif(not HAS_FFMPEG, reason="precisa do ffmpeg")
def test_avatar_iv_refused_falls_back_to_standard(tmp_path):
    audio = tmp_path / "n.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=8", str(audio)], check=True)
    photo = _photo(tmp_path / "mecanico.jpg")

    class NoIV(FakeHTTP):
        def post(self, url, data=None, json=None, headers=None, timeout=None):
            if json and json["video_inputs"][0]["character"].get("use_avatar_iv_model"):
                self.calls.append(("POST", url, json))
                return FakeResp(status=400, data={"error": {"message": "Avatar IV not available on this plan"}})
            return super().post(url, data=data, json=json, headers=headers, timeout=timeout)

    http = NoIV()
    job = AvatarJob(AvatarConfig(photo=str(photo)), "k", audio, [AvatarSegment("intro", 0.0, 6.0)],
                    tmp_path / "cache", tmp_path / "work", client=HeyGen("k", session=http), photo=photo)
    job.client.wait = lambda vid, deadline, **kw: "https://x/v.mp4"
    ready = job.start().result()
    assert [g.status for g in ready] == ["ok"] and ready[0].engine == "standard"
    assert "not available on this plan" in ready[0].note  # o motivo vai para o report


def test_checkins_every_minute_in_the_first_three_minutes():
    spec, t = [("A Chinese electric car, taken apart.", 0.2, 6.5)], 7.0
    while t < 240:
        # frases de ~5 s; a cada 20 s uma com número (onde entra gráfico)
        text = "About seventy-five per cent of the parts." if int(t) % 20 < 5 else "That is the secret here."
        spec.append((text, t, t + 4.8))
        t += 5.0
    spec.append(("Look after your car.", 241.0, 246.0))
    segs = pick_segments(_sentences(spec), AvatarConfig(checkin_until=180), 247.0)
    checks = [g for g in segs if g.moment == "checkin"]
    assert len(checks) == 3
    for g, minute in zip(checks, (60, 120, 180)):
        assert abs(g.start - minute) <= 18.5 and 4 <= g.duration <= 9.6
    texts = {s.start: s.text for s in _sentences(spec)}
    assert all("per cent" not in texts[round(g.start + 0.12, 2)] for g in checks)  # sem número
    assert [g.moment for g in segs][0] == "intro" and segs[-1].moment == "outro"
    assert not [g for g in pick_segments(_sentences(spec), AvatarConfig(checkin_every=0), 247.0)
                if g.moment == "checkin"]


def test_scenes_under_full_screen_avatar_lose_template_but_keep_subscribe():
    from broll_bot.avatar import free_scenes
    from broll_bot.models import Scene, SceneAnalysis

    def scene(i, a, b, style="normal", overlay=None):
        return Scene(i, "x", a, b, SceneAnalysis("", [], "", style=style, overlay=overlay or {}))

    scenes = [scene(0, 0.0, 6.0, "exploded"), scene(1, 6.0, 12.0, "percentbar"),
              scene(2, 60.0, 66.0, overlay={"kind": "subscribe"}), scene(3, 200.0, 206.0, overlay={"kind": "phrase"})]
    notes = free_scenes(scenes, [AvatarSegment("intro", 0.0, 6.3), AvatarSegment("cta", 60.0, 66.0),
                                 AvatarSegment("outro", 201.0, 206.0)])
    assert scenes[0].analysis.style == "normal" and scenes[1].analysis.style == "percentbar"
    assert scenes[2].analysis.overlay["kind"] == "subscribe" and scenes[3].analysis.overlay == {}
    assert len(notes) == 2


def test_heygen_prices_and_usage():
    from broll_bot import usage
    from broll_bot.avatar import heygen_rate, wallet_usd

    assert heygen_rate("photo", "avatar_iv") == ("photo_avatar_iv", 0.0385)
    assert heygen_rate("photo", "standard")[1] == 0.0165
    assert heygen_rate("avatar", "avatar_iv")[1] == 0.0805
    assert heygen_rate("photo", "avatar_iv", {"photo_avatar_iv": 0.05})[1] == 0.05  # config.yaml muda a tabela
    usage.reset()
    usage.record("openai", "gpt-5-mini", 100_000, 10_000, "plano de cenas")
    usage.charge("avatar (HeyGen)", 0.385, "HeyGen Avatar IV · foto", "intro 10.0 s", 10.0)
    usage.charge("avatar (HeyGen)", 0.1925, "HeyGen Avatar IV · foto", "outro 5.0 s", 5.0)
    u = usage.summary()
    av = u["steps"]["avatar (HeyGen)"]
    assert av["usd"] == 0.5775 and av["seconds"] == 15.0 and "intro" in av["detail"]
    assert u["services_usd"] == 0.5775 and u["usd"] == round(0.5775 + 0.045, 4)
    assert u["calls"] == 1  # chamadas à IA não contam o avatar
    usage.reset()
    assert wallet_usd({"wallet": {"balance": 12.5}}) == 12.5 and wallet_usd({"wallet": 3}) == 3.0
    assert wallet_usd({"wallet": {"remaining_balance": "7.25"}}) == 7.25 and wallet_usd({}) is None


@pytest.mark.skipif(not HAS_FFMPEG, reason="precisa do ffmpeg")
def test_job_charges_generated_clips_not_cached_ones(tmp_path):
    from broll_bot import usage

    audio = tmp_path / "n.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=8", str(audio)], check=True)
    photo = _photo(tmp_path / "mecanico.jpg")
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=320x180:r=25:d=6",
                    "-pix_fmt", "yuv420p", str(clip)], check=True)

    class Http(FakeHTTP):
        def get(self, url, params=None, headers=None, timeout=None, stream=False):
            if "users/me" in url:
                return FakeResp(data={"data": {"wallet": {"balance": 41.2}}})
            if url.startswith("https://x/"):
                return FakeResp(content=clip.read_bytes())
            return super().get(url, params=params, headers=headers, timeout=timeout, stream=stream)

    def run_job():
        usage.reset()
        job = AvatarJob(AvatarConfig(photo=str(photo)), "k", audio, [AvatarSegment("intro", 0.0, 6.0)],
                        tmp_path / "cache", tmp_path / "work", client=HeyGen("k", session=Http()), photo=photo)
        job.client.wait = lambda vid, deadline, **kw: "https://x/v.mp4"
        job.start().result()
        return job, usage.summary()

    job, u = run_job()
    seg = job.segments[0]
    assert seg.seconds == pytest.approx(6.0, abs=0.1) and seg.usd == pytest.approx(6.0 * 0.0385, abs=0.005)
    assert u["services_usd"] == pytest.approx(1.32 + seg.usd, abs=0.001)  # + envio da imagem (uma vez)
    assert job.wallet == 41.2
    job, u = run_job()  # mesmo trecho de novo: veio do cache, não custou
    assert job.segments[0].status == "cache" and u["services_usd"] == 0 and job.wallet is None


def test_default_is_one_extra_near_the_first_minute():
    spec = [("A Chinese electric car, taken apart.", 0.2, 6.5)]
    t = 7.0
    while t < 240:
        spec.append(("That is the secret here.", t, t + 4.8))
        t += 5.0
    spec.append(("Look after your car.", 241.0, 246.0))
    checks = [g for g in pick_segments(_sentences(spec), AvatarConfig(), 247.0) if g.moment == "checkin"]
    assert len(checks) == 1 and abs(checks[0].start - 60) <= 18.5


@pytest.mark.skipif(not HAS_FFMPEG, reason="precisa do ffmpeg")
def test_rerun_reuses_every_clip_even_with_several_checkins(tmp_path):
    """Três aparições extras em paralelo: cada uma com o próprio áudio; refazer o vídeo não gera nada de novo,
    mesmo que a sincronização mude alguns centésimos."""
    audio = tmp_path / "n.mp3"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=40", "-c:a", "libmp3lame",
                    str(audio)], check=True)
    photo = _photo(tmp_path / "mecanico.jpg")
    uploads: list[bytes] = []

    class Http(FakeHTTP):
        def post(self, url, data=None, json=None, headers=None, timeout=None):
            if "upload" in url and "talking_photo" not in url:
                uploads.append(data)
            return super().post(url, data=data, json=json, headers=headers, timeout=timeout)

    def run_job(shift=0.0):
        http = Http()
        segs = [AvatarSegment("intro", 0.0, 6.0), AvatarSegment("checkin", 10.0 + shift, 15.0 + shift),
                AvatarSegment("checkin", 20.0 + shift, 26.0), AvatarSegment("checkin", 30.0, 35.5 + shift)]
        job = AvatarJob(AvatarConfig(photo=str(photo)), "k", audio, segs, tmp_path / "cache", tmp_path / "work",
                        client=HeyGen("k", session=http), photo=photo)
        job.client.wait = lambda vid, deadline, **kw: "https://x/v.mp4"
        return job.start().result(), http

    ready, http = run_job()
    assert [g.status for g in ready] == ["ok"] * 4
    assert len(set(uploads)) == 4  # cada trecho mandou o SEU áudio
    ready, http = run_job(shift=0.03)  # mesma narração, sincronização um pouco diferente
    assert [g.status for g in ready] == ["cache"] * 4 and not [c for c in http.calls if "generate" in c[1]]
    assert ready[1].start == 10.0  # usa o trecho exato do clipe guardado (boca sincronizada)


@pytest.mark.skipif(not HAS_FFMPEG, reason="precisa do ffmpeg")
def test_photo_limit_frees_old_images_and_retries(tmp_path):
    """Plano com limite de avatares de foto: a 4ª imagem é recusada; o bot apaga as que ele mesmo enviou e nenhum
    canal usa mais (testes, imagens trocadas) e tenta de novo. A imagem de outro canal em uso não é apagada."""
    audio = tmp_path / "n.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=8", str(audio)], check=True)
    jw = _photo(tmp_path / "jw.jpg", (10, 20, 30))
    bc = _photo(tmp_path / "bc.jpg", (200, 20, 30))
    from broll_bot.avatar import photo_digest

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "fotos_heygen.json").write_text(json.dumps({
        "teste1": "tp_old1", "teste2": "tp_old2", photo_digest(jw): "tp_jw"}))
    deleted: list[str] = []

    class Http(FakeHTTP):
        uploads = 0

        def post(self, url, data=None, json=None, headers=None, timeout=None):
            if "talking_photo" in url:
                Http.uploads += 1
                if len(deleted) < 2:
                    return FakeResp(status=400, data={"error": {"message":
                                    "You have exceeded your limit of 3 photo avatars. Please upgrade for more."}})
            return super().post(url, data=data, json=json, headers=headers, timeout=timeout)

        def delete(self, url, headers=None, timeout=None):
            deleted.append(url.rsplit("/", 1)[-1])
            return FakeResp(data={"error": None, "data": {}})

    job = AvatarJob(AvatarConfig(photo=str(bc)), "k", audio, [AvatarSegment("intro", 0.0, 6.0)], cache,
                    tmp_path / "work", client=HeyGen("k", session=Http()), photo=bc, channel="bernard_chevalier",
                    in_use={photo_digest(jw), photo_digest(bc)})
    job.client.wait = lambda vid, deadline, **kw: "https://x/v.mp4"
    assert [g.status for g in job.start().result()] == ["ok"]
    assert sorted(deleted) == ["tp_old1", "tp_old2"]  # a do Jhonny (em uso) fica
    known = json.loads((cache / "fotos_heygen.json").read_text())
    assert known[photo_digest(bc)] == {"id": "tp9", "channel": "bernard_chevalier"} and "teste1" not in known


def test_short_first_sentence_takes_the_next_one():
    sents = _sentences([("Les garagistes français n'en peuvent plus.", 0.0, 2.9),
                        ("Chaque semaine, ils voient revenir les mêmes voitures, avec les mêmes pannes.", 3.1, 13.6),
                        ("Corps.", 14.0, 120.0), ("Allez, prenez soin de vous.", 120.2, 124.0)])
    intro = pick_segments(sents, AvatarConfig(), 124.0)[0]
    assert intro.moment == "intro" and intro.end == pytest.approx(13.9)  # duas frases inteiras (não 3 s)


def test_short_first_line_then_long_one():
    """ "It's the first real snow of the year." (3 s) + uma frase longa: a abertura pega as duas (não fica 3 s)."""
    sents = _sentences([("It's the first real snow of the year.", 0.0, 3.0),
                        ("You're coming down the Whitemud at sixty, the light ahead goes yellow, and you brake.", 3.2, 12.6),
                        ("Corps.", 13.0, 120.0), ("Drive it like it's February.", 120.2, 124.0)])
    assert pick_segments(sents, AvatarConfig(), 124.0)[0].end == pytest.approx(12.9)


@pytest.mark.skipif(not HAS_FFMPEG, reason="precisa do ffmpeg")
def test_photo_limit_message_says_what_is_in_the_account(tmp_path):
    """A HeyGen não deixa apagar (ou não há o que apagar): o erro diz quantas fotos tem na conta, quais, e o
    motivo da HeyGen, para você saber o que apagar à mão."""
    audio = tmp_path / "n.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=8", str(audio)], check=True)
    doug = _photo(tmp_path / "doug.jpg", (90, 20, 30))
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "fotos_heygen.json").write_text(json.dumps({"velha": "tp_old"}))

    class Http(FakeHTTP):
        def post(self, url, data=None, json=None, headers=None, timeout=None):
            if "talking_photo" in url:
                return FakeResp(status=400, data={"error": {"message": "You have exceeded your limit of 3 photo "
                                                                       "avatars. Please upgrade for more."}})
            return super().post(url, data=data, json=json, headers=headers, timeout=timeout)

        def delete(self, url, headers=None, timeout=None):
            return FakeResp(status=404, data={"error": {"message": "not found"}})

        def get(self, url, params=None, headers=None, timeout=None, stream=False):
            if "avatars" in url:
                return FakeResp(data={"error": None, "data": {"avatars": [], "talking_photos": [
                    {"talking_photo_id": f"t{i}", "talking_photo_name": n} for i, n in enumerate(["Graham", "Teste", "Graham 2"])]}})
            return super().get(url, params=params, headers=headers, timeout=timeout, stream=stream)

    job = AvatarJob(AvatarConfig(photo=str(doug)), "k", audio, [AvatarSegment("intro", 0.0, 6.0)], cache,
                    tmp_path / "work", client=HeyGen("k", session=Http()), photo=doug, channel="doug",
                    in_use=set())
    assert job.start().result() == []
    err = job.segments[0].error
    assert "Fotos na conta: 3 (Graham, Teste, Graham 2)" in err and "não deixou apagar" in err and "not found" in err


def test_failed_for_lack_of_api_credit_says_so():
    """Vídeo do plug-in: "Insufficient credit. This operation requires 'api' credits." — o report diz o que fazer."""
    import time

    from broll_bot.avatar import HeyGenError

    class NoCredit(FakeHTTP):
        def get(self, url, params=None, headers=None, timeout=None, stream=False):
            return FakeResp(data={"code": 100, "data": {"status": "failed", "error": {
                "message": "Insufficient credit. This operation requires 'api' credits."}}})

    with pytest.raises(HeyGenError, match="saldo da API da HeyGen"):
        HeyGen("k", session=NoCredit()).wait("vid1", time.monotonic() + 60, poll=0, sleep=lambda s: None)


@pytest.mark.skipif(not HAS_FFMPEG, reason="precisa do ffmpeg")
def test_photo_deleted_in_heygen_is_uploaded_again(tmp_path):
    """Você apagou as fotos antigas em app.heygen.com: o ID guardado não existe mais ("avatar look not found").
    O bot sobe a imagem de novo uma vez só (mesmo com vários trechos em paralelo) e gera."""
    audio = tmp_path / "n.wav"
    # tom que sobe com o tempo: cada trecho tem um áudio diferente, como numa narração
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "aevalsrc=sin(2*PI*(200+10*t)*t):s=22050:d=20",
                    str(audio)], check=True)
    gm = _photo(tmp_path / "graham.jpg", (10, 60, 30))
    from broll_bot.avatar import photo_digest

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "fotos_heygen.json").write_text(json.dumps({photo_digest(gm): {"id": "tp_gone", "channel": "graham"}}))

    class Http(FakeHTTP):
        uploads = 0

        def post(self, url, data=None, json=None, headers=None, timeout=None):
            if "talking_photo" in url:
                Http.uploads += 1
            elif "generate" in url and "tp_gone" in str(json):
                return FakeResp(status=404, data={"error": {"message": "avatar look not found, look_id: tp_gone"}})
            return super().post(url, data=data, json=json, headers=headers, timeout=timeout)

    segs = [AvatarSegment("intro", 0.0, 6.0), AvatarSegment("cta", 8.0, 14.0), AvatarSegment("outro", 15.0, 20.0)]
    job = AvatarJob(AvatarConfig(photo=str(gm)), "k", audio, segs, cache, tmp_path / "work",
                    client=HeyGen("k", session=Http()), photo=gm, channel="graham", in_use={photo_digest(gm)})
    job.client.wait = lambda vid, deadline, **kw: "https://x/v.mp4"
    assert [g.status for g in job.start().result()] == ["ok"] * 3
    assert Http.uploads == 1
    assert json.loads((cache / "fotos_heygen.json").read_text())[photo_digest(gm)]["id"] == "tp9"


@pytest.mark.skipif(not HAS_FFMPEG, reason="precisa do ffmpeg")
def test_segments_with_same_audio_make_one_clip(tmp_path):
    """Dois trechos com o mesmo áudio dão o mesmo clipe: um gera, o outro espera e reaproveita (antes os dois
    escreviam o mesmo arquivo ao mesmo tempo e, no Windows, um saía sem avatar com "Acesso negado")."""
    audio = tmp_path / "n.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=20", str(audio)], check=True)
    gm = _photo(tmp_path / "graham.jpg", (10, 60, 30))
    http = FakeHTTP()
    segs = [AvatarSegment("intro", 0.0, 6.0), AvatarSegment("cta", 8.0, 14.0)]  # tom puro: mesmo áudio
    for _ in range(5):  # a corrida não acontece sempre: repete para pegar
        for g in segs:
            g.path, g.status, g.error = "", "pending", ""
        cache = tmp_path / f"cache{_}"
        job = AvatarJob(AvatarConfig(photo=str(gm)), "k", audio, segs, cache, tmp_path / "work",
                        client=HeyGen("k", session=http), photo=gm, channel="graham")
        job.client.wait = lambda vid, deadline, **kw: "https://x/v.mp4"
        assert sorted(g.status for g in job.start().result()) == ["cache", "ok"]
    assert sum("generate" in c[1] for c in http.calls) == 5  # um pedido por rodada, não dois


@pytest.mark.skipif(not HAS_FFMPEG, reason="precisa do ffmpeg")
def test_channel_heygen_id_is_used_and_upload_is_remembered(tmp_path):
    """Canal com o ID da HeyGen: usa direto, nunca envia a imagem (cada envio cria um personagem novo e custa
    US$ 1,32). Sem ID: envia uma vez e guarda o ID no canal; o ID sumido na HeyGen não faz enviar de novo."""
    import yaml

    from broll_bot.avatar import remember_photo_id

    audio = tmp_path / "n.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=8", str(audio)], check=True)
    gm = _photo(tmp_path / "dale.jpg", (10, 60, 90))

    class Http(FakeHTTP):
        uploads = 0

        def post(self, url, data=None, json=None, headers=None, timeout=None):
            if "talking_photo" in url:
                Http.uploads += 1
            return super().post(url, data=data, json=json, headers=headers, timeout=timeout)

    http = Http()
    job = AvatarJob(AvatarConfig(photo=str(gm), avatar_id="tp_meu", kind="photo"), "k", audio,
                    [AvatarSegment("intro", 0.0, 6.0)], tmp_path / "cache", tmp_path / "work",
                    client=HeyGen("k", session=http), photo=gm, channel="dale")
    job.client.wait = lambda vid, deadline, **kw: "https://x/v.mp4"
    assert [g.status for g in job.start().result()] == ["ok"] and Http.uploads == 0
    gen = [c for c in http.calls if "generate" in c[1]][0][2]
    assert gen["video_inputs"][0]["character"]["talking_photo_id"] == "tp_meu"
    # o bot enviou a imagem (canal sem ID): o ID fica no canal
    ch = tmp_path / "dale.yaml"
    ch.write_text(yaml.safe_dump({"canal": {"nome": "Dale"}, "avatar": {"photo": "canais/avatar/dale.jpg"}}))
    assert remember_photo_id(ch, "tp_novo", gm)
    av = yaml.safe_load(ch.read_text())["avatar"]
    assert av["avatar_id"] == "tp_novo" and av["kind"] == "photo" and av["id_photo"] == "canais/avatar/dale.jpg"
    assert not remember_photo_id(ch, "tp_outro", gm)  # já tem ID: não troca

    class Gone(Http):
        def post(self, url, data=None, json=None, headers=None, timeout=None):
            if "generate" in url:
                return FakeResp(status=404, data={"error": {"message": "avatar look not found, look_id: tp_meu"}})
            return super().post(url, data=data, json=json, headers=headers, timeout=timeout)

    Http.uploads = 0
    job = AvatarJob(AvatarConfig(photo=str(gm), avatar_id="tp_meu", kind="photo"), "k", audio,
                    [AvatarSegment("intro", 0.0, 6.0)], tmp_path / "cache2", tmp_path / "work2",
                    client=HeyGen("k", session=Gone()), photo=gm, channel="dale")
    assert job.start().result() == [] and Http.uploads == 0
    assert "não acha o avatar tp_meu" in job.segments[0].error


@pytest.mark.skipif(not HAS_FFMPEG, reason="precisa do ffmpeg")
def test_channel_id_listed_as_avatar_still_moves_with_avatar_iv(tmp_path):
    """O personagem do Dale foi escolhido na lista da HeyGen como "Avatar": ia no motor padrão e saiu só mexendo a
    boca. Com a imagem do personagem no canal, o ID é a foto falante dela: vai com Avatar IV. Se a HeyGen não
    aceitar como foto falante (avatar de vídeo de verdade), gera como avatar."""
    audio = tmp_path / "n.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=8", str(audio)], check=True)
    dale = _photo(tmp_path / "dale.jpg", (90, 60, 30))
    http = FakeHTTP()
    cfg = AvatarConfig(photo=str(dale), avatar_id="16ae584627934345a744c9ac1f5a79e1", kind="avatar")
    job = AvatarJob(cfg, "k", audio, [AvatarSegment("intro", 0.0, 6.0)], tmp_path / "c", tmp_path / "w",
                    client=HeyGen("k", session=http), photo=dale, channel="dale")
    job.client.wait = lambda vid, deadline, **kw: "https://x/v.mp4"
    ready = job.start().result()
    char = [c for c in http.calls if "generate" in c[1]][0][2]["video_inputs"][0]["character"]
    assert char == {"type": "talking_photo", "talking_photo_id": cfg.avatar_id, "use_avatar_iv_model": True}
    assert ready[0].engine == "avatar_iv"

    class NotPhoto(FakeHTTP):
        def post(self, url, data=None, json=None, headers=None, timeout=None):
            if "generate" in url and json["video_inputs"][0]["character"]["type"] == "talking_photo":
                return FakeResp(status=400, data={"error": {"message": "talking photo not found"}})
            return super().post(url, data=data, json=json, headers=headers, timeout=timeout)

    http2 = NotPhoto()
    job = AvatarJob(AvatarConfig(photo=str(dale), avatar_id="Anna_public", kind="avatar"), "k", audio,
                    [AvatarSegment("intro", 0.0, 6.0)], tmp_path / "c2", tmp_path / "w2",
                    client=HeyGen("k", session=http2), photo=dale, channel="dale")
    job.client.wait = lambda vid, deadline, **kw: "https://x/v.mp4"
    assert [g.status for g in job.start().result()] == ["ok"]
    last = [c for c in http2.calls if "generate" in c[1]][-1][2]["video_inputs"][0]["character"]
    assert last["type"] == "avatar" and last["avatar_id"] == "Anna_public"


@pytest.mark.skipif(not HAS_FFMPEG, reason="precisa do ffmpeg")
def test_second_version_of_the_same_narration_never_pays_heygen_again(tmp_path):
    """Versão 2 do vídeo do Dale: a transcrição mudou os trechos uns décimos (abertura 0-8,3 s -> 0-8,0 s), o
    corte do áudio mudou e o bot pagou os 4 clipes de novo. Agora a mesma narração usa os clipes que já tem,
    mesmo com o trecho segundos fora, e momento sem clipe fica sem avatar: nenhum pedido à HeyGen."""
    audio = tmp_path / "n.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "aevalsrc=sin(2*PI*(200+10*t)*t):s=22050:d=40",
                    str(audio)], check=True)
    gm = _photo(tmp_path / "graham.jpg", (10, 60, 30))

    def run_job(segs):
        http = FakeHTTP()
        job = AvatarJob(AvatarConfig(photo=str(gm)), "k", audio, segs, tmp_path / "cache", tmp_path / "work",
                        client=HeyGen("k", session=http), photo=gm, channel="graham")
        job.client.wait = lambda vid, deadline, **kw: "https://x/v.mp4"
        job.start()
        return job, job.result(), http

    _, ready, http = run_job([AvatarSegment("intro", 0.0, 8.3), AvatarSegment("checkin", 20.0, 25.0)])
    assert [g.status for g in ready] == ["ok", "ok"] and any("generate" in c[1] for c in http.calls)
    # versão 2: trechos mudaram (2 s de diferença, bem mais que a folga) e entrou um momento novo
    job, ready, http = run_job([AvatarSegment("intro", 0.0, 6.2), AvatarSegment("checkin", 22.0, 27.5),
                                AvatarSegment("outro", 34.0, 39.0)])
    assert not http.calls  # nem a imagem, nem o áudio, nem gerar
    assert [(g.moment, g.status, g.start, g.end) for g in job.segments] == [
        ("intro", "cache", 0.0, 8.3), ("checkin", "cache", 20.0, 25.0), ("outro", "skipped", 34.0, 39.0)]
    # quem quer refazer de propósito: avatar.regenerate
    http = FakeHTTP()
    job = AvatarJob(AvatarConfig(photo=str(gm), regenerate=True), "k", audio, [AvatarSegment("intro", 0.0, 6.2)],
                    tmp_path / "cache", tmp_path / "work", client=HeyGen("k", session=http), photo=gm, channel="graham")
    job.client.wait = lambda vid, deadline, **kw: "https://x/v.mp4"
    job.start().result()
    assert any("generate" in c[1] for c in http.calls)


@pytest.mark.skipif(not HAS_FFMPEG, reason="precisa do ffmpeg")
def test_reused_avatar_does_not_log_that_it_is_generating(tmp_path, caplog):
    """Na versão 2 do vídeo das baterias o log dizia "gerando na HeyGen" mesmo reaproveitando os clipes (a frase
    saía antes da checagem) e parecia que o bot estava pagando de novo."""
    import logging

    audio = tmp_path / "n.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "aevalsrc=sin(2*PI*(200+10*t)*t):s=22050:d=20",
                    str(audio)], check=True)
    gm = _photo(tmp_path / "graham.jpg", (10, 60, 30))

    def run_job():
        job = AvatarJob(AvatarConfig(photo=str(gm)), "k", audio, [AvatarSegment("intro", 0.0, 6.0)], tmp_path / "cache",
                        tmp_path / "work", client=HeyGen("k", session=FakeHTTP()), photo=gm, channel="graham")
        job.client.wait = lambda vid, deadline, **kw: "https://x/v.mp4"
        job.start().result()

    with caplog.at_level(logging.INFO, logger="broll_bot.avatar"):
        run_job()
        run_job()
    msgs = [r.getMessage() for r in caplog.records]
    assert sum("gerando na HeyGen" in m for m in msgs) == 1  # só a primeira rodada gera
    assert any("reaproveitando os clipes" in m for m in msgs)
