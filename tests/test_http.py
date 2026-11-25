"""HttpClient: retry, 429, headers de rate limit e erro de conexão (sem rede real)."""

import pytest
import requests

from broll_bot import http as http_mod
from broll_bot.errors import AuthError, RateLimitedError, UnreachableError
from broll_bot.http import HttpClient
from broll_bot.ratelimit import RetryPolicy, TokenBucket


class Resp:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})
        self.text = ""

    def json(self):
        return self._payload


class Session(requests.Session):
    def __init__(self, script):
        super().__init__()
        self.script = list(script)
        self.calls = 0

    def get(self, *a, **k):  # type: ignore[override]
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(http_mod.time, "sleep", lambda s: None)


def client(script, retries=3):
    return HttpClient("t", TokenBucket(1000, 100), RetryPolicy(max_retries=retries), session=Session(script))


def test_retries_5xx_then_succeeds():
    c = client([Resp(503), Resp(500), Resp(200, {"ok": 1})])
    assert c.get_json("https://x") == {"ok": 1}
    assert c.session.calls == 3


def test_429_with_long_reset_gives_up_fast():
    c = client([Resp(429, headers={"X-RateLimit-Reset": "3600"})])
    with pytest.raises(RateLimitedError):
        c.get_json("https://x")
    assert c.session.calls == 1


def test_401_is_not_retried():
    c = client([Resp(401)])
    with pytest.raises(AuthError):
        c.get_json("https://x")
    assert c.session.calls == 1


def test_connection_error_retried_once_then_unreachable():
    c = client([requests.ConnectionError("proxy"), requests.ConnectionError("proxy")])
    with pytest.raises(UnreachableError):
        c.get_json("https://x")
    assert c.session.calls == 2


def test_remaining_zero_blocks_limiter():
    c = client([Resp(200, {"a": 1}, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "120"})])
    c.get_json("https://x")
    assert c.limiter.wait_time() > 100
    assert not c.limiter.acquire(max_wait=1)


class StreamResp(Resp):
    def __init__(self, status=200, body=b"", headers=None):
        super().__init__(status, headers=headers)
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, chunk_size=1):
        yield self.body


def test_download_waits_what_the_site_asks_on_429(tmp_path, monkeypatch):
    """Flickr/Openverse respondem 429 com Retry-After: o download espera e tenta de novo (antes desistia rápido)."""
    waits = []
    monkeypatch.setattr(http_mod.time, "sleep", waits.append)
    c = client([StreamResp(429, headers={"Retry-After": "7"}), StreamResp(200, b"jpegdata")])
    out = c.download("https://live.staticflickr.com/x.jpg", tmp_path / "a.jpg")
    assert out.read_bytes() == b"jpegdata" and waits and waits[0] >= 7


def test_download_error_message_stays_readable(tmp_path):
    c = client([StreamResp(429), StreamResp(429)], retries=1)
    with pytest.raises(Exception) as err:
        c.download("https://x/y.jpg", tmp_path / "b.jpg")
    assert str(err.value) == "t: download falhou: HTTP 429"


def _client():
    return HttpClient("pexels", TokenBucket(100, 100), RetryPolicy(max_retries=0), timeout=5)


def test_long_stock_video_downloads_only_the_head_without_sound(tmp_path):
    """Vídeo dos rios: 239 vídeos inteiros de 60–90 s em 1080p = 7,6 GB e 10 min de download para usar ~16 s de
    cada um. Agora só os primeiros segundos, sem o som (o b-roll entra mudo)."""
    import subprocess

    from broll_bot.media import probe

    src = tmp_path / "long.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=40",
                    "-f", "lavfi", "-i", "sine=duration=40", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", str(src)], check=True)
    dest = _client().download_video(str(src), tmp_path / "pexels_video_1.mp4", duration=40.0, head_seconds=12.0)
    info = probe(dest)
    assert 11.0 <= info.duration <= 13.0 and info.width == 320
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                              str(dest)], capture_output=True, text=True).stdout.split()
    assert streams == ["video"]
    assert not list(tmp_path.glob("*.part*"))  # nada de arquivo temporário largado


def test_short_video_or_failed_head_downloads_the_whole_file(tmp_path, monkeypatch):
    client = _client()
    whole, heads = [], []
    monkeypatch.setattr(client, "download", lambda url, dest, **k: whole.append(url) or dest)

    def broken_head(url, dest, seconds):
        heads.append(url)
        raise http_mod.ProviderError("servidor não aceita leitura por partes")

    monkeypatch.setattr(client, "download_head", broken_head)
    client.download_video("https://x/curto.mp4", tmp_path / "a.mp4", duration=20.0, head_seconds=24.0)
    client.download_video("https://x/longo.mp4", tmp_path / "b.mp4", duration=60.0, head_seconds=24.0)
    client.download_video("https://x/desligado.mp4", tmp_path / "c.mp4", duration=60.0, head_seconds=0.0)
    assert heads == ["https://x/longo.mp4"]  # curto e desligado nem tentam o trecho
    assert whole == ["https://x/curto.mp4", "https://x/longo.mp4", "https://x/desligado.mp4"]


def test_stock_providers_get_the_head_setting_from_config(tmp_path):
    from broll_bot.config import AppConfig
    from broll_bot.providers import build_providers

    cfg = AppConfig()
    cfg.pexels_api_key = cfg.pixabay_api_key = "k"
    cfg.search.provider_order = ["pexels", "pixabay"]
    provs = build_providers(cfg, tmp_path)
    assert provs["pexels"].head_seconds == provs["pixabay"].head_seconds == cfg.search.video_head_seconds > 16
