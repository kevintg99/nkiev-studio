"""Buscas de b-roll sempre em inglês.

Pexels, Pixabay e o Wikimedia Commons só entendem bem inglês. O plano pede isso, mas a IA
barata às vezes escreve as buscas no idioma do roteiro ("voiture occasion parking",
"dacia logan mcv exterieur break"), e aí os bancos devolvem pouco ou nada, e o vídeo fica
repetindo as mesmas imagens. Depois do plano (inclusive um plano do cache), o bot confere as
buscas e, se muitas estiverem em outro idioma, traduz todas numa chamada só (barata e guardada
no cache).
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .cache import SQLiteCache, make_key
from .config import AnalysisConfig
from .lang import MARKERS
from .log import get_logger
from .models import Scene
from .text import normalize

log = get_logger("query_lang")

# palavras de busca frequentes em cada idioma que NÃO existem em inglês (sem acento)
FOREIGN_WORDS: dict[str, set[str]] = {
    "fr": {"voiture", "voitures", "interieur", "exterieur", "moteur", "moteurs", "coffre", "courroie", "freinage",
           "freins", "pneus", "rouille", "concession", "concessionnaire", "annonce", "annonces", "garagiste",
           "atelier", "vidange", "ville", "autoroute", "commentaires", "commentaire", "ecran", "compteur",
           "tableau", "volant", "sieges", "siege", "banquette", "boite", "embrayage", "vitesses", "batterie",
           "voyant", "carrosserie", "phare", "phares", "jante", "jantes", "essai", "conducteur", "familiale",
           "citadine", "berline", "occasion", "neuve", "vieille", "petite", "grosse", "rouge", "blanche", "noire",
           "grise", "bleue", "chemin", "campagne", "montagne", "dessous", "capot", "retraite", "utilisateur",
           "ecrit", "modele", "prix", "vendeur", "acheteur", "facture", "plaquettes", "disques", "filtre"},
    "pt": {"carro", "carros", "porta", "malas", "freio", "freios", "pneu", "pneus",
           "ferrugem", "concessionaria", "anuncio", "oficina", "mecanico", "estrada", "cidade", "rodovia",
           "painel", "volante", "banco", "bancos", "cambio", "embreagem", "bateria", "farol", "farois", "roda",
           "rodas", "usado", "usados", "novo", "vermelho", "branco", "preto", "prata", "motorista", "preco",
           "vendedor", "comprador", "revisao", "consumo", "garagem", "familia"},
    "pl": {"samochod", "samochody", "silnik", "wnetrze", "bagaznik", "hamulce", "opony", "rdza",
           "ogloszenie", "warsztat", "mechanik", "droga", "miasto", "autostrada", "deska", "kierownica",
           "fotele", "skrzynia", "biegow", "akumulator", "reflektory", "kola", "uzywany", "uzywane", "nowy",
           "czerwony", "bialy", "czarny", "kierowca", "cena", "sprzedawca", "kupujacy", "przeglad", "spalanie"},
}
_ACCENTS = re.compile(r"[àâäçéèêëîïôöùûüÿœæãõáíóúñąćęłńśźż]")


def is_foreign(query: str, script_lang: str) -> bool:
    """A busca tem palavra do idioma do roteiro (ou acento), não de inglês."""
    if _ACCENTS.search(query.lower()):
        return True
    words = set(re.findall(r"[a-z0-9]+", normalize(query)))
    lang = (script_lang or "").split("-")[0]
    markers = {normalize(w) for w in MARKERS.get(lang, set())} - MARKERS["en"]
    return bool(words & (FOREIGN_WORDS.get(lang, set()) | markers))


def foreign_share(scenes: list[Scene], script_lang: str) -> float:
    qs = [q for s in scenes for q in s.analysis.queries if s.analysis.query_language == "en"]
    return sum(is_foreign(q, script_lang) for q in qs) / len(qs) if qs else 0.0


def ensure_english(scenes: list[Scene], cfg: AnalysisConfig, cache: SQLiteCache | None,
                   threshold: float = 0.1) -> list[str]:
    """Traduz para o inglês as buscas, a descrição visual e os termos a evitar de todas as cenas,
    se mais de ``threshold`` das buscas estiverem em outro idioma. Devolve o que foi feito."""
    lang = (cfg.script_language or "en").split("-")[0]
    if lang == "en" or cfg.provider not in ("openai", "anthropic"):
        return []
    share = foreign_share(scenes, lang)
    if share <= threshold:
        return []
    texts: list[str] = []
    for s in scenes:
        a = s.analysis
        if a.query_language != "en":
            continue
        texts += [*a.queries, a.fallback_query, a.visual_summary, *a.negative_terms]
    unique = list(dict.fromkeys(t for t in texts if t and t.strip()))
    try:
        mapping = _translate(unique, lang, cfg, cache)
    except Exception as exc:  # noqa: BLE001 - sem tradução o vídeo sai igual, só com buscas piores
        log.warning("não deu para traduzir as buscas para o inglês", extra={"stage": "scenes", "error": str(exc)})
        return []
    for s in scenes:
        a = s.analysis
        if a.query_language != "en":
            continue
        a.queries = list(dict.fromkeys(_short(mapping.get(q, q)) for q in a.queries))
        a.fallback_query = _short(mapping.get(a.fallback_query, a.fallback_query))
        a.visual_summary = mapping.get(a.visual_summary, a.visual_summary)
        a.negative_terms = list(dict.fromkeys(mapping.get(t, t) for t in a.negative_terms))
    note = f"{share:.0%} das buscas estavam em '{lang}': traduzidas para o inglês ({len(unique)} textos)"
    log.info("buscas traduzidas para o inglês", extra={"stage": "scenes", "detalhes": note})
    return [note]


def _short(q: str) -> str:
    return " ".join(q.replace('"', " ").split()[:6])


SYSTEM = ("You translate stock-footage search terms into natural, short English search queries. "
          "Keep brand and model names exactly (Dacia Logan MCV, Mazda 6, Lexus CT 200h). "
          "Answer only with JSON: {\"t\": [one English string per input, same order]}.")


def _translate(texts: list[str], lang: str, cfg: AnalysisConfig, cache: SQLiteCache | None) -> dict[str, str]:
    out: dict[str, str] = {}
    todo: list[str] = []
    for t in texts:
        hit = cache.get("query_en", make_key("query-en", lang, t)) if cache else None
        if hit is not None:
            out[t] = hit
        else:
            todo.append(t)
    for i in range(0, len(todo), 120):
        batch = todo[i:i + 120]
        prompt = (f"Source language: {lang}. Translate each item to English (already-English items stay "
                  f"as they are):\n" + json.dumps(batch, ensure_ascii=False))
        result = _llm_json(cfg, SYSTEM, prompt)
        translated = result.get("t") if isinstance(result, dict) else None
        if not isinstance(translated, list) or len(translated) != len(batch):
            raise ValueError("resposta da tradução fora do formato")
        for src, dst in zip(batch, translated):
            dst = str(dst).strip() or src
            out[src] = dst
            if cache:
                cache.set("query_en", make_key("query-en", lang, src), dst, ttl_seconds=90 * 86400)
    return out


def _llm_json(cfg: AnalysisConfig, system: str, prompt: str, effort: str = "low",
              timeout: float | None = None) -> dict[str, Any]:
    if cfg.provider == "openai":
        from .llm_openai import OpenAIError, OpenAIJSON

        try:
            return OpenAIJSON(cfg.openai_model, effort, 8000, step="buscas em inglês",
                              timeout=timeout).complete(system, prompt)
        except OpenAIError as exc:  # modelo que não aceita o raciocínio "minimal": vai no "low"
            if effort == "low" or "reasoning" not in str(exc).lower():
                raise
            return OpenAIJSON(cfg.openai_model, "low", 8000, step="buscas em inglês",
                              timeout=timeout).complete(system, prompt)
    import anthropic  # type: ignore[import-not-found]

    resp = anthropic.Anthropic().messages.create(model=cfg.model, max_tokens=8000, system=system,
                                                 messages=[{"role": "user", "content": prompt}])
    from . import usage

    usage.from_anthropic(resp, cfg.model, "buscas em inglês")
    text = "".join(getattr(b, "text", "") for b in resp.content)
    return json.loads(text[text.find("{"): text.rfind("}") + 1])


REQUERY_SYSTEM = (
    "You are a video editor choosing stock b-roll. For each narration excerpt write 3 short ENGLISH search "
    "queries (2-5 words) for footage that shows exactly what the excerpt talks about (a part, an action, a "
    "place), plus a one-line English visual summary. Stock sites have footage, not documents: never ask for charts, graphs, statistics, percentages, survey forms or numbers on paper; show the car, the part, the workshop or the people instead. When the footage shows the car itself, name the given car "
    "model; never another model. Vary the queries between excerpts. Answer only with JSON: "
    "{\"r\": [{\"i\": 0, \"q\": [\"...\", \"...\", \"...\"], \"v\": \"...\"}, ...]} with one item per excerpt, same "
    "order, each with the excerpt's \"i\".")


REQUERY_SYSTEM_NICHE = (
    "You are a video editor choosing stock b-roll for a video about {niche}. For each narration excerpt write 3 "
    "short ENGLISH search queries (2-5 words) for footage that shows exactly what the excerpt talks about (an "
    "object, an action, a place, people doing it), plus a one-line English visual summary. Stock sites have "
    "footage, not documents: never ask for charts, graphs, statistics, percentages or numbers on paper; show the "
    "real thing instead. When the excerpt has a named subject (\"car\" field), name it. Vary the queries between "
    "excerpts. Answer only with JSON: {{\"r\": [{{\"i\": 0, \"q\": [\"...\", \"...\", \"...\"], \"v\": \"...\"}}, ...]}} "
    "with one item per excerpt, same order, each with the excerpt's \"i\".")


def _niche_key(cfg: AnalysisConfig) -> tuple[str, ...]:
    from .subject import is_automotive

    return () if is_automotive(cfg.niche) else (cfg.niche,)  # vídeo de carro mantém o cache de antes


def _requery_system(cfg: AnalysisConfig) -> str:
    from .subject import is_automotive

    return REQUERY_SYSTEM if is_automotive(cfg.niche) else REQUERY_SYSTEM_NICHE.format(niche=cfg.niche)


REQUERY_BATCH = 10
REQUERY_WORKERS = 4
REQUERY_TIMEOUT = 120.0  # s por chamada


def _requery_batch(batch: list[Scene], cfg: AnalysisConfig, system: str, lang: str) -> dict[int, dict[str, Any]]:
    """Buscas novas para um lote de pedaços (índice da cena -> {"q", "v"}). Resposta cortada: refaz em duas metades."""
    items = [{"i": k, "car": s.analysis.subject or "", "text": s.text[:600]} for k, s in enumerate(batch)]
    prompt = f"Narration language: {lang}. Excerpts:\n" + json.dumps(items, ensure_ascii=False)
    try:  # um lote que falha não derruba os outros (antes 1 erro perdia o vídeo inteiro)
        result = _llm_json(cfg, system, prompt, effort="minimal", timeout=REQUERY_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - sem isso o vídeo sai igual, só com mais repetição
        if len(batch) > 1:  # resposta cortada pelo limite de tokens: tenta de novo em dois lotes menores
            half = len(batch) // 2
            log.info("lote de buscas falhou; tentando em lotes menores",
                     extra={"stage": "scenes", "cenas": len(batch), "error": str(exc)[:150]})
            return {**_requery_batch(batch[:half], cfg, system, lang), **_requery_batch(batch[half:], cfg, system, lang)}
        log.warning("não deu para refazer as buscas das cenas divididas", extra={"stage": "scenes", "error": str(exc)})
        return {}
    rows = result.get("r") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        return {}
    out: dict[int, dict[str, Any]] = {}
    for pos, row in enumerate(rows):
        if not isinstance(row, dict) or not row.get("q"):
            continue
        # a IA às vezes pula um item: vale o "i" que ela devolve; sem ele, só a ordem se bater
        k = row.get("i")
        if not isinstance(k, int):
            if len(rows) != len(batch):
                continue
            k = pos
        if not 0 <= k < len(batch):
            continue
        qs = row["q"] if isinstance(row["q"], list) else [row["q"]]
        out[batch[k].index] = {"q": [str(q) for q in qs][:4], "v": str(row.get("v") or "")}
    return out


def requery_parts(scenes: list[Scene], cfg: AnalysisConfig, cache: SQLiteCache | None) -> list[str]:
    """Pedaços de uma cena longa (a IA fez uma cena de 3 min para um carro inteiro) herdam as mesmas
    4 buscas: 15 pedaços dividem o mesmo punhado de imagens. Aqui cada pedaço ganha buscas sobre o
    que ele fala, numa chamada só para todos (e guardada no cache)."""
    parts = [s for s in scenes if s.analysis.requery and s.analysis.query_language == "en"]
    if not parts or cfg.provider not in ("openai", "anthropic"):
        return []
    lang = (cfg.script_language or "en").split("-")[0]
    done: dict[int, dict[str, Any]] = {}
    todo: list[Scene] = []
    for s in parts:
        hit = cache.get("requery", make_key("requery-v1", lang, s.analysis.subject, s.text, *_niche_key(cfg))) if cache else None
        if isinstance(hit, dict):
            done[s.index] = hit
        else:
            todo.append(s)
    # lotes de 10 em paralelo, com raciocínio mínimo e tempo máximo: no vídeo dos 9 pneus de inverno os lotes de 20,
    # um por vez, com o gpt-5-mini pensando até estourar os 8.000 tokens (e uma chamada pendurada 10 min) levaram
    # 19 min para uma tarefa de 1 min
    batches = [todo[i:i + REQUERY_BATCH] for i in range(0, len(todo), REQUERY_BATCH)]
    if batches:
        system = _requery_system(cfg)
        with ThreadPoolExecutor(max_workers=min(REQUERY_WORKERS, len(batches))) as pool:
            for found in pool.map(lambda b: _requery_batch(b, cfg, system, lang), batches):
                done.update(found)
                if cache:
                    for s in todo:
                        if s.index in found:
                            cache.set("requery", make_key("requery-v1", lang, s.analysis.subject, s.text,
                                                          *_niche_key(cfg)), found[s.index], ttl_seconds=90 * 86400)
    changed = 0
    for s in parts:
        new = done.get(s.index)
        if not new:
            continue
        a = s.analysis
        # as novas primeiro; uma da cena original fica de reserva
        from .plan_fix import is_abstract

        fresh = [_short(q) for q in new["q"] if q.strip() and not is_abstract(q)]  # nada de "sales chart"
        a.queries = list(dict.fromkeys(fresh + a.queries[:1]))[:4]
        if new["v"]:
            a.visual_summary = new["v"]
        a.requery = False
        changed += 1
    if not changed:
        return []
    note = f"{changed} pedaço(s) de cenas longas ganharam buscas próprias"
    log.info("buscas refeitas nas cenas divididas", extra={"stage": "scenes", "detalhes": note})
    return [note]
