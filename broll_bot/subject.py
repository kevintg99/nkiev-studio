"""Assunto do vídeo e sentido das palavras no nicho.

* ``detect_subject``: acha no roteiro o carro principal ("toyota corolla") pela marca
  mais citada seguida do modelo. Pode ser fixado em ``analysis.subject``.
* ``GLOSSARY``: palavras ambíguas no nicho automotivo ("geração" é do carro, não
  geração Z; "câmbio" é a caixa de marcha, não dinheiro). Viram termos a evitar nas
  cenas em que aparecem, como rede de segurança para o que a IA não pegar.
* ``anchor_scenes``: toda busca de b-roll precisa mostrar um carro/o assunto; busca
  abstrata ("young generation", "evolution") ganha o nome do carro, e cenas que falam
  do carro ganham uma busca pelo modelo.
* ``section_subjects``: vídeo de ranking/lista com vários carros ("Numéro 8 : la Dacia
  Logan MCV" ... "Numéro 1 : la Toyota Prius"). Cada seção tem o seu carro; o assunto
  único do vídeo ancoraria as buscas dos 8 no mesmo modelo.
"""

from __future__ import annotations

import re
from collections import Counter

from .log import get_logger
from .models import Scene
from .text import STOPWORDS, normalize, tokens

log = get_logger("subject")

BRANDS = (
    "toyota", "honda", "chevrolet", "fiat", "volkswagen", "vw", "hyundai", "jeep", "ford", "renault", "nissan",
    "bmw", "mercedes", "audi", "byd", "peugeot", "citroen", "kia", "mitsubishi", "chery", "caoa", "gwm", "haval",
    "porsche", "volvo", "ram", "dodge", "tesla", "subaru", "mazda", "suzuki", "lexus", "jac", "land", "mini",
    # marcas fortes na Europa (França, Polônia)
    "dacia", "skoda", "seat", "cupra", "opel", "alfa", "lancia", "smart", "ds", "infiniti", "jaguar", "saab",
    "chrysler", "cadillac", "lada", "mg", "polestar", "genesis", "ssangyong", "isuzu", "daihatsu", "rover",
)
# palavras que não são nome de modelo quando aparecem depois da marca
NOT_MODEL = {"do", "da", "de", "e", "que", "com", "no", "na", "em", "para", "pra", "um", "uma", "o", "a", "é",
             # francês e polonês
             "du", "des", "et", "la", "le", "les", "est", "a", "en", "pour", "avec", "dans", "i", "w", "z", "to",
             "jest", "ma", "od", "po", "za", "przez", "oraz"}

# termos que já garantem que a busca mostra um carro (ou parte dele / contexto automotivo)
AUTO_TERMS = {
    "car", "cars", "sedan", "suv", "vehicle", "vehicles", "truck", "pickup", "hatchback", "coupe", "convertible",
    "engine", "wheel", "wheels", "tire", "tires", "tyre", "dashboard", "steering", "headlight", "headlights",
    "taillight", "dealership", "garage", "mechanic", "speedometer", "gearbox", "transmission", "trunk", "bumper",
    "grille", "interior", "seat", "seats", "highway", "road", "driving", "drive", "driver", "parking", "traffic",
    "automobile", "auto", "motor", "brake", "brakes", "exhaust", "hood", "fuel", "gas", "odometer", "showroom",
    *BRANDS,
}

# palavra (sem acento) -> o que ela NÃO é no nicho automotivo
GLOSSARY: dict[str, list[str]] = {
    "geracao": ["young people", "teenagers", "family portrait", "generation z"],
    "geracoes": ["young people", "teenagers", "family portrait", "generation z"],
    "modelo": ["fashion model", "runway"],
    "modelos": ["fashion model", "runway"],
    "cambio": ["currency exchange", "money bills"],
    "banco": ["bank building", "bank"],
    "bancos": ["bank building", "bank"],
    "bateria": ["drum kit", "drummer"],
    "revisao": ["paper document", "office meeting"],
    "pecas": ["chess pieces", "puzzle"],
    "evoluiu": ["human evolution", "evolution chart"],
    "evolucao": ["human evolution", "evolution chart"],
    "familia": ["family portrait"],
    "familiar": ["family portrait"],
    # francês
    "generation": ["young people", "teenagers", "family portrait", "generation z"],
    "generations": ["young people", "teenagers", "family portrait", "generation z"],
    "modele": ["fashion model", "runway"],
    "modeles": ["fashion model", "runway"],
    "boite": ["nightclub", "cardboard box"],
    "batterie": ["drum kit", "drummer"],
    "entretien": ["job interview", "office meeting"],
    "revente": ["shop sale", "market stall"],
    # polonês (sem acento; ł vira l)
    "generacja": ["young people", "teenagers", "family portrait", "generation z"],
    "generacji": ["young people", "teenagers", "family portrait", "generation z"],
    "generacje": ["young people", "teenagers", "family portrait", "generation z"],
    "pokolenia": ["young people", "family portrait", "grandparents"],
    "model": ["fashion model", "runway"],
    "skrzynia": ["wooden crate", "cardboard box"],
    "skrzynie": ["wooden crate", "cardboard box"],
    "serwis": ["tea set", "tableware"],
    # inglês: modelos com nome de bicho/objeto (BYD Seal, BYD Dolphin, bateria Blade)
    "seal": ["seal animal", "sea lion", "wildlife"],
    "dolphin": ["dolphin animal", "ocean wildlife"],
    "blade": ["knife", "sword"],
    "beetle": ["insect", "bug"],
    "mustang": ["wild horse"],
    "jaguar": ["jaguar animal", "big cat"],
}

GLOSSARY_PROMPT = (
    "Read every word in the channel niche sense. Automotive: 'geração' = a generation of the CAR MODEL "
    "(model years, e.g. 'toyota corolla 2014 sedan'), never people generations; 'modelo' = car model, never "
    "fashion; 'câmbio' = gearbox/transmission, never money; 'banco' = car seat; 'bateria' = car battery; "
    "'direção' = steering; 'revisão' = car service at a workshop; 'peças' = car parts; 'consumo' = fuel "
    "economy; 'revenda' = used-car resale value; 'família' in 'sedã familiar' = a family car. Illustrate these "
    "with the car itself, never with people, money, banks or abstract concepts. The same in French and Polish: "
    "'génération'/'generacja'/'pokolenie' = car generation; 'modèle'/'model' = car model; 'boîte'/'skrzynia "
    "biegów' = gearbox; 'entretien'/'serwis' = car service at a workshop; 'revente'/'odsprzedaż' = resale value; "
    "'batterie'/'akumulator' = car battery. Car models named after animals or objects are the CAR, never the "
    "thing: BYD 'Seal' and 'Dolphin', VW 'Beetle', Ford 'Mustang'; BYD 'Blade' is a car battery, never a knife."
)


def is_automotive(niche: str) -> bool:
    n = normalize(niche)
    return any(k in n for k in ("automot", "carro", "car ", "cars", "veicul", "motor"))


def _spelled(text: str) -> str:
    """ "B Y D" (como a voz de IA soletra) -> "BYD", para achar a marca no roteiro."""
    from .spoken import letters_to_acronyms

    return letters_to_acronyms(text or "")


def detect_subject(text: str) -> str:
    """Marca + modelo mais citados ("Toyota Corolla" x3 -> "toyota corolla"); "" se não achar.
    O modelo precisa ter cara de nome no roteiro (maiúscula ou número): "Land Rover paid for..." não
    é o carro "rover paid". Vale a marca mais falada do vídeo ("B Y D" 40 vezes contra 4 da Tesla) e, se
    ela aparece com vários modelos sem um dominar (Seal, Atto 3, Dolphin), o assunto é só a marca ("byd")."""
    raw = re.findall(r"[^\W_]+", _spelled(text))
    pairs: Counter[str] = Counter()
    brands: Counter[str] = Counter(b for w in raw if (b := normalize(w)) in BRANDS and w[:1].isupper())
    for i, w in enumerate(raw[:-1]):
        brand, nxt = normalize(w), raw[i + 1]
        model = normalize(nxt)
        if brand not in BRANDS or not w[:1].isupper() or model in NOT_MODEL or model in BRANDS:
            continue
        if not (nxt[:1].isupper() or any(c.isdigit() for c in nxt)):
            continue
        pairs[f"{brand} {model}"] += 1
    if not pairs:
        return ""
    subject = max(pairs, key=lambda p: (brands[p.split()[0]] * pairs[p], pairs[p]))
    if pairs[subject] < 2 and brands[subject.split()[0]] < 3:
        return ""  # "Stellantis recalled plug-in hybrid Jeep Wranglers" uma vez: o vídeo não é sobre o Jeep
    if len(pairs) >= 6 and (pairs[subject] < 3 or len(pairs) >= 8) and pairs[subject] < 0.3 * sum(pairs.values()):
        # vídeo sobre carros em geral, com uma lista de exemplos (Corolla, Yaris, Jazz, Mazda 3, Swift, Logan...):
        # a Corolla da abertura não é o assunto (senão toda busca vira "Toyota Corolla")
        return ""
    brand = subject.split()[0]
    same = sorted((n for p, n in pairs.items() if p.split()[0] == brand), reverse=True)
    if len(same) >= 3 and same[0] < 2 * same[1] and brands[brand] >= 3 * sum(same):
        return brand  # vídeo sobre a marca, não sobre um modelo
    return subject


# palavras que vêm depois da marca sem ser o modelo ("BMW diesel N47", "Ford essence 1.0")
_NOT_MODEL_WORDS = {"diesel", "essence", "petrol", "gasolina", "hybrid", "hybride", "electrique", "electric", "boite",
                    "moteur", "engine", "motor", "gearbox"}
# seção do ranking que é um motor/câmbio, não um modelo ("Peugeot 1.2 PureTech", "VW / Audi / Seat / Skoda DSG")
_COMPONENT = re.compile(r"\b(?:boite|gearbox|dsg|edc|eat\d|bmp6|etg6|engine|moteur|motor|ecoboost|puretech|bluehdi|"
                        r"tce|thp|hdi|dci|tsi|tdi|tfsi|n47|ingenium|dig-t|vti|pilotee|automatique|cambio)\b"
                        r"|\b\d\.\d\b")


TIRE_BRANDS = {"michelin", "bridgestone", "nokian", "goodyear", "firestone", "toyo", "general", "continental",
               "pirelli", "hankook", "yokohama", "falken", "cooper", "bfgoodrich", "dunlop", "kumho", "nexen",
               "motomaster", "nitto", "sailun", "gislaved", "vredestein", "uniroyal", "kelly", "blizzak",
               "hakkapeliitta", "nordman", "laufenn", "westlake", "giti", "maxxis", "sumitomo", "kleber"}


def is_tire_name(name: str) -> bool:
    """ "Nokian Hakkapeliitta R5", "Michelin X-Ice Snow", "MotoMaster" (vídeo de pneus)."""
    words = re.findall(r"[a-z0-9]+", normalize(name))
    return bool(words) and words[0] in TIRE_BRANDS


def is_component(subject: str) -> bool:
    """A seção é um motor, câmbio ou pneu (vale qualquer carro), não um modelo de carro só."""
    return " / " in subject or bool(_COMPONENT.search(normalize(subject))) or is_tire_name(subject)


def model_word(subject: str) -> str:
    """Palavra que identifica o modelo: "toyota corolla" -> "corolla"; "Dacia Logan MCV" -> "logan";
    "BMW diesel N47" -> "n47"; "Peugeot 1.2 PureTech" -> "puretech" (a cilindrada não identifica)."""
    words = re.findall(r"[a-z0-9]+(?:\.\d+)?", normalize(subject))
    if len(words) > 1 and words[0] in BRANDS:
        rest = [w for w in words[1:] if w not in _NOT_MODEL_WORDS and w not in BRANDS]
        rest = [w for w in rest if not re.fullmatch(r"\d\.\d", w)] or rest
        return rest[0] if rest else words[1]
    return words[-1] if words else ""


# o nome do carro no cabeçalho acaba no primeiro complemento: "Citroën Berlingo | de première génération",
# "Toyota Aygo | … et ses deux jumelles", "Mazda 3 | , avec le moteur 2.0" (pt, fr, pl)
_NAME_END = re.compile(r"\s*(?:,|;|:|…|\.\.\.|\(|\s[–—-]\s|\s(?:de|du|des|da|do|avec|com|en|em|et|e|i|z|ze|w|od|"
                       r"version|versão|wersja|première|primeira|pierwsza|deuxième|segunda|druga|troisième|terceira|"
                       r"trzecia|with|and|in|from|the|first|second|third|which|that|generation)\s)", re.I)


# cabeçalho falado pela voz de IA: "Number ten.", "And number one.", "Number nine is a used car, ...",
# "Number four, and our only fully electric car." (o nome do carro vem na frase seguinte)
_RANK_WORD = r"(?:number|no\.?|#|top|num[ée]ro|n[uú]mero|n[º°o]\.?|numer|nr\.?|miejsce|posi[cç][aã]o)"
_LEAD = re.compile(r"^\s*(?:(?:and|so|now|right|okay|ok|then|et|alors|enfin|e|agora|ent[aã]o|i|a|teraz)[,:]?\s+)+",
                   re.I)
_SPOKEN_RANK = re.compile(rf"^({_RANK_WORD})\s+([^\s,.:;!?]+(?:-[^\s,.:;!?]+)?)", re.I)
_BARE_RANK = re.compile(rf"^{_RANK_WORD}\s*(\d{{1,2}})(?:\s*(?:$|[.!:,;–—-])|\s+(?:is|was|on|in|and|goes|de|est|"
                        rf"[ée]|c['’]est|to|jest|na|w|might|may|could|will|should|has|have|comes|takes|brings)\b)",
                        re.I)
# adjetivos que vêm antes do nome ("the previous Nissan Qashqai", "the current Range Rover")
_BEFORE_NAME = re.compile(r"^(?:(?:(?:newer|later|earlier|updated|revised|original|first|second)\s+)?versions?\s+of\s+|"
                          r"(?:previous|current|latest|new|newer|newest|old|older|original|all-new|outgoing|later|"
                          r"earlier|updated|revised|"
                          r"ancien(?:ne)?|nouveau|nouvelle|actuel(?:le)?|antigo|antiga|novo|nova|atual|"
                          r"poprzedni[ae]?|nowy|nowa|obecny|obecna)\s+|"
                          # "the gas Chevrolet Blazer", "the electric Hyundai Kona" (motorização antes da marca)
                          r"(?:(?:gas|gasoline|petrol|diesel|electric|hybrid|battery)(?:-powered|-electric)?|"
                          r"plug-in(?:\s+hybrid)?|brand-new)\s+"
                          r"(?=(?-i:[A-Z])))+", re.I)
# palavras com maiúscula que não são nome de carro quando abrem a frase ("The Brits ...")
_NOT_NAME = {"this", "that", "these", "those", "it", "its", "there", "here", "our", "your", "my", "his", "her",
             "their", "one", "car", "cars", "model", "brand", "most", "best", "worst", "same", "other", "next",
             "last", "first", "survey", "owner", "owners", "list", "right", "okay", "ok", "so", "well", "now", "yes",
             "no", "and", "but", "then", "remember", "reliability", "price", "voila", "voilà", "bon", "alors", "isso",
             "entao", "pronto", "dobra", "dobrze", "no", "tak", "et", "on", "il", "elle", "je", "vous", "nous", "mais",
             "puis", "chez", "voici", "ce", "cette", "ces", "ca", "ça", "quand", "si", "le", "la", "les", "un", "une",
             "pour", "avec", "sur", "dans", "eu", "ele", "ela", "voce", "nos", "mas", "aqui", "ali"}
_PRIMED: dict[str, tuple[int, str, str]] = {}


# duas listas no mesmo vídeo: "Avoid number four." ... "Buy number nine." (o veredito vira o subtítulo do cartão)
_VERDICT = re.compile(rf"^\s*(?:and\s+|et\s+|e\s+)?(avoid|skip|buy|pick|à\s+éviter|a\s+eviter|évitez|achetez|"
                      rf"evite|compre|fuja)\s+(?={_RANK_WORD})", re.I)


def _verdict(text: str) -> str:
    m = _VERDICT.match(text or "")
    return m.group(1).capitalize() if m else ""


def _digits_rank(text: str) -> str:
    """ "And number ten." -> "Number 10."; "Numéro dix : la Clio" -> "Numéro 10 : la Clio";
    "Avoid number four." -> "number 4."."""
    from .spoken import word_int

    t = _VERDICT.sub("", text.strip(), count=1)
    t = _LEAD.sub("", t, count=1)
    t = re.sub(rf"^(?:la|le|the|o|a)\s+(?={_RANK_WORD}\s)", "", t, flags=re.I)  # "Et la numéro 1."
    m = _SPOKEN_RANK.match(t)
    if m:
        n = word_int(m.group(2))
        if n is not None and 0 < n <= 99:
            t = f"{m.group(1)} {n}{t[m.end():]}"
    return t


def _clean_name(raw: str) -> str:
    """Tira o artigo e o adjetivo da frente: "the previous Nissan Qashqai, ..." -> "Nissan Qashqai, ..."."""
    from .cues import C

    body = re.sub(C("article"), "", raw.strip(), flags=re.I)
    body = _BEFORE_NAME.sub("", body)
    return body


# códigos de motor/câmbio que valem como nome: 1.2, N47, DSG, THP, TCe, DIG-T, EcoBoost, PureTech, BlueHDi, dCi
_TECH = re.compile(r"^(?:\d+(?:[.,]\d+)?[a-z]?|[A-Z][A-Z0-9-]+|[A-Za-z]*\d[\w-]*|[A-Z][a-z]+[A-Z]\w*|[A-Z]{2,}[a-z]{1,2}"
                   r"|[a-z]{1,2}[A-Z]\w*)$")
# palavras genéricas na frente do código ("le moteur 1.6 THP", "la boîte DSG", "le diesel 1.5 BlueHDi")
_GENERIC_LEAD = {"moteur", "moteurs", "bloc", "boite", "boîte", "engine", "engines", "gearbox", "motor", "motores",
                 "cambio", "câmbio", "caixa", "silnik", "skrzynia", "diesel", "essence", "petrol", "gasolina",
                 "tire", "tires", "tyre", "tyres", "pneu", "pneus", "winter", "winters", "hiver"}
# onde o nome acaba dentro da oração ("Renault Zoé avec batterie...", "1.6 THP dans ses premières...")
_NAME_STOP = {"en", "in", "dans", "with", "avec", "which", "that", "qui", "que", "sur", "pour", "for", "sold", "since",
              "depuis", "from", "à", "a", "au", "aux", "entre", "between", "until", "surtout", "especially",
              "notamment", "principalement", "mainly", "produit", "produite", "produced", "built", "vendu", "vendue",
              "com", "em", "na", "no", "para", "w", "z", "od", "do", "dla", "is", "was", "est", "é", "c'est",
              "the", "le", "la", "les", "un", "une", "o", "os", "as", "um", "uma", "first", "première", "premières",
              "primeira", "generation", "génération", "générations", "geração", "version", "versão", "wersja",
              "de", "du", "des", "of", "by", "da", "dos", "das"}
_EQUIPPED = re.compile(r"\s+(?:équipée?s?|equipped\s+with|fitted\s+with|equipad[oa]s?\s+com|wyposażon\w*\s+w)\s+"
                       r"(?:(?:du|de\s+la|de\s+l['’]|des|d['’]|the|o|a)\s*)?", re.I)
_ARTICLES = re.compile(r"\b(?:les|le|la|l['’]|the|os|as|o|a)\s+(?=[A-Z0-9])")


def _clause(text: str) -> str:
    """Até o fim da oração: reticências, ponto (não decimal), ponto e vírgula, dois-pontos, parêntese, travessão."""
    m = re.search(r"…|\.\.\.|;|:|\(|\s[–—-]\s|\.(?!\d)|[!?]", text)
    return text[:m.start()] if m else text


def _words_until_stop(text: str, max_words: int = 4) -> list[str]:
    out: list[str] = []
    for w in text.split():
        core = w.strip(",.;:!?…\"'«»")
        if not core or core.lower() in _NAME_STOP or (out and w.startswith("(")):
            break
        out.append(core)
        if w.endswith(",") or len(out) >= max_words:
            break
    return out


def _has_brand(name: str) -> bool:
    return any(t in BRANDS for t in re.findall(r"[a-z0-9]+", normalize(name)))


def _parse_name(body: str) -> str:
    """Nome no cabeçalho do ranking (carro, motor ou câmbio) já sem artigo:
    "BMW diesel équipées du moteur N47" -> "BMW diesel N47"; "Volkswagen, Audi, Seat et Skoda équipées de la
    boîte DSG à 7 rapports" -> "Volkswagen / Audi / Seat / Skoda DSG"; "moteur 1.6 THP, dans ses premières..."
    -> "1.6 THP"; "diesel 1.5 BlueHDi de Peugeot, Citroën, DS et Opel" -> "Peugeot 1.5 BlueHDi";
    "Range Rover Evoque et le Land Rover Discovery Sport, en diesel" -> "Range Rover Evoque"."""
    from .spoken import clean_model_name

    body = _ARTICLES.sub("", body.strip())
    tech: list[str] = []
    m = _EQUIPPED.search(body)
    if m:  # "les Ford équipées du 1.0 EcoBoost": a marca + o código
        tail = _words_until_stop(_clause(body[m.end():]), 4)
        if len(tail) > 1 and tail[0].lower() in _GENERIC_LEAD and _TECH.match(tail[1]):
            tail = tail[1:]  # "boîte DSG" -> "DSG" (mas "boîte pilotée" fica)
        tech = [t for t in tail if _TECH.match(t)] or tail[:2]
        body = body[:m.start()]
    clause = _clause(body)
    # lista de marcas/carros: "Volkswagen, Audi, Seat et Skoda"; "Citroën et Peugeot"
    items = [x.strip() for x in re.split(r",\s*|\s+(?:et|and|e|i|/)\s+", clause) if x.strip()]
    kept: list[str] = []
    for k, item in enumerate(items):
        words = _words_until_stop(item, 4)
        if not words:
            break
        if k and not (words[0][:1].isupper() or _TECH.match(words[0])):
            break  # ", en diesel 2.0": acabou a lista
        kept.append(" ".join(words))
        if len(words) < len(item.split()):
            break  # o item foi cortado (vem descrição depois)
    if not kept:
        return ""
    first = kept[0].split()
    # "moteur 1.6 THP" / "diesel 1.5 BlueHDi de Peugeot": tira o genérico da frente, a marca vem do "de"
    while len(first) > 1 and first[0].lower() in _GENERIC_LEAD and not first[0][:1].isupper():
        first = first[1:]
    kept[0] = " ".join(first)
    brand = re.search(r"\b(?:de|by|from|da|do|od)\s+([A-Z][\w-]+)", clause)
    if not _has_brand(" ".join(kept)) and brand and normalize(brand.group(1)) in BRANDS:
        kept[0] = f"{brand.group(1)} {kept[0]}"
    if len(kept) > 1 and all(len(x.split()) <= 2 for x in kept):
        name = " / ".join(kept)  # lista de marcas
    else:
        name = kept[0]
    name = " ".join([name, *tech]).strip()
    words = name.split()
    while len(words) > 1 and words[-1].lower() in _GENERIC_LEAD:  # "1.2 PureTech petrol" -> "1.2 PureTech"
        words.pop()
    name = clean_model_name(" ".join(words).strip(" :.;!–—-…,"))
    return name if len([w for w in name.split() if w != "/"]) <= 7 else ""


def _name_ok(name: str) -> bool:
    if not name:
        return False
    first = name.split()[0]
    return is_car_name(name) or first[:1].isupper() or bool(_TECH.match(first))


_NUM_WORDS = {"zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
              "eight": 8, "nine": 9, "ten": 10}
_NW = r"(zero|oh|one|two|three|four|five|six|seven|eight|nine|ten)"
_UNIT = r"(?:[\s-]+lit(?:re|er)s?\b)"


def spoken_engine_names(text: str) -> str:
    """Motor escrito para a voz -> como se escreve: "Ford's one point five and one point six litre EcoBoost" ->
    "Ford 1.5/1.6 EcoBoost"; "the one-litre PureTech" -> "the 1.0 PureTech"; "Ford's two-litre EcoBlue" ->
    "Ford 2.0 EcoBlue"."""
    def dec(m: re.Match) -> str:
        return f"{_NUM_WORDS[m.group(1).lower()]}.{_NUM_WORDS[m.group(2).lower()]}"

    t = re.sub(rf"\b{_NW}\s+point\s+{_NW}\b{_UNIT}?", dec, text, flags=re.I)
    t = re.sub(rf"\b{_NW}{_UNIT}", lambda m: f"{_NUM_WORDS[m.group(1).lower()]}.0", t, flags=re.I)
    t = re.sub(r"\b(\d\.\d)\s*lit(?:re|er)s?\b", r"\1", t, flags=re.I)  # "1.2 litre" -> "1.2"
    t = re.sub(r"\b(\d\.\d)\s+(?:and|or|e|et|ou)\s+(\d\.\d)\b", r"\1/\2", t)  # "1.5 and 1.6" -> "1.5/1.6"
    return re.sub(r"\b([A-Z][\w-]+)['’]s\b", lambda m: m.group(1) if normalize(m.group(1)) in BRANDS else m.group(0), t)


def lookahead_name(text: str) -> str:
    """Frase que só diz o nome do carro (depois de um "Number ten."): "The Range Rover Evoque, the
    current shape, ..." -> "Range Rover Evoque"; "The Mercedes G L E, ..." -> "Mercedes GLE";
    "The Jaecoo Seven." -> "Jaecoo 7"; "Le 1.2 TCe de Renault, produit entre 2012 et 2016." -> "Renault 1.2 TCe";
    "Vous l'attendiez : le 1.2 PureTech à courroie humide." -> "1.2 PureTech";
    "The car from the start of this video." -> ""; "208, 2008, 308, 3008..." -> ""."""
    from .spoken import clean_model_name, hyphen_spelled, word_int

    text = hyphen_spelled(spoken_engine_names(text.strip()))  # "R-X three-fifty" -> "RX 350", "four-Runner" -> "4Runner"
    # "The twenty twenty-five and twenty twenty-six Toyota Camry" -> "Toyota Camry" (o ano do modelo vem antes)
    from .spoken import spoken_years

    text = re.sub(r"^(?:the\s+|a\s+|an\s+)?(?:new\s+|all-new\s+)?(?:(?:19|20)\d\d(?:\s*(?:,|and|to|or|-|–)\s*(?:19|20)\d\d)*"
                  r"\s+)(?=[A-Z])", "", spoken_years(text), flags=re.I)
    colon = text.find(":")
    pre = _parse_name(_clean_name(text[:colon])) if colon > 0 else ""
    if 0 < colon < 40 and (not _name_ok(pre) or normalize(pre.split()[0]) in _NOT_NAME):
        text = text[colon + 1:]  # "Vous l'attendiez : le 1.2 PureTech"
    body = _clean_name(text)
    if body.count(",") >= 3 and len(body.split()) <= body.count(",") * 2 + 3:
        return ""  # lista de modelos ("208, 2008, 308, 3008, 5008...") não é o nome
    clause = re.match(r"[^,.;:!?…(]*", re.sub(r"(?<=\d)\.(?=\d)", "\u2024", body)).group(0).replace("\u2024", ".").strip()
    rest = body[len(clause):].strip()
    closed = not rest or rest[0] in ",.;:!?…("
    words = clause.split()
    if not words or normalize(words[0]) in _NOT_NAME:
        return ""
    take: list[str] = []
    for w in words:
        if not (w[:1].isupper() or w[:1].isdigit() or (take and word_int(w) is not None) or _TECH.match(w)):
            break
        take.append(w)
    if take and len(take) < len(words) and _TECH.match(take[0]):  # "1.2 TCe de Renault": código + marca
        parsed = _parse_name(clause)
        if parsed and (_has_brand(parsed) or closed):
            return parsed
    while len(take) > 1 and take[-1].lower() in _GENERIC_LEAD:
        take.pop()
    if not take:
        return ""
    name = clean_model_name(" ".join(take))
    if len(name.split()) > 5:
        return ""
    if is_car_name(name):
        return name
    # sem marca conhecida só vale se o nome fechar a oração ("The Jaecoo Seven." sim; "The Brits love" não)
    return name if len(take) == len(words) and closed else ""


def _brand_after(texts: list[str]) -> str:
    """A primeira marca citada nas frases seguintes ("On le trouve chez Peugeot", "toutes les Peugeot")."""
    for t in texts:
        for w in re.findall(r"\b[A-Z][\w-]+", t):
            if normalize(w) in BRANDS:
                return w
    return ""


# frase de suspense depois do número ("And this one surprises people.", "The one that surprised me most.",
# "Not the battery."): o motivo vem na frase seguinte
_TEASER = re.compile(r"^(?:and\s+|but\s+)?(?:this|that|the)\s+one\b|"
                     r"^(?:and\s+|but\s+)?(?:this|that|it)(?:\s+is|['’]s)\s+the\s+one\b|^not\s|^(?:and\s+)?here['’]s\b|"
                     r"^(?:all\s+right|right|okay|ok|so|now|well|let['’]s\s+go|allez|bon|alors|beleza|bora)\W*$|"
                     r"^(?:et\s+)?(?:celle-ci|celui-ci|celle-là)\b|^(?:e\s+)?(?:esse|essa|este|esta)\b", re.I)


def _reason(text: str, max_words: int = 10) -> str:
    """Ranking de motivos ("Number five. Charging slows down in the cold."): a frase curta logo depois do
    número vira o título do cartão. Frase longa, pergunta ou de suspense não."""
    t = text.strip()
    words = t.split()
    if not words or t.endswith("?") or _TEASER.match(t) or rank_header_like(t):
        return ""
    if len(words) > max_words:
        # "On a long winter highway drive, a plug-in can burn about as much gas as a regular hybrid."
        head, sep, tail = t.partition(",")
        tail = tail.strip()
        if not sep or not 3 <= len(tail.split()) <= max_words + 2:
            return ""
        t = tail[0].upper() + tail[1:]
    return t.rstrip(".!…").strip()


def _reason_after(texts: list[str]) -> str:
    """O motivo depois do número: pula a frase de suspense e para na primeira frase de verdade."""
    for t in texts:
        if _TEASER.match(t.strip()):
            continue
        return _reason(t)
    return ""


def rank_header_like(text: str) -> bool:
    return bool(_BARE_RANK.match(_digits_rank(text))) or _ordinal_reason(text) is not None


# lista de motivos falada com ordinal: "Première raison : le prix.", "Et la septième raison, c'est peut-être la plus
# intéressante.", "Segunda razão: o aluguel.", "The third reason: reliability."
_ORDINALS = {
    "premiere": 1, "premier": 1, "deuxieme": 2, "second": 2, "seconde": 2, "troisieme": 3, "quatrieme": 4,
    "cinquieme": 5, "sixieme": 6, "septieme": 7, "huitieme": 8, "neuvieme": 9, "dixieme": 10,
    "primeira": 1, "primeiro": 1, "segunda": 2, "segundo": 2, "terceira": 3, "terceiro": 3, "quarta": 4,
    "quarto": 4, "quinta": 5, "quinto": 5, "sexta": 6, "sexto": 6, "setima": 7, "setimo": 7, "oitava": 8,
    "oitavo": 8, "nona": 9, "nono": 9, "decima": 10, "decimo": 10,
    "first": 1, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}
_ORD_REASON = re.compile(r"^(?:(?:et|and|e|enfin|so)\s+)?(?:(?:la|le|a|o|the)\s+)?([^\W\d_]+)\s+"
                         r"(raison|raz[ãa]o|motivo|reason)\b\s*(?:[:–—-]\s*(.+?)|,[^:]*?)?[.!…]*\s*$", re.I)


def _ordinal_reason(text: str) -> tuple[int, str] | None:
    """(posição, o motivo dito na mesma frase ou "") para "Première raison : le prix."."""
    m = _ORD_REASON.match(text.strip())
    if not m:
        return None
    n = _ORDINALS.get(normalize(m.group(1)))
    if not n:
        return None
    # "Et la septième raison, c'est peut-être la plus intéressante.": sem o motivo aqui (vem na frase seguinte)
    rest = (m.group(3) or "").strip(" .!…")
    return n, (rest[:1].upper() + rest[1:]) if rest else ""


def _has_year(text: str) -> bool:
    from .spoken import spoken_years

    return bool(YEAR.search(spoken_years(text or "")))


def prime_headers(texts: list[str]) -> None:
    """Cabeçalhos sem nome ("Number ten.") pegam o nome do carro nas próximas frases. Guarda o
    resultado para ``rank_header`` (que só vê uma frase por vez)."""
    _PRIMED.clear()
    last: tuple[int, int] | None = None  # (frase, número) do último cabeçalho com nome
    for i, text in enumerate(texts):
        ordinal = _ordinal_reason(text)
        if ordinal:  # "Première raison : le prix." / "Et la septième raison, c'est ..." + o motivo na frase seguinte
            name = ordinal[1] or _reason_after(texts[i + 1:i + 3])
            if name:
                _PRIMED[text.strip()] = (ordinal[0], name, "")
                last = (i, ordinal[0])
            continue
        t = _digits_rank(text)
        bare = _BARE_RANK.match(t)
        if bare and last and last[1] == int(bare.group(1)) and i - last[0] <= 2:
            continue  # "Number seven." + "Number seven is the grey area...": o mesmo número, uma vez só
        strict = _strict_header(t)
        if strict and not _has_brand(strict[1]):  # "1.6 THP": a marca está logo depois ("chez Peugeot")
            brand = _brand_after(texts[i + 1:i + 3])
            if brand:
                _PRIMED[text.strip()] = (strict[0], f"{brand} {strict[1]}", strict[2])
        m = _BARE_RANK.match(t)
        if not m or strict:
            continue
        n = int(m.group(1))
        following = []
        named_later = False
        for nxt in texts[i + 1:i + 4]:
            head = _BARE_RANK.match(_digits_rank(nxt))
            if head:
                if int(head.group(1)) == n:
                    # "La numéro 9, maintenant." + "Numéro 9 : les BMW diesel...": o de depois tem o nome e vale
                    named_later = named_later or _strict_header(_digits_rank(nxt)) is not None
                    continue  # "Number ten. Number ten." (a voz repete)
                break
            following.append(nxt)
        if named_later:
            continue
        reason = _reason_after(following)
        first = next((t for t in following if not _TEASER.match(t.strip())), None)
        for nxt in following:
            name = lookahead_name(nxt)
            if not name:
                continue
            carish = is_car_name(name) or is_tire_name(name) or bool(_TECH.match(name.split()[0]))
            if reason and not carish and nxt is not first:
                continue  # "Number seven. The range on the sticker is a summer number. Body." -> o motivo
            if not carish and reason and nxt is first and reason.startswith(name) and len(reason) > len(name):
                name = reason  # "Rubberized, or hard, undercoating." (o motivo inteiro, não só "Rubberized")
            k = texts.index(nxt, i + 1)
            if not _has_brand(name) and _TECH.match(name.split()[0]):  # "1.2 PureTech" -> "Peugeot 1.2 PureTech"
                brand = _brand_after(texts[k:k + 5])
                name = f"{brand} {name}" if brand else name
            if len(name.split()) == 1 and normalize(name) in BRANDS and k + 1 < len(texts):
                fuller = lookahead_name(texts[k + 1])  # "A Honda." + "Honda's one-litre VTEC Turbo, ..."
                if fuller and normalize(fuller.split()[0]) == normalize(name):
                    name = fuller
            if not _has_year(nxt):
                # "The Land Cruiser hiding in a business suit." + "The Lexus G-X four-sixty, twenty ten to ...": a
                # frase com os anos é a que dá o nome de verdade (a outra é a metáfora)
                for later in following[following.index(nxt) + 1:]:
                    alt = lookahead_name(later)
                    # "Honda became the first Japanese automaker... in 1986" não troca "Honda CR-V" por "Honda":
                    # a frase com ano só vale se ela der um nome de verdade (marca + modelo), não só a marca
                    if alt and alt != name and is_car_name(alt) and _has_year(later) and len(alt.split()) >= 2 \
                            and normalize(alt) not in normalize(name):
                        name = alt
                        break
            _PRIMED[text.strip()] = (n, name, _verdict(text))
            last = (i, n)
            break
        else:
            # só "Number seven." / "And number one." (curto): "And number one, by the way, comes from..." é suspense
            if reason and len(text.split()) <= 4:
                _PRIMED[text.strip()] = (n, reason, _verdict(text))
                last = (i, n)


def _strict_header(t: str) -> tuple[int, str, str] | None:
    from .cues import C

    m = re.match(C("rank"), t, re.I)
    if not m:
        return None
    body = _clean_name(m.group(2))
    name = _parse_name(body)
    if not _name_ok(name):
        # "Number 4, and our only..." / "Number 9 is a used car": sem nome aqui (vem na frase seguinte)
        return None
    cut = _NAME_END.search(body)
    rest = body[cut.start():] if cut else ""
    return int(m.group(1)), name, rest.strip(" ,.;:…")


def rank_header(text: str) -> tuple[int, str, str] | None:
    """ "Numéro 4 : la Mazda 6, en version 2.0 essence Skyactiv." -> (4, "Mazda 6", "en version 2.0...");
    "Number ten." (com o nome na frase seguinte, ver ``prime_headers``) -> (10, "Range Rover Evoque", "")."""
    from . import director

    if director.active():  # o diretor de IA leu o roteiro: os cabeçalhos são os dele
        return director.header(text)
    primed = _PRIMED.get(text.strip())
    if primed:
        return primed
    ordinal = _ordinal_reason(text)
    if ordinal:
        return (ordinal[0], ordinal[1], "") if ordinal[1] else None
    strict = _strict_header(_digits_rank(text))
    if strict and not strict[2] and _verdict(text):
        return strict[0], strict[1], _verdict(text)
    return strict


def is_rank_line(text: str) -> bool:
    """Frase que anuncia uma posição do ranking, com ou sem o nome ("Number ten.", "Numéro 3 : la Clio")."""
    t = _digits_rank(text)
    return bool(_strict_header(t) or _BARE_RANK.match(t)) or _ordinal_reason(text) is not None


def search_name(subject: str) -> str:
    """Nome do carro para as buscas (em inglês nos bancos): sem acento e "hybride" -> "hybrid"; motor ou
    câmbio vira a primeira marca + o código + "engine"/"gearbox" ("VW / Audi / Seat / Skoda DSG" ->
    "volkswagen dsg gearbox"; "Peugeot 1.2 PureTech" -> "peugeot 1.2 puretech engine")."""
    if is_component(subject):
        parts = [p.strip() for p in subject.split(" / ")]
        first = normalize(parts[0]).split()
        brand = [w for w in first if w in BRANDS][:1] or first[:1]
        codes = [w for w in normalize(parts[-1]).split() if w not in BRANDS and w not in ("boite", "moteur", "pilotee")]
        gear = re.search(r"boite|gearbox|dsg|edc|eat\d|pilotee|automatique|cambio", normalize(subject))
        return " ".join(dict.fromkeys([*brand, *codes, "gearbox" if gear else "engine"]))
    words = normalize(subject).split()
    return " ".join("hybrid" if w in ("hybride", "hibrido", "hybrydowa", "hybryda", "hybrid") else w for w in words)


# outros nomes do mesmo carro em outros mercados (Honda Jazz = Fit nas Américas e no Japão)
ALIASES: dict[str, tuple[str, ...]] = {
    "honda jazz": ("fit",), "mazda 6": ("atenza", "mazda6"), "suzuki vitara": ("escudo",),
    "toyota prius": ("prius",), "kia venga": ("venga",), "skoda octavia": ("octavia",),
    "toyota corolla": ("corolla",), "honda city": ("city",), "chevrolet onix": ("onix",),
}
# palavras que podem vir depois da marca sem ser nome de modelo ("white lexus sedan", "mazda on the road")
_GENERIC_AFTER_BRAND = {
    "car", "cars", "auto", "autos", "automobile", "vehicle", "vehicles", "suv", "sedan", "saloon", "hatchback",
    "wagon", "estate", "break", "interior", "exterior", "steering", "wheel", "wheels", "dashboard", "engine",
    "logo", "emblem", "badge", "dealership", "dealer", "showroom", "road", "street", "highway", "parked",
    "parking", "driving", "drive", "front", "rear", "back", "side", "view", "detail", "details", "close",
    "closeup", "up", "white", "black", "red", "blue", "silver", "grey", "gray", "green", "yellow", "orange",
    "brown", "beige", "gold", "dark", "light", "hybrid", "electric", "diesel", "new", "old", "used", "classic",
    "on", "in", "at", "of", "the", "a", "an", "and", "with", "is", "for", "to", "by", "near", "photo", "image",
    "file", "jpg", "jpeg", "png", "webp", "motor", "motors", "company", "brand", "sign", "key", "keys", "seat",
    "seats", "trunk", "boot", "light", "lights", "headlight", "headlights", "taillight", "mirror", "door",
}


def other_brands(text: str, allowed_text: str, car: str = "") -> set[str]:
    """Marcas citadas em ``text`` que não são a do carro nem aparecem em ``allowed_text`` (a fala da
    cena). Ex.: foto achada por "peugeot 208 engine" numa cena do Honda Jazz que não cita a Peugeot."""
    toks = set(re.findall(r"[a-z0-9]+", normalize(text)))
    allowed = set(re.findall(r"[a-z0-9]+", normalize(_spelled(f"{allowed_text} {car}"))))
    return {b for b in BRANDS if b in toks and b not in allowed}


def names_car(tags: list[str] | str, car: str) -> bool:
    """As tags (nome da pasta/arquivo) citam este carro? ("honda", "jazz" -> Honda Jazz)."""
    words = re.findall(r"[a-z0-9]+", normalize(car))
    toks = set(re.findall(r"[a-z0-9]+", normalize(tags if isinstance(tags, str) else " ".join(tags))))
    if len(words) < 2:
        return bool(words) and words[0] in toks
    brand, model = words[0], words[1]
    ok = {model, f"{brand}{model}", *ALIASES.get(f"{brand} {model}", ())}
    # "6" sozinho não é o Mazda 6: modelo que é só número precisa da marca junto
    return bool(toks & ok) and (not model.isdigit() or brand in toks or f"{brand}{model}" in toks)


def wrong_model(text: str, subject: str) -> bool:
    """O arquivo nomeia OUTRO modelo da mesma marca do carro da seção: "Dacia Duster" na seção da
    Dacia Logan, "Mazda 3"/"Mazda CX-5" na do Mazda 6, "Lexus RX" na do CT 200h. Marca sem modelo
    ("white lexus sedan", "mazda on the road") passa: quem decide é o revisor visual."""
    words = re.findall(r"[a-z0-9]+", normalize(subject))
    if len(words) < 2 or words[0] not in BRANDS or is_component(subject):
        return False  # seção de motor/câmbio: qualquer modelo da marca serve (o N47 está em vários BMW)
    brand, model = words[0], model_word(subject)
    ok = {model, f"{brand}{model}", *ALIASES.get(f"{brand} {model}", ())}
    toks = re.findall(r"[a-z0-9]+", normalize(text))
    if f"{brand}{model}" in toks or any(t in ok for t in toks if t != brand):
        return False
    for i, tok in enumerate(toks):
        if tok != brand:
            continue
        nxt = next((t for t in toks[i + 1:] if t != brand), "")  # "mazda-mazda-3" -> "3"
        if not nxt or nxt in _GENERIC_AFTER_BRAND or re.fullmatch(r"(19|20)\d\d", nxt):
            continue
        if nxt.startswith(model) or any(nxt.startswith(a) for a in ok):  # "ct200h"
            continue
        return True
    return False


def is_car_name(name: str) -> bool:
    """ "Toyota Corolla", "Range Rover Evoque", "BMW X3" (marca na 1ª ou 2ª palavra)."""
    words = re.findall(r"[a-z0-9]+", normalize(name))
    return bool(words) and (words[0] in BRANDS or len(words) > 1 and words[1] in BRANDS and words[0] in _BRAND_LEAD)


# 1ª palavra de marca com duas palavras ("Range Rover", "Aston Martin", "Alfa Romeo")
_BRAND_LEAD = {"range", "land", "aston", "alfa"}


def sentence_subjects(texts: list[str]) -> list[str]:
    """Carro de cada frase num vídeo de ranking: cada cabeçalho "Número N: <marca> <modelo>" abre
    uma seção que vai até o próximo cabeçalho ou até a despedida ("avant de vous laisser").
    Frases antes do 1º cabeçalho, depois da despedida e na pausa do meio ("So that's five.", "Quick favour")
    ficam sem carro (""). Vídeo com menos de 2 carros diferentes nos cabeçalhos não é ranking: tudo "" (vale o assunto único)."""
    from . import director
    from .cues import C

    plan = director.current()
    if plan is not None and [t.strip() for t in texts] == [t.strip() for t in plan.texts]:
        return plan.per_sentence()  # as seções que o diretor de IA marcou
    prime_headers(texts)
    heads = [rank_header(t) for t in texts]
    cars = {normalize(h[1]) for h in heads if h and (is_car_name(h[1]) or is_tire_name(h[1]))}
    if len(cars) < 2:
        return [""] * len(texts)
    # sequências de cabeçalhos (1, 2, 3, 4 / 4, 3, 2, 1 / 9 ... 1): uma sequência sem nenhum carro é de motivos
    # ("Number one. Salt." ... "Number four. The transmission.") e não vira seção de carro
    not_cars: set[int] = set()
    run: list[int] = []
    for k in [k for k, h in enumerate(heads) if h] + [-1]:
        if k >= 0 and run and abs(heads[k][0] - heads[run[-1]][0]) == 1:
            run.append(k)
            continue
        if run and not any(is_car_name(heads[j][1]) or is_tire_name(heads[j][1]) for j in run):
            not_cars.update(run)
        run = [k] if k >= 0 else []
    out: list[str] = []
    current = ""
    for k, (text, head) in enumerate(zip(texts, heads)):
        if head is not None:
            # a lista é de carros: "Numéro 3 : la Clio" (sem marca) também é um carro, se tiver cara de nome
            name = head[1]
            current = "" if k in not_cars else name if is_car_name(name) or is_tire_name(name) or (
                name[:1].isupper() and len(name.split()) <= 3) else ""
        elif current and (re.search(C("outro"), text, re.I) or re.search(C("pause"), text, re.I)):
            # despedida, ou pausa no meio da contagem ("So that's five.", "Quick favour before the top five"):
            # a recapitulação e o pedido de like não são mais do carro anterior
            current = ""
        out.append(current)
    return out


YEAR = re.compile(r"(?<![0-9])(19[5-9][0-9]|20[0-3][0-9])(?![0-9])")


# "avant 2018" é teto, "à partir de 2015" é piso (pt, fr, pl); ano solto vale para os dois lados
_YEAR_UPPER = re.compile(r"(?:avant|jusqu['’]?(?:en|à)?|antes de|até|przed|do roku|before|until|up to|through)"
                         r"\s+(?:l['’]an\s+)?$", re.I)
_YEAR_LOWER = re.compile(r"(?:[àa] partir de|depuis|apr[eè]s|desde|a partir de|od|po|from|since|after)"
                         r"\s+(?:l['’]an\s+)?$", re.I)


def section_eras(texts: list[str], per_sentence: list[str], before: int = 2, after: int = 5,
                 span: int = 10) -> dict[str, tuple[int, int]]:
    """Anos falados em cada seção -> faixa aceita nas fotos ("entre 2008 et 2015" -> 2006..2020;
    "avant 2018" -> 2008..2023; "à partir de 2015" -> 2013..2025). Seção sem ano fica de fora."""
    from .spoken import spoken_years

    found: dict[str, list[tuple[int, str]]] = {}
    for text, name in zip(texts, per_sentence):
        if not name:
            continue
        text = spoken_years(text)  # "since twenty nineteen" -> "since 2019"
        for m in YEAR.finditer(text):
            head = text[max(0, m.start() - 20):m.start()]
            kind = "up" if _YEAR_UPPER.search(head) else "low" if _YEAR_LOWER.search(head) else ""
            found.setdefault(name, []).append((int(m.group(1)), kind))
    eras: dict[str, tuple[int, int]] = {}
    for name, ys in found.items():
        lows = [y for y, k in ys if k != "up"]
        ups = [y for y, k in ys if k != "low"]
        lo = min(lows) - before if lows else min(ups) - span
        hi = max(ups) + after if ups else max(lows) + span
        eras[name] = (lo, hi)
    return eras


def wrong_era(text: str, era: tuple[int, int] | None) -> bool:
    """A foto traz um ano e todos os anos dela estão fora da faixa (Jazz 1986 numa seção de 2008-2015)."""
    if not era:
        return False
    ys = [int(y) for y in YEAR.findall(text or "")]
    return bool(ys) and all(not era[0] <= y <= era[1] for y in ys)


# geração falada ("troisième génération", "primeira geração", "pierwszej generacji") e a escrita no
# título das fotos ("Honda CR-V (fourth generation)", "3rd gen")
_ORD = {"premi": 1, "1re": 1, "1ere": 1, "1er": 1, "first": 1, "1st": 1, "primeir": 1, "pierwsz": 1,
        "deuxi": 2, "2e": 2, "second": 2, "2nd": 2, "segund": 2, "drug": 2,
        "troisi": 3, "3e": 3, "third": 3, "3rd": 3, "terceir": 3, "trzec": 3,
        "quatri": 4, "4e": 4, "fourth": 4, "4th": 4, "quart": 4, "czwart": 4,
        "cinqui": 5, "5e": 5, "fifth": 5, "5th": 5, "quint": 5, "piat": 5,
        "sixi": 6, "6e": 6, "sixth": 6, "6th": 6, "sext": 6, "szost": 6}
_GEN_WORD = r"(?:g[ée]n[ée]ration|gera[cç][aã]o|generacj\w*|generation|gen)\b"
GEN_SPOKEN = re.compile(r"\b(" + "|".join(sorted(_ORD, key=len, reverse=True)) + r")\w*\s+(?:de\s+)?" + _GEN_WORD, re.I)


def _ordinal(word: str) -> int:
    w = normalize(word)
    return next((n for k, n in sorted(_ORD.items(), key=lambda kv: -len(kv[0])) if w.startswith(normalize(k))), 0)


def section_generations(texts: list[str], per_sentence: list[str]) -> dict[str, int]:
    """Geração citada em cada seção (a primeira menção, que costuma ser o cabeçalho)."""
    gens: dict[str, int] = {}
    for text, name in zip(texts, per_sentence):
        if name and name not in gens and (m := GEN_SPOKEN.search(text)):
            if n := _ordinal(m.group(1)):
                gens[name] = n
    return gens


def wrong_generation(text: str, gen: int | None) -> bool:
    """O título da foto cita outra geração ("fourth generation" numa seção da terceira)."""
    if not gen:
        return False
    found = {_ordinal(m.group(1)) for m in GEN_SPOKEN.finditer(text or "")} - {0}
    return bool(found) and gen not in found


def first_generation_era(gens: dict[str, int], eras: dict[str, tuple[int, int]],
                         newest: int = 2018) -> dict[str, tuple[int, int]]:
    """"Première génération" sem ano: foto de 2019 em diante é outra geração (o ë-Berlingo 2025
    numa seção do Berlingo 1996-2008)."""
    out = dict(eras)
    for name, n in gens.items():
        if n == 1 and name not in out:
            out[name] = (1900, newest)
    return out


def section_names(per_sentence: list[str]) -> list[str]:
    return list(dict.fromkeys(n for n in per_sentence if n))


def assign_scene_subjects(scenes: list[Scene], sentences: list, per_sentence: list[str]) -> None:
    """Cada cena recebe o carro da maioria das suas frases (cenas sem carro ficam com "")."""
    if not any(per_sentence):
        return
    for scene in scenes:
        own = [per_sentence[k] for k, s in enumerate(sentences)
               if scene.start - 0.05 <= (s.start + s.end) / 2 <= scene.end + 0.05 and k < len(per_sentence)]
        scene.analysis.subject = Counter(own).most_common(1)[0][0] if own else ""


def sections_prompt(sentences: list, per_sentence: list[str]) -> str:
    """ "[12]-[25]: Dacia Logan MCV" para o plano de cenas saber o carro de cada trecho."""
    spans: list[list] = []
    for s, name in zip(sentences, per_sentence):
        if spans and spans[-1][2] == name:
            spans[-1][1] = s.index
        else:
            spans.append([s.index, s.index, name])
    lines = [f"  sentences [{a}]-[{b}]: {name}" for a, b, name in spans if name]
    return ("This video is a ranking/list of several car models, one per section. Sections:\n" + "\n".join(lines)
            + "\nA section is NOT a scene: split every section into many scenes (one per idea, about the target "
              "length each: the header, the engine, the seats, the rust check, the price, the verdict...). Each "
              "scene gets its OWN queries about what its sentences say (e.g. 'honda jazz magic seats folded', "
              "'dacia logan mcv rust underbody', 'mechanic checking timing chain'), never the same queries as the "
              "previous scene. When a query shows the car itself it names the model of ITS section, never another "
              "model of the list. A scene may show another car only when its own sentences name it (a "
              "comparison). Scenes outside the sections (intro, outro) use generic footage that matches the "
              "narration.\n")


def _names(query: str, subject: str) -> bool:
    """A busca cita o modelo? ("mazda 6" precisa de "mazda" e "6"; "corolla" basta sozinho)."""
    toks = set(tokens(query))
    model = model_word(subject)
    if len(model) > 2 and not model.isdigit():
        return model in toks
    return set(tokens(subject)) <= toks


def _has_auto_term(query: str, subject_words: set[str]) -> bool:
    toks = set(tokens(query))
    return bool(toks & AUTO_TERMS or toks & subject_words)


# modelo cujo nome é uma palavra comum em inglês: sozinho na busca, os bancos devolvem a coisa ("seal" = foca,
# "dolphin" = golfinho, "model" = modelo de passarela); a âncora vai com a marca ("byd seal")
AMBIGUOUS_MODELS = {"seal", "dolphin", "jazz", "beetle", "mustang", "golf", "polo", "up", "focus", "note", "leaf",
                    "soul", "pride", "rogue", "kicks", "panda", "puma", "jaguar", "model", "pilot", "passport",
                    "ranger", "explorer", "escape", "edge", "journey", "spark", "accord", "civic", "odyssey", "fit",
                    "atto", "han", "tang", "song", "yuan", "seagull", "shark", "spring", "zoe", "captur", "juke",
                    "ka", "kuga", "galaxy", "fiesta", "corsa", "astra", "charger", "challenger", "wrangler"}


# palavras que dizem que a fala é sobre carro (pt, fr, pl, en) — além das marcas e das peças de cada idioma
CAR_TALK = {
    "car", "cars", "vehicle", "vehicles", "suv", "suvs", "sedan", "saloon", "hatchback", "estate", "pickup",
    "engine", "engines", "gearbox", "transmission", "clutch", "brakes", "tyres", "tires", "wheel", "wheels",
    "drive", "driving", "driver", "drivers", "motorway", "road", "roads", "mileage", "miles", "garage",
    "mechanic", "mechanics", "dealer", "dealership", "showroom", "forecourt", "bodywork", "bodyshop", "chassis",
    "battery", "ev", "evs", "hybrid", "diesel", "petrol", "model", "models", "teardown",
    "voiture", "voitures", "vehicule", "vehicules", "moteur", "moteurs", "boite", "embrayage", "freins", "pneus",
    "conduire", "route", "routes", "garagiste", "garagistes", "mecanicien", "concession", "kilometres",
    "carro", "carros", "veiculo", "veiculos", "motor", "cambio", "embreagem", "freios", "pneus", "dirigir",
    "estrada", "oficina", "mecanico", "concessionaria", "quilometros",
    "samochod", "samochody", "silnik", "skrzynia", "hamulce", "opony", "warsztat", "mechanik", "salon",
}


# coisas concretas que não são carro: se a fala cita, a imagem mostra isso (pt, fr, en)
OTHER_TALK = {
    "bank", "banks", "office", "offices", "headquarters", "exhibition", "fair", "expo", "insurer", "insurers",
    "insurance", "analyst", "analysts", "investors", "publisher", "book", "books", "report", "survey", "magazine",
    "newspaper", "paper", "phone", "phones", "electronics", "money", "cash", "price", "bill", "invoice", "contract",
    "university", "government", "minister", "parliament", "court", "factory", "warehouse", "port", "ship",
    "banque", "bureau", "salon", "assureur", "assurance", "livre", "rapport", "enquete", "journal", "argent",
    "facture", "usine", "gouvernement", "banco", "escritorio", "feira", "seguradora", "seguro", "livro",
    "relatorio", "pesquisa", "dinheiro", "fabrica", "governo",
}


def car_scene(scene: Scene) -> bool:
    """A fala desta cena é sobre carro? Aí a imagem tem que mostrar carro. Senão (banco, feira, pessoas,
    bateria, dinheiro, documento...) a imagem mostra o que a fala diz. Vale a decisão da IA no plano
    (``shows``); sem ela, as palavras da própria fala e do carro da seção decidem."""
    a = scene.analysis
    if a.shows in ("car", "other"):
        return a.shows == "car"
    if a.subject:
        return True
    from .cues import CUES
    from .spoken import letters_to_acronyms

    parts = {normalize(p) for lang in CUES.values() for p in lang.get("parts", ())}
    words = set(tokens(letters_to_acronyms(scene.text)))
    if words & (CAR_TALK | set(BRANDS) | parts):
        return True
    # fala genérica ("before we start, let me be straight with you") num canal de carros: mostra carro;
    # só a fala que cita outra coisa concreta (banco, seguradora, feira, livro...) mostra essa coisa
    return not words & OTHER_TALK


def anchor_scenes(scenes: list[Scene], subject: str, niche: str) -> list[str]:
    """Aplica o glossário e ancora as buscas no carro. Devolve o que foi ajustado."""
    notes: list[str] = []
    if not is_automotive(niche):
        return notes
    ranked = sorted({s.analysis.subject for s in scenes if s.analysis.subject})
    for scene in scenes:
        a = scene.analysis
        spoken = set(tokens(scene.text))
        # vídeo de ranking: cada cena usa o carro da sua seção (e evita os outros da lista)
        subject_here = a.subject or ("" if ranked else subject)
        q_name = search_name(subject_here) if a.subject else subject_here.lower()  # "citroen berlingo"
        subject_words = set(tokens(subject_here))
        model = model_word(subject_here)
        if a.subject and len(ranked) > 1:
            others = [o for o in ranked if o != a.subject and not set(tokens(o)) & spoken - {"de"}]
            a.negative_terms = list(dict.fromkeys(a.negative_terms[:8] + [search_name(o) for o in others]))[:12]
        # 1) glossário: o que a palavra ambígua NÃO é vira termo a evitar
        extra = [t for w in spoken if w in GLOSSARY for t in GLOSSARY[w]]
        if extra:
            keep = [t for t in a.negative_terms if t not in extra]
            a.negative_terms = list(dict.fromkeys(keep[:4] + extra + keep[4:]))[:12]
            notes.append(f"cena {scene.index}: glossário ({', '.join(sorted(w for w in spoken if w in GLOSSARY))})")
        # "the battery is covered for eight years" num vídeo de carro: a bateria do carro, não de moto ou pilha
        # (já "batteries for phones and laptops" é cena de outro assunto e fica como está)
        if car_scene(scene) and spoken & {"battery", "batteries", "batterie", "bateria", "baterias", "akumulator"}:
            extra = [t for t in ("motorcycle battery", "aa batteries", "phone battery") if t not in a.negative_terms]
            a.negative_terms = list(dict.fromkeys(a.negative_terms[:6] + extra))[:12]
        if a.query_language != "en":
            continue
        if not car_scene(scene):  # a fala é de outra coisa (banco, feira, bateria): a busca mostra isso
            continue
        # 2) busca sem carro nenhum ganha o assunto ("young generation" -> "corolla young generation")
        # "Mazda 6": o número sozinho não identifica nada, vai o nome inteiro
        # (no ranking vai o nome inteiro: "logan" sozinho acha gente chamada Logan)
        anchor = (model if len(model) > 2 and not model.isdigit() and not a.subject and model not in AMBIGUOUS_MODELS
                  else q_name) or "car"
        # o que a fala cita de verdade (banco, analistas, seguradora, bateria, feira): a busca que mostra isso
        # fica como está ("bank analysts discussing report" não vira "seal bank analysts...")
        said = {w for w in spoken if len(w) > 3 and w not in GLOSSARY and w not in STOPWORDS}
        fixed = []
        for q in a.queries:
            if _has_auto_term(q, subject_words) or any(t[:5] == w[:5] for t in tokens(q) if len(t) > 3 for w in said):
                fixed.append(q)
            else:
                fixed.append(" ".join(f"{anchor} {q}".split()[:5]))
                notes.append(f"cena {scene.index}: busca '{q}' ancorada em '{anchor}'")
        if a.fallback_query and not _has_auto_term(a.fallback_query, subject_words):
            a.fallback_query = f"{q_name or 'car'}"
        # 3) cena que fala do carro ganha uma busca pelo modelo (acha o carro certo nos bancos);
        # no ranking, toda cena da seção é sobre o carro dela, mesmo sem repetir o nome
        if model and (model in spoken or a.subject) and not any(_names(q, subject_here) for q in fixed):
            fixed.insert(min(1, len(fixed)), q_name)
            if a.subject:
                notes.append(f"cena {scene.index}: busca pelo carro da seção ({a.subject})")
        a.queries = list(dict.fromkeys(fixed))[:5]
        # 4) a descrição visual (usada pelo filtro de imagem) também precisa ter o carro
        if not _has_auto_term(a.visual_summary, subject_words) or (a.subject and model not in tokens(a.visual_summary)):
            a.visual_summary = f"{q_name or 'a car'}: {a.visual_summary}".strip(": ")
    if notes:
        log.info("buscas ancoradas no assunto do vídeo", extra={"stage": "subject", "subject": subject,
                                                             "ajustes": len(notes)})
    return notes
