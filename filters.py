"""Filtres de jobwatch : localisation, séniorité, type de poste, langues, années d'expérience."""
import html
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

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
    chinese: str = "unknown"              # "non" | "souhaité" | "fortement souhaité" | "requis" | "unknown"
    other_langs: List[str] = field(default_factory=list)
    note10: Optional[int] = None          # note /10 selon ton profil
    why: List[str] = field(default_factory=list)
    excerpt: str = ""


# --------------------------------------------------------------------------- titres
SENIOR_RE = re.compile(
    r"\b(vp|svp|evp|avp|vice[- ]president|assistant vice president|director|md|"
    r"managing director|head|chief|principal|partner|president|lead|senior|sr|"
    r"general manager|team leader|directeur|directrice|responsable|chef)\b", re.I)

JUNIOR_RE = re.compile(
    r"\b(analyst|analyste|assistant|trainee|graduate|intern|internship|junior|vie|"
    r"v\.i\.e|stagiaire|apprenti|alternan\w*)\b", re.I)

# Titres hors-sujet : technique, risque, juridique, back-office, trading, retail...
TITLE_NEG_RE = re.compile(
    r"engineer|developer|software|devops|data (scientist|engineer|analyst)|\bquant|actuar|"
    r"audit|account(ant|ing)\b|\btax\b|legal|counsel|compliance|\brisk\b|\baml\b|financial crime|"
    r"\bhr\b|human resources|people partner|talent|recruit|payroll|facilit|"
    r"settlement|collateral|reconciliation|trade support|middle office|back office|"
    r"fund accounting|transfer agen|custody|corporate action|operations|"
    r"technolog|\bit\b|cyber|infrastructure|security|"
    r"trader|trading|market maker|structur(er|ing)|research (analyst|associate)|"
    r"equity research|portfolio manager|fund manager|treasury|financial reporting|"
    r"financial (analyst|accountant|controller)|\bfp&a|controller|credit (analyst|risk|specialist)|"
    r"lending|\bloan|mortgage|underwrit|branch|teller|customer service officer|contact cent|"
    r"personal banking|premier|insurance agent|communications|\bpr\b|designer|\bux\b|"
    r"administrative|executive assistant|secretary|receptionist|office manager|"
    r"investment bank(er|ing)|leveraged|origination|"
    r"comptab|juridique|ressources humaines|informatique|d[ée]veloppeur|ing[ée]nieur|"
    r"contr[ôo]le|conformit[ée]|tr[ée]sorerie", re.I)

TITLE_CHINA_RE = re.compile(r"\b(china|chinese|greater china|mainland|prc|taiwan)\b|中国|大中华", re.I)

# Titres qui collent à ton profil (+3 chacun, plafonné)
TITLE_POS = [re.compile(p, re.I) for p in [
    r"investor relations|\bir\b|relations? investisseurs?",
    r"client (service|services|solutions?|coverage|specialist|relationship|success|reporting|onboarding)",
    r"distribution",
    r"\bsales\b|\bvente|\bcommercial",
    r"product (specialist|analyst|management|manager|development)",
    r"business (development|management|manager)|d[ée]veloppement commercial",
    r"fundrais|capital (raising|introduction|formation)",
    r"corporate access",
    r"wealth|private (wealth|markets|banking|credit|equity|assets)|gestion (priv[ée]e|de patrimoine)",
    r"alternatives?",
    r"investment (specialist|counsel+or|product)",
    r"\brfp\b|\bddq\b",
    r"relationship (support|management)|consultant relations",
    r"marketing",
    r"institutional|intermediar",
    r"account (manager|executive)",
]]

# Mots de description (+1 chacun, plafonné à 3)
DESC_POS = re.compile(
    r"\brfp\b|\bddq\b|pitch ?book|investor|\blps?\b|private markets?|\baum\b|"
    r"distribution|client (service|solutions|reporting|onboarding)|sales support|"
    r"fund performance|product (material|specialist)", re.I)

# --------------------------------------------------------------------------- langues
NOT_LANG = (r"(?!\s+(?:market|markets|equity|equities|bond|bonds|economy|economies|regulat\w+|stock|"
            r"stocks|clients?|companies|corporates?|property|real estate|banks?|government|renminbi|"
            r"yuan|capital|institutions?|investors?|assets?|insurers?|pension|credit|a-?shares?|rmb|"
            r"onshore|offshore))")
LANG_PATTERNS = {
    "chinois": re.compile(r"mandarin|cantonese|putonghua|普通话|普通話|粤语|粵語|廣東話|广东话|中文|汉语|漢語|"
                          r"chinese" + NOT_LANG, re.I),
    "japonais": re.compile(r"japanese" + NOT_LANG + r"|日本語", re.I),
    "coréen": re.compile(r"korean" + NOT_LANG + r"|한국어", re.I),
    "thaï": re.compile(r"\bthai\b" + NOT_LANG, re.I),
    "vietnamien": re.compile(r"vietnamese" + NOT_LANG, re.I),
    "indonésien": re.compile(r"bahasa|indonesian" + NOT_LANG, re.I),
}
SOFT_RE = re.compile(
    r"advantage|advantageous|an asset|a plus|\bplus\b|preferred|desirable|nice to have|beneficial|"
    r"bonus|favou?rable|helpful|optional|not required|not essential|welcome|considered a|souhait|atout|"
    r"appr[ée]ci", re.I)
HIGHLY_RE = re.compile(r"highly (preferred|desirable|advantageous)|strongly (preferred|desirable)", re.I)


def html_to_text(raw: str) -> str:
    if not raw:
        return ""
    raw = re.sub(r"(?i)</(p|li|div|h\d|tr)>|<br\s*/?>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    return re.sub(r"[ \t\r\f\v]+", " ", raw).strip()


def _clause(text: str, start: int, end: int) -> str:
    seps = ".;\n•·"
    left = max((text.rfind(s, 0, start) for s in seps), default=-1)
    rights = [text.find(s, end) for s in seps]
    rights = [r for r in rights if r != -1]
    right = min(rights) if rights else len(text)
    return text[left + 1:right]


def language_requirements(text: str, langs: List[str]) -> dict:
    """Retourne {langue: 'required'|'preferred'|'highly_preferred'} pour les langues détectées."""
    out = {}
    for lang in langs:
        pat = LANG_PATTERNS.get(lang)
        if not pat:
            continue
        level = None
        for m in pat.finditer(text):
            clause = _clause(text, m.start(), m.end())
            if SOFT_RE.search(clause):
                lv = "highly_preferred" if HIGHLY_RE.search(clause) else "preferred"
            else:
                lv = "required"
            if lv == "required":
                level = "required"
                break
            if level is None or (level == "preferred" and lv == "highly_preferred"):
                level = lv
        if level:
            out[lang] = level
    return out


def min_years_required(text: str) -> Optional[int]:
    pat = re.compile(
        r"(?P<a>\d{1,2}|" + "|".join(WORDS) + r")\s*(?:\+|plus)?\s*"
        r"(?:(?:-|–|to|à)\s*(?P<b>\d{1,2}|" + "|".join(WORDS) + r"))?\s*\+?\s*"
        r"(?:years?|ans)\b['’]?(?P<tail>[^.\n]{0,80})", re.I)
    found = []
    for m in pat.finditer(text):
        if re.search(r"experience|exp[ée]rience|working|background", m.group("tail"), re.I) or \
           re.search(r"experience|exp[ée]rience", text[max(0, m.start() - 40):m.start()], re.I):
            a = m.group("a").lower()
            found.append(int(a) if a.isdigit() else WORDS[a])
    return min(found) if found else None


# --------------------------------------------------------------------------- localisation
LOC_ALIASES = {
    "hong kong": r"hong[ -]?kong|\bhk\b|\bhkg\b",
    "singapore": r"singapore|singapour|\bsg\b",
    "tokyo": r"tokyo|\bjapan\b|japon",
}


def location_matches(text: str, locations: List[str]) -> bool:
    t = text or ""
    for loc in locations:
        pat = LOC_ALIASES.get(loc.lower(), re.escape(loc))
        if re.search(pat, t, re.I):
            return True
    return False


# --------------------------------------------------------------------------- évaluation
def cheap_check(job: Job, cfg: dict) -> Verdict:
    """Filtre avant lecture de la description (économise les appels réseau)."""
    t = job.title or ""
    loc = job.location or ""
    multi = re.search(r"\b\d+\s+locations?\b", loc, re.I)
    if loc and not multi and not location_matches(loc + " " + job.extra_location, cfg["locations"]):
        return Verdict(False, "lieu")
    if SENIOR_RE.search(t):
        return Verdict(False, "titre trop senior")
    if re.search(r"relationship manager", t, re.I) and not JUNIOR_RE.search(t):
        return Verdict(False, "relationship manager (portefeuille)")
    if TITLE_NEG_RE.search(t):
        return Verdict(False, "type de poste hors cible")
    if TITLE_CHINA_RE.search(t):
        return Verdict(False, "périmètre Chine / chinois")
    if cfg.get("max_age_days") and job.posted:
        from datetime import date
        try:
            age = (date.today() - date.fromisoformat(job.posted)).days
            if age > cfg["max_age_days"]:
                return Verdict(False, "annonce trop ancienne")
        except ValueError:
            pass
    return Verdict(True)


def _excerpt(text: str, n: int = 700) -> str:
    m = re.search(r"responsibilit|you will|the role|key duties|what you|your role|mission", text, re.I)
    start = m.start() if m else 0
    return re.sub(r"\s+", " ", text[start:start + n]).strip()


def _chinese_label(langs: dict) -> str:
    lv = langs.get("chinois")
    return {"required": "requis", "highly_preferred": "fortement souhaité",
            "preferred": "souhaité"}.get(lv, "non")


def full_check(job: Job, cfg: dict) -> Verdict:
    text = html_to_text(job.description)
    t = job.title or ""
    flags: List[str] = []
    why: List[str] = []

    # lieu : on revérifie avec le détail pour les offres multi-lieux
    full_loc = " ".join([job.location or "", job.extra_location or ""])
    if re.search(r"\b\d+\s+locations?\b", full_loc, re.I) or not job.location:
        if not location_matches(full_loc + " " + text[:400], cfg["locations"]):
            return Verdict(False, "lieu")

    # score de fit
    score = 0
    pos_hits = sum(1 for p in TITLE_POS if p.search(t))
    score += min(pos_hits, 2) * 3
    if pos_hits:
        why.append(f"Intitulé proche de ton profil (IR / distribution / client / produit) +{min(pos_hits, 2) * 3}")
    if JUNIOR_RE.search(t):
        score += 2
        why.append("Intitulé junior (analyst / assistant / VIE…) +2")
    elif re.search(r"\bassociate\b", t, re.I):
        score += 1
        why.append("Intitulé « Associate » +1")
    desc_hits = len(set(m.group(0).lower() for m in DESC_POS.finditer(text)))
    score += min(desc_hits, 3)
    if desc_hits:
        why.append(f"Mots de l'annonce qui collent à ton profil +{min(desc_hits, 3)}")
    base = dict(score=score, why=why, excerpt=_excerpt(text) if text else "")
    if score < cfg.get("min_score", 3):
        return Verdict(False, f"fit insuffisant ({score})", **base)

    # années d'expérience
    years = min_years_required(text) if text else None
    max_years = cfg.get("max_years", 3)
    note = score
    if years is not None:
        if years > max_years:
            return Verdict(False, f"{years} ans demandés", min_years=years, **base)
        if years == max_years:
            flags.append(f"{years} ans demandés (limite)")
            note -= 1
            why.append("3 ans d'expérience demandés : limite haute pour toi -1")
    if re.search(r"\bmanager\b", t, re.I) and not JUNIOR_RE.search(t):
        if years is None:
            flags.append("titre « Manager » : niveau à vérifier")
            score -= 1
            note -= 1
            why.append("Titre « Manager » : niveau probablement au-dessus de ton profil -1")

    # langues
    chinese, other = "unknown", []
    if not text:
        flags.append("description non lue : langues non vérifiées")
        note -= 1
        why.append("Description illisible : chinois non vérifié -1")
    else:
        langs = language_requirements(text, cfg.get("exclude_languages", ["chinois"]))
        chinese = _chinese_label(language_requirements(text, ["chinois"]))
        for lang, level in langs.items():
            if lang != "chinois":
                other.append(f"{lang} {'requis' if level == 'required' else 'souhaité'}")
            if level == "required":
                return Verdict(False, f"{lang} requis", min_years=years, chinese=chinese,
                               other_langs=other, **{**base, "score": score})
            flags.append(f"{lang} fortement souhaité" if level == "highly_preferred" else f"{lang} souhaité")
            if level == "highly_preferred":
                score -= 1
                note -= 2
                why.append(f"{lang} fortement souhaité -2")
                if cfg.get("reject_highly_preferred", False):
                    return Verdict(False, f"{lang} fortement souhaité", chinese=chinese,
                                   other_langs=other, **{**base, "score": score})
            else:
                note -= 1
                why.append(f"{lang} souhaité (pas requis) -1")
        if chinese == "non":
            why.append("Aucun chinois demandé dans l'annonce")

    rating = "A" if score >= 7 else "B" if score >= 5 else "C"
    note10 = max(0, min(10, note))
    return Verdict(True, "", score=score, rating=rating, flags=flags, min_years=years,
                   chinese=chinese, other_langs=other, note10=note10, why=why,
                   excerpt=base["excerpt"])
