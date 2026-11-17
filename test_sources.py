"""Tests hors ligne des connecteurs (réponses simulées) : python test_sources.py"""
import json
from unittest import mock

import sources


class R:
    def __init__(self, data, code=200, text=None):
        self.status_code, self._d, self.headers = code, data, {}
        self.text = text if text is not None else json.dumps(data)
        self.content = self.text.encode()

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise sources.requests.HTTPError(str(self.status_code))


def fake(routes):
    def get(self, *a, **k):
        url = a[1] if len(a) > 1 and a[0] in ("GET", "POST") else a[0]
        for pat, resp in routes:
            if pat in url:
                return resp(url, k) if callable(resp) else resp
        return R({}, 404)
    return get


# MyCareersFuture
mcf = {"results": [{"uuid": "u1", "title": "Investor Relations Analyst", "postedCompany": {"name": "ACME CAPITAL PTE. LTD."},
                    "metadata": {"jobDetailsUrl": "https://mcf/u1", "newPostingDate": "2026-11-15"}}]}
with mock.patch.object(sources.requests.Session, "request", fake([("v2/jobs/u1", R({"description": "<p>Full text</p>"})),
                                                              ("v2/jobs", R(mcf))])):
    jobs = sources.mycareersfuture({"name": "MCF", "max_pages": 1}, ["investor relations"])
    assert jobs[0].company == "Acme Capital Pte. Ltd." and jobs[0].posted == "2026-11-15"
    assert jobs[0].desc_loader() == "<p>Full text</p>"

# JobsDB (SEEK)
sk = {"data": [{"id": 99, "title": "Fund Distribution Analyst", "companyName": "Big AM", "locations": [{"label": "Central, Hong Kong"}],
                "listingDate": "2026-11-16T01:00:00Z", "teaser": "Support wholesale", "bulletPoints": ["Asset management"]}]}
page = '<script>window.SEEK_REDUX_DATA = {"content":"' + "<p>" + "Full description of the role. " * 10 + '</p>","x":1}</script>'
with mock.patch.object(sources.requests.Session, "request", fake([("/api/jobsearch", R(sk)), ("/job/99", R({}, text=page))])):
    jobs = sources.seek({"name": "JobsDB", "host": "hk.jobsdb.com", "site_key": "HK-Main", "max_pages": 1}, ["distribution"])
    assert jobs[0].location == "Central, Hong Kong" and "Full description" in jobs[0].desc_loader()

# découverte automatique : Lever trouvé au 2e essai, gardé en cache
sources.ATS_CACHE.clear()
lever_jobs = [{"id": "L1", "text": "Business Development Analyst", "categories": {"location": "Singapore"},
               "hostedUrl": "https://jobs.lever.co/acme/L1", "createdAt": 1794700000000, "descriptionPlain": "BD role"}]
_lev = fake([("api.lever.co/v0/postings/acmepay", R(lever_jobs))])
with mock.patch.object(sources.requests.Session, "request", _lev), mock.patch.object(sources.requests.Session, "get", _lev):
    jobs = sources.auto({"name": "Acme Pay", "type": "auto"}, ["Singapore"])
assert sources.ATS_CACHE["Acme Pay"]["ats"] == "lever" and jobs and jobs[0].title == "Business Development Analyst"
with mock.patch.object(sources.requests.Session, "request", fake([])), mock.patch.object(sources.requests.Session, "get", fake([])):
    assert sources.auto({"name": "Nobody Corp", "type": "auto"}, []) == []
assert sources.ATS_CACHE["Nobody Corp"]["ats"] is None

# Ashby
ash = {"jobs": [{"id": "a1", "title": "Partnerships Associate", "location": "Tokyo", "secondaryLocations": [{"location": "Singapore"}],
                 "jobUrl": "https://jobs.ashbyhq.com/x/a1", "publishedAt": "2026-11-10T00:00:00Z", "descriptionHtml": "<p>x</p>"}]}
with mock.patch.object(sources.requests.Session, "request", fake([("ashbyhq", R(ash))])):
    jobs = sources.ashby({"name": "X", "board": "x"}, [])
    assert jobs[0].location == "Tokyo ; Singapore"
# Workday : 1 requête pour les filtres de lieu, puis seulement les offres HK / SG / Japon (avec reprise sur 429)
facets = {"total": 5000, "jobPostings": [], "facets": [
    {"facetParameter": "locationMainGroup", "values": [
        {"facetParameter": "locationCountry", "values": [
            {"descriptor": "Hong Kong", "id": "HK1", "count": 30}, {"descriptor": "Japan", "id": "JP1", "count": 4},
            {"descriptor": "France", "id": "FR1", "count": 90}]}]}]}
page1 = {"total": 21, "jobPostings": [{"title": f"Sales Analyst {i}", "externalPath": f"/job/{i}", "locationsText": "Hong Kong",
                                        "postedOn": "Posted Today"} for i in range(20)]}
page2 = {"total": 0, "jobPostings": [{"title": "Last", "externalPath": "/job/x", "locationsText": "Tokyo", "postedOn": "Posted Today"}]}
bodies = []
seq = iter([R({}, 429), R(facets), R(page1), R(page2)])
def wd(self, method, url, **k):
    bodies.append(k.get("json"))
    return next(seq)
with mock.patch.object(sources.requests.Session, "request", wd), mock.patch.object(sources.time, "sleep"):
    sources.WORKDAY_BLOCKED["n"] = 0
    jobs = sources.workday({"name": "W", "url": "https://acme.wd3.myworkdayjobs.com/en-US/Ext"}, ["Hong Kong", "Singapore", "Tokyo"])
assert len(jobs) == 21, len(jobs)                                   # total lu à la 1re page seulement
assert bodies[2]["appliedFacets"] == {"locationCountry": ["HK1", "JP1"]} and bodies[2]["searchText"] == ""
assert len(bodies) == 4                                             # 1 refus + 1 filtres + 2 pages (au lieu de 3 recherches)

# coupe-circuit : 3 sites Workday refusés de suite -> les suivants sont sautés sans requête
calls = []
def deny(self, *a, **k):
    calls.append(1); return R({}, 429)
with mock.patch.object(sources.requests.Session, "request", deny), mock.patch.object(sources.time, "sleep"):
    sources.WORKDAY_BLOCKED["n"] = 0
    for i in range(5):
        try:
            sources.workday({"name": f"W{i}", "url": f"https://t{i}.wd1.myworkdayjobs.com/Ext"}, ["Hong Kong"])
        except sources.requests.HTTPError:
            pass
assert len(calls) == 9, len(calls)                                  # 3 sites x 3 essais, puis plus rien
sources.WORKDAY_BLOCKED["n"] = 0
print("test_sources : OK")

# Jooble (clé par pays) et Careerjet v4 (auth Basic)
import os
calls = []
def jpost(self, *a, **k):
    calls.append(a[1]); return R({"jobs": [{"id": 1, "title": "Investor Relations Analyst", "company": "X", "location": "Central, Hong Kong",
                                           "link": "https://hk.jooble.org/1", "snippet": "IR role", "updated": "2026-11-16T00:00:00"}]})
with mock.patch.dict(os.environ, {"JOOBLE_KEYS": "hk:K1"}), mock.patch.object(sources.requests.Session, "request", jpost), \
        mock.patch.object(sources.time, "sleep"):
    jobs = sources.jooble({"name": "Jooble"}, ["investor relations"])
    assert calls[0] == "https://hk.jooble.org/api/K1" and jobs[0].location == "Central, Hong Kong"
seen_auth = []
def cget(self, url, **k):
    if "ipify" in url:
        return R({}, text="9.9.9.9")
    seen_auth.append((k.get("auth"), k["params"]["locale_code"], k["params"]["user_ip"]))
    if k["params"]["locale_code"] == "ja_JP":
        return R({}, 400, text="Unsupported locale code")
    return R({"type": "JOBS", "jobs": [{"title": "Fund Sales Analyst", "company": "Y", "locations": "Hong Kong",
                                         "url": "https://jobviewtrack.com/" + k["params"]["locale_code"],
                                         "date": "Wed, 15 Nov 2026 19:13:43 GMT", "description": "fund sales"}]})
with mock.patch.dict(os.environ, {"CAREERJET_API_KEY": "CK"}), mock.patch.object(sources.requests.Session, "get", cget), \
        mock.patch.object(sources.time, "sleep"):
    jobs = sources.careerjet({"name": "Careerjet"}, ["fund sales"])
    assert seen_auth[0] == (("CK", ""), "en_HK", "9.9.9.9") and len(jobs) == 2 and jobs[0].posted == "2026-11-15"
print("test_sources (agrégateurs) : OK")
