"""Cliente mínimo da API da OpenAI (ChatGPT) para respostas em JSON.

Usado como alternativa à API da Anthropic no plano de cenas e na revisão visual
(``analysis.provider: openai`` / ``--ai openai``). A chave vem de ``OPENAI_API_KEY``
no ``.env``. O modelo é configurável (``analysis.openai_model``/``review.openai_model``)
porque a lista de modelos disponíveis muda com o tempo e depende da conta.
"""

from __future__ import annotations

import base64
import io
import json
from typing import Any

from PIL import Image

from .log import get_logger

log = get_logger("llm")

# modelos de raciocínio aceitam "reasoning_effort" (low/medium/high)
REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


class OpenAIError(RuntimeError):
    pass


def image_part(img: Image.Image, quality: int = 85) -> dict[str, Any]:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    data = base64.b64encode(buf.getvalue()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}", "detail": "high"}}


def text_part(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


class OpenAIJSON:
    """Pede uma resposta em JSON (modo ``json_object``) e devolve o objeto já decodificado."""

    def __init__(self, model: str, effort: str = "medium", max_tokens: int = 16000, client: Any = None,
                 step: str = "", timeout: float | None = None) -> None:
        self.step = step  # etapa para a conta de custo (usage.py)
        if client is None:
            import openai  # import tardio: só é necessário neste modo

            # lê OPENAI_API_KEY do ambiente; ``timeout``: chamada curta que não pode ficar pendurada (o padrão da
            # biblioteca espera 10 min e tenta de novo 2 vezes)
            client = openai.OpenAI(timeout=timeout, max_retries=1) if timeout else openai.OpenAI()
        self.client = client
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.tokens_in = 0
        self.tokens_out = 0

    def complete(self, system: str, content: str | list[dict[str, Any]]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
            "max_completion_tokens": self.max_tokens,
        }
        if self.model.startswith(REASONING_PREFIXES) and self.effort in ("minimal", "low", "medium", "high"):
            kwargs["reasoning_effort"] = self.effort
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - erro da API vira mensagem clara
            raise OpenAIError(f"falha na API da OpenAI ({self.model}): {exc}") from exc
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self.tokens_in += int(getattr(usage, "prompt_tokens", 0) or 0)
            self.tokens_out += int(getattr(usage, "completion_tokens", 0) or 0)
            from . import usage as usage_log

            usage_log.record("openai", self.model, int(getattr(usage, "prompt_tokens", 0) or 0),
                             int(getattr(usage, "completion_tokens", 0) or 0), self.step)
            log.info("OpenAI respondeu", extra={"stage": "llm", "model": self.model,
                                                "tokens_entrada": getattr(usage, "prompt_tokens", None),
                                                "tokens_saida": getattr(usage, "completion_tokens", None)})
        choice = resp.choices[0]
        if getattr(choice, "finish_reason", "") == "length":
            raise OpenAIError("resposta cortada pelo limite de tokens (aumente max_tokens)")
        text = (choice.message.content or "").strip()
        if not text:
            refusal = getattr(choice.message, "refusal", None)
            raise OpenAIError(f"resposta vazia da OpenAI{': ' + refusal if refusal else ''}")
        start, end = text.find("{"), text.rfind("}")
        try:
            return json.loads(text[start:end + 1])
        except ValueError as exc:
            raise OpenAIError("a OpenAI não devolveu um JSON válido") from exc
