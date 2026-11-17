# jobwatch

Veille d'offres pour Théo : postes Analyst / junior en IR, distribution, client coverage et product specialist à **Hong Kong, Singapour et Tokyo**, en anglais uniquement. GitHub lit les sites carrières 4 fois par jour, analyse chaque annonce complète (règles + IA avec ton CV) et publie la page `index.html` (GitHub Pages).

## Ce qui est filtré
- Lieu hors HK / Singapour / Tokyo, titres seniors (VP, Director, Senior, Head…), métiers hors cible (tech, compta, risque, conformité, ops…).
- Plus de 3 ans d'expérience demandés.
- **Toute autre langue que français / anglais exigée ou fortement souhaitée** (mandarin, cantonais, japonais, coréen, langue locale, annonce rédigée en japonais/chinois…) : offre exclue. Une langue seulement « un plus » : offre gardée avec -1, masquée par le filtre de base « Aucune autre langue ».

## La note /10
Métier /4 + Niveau /3 + Secteur /2 + Atouts /1 (-1 si une autre langue est « un plus »). Chaque critère est expliqué dans la fiche de l'offre. Les règles donnent une première note ; l'IA la remplace ensuite en lisant l'annonce avec ton CV (`cv.json`).

## IA gratuite (GitHub > Settings > Secrets and variables > Actions)
| Secret | Où l'obtenir | Quota gratuit |
|---|---|---|
| `GEMINI_API_KEY` (recommandé) | aistudio.google.com/apikey | ~500 analyses/jour (gemini-3.5-flash-lite) |
| `OPENROUTER_API_KEY` (secours) | openrouter.ai/keys | ~50/jour sans crédit (Nemotron :free) |

Les modèles et budgets sont dans `config.yaml` (section `ai`). Si un quota est atteint, le script passe au fournisseur suivant.

## CV adapté (depuis le téléphone)
Dans une offre : **CV adapté (PDF)**. L'IA part de `cv.json`, reprend les mots-clés de l'annonce, renomme et réécrit les missions, peut ajouter des outils ; employeurs, dates, diplômes, langues et certifications restent verrouillés. PDF texte (lisible par les ATS), une page. Réglages (roue dentée) : clé Gemini et coordonnées, gardées sur le téléphone uniquement.

## Commandes
```
pip install -r requirements.txt
python jobwatch.py --check      # teste chaque source : OK / KO
python jobwatch.py --explain    # passage complet + out/rejected.csv (raison de chaque rejet)
python test_filters.py          # tests des règles (hors ligne)
python test_ai.py               # tests de l'analyse IA (hors ligne)
```
Ajouter un site : `python jobwatch.py --detect "<url de la page carrières>"` puis coller la ligne dans `config.yaml`.

## Sources (≈ 400)
- **Sites carrières directs** (`sources` dans `config.yaml`) : Workday, Oracle, Eightfold, Greenhouse, Lever, SmartRecruiters, Amazon, VIE Business France.
- **Grands groupes en découverte automatique** (`companies`) : le script trouve seul leur site d'offres (Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee) et le garde dans `ats_cache.json`. Ceux qui ont un site maison sont listés dans les jauges.
- **Agrégateurs** (`aggregators`, recherche par métier) : MyCareersFuture (toutes les offres de Singapour) et JobStreet (Singapour) sans clé (JobsDB et le site VIE refusent les accès automatiques : utiliser leurs alertes e-mail) ; Jooble, Adzuna, Careerjet avec clé gratuite (secrets `JOOBLE_KEYS` au format `hk:CLÉ,sg:CLÉ`, `ADZUNA_APP_ID` + `ADZUNA_APP_KEY`, `CAREERJET_API_KEY`).
- Une source qui échoue 3 passages de suite n'est plus réessayée qu'une fois par jour. Les doublons entre sources sont retirés.
- `python test_sources.py` : tests des connecteurs (hors ligne).
