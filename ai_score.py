"""Relecture des offres par une IA gratuite, avec ton CV.

Fournisseurs essayés dans l'ordre de config.yaml (ai.providers), selon les clés présentes :
  gemini     : GEMINI_API_KEY     (Google AI Studio, gratuit, ~500 analyses/jour sur Flash-Lite)
  openrouter : OPENROUTER_API_KEY (modèles « :free », ~50 analyses/jour sans crédit, repli automatique entre modèles)
  groq       : GROQ_API_KEY       (Llama 3.3 70B, gratuit)
Sans clé ou en cas d'erreur, rien ne casse : l'offre garde la note des règles.
"""
import json
import re
import time
from pathlib import Path

import requests

HERE = Path(__file__).parent
AI_VERSION = 3                 # change = les analyses IA en cache sont refaites
LAST_ERROR = ""
EXHAUSTED = set()              # fournisseurs dont le quota du jour est atteint pendant ce passage
MAX_PTS = {"metier": 4, "niveau": 3, "secteur": 2, "atouts": 1}
LABELS = {"metier": "Métier", "niveau": "Niveau", "secteur": "Secteur", "atouts": "Atouts"}

DEFAULTS = {
    "providers": ["gemini", "openrouter", "groq"],
    "gemini_model": "gemini-3.5-flash-lite",
    "gemini_fallbacks": ["gemini-3.1-flash-lite", "gemini-3.6-flash"],
    "openrouter_models": ["nvidia/nemotron-3.5-super-vl:free", "nvidia/nemotron-3-ultra-550b-a55b:free",
                          "cohere/north-mini-code:free"],
    "groq_model": "llama-3.3-70b-versatile",
}
ENV = {"gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY", "groq": "GROQ_API_KEY"}


# --------------------------------------------------------------------------- profil
def load_cv() -> dict:
    try:
        return json.loads((HERE / "cv.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def cv_text(cv: dict) -> str:
    out = [f"{cv.get('name', '')} — {cv.get('headline', '')}", cv.get("location_line", "")]
    out.append("EXPÉRIENCE :")
    for e in cv.get("experience", []):
        out.append(f"- {e['title']} | {e['company']} | {e['location']} | {e['dates']}")
        out += [f"  • {b}" for b in e.get("bullets", [])]
    out.append("FORMATION :")
    for e in cv.get("education", []):
        out.append(f"- {e['degree']} | {e['school']} | {e['dates']} — {e.get('details', '')}")
    out.append("COMPÉTENCES :")
    out += [f"- {s['label']} : {s['text']}" for s in cv.get("skills", [])]
    return "\n".join(x for x in out if x)


SYSTEM = """Tu es un recruteur expert en gestion d'actifs et private markets en Asie (Hong Kong, Singapour, Tokyo).
Tu évalues des offres d'emploi pour Théo et réponds UNIQUEMENT par un objet JSON valide, rédigé en FRANÇAIS."""

RUBRIC = """BARÈME (sois strict et cohérent, ne gonfle pas les notes) :
- metier (0-4) : 4 = cœur de cible (relations investisseurs / levée de fonds, distribution ou vente de fonds,
  client coverage institutionnel ou wealth, product specialist en gestion d'actifs / private markets) ;
  3 = proche (client service / reporting / RFP-DDQ, corporate access, sales marchés, graduate programme finance,
  business development en finance) ; 2 = transposable (vente / BD hors finance, marketing de fonds, business management) ;
  1 = éloigné (poste d'investissement : analyse de fonds, deals, modélisation, due diligence ; opérations client) ;
  0 = sans rapport.
- niveau (0-3) : 3 = junior (analyst, graduate, VIE, 0-2 ans demandés) ; 2 = 3 ans ou « Associate » ;
  1 = probablement au-dessus (manager, specialist confirmé, 4 ans implicites) ; 0 = clairement senior.
- secteur (0-2) : 2 = gestion d'actifs, private markets, hedge fund, wealth ; 1 = banque, assurance, fintech, bourse ;
  0 = autre.
- atouts (0-1) : 1 si un élément précis de son parcours lui donne un avantage net (private markets / LPs / levée,
  distribution wealth, français apprécié, VIE ou groupe français, expérience demandée qu'il a déjà).
EXCLUSION (exclude = true) si : une langue autre que français / anglais est exigée ou fortement souhaitée
(mandarin, cantonais, japonais, coréen, « langue locale », « bilingue chinois »…, ou annonce rédigée dans cette langue) ;
plus de 3 ans d'expérience exigés ; poste clairement senior. Une langue seulement « un plus / advantage » n'exclut PAS :
mets-la dans languages_preferred."""

EXAMPLE = {
    "exclude": False, "exclude_reason": "",
    "role": "Distribution de fonds (wholesale)", "level": "Junior (0-2 ans)", "years_required": 2,
    "languages_required": [], "languages_preferred": ["mandarin"],
    "scores": {"metier": 4, "niveau": 3, "secteur": 2, "atouts": 1},
    "explain": {"metier": "Vente de fonds aux banques privées et IFA : exactement ton métier chez Audacia.",
                "niveau": "Analyst, 2 ans demandés : tu les as.",
                "secteur": "Gestionnaire d'actifs mondial.",
                "atouts": "Ton expérience wholesale (accord de distribution signé) est demandée."},
    "summary": "Soutien de l'équipe de distribution wholesale Asie : supports de vente, RFP, suivi des distributeurs. "
               "Rattaché au head of wholesale Hong Kong.",
    "pros": ["Métier identique à ton poste chez Audacia", "Anglais suffisant"],
    "cons": ["Mandarin apprécié : candidats locaux avantagés"],
    "keywords": ["fund distribution", "wholesale", "RFP", "pitch book", "Salesforce", "private banks", "IFA"],
}


def build_prompt(job_title, company, location, description, hints=""):
    cv = load_cv()
    return (f"PROFIL DE THÉO\n{cv_text(cv)}\nLangues parlées : français et anglais UNIQUEMENT.\n"
            f"Ce qu'il cherche : {cv.get('target', '')}\n\n{RUBRIC}\n\n"
            f"OFFRE\nIntitulé : {job_title}\nEntreprise : {company}\nLieu : {location}\n"
            f"Indices des règles automatiques : {hints or 'aucun'}\n\nAnnonce complète :\n{description[:15000]}\n\n"
            "Réponds avec un objet JSON exactement de cette forme (valeurs à remplacer, textes EN FRANÇAIS, "
            "pros/cons : 2 à 4 phrases courtes chacune, keywords : 10 à 20 mots-clés ATS en anglais tirés de l'annonce) :\n"
            + json.dumps(EXAMPLE, ensure_ascii=False))


# --------------------------------------------------------------------------- mise en forme
EN_WORDS = re.compile(r"\b(the|and|is|are|with|requires?|which|does|not|this|role|candidate|experience|it|of|for)\b", re.I)
FR_WORDS = re.compile(r"\b(le|la|les|et|est|une|des|du|pour|avec|poste|pas|expérience|il|en|au|aux|ton|tu)\b", re.I)


def looks_english(text):
    text = text or ""
    en, fr = len(EN_WORDS.findall(text)), len(FR_WORDS.findall(text))
    return en >= 4 and en > 2 * fr


def as_list(v):
    """Liste de phrases propres, même si l'IA renvoie une chaîne (sinon Python la lit lettre par lettre)."""
    if v is None:
        return []
    if isinstance(v, dict):
        return [f"{k} : {x}" for k, x in v.items()]
    if isinstance(v, (list, tuple)):
        items = [x if isinstance(x, str) else json.dumps(x, ensure_ascii=False) for x in v if x is not None]
        if len(items) >= 4 and sum(1 for x in items if len(x) <= 1) >= 0.8 * len(items):
            return as_list("".join(items))
        return [x.strip(" \t\n-•*") for x in items if x and x.strip(" \t\n-•*")]
    s = str(v).strip()
    if not s:
        return []
    if s.startswith("[") and s.endswith("]"):
        try:
            return as_list(json.loads(s))
        except ValueError:
            pass
    lines = [l.strip(" \t-•*").strip() for l in re.split(r"\n+|;\s+", s)]
    lines = [re.sub(r"^\d+[.)]\s*", "", l) for l in lines if l]
    if len(lines) > 1:
        return lines
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý0-9«\"])", s)
    return [p.strip() for p in parts if p.strip()]


def _int(v, lo, hi, default=0):
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(v if v is not None else ""))
    return max(lo, min(hi, round(float(m.group().replace(",", "."))))) if m else default


def _truthy(v):
    return v is True or str(v).strip().lower() in ("true", "oui", "yes", "1")


def normalize(res, model=""):
    """Remet la réponse de l'IA au bon format. Retourne None si les notes sont absentes."""
    if not isinstance(res, dict):
        return None
    sc = res.get("scores")
    if not isinstance(sc, dict) or not any(k in sc for k in MAX_PTS):
        return None
    ex = res.get("explain") if isinstance(res.get("explain"), dict) else {}
    out = {
        "exclude": _truthy(res.get("exclude")),
        "exclude_reason": str(res.get("exclude_reason") or "").strip(),
        "role": str(res.get("role") or "").strip(),
        "level": str(res.get("level") or "").strip(),
        "years_required": _int(res.get("years_required"), 0, 30),
        "languages_required": [x for x in as_list(res.get("languages_required"))
                               if not re.fullmatch(r"(?i)(english|anglais|french|français|none|aucune?|n/?a)", x)],
        "languages_preferred": [x for x in as_list(res.get("languages_preferred"))
                                if not re.fullmatch(r"(?i)(english|anglais|french|français|none|aucune?|n/?a)", x)],
        "scores": {k: _int(sc.get(k), 0, m) for k, m in MAX_PTS.items()},
        "explain": {k: " ".join(as_list(ex.get(k))) for k in MAX_PTS},
        "summary": " ".join(as_list(res.get("summary"))),
        "pros": [x for x in as_list(res.get("pros")) if len(x) > 2][:5],
        "cons": [x for x in as_list(res.get("cons")) if len(x) > 2][:5],
        "keywords": [x for x in as_list(res.get("keywords")) if 1 < len(x) < 60][:25],
        "model": res.get("model") or model,
        "v": res.get("v", 0),
    }
    if out["languages_required"]:
        out["exclude"] = True
        out["exclude_reason"] = out["exclude_reason"] or ("langue exigée : " + ", ".join(out["languages_required"]))
    return out


def parts_of(ai):
    """Note /10 et sa décomposition (même format que les règles)."""
    parts = [{"k": k, "label": LABELS[k], "pts": ai["scores"][k], "max": m, "why": ai["explain"].get(k, "")}
             for k, m in MAX_PTS.items()]
    if ai["languages_preferred"]:
        parts.append({"k": "langue", "label": "Langue", "pts": -1, "max": 0,
                      "why": "Autre langue appréciée (pas exigée) : " + ", ".join(ai["languages_preferred"])})
    return max(0, min(10, sum(p["pts"] for p in parts))), parts


def _json_from_text(text):
    t = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    a, b = t.find("{"), t.rfind("}")
    if a != -1 and b > a:
        try:
            return json.loads(t[a:b + 1])
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------- appels
def available(env, cfg):
    cfg = {**DEFAULTS, **(cfg or {})}
    return [p for p in cfg["providers"] if p in ENV and (env.get(ENV[p]) or "").strip()]


def _call(provider, key, cfg, prompt):
    """Un appel ; retourne (texte, modèle) ou lève RuntimeError('quota'|'...')."""
    if provider == "gemini":
        models = [cfg["gemini_model"]] + [m for m in cfg["gemini_fallbacks"] if m != cfg["gemini_model"]]
        last = ""
        for model in models:
            body = {"systemInstruction": {"parts": [{"text": SYSTEM}]},
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2,
                                         "maxOutputTokens": 4096}}
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                              headers={"x-goog-api-key": key, "Content-Type": "application/json"}, json=body, timeout=120)
            if r.status_code in (404, 400) and "model" in r.text.lower():
                last = f"modèle {model} indisponible"
                continue
            if r.status_code == 429:
                last = "quota"
                continue                      # quota par modèle : on tente le suivant
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code} : {r.text[:200]}")
            d = r.json()
            parts = (((d.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
            return "".join(p.get("text", "") for p in parts if not p.get("thought")), model
        raise RuntimeError(last or "aucun modèle Gemini disponible")
    if provider == "openrouter":
        body = {"models": cfg["openrouter_models"][:3], "temperature": 0.2, "max_tokens": 4000,
                "response_format": {"type": "json_object"}, "reasoning": {"exclude": True},
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]}
        url, model = "https://openrouter.ai/api/v1/chat/completions", cfg["openrouter_models"][0]
    else:
        body = {"model": cfg["groq_model"], "temperature": 0.2, "max_tokens": 3000,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]}
        url, model = "https://api.groq.com/openai/v1/chat/completions", cfg["groq_model"]
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "HTTP-Referer": "https://github.com/jobwatch", "X-Title": "jobwatch"}
    for attempt in range(2):
        r = requests.post(url, headers=headers, json=body, timeout=120)
        if r.status_code == 400 and "response_format" in body:
            body.pop("response_format")
            continue
        break
    if r.status_code == 429 or (r.status_code == 402):
        raise RuntimeError("quota")
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} : {r.text[:200]}")
    d = r.json()
    msg = ((d.get("choices") or [{}])[0].get("message") or {})
    c = msg.get("content")
    if isinstance(c, list):
        c = "".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in c)
    return (c or msg.get("reasoning") or ""), d.get("model") or model


def review(job_title, company, location, description, env, cfg=None, hints=""):
    """Analyse une offre. Retourne (résultat normalisé, fournisseur) ou (None, None)."""
    global LAST_ERROR
    cfg = {**DEFAULTS, **(cfg or {})}
    prompt = build_prompt(job_title, company, location, description, hints)
    for provider in available(env, cfg):
        if provider in EXHAUSTED:
            continue
        key = env[ENV[provider]].strip()
        first, p = None, prompt
        for attempt in range(3):
            try:
                text, model = _call(provider, key, cfg, p)
            except RuntimeError as e:
                if str(e) == "quota":
                    EXHAUSTED.add(provider)
                    LAST_ERROR = LAST_ERROR or f"{provider} : quota gratuit du jour atteint"
                else:
                    LAST_ERROR = LAST_ERROR or f"{provider} : {e}"
                break
            except (requests.RequestException, ValueError) as e:
                LAST_ERROR = LAST_ERROR or f"{provider} : {type(e).__name__} {str(e)[:150]}"
                time.sleep(3)
                continue
            res = normalize(_json_from_text(text), model)
            if not res:
                LAST_ERROR = LAST_ERROR or f"{provider} ({model}) : réponse illisible : {text[:150]}"
                continue
            res["v"] = AI_VERSION
            if first is None and looks_english(res["summary"] + " " + " ".join(res["pros"] + res["cons"])):
                first = res                         # réponse en anglais : une relance en insistant
                p = prompt + "\n\nATTENTION : tous les textes du JSON doivent être en FRANÇAIS."
                continue
            return res, provider
        if first:
            return first, provider
    return None, None
