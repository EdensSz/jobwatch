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
# Workday saturé (429 puis 200) : on réessaie au lieu de planter avec « NoneType has no attribute json »
seq = iter([R({}, 429), R({}, 503), R({"total": 1, "jobPostings": [{"title": "Sales Analyst", "externalPath": "/job/x",
                                                               "locationsText": "Hong Kong", "postedOn": "Posted Today"}]})])
with mock.patch.object(sources.requests.Session, "request", lambda self, *a, **k: next(seq)), mock.patch.object(sources.time, "sleep"):
    jobs = sources.workday({"name": "W", "url": "https://acme.wd3.myworkdayjobs.com/en-US/Ext"}, ["Hong Kong"])
    assert jobs[0].title == "Sales Analyst"
seq = iter([R({}, 429)] * 5)
with mock.patch.object(sources.requests.Session, "request", lambda self, *a, **k: next(seq)), mock.patch.object(sources.time, "sleep"):
    try:
        sources.workday({"name": "W", "url": "https://acme.wd3.myworkdayjobs.com/en-US/Ext"}, ["Hong Kong"])
        raise SystemExit("aurait dû lever une erreur")
    except sources.requests.HTTPError as e:
        assert "429" in str(e)
print("test_sources : OK")
