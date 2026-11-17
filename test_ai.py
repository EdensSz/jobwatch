"""Tests hors ligne de l'analyse IA (aucun appel réseau) : python test_ai.py"""
import json
from unittest import mock

import ai_score
import jobwatch
from filters import Job, full_check
import yaml

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))

# 1) réponse mal formée (listes en texte, notes en texte) -> remise au propre
raw = {"exclude": "false", "role": "Distribution", "level": "Junior", "years_required": "2-3 years",
       "languages_required": "none", "languages_preferred": "Mandarin",
       "scores": {"metier": "4/4", "niveau": 3, "secteur": "2", "atouts": 5},
       "explain": {"metier": ["Vente de fonds.", "Ton cœur de métier."]},
       "summary": "Poste de distribution. Rattaché au head.", "pros": "Bon métier. Anglais suffit.",
       "cons": list("Mandarin apprécié."), "keywords": "RFP, pitch book"}
r = ai_score.normalize(raw)
assert r["scores"] == {"metier": 4, "niveau": 3, "secteur": 2, "atouts": 1}, r["scores"]
assert r["years_required"] == 2 and r["languages_required"] == [] and r["languages_preferred"] == ["Mandarin"]
assert r["pros"] == ["Bon métier.", "Anglais suffit."] and r["cons"] == ["Mandarin apprécié."], r
note, parts = ai_score.parts_of(r)
assert note == 9 and parts[-1]["pts"] == -1, (note, parts)
assert ai_score.normalize({"note": 8}) is None
r2 = ai_score.normalize({**raw, "languages_required": ["Japanese (business level)"], "exclude": False})
assert r2["exclude"] and "Japanese" in r2["exclude_reason"]

# 2) appel simulé : 1re réponse en anglais -> relance en français
class R:
    def __init__(self, content, code=200):
        self.status_code, self.text = code, content
        self._d = {"model": "nvidia/test:free", "choices": [{"message": {"content": content}}]}

    def json(self):
        return self._d

fr = dict(ai_score.EXAMPLE)
en = {**fr, "summary": "The role is for an analyst with the team and it requires experience.",
      "pros": ["The role is good", "It is in the team of the head"]}
seq = iter([R(json.dumps(en)), R("```json\n" + json.dumps(fr, ensure_ascii=False) + "\n```")])
with mock.patch.object(ai_score.requests, "post", side_effect=lambda *a, **k: next(seq)):
    out, prov = ai_score.review("Analyst", "X", "Hong Kong", "desc", {"OPENROUTER_API_KEY": "k"}, cfg["ai"])
assert prov == "openrouter" and out["summary"] == fr["summary"] and out["v"] == ai_score.AI_VERSION, out

# 3) quota Gemini -> bascule sur OpenRouter
seq = iter([R("{}", 429), R("{}", 429), R("{}", 429), R(json.dumps(fr, ensure_ascii=False))])
ai_score.EXHAUSTED.clear()
with mock.patch.object(ai_score.requests, "post", side_effect=lambda *a, **k: next(seq)):
    out, prov = ai_score.review("Analyst", "X", "HK", "desc", {"GEMINI_API_KEY": "g", "OPENROUTER_API_KEY": "k"}, cfg["ai"])
assert prov == "openrouter" and "gemini" in ai_score.EXHAUSTED, (prov, ai_score.EXHAUSTED)

# 4) jobwatch.apply_ai : note décomposée, exclusion langue par l'IA
ai_score.EXHAUSTED.clear()
j = Job("t", "Carlyle", "1", "Analyst", "Hong Kong", "u",
        description="Fund distribution analyst for wholesale clients, asset management. 2 years of experience.")
j.first_seen = "2026-11-17"
v = full_check(j, cfg)
assert v.keep, v.reason
res = ai_score.normalize(dict(fr, v=ai_score.AI_VERSION))
with mock.patch.object(ai_score, "review", return_value=(res, "gemini")), mock.patch.object(jobwatch.time, "sleep"):
    with mock.patch.dict(jobwatch.os.environ, {"GEMINI_API_KEY": "g"}):
        kept = jobwatch.apply_ai([(j, v)], [], {}, cfg)
v2 = kept[0][1]
assert v2.note_source == "ia" and v2.note10 == 9 and len(v2.parts) == 5 and v2.keywords, (v2.note10, v2.parts)
excl = ai_score.normalize(dict(fr, languages_required=["cantonais"], v=ai_score.AI_VERSION))
rej = []
v = full_check(j, cfg)
with mock.patch.object(ai_score, "review", return_value=(excl, "gemini")), mock.patch.object(jobwatch.time, "sleep"):
    with mock.patch.dict(jobwatch.os.environ, {"GEMINI_API_KEY": "g"}):
        kept = jobwatch.apply_ai([(j, v)], rej, {}, cfg)
assert not kept and rej and "cantonais" in rej[0][1].reason, rej
print("test_ai : OK")
