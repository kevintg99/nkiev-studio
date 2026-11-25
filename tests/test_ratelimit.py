from broll_bot.ratelimit import CircuitBreaker, RetryPolicy, TokenBucket


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def test_token_bucket_burst_then_waits():
    c = Clock()
    bucket = TokenBucket(rate_per_second=1.0, capacity=2, clock=c, sleep=c.sleep)
    assert bucket.acquire(max_wait=0) and bucket.acquire(max_wait=0)
    assert not bucket.acquire(max_wait=0.5)  # precisaria esperar ~1s
    assert bucket.acquire(max_wait=2)
    assert 0.9 <= c.t <= 1.1


def test_token_bucket_respects_block_until():
    c = Clock()
    bucket = TokenBucket(rate_per_second=100, capacity=5, clock=c, sleep=c.sleep)
    bucket.block_until(30.0)
    assert not bucket.acquire(max_wait=10)  # não trava o pipeline: devolve False
    assert bucket.acquire(max_wait=60)
    assert c.t >= 30.0


def test_retry_delay_grows_and_is_capped():
    policy = RetryPolicy(max_retries=5, base_delay=1.0, max_delay=8.0)
    import random

    rng = random.Random(0)
    delays = [policy.delay(i, rng) for i in range(6)]
    assert 0.5 <= delays[0] <= 1.0
    assert all(d <= 8.0 for d in delays)
    assert delays[3] >= 4.0


def test_circuit_breaker_opens_and_half_opens():
    c = Clock()
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=60, clock=c)
    cb.record_failure()
    assert not cb.is_open
    cb.record_failure()
    assert cb.is_open
    c.t += 61
    assert not cb.is_open  # meia-abertura
    cb.record_failure()  # falhou de novo -> reabre
    assert cb.is_open
    c.t += 61
    cb.record_success()
    assert not cb.is_open


def test_bucket_counts_the_time_spent_waiting():
    clock = Clock()
    bucket = TokenBucket(1.0, 1, clock=clock, sleep=clock.sleep)
    assert bucket.acquire(5) and bucket.waited == 0
    assert bucket.acquire(5) and abs(bucket.waited - 1.0) < 1e-9


def test_source_that_keeps_failing_pauses_longer_each_time():
    """O Openverse deu timeout o vídeo inteiro: reabria a cada 5 min e cada cena esperava ~1 min ele cair
    (97 timeouts, ~40 min jogados fora). Agora a pausa dobra a cada recaída e zera quando ele responde."""
    c = Clock()
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=300, clock=c)
    for _ in range(3):
        cb.record_failure()
    pauses = []
    for _ in range(5):  # volta (meia-abertura), falha de novo, fica fora mais tempo
        start = c.t
        while cb.is_open:
            c.t += 10
        pauses.append(c.t - start)
        cb.record_failure()
    assert pauses == [300, 600, 1200, 2400, 2400]  # teto: 8x
    c.t += 2400
    assert not cb.is_open
    cb.record_success()  # voltou a responder: a próxima queda começa de 5 min de novo
    for _ in range(3):
        cb.record_failure()
    c.t += 300
    assert not cb.is_open


def test_rate_limited_source_that_keeps_refusing_pauses_longer():
    """O YouTube deu 429 o vídeo polonês inteiro e era tentado de novo a cada minuto (40 tentativas)."""
    c = Clock()
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=300, clock=c)
    pauses = []
    for _ in range(4):
        cb.trip(60.0)
        start = c.t
        while cb.is_open:
            c.t += 10
        pauses.append(c.t - start)
    assert pauses == [60, 120, 240, 480]
    cb.record_success()
    cb.trip(60.0)
    c.t += 60
    assert not cb.is_open  # respondeu no meio: volta a ser 1 min


def test_pexels_spends_what_is_left_of_the_hour_counting_earlier_runs(tmp_path):
    # vídeo da garantia Toyota: o Pexels (200/h) ficava em 3 por minuto depois de 40 buscas e o vídeo esperou 9 min
    # pela fila com só 125 buscas nele; o cabeçalho do Pexels só conta a cota do mês
    from broll_bot.ratelimit import HourLedger, hourly_bucket

    wall = Clock()
    wall.t = 10_000.0
    path = tmp_path / "ratelimit_pexels.json"
    first = hourly_bucket(200, HourLedger(path, clock=wall))
    assert all(first.acquire(max_wait=0) for _ in range(150))  # a rodada inteira sem esperar a fila
    # rodada seguinte, 10 min depois: só sobra o resto da hora (190 de folga - 150 gastas)
    wall.t += 600
    again = HourLedger(path, clock=wall)
    assert again.used() == 150
    second = hourly_bucket(200, again)
    assert sum(second.acquire(max_wait=0) for _ in range(100)) == 40
    # uma hora depois das primeiras, a cota volta inteira
    wall.t += 3600
    assert HourLedger(path, clock=wall).used() == 0


def test_bad_ledger_file_does_not_stop_the_search(tmp_path):
    from broll_bot.ratelimit import HourLedger

    path = tmp_path / "ratelimit_pexels.json"
    path.write_text("{quebrado", encoding="utf-8")
    assert HourLedger(path).used() == 0


def test_build_providers_gives_pexels_the_hourly_quota(tmp_path):
    from broll_bot.config import AppConfig
    from broll_bot.providers import build_providers

    cfg = AppConfig()
    cfg.pexels_api_key = "x"
    cfg.search.provider_order = ["pexels"]
    limiter = build_providers(cfg, tmp_path)["pexels"].http.limiter
    assert limiter.capacity == 190 and limiter.wait_time() == 0
