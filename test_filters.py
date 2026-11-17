"""Tests hors ligne des règles (cas réels). Lancer : python test_filters.py"""
import yaml
from filters import Job, cheap_check, full_check, min_years_required, language_requirements

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))

CARLYLE = """Carlyle AlpInvest seeks to hire an Analyst with a focus on primary fund investments with also exposure to
secondary and co-investment activities. The Analyst is a two-year program (with optional 6 months extension).
• Constructing detailed financial models, performance due diligence on fund investment opportunities
• Preparing periodic investor updates and conducting analyses and support work in developing relationships LP base and fundraising activities
Minimum of 2 years of relevant work experience at an investment bank, private equity firm, management consulting
• Excellent written and spoken English is required
• Knowledge of a second Asian language is considered an advantage
Carlyle AlpInvest is a leading global private equity investor with over USD 102 billion of assets under management"""


def run(title, loc, desc, source="t"):
    j = Job(source, "t", "1", title, loc, "u", description=desc)
    v = cheap_check(j, cfg)
    return v if not v.keep else full_check(j, cfg)


# (nom, intitulé, lieu, annonce, gardée ?, note mini, note maxi)
CASES = [
 ("HSBC Product Specialist (mandarin = plus)", "Product Specialist – Discretionary and Funds", "Hong Kong",
  "Support distribution of funds, client presentations, product knowledge. Mandarin is an added advantage.", True, 4, 8),
 ("Fidelity CSM 3 ans", "Client Services Manager", "Hong Kong",
  "Client servicing for institutional and wholesale clients, reporting, product. 3 years experience in asset management.", True, 5, 9),
 ("Fidelity AD sales", "Associate Director, Institutional Sales", "Tokyo", "Sales", False, 0, 0),
 ("Schroders 5-7 ans", "Analyst, AxJ Equities", "Singapore", "Product specialist. 5-7 years of experience required.", False, 0, 0),
 ("Chinois fortement souhaité = exclu", "Product Analyst, Multi-Asset", "Hong Kong",
  "Product analyst for multi-asset funds. Mandarin highly preferred.", False, 0, 0),
 ("Comptable", "Financial Accountant", "Hong Kong", "", False, 0, 0),
 ("Senior", "Wholesale Sales Senior Manager", "Singapore", "", False, 0, 0),
 ("Corporate access", "Corporate Access Analyst", "Hong Kong",
  "Organise roadshows for institutional investors. Fluent English.", True, 7, 10),
 ("VIE BNP", "VIE - Sales Assistant Asset Management", "Singapore",
  "Support sales team of BNP Paribas Asset Management, institutional clients, French is a plus.", True, 8, 10),
 ("Mandarin requis", "Client Service Analyst", "Hong Kong", "Fluent in English and Mandarin.", False, 0, 0),
 ("Cantonais requis", "Investor Relations Analyst", "Hong Kong", "Proficiency in Cantonese is essential.", False, 0, 0),
 ("Japonais requis (JLPT)", "Sales Analyst", "Tokyo", "Investor sales for funds. JLPT N1 required.", False, 0, 0),
 ("Coréen requis", "Distribution Analyst", "Singapore", "Wholesale distribution. Korean speaking required.", False, 0, 0),
 ("Annonce en japonais", "Sales Analyst", "Tokyo", "投資信託の販売会社向け営業を担当します。日本語ネイティブレベル必須。資産運用会社での経験歓迎。", False, 0, 0),
 ("Clients chinois ≠ langue", "Investor Relations Associate", "Singapore",
  "Cover Chinese investors and Japanese clients for private equity funds. 2 years of experience.", True, 8, 10),
 ("Logiciel", "Software Engineer", "Singapore", "", False, 0, 0),
 ("Tokyo anglais OK", "Client Service Analyst", "Tokyo, Japan",
  "Client reporting and RFP for asset management institutional clients. English is the working language.", True, 7, 10),
 ("Carlyle Analyst = investissement", "Analyst", "Hong Kong", CARLYLE, True, 2, 6),
 ("Paris", "Sales Analyst", "Paris", "", False, 0, 0),
 ("Sales & trading graduate", "Sales & Trading Graduate Programme", "Hong Kong",
  "Graduate programme in global markets at a bank.", True, 5, 9),
 ("Vente tech", "Account Executive, SMB", "Singapore", "Sell our SaaS platform to SMB customers.", True, 2, 6),
]

ok = 0
for name, title, loc, desc, keep, lo, hi in CASES:
    v = run(title, loc, desc)
    good = v.keep == keep and (not keep or lo <= (v.note10 or 0) <= hi)
    ok += good
    print(f"{'OK ' if good else 'KO '} {name:<38} keep={v.keep!s:<5} note={v.note10} {v.reason or ''} {v.other_langs}")
    if not good:
        for w in v.why:
            print("      ", w)

assert min_years_required("at least five (5) years of experience") == 5
assert min_years_required("The Analyst is a two-year program. Minimum of 2 years of relevant work experience") == 2
assert min_years_required("3-5 years' experience in sales") == 3
assert language_requirements("Fluent English; Mandarin would be an advantage") == {"chinois": "preferred"}
assert language_requirements("Bilingual in English and French") == {}
print(f"\n{ok}/{len(CASES)} OK")
assert ok == len(CASES)
