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


def review(job_title, company, location, description, api_key, model=DEFAULT_MODEL):
    """Retourne un dict {note, chinese, ...} ou None."""
    global LAST_ERROR
    body = {"model": model, "input": _prompt(job_title, company, location, description),
            "system_instruction": SYSTEM,
            "response_format": {"type": "text", "mime_type": "application/json", "schema": SCHEMA}}
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    for attempt in range(3):
        try:
            r = requests.post(URL, headers=headers, json=body, timeout=60)
        except requests.RequestException as e:
            LAST_ERROR = LAST_ERROR or f"réseau : {e}"
            return None
        if r.status_code == 429:
            time.sleep(25)
            continue
        if r.status_code == 400 and "response_format" in body:
            body.pop("response_format")     # on retente sans schéma imposé
            LAST_ERROR = LAST_ERROR or f"400 avec schéma : {r.text[:200]}"
            continue
        if r.status_code >= 400:
            LAST_ERROR = LAST_ERROR or f"HTTP {r.status_code} : {r.text[:300]}"
            return None
        res = parse_response(r.json())
        if not res:
            LAST_ERROR = LAST_ERROR or f"réponse illisible : {r.text[:300]}"
            return None
        try:
            res["note"] = max(0, min(10, int(res.get("note"))))
        except (TypeError, ValueError):
            LAST_ERROR = LAST_ERROR or "note absente de la réponse"
            return None
        res["chinese"] = res.get("chinese") if res.get("chinese") in ("non", "souhaité", "requis") else "non"
        return res
    return None
