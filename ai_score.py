"""Relecture des offres par une IA gratuite (API Gemini de Google AI Studio).

Sans clé (variable GEMINI_API_KEY) ou en cas d'erreur, rien ne casse : le script garde la note par règles.
La première erreur rencontrée est gardée dans LAST_ERROR et écrite dans out/errors.txt.
"""
import json
import re
import time

import requests

URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
DEFAULT_MODEL = "gemini-3.1-flash-lite"
LAST_ERROR = ""
RATE_STOP = False   # passe à True quand le plafond gratuit du jour est atteint : on arrête d'appeler l'IA

SYSTEM = (
    "Tu aides Théo à trier des offres d'emploi. Profil : analyste Investor Relations à Paris dans une société "
    "de capital-risque, master NEOMA 2026, moins de 3 ans d'expérience, parle français et anglais uniquement "
    "(ni chinois, ni japonais, ni coréen), disponible en janvier 2027. Il cherche un poste de niveau Analyst "
    "(ou VIE / junior) en ventes, distribution, relations investisseurs, support produit ou service client "
    "dans la gestion d'actifs, la banque privée ou le private equity, à Hong Kong, Singapour ou Tokyo. "
    "Réponds uniquement par un objet JSON."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "note": {"type": "integer", "description": "Adéquation au profil de 0 à 10"},
        "chinese": {"type": "string", "enum": ["non", "souhaité", "requis"],
                    "description": "requis si mandarin/cantonais est une exigence ; souhaité si seulement un plus"},
        "other_langs": {"type": "array", "items": {"type": "string"},
                        "description": "autres langues exigées en plus de l'anglais, ex. 'japonais requis'"},
        "years_required": {"type": "integer", "description": "années d'expérience minimum demandées, 0 si non précisé"},
        "summary": {"type": "string", "description": "2 phrases en français : ce que fait le poste"},
        "reasons": {"type": "array", "items": {"type": "string"},
                    "description": "2 à 4 raisons courtes en français justifiant la note"},
    },
    "required": ["note", "chinese", "years_required", "summary", "reasons"],
}


def _texts(node, out):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "text" and isinstance(v, str) and v.strip():
                out.append(v)
            else:
                _texts(v, out)
    elif isinstance(node, list):
        for v in node:
            _texts(v, out)


def parse_response(data):
    """Extrait l'objet JSON de la réponse, quelle que soit la forme exacte de l'enveloppe."""
    cands = []
    if isinstance(data, dict) and isinstance(data.get("output_text"), str):
        cands.append(data["output_text"])
    _texts(data, cands)
    for text in reversed(cands):
        t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        a, b = t.find("{"), t.rfind("}")
        if a != -1 and b > a:
            try:
                return json.loads(t[a:b + 1])
            except ValueError:
                continue
    if isinstance(data, dict) and "note" in data and "chinese" in data:
        return data
    return None


def _prompt(job_title, company, location, description):
    return (f"Offre : {job_title}\nEntreprise : {company}\nLieu : {location}\n\n"
            f"Description complète :\n{description[:6000]}\n\n"
            "Note l'offre de 0 à 10 pour Théo (10 = parfaite : junior, vente/distribution/IR/produit, "
            "anglais seul suffisant). Retire des points si le chinois est exigé ou si l'expérience demandée dépasse "
            "3 ans. Réponds en JSON avec les champs : note, chinese (non/souhaité/requis), other_langs, "
            "years_required, summary, reasons.")


PROVIDERS = {   # ordre de préférence ; le premier dont la clé existe est utilisé
    "openrouter": {"env": "OPENROUTER_API_KEY", "url": "https://openrouter.ai/api/v1/chat/completions",
                   "model": "openrouter/free"},
    "groq": {"env": "GROQ_API_KEY", "url": "https://api.groq.com/openai/v1/chat/completions",
             "model": "llama-3.3-70b-versatile"},
    "mistral": {"env": "MISTRAL_API_KEY", "url": "https://api.mistral.ai/v1/chat/completions",
                "model": "mistral-small-latest"},
    "gemini": {"env": "GEMINI_API_KEY", "url": URL, "model": DEFAULT_MODEL},
}


def pick_provider(env, wanted=None):
    """Retourne (nom, clé, config) du premier fournisseur disponible, ou None."""
    for name in ([wanted] if wanted else list(PROVIDERS)):
        p = PROVIDERS.get(name)
        if p and (env.get(p["env"]) or "").strip():
            return name, env[p["env"]].strip(), p
    return None


def _json_from_text(text):
    t = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    a, b = t.find("{"), t.rfind("}")
    if a != -1 and b > a:
        try:
            return json.loads(t[a:b + 1])
        except ValueError:
            return None
    return None


def review(job_title, company, location, description, api_key, model=None, provider="gemini"):
    """Retourne un dict {note, chinese, ...} ou None."""
    global LAST_ERROR, RATE_STOP
    if RATE_STOP:
        return None
    cfg = PROVIDERS[provider]
    model = model or cfg["model"]
    prompt = _prompt(job_title, company, location, description)
    if provider == "gemini":
        body = {"model": model, "input": prompt, "system_instruction": SYSTEM,
                "response_format": {"type": "text", "mime_type": "application/json", "schema": SCHEMA}}
        headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    else:   # format OpenAI (OpenRouter, Groq, Mistral)
        body = {"model": model, "messages": [{"role": "system", "content": SYSTEM},
                                              {"role": "user", "content": prompt + "\nRéponds uniquement avec l'objet JSON."}]}
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    for attempt in range(3):
        try:
            r = requests.post(cfg["url"], headers=headers, json=body, timeout=90)
        except requests.RequestException as e:
            LAST_ERROR = LAST_ERROR or f"réseau : {e}"
            return None
        if r.status_code == 429:
            if attempt == 0:
                time.sleep(25)
                continue
            RATE_STOP = True
            LAST_ERROR = LAST_ERROR or f"{provider} : limite gratuite atteinte (429), analyse IA arrêtée pour ce passage"
            return None
        if r.status_code == 400 and "response_format" in body:
            body.pop("response_format")     # on retente sans schéma imposé
            LAST_ERROR = LAST_ERROR or f"400 avec schéma : {r.text[:200]}"
            continue
        if r.status_code >= 400:
            LAST_ERROR = LAST_ERROR or f"{provider} HTTP {r.status_code} : {r.text[:300]}"
            return None
        data = r.json()
        if provider == "gemini":
            res = parse_response(data)
        else:
            try:
                res = _json_from_text(data["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError):
                res = None
        if not res:
            LAST_ERROR = LAST_ERROR or f"{provider} : réponse illisible : {r.text[:300]}"
            return None
        try:
            res["note"] = max(0, min(10, int(res.get("note"))))
        except (TypeError, ValueError):
            LAST_ERROR = LAST_ERROR or f"{provider} : note absente de la réponse"
            return None
        try:
            res["years_required"] = int(float(str(res.get("years_required") or 0).split("-")[0].strip() or 0))
        except (TypeError, ValueError):
            res["years_required"] = 0
        res["model"] = (data.get("model") if isinstance(data, dict) else None) or model
        res["chinese"] = res.get("chinese") if res.get("chinese") in ("non", "souhaité", "requis") else "non"
        return res
    return None
