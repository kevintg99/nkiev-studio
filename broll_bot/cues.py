"""Pistas do detector de templates em cada idioma (português, francês, polonês).

Cada chave é uma expressão regular (ou lista) usada por ``auto_templates``. Idioma sem
uma chave usa a do português. Para adicionar um idioma, copie o bloco "pt" e traduza.
"""

from __future__ import annotations

import re

# número falado/escrito: 9,2 · 4.630 · 89 900 (espaço normal ou fino, fr/pl)
NUM = r"(\d{1,3}(?:[   ]\d{3})+(?!\d)|\d{1,3}(?:,\d{3}){2,}(?!\d)|\d+(?:[.,]\d+)?)"

CUES: dict[str, dict] = {
    "pt": {
        "sep_and": r"\s+e\s+|\s+ou\s+",
        "article": r"^(?:os|as|o|a|um|uma|de|da|do)\s+",
        "clause": r"^(?:que|qual|quando|mas|porque|pois)\b",
        "trail": ("de", "da", "do", "que", "e", "com"),
        "lead_and": r"^\s*e\s+",
        "beforeafter": r"\bantes\b.{0,60}\bdepois\b|\bfacelift\b|reestiliza",
        "accel": r"(?:zero|0)\s*(?:a|ao|até|-|–)\s*(?:cem|100)(?:\s*km/?h)?\D{0,40}?" + NUM + r"\s*(?:segundos|s\b)",
        "accel_guard": r"(?:zero|0)\s*(?:a|ao)\s*(?:cem|100)",
        "accel_label": "0 a 100 km/h",
        "topspeed": r"velocidade m[aá]xima\D{0,30}?(\d{3})\s*(?:km/?h|quil[oô]metros por hora)",
        "topspeed_label": "Velocidade máxima",
        "specs": (
            ("Motor", r"\bmotor\s+(\d\.\d(?:\s*(?:turbo|flex|híbrido|hibrido))?)", ""),
            ("Potência", r"\b(\d{2,4})\s*(?:cavalos|cv|hp)\b", "cv"),
            ("Torque", r"\b" + NUM + r"\s*(?:kgfm|kgf\.m|kgf)\b", "kgfm"),
            ("Torque", r"\b(\d{2,4})\s*(?:nm|newton)", "Nm"),
            ("Consumo", r"\b" + NUM + r"\s*(?:km/l|quil[oô]metros por litro)", "km/l"),
            ("Câmbio", r"\bc[aâ]mbio\s+(?:autom[aá]tico\s+)?(cvt|autom[aá]tico|manual|dct)\b", ""),
            ("Porta-malas", r"\b(\d{3})\s*litros\b", "L"),
        ),
        "specs_title": "Ficha técnica",
        "price_cue": r"\b(?:fipe|custa|pre[cç]o|sai por|vale|por cerca de|a partir de)\b",
        "money": r"(r\$\s*)?" + NUM + r"\s*(mil|milh[aãoõ]es)?\s*(reais)?",
        "currency": ("R$ ", ""),
        "fair": r"\bjust[oa]\b|na m[eé]dia|compat[ií]vel",
        "cheap": r"\bbarat[oa]\b|em conta|pechincha",
        "expensive": r"\b(?:é|está|ficou|muito|bem)\s+car[oa]\b|car[oa] demais|salgad",
        "judge_any": r"\bjust[oa]\b|barat|car[oa]",
        "price_label": "Preço",
        "price_special": (("fipe", "Tabela FIPE"),),
        "pos": r"(pontos? fortes?|pontos? positivos?|vantagens?|o que (?:ele )?tem de bom|\bpr[oó]s\b)",
        "neg": r"(pontos? fracos?|pontos? negativos?|desvantagens?|o que (?:ele )?tem de ruim|defeitos?|\bcontras\b)",
        "list_lead": r"^\s*(?:s[aã]o|é|:|ficam? por conta d[aeo]s?|incluem)\s*",
        "score_cue": r"\bnotas?\b",
        "score_final": r"nota final\D{0,12}?(\d{1,2}(?:[.,]\d)?)\b",
        "score_word": r"nota",
        "criteria": (r"conforto|consumo|revenda|pre[cç]o|desempenho|espa[cç]o|acabamento|seguran[cç]a|tecnologia|"
                     r"motor|design|custo[- ]benef[ií]cio|dirigibilidade|conectividade|economia|manuten[cç][aã]o"),
        "verdict": "Veredito",
        "look": (r"\b(?:repare|olha|olhe|veja|note|perceba)\s+(?:bem\s+|s[oó]\s+)?(?:n?[oa]s?|nesses?|nessas?|"
                 r"esses?|essas?)\s+([^:,.;!?]{3,40})"),
        "parts": {"farol", "farois", "faróis", "lanterna", "lanternas", "roda", "rodas", "painel", "volante", "grade",
                  "emblema", "banco", "bancos", "multimídia", "multimidia", "câmbio", "cambio", "retrovisor",
                  "retrovisores", "teto", "porta-malas", "motor", "pneu", "pneus", "para-choque", "parachoque",
                  "capô", "capo", "spoiler", "aerofólio", "freio", "freios", "escapamento", "led", "tela", "console",
                  "maçaneta", "vidro", "vidros", "chave"},
        "news": r"\b(?:anunciou|anunciaram|lançou|divulgou|fez\s+(?:um\s+)?recall|convoca|convocou|not[ií]cia|revelou)\b",
        "first_world": r"(primeir[oa]\s+[^,.]{3,40}?\s+do\s+mundo)",
        "comment": (r"\b(?:inscrit[oa]|seguidor(?:a)?|algu[eé]m|um de voc[eê]s|uma de voc[eê]s)\s+(?:me\s+)?"
                    r"(?:comentou|escreveu|mandou|perguntou|disse)\s*[:\-–,]?\s*(.+)"),
        "poll": r"\bqual\s+(?:voc[eê]\s+)?(?:escolheria|prefere|compraria|levaria|ficaria)\b",
        # cabeçalho de ranking: "Número 8: o Toyota Corolla, geração 2014" -> posição + nome (+ detalhe)
        "rank": r"^\s*(?:n[uú]mero|n[º°o]\.?|top|posi[cç][aã]o)\s*(\d{1,2})\s*[:.–—-]\s*(.{2,240}?)[.!]?\s*$",
        # fim da lista: o que vem depois não é mais sobre o último carro
        # pausa no meio da contagem (recapitulação ou pedido de like): a seção do carro acaba aqui
        "pause": r"^\s*(?:ent[aã]o\s+)?(?:essas?|esses?|esta|este)\s+(?:s[aã]o|foram)\s+(?:as|os)\s+\w+(?:\s+primeir[oa]s)?\s*[.!]?\s*$|\bum favor r[aá]pido\b|\bantes d[oa]s?\s+(?:top|\w+\s+primeir[oa]s)\b",
        "outro": r"\b(?:antes de (?:ir|terminar|encerrar|fechar)|pra fechar|para fechar|para finalizar|pra finalizar|"
                 r"pra terminar|para terminar|resumindo|em resumo|conclus[aã]o)\b",
        # faixa de preço: "entre 50 e 70 mil reais", "de R$ 50 mil a R$ 70 mil"
        "price_range": (r"\b(?:entre|de)\s+(r\$\s*)?" + NUM + r"\s*(mil|milh[aãoõ]es)?\s*(?:reais\s*)?(?:e|a|at[eé])\s+"
                        r"(r\$\s*)?" + NUM + r"\s*(mil|milh[aãoõ]es)?\s*(reais)?"),
        "chapter": (r"^\s*(?:agora|vamos falar d[oa]s?|primeiro|segundo|terceiro|por fim|pra fechar|para fechar)"
                    r"[,:]?\s+(.{2,40}?)[.!]?\s*$"),
        # consumo: "faz 12 km/l", "de 10 a 12 km por litro", "7 litros a cada 100 km" -> (regex, unidade)
        "fuel": (
            (r"\b" + NUM + r"(?:\s*(?:a|e|-|–|ou)\s*" + NUM + r")?\s*(?:km\s*/\s*l\b|km\s*por\s*litro|"
             r"quil[oô]metros?\s*por\s*litro)", "km/l"),
            (r"\b" + NUM + r"(?:\s*(?:a|e|-|–|ou)\s*" + NUM + r")?\s*(?:litros?|l)\s*(?:a cada|por|aos|/)\s*"
             r"(?:cem|100)\s*(?:km|quil[oô]metros)", "L/100 km"),
        ),
        "fuel_where": ((r"\bna cidade|\bno urbano|\burban", "NA CIDADE"), (r"\bna estrada|\brodovi", "NA ESTRADA")),
    },
    "fr": {
        "sep_and": r"\s+et\s+|\s+ou\s+",
        "article": r"^(?:les|le|la|un|une|des|du|de la|de)\s+|^[ld]['’]",
        "clause": r"^(?:qui|que|qu['’]|mais|car|quand|parce)\b",
        "trail": ("de", "du", "des", "la", "le", "qui", "que", "et", "avec"),
        "lead_and": r"^\s*et\s+",
        "beforeafter": r"\bavant\b.{0,60}\bapr[eè]s\b|\bfacelift\b|restyl",
        "accel": (r"(?:z[ée]ro|0)\s*(?:à|a|-|–)\s*(?:cent|100)(?:\s*km/?h)?\D{0,40}?" + NUM +
                  r"\s*(?:secondes|s\b)"),
        "accel_guard": r"(?:z[ée]ro|0)\s*(?:à|a)\s*(?:cent|100)",
        "accel_label": "0 à 100 km/h",
        "topspeed": r"vitesse (?:de pointe|maximale|max)\D{0,30}?(\d{3})\s*(?:km/?h|kilom[eè]tres[- ]heure)",
        "topspeed_label": "Vitesse maximale",
        "specs": (
            ("Moteur", r"\bmoteur\s+(\d\.\d(?:\s*(?:turbo|hybride|essence|diesel))?)", ""),
            ("Puissance", r"\b(\d{2,4})\s*(?:chevaux|ch|cv)\b", "ch"),
            ("Couple", r"\b(\d{2,4})\s*(?:nm|newton)", "Nm"),
            ("Consommation", r"\b" + NUM + r"\s*(?:l/100\s*km|litres aux cent)", "l/100 km"),
            ("Boîte", r"\bbo[iî]te\s+(?:de vitesses\s+)?(cvt|automatique|manuelle|dct|edc)\b", ""),
            ("Coffre", r"\b(\d{3})\s*litres\b", "L"),
        ),
        "specs_title": "Fiche technique",
        "price_cue": r"\b(?:co[uû]te|prix|tarif|vaut|[aà] partir de|affich[ée]e?|vendue?|argus)\b",
        "money": r"()" + NUM + r"\s*(mille|millions?)?\s*(euros?|€)?",
        "currency": ("", " €"),
        "fair": r"\b(?:juste|correct|raisonnable|coh[ée]rent|dans la moyenne)\b",
        "cheap": r"\b(?:bon march[ée]|pas cher|abordable|une affaire)\b",
        "expensive": r"\b(?:trop cher|cher|ch[eè]re|sal[ée]e?|hors de prix)\b",
        "judge_any": r"\b(?:juste|correct|raisonnable|march[ée]|abordable|cher|ch[eè]re|sal[ée])",
        "price_label": "Prix",
        "price_special": (("argus", "Cote Argus"),),
        "pos": r"(points? forts?|avantages?|les plus\b|qualit[ée]s?)",
        "neg": r"(points? faibles?|inconv[ée]nients?|d[ée]fauts?|les moins\b)",
        "list_lead": r"^\s*(?:sont|est|:|ce sont|incluent)\s*",
        "score_cue": r"\bnotes?\b",
        "score_final": r"note (?:finale|globale)\D{0,12}?(\d{1,2}(?:[.,]\d)?)\b",
        "score_word": r"note",
        "criteria": (r"confort|consommation|revente|prix|performances?|espace|finition|s[ée]curit[ée]|technologie|"
                     r"moteur|design|fiabilit[ée]|habitabilit[ée]|conduite|[ée]quipement"),
        "verdict": "Verdict",
        "look": (r"\b(?:regardez|remarquez|observez|admirez|voyez|jetez un (?:œil|oeil) (?:à|aux?))\s+"
                 r"(?:bien\s+)?(?:les?\s+|la\s+|l['’]|ces?\s+|cette\s+)?([^:,.;!?]{3,40})"),
        "parts": {"phare", "phares", "feux", "feu", "jantes", "jante", "roues", "roue", "tableau", "volant",
                  "calandre", "logo", "sièges", "siège", "écran", "ecran", "boîte", "boite", "rétroviseurs",
                  "rétroviseur", "toit", "coffre", "moteur", "pneus", "pneu", "pare-chocs", "capot", "led",
                  "console", "freins", "vitres", "clé", "cle", "intérieur", "interieur"},
        # "annonce" sozinho é o anúncio de venda ("sur l'annonce, cherchez..."), não notícia
        "news": (r"\b(?:a annonc[ée]|vient d['’]annoncer|annonce que|a lanc[ée]|vient de lancer|campagne de rappel|"
                 r"actualit[ée]|a d[ée]voil[ée]|d[ée]voile|a pr[ée]sent[ée])\b"),
        "first_world": r"(premi[eè]re?\s+[^,.]{3,40}?\s+(?:au|du)\s+monde)",
        "comment": (r"\b(?:un abonn[ée]e?|une abonn[ée]e|un spectateur|une spectatrice|quelqu['’]un|l['’]un de vous|"
                    r"un de vous)\s+(?:m['’]|nous\s+)?(?:a [ée]crit|a comment[ée]|a demand[ée]|a dit)\s*[:\-–,]?\s*(.+)"),
        "poll": (r"\b(?:lequel|laquelle|quelle voiture|quel mod[eè]le)\s+(?:vous\s+)?(?:choisiriez|prendriez|"
                 r"pr[ée]f[ée]rez|ach[eè]teriez)(?:[- ]vous)?\b|\bvous choisiriez (?:laquelle|lequel)\b"),
        "rank": r"^\s*(?:num[ée]ro|n[º°o]\.?|top)\s*(\d{1,2})\s*[:.–—-]\s*(.{2,240}?)[.!]?\s*$",
        "pause": r"^\s*(?:et\s+)?voil[aà]\s+pour\s+(?:les|nos)\s+\w+(?:\s+premi[eè]res?)?\s*[.!]?\s*$|\bpetit service\b|\bavant le top\b",
        "outro": r"\b(?:avant de vous laisser|avant de (?:partir|conclure|terminer)|pour (?:conclure|terminer)|"
                 r"en conclusion|en r[ée]sum[ée])\b",
        "price_range": (r"\b(?:entre|de)\s+()" + NUM + r"\s*(mille|millions?)?\s*(?:euros?\s*|€\s*)?(?:et|à|a)\s+()"
                        + NUM + r"\s*(mille|millions?)?\s*(euros?|€)"),
        "chapter": (r"^\s*(?:maintenant|passons (?:à|au|aux)|parlons (?:de|du|des)|d['’]abord|ensuite|enfin|"
                    r"pour finir)[,:]?\s+(.{2,40}?)[.!]?\s*$"),
        # "5 litres aux cent", "8 à 9 litres aux 100", "6 l/100 km" (nunca "un deux-litres")
        "fuel": (
            (r"\b" + NUM + r"(?:\s*(?:à|a|-|–|ou|et)\s*" + NUM + r")?\s*(?:litres?|l)\s*(?:aux\s*(?:cent|100)|"
             r"/\s*100|pour\s*(?:cent|100)\s*(?:km|kilom[eè]tres))", "L/100 km"),
            (r"\b" + NUM + r"(?:\s*(?:à|a|-|–|ou|et)\s*" + NUM + r")?\s*(?:km\s*/\s*l\b|kilom[eè]tres?\s*par\s*litre)",
             "km/l"),
        ),
        "fuel_where": ((r"\ben ville\b|\burbain", "EN VILLE"), (r"\bsur (?:route|autoroute)|\bautoroute", "SUR ROUTE")),
    },
    "pl": {
        "sep_and": r"\s+i\s+|\s+oraz\s+|\s+lub\s+|\s+albo\s+|\s+czy\s+",
        "article": r"^(?!)",
        "clause": r"^(?:który|która|które|co|ale|bo|gdy|kiedy|ponieważ)\b",
        "trail": ("i", "z", "na", "w", "do", "który", "która", "które"),
        "lead_and": r"^\s*i\s+",
        "beforeafter": r"\bprzed\b.{0,60}\bpo\b|\bfacelift|\blifting",
        "accel": (r"(?:od\s+)?(?:zera|0)\s*(?:do|-|–)\s*(?:stu|setki|100)(?:\s*km/?h)?\D{0,40}?" + NUM +
                  r"\s*(?:sekund[ay]?|s\b)"),
        "accel_guard": r"(?:zera|0)\s*(?:do|-|–)\s*(?:stu|setki|100)",
        "accel_label": "0-100 km/h",
        "topspeed": r"pr[eę]dko[sś][cć] maksymaln\w*\D{0,30}?(\d{3})\s*(?:km/?h|kilometr)",
        "topspeed_label": "Prędkość maksymalna",
        "specs": (
            ("Silnik", r"\bsilnik\w*\s+(\d\.\d(?:\s*(?:turbo|hybryd\w*|benzyn\w*|diesel))?)", ""),
            ("Moc", r"\b(\d{2,4})\s*(?:(?-i:KM)(?!/)\b|koni)", "KM"),
            ("Moment obrotowy", r"\b(\d{2,4})\s*(?:nm|niutonometr)", "Nm"),
            ("Spalanie", r"\b" + NUM + r"\s*(?:l/100\s*km|litra? na (?:sto|100))", "l/100 km"),
            ("Skrzynia", r"\bskrzyni\w*\s+(?:biegów\s+)?(cvt|automatyczn\w*|manualn\w*|dsg|dct)\b", ""),
            ("Bagażnik", r"\b(\d{3})\s*(?:litrów|litry|l)\b", "L"),
        ),
        "specs_title": "Dane techniczne",
        "price_cue": r"\b(?:kosztuje|kosztować|cena|cenę|ceny|cenie|wyceniony|zapłacić|zapłacimy)\b",
        "money": r"()" + NUM + r"\s*(tys\.?|tysi[aą]c\w*|milion\w*)?\s*(z[łl]\w*|pln)?",
        "currency": ("", " zł"),
        "fair": r"\b(?:uczciw\w*|rozsądn\w*|w porządku|adekwatn\w*|w normie)\b",
        "cheap": r"\b(?:tani\w*|tanio|okazj\w*|przystępn\w*)\b",
        "expensive": r"\b(?:drogi|droga|drogo|za drogi|przepłacon\w*)\b",
        "judge_any": r"\b(?:uczciw|rozsądn|porządku|tani|tanio|okazj|drog|przepłac)",
        "price_label": "Cena",
        "price_special": (),
        "pos": r"(zalet\w*|plusy|plusem|mocn\w* stron\w*|atut\w*)",
        "neg": r"(wady|wadą|wadami|minusy|minusem|słab\w* stron\w*)",
        "list_lead": r"^\s*(?:to|są|jest|:|należą|zaliczam)\s*",
        "score_cue": r"\b(?:ocen\w*|not\w*|punkt\w*)\b",
        "score_final": (r"(?:ocen\w*|not\w*)\s+(?:ko[nń]cow\w*|og[oó]ln\w*)\D{0,12}?(\d{1,2}(?:[.,]\d)?)\b|"
                        r"ko[nń]cow\w*\s+ocen\w*\D{0,12}?(\d{1,2}(?:[.,]\d)?)\b"),
        "score_word": r"(?:ocena|na)",
        "criteria": (r"komfort|spalanie|cen[aę]|osi[aą]gi|przestrze[nń]|wyko[nń]czenie|bezpiecze[nń]stwo|"
                     r"technologi[aę]|silnik|design|niezawodno[sś][cć]|prowadzenie|wyposa[zż]enie|"
                     r"warto[sś][cć] rezydualn\w*|utrata warto[sś]ci"),
        "verdict": "Werdykt",
        "look": (r"\b(?:spójrzcie|spójrz|zobaczcie|zobacz|zwróćcie uwagę|zwróć uwagę|popatrzcie)\s+(?:na\s+)?"
                 r"([^:,.;!?]{3,40})"),
        "parts": {"reflektory", "reflektorów", "reflektor", "światła", "swiatla", "felgi", "koła", "kola", "deska",
                  "deskę", "kierownica", "kierownicę", "grill", "atrapa", "logo", "fotele", "fotel", "ekran",
                  "skrzynia", "lusterka", "dach", "bagażnik", "silnik", "opony", "zderzak", "maska", "maskę", "led",
                  "konsola", "hamulce", "szyby", "wnętrze", "kluczyk"},
        "news": (r"\b(?:ogłosił\w*|zaprezentował\w*|wprowadził\w*|przedstawił\w*|akcj\w* serwisow\w*|wezwani\w*|"
                 r"wiadomo[sś][cć]|nowo[sś][cć])"),
        "first_world": r"(pierwsz\w+\s+[^,.]{3,40}?\s+na\s+[sś]wiecie)",
        "comment": (r"\b(?:widz\w*|subskrybent\w*|ktoś|jeden z was|jedna z was)\s+(?:mi\s+)?"
                    r"(?:napisał\w*|skomentował\w*|zapytał\w*|powiedział\w*)\s*[:\-–,]?\s*(.+)"),
        "poll": (r"\b(?:który|którą|które|co)\s+(?:(?:wy|ty)\s+)?(?:wybralibyście|wybrałbyś|wybrałabyś|wybierzecie|"
                 r"wybierasz|byście wybrali|kupilibyście|wolicie)\b"),
        "rank": r"^\s*(?:numer|nr\.?|miejsce|top)\s*(\d{1,2})\s*[:.–—-]\s*(.{2,240}?)[.!]?\s*$",
        "pause": r"\bzanim przejdziemy do (?:pierwszej|najlepszej)\b|\bma[łl]a pro[sś]ba\b",
        "outro": r"\b(?:na koniec|zanim (?:się pożegnam|skończę|skończymy|zakończę)|podsumowując|w skrócie)\b",
        "price_range": (r"\b(?:od|między|miedzy)\s+()" + NUM + r"\s*(tys\.?|tysi[aą]c\w*|milion\w*)?\s*(?:z[łl]\w*\s*)?"
                        r"(?:do|a)\s+()" + NUM + r"\s*(tys\.?|tysi[aą]c\w*|milion\w*)?\s*(z[łl]\w*|pln)"),
        "chapter": (r"^\s*(?:teraz|przejdźmy do|czas na|najpierw|na koniec|pora na)[,:]?\s+"
                    r"(.{2,40}?)[.!]?\s*$"),
        # "6 litrów na sto", "od 7 do 8 l/100 km"
        "fuel": (
            (r"\b" + NUM + r"(?:\s*(?:do|-|–|lub|albo)\s*" + NUM + r")?\s*(?:l|litr\w*)\s*(?:/\s*100|na\s*"
             r"(?:sto|setk[eę]|100))", "l/100 km"),
        ),
        "fuel_where": ((r"\bw mie[sś]cie", "W MIEŚCIE"), (r"\bw trasie|\bna trasie|\bautostrad", "W TRASIE")),
    },
    "en": {
        "sep_and": r"\s+and\s+|\s+or\s+|\s+vs\.?\s+|\s+versus\s+",
        "article": r"^(?:the|a|an)\s+",
        "clause": r"^(?:that|which|who|when|but|because|since)\b",
        "trail": ("of", "the", "and", "with", "that", "a", "an", "to"),
        "lead_and": r"^\s*and\s+",
        "beforeafter": r"\bbefore\b.{0,60}\bafter\b|\bfacelift\b|\brefresh\b",
        "accel": (r"(?:zero|0)\s*(?:to|-|–)\s*(?:sixty|60|100)(?:\s*(?:mph|km/?h))?\D{0,40}?" + NUM +
                  r"\s*(?:seconds|secs?|s\b)"),
        "accel_guard": r"(?:zero|0)\s*(?:to|-|–)\s*(?:sixty|60|100)",
        "accel_label": "0-60 mph",
        "topspeed": r"top speed\D{0,30}?(\d{3})\s*(?:mph|km/?h|miles per hour)",
        "topspeed_label": "Top speed",
        "specs": (
            ("Engine", r"\b(\d\.\d(?:\s*(?:liter|litre|l)\b)?(?:\s*(?:turbo|hybrid|diesel|v6|v8))?)\s*(?:engine|motor)", ""),
            ("Power", r"\b(\d{2,4})\s*(?:horsepower|hp|bhp|ps)\b", "hp"),
            ("Torque", r"\b(\d{2,4})\s*(?:lb-?ft|pound-?feet|nm|newton)", "lb-ft"),
            ("Fuel economy", r"\b" + NUM + r"\s*(?:mpg|miles per gallon)", "mpg"),
            ("Gearbox", r"\b(cvt|automatic|manual|dct|dual-clutch)\s+(?:gearbox|transmission)\b", ""),
            ("Trunk", r"\b(\d{3})\s*(?:liters|litres|l)\s+(?:of\s+)?(?:trunk|boot|cargo)", "L"),
        ),
        "specs_title": "Specs",
        "price_cue": r"\b(?:costs?|price[sd]?|priced|sells? for|worth|starting at|from about|msrp|book value)\b",
        "money": r"([$£€]\s*)?" + NUM + r"\s*(k|grand|thousand|million)?\s*(dollars?|bucks|pounds?|euros?)?",
        "currency": ("$", ""),
        "fair": r"\b(?:fair|reasonable|about right|good value|decent)\b",
        "cheap": r"\b(?:cheap|bargain|a steal|affordable|budget)\b",
        "expensive": r"\b(?:expensive|overpriced|pricey|too much|costly)\b",
        "judge_any": r"\b(?:fair|reasonable|cheap|bargain|steal|affordable|expensive|overpriced|pricey)",
        "price_label": "Price",
        "price_special": (("kelley blue book", "Kelley Blue Book"), ("kbb", "Kelley Blue Book")),
        "pos": r"(pros\b|strengths?|strong points?|advantages?|what (?:it|this car) does (?:well|right)|the good)",
        "neg": r"(cons\b|weaknesses|weak points?|downsides?|drawbacks?|what (?:it|this car) does wrong|the bad)",
        "list_lead": r"^\s*(?:are|is|:|include|come down to)\s*",
        "score_cue": r"\b(?:scores?|ratings?|grades?)\b",
        "score_final": r"(?:final|overall)\s+(?:score|rating|grade)\D{0,12}?(\d{1,2}(?:[.,]\d)?)\b",
        "score_word": r"(?:score|rating)",
        "criteria": (r"comfort|fuel economy|resale|price|performance|space|build quality|safety|technology|"
                     r"engine|design|reliability|handling|equipment|value for money|maintenance"),
        "verdict": "Verdict",
        "look": (r"\b(?:look at|check out|notice|take a look at|see)\s+(?:the\s+|these\s+|this\s+|those\s+)?"
                 r"([^:,.;!?]{3,40})"),
        "parts": {"headlight", "headlights", "taillights", "taillight", "wheels", "wheel", "rims", "dashboard",
                  "dash", "steering", "grille", "logo", "badge", "seats", "seat", "screen", "touchscreen",
                  "gearbox", "transmission", "mirrors", "mirror", "roof", "trunk", "boot", "engine", "tires", "tyres",
                  "bumper", "hood", "bonnet", "spoiler", "brakes", "exhaust", "led", "console", "door", "doors",
                  "key", "interior", "turbo", "chain", "belt", "battery", "clutch", "chassis", "suspension"},
        # "good news"/"bad news" é jeito de falar, não notícia
        # "Ford recalled 600,000 vehicles" é notícia; a palavra "recall" solta ("a recall is not proof") não
        "news": (r"\b(?:announced|launched|unveiled|revealed|recalled|issued\s+a\s+recall|breaking|"
                 r"(?<!good )(?<!bad )(?<!great )(?<!better )(?<!worse )(?<!some )news)\b"),
        "first_world": r"(world['’]s first\s+[^,.]{3,40}|first\s+[^,.]{3,40}?\s+in the world)",
        "comment": (r"\b(?:a (?:viewer|subscriber)|someone|one of you)\s+(?:asked|commented|wrote|said)\s*"
                    r"[:\-–,]?\s*(.+)"),
        "poll": r"\b(?:which (?:one )?would you (?:pick|choose|buy|take)|what would you (?:pick|choose|buy))\b",
        "rank": r"^\s*(?:number|no\.?|#|top)\s*(\d{1,2})\s*[:.–—-]\s*(.{2,240}?)[.!]?\s*$",
        "pause": r"^\s*(?:so\s+)?that'?s\s+(?:the\s+first\s+)?(?:two|three|four|five|six|seven|eight|nine|ten|\d+)(?:\s+down)?\s*[.!]?\s*$|\bquick favou?r\b|\bbefore the top (?:three|five|ten|\d+)\b",
        "outro": r"\b(?:before you go|before we wrap up|to wrap (?:things )?up|in conclusion|to sum (?:it )?up|"
                 r"to summarize|bottom line)\b",
        "price_range": (r"\b(?:between|from)\s+([$£€]\s*)?" + NUM + r"\s*(k|grand|thousand|million)?\s*"
                        r"(?:dollars?\s*|pounds?\s*|euros?\s*)?(?:and|to|-|–)\s+([$£€]\s*)?" + NUM +
                        r"\s*(k|grand|thousand|million)?\s*(dollars?|bucks|pounds?|euros?)?"),
        "chapter": (r"^\s*(?:now|let['’]s talk about|first|second|third|next|finally|to wrap up)[,:]?\s+"
                    r"(.{2,40}?)[.!]?\s*$"),
        # "gets 40 mpg", "35 to 40 miles per gallon", "6 liters per 100 km"
        "fuel": (
            (r"\b" + NUM + r"(?:\s*(?:to|-|–|or)\s*" + NUM + r")?\s*(?:mpg\b|miles per gallon)", "mpg"),
            (r"\b" + NUM + r"(?:\s*(?:to|-|–|or)\s*" + NUM + r")?\s*(?:liters?|litres?|l)\s*(?:per|/|every)\s*"
             r"(?:100|hundred)\s*(?:km|kilometers|kilometres)", "L/100 km"),
        ),
        "fuel_where": ((r"\bin (?:the )?city\b|\bin town\b|\burban\b", "IN THE CITY"),
                       (r"\bon the (?:highway|motorway|freeway)\b|\bhighway\b|\bmotorway\b", "ON THE HIGHWAY")),
    },
}

_lang = "pt"
_currency: tuple[str, str] | None = None  # moeda padrão do canal (perfil em canais/)
CURRENCY_SYMBOLS = {"£": ("£", ""), "$": ("$", ""), "€": ("", " €"), "r$": ("R$ ", ""), "zł": ("", " zł"),
                    "zl": ("", " zł"), "c$": ("C$", ""), "cad": ("C$", "")}


def dollar_symbol() -> str:
    """ "dollars" na fala: "C$" num canal em dólar canadense, "$" nos outros."""
    return "C$" if _currency is not None and _currency[0] == "C$" else "$"


def set_currency(symbol: str | None) -> None:
    """Moeda padrão dos preços quando a fala não diz qual ("" = a do idioma). A falada sempre vence."""
    global _currency
    _currency = CURRENCY_SYMBOLS.get((symbol or "").strip().lower()) if symbol else None


def set_language(lang: str) -> None:
    global _lang
    lang = (lang or "pt").split("-")[0].lower()
    _lang = lang if lang in CUES else "pt"


def language() -> str:
    return _lang


def C(key: str):  # noqa: N802 - curto de propósito: usado em todas as regras
    if key == "currency" and _currency is not None:
        return _currency
    table = CUES.get(_lang, CUES["pt"])
    return table[key] if key in table else CUES["pt"][key]


# fala de notícia/reportagem (o Broadcast só põe "BREAKING NEWS", "AO VIVO" e "REPORTAGEM" quando a fala
# é isso): anúncio, recall, lançamento, investigação, "segundo X", números oficiais, pesquisa publicada.
# Sem isso, os templates de TV usam a versão de análise do estúdio ("PONTO-CHAVE", "ANÁLISE").
_NEWS_TALK = re.compile(
    r"\b(?:announc\w*|launch(?:ed|es)?|unveil\w*|reveal(?:ed|s)|recall\w*|breaking|reported|reports?\s+(?:say|of)|"
    r"published|investigation|according\s+to|figures\s+(?:from|show)|survey|statement|confirmed|officially|"
    r"registrations?|registered|press\s+release|this\s+(?:week|month)|(?<!good\s)(?<!bad\s)(?<!great\s)news|"
    # francês
    r"a\s+annonc[ée]|vient\s+d['’]annoncer|annonce\s+que|annoncent|a\s+lanc[ée]|lancement|d[ée]voil\w*|"
    r"campagne\s+de\s+rappel|enqu[êe]te|selon|d['’]apr[èe]s|publi[ée]\w*|"
    r"immatricul\w*|officiel\w*|communiqu[ée]|cette\s+semaine|actualit[ée]|"
    # português
    r"anunciou|anunciaram|anuncia\s+que|lançou|lançamento|revelou|divulg\w*|convoca\w*|segundo\s+(?:a|o|os|as|dados)|de\s+acordo\s+com|"
    r"investiga[çc][ãa]o|pesquisa|emplacamento\w*|oficialmente|nesta\s+semana|not[íi]cia|"
    # polonês
    r"ogłosi\w*|zaprezentowa\w*|wezwani\w*|według|raport\w*|rejestracj\w*|oficjaln\w*)\b", re.I)


def is_news_talk(text: str) -> bool:
    """A fala é notícia/reportagem? ("A Reuters investigation in 2023 quoted experts", "BYD sold its first car
    here in March 2023", "Toyota announced a recall"). "Fewer parts is good news" não é."""
    return bool(_NEWS_TALK.search(text or ""))
