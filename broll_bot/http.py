"""Cliente HTTP com rate limit, retry/backoff e leitura de headers X-RateLimit-*."""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import requests

from .log import get_logger
from .errors import AuthError, ProviderError, RateLimitedError, UnreachableError
from .ratelimit import RetryPolicy, TokenBucket

log = get_logger("http")

USER_AGENT = "broll-bot/1.0 (+automacao de edicao de video)"


def user_agent(contact: str | None = None) -> str:
    """User-Agent com contato (a Wikimedia exige saber quem está consultando)."""
    return f"broll-bot/1.0 ({contact}; automacao de edicao de video)" if contact else USER_AGENT


class HttpClient:
    def __init__(
        self,
        name: str,
        limiter: TokenBucket,
        retry: RetryPolicy,
        max_wait_seconds: float = 20.0,
        timeout: float = 20.0,
        headers: dict[str, str] | None = None,
        session: requests.Session | None = None,
        min_interval: float = 0.0,
    ) -> None:
        self.name = name
        self.limiter = limiter
        self.retry = retry
        self.max_wait = max_wait_seconds
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, **(headers or {})})
        self.requests_made = 0
        self.ok_responses = 0
        self.min_interval = min_interval
        self._last_at = 0.0
        self._gap_lock = threading.Lock()  # respostas boas: com elas, um 403 depois é bloqueio por excesso, não chave errada

    # ------------------------------------------------------------------ API
    def get_json(self, url: str, params: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.retry.max_retries + 1):
            if not self.limiter.acquire(self.max_wait):
                raise RateLimitedError(f"{self.name}: limite local atingido (espera > {self.max_wait}s)")
            if self.min_interval > 0:  # vários workers ao mesmo tempo: um pedido por vez, espaçados
                with self._gap_lock:
                    wait = self._last_at + self.min_interval - time.monotonic()
                    if wait > 0:
                        time.sleep(wait)
                    self._last_at = time.monotonic()
            try:
                self.requests_made += 1
                resp = self.session.get(url, params=params, timeout=self.timeout, headers=headers)
            except (requests.ConnectionError, requests.Timeout) as exc:
                # rede/DNS/proxy fora, ou o banco parou de responder: uma nova tentativa só; depois a fonte pausa (o
                # disjuntor dobra a pausa a cada queda). Antes o tempo esgotado tentava 3 vezes de 20 s em toda busca:
                # o Openverse fez isso 41 vezes no vídeo da Operação Greif (~14 min parados, 0 imagens usadas)
                if attempt >= 1:
                    why = "não respondeu" if isinstance(exc, requests.Timeout) else "sem conexão"
                    raise UnreachableError(f"{self.name}: {why} ({type(exc).__name__})") from exc
                last_error = exc
                self._sleep_backoff(attempt, reason=type(exc).__name__)
                continue
            except requests.RequestException as exc:
                last_error = exc
                self._sleep_backoff(attempt, reason=type(exc).__name__)
                continue

            self._apply_rate_headers(resp)

            if resp.status_code == 403 and self.ok_responses > 0:
                # a chave já funcionou neste vídeo: o banco (Pexels) bloqueou por excesso de pedidos seguidos.
                # Espera e tenta de novo, em vez de desligar a fonte para o vídeo inteiro
                last_error = RateLimitedError(f"{self.name}: bloqueio temporário (403) por excesso de pedidos")
                self._sleep_backoff(attempt, reason="403 temporário", minimum=15.0 * (attempt + 1))
                continue
            if resp.status_code in (401, 403):
                raise AuthError(f"{self.name}: acesso negado ({resp.status_code}) — verifique a chave da API")
            if resp.status_code == 429:
                wait = self._retry_after(resp)
                last_error = RateLimitedError(f"{self.name}: HTTP 429")
                if wait is not None and wait > self.max_wait:
                    raise RateLimitedError(f"{self.name}: cota esgotada, reset em {wait:.0f}s")
                self._sleep_backoff(attempt, reason="429", minimum=wait or 0.0)
                continue
            if resp.status_code >= 500:
                last_error = ProviderError(f"{self.name}: HTTP {resp.status_code}")
                self._sleep_backoff(attempt, reason=str(resp.status_code))
                continue
            if resp.status_code >= 400:
                raise ProviderError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:200]}")
            try:
                data = resp.json()
            except ValueError as exc:
                raise ProviderError(f"{self.name}: resposta não é JSON") from exc
            self.ok_responses += 1
            return data
        if isinstance(last_error, RateLimitedError):
            raise last_error
        raise ProviderError(f"{self.name}: falhou após {self.retry.max_retries + 1} tentativas: {last_error}")

    def download(self, url: str, dest: Path, max_bytes: int = 600 * 1024 * 1024) -> Path:
        """Baixa em arquivo temporário e renomeia no fim (downloads atômicos)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        last_error: Exception | None = None
        for attempt in range(self.retry.max_retries + 1):
            fd, tmp_name = tempfile.mkstemp(dir=dest.parent, suffix=".part")
            os.close(fd)
            tmp = Path(tmp_name)
            try:
                with self.session.get(url, stream=True, timeout=(self.timeout, self.timeout * 3)) as resp:
                    if resp.status_code == 429 or resp.status_code >= 500:
                        wait = self._retry_after(resp) if resp.status_code == 429 else None
                        raise ProviderError(f"HTTP {resp.status_code}", wait)
                    if resp.status_code >= 400:
                        raise AuthError(f"download negado: HTTP {resp.status_code}")
                    written = 0
                    with open(tmp, "wb") as fh:
                        for chunk in resp.iter_content(chunk_size=1024 * 256):
                            written += len(chunk)
                            if written > max_bytes:
                                raise ProviderError("arquivo excede o tamanho máximo permitido")
                            fh.write(chunk)
                if written == 0:
                    raise ProviderError("download vazio")
                shutil.move(str(tmp), dest)
                return dest
            except AuthError:
                tmp.unlink(missing_ok=True)
                raise
            except (requests.RequestException, ProviderError) as exc:
                tmp.unlink(missing_ok=True)
                last_error = exc
                # 429 com Retry-After (Flickr, Openverse): espera o que o site pediu (até 30 s) antes de tentar de novo
                wait = exc.args[1] if isinstance(exc, ProviderError) and len(exc.args) > 1 else None
                self._sleep_backoff(attempt, reason=f"download: {exc.args[0] if exc.args else exc}",
                                    minimum=min(30.0, float(wait or 0.0)))
        msg = last_error.args[0] if isinstance(last_error, ProviderError) and last_error.args else last_error
        raise ProviderError(f"{self.name}: download falhou: {msg}")

    def download_head(self, url: str, dest: Path, seconds: float) -> Path:
        """Baixa só os primeiros ``seconds`` do vídeo, sem o som (o FFmpeg lê o arquivo remoto por partes e para
        ali). O b-roll usa no máximo uns 16 s de cada clipe e entra mudo: no vídeo dos rios os 239 arquivos inteiros
        (vídeos de 60–90 s em 1080p) somaram 7,6 GB e 10 min de download."""
        return self._ffmpeg_fetch(url, dest, ["-t", f"{seconds:.2f}"], ["-c", "copy"])

    def download_section(self, url: str, dest: Path, start: float, seconds: float) -> Path:
        """Baixa só o trecho ``[start, start + seconds]`` do vídeo remoto, sem som (filme de época de 20 min do
        Internet Archive: o b-roll usa 8 s). Recodifica para o corte cair no quadro certo (o filme é pequeno, 640 px)."""
        return self._ffmpeg_fetch(url, dest, ["-ss", f"{max(0.0, start):.2f}", "-t", f"{seconds:.2f}"],
                                  ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"])

    def _ffmpeg_fetch(self, url: str, dest: Path, before: list[str], codec: list[str]) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=dest.parent, suffix=".part.mp4")
        os.close(fd)
        tmp = Path(tmp_name)
        cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-rw_timeout", str(int(self.timeout * 3 * 1e6)),
               *before, "-i", url, "-map", "0:v:0", *codec, "-movflags", "+faststart", str(tmp)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=max(120.0, self.timeout * 10))
            if proc.returncode != 0 or tmp.stat().st_size == 0:
                raise ProviderError(f"{self.name}: trecho do vídeo não baixou: {proc.stderr.strip()[-200:]}")
            shutil.move(str(tmp), dest)
            return dest
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProviderError(f"{self.name}: trecho do vídeo não baixou: {exc}") from exc
        finally:
            tmp.unlink(missing_ok=True)

    def download_video(self, url: str, dest: Path, duration: float, head_seconds: float) -> Path:
        """Vídeo longo: só o começo (``download_head``); curto, ou se o trecho falhar, o arquivo inteiro."""
        if head_seconds > 0 and duration > head_seconds + 6:
            try:
                return self.download_head(url, dest, head_seconds)
            except ProviderError as exc:
                log.info("trecho do vídeo falhou; baixando inteiro", extra={"stage": "download", "source": self.name,
                                                                           "error": str(exc)[-150:]})
        return self.download(url, dest)

    # ------------------------------------------------------------- helpers
    def _sleep_backoff(self, attempt: int, reason: str, minimum: float = 0.0) -> None:
        if attempt >= self.retry.max_retries:
            return
        delay = max(minimum, self.retry.delay(attempt, random))
        log.info("retry com backoff", extra={"stage": "http", "source": self.name, "reason": reason, "delay": round(delay, 2)})
        time.sleep(delay)

    @staticmethod
    def _header(resp: requests.Response, name: str) -> float | None:
        value = resp.headers.get(name)
        try:
            return float(value) if value is not None else None
        except ValueError:
            return None

    def _reset_in(self, resp: requests.Response) -> float | None:
        reset = self._header(resp, "X-RateLimit-Reset")
        if reset is None:
            return None
        # Pexels manda timestamp UNIX; Pixabay manda segundos até o reset.
        return max(0.0, reset - time.time()) if reset > 1e9 else reset

    def _retry_after(self, resp: requests.Response) -> float | None:
        retry_after = self._header(resp, "Retry-After")
        return retry_after if retry_after is not None else self._reset_in(resp)

    def _apply_rate_headers(self, resp: requests.Response) -> None:
        remaining = self._header(resp, "X-RateLimit-Remaining")
        if remaining is not None and remaining <= 0:
            reset_in = self._reset_in(resp) or 60.0
            self.limiter.block_until(time.monotonic() + reset_in)
            log.warning(
                "cota da fonte zerada; pausando",
                extra={"stage": "http", "source": self.name, "reset_in_s": round(reset_in, 1)},
            )
