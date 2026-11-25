"""Aba "Tags e descrição": descrição com capítulos, tags e sugestões de título, pela IA já configurada.

Os capítulos saem do relatório (sem IA); a IA escreve o resto no idioma do canal. Custa centavos.
"""

from __future__ import annotations

import json
import re
from typing import Any

from broll_bot.config import AppConfig

LANG_NAMES = {"en": "British English", "fr": "French (France)", "pt": "Brazilian Portuguese", "pl": "Polish",
              "es": "Spanish"}

SYSTEM = (
    "You write YouTube metadata for a faceless channel. Reply with JSON only: "
    '{"description": "...", "tags": ["..."], "titles": ["...", "...", "..."]}. '
    "description: 2 short paragraphs that hook the viewer, no hashtags, no emojis, no chapter list "
    "(chapters are added separately). tags: 12 to 18 search terms, lowercase, most specific first, "
    "total under 450 characters. titles: 3 options under 70 characters, curiosity without clickbait lies. "
    "Write everything in {lang}. Use only facts that are in the script."
)


def _prompt(title: str, script: str, chapters: list[tuple[str, str]]) -> str:
    items = "\n".join(f"{t} {name}" for t, name in chapters) or "(no chapters)"
    return f"Working title: {title}\n\nChapters:\n{items}\n\nScript:\n{script[:12000]}"


def _parse(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("a IA não devolveu JSON")
    data = json.loads(m.group(0))
    return {"description": str(data.get("description") or "").strip(),
            "tags": [str(t).strip() for t in data.get("tags") or [] if str(t).strip()][:20],
            "titles": [str(t).strip() for t in data.get("titles") or [] if str(t).strip()][:5]}


def generate(cfg: AppConfig, title: str, script: str, chapters: list[tuple[str, str]], lang: str,
             provider: str = "") -> dict[str, Any]:
    provider = provider or cfg.analysis.provider
    system = SYSTEM.replace("{lang}", LANG_NAMES.get(lang, "the script's language"))
    prompt = _prompt(title, script, chapters)
    if provider == "openai":
        from broll_bot.llm_openai import OpenAIJSON

        client = OpenAIJSON(cfg.analysis.openai_model, effort="low", max_tokens=6000, step="descrição e tags")
        data = client.complete(system, prompt)
        return _parse(json.dumps(data))
    if provider == "anthropic":
        import anthropic

        resp = anthropic.Anthropic().messages.create(model=cfg.analysis.model, max_tokens=3000, system=system,
                                                     messages=[{"role": "user", "content": prompt}])
        from broll_bot import usage

        usage.from_anthropic(resp, cfg.analysis.model, "descrição e tags")
        return _parse("".join(getattr(b, "text", "") for b in resp.content))
    raise ValueError("Escolha a IA (Claude ou ChatGPT) em Configurações para gerar a descrição.")


def full_description(seo: dict[str, Any], chapters: list[tuple[str, str]], credits_line: str = "") -> str:
    parts = [seo.get("description", "").strip()]
    if chapters:
        parts.append("\n".join(f"{t} {name}" for t, name in chapters))
    if credits_line:
        parts.append(credits_line)
    return "\n\n".join(p for p in parts if p)
