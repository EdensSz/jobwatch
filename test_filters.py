"""Tests hors ligne : cas réels vus pendant la recherche. Lancer : python test_filters.py"""
import yaml
from filters import Job, cheap_check, full_check

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))


def run(title, loc, desc):
    j = Job("t", "t", "1", title, loc, "u", description=desc)
    v = cheap_check(j, cfg)
    return v if not v.keep else full_check(j, cfg)


CASES = [
 ("HSBC Product Specialist", "Product Specialist – Discretionary and Funds", "Hong Kong",
  "Support distribution of funds, client presentations, product knowledge. Mandarin is an added advantage.", True),
 ("Fidelity Client Services Manager", "Client Services Manager", "Hong Kong",
  "Client servicing for institutional and wholesale clients, reporting, product. 3 years experience in asset management.", True),
 ("Fidelity AD sales", "Associate Director, Institutional Sales", "Tokyo", "Sales", False),
 ("Schroders AxJ Analyst 5-7y", "Analyst, AxJ Equities", "Singapore", "Product specialist. 5-7 years of experience required.", False),
 ("Schroders Multi-Asset", "Multi-Asset Product Analyst", "Hong Kong",
  "Support product and client questions, presentations, distribution of funds. Mandarin highly preferred.", True),
 ("Financial Accountant", "Financial Accountant", "Hong Kong", "Fluent in English and Chinese", False),
 ("Wholesale Sales Senior Manager", "Wholesale Sales Senior Manager", "Hong Kong", "Sales of funds", False),
 ("Nomura Corporate Access", "Analyst, Corporate Access", "Singapore",
  "Organise investor meetings, client relationship, sales support for institutional clients.", True),
 ("BNP VIE", "VIE – Sales management & Analytics officer", "Hong Kong",
  "Support sales team, client reporting, asset management distribution.", True),
 ("China focus", "Associate Director, Equity Sales, Asia (China Focus)", "Hong Kong", "Sales", False),
 ("Mandarin required", "Client Service Associate", "Hong Kong",
  "Client servicing for investors and funds. Mandarin required, fluent in spoken and written.", False),
 ("Software", "Software Engineer", "Hong Kong", "Java", False),
 ("Chinese market ok", "Sales Analyst", "Hong Kong",
  "Support fund distribution and client reporting; coverage of the Chinese market and investor clients.", True),
 ("Paris", "Sales Analyst", "Paris", "Fund distribution", False),
]
bad = 0
for name, t, l, d, exp in CASES:
    v = run(t, l, d)
    ok = v.keep == exp
    bad += not ok
    print(("OK " if ok else "KO ") + f"{name:<34} keep={v.keep!s:<5} {v.rating or v.reason} {v.flags}")
print(f"\n{len(CASES)-bad}/{len(CASES)} OK")
raise SystemExit(bad)
