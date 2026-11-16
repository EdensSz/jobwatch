# jobwatch

Agrège les offres de sites carrières (Workday, Oracle HCM, Eightfold, SmartRecruiters, Greenhouse, Lever, VIE Business France, flux RSS), filtre et produit un flux RSS.

**Filtres appliqués** : lieu (HK / Singapour), pas de séniorité (VP, Director, Senior, Head…), pas de profil technique / risque / compta / ops / trading, pas plus de 3 ans d'expérience demandés, pas de langue asiatique *requise* (chinois « souhaité » = gardé avec un avertissement ⚠). Notes : A (très bon fit) / B / C.

## Utilisation
```
pip install -r requirements.txt
python jobwatch.py --check      # 1) teste chaque source : OK / KO
python jobwatch.py --explain    # 2) run complet + out/rejected.csv (pourquoi chaque offre a été écartée)
python test_filters.py          # tests des filtres (hors ligne)
```
Résultats dans `out/` : `jobs.rss` (à brancher dans Feedly / Inoreader), `jobs.csv`, `jobs.json`, `errors.txt`.

## Ajouter / réparer une source
- `python jobwatch.py --detect "<url page carrières>"` propose la ligne à coller dans `config.yaml`.
- Les sources marquées « À VÉRIFIER » sont écrites de mémoire (tenant / site Workday). Si `--check` donne KO : ouvre la page carrières, copie l'URL, relance `--detect`.
- Schroders (Oracle HCM) : F12 > onglet Réseau > cherche `recruitingCEJobRequisitions` > note le host et `siteNumber`.
- Sites sans API publique : passer par une alerte e-mail ou un flux RSS (type `rss`).
- LinkedIn est volontairement exclu (pas de flux, scraping contraire aux CGU) : utilise les alertes e-mail LinkedIn.

## Automatiser (GitHub Actions)
Dépôt **privé** > ajoute `.github/workflows/jobwatch.yml` (déjà fourni) > Settings > Secrets : `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `MAIL_TO` pour recevoir un e-mail avec les nouvelles offres chaque matin. `state.json` mémorise les offres déjà vues.
