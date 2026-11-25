"""Textos fixos dos templates no idioma do roteiro (``analysis.script_language``).

Os textos que vêm da fala (títulos, itens, números) já chegam no idioma certo; aqui
ficam só os rótulos que o template desenha sozinho ("PRÓS", "INSCREVER-SE"...).
Idioma sem tradução cai no inglês.
"""

from __future__ import annotations

import re

TEXTS: dict[str, dict[str, str]] = {
    "pt": {"pros": "PRÓS", "cons": "CONTRAS", "cheap": "BARATO", "fair": "JUSTO", "expensive": "CARO",
           "final_score": "NOTA FINAL", "subscribe": "INSCREVER-SE", "subscribed": "INSCRITO",
           "ago": "há 2 dias", "reply": "Responder", "user": "@inscrito", "comment_cta": "COMENTE AQUI",
           "before": "ANTES", "after": "DEPOIS", "news": "NOTÍCIA", "timer": "TEMPO", "top_speed": "VELOCIDADE MÁXIMA",
           "fuel": "CONSUMO", "file": "DOSSIÊ",
           "breaking": "URGENTE", "live": "AO VIVO", "latest": "ÚLTIMAS", "report": "REPORTAGEM",
           "ranking": "RANKING", "number": "Nº", "spotlight": "EM FOCO", "timeline": "LINHA DO TEMPO",
           "compare": "COMPARATIVO", "in_of": "EM", "repair": "OFICINA", "fault": "DEFEITO", "paid": "PAGO",
           "key_point": "PONTO-CHAVE", "analysis": "ANÁLISE", "source": "FONTE", "step": "PASSO", "of": "DE", "unconfirmed": "NÃO CONFIRMADO", "no_data": "SEM DADOS AINDA", "estimate": "ESTIMATIVA", "fact_check": "CHECAGEM", "warranty": "GARANTIA", "years": "ANOS", "teardown": "DESMONTADO", "growth": "CRESCIMENTO", "drop": "QUEDA", "myth": "MITO", "fact": "FATO", "definition": "Definição", "in_this_video": "Neste vídeo"},
    "en": {"pros": "PROS", "cons": "CONS", "cheap": "CHEAP", "fair": "FAIR", "expensive": "EXPENSIVE",
           "final_score": "FINAL SCORE", "subscribe": "SUBSCRIBE", "subscribed": "SUBSCRIBED",
           "ago": "2 days ago", "reply": "Reply", "user": "@subscriber", "comment_cta": "COMMENT BELOW",
           "before": "BEFORE", "after": "AFTER", "news": "NEWS", "timer": "TIME", "top_speed": "TOP SPEED",
           "fuel": "FUEL ECONOMY", "file": "FILE",
           "breaking": "BREAKING NEWS", "live": "LIVE", "latest": "LATEST", "report": "REPORT",
           "ranking": "COUNTDOWN", "number": "No.", "spotlight": "SPOTLIGHT", "timeline": "TIMELINE",
           "compare": "HEAD TO HEAD", "in_of": "IN", "repair": "WORKSHOP", "fault": "FAULT", "paid": "PAID",
           "key_point": "KEY POINT", "analysis": "ANALYSIS", "source": "SOURCE", "step": "STEP", "of": "OF", "unconfirmed": "UNCONFIRMED", "no_data": "NO DATA YET", "estimate": "ESTIMATE", "fact_check": "FACT CHECK", "warranty": "WARRANTY", "years": "YEARS", "teardown": "TEARDOWN", "growth": "GROWTH", "drop": "DOWN", "myth": "MYTH", "fact": "FACT", "definition": "Definition", "in_this_video": "In this video"},
    "fr": {"pros": "POINTS FORTS", "cons": "POINTS FAIBLES", "cheap": "BON MARCHÉ", "fair": "CORRECT",
           "expensive": "CHER", "final_score": "NOTE FINALE", "subscribe": "S'ABONNER", "subscribed": "ABONNÉ",
           "ago": "il y a 2 jours", "reply": "Répondre", "user": "@abonne", "comment_cta": "DITES-LE EN COMMENTAIRE",
           "before": "AVANT", "after": "APRÈS", "news": "ACTUALITÉ", "timer": "TEMPS", "top_speed": "VITESSE MAX",
           "fuel": "CONSOMMATION", "file": "DOSSIER",
           "breaking": "DERNIÈRE MINUTE", "live": "EN DIRECT", "latest": "À LA UNE", "report": "REPORTAGE",
           "ranking": "CLASSEMENT", "number": "N°", "spotlight": "EN VUE", "timeline": "CHRONOLOGIE",
           "compare": "FACE-À-FACE", "in_of": "SUR", "repair": "GARAGE", "fault": "PANNE", "paid": "PAYÉ",
           "key_point": "À RETENIR", "analysis": "ANALYSE", "source": "SOURCE", "step": "ÉTAPE", "of": "SUR", "unconfirmed": "NON CONFIRMÉ", "no_data": "PAS ENCORE DE DONNÉES", "estimate": "ESTIMATION", "fact_check": "VÉRIFICATION", "warranty": "GARANTIE", "years": "ANS", "teardown": "DÉMONTÉE", "growth": "CROISSANCE", "drop": "BAISSE", "myth": "MYTHE", "fact": "RÉALITÉ", "definition": "Définition", "in_this_video": "Dans cette vidéo"},
    "pl": {"pros": "ZALETY", "cons": "WADY", "cheap": "TANIO", "fair": "W SAM RAZ", "expensive": "DROGO",
           "final_score": "OCENA KOŃCOWA", "subscribe": "SUBSKRYBUJ", "subscribed": "SUBSKRYBUJESZ",
           "ago": "2 dni temu", "reply": "Odpowiedz", "user": "@widz", "comment_cta": "NAPISZ W KOMENTARZU",
           "before": "PRZED", "after": "PO", "news": "WIADOMOŚCI", "timer": "CZAS", "top_speed": "PRĘDKOŚĆ MAKS.",
           "fuel": "SPALANIE", "file": "AKTA",
           "breaking": "PILNE", "live": "NA ŻYWO", "latest": "NAJNOWSZE", "report": "REPORTAŻ",
           "ranking": "RANKING", "number": "NR", "spotlight": "W CENTRUM", "timeline": "OŚ CZASU",
           "compare": "PORÓWNANIE", "in_of": "NA", "repair": "WARSZTAT", "fault": "USTERKA", "paid": "ZAPŁACONO",
           "key_point": "NAJWAŻNIEJSZE", "analysis": "ANALIZA", "source": "ŹRÓDŁO", "step": "KROK", "of": "Z", "unconfirmed": "NIEPOTWIERDZONE", "no_data": "BRAK DANYCH", "estimate": "SZACUNEK", "fact_check": "SPRAWDZAMY", "warranty": "GWARANCJA", "years": "LAT", "teardown": "ROZEBRANY", "growth": "WZROST", "drop": "SPADEK", "myth": "MIT", "fact": "FAKT", "definition": "Definicja", "in_this_video": "W tym filmie"},
    "es": {"pros": "PROS", "cons": "CONTRAS", "cheap": "BARATO", "fair": "JUSTO", "expensive": "CARO",
           "final_score": "NOTA FINAL", "subscribe": "SUSCRIBIRSE", "subscribed": "SUSCRITO",
           "ago": "hace 2 días", "reply": "Responder", "user": "@suscriptor", "comment_cta": "COMENTA ABAJO",
           "before": "ANTES", "after": "DESPUÉS", "news": "NOTICIA", "timer": "TIEMPO", "top_speed": "VELOCIDAD MÁXIMA",
           "fuel": "CONSUMO", "file": "EXPEDIENTE",
           "breaking": "ÚLTIMA HORA", "live": "EN VIVO", "latest": "ÚLTIMAS", "report": "REPORTAJE",
           "ranking": "RANKING", "number": "Nº", "spotlight": "EN FOCO", "timeline": "CRONOLOGÍA",
           "compare": "CARA A CARA", "in_of": "DE", "repair": "TALLER", "fault": "AVERÍA", "paid": "PAGADO",
           "key_point": "CLAVE", "analysis": "ANÁLISIS", "source": "FUENTE", "step": "PASO", "of": "DE", "unconfirmed": "SIN CONFIRMAR", "no_data": "SIN DATOS AÚN", "estimate": "ESTIMACIÓN", "fact_check": "VERIFICACIÓN", "warranty": "GARANTÍA", "years": "AÑOS", "teardown": "DESMONTADO", "growth": "CRECIMIENTO", "drop": "CAÍDA", "myth": "MITO", "fact": "REALIDAD", "definition": "Definición", "in_this_video": "En este video"},
}

# chamada para inscrição/like na fala (liga o overlay "subscribe" sozinho)
CTA_WORDS = (
    "inscrev", "sininho", "deixa o like", "deixe o like", "deixe seu like", "deixa seu like", "curte o video",
    "subscribe", "hit the like", "like button", "smash that like", "the bell",
    "abonne", "abonnez", "la cloche", "pouce bleu", "un like",
    "subskryb", "zasubskrybuj", "dzwonek", "dzwoneczek", "łapk", "lapk", "lajk",
    "suscrib", "campanita", "dale like",
)
# palavras que contêm as de cima sem ser chamada para se inscrever: "sans abonnement", "les abonnements"
# (assinatura paga), "subscription"
_NOT_CTA = re.compile(r"abonnements?|subscriptions?", re.I)


def has_cta(text: str) -> bool:
    """A frase chama para se inscrever / dar like ("abonnez-vous", "hit subscribe")?"""
    from ..text import normalize

    low = _NOT_CTA.sub(" ", (text or "").lower())
    norm = _NOT_CTA.sub(" ", normalize(text or ""))
    return any(c in low or c in norm for c in CTA_WORDS)


def text(key: str, lang: str = "pt") -> str:
    lang = (lang or "pt").split("-")[0].lower()
    return TEXTS.get(lang, TEXTS["en"]).get(key) or TEXTS["en"][key]
