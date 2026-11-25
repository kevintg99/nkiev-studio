"""Leitura do report.json para as abas Diagnóstico, Arquivos e Tags e descrição."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from broll_bot.cues import set_language
from broll_bot.subject import prime_headers, rank_header

OK, WARN, INFO = "ok", "aviso", "info"
INTRO = {"en": "Intro", "fr": "Introduction", "pl": "Wstęp", "es": "Introducción", "pt": "Introdução"}


def clock(seconds: float) -> str:
    s = int(seconds)
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _shots(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [{**shot, "start": scene["start"]} for scene in report.get("scenes", []) for shot in scene.get("shots", [])]


def diagnose(report: dict[str, Any], reuse_gap: float = 45.0) -> list[dict[str, str]]:
    """Avisos em português simples, do mais importante para o menos."""
    out: list[dict[str, str]] = []
    sync = report.get("sync") or {}
    if sync.get("mode") == "estimada":
        out.append({"level": WARN, "text": "A narração não foi transcrita: os cortes seguiram uma estimativa e as "
                                           "imagens podem entrar fora do tempo da fala (veja o log)", "when": ""})
    if report.get("language_note"):
        out.append({"level": WARN, "text": f"Idioma: {report['language_note']} (confira o canal escolhido)",
                    "when": ""})
    shots = _shots(report)
    media = [s for s in shots if s.get("media_id")]
    distinct = {(s["source"], s["media_id"]) for s in media}
    refs = report.get("references") or {}
    subjects = report.get("subjects") or []
    if subjects:
        with_refs = sum(1 for c in subjects if refs.get(c))
        level = OK if with_refs == len(subjects) else WARN
        out.append({"level": level, "text": f"{with_refs} de {len(subjects)} itens com fotos de referência", "when": ""})
    if media:
        counts: dict[tuple, int] = defaultdict(int)
        for s in media:
            counts[(s["source"], s["media_id"])] += 1
        most = max(counts.values())
        out.append({"level": OK if most <= 3 else WARN,
                    "text": f"{len(distinct)} imagens diferentes em {len(media)} trechos · "
                            f"a mais repetida aparece {most}x", "when": ""})
    # mesma imagem de novo em menos de 45 s
    seen: dict[tuple, float] = {}
    close = []
    for s in sorted(media, key=lambda x: x.get("cut_in") or x["start"]):
        key, t = (s["source"], s["media_id"]), s.get("cut_in") or s["start"]
        if key in seen and t - seen[key] < reuse_gap:
            close.append(t)
        seen[key] = t
    for t in close[:4]:
        out.append({"level": WARN, "text": f"Mesma imagem duas vezes em menos de {int(reuse_gap)} s", "when": clock(t)})
    fallbacks = [s for s in shots if s.get("fallback")]
    if fallbacks:
        out.append({"level": WARN, "text": f"{len(fallbacks)} trecho(s) sem imagem boa (fundo gerado ou genérico)",
                    "when": clock(fallbacks[0].get("cut_in") or fallbacks[0]["start"])})
    rv = report.get("review") or {}
    if rv.get("mode") == "api":
        if rv.get("verdicts"):
            out.append({"level": OK, "text": f"Revisão das imagens pela IA: {rv['verdicts']} imagens avaliadas em "
                                             f"{rv.get('scenes_sent', 0)} cenas", "when": ""})
        else:
            why = (rv.get("errors") or [""])[-1] or ("nenhuma cena precisou" if not rv.get("scenes_sent") else
                                                     "a IA não devolveu avaliações")
            out.append({"level": WARN, "text": f"A revisão das imagens estava ligada mas não rodou: {why}", "when": ""})
    names = {"youtube_cc": "YouTube Creative Commons", "youtube_any": "YouTube (qualquer licença)",
             "openverse": "Openverse", "wikimedia": "Wikimedia", "archive": "Arquivo histórico (Internet Archive)",
             "pexels": "Pexels", "pixabay": "Pixabay"}
    for name, why in (report.get("sources_off") or {}).items():
        if name != "mixkit_local":  # biblioteca vazia não é problema
            out.append({"level": WARN, "text": f"{names.get(name, name)} não foi usado: {why}", "when": ""})
    for name, row in (report.get("sources_funnel") or {}).items():
        if name not in ("openverse", "archive", "youtube_cc", "youtube_any") or not row.get("found") or row.get("used", 0) >= 3:
            continue
        label = names.get(name, name)
        found = row["found"]
        if row.get("download_failed") and row["download_failed"] >= max(2, row.get("approved", 0) // 2):
            errors = " ".join(row.get("errors") or [])
            why = (row.get("errors") or [""])[0]
            hint = ""
            if name.startswith("youtube") and re.search(r"not a bot|anti-robô|sign in to confirm", errors, re.I):
                hint = (" — o YouTube pediu login (anti-robô): faça login no YouTube no Firefox e ponha "
                        "youtube_cc.cookies_from_browser: firefox no config.yaml")
                why = ""
            elif name.startswith("youtube") and "Deno" in errors:
                hint = " (instale o Deno: rode o instalar_app.bat de novo)"
            text = f"{label}: {row['download_failed']} downloads falharam{hint}" + (f" — {why[:90]}" if why else "")
        elif row.get("thumb", 0) < found * 0.5:
            why = (row.get("thumb_errors") or [""])[0]
            text = (f"{label}: {found} achados, só {row.get('thumb', 0)} com miniatura (a IA não conseguiu ver o resto)"
                    + (f" — {why[:80]}" if why else ""))
        elif row.get("rejected", 0) > row.get("approved", 0):
            text = f"{label}: {found} achados, {row['rejected']} reprovados pela revisão (não combinavam com a fala)"
        else:
            text = (f"{label}: {found} achados, {row.get('good', 0)} bons, {row.get('used', 0)} usados "
                    "(os outros bancos tinham imagem melhor)")
        out.append({"level": INFO, "text": text, "when": ""})
    vetoes = report.get("vetoes") or {}
    if vetoes:
        out.append({"level": INFO, "text": f"{sum(vetoes.values())} imagens barradas pelas checagens visuais "
                                           "(prédio, carro batido ou outro carro)", "when": ""})
    fixes = report.get("coherence_fixes") or []
    if fixes:
        out.append({"level": INFO, "text": f"{len(fixes)} ajuste(s) no plano da IA para nada aparecer na tela "
                                           "sem estar na fala", "when": ""})
    totals = report.get("totals") or {}
    if totals.get("templates"):
        out.append({"level": INFO, "text": f"{sum(totals['templates'].values())} templates e "
                                           f"{sum((totals.get('overlays') or {}).values())} overlays no vídeo", "when": ""})
    return out


def credits_text(report: dict[str, Any]) -> str:
    rows = report.get("credits") or []
    by_source: dict[str, list[str]] = defaultdict(list)
    for c in rows:
        who = (c.get("author") or "").strip()
        by_source[c.get("source") or "?"].append(f"{who} – {c['url']}" if who else c["url"])
    names = {"wikimedia": "Wikimedia Commons", "pexels": "Pexels", "pixabay": "Pixabay", "mixkit_local": "Mixkit",
             "openverse": "Openverse (Creative Commons)", "youtube_cc": "YouTube (Creative Commons CC BY)",
             "youtube_any": "YouTube (trechos curtos, © de cada canal)",
             "archive": "Internet Archive (filmes de época em domínio público)"}
    parts = []
    for source, lines in sorted(by_source.items()):
        parts.append(f"{names.get(source, source)}:\n" + "\n".join(sorted(set(lines))))
    return "\n\n".join(parts)


def chapters(report: dict[str, Any], lang: str | None = None) -> list[tuple[str, str]]:
    """Capítulos do YouTube a partir dos títulos do ranking ("Number 8: the Toyota Corolla")."""
    lang = lang or report.get("language") or "pt"
    set_language(lang)
    try:
        out: list[tuple[str, str]] = []
        found: list[tuple[str, float]] = []
        for scene in report.get("scenes", []):
            text = scene.get("narrated_text") or ""
            start, end = scene.get("start", 0.0), scene.get("end", scene.get("start", 0.0))
            for m in re.finditer(r"[^.!?…]+[.!?…]*", text):  # o título pode estar no meio da cena
                found.append((m.group(0), start + (end - start) * m.start() / max(1, len(text))))  # proporcional
        prime_headers([t for t, _ in found])  # "Number ten." + "The Range Rover Evoque, ..." na frase seguinte
        for sentence, at in found:
            hit = rank_header(sentence)
            if hit and not any(name.startswith(f"#{hit[0]} ") for _, name in out):
                out.append((clock(at), f"#{hit[0]} {hit[1]}"))
    finally:
        set_language("pt")
    if not out:
        return []
    if out[0][0] != "00:00":
        out.insert(0, ("00:00", INTRO.get(lang, "Intro")))
    return out if len(out) >= 3 else []  # o YouTube só mostra capítulos com 3 ou mais
