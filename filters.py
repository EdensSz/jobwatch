"""Règles de jobwatch : lieu, niveau, métier, langues, années d'expérience, et note /10 décomposée.

La note est la somme de 4 critères, chacun expliqué en clair (affichés dans l'interface) :
  Métier  /4  : le poste correspond-il à ce que tu cherches (IR, distribution, coverage, produit…) ?
  Niveau  /3  : junior (0-2 ans) = 3 ; 3 ans ou « Associate » = 2 ; « Manager » sans années = 1
  Secteur /2  : gestion d'actifs / private markets / wealth = 2 ; banque, assurance, fintech = 1 ; autre = 0
  Atouts  /1  : un plus propre à ton profil (français apprécié, VIE, groupe français, private markets)
  Malus       : -1 si une autre langue est « souhaitée » (une langue exigée = offre exclue)
"""
import html
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

RULES_VERSION = 3          # change = toutes les offres sont réévaluées au passage suivant

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10,
         "un": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5}


@dataclass
class Job:
    source: str
    company: str
    id: str
    title: str
    location: str
    url: str
    posted: Optional[str] = None          # AAAA-MM-JJ si connu
    description: str = ""
    desc_loader: Optional[Callable[[], str]] = None
    extra_location: str = ""              # lieux supplémentaires connus après lecture du détail

    @property
    def key(self) -> str:
        return f"{self.source}::{self.id}"


@dataclass
class Verdict:
    keep: bool
    reason: str = ""
    score: int = 0
    rating: str = ""
    flags: List[str] = field(default_factory=list)
    min_years: Optional[int] = None
    chinese: str = "unknown"              # "non" | "souhaité" | "requis" | "unknown"
    other_langs: List[str] = field(default_factory=list)   # ex. ["japonais requis", "coréen souhaité"]
    note10: Optional[int] = None
    why: List[str] = field(default_factory=list)
    excerpt: str = ""
    description: str = ""
    summary: str = ""
    note_source: str = "règles"
    ai_model: str = ""
    parts: List[Dict] = field(default_factory=list)       # [{k, label, pts, max, why}]
    role: str = ""                        # famille du poste, ex. « Distribution / vente de fonds »
    level: str = ""                       # ex. « Junior (0-2 ans) »
    langs_ok: bool = True                 # aucune autre langue que français / anglais demandée
    pros: List[str] = field(default_factory=list)
    cons: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------- titres
SENIOR_RE = re.compile(
    r"\b(vp|svp|evp|avp|vice[- ]president|assistant vice president|director|md|ed|"
    r"managing director|executive director|head|chief|principal|partner|president|lead|senior|sr|"
    r"general manager|team leader|directeur|directrice|responsable|chef|expert|staff|"
    r"部長|課長|マネージャー)\b", re.I)

JUNIOR_RE = re.compile(
    r"\b(analyst|analyste|assistant|trainee|graduate|intern|internship|junior|jr|vie|v\.i\.e\.?|"
    r"stagiaire|apprenti|alternan\w*|coordinator|entry[- ]level|early careers?|campus|"
    r"management associate|officer)\b", re.I)

# Titres hors-sujet : technique, risque, juridique, back-office, trading pur, retail...
TITLE_NEG_RE = re.compile(
    r"engineer|developer|software|devops|data (scientist|engineer)|\bquant|actuar|"
    r"audit|account(ant|ing)\b|\btax\b|legal|counsel|compliance|\brisk\b|\baml\b|\bkyc\b|financial crime|"
    r"\bhr\b|human resources|people partner|talent|recruit|payroll|facilit|"
    r"settlement|collateral|reconciliation|trade support|middle office|back office|"
    r"fund accounting|transfer agen|custody|corporate action|operations|\bops\b|"
    r"technolog|\bit\b|cyber|infrastructure|security|architect|"
    r"market maker|structur(er|ing)|research (analyst|associate)|"
    r"equity research|portfolio manager|fund manager|treasury|financial reporting|"
    r"financial (accountant|controller)|\bfp&a|controller|credit (analyst|risk|specialist)|"
    r"lending|\bloan|mortgage|underwrit|branch|teller|customer service officer|contact cent|call cent|"
    r"personal banking|premier|insurance agent|financial planner|agency|\bagent\b|"
    r"communications|\bpr\b|designer|\bux\b|graphic|content|copywriter|"
    r"administrative|executive assistant|secretary|receptionist|office manager|personal assistant|"
    r"investment bank(er|ing)|leveraged|m&a|mergers|"
    r"procurement|supply chain|logistic|warehouse|driver|nurse|"
    r"comptab|juridique|ressources humaines|informatique|d[ée]veloppeur|ing[ée]nieur|"
    r"contr[ôo]le|conformit[ée]|tr[ée]sorerie", re.I)
TRADING_RE = re.compile(r"\btrad(er|ing)\b", re.I)

TITLE_CHINA_RE = re.compile(r"\b(china|chinese|greater china|mainland|prc|taiwan|japan desk|korea desk)\b|中国|大中华", re.I)

# --------------------------------------------------------------------------- métiers (critère /4)
# (libellé, points, motif dans l'intitulé, motif dans la description)
FAMILIES = [
    ("Relations investisseurs / levée de fonds", 4,
     r"investor relations|\bir\b|fundrais|capital (raising|formation|introduction)|relations? investisseurs?|\blp relations",
     r"investor relations|fundrais\w*|capital raising|limited partners?|\blps?\b|investor (reporting|enquir\w+|meetings?)"),
    ("Distribution / vente de fonds", 4,
     r"distribution|wholesale|intermediar|fund sales|(institutional|wealth|client|investment|asset management|funds?) sales|"
     r"sales (support|associate|analyst|executive|specialist|coordinator)|\bsales\b|\bventes?\b|commercial",
     r"distribution|wholesale|intermediar\w*|fund sales|ifas?\b|wealth managers?|private banks?|sales support|"
     r"distributors?|platforms?|fund selectors?"),
    ("Couverture / relation client", 4,
     r"client (coverage|relationship|solutions?|portfolio|partner)|coverage|relationship (management|support|analyst|associate|officer)|"
     r"consultant relations|business development|bizdev|partnerships?|account (manager|executive)",
     r"client coverage|relationship management|institutional clients?|key accounts?|business development|"
     r"pension funds?|sovereign|family offices?|insurers?|endowments?"),
    ("Spécialiste produit", 3,
     r"product (specialist|analyst|management|manager|development|strategist)|investment (specialist|product)|"
     r"product (and|&) (client|investment)",
     r"product specialist|product management|fund (range|launch\w*)|investment product"),
    ("Service client / reporting / RFP", 3,
     r"client (service|services|reporting|onboarding|experience|success)|\brfp\b|\bddq\b|proposal|investor services",
     r"\brfps?\b|\bddqs?\b|client reporting|client onboarding|due diligence questionnaires?|pitch ?books?"),
    ("Corporate access / ventes marchés", 3,
     r"corporate access|(equity|fixed income|fx|credit|markets?|derivatives?|structured products?) sales|sales (&|and) trading",
     r"corporate access|institutional investors?|roadshows?"),
    ("Programme graduate / VIE", 3,
     r"graduate|v\.?i\.?e\b|trainee|management associate|early careers?",
     r"rotation\w*|graduate programme|graduate program"),
    ("Marketing (fonds / produit)", 2, r"marketing", r"marketing materials?|fund marketing"),
    ("Business management / stratégie", 2, r"business management|strateg|chief of staff|cao\b|coo office",
     r"business management|strategic initiatives"),
    ("Investissement / analyse", 1,
     r"investment (analyst|associate)|private equity|\bdeal|fund investments?|portfolio (analyst|monitoring)|valuation|"
     r"\bm&a\b|origination",
     r"financial model\w*|valuation|due diligence|investment recommendations?|deal (execution|sourcing)|"
     r"fund investments?|co-?investments?|secondar(y|ies)"),
]
FAM = [(l, p, re.compile(t, re.I), re.compile(d, re.I)) for l, p, t, d in FAMILIES]

SECTOR_CORE = re.compile(
    r"asset management|asset manager|investment management|private (markets?|equity|credit|assets|wealth|banking)|"
    r"venture capital|hedge funds?|alternative (investments?|assets)|alternatives|wealth management|\baum\b|"
    r"mutual funds?|\bfunds\b|\betfs?\b|fund (house|manager)|institutional investors?|fund of funds|infrastructure (fund|investing)|"
    r"gestion d'actifs|capital[- ]investissement", re.I)
SECTOR_FIN = re.compile(
    r"\bbank(ing)?\b|insurance|insurer|payments?|fintech|crypto|digital assets?|exchange|securities|brokerage|"
    r"financial (services|institutions?)|capital markets|banque|assurance", re.I)
FR_COMPANY = re.compile(
    r"bnp|soci[ée]t[ée] g[ée]n[ée]rale|\bsg cib\b|natixis|amundi|axa|cr[ée]dit agricole|ca[- ]cib|lyxor|"
    r"rothschild|tikehau|ardian|eurazeo|edmond de rothschild|carmignac|oddo|lazard fr[eè]res gestion|"
    r"business france|v\.?i\.?e\b", re.I)
FRENCH_PLUS = re.compile(r"\bfrench\b|fran[cç]ais", re.I)
BACKGROUND_PLUS = re.compile(r"private markets?|venture capital|private equity|fundrais|\blps?\b|limited partners?|"
                             r"\bvc\b|alternatives", re.I)

# --------------------------------------------------------------------------- langues
NOT_LANG = (r"(?!\s*(?:-\s*)?(?:market|markets|equity|equities|bond|bonds|economy|economies|regulat\w+|stock|"
            r"stocks|clients?|customers?|companies|corporates?|property|real estate|banks?|government|renminbi|"
            r"yuan|yen|won|capital|institutions?|investors?|assets?|insurers?|pension|credit|a-?shares?|rmb|"
            r"onshore|offshore|business(es)?|entities|subsidiar\w+|team|office|desk|coverage|nationals?|"
            r"citizens?|law|tax|gaap|accounting|culture|holidays?|new year|fund|funds|operations?|counterpart\w*|"
            r"speaking (?:clients|investors|markets))\b)")
# libellé -> motif ; tout ce qui n'est ni français ni anglais
LANG_PATTERNS = {
    "chinois": re.compile(r"mandarin|cantonese|putonghua|普通话|普通話|粤语|粵語|廣東話|广东话|中文|汉语|漢語|"
                          r"\bchinese" + NOT_LANG, re.I),
    "japonais": re.compile(r"\bjapanese" + NOT_LANG + r"|日本語|\bjlpt\b|nihongo|business[- ]level japanese", re.I),
    "coréen": re.compile(r"\bkorean" + NOT_LANG + r"|한국어", re.I),
    "thaï": re.compile(r"\bthai\b" + NOT_LANG, re.I),
    "vietnamien": re.compile(r"\bvietnamese" + NOT_LANG, re.I),
    "indonésien / malais": re.compile(r"bahasa|\bindonesian" + NOT_LANG + r"|\bmalay\b" + NOT_LANG, re.I),
    "tagalog": re.compile(r"tagalog|filipino" + NOT_LANG, re.I),
    "hindi": re.compile(r"\bhindi\b", re.I),
    "arabe": re.compile(r"\barabic\b", re.I),
    "allemand": re.compile(r"\bgerman" + NOT_LANG, re.I),
    "espagnol": re.compile(r"\bspanish" + NOT_LANG, re.I),
    "italien": re.compile(r"\bitalian" + NOT_LANG, re.I),
    "portugais": re.compile(r"\bportuguese" + NOT_LANG, re.I),
    "russe": re.compile(r"\brussian" + NOT_LANG, re.I),
    "néerlandais": re.compile(r"\bdutch" + NOT_LANG, re.I),
    "langue asiatique": re.compile(r"asian languages?|second (asian )?language|regional languages?|local languages?|"
                                   r"other languages?|additional languages?|bilingual|multilingual|trilingual", re.I),
}
CHINESE_KEYS = {"chinois"}
SOFT_RE = re.compile(
    r"advantage|advantageous|an asset|a plus|\bplus\b|preferred|preferable|preferably|desirable|nice[- ]to[- ]have|"
    r"beneficial|bonus|favou?rable|favou?red|helpful|optional|not (required|essential|mandatory)|welcome|"
    r"considered|ideally|added value|value[- ]add|would be good|souhait|atout|appr[ée]ci|un plus", re.I)
HIGHLY_RE = re.compile(r"highly (preferred|desirable|advantageous|regarded)|strongly (preferred|desirable)|"
                       r"significant advantage|is a must|must have", re.I)
CJK_RE = re.compile(r"[぀-ヿ一-鿿가-힯]")


def html_to_text(raw: str) -> str:
    if not raw:
        return ""
    if "&lt;" in raw and "<" not in raw[:200]:          # HTML échappé (Greenhouse)
        raw = html.unescape(raw)
    raw = re.sub(r"(?i)<li[^>]*>", "\n• ", raw)
    raw = re.sub(r"(?i)</(p|li|div|h\d|tr|ul|ol)>|<br\s*/?>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw).replace("\xa0", " ")
    raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
    raw = re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", raw)
    return "\n".join(l.strip() for l in raw.split("\n")).strip()


def _clause(text: str, start: int, end: int) -> str:
    seps = ".;\n•·"
    left = max((text.rfind(s, 0, start) for s in seps), default=-1)
    rights = [text.find(s, end) for s in seps]
    rights = [r for r in rights if r != -1]
    right = min(rights) if rights else len(text)
    return text[left + 1:right]


def language_requirements(text: str, langs: Optional[List[str]] = None) -> dict:
    """Retourne {langue: 'required'|'highly_preferred'|'preferred'} pour les langues détectées (hors FR/EN)."""
    out = {}
    for lang, pat in LANG_PATTERNS.items():
        if langs and lang not in langs:
            continue
        level = None
        for m in pat.finditer(text):
            clause = _clause(text, m.start(), m.end())
            if lang == "langue asiatique" and re.search(r"english and french|french and english|"
                                                        r"anglais et fran|fran\w+ et anglais", clause, re.I):
                continue                      # « bilingue anglais/français » : parfait pour toi
            if HIGHLY_RE.search(clause):
                lv = "highly_preferred"
            elif SOFT_RE.search(clause):
                lv = "preferred"
            else:
                lv = "required"
            rank = {"preferred": 1, "highly_preferred": 2, "required": 3}
            if level is None or rank[lv] > rank[level]:
                level = lv
            if level == "required":
                break
        if level:
            out[lang] = level
    # annonce rédigée en grande partie en japonais / chinois / coréen
    letters = re.findall(r"\w", text)
    if letters and len(CJK_RE.findall(text)) > 0.15 * len(letters):
        out["langue asiatique"] = "required"
        out.setdefault("_cjk", "required")
    return out


def min_years_required(text: str) -> Optional[int]:
    t = re.sub(r"\((\d{1,2})\)", r" \1 ", text)                  # « five (5) years »
    num = r"\d{1,2}|" + "|".join(w.strip() for w in WORDS)
    pat = re.compile(
        r"(?P<a>" + num + r")\s*(?:\+|plus)?\s*"
        r"(?:(?:-|–|to|à|or)\s*(?P<b>" + num + r"))?\s*\+?\s*"
        r"(?:years?|yrs?|ans|années)\b['’]?(?P<tail>[^.\n]{0,90})", re.I)
    found = []
    for m in pat.finditer(t):
        before = t[max(0, m.start() - 60):m.start()]
        if re.search(r"experience|exp[ée]rience|working|background|track record|in (a|an|the) .{0,30}role", m.group("tail"), re.I) or \
           re.search(r"experience|exp[ée]rience|minimum|at least|au moins", before, re.I):
            if re.search(r"program|programme|contract|duration|mission|month", m.group("tail")[:25], re.I):
                continue
            a = m.group("a").lower().strip()
            v = int(a) if a.isdigit() else WORDS.get(a, 0)
            if 0 < v <= 25:
                found.append(v)
    return min(found) if found else None


# --------------------------------------------------------------------------- localisation
LOC_ALIASES = {
    "hong kong": r"hong[ -]?kong|\bhk\b|\bhkg\b|香港",
    "singapore": r"singapore|singapour|\bsg\b|\bsgp\b",
    "tokyo": r"tokyo|東京|\bjapan\b|japon|minato|chiyoda|marunouchi",
}


def location_matches(text: str, locations: List[str]) -> bool:
    t = text or ""
    for loc in locations:
        pat = LOC_ALIASES.get(loc.lower(), re.escape(loc))
        if re.search(pat, t, re.I):
            return True
    return False


def city_of(text: str) -> str:
    for name, pat in (("Hong Kong", LOC_ALIASES["hong kong"]), ("Singapour", LOC_ALIASES["singapore"]),
                      ("Tokyo", LOC_ALIASES["tokyo"])):
        if re.search(pat, text or "", re.I):
            return name
    return ""


# --------------------------------------------------------------------------- évaluation
def cheap_check(job: Job, cfg: dict) -> Verdict:
    """Filtre avant lecture de la description (économise les appels réseau)."""
    t = job.title or ""
    loc = job.location or ""
    multi = re.search(r"\b\d+\s+locations?\b", loc, re.I)
    if loc and not multi and not location_matches(loc + " " + job.extra_location, cfg["locations"]):
        return Verdict(False, "lieu")
    if SENIOR_RE.search(t) and not re.search(r"\b(assistant|analyst|associate) to\b", t, re.I):
        return Verdict(False, "titre trop senior")
    if re.search(r"relationship manager", t, re.I) and not JUNIOR_RE.search(t):
        return Verdict(False, "relationship manager (portefeuille)")
    neg = TITLE_NEG_RE.search(t)
    if neg or (TRADING_RE.search(t) and not re.search(r"\bsales\b", t, re.I)):
        return Verdict(False, "type de poste hors cible")
    if TITLE_CHINA_RE.search(t):
        return Verdict(False, "périmètre Chine / Japon / Corée")
    if CJK_RE.search(t) and len(CJK_RE.findall(t)) >= 3:
        return Verdict(False, "annonce en japonais / chinois")
    if cfg.get("max_age_days") and job.posted:
        from datetime import date
        try:
            if (date.today() - date.fromisoformat(job.posted)).days > cfg["max_age_days"]:
                return Verdict(False, "annonce trop ancienne")
        except ValueError:
            pass
    return Verdict(True)


def _excerpt(text: str, n: int = 700) -> str:
    m = re.search(r"responsibilit|you will|the role|key duties|what you|your role|mission", text, re.I)
    start = m.start() if m else 0
    return re.sub(r"\s+", " ", text[start:start + n]).strip()


def classify(title: str, text: str):
    """Famille de métier : (libellé, points /4, explication)."""
    for label, pts, tre, _ in FAM:
        m = tre.search(title)
        if m:
            return label, pts, f"{label} : « {m.group(0).strip()} » dans l'intitulé"
    counts = []
    for label, pts, _, dre in FAM:
        hits = sorted(set(h.group(0).lower() for h in dre.finditer(text)))
        if hits:
            counts.append((len(hits), pts, label, hits))
    if not counts:
        return "", 0, "Intitulé et annonce sans lien avec IR / distribution / client / produit"
    counts.sort(key=lambda c: (-c[0], -c[1]))
    n, pts, label, hits = counts[0]
    if n < 2:
        return label, max(0, pts - 3), f"Intitulé vague, un seul indice ({hits[0]}) : {label} peu probable"
    pts = max(1, pts - 1)
    return label, pts, f"Intitulé vague ; l'annonce parle surtout de {', '.join(hits[:4])} → {label}"


def full_check(job: Job, cfg: dict) -> Verdict:
    text = html_to_text(job.description)
    t = job.title or ""
    flags: List[str] = []
    cons: List[str] = []

    full_loc = " ".join([job.location or "", job.extra_location or ""])
    if re.search(r"\b\d+\s+locations?\b", full_loc, re.I) or not job.location:
        if not location_matches(full_loc + " " + text[:600], cfg["locations"]):
            return Verdict(False, "lieu")

    base = dict(excerpt=_excerpt(text) if text else "", description=text)

    # ---- langues (tout sauf français / anglais)
    langs = language_requirements(text) if text else {}
    langs.pop("_cjk", None)
    other, malus = [], 0
    excluded_lang = None
    for lang, level in langs.items():
        lab = {"required": "requis", "highly_preferred": "fortement souhaité", "preferred": "souhaité"}[level]
        other.append(f"{lang} {lab}")
        if level in ("required", "highly_preferred") or (level == "preferred" and cfg.get("reject_preferred_languages")):
            excluded_lang = excluded_lang or f"{lang} {lab}"
        else:
            malus = -1
    ch = langs.get("chinois")
    chinese = {"required": "requis", "highly_preferred": "requis", "preferred": "souhaité"}.get(ch, "non" if text else "unknown")
    if excluded_lang:
        return Verdict(False, excluded_lang, chinese=chinese, other_langs=other, langs_ok=False, **base)

    # ---- années / niveau (critère /3)
    years = min_years_required(text) if text else None
    max_years = cfg.get("max_years", 3)
    if years is not None and years > max_years:
        return Verdict(False, f"{years} ans d'expérience demandés", min_years=years, chinese=chinese,
                       other_langs=other, langs_ok=not other, **base)
    junior = JUNIOR_RE.search(t)
    if years is not None and years >= 3:
        niv, level, nwhy = 2, "Confirmé (3 ans)", f"{years} ans demandés : limite haute (tu as ~2 ans)"
    elif junior and (years is None or years <= 2):
        niv, level = 3, "Junior"
        nwhy = f"Intitulé junior (« {junior.group(0)} »)" + (f", {years} an(s) demandé(s)" if years else ", pas d'années exigées")
    elif re.search(r"\bassociate\b", t, re.I):
        niv, level = 2, "Associate"
        nwhy = "« Associate » : souvent 2-4 ans" + (f" ; ici {years} an(s) demandé(s)" if years else "")
        if years is not None and years <= 2:
            niv, nwhy = 3, f"« Associate » mais {years} an(s) demandé(s) seulement"
    elif re.search(r"\bmanager\b|\bspecialist\b|\bexecutive\b", t, re.I):
        word = re.search(r"\bmanager\b|\bspecialist\b|\bexecutive\b", t, re.I).group(0)
        if years is not None and years <= 2:
            niv, level, nwhy = 3, "Junior", f"« {word} » mais {years} an(s) demandé(s) seulement"
        else:
            niv, level, nwhy = 1, "À vérifier", f"« {word} » sans années précisées : niveau peut-être au-dessus"
    elif years is not None:
        niv, level, nwhy = 3, "Junior", f"{years} an(s) demandé(s)"
    else:
        niv, level, nwhy = 2, "Non précisé", "Ni niveau ni années précisés dans l'annonce"
    if not text:
        niv, nwhy = min(niv, 2), nwhy + " (annonce non lue)"

    # ---- métier (critère /4)
    fam, met, mwhy = classify(t, text)
    if met == 0:
        return Verdict(False, "métier hors cible", min_years=years, chinese=chinese, other_langs=other, **base)
    if fam.startswith("Investissement"):
        cons.append("Poste d'investissement (analyse, modélisation) plutôt que relation investisseurs / distribution")

    # ---- secteur (critère /2)
    blob = f"{job.company} {t} {text[:4000]}"
    if SECTOR_CORE.search(blob):
        sec, swhy = 2, f"Gestion d'actifs / private markets / wealth (« {SECTOR_CORE.search(blob).group(0)} »)"
    elif SECTOR_FIN.search(blob):
        sec, swhy = 1, f"Finance hors gestion d'actifs (« {SECTOR_FIN.search(blob).group(0)} »)"
    else:
        sec, swhy = 0, "Hors finance (tech, autre secteur)"

    # ---- atouts (critère /1)
    at, awhy = 0, "Aucun avantage particulier pour ton profil"
    if FRENCH_PLUS.search(text) and re.search(r"french", _clause(text, *FRENCH_PLUS.search(text).span()), re.I):
        at, awhy = 1, "Le français est demandé ou apprécié"
    elif FR_COMPANY.search(blob) or job.source.startswith("VIE"):
        m = FR_COMPANY.search(blob)
        at, awhy = 1, f"Groupe français / VIE (« {m.group(0) if m else 'VIE'} »)"
    elif BACKGROUND_PLUS.search(text):
        at, awhy = 1, f"Colle à ton expérience private markets / levée (« {BACKGROUND_PLUS.search(text).group(0)} »)"

    parts = [
        {"k": "metier", "label": "Métier", "pts": met, "max": 4, "why": mwhy},
        {"k": "niveau", "label": "Niveau", "pts": niv, "max": 3, "why": nwhy},
        {"k": "secteur", "label": "Secteur", "pts": sec, "max": 2, "why": swhy},
        {"k": "atouts", "label": "Atouts", "pts": at, "max": 1, "why": awhy},
    ]
    if malus:
        parts.append({"k": "langue", "label": "Langue", "pts": -1, "max": 0,
                      "why": "Autre langue souhaitée (pas exigée) : " + ", ".join(other)})
        flags.append("autre langue souhaitée : " + ", ".join(other))
    if years is not None and years >= 3:
        flags.append(f"{years} ans demandés (limite)")
    if not text:
        flags.append("annonce non lue : langues non vérifiées")
    note10 = max(0, min(10, sum(p["pts"] for p in parts)))
    rating = "A" if note10 >= 8 else "B" if note10 >= 6 else "C"
    why = [f"{p['label']} {p['pts']:+d}" if p["max"] == 0 else f"{p['label']} {p['pts']}/{p['max']} : {p['why']}" for p in parts]
    return Verdict(True, "", score=note10, rating=rating, flags=flags, min_years=years, chinese=chinese,
                   other_langs=other, note10=note10, why=why, parts=parts, role=fam, level=level,
                   langs_ok=not other, cons=cons, **base)
