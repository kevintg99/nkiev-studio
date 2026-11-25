"""Controle de taxa, retry com backoff e circuit breaker por fonte."""

from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class TokenBucket:
    """Token bucket thread-safe.

    ``acquire`` nunca espera mais que ``max_wait``: se a espera necessária
    for maior, devolve ``False`` e o chamador pode partir para outra fonte
    em vez de travar o pipeline.
    """

    def __init__(
        self,
        rate_per_second: float,
        capacity: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        tokens: float | None = None,
        on_acquire: Callable[[], None] | None = None,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second precisa ser > 0")
        self.rate = rate_per_second
        self.capacity = max(1, capacity)
        self._tokens = float(self.capacity if tokens is None else max(0.0, min(tokens, self.capacity)))
        self._on_acquire = on_acquire
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._blocked_until = 0.0
        self.waited = 0.0  # segundos que os pedidos já passaram esperando a vez (no vídeo todo)
        self._lock = threading.Lock()

    def _refill(self, now: float) -> None:
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
        self._last = now

    def block_until(self, monotonic_deadline: float) -> None:
        """Bloqueia a fonte até um instante (ex.: header X-RateLimit-Reset)."""
        with self._lock:
            self._blocked_until = max(self._blocked_until, monotonic_deadline)
            self._tokens = 0.0

    def wait_time(self) -> float:
        with self._lock:
            now = self._clock()
            self._refill(now)
            blocked = max(0.0, self._blocked_until - now)
            deficit = 0.0 if self._tokens >= 1 else (1 - self._tokens) / self.rate
            return max(blocked, deficit)

    def acquire(self, max_wait: float) -> bool:
        while True:
            with self._lock:
                now = self._clock()
                self._refill(now)
                got = now >= self._blocked_until and self._tokens >= 1
                if got:
                    self._tokens -= 1
                else:
                    wait = max(self._blocked_until - now, (1 - self._tokens) / self.rate)
            if got:
                if self._on_acquire is not None:
                    self._on_acquire()
                return True
            if wait > max_wait:
                return False
            self._sleep(wait)
            with self._lock:
                self.waited += wait
            max_wait -= wait


class HourLedger:
    """Pedidos da última hora a um banco, gravados em arquivo para valer entre as rodadas. O Pexels aceita 200 por
    hora, mas o cabeçalho X-RateLimit dele conta a cota do mês: sem saber quanto da hora já foi gasto, o bot
    segurava 3 por minuto e o vídeo da garantia Toyota esperou 9 min pela fila com só 125 buscas no Pexels."""

    WINDOW = 3600.0

    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()
        try:
            stamps = json.loads(path.read_text(encoding="utf-8"))
            self._stamps = [float(t) for t in stamps if isinstance(t, (int, float))]
        except (OSError, ValueError, TypeError):
            self._stamps = []

    def _prune(self) -> None:
        cut = self._clock() - self.WINDOW
        self._stamps = [t for t in self._stamps if t > cut]

    def used(self) -> int:
        with self._lock:
            self._prune()
            return len(self._stamps)

    def record(self) -> None:
        with self._lock:
            self._prune()
            self._stamps.append(self._clock())
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps([round(t, 1) for t in self._stamps]), encoding="utf-8")
            except OSError:
                pass  # sem gravar, a próxima rodada só fica mais folgada do que devia


def hourly_bucket(per_hour: int, ledger: HourLedger, margin: int = 10) -> TokenBucket:
    """Rajada até o que sobrou da cota da hora (menos uma folga), reposta no ritmo da cota."""
    cap = max(1, per_hour - margin)
    return TokenBucket(rate_per_second=per_hour / HourLedger.WINDOW, capacity=cap, tokens=cap - ledger.used(),
                       on_acquire=ledger.record)


@dataclass
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 20.0

    def delay(self, attempt: int, rng: random.Random | None = None) -> float:
        """Backoff exponencial com *full jitter* (attempt começa em 0)."""
        rng = rng or random
        cap = min(self.max_delay, self.base_delay * (2**attempt))
        return rng.uniform(cap / 2, cap)


class CircuitBreaker:
    """Desativa uma fonte temporariamente depois de N falhas seguidas. A cada vez que ela volta e cai de novo
    sem nenhum acerto, a pausa dobra (5, 10, 20, 40 min): o Openverse dando timeout o vídeo inteiro reabria a
    cada 5 min, e cada cena esperava ~1 min ele cair (30 vezes, ~40 min de busca jogados fora)."""

    MAX_FACTOR = 8  # teto da pausa: 8x o cooldown

    def __init__(
        self, failure_threshold: int = 3, cooldown_seconds: float = 300.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown = cooldown_seconds
        self._clock = clock
        self._failures = 0
        self._streak = 0  # aberturas seguidas sem nenhum acerto no meio
        self._opened_at: float | None = None
        self._open_for = cooldown_seconds
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return False
            if self._clock() - self._opened_at >= self._open_for:
                # meia-abertura: deixa uma tentativa passar
                self._opened_at = None
                self._failures = self.failure_threshold - 1
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._streak = 0
            self._opened_at = None

    def _open(self) -> None:
        self._streak += 1
        self._open_for = self.cooldown * min(self.MAX_FACTOR, 2 ** (self._streak - 1))
        self._opened_at = self._clock()

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._open()

    def trip(self, seconds: float | None = None) -> None:
        """Abre imediatamente (ex.: chave inválida, cota esgotada). ``seconds``: pausa mais curta que o
        padrão (bloqueio temporário por excesso de pedidos). Ela também dobra se a fonte continua barrando
        (1, 2, 4, 8 min...): o YouTube deu 429 o vídeo polonês inteiro e era tentado de novo a cada minuto."""
        with self._lock:
            self._failures = self.failure_threshold
            if seconds is None:
                self._open()
            else:
                self._streak += 1
                self._opened_at = self._clock()
                self._open_for = min(min(seconds, self.cooldown) * 2 ** (self._streak - 1),
                                     self.cooldown * self.MAX_FACTOR)
