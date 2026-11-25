"""Quanto cada vídeo gastou de IA: tokens de cada chamada (plano de cenas, revisão, tradução das buscas,
descrição) e a estimativa em dólar pela tabela de preços.

É uma estimativa: o valor que vale é o do painel da Anthropic/OpenAI. Os preços (US$ por 1 milhão de
tokens) podem ser ajustados no ``config.yaml`` (``costs.prices``) quando a empresa mudar a tabela.
Plano de cenas que veio do cache não chama a IA e não custa nada.
"""

from __future__ import annotations

import threading
from typing import Any

# US$ por 1M de tokens: (entrada, saída). Leitura de cache da Anthropic: 10% da entrada.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0), "claude-opus-5": (5.0, 25.0), "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0), "claude-haiku-4-5": (1.0, 5.0), "claude-fable-5-1": (10.0, 50.0),
    "gpt-5-mini": (0.25, 2.0), "gpt-5": (1.25, 10.0), "gpt-5-nano": (0.05, 0.4),
}

_lock = threading.Lock()
_calls: list[dict[str, Any]] = []
_charges: list[dict[str, Any]] = []  # serviços cobrados por uso, não por token (avatar da HeyGen)


def reset() -> None:
    with _lock:
        _calls.clear()
        _charges.clear()


def charge(step: str, usd: float, model: str, detail: str = "", seconds: float = 0.0) -> None:
    """Gasto de um serviço cobrado por segundo/chamada (ex.: avatar da HeyGen), já em dólar."""
    with _lock:
        _charges.append({"step": step, "usd": float(usd), "model": model, "detail": detail,
                         "seconds": float(seconds)})


def record(provider: str, model: str, input_tokens: int, output_tokens: int, step: str = "",
           cache_read: int = 0, cache_write: int = 0) -> None:
    with _lock:
        _calls.append({"provider": provider, "model": model, "step": step or "outros",
                       "input": int(input_tokens or 0), "output": int(output_tokens or 0),
                       "cache_read": int(cache_read or 0), "cache_write": int(cache_write or 0)})


def from_anthropic(response: Any, model: str, step: str) -> None:
    """Lê ``response.usage`` de uma resposta da Anthropic (create ou parse)."""
    u = getattr(response, "usage", None)
    if u is None:
        return
    record("anthropic", model, getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0, step,
           getattr(u, "cache_read_input_tokens", 0) or 0, getattr(u, "cache_creation_input_tokens", 0) or 0)


def price_of(model: str, prices: dict[str, Any] | None = None) -> tuple[float, float] | None:
    table = {**PRICES, **{k: tuple(v) for k, v in (prices or {}).items()}}
    if model in table:
        return table[model]  # type: ignore[return-value]
    base = next((k for k in sorted(table, key=len, reverse=True) if model.startswith(k)), None)
    return table[base] if base else None  # type: ignore[return-value]


def cost(call: dict[str, Any], prices: dict[str, Any] | None = None) -> float | None:
    p = price_of(call["model"], prices)
    if p is None:
        return None
    pin, pout = p
    tokens_in = call["input"] + call.get("cache_read", 0) * 0.1 + call.get("cache_write", 0) * 1.25
    return (tokens_in * pin + call["output"] * pout) / 1e6


def summarize(calls: list[dict[str, Any]], prices: dict[str, Any] | None = None,
              charges: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    steps: dict[str, dict[str, Any]] = {}
    total, unpriced = 0.0, set()
    for ch in charges or []:
        s = steps.setdefault(ch["step"], {"calls": 0, "input": 0, "output": 0, "usd": 0.0, "model": ch["model"],
                                          "seconds": 0.0, "detail": ""})
        s["calls"] += 1
        s["usd"] += ch["usd"]
        s["seconds"] = round(s.get("seconds", 0.0) + ch.get("seconds", 0.0), 1)
        s["detail"] = "; ".join(x for x in (s.get("detail"), ch.get("detail")) if x)
        total += ch["usd"]
    for c in calls:
        s = steps.setdefault(c["step"], {"calls": 0, "input": 0, "output": 0, "usd": 0.0, "model": c["model"]})
        s["calls"] += 1
        s["input"] += c["input"] + c.get("cache_read", 0) + c.get("cache_write", 0)
        s["output"] += c["output"]
        value = cost(c, prices)
        if value is None:
            unpriced.add(c["model"])
        else:
            s["usd"] += value
            total += value
    for s in steps.values():
        s["usd"] = round(s["usd"], 4)
    return {"usd": round(total, 4), "calls": len(calls), "steps": steps,
            "services_usd": round(sum(ch["usd"] for ch in charges or []), 4),
            "input": sum(s["input"] for s in steps.values()), "output": sum(s["output"] for s in steps.values()),
            "unpriced": sorted(unpriced)}


def summary(prices: dict[str, Any] | None = None) -> dict[str, Any]:
    with _lock:
        calls, charges = list(_calls), list(_charges)
    return summarize(calls, prices, charges)
