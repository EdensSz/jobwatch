"""Connecteurs vers les sites de carrières. Chaque connecteur renvoie une liste de Job
(avec un `desc_loader` pour lire la description seulement si l'offre passe le premier filtre)."""
import html
import re
import time
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List
from urllib.parse import quote, urlparse

import requests

from filters import Job

UA = {"User-Agent": "Mozilla/5.0 (jobwatch personal job-search tool)", "Accept": "application/json",
      "Accept-Language": "en-US,en;q=0.9"}
TIMEOUT = 30
PAUSE = 0.4


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(UA)
    return s


import threading
from urllib.parse import urlparse as _up

# Workday limite le nombre de requêtes par IP (429) : on reste poli. Une seule requête Workday à la fois sur tout
# le passage, espacées d'au moins 2 s, et on respecte le délai demandé par le site avant de réessayer.
_HOST_LIMITS = {"myworkdayjobs.com": threading.BoundedSemaphore(1)}
_MIN_GAP = {"myworkdayjobs.com": 2.0}
_LAST = {}


def _limit(url):
    h = _up(url).netloc
    for dom, sem in _HOST_LIMITS.items():
        if h.endswith(dom):
            return sem
    return None


def _req(method, s, url, **kw):
    """Requête avec reprises : 429 / 5xx -> on attend (Retry-After si donné) et on réessaie 5 fois.
    Lève toujours une vraie erreur à la fin (jamais None)."""
    sem = _limit(url)
    host = _up(url).netloc
    dom = next((d for d in _MIN_GAP if host.endswith(d)), None)
    last = None
    tries = 2 if dom else 5            # Workday : un seul nouvel essai, inutile d'insister quand il bloque
    for attempt in range(tries):
        if sem:
            sem.acquire()
        try:
            if dom:
                wait_ = _LAST.get(dom, 0) + _MIN_GAP[dom] - time.time()
                if wait_ > 0:
                    time.sleep(wait_)
                _LAST[dom] = time.time()
            r = s.request(method, url, timeout=TIMEOUT, **kw)
        except requests.RequestException as e:
            last = e
            r = None
        finally:
            if sem:
                sem.release()
        if r is not None:
            if r.status_code in (429, 500, 502, 503, 504):
                last = requests.HTTPError(f"{r.status_code} après {attempt + 1} essai(s) pour {url[:120]}")
                wait = r.headers.get("Retry-After", "")
                pause = float(wait) if wait.replace(".", "").isdigit() else \
                    (20 if r.status_code == 429 else 2 * 2 ** attempt)
                pause = min(90, pause)
                if dom and r.status_code == 429:          # toutes les requêtes Workday attendent aussi
                    _LAST[dom] = time.time() + pause
                time.sleep(pause)
                continue
            r.raise_for_status()
            return r
        time.sleep(2 * 2 ** attempt)
    raise last or requests.HTTPError(f"échec pour {url[:120]}")


def _get(s, url, **kw):
    return _req("GET", s, url, **kw)


def _post(s, url, body, **kw):
    return _req("POST", s, url, json=body, **kw)


def _iso(value) -> str:
    """Convertit une date (ISO, timestamp, texte) en AAAA-MM-JJ ; '' si inconnue."""
    if not value:
        return ""
    if isinstance(value, (int, float)):
        v = value / 1000 if value > 1e11 else value
        return datetime.utcfromtimestamp(v).date().isoformat()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(value))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


# --------------------------------------------------------------------------- Workday
def _workday_posted(text: str) -> str:
    t = (text or "").lower()
    if "today" in t or "aujourd" in t:
        return date.today().isoformat()
    if "yesterday" in t or "hier" in t:
        return (date.today() - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*(?:days?|jours?)", t)
    return (date.today() - timedelta(days=int(m.group(1)))).isoformat() if m else ""


WD_CITY_RE = re.compile(r"hong ?kong|singapore|japan|tokyo|香港|東京|日本", re.I)
WORKDAY_BLOCKED = {"n": 0}          # coupe-circuit : après 4 sites Workday refusés, on arrête Workday pour ce passage


def _wd_location_facets(facets) -> Dict[str, List[str]]:
    """Dans la liste « facets » de la 1re réponse, trouve les filtres de lieu qui correspondent à HK / Singapour / Japon.
    Retourne {nom_du_filtre: [ids]} (le filtre le plus précis : pays de préférence)."""
    found: Dict[str, List[str]] = {}

    def walk(fs):
        for f in fs or []:
            param = f.get("facetParameter") or ""
            for v in f.get("values") or []:
                if v.get("facetParameter") or (v.get("values") and not v.get("id")):
                    walk([v])                                  # groupe imbriqué (ex. locationMainGroup)
                    continue
                if v.get("id") and WD_CITY_RE.search(v.get("descriptor") or ""):
                    found.setdefault(param, []).append(v["id"])
    walk(facets)
    if not found:
        return {}
    best = sorted(found, key=lambda k: (0 if "country" in k.lower() else 1, -len(found[k])))[0]
    return {best: found[best]}


def workday(src: Dict, terms: List[str]) -> List[Job]:
    """Workday, en économisant les requêtes : 1 requête pour lire les filtres de lieu du site, puis seulement les
    pages des offres à Hong Kong / Singapour / Japon (au lieu d'une recherche par ville). Repli : recherche par ville."""
    if WORKDAY_BLOCKED["n"] >= 4:
        raise requests.HTTPError("Workday refuse les requêtes depuis ce serveur : site sauté pour ce passage")
    if "url" in src:
        u = urlparse(src["url"])
        host = u.netloc
        tenant = host.split(".")[0]
        parts = [p for p in u.path.split("/") if p and not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)]
        site = parts[0] if parts else src["site"]
    else:
        host, tenant, site = src["host"], src["tenant"], src["site"]
    base = f"https://{host}/wday/cxs/{tenant}/{site}"
    s = _session()
    try:
        first = _post(s, f"{base}/jobs", {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}).json()
    except requests.HTTPError as e:
        if "429" in str(e):
            WORKDAY_BLOCKED["n"] += 1
        raise
    facet = _wd_location_facets(first.get("facets"))
    queries = [(facet, "")] if facet else [({}, t) for t in terms]
    seen, jobs = set(), []
    for applied, text in queries:
        offset, total = 0, None
        for _ in range(src.get("max_pages", 60)):
            data = _post(s, f"{base}/jobs", {"appliedFacets": applied, "limit": 20, "offset": offset,
                                              "searchText": text}).json()
            if total is None:                       # Workday ne donne le total qu'à la 1re page
                total = data.get("total") or 0
            postings = data.get("jobPostings", [])
            if not postings:
                break
            for p in postings:
                path = p.get("externalPath")
                if not path or path in seen:
                    continue
                seen.add(path)
                jobs.append(_workday_job(src, s, host, base, site, p))
            offset += 20
            if offset >= total:
                break
    return jobs


def _workday_job(src, s, host, base, site, p) -> Job:
    path = p["externalPath"]
    bullets = p.get("bulletFields") or []          # certains tenants (ex. Fidelity) renvoient ceci
    location = p.get("locationsText") or (bullets[0] if bullets else "")
    posted = _workday_posted(p.get("postedOn", ""))
    for b in bullets:                              # "Posting Date: 26/10/2026"
        m = re.search(r"(\d{2})/(\d{2})/(\d{4})", b)
        if m and not posted:
            posted = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    job = Job(source=src["name"], company=src["name"], id=path, title=p.get("title", ""),
              location=location, url=f"https://{host}/en-US/{site}{path}", posted=posted or None)

    def load() -> str:
        info = _get(s, f"{base}{path}").json().get("jobPostingInfo", {})
        extra = [info.get("location", "")] + (info.get("additionalLocations") or [])
        job.extra_location = " ".join(x for x in extra if x)
        if info.get("startDate"):
            job.posted = job.posted or _iso(info["startDate"])
        return info.get("jobDescription", "")
    job.desc_loader = load
    return job


# --------------------------------------------------------------------------- Oracle HCM (Schroders, JPMorgan...)
def oracle(src: Dict, terms: List[str]) -> List[Job]:
    """src: name, host, site_number (ex. CX_1001), site_name. (JPMorgan : jpmc.fa.oraclecloud.com / CX_1001)"""
    host, site = src["host"], src["site_number"]
    api = f"https://{host}/hcmRestApi/resources/latest"
    s = _session()
    seen, jobs = set(), []
    for term in terms:
        offset = 0
        for _ in range(src.get("max_pages", 80)):
            finder = (f"findReqs;siteNumber={site},facetsList=LOCATIONS,limit=25,offset={offset},"
                      f"keyword={term},sortBy=POSTING_DATES_DESC")
            r = _get(s, f"{api}/recruitingCEJobRequisitions",
                     params={"onlyData": "true", "expand": "requisitionList.secondaryLocations", "finder": finder})
            items = r.json().get("items", [])
            first = items[0] if items else {}
            reqs = first.get("requisitionList", [])
            if not reqs:
                break
            for q in reqs:
                rid = str(q.get("Id"))
                if rid in seen:
                    continue
                seen.add(rid)
                sec = " ".join(x.get("Name", "") for x in q.get("secondaryLocations", []) or [])
                short = " ".join(str(q.get(k) or "") for k in
                                 ("ShortDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr"))
                job = Job(source=src["name"], company=src["name"], id=rid, title=q.get("Title", ""),
                          location=q.get("PrimaryLocation", ""), extra_location=sec,
                          url=f"https://{host}/hcmUI/CandidateExperience/en/sites/{src.get('site_name', site)}/job/{rid}",
                          posted=_iso(q.get("PostedDate")) or None)

                def load(rid=rid, short=short) -> str:
                    """Annonce complète (la liste ne donne qu'un résumé)."""
                    try:
                        d = _get(s, f"{api}/recruitingCEJobRequisitionDetails",
                                 params={"onlyData": "true", "expand": "all",
                                         "finder": f'ById;Id="{rid}",siteNumber={site}'}).json()
                        it = (d.get("items") or [{}])[0]
                        full = "\n\n".join(str(it.get(k) or "") for k in
                                            ("ExternalDescriptionStr", "ExternalResponsibilitiesStr",
                                             "ExternalQualificationsStr", "CorporateDescriptionStr") if it.get(k))
                        return full or short
                    except Exception:
                        return short
                job.desc_loader = load
                jobs.append(job)
            offset += len(reqs)
            if offset >= (first.get("TotalJobsCount") or 0):
                break
            time.sleep(PAUSE)
    return jobs


# --------------------------------------------------------------------------- Eightfold (HSBC...)
def eightfold(src: Dict, terms: List[str]) -> List[Job]:
    """src: name, host, domain. (HSBC : host portal.careers.hsbc.com, domain hsbc.com)"""
    host, domain = src["host"], src["domain"]
    s = _session()
    seen, jobs = set(), []
    for term in terms:
        start = 0
        for _ in range(src.get("max_pages", 100)):
            r = _get(s, f"https://{host}/api/apply/v2/jobs",
                     params={"domain": domain, "start": start, "num": 25, "location": term, "sort_by": "timestamp"})
            data = r.json()
            pos = data.get("positions", [])
            if not pos:
                break
            for q in pos:
                pid = str(q.get("id"))
                if pid in seen:
                    continue
                seen.add(pid)
                locs = q.get("locations") or [q.get("location", "")]
                job = Job(source=src["name"], company=src["name"], id=pid, title=q.get("name", ""),
                          location="; ".join(locs), url=q.get("canonicalPositionUrl") or f"https://{host}/careers/job/{pid}",
                          posted=_iso(q.get("t_update") or q.get("t_create")) or None,
                          description=q.get("job_description") or "")
                if not job.description:
                    def load(pid=pid) -> str:
                        d = _get(s, f"https://{host}/api/apply/v2/jobs/{pid}", params={"domain": domain}).json()
                        return d.get("job_description", "")
                    job.desc_loader = load
                jobs.append(job)
            start += len(pos)
            if start >= (data.get("count") or 0):
                break
            time.sleep(PAUSE)
    return jobs


# --------------------------------------------------------------------------- SmartRecruiters
def smartrecruiters(src: Dict, terms: List[str]) -> List[Job]:
    """src: name, company (identifiant SmartRecruiters)."""
    s = _session()
    seen, jobs = set(), []
    for term in terms:
        offset = 0
        for _ in range(src.get("max_pages", 20)):
            r = _get(s, f"https://api.smartrecruiters.com/v1/companies/{src['company']}/postings",
                     params={"limit": 100, "offset": offset, "q": term})
            content = r.json().get("content", [])
            if not content:
                break
            for q in content:
                pid = q["id"]
                if pid in seen:
                    continue
                seen.add(pid)
                loc = q.get("location", {})
                job = Job(source=src["name"], company=src["name"], id=pid, title=q.get("name", ""),
                          location=", ".join(x for x in [loc.get("city"), loc.get("country")] if x),
                          url=f"https://jobs.smartrecruiters.com/{src['company']}/{pid}",
                          posted=_iso(q.get("releasedDate")) or None)

                def load(pid=pid) -> str:
                    d = _get(s, f"https://api.smartrecruiters.com/v1/companies/{src['company']}/postings/{pid}").json()
                    secs = (d.get("jobAd") or {}).get("sections", {})
                    return "\n\n".join((v or {}).get("text", "") for v in secs.values() if v)
                job.desc_loader = load
                jobs.append(job)
            if len(content) < 100:
                break
            offset += 100
            time.sleep(PAUSE)
    return jobs


# --------------------------------------------------------------------------- Greenhouse / Lever
def greenhouse(src: Dict, terms: List[str]) -> List[Job]:
    s = _session()
    data = _get(s, f"https://boards-api.greenhouse.io/v1/boards/{src['board']}/jobs", params={"content": "true"}).json()
    jobs = []
    for q in data.get("jobs", []):
        job = Job(source=src["name"], company=src["name"], id=str(q["id"]), title=q.get("title", ""),
                  location=" ; ".join([(q.get("location") or {}).get("name", "")] +
                                      [o.get("name", "") for o in q.get("offices") or []]),
                  url=q.get("absolute_url", ""),
                  posted=_iso(q.get("updated_at")) or None, description=q.get("content", ""))
        jobs.append(job)
    return jobs


def lever(src: Dict, terms: List[str]) -> List[Job]:
    s = _session()
    data = _get(s, f"https://api.lever.co/v0/postings/{src['company']}", params={"mode": "json"}).json()
    def full(q) -> str:
        """Annonce complète : description + listes (missions, profil) + texte additionnel."""
        parts = [q.get("description") or q.get("descriptionPlain", "")]
        for lst in q.get("lists") or []:
            parts.append(f"<h3>{lst.get('text', '')}</h3><ul>{lst.get('content', '')}</ul>")
        parts.append(q.get("additional") or q.get("additionalPlain", ""))
        return "\n".join(p for p in parts if p)
    cats = lambda q: q.get("categories") or {}
    return [Job(source=src["name"], company=src["name"], id=q["id"], title=q.get("text", ""),
                location=" ; ".join([cats(q).get("location", "")] + (cats(q).get("allLocations") or [])),
                url=q.get("hostedUrl", ""), posted=_iso(q.get("createdAt")) or None, description=full(q))
            for q in data]


# --------------------------------------------------------------------------- VIE Business France (Civiweb)
def civiweb(src: Dict, terms: List[str]) -> List[Job]:
    """Offres V.I.E / V.I.A de Business France. Les mots de recherche sont ceux de `terms`
    (ex. Hong Kong), la localisation est ensuite vérifiée par les filtres."""
    api = "https://api-mon-vie-via.businessfrance.fr/api/Offers"
    s = _session()
    seen, jobs = set(), []
    for term in terms:
        skip = 0
        for _ in range(src.get("max_pages", 30)):
            body = {"limit": 50, "skip": skip, "query": term, "activitySectorId": [], "missionsTypesIds": [],
                    "missionsDurations": [], "gerographicZones": [], "countriesIds": [], "studiesLevelId": [],
                    "companiesSizes": [], "specializationsIds": [], "entreprisesIds": [0], "missionStartDate": None}
            r = _post(s, f"{api}/search", body)
            res = r.json().get("result") or []
            if not res:
                break
            for q in res:
                oid = str(q.get("id"))
                if oid in seen:
                    continue
                seen.add(oid)
                loc = ", ".join(x for x in [q.get("cityNameEn") or q.get("cityName"),
                                            q.get("countryNameEn") or q.get("countryName")] if x)
                job = Job(source=src["name"], company=q.get("organizationName") or src["name"], id=oid,
                          title=q.get("missionTitle", ""), location=loc,
                          url=f"https://mon-vie-via.businessfrance.fr/en/offres/{oid}",
                          posted=_iso(q.get("startBroadcastDate") or q.get("creationDate")) or None,
                          description=" ".join(str(q.get(k) or "") for k in ("missionDescription", "missionProfile")))
                jobs.append(job)
            if len(res) < 50:
                break
            skip += 50
            time.sleep(PAUSE)
    return jobs


# --------------------------------------------------------------------------- flux RSS existant (alertes, Feedity, etc.)
def rss(src: Dict, terms: List[str]) -> List[Job]:
    import xml.etree.ElementTree as ET
    s = _session()
    root = ET.fromstring(_get(s, src["url"]).content)
    jobs = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        jobs.append(Job(source=src["name"], company=src["name"], id=it.findtext("guid") or link,
                        title=(it.findtext("title") or "").strip(),
                        location=src.get("location_hint", ""), url=link,
                        description=it.findtext("description") or ""))
    return jobs


# --------------------------------------------------------------------------- Eightfold « PCSX » (Microsoft, PayPal...)
def pcsx(src: Dict, terms: List[str]) -> List[Job]:
    """src: name, host, domain. (Microsoft : host apply.careers.microsoft.com, domain microsoft.com)"""
    host, domain = src["host"], src["domain"]
    s = _session()
    seen, jobs = set(), []
    for term in terms:
        start = 0
        for _ in range(src.get("max_pages", 100)):
            data = (_get(s, f"https://{host}/api/pcsx/search",
                         params={"domain": domain, "query": "", "location": term, "start": start,
                                 "sort_by": "timestamp"}).json().get("data") or {})
            pos = data.get("positions") or []
            if not pos:
                break
            for q in pos:
                pid = str(q.get("id"))
                if pid in seen:
                    continue
                seen.add(pid)
                job = Job(source=src["name"], company=src["name"], id=pid, title=q.get("name", ""),
                          location="; ".join(q.get("locations") or []),
                          url=f"https://{host}{q.get('positionUrl') or '/careers/job/' + pid}",
                          posted=_iso(q.get("postedTs") or q.get("creationTs")) or None)

                def load(pid=pid) -> str:
                    d = _get(s, f"https://{host}/api/pcsx/position_details",
                             params={"position_id": pid, "domain": domain, "hl": "en"}).json()
                    return (d.get("data") or {}).get("jobDescription", "")
                job.desc_loader = load
                jobs.append(job)
            start += len(pos)
            if start >= (data.get("count") or 0):
                break
            time.sleep(PAUSE)
    return jobs


CONNECTORS: Dict[str, Callable[[Dict, List[str]], List[Job]]] = {
    "workday": workday, "oracle": oracle, "eightfold": eightfold, "smartrecruiters": smartrecruiters,
    "greenhouse": greenhouse, "lever": lever, "civiweb": civiweb, "rss": rss, "pcsx": pcsx,
}


def detect(url: str) -> str:
    """Devine le type de site de carrières à partir d'une URL, et propose la ligne de config."""
    u = urlparse(url)
    h = u.netloc.lower()
    if "myworkdayjobs.com" in h:
        return f'- {{name: "{h.split(".")[0]}", type: workday, url: "{url}"}}'
    if "oraclecloud.com" in h:
        site = re.search(r"/sites/([^/?#]+)", url)
        return (f'- {{name: "{h.split(".")[0]}", type: oracle, host: "{h}", site_number: "CX_1", '
                f'site_name: "{site.group(1) if site else "?"}"}}  # site_number à lire dans l\'onglet Réseau')
    if "smartrecruiters.com" in h:
        return f'- {{name: "?", type: smartrecruiters, company: "{u.path.strip("/").split("/")[0]}"}}'
    if "greenhouse.io" in h:
        return f'- {{name: "?", type: greenhouse, board: "{u.path.strip("/").split("/")[0]}"}}'
    if "lever.co" in h:
        return f'- {{name: "?", type: lever, company: "{u.path.strip("/").split("/")[0]}"}}'
    if "eightfold" in h or "portal.careers" in h:
        return f'- {{name: "{h}", type: eightfold, host: "{h}", domain: "<domaine>"}}  # ex. hsbc.com'
    return "# Type non reconnu : ouvre la page, onglet Réseau (F12), et cherche la requête qui renvoie les offres en JSON."

# =========================================================================== nouveaux connecteurs (17/11/2026)
import json as _json
import os as _os
from pathlib import Path as _Path


def ashby(src: Dict, terms: List[str]) -> List[Job]:
    """Ashby (OpenAI, Notion, Ramp…) : src: name, board."""
    s = _session()
    data = _get(s, f"https://api.ashbyhq.com/posting-api/job-board/{src['board']}",
                params={"includeCompensation": "false"}).json()
    out = []
    for q in data.get("jobs") or []:
        if q.get("isListed") is False:
            continue
        locs = [q.get("location") or ""] + [x.get("location", "") for x in q.get("secondaryLocations") or []]
        out.append(Job(source=src["name"], company=src["name"], id=str(q.get("id")), title=q.get("title", ""),
                       location=" ; ".join(x for x in locs if x), url=q.get("jobUrl") or q.get("applyUrl") or "",
                       posted=_iso(q.get("publishedAt")) or None,
                       description=q.get("descriptionHtml") or q.get("descriptionPlain") or ""))
    return out


def workable(src: Dict, terms: List[str]) -> List[Job]:
    """Workable : src: name, account."""
    s = _session()
    data = _get(s, f"https://apply.workable.com/api/v1/widget/accounts/{src['account']}",
                params={"details": "true"}).json()
    out = []
    for q in data.get("jobs") or []:
        locs = [", ".join(x for x in [l.get("city"), l.get("country")] if x) for l in q.get("locations") or []]
        loc = " ; ".join(locs) or ", ".join(x for x in [q.get("city"), q.get("country")] if x)
        out.append(Job(source=src["name"], company=src["name"], id=str(q.get("shortcode") or q.get("id")),
                       title=q.get("title", ""), location=loc, url=q.get("url") or q.get("shortlink") or "",
                       posted=_iso(q.get("published_on") or q.get("created_at")) or None,
                       description=q.get("description") or ""))
    return out


def recruitee(src: Dict, terms: List[str]) -> List[Job]:
    """Recruitee : src: name, company (sous-domaine)."""
    s = _session()
    data = _get(s, f"https://{src['company']}.recruitee.com/api/offers/").json()
    out = []
    for q in data.get("offers") or []:
        loc = q.get("location") or ", ".join(x for x in [q.get("city"), q.get("country")] if x)
        out.append(Job(source=src["name"], company=src["name"], id=str(q.get("id")), title=q.get("title", ""),
                       location=loc, url=q.get("careers_url") or "", posted=_iso(q.get("published_at")) or None,
                       description=(q.get("description") or "") + "\n" + (q.get("requirements") or "")))
    return out


def amazon(src: Dict, terms: List[str]) -> List[Job]:
    """Amazon / AWS (amazon.jobs) : recherche par ville."""
    s = _session()
    seen, out = set(), []
    for term in terms:
        offset = 0
        for _ in range(src.get("max_pages", 20)):
            d = _get(s, "https://www.amazon.jobs/en/search.json",
                     params={"base_query": "", "loc_query": term, "offset": offset, "result_limit": 100,
                             "sort": "recent"}).json()
            jobs = d.get("jobs") or []
            if not jobs:
                break
            for q in jobs:
                jid = str(q.get("id_icims") or q.get("id"))
                if jid in seen:
                    continue
                seen.add(jid)
                desc = "\n\n".join(str(q.get(k) or "") for k in
                                   ("description", "basic_qualifications", "preferred_qualifications"))
                posted = ""
                try:
                    posted = datetime.strptime(q.get("posted_date", ""), "%B %d, %Y").date().isoformat()
                except ValueError:
                    pass
                out.append(Job(source=src["name"], company=q.get("company_name") or "Amazon", id=jid,
                               title=q.get("title", ""), location=q.get("normalized_location") or q.get("location", ""),
                               url="https://www.amazon.jobs" + (q.get("job_path") or ""), posted=posted or None,
                               description=desc))
            offset += len(jobs)
            if offset >= (d.get("hits") or 0):
                break
            time.sleep(PAUSE)
    return out


def mycareersfuture(src: Dict, terms: List[str]) -> List[Job]:
    """MyCareersFuture (portail officiel de Singapour : quasi toutes les offres du pays, toutes entreprises).
    Recherche par métier (role_terms de config.yaml)."""
    s = _session()
    seen, out = set(), []
    for term in src.get("terms") or terms:
        for page in range(src.get("max_pages", 5)):
            d = _get(s, "https://api.mycareersfuture.gov.sg/v2/jobs",
                     params={"search": term, "limit": 100, "page": page, "sortBy": "new_posting_date"}).json()
            res = d.get("results") or []
            if not res:
                break
            for q in res:
                uid = q.get("uuid")
                if not uid or uid in seen:
                    continue
                seen.add(uid)
                meta = q.get("metadata") or {}
                job = Job(source=src["name"], company=((q.get("postedCompany") or {}).get("name") or "").title(),
                          id=uid, title=q.get("title", ""), location="Singapore",
                          url=meta.get("jobDetailsUrl") or f"https://www.mycareersfuture.gov.sg/job/{uid}",
                          posted=_iso(meta.get("newPostingDate") or meta.get("createdAt")) or None,
                          description=q.get("description") or "")
                if not job.description:
                    def load(uid=uid) -> str:
                        return _get(s, f"https://api.mycareersfuture.gov.sg/v2/jobs/{uid}").json().get("description", "")
                    job.desc_loader = load
                out.append(job)
            if len(res) < 100:
                break
            time.sleep(PAUSE)
    return out


def seek(src: Dict, terms: List[str]) -> List[Job]:
    """JobsDB Hong Kong (site_key HK-Main, host hk.jobsdb.com) / JobStreet Singapour (SG-Main, sg.jobstreet.com).
    Recherche par métier."""
    host, site_key = src["host"], src["site_key"]
    s = _session()
    seen, out = set(), []
    for term in src.get("terms") or terms:
        for page in range(1, src.get("max_pages", 4) + 1):
            d = _get(s, f"https://{host}/api/jobsearch/v5/search",
                     params={"siteKey": site_key, "sourcesystem": "houston", "page": page, "pageSize": 100,
                             "keywords": term, "locale": "en-" + site_key[:2],
                             **({"where": src["where"]} if src.get("where") else {})}).json()
            res = d.get("data") or []
            if not res:
                break
            for q in res:
                jid = str(q.get("id"))
                if jid in seen:
                    continue
                seen.add(jid)
                locs = [l.get("label", "") for l in q.get("locations") or []] or [q.get("location", "")]
                teaser = (q.get("teaser") or "") + "\n" + "\n".join(q.get("bulletPoints") or [])
                job = Job(source=src["name"], company=q.get("companyName") or (q.get("advertiser") or {}).get("description", ""),
                          id=jid, title=q.get("title", ""), location=" ; ".join(x for x in locs if x) or src.get("city", ""),
                          url=f"https://{host}/job/{jid}", posted=_iso(q.get("listingDate")) or None)

                def load(jid=jid, teaser=teaser) -> str:
                    """Annonce complète lue dans la page (données intégrées), sinon le résumé."""
                    try:
                        html_ = _get(s, f"https://{host}/job/{jid}", headers={"Accept": "text/html"}).text
                        m = re.search(r'"content":"((?:[^"\\]|\\.){200,})"', html_)
                        if m:
                            return _json.loads('"' + m.group(1) + '"')
                    except Exception:
                        pass
                    return teaser
                job.desc_loader = load
                out.append(job)
            if len(res) < 100:
                break
            time.sleep(PAUSE)
    return out


def jooble(src: Dict, terms: List[str]) -> List[Job]:
    """Jooble (agrégateur : JobsDB, CTgoodjobs, eFinancialCareers, cabinets…). Une clé gratuite PAR PAYS
    (formulaire sur hk.jooble.org/api/about, sg.jooble.org/api/about…), 100 requêtes / jour / clé.
    Secret GitHub JOOBLE_KEYS au format « hk:CLÉ1,sg:CLÉ2 » (ancien JOOBLE_API_KEY = clé seule, pays hk)."""
    raw = _os.environ.get("JOOBLE_KEYS", "").strip() or (
        "hk:" + _os.environ["JOOBLE_API_KEY"].strip() if _os.environ.get("JOOBLE_API_KEY", "").strip() else "")
    if not raw:
        return []
    city = {"hk": "Hong Kong", "sg": "Singapore", "jp": "Tokyo"}
    s = _session()
    out, seen = [], set()
    per_run = int(src.get("terms_per_run", 20))        # 4 passages x 20 = 80 requêtes / jour < 100
    for pair in raw.split(","):
        cc, _, key = pair.strip().partition(":")
        if not key:
            continue
        host = "jooble.org" if cc in ("us", "") else f"{cc}.jooble.org"
        for term in (src.get("terms") or terms)[:per_run]:
            d = _post(s, f"https://{host}/api/{key}", {"keywords": term, "location": city.get(cc, cc), "page": "1",
                                                     "ResultOnPage": "50"}).json()
            for q in d.get("jobs") or []:
                jid = str(q.get("id") or q.get("link"))
                if jid in seen:
                    continue
                seen.add(jid)
                out.append(Job(source=src["name"], company=q.get("company") or "", id=jid, title=q.get("title", ""),
                               location=q.get("location") or city.get(cc, ""), url=q.get("link", ""),
                               posted=_iso(q.get("updated")) or None, description=q.get("snippet") or ""))
            time.sleep(PAUSE)
    return out


def adzuna(src: Dict, terms: List[str]) -> List[Job]:
    """Adzuna Singapour (clés gratuites ADZUNA_APP_ID / ADZUNA_APP_KEY)."""
    app_id, app_key = _os.environ.get("ADZUNA_APP_ID", "").strip(), _os.environ.get("ADZUNA_APP_KEY", "").strip()
    if not (app_id and app_key):
        return []
    s = _session()
    out, seen = [], set()
    for term in src.get("terms") or terms:
        for page in range(1, src.get("max_pages", 3) + 1):
            d = _get(s, f"https://api.adzuna.com/v1/api/jobs/{src.get('country', 'sg')}/search/{page}",
                     params={"app_id": app_id, "app_key": app_key, "what": term, "results_per_page": 50,
                             "sort_by": "date", "content-type": "application/json"}).json()
            res = d.get("results") or []
            for q in res:
                jid = str(q.get("id"))
                if jid in seen:
                    continue
                seen.add(jid)
                out.append(Job(source=src["name"], company=(q.get("company") or {}).get("display_name", ""), id=jid,
                               title=q.get("title", ""), location=(q.get("location") or {}).get("display_name", "Singapore"),
                               url=q.get("redirect_url", ""), posted=_iso(q.get("created")) or None,
                               description=q.get("description") or ""))
            if len(res) < 50:
                break
            time.sleep(PAUSE)
    return out


def careerjet(src: Dict, terms: List[str]) -> List[Job]:
    """Careerjet API v4 (agrégateur, Hong Kong / Singapour / Japon). Clé gratuite d'éditeur :
    careerjet.com/partners/register/as-publisher -> secret GitHub CAREERJET_API_KEY."""
    key = (_os.environ.get("CAREERJET_API_KEY") or _os.environ.get("CAREERJET_AFFID") or "").strip()
    if not key:
        return []
    s = _session()
    try:                                           # l'API demande l'IP de l'appelant : celle du serveur GitHub
        ip = s.get("https://api.ipify.org", timeout=10).text.strip()
    except Exception:
        ip = "127.0.0.1"
    out, seen = [], set()
    for locale, city in src.get("locales", [["en_HK", "Hong Kong"], ["en_SG", "Singapore"], ["ja_JP", "Tokyo"]]):
        for term in src.get("terms") or terms:
            r = s.get("https://search.api.careerjet.net/v4/query", auth=(key, ""), timeout=TIMEOUT,
                      params={"locale_code": locale, "keywords": term, "location": city, "sort": "date",
                              "page_size": 100, "user_ip": ip, "user_agent": UA["User-Agent"]})
            if r.status_code == 400 and "locale" in r.text.lower():
                break                              # pays non couvert par Careerjet : on passe au suivant
            r.raise_for_status()
            for q in r.json().get("jobs") or []:
                jid = q.get("url", "")
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                locs = q.get("locations")
                out.append(Job(source=src["name"], company=q.get("company") or "", id=jid, title=q.get("title", ""),
                               location=locs if isinstance(locs, str) else " ; ".join(locs or [city]), url=jid,
                               posted=_iso(_rfc(q.get("date"))) or None, description=q.get("description") or ""))
            time.sleep(PAUSE)
    return out


def _rfc(v):
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(v).date().isoformat()
    except Exception:
        return ""


# --------------------------------------------------------------------------- découverte automatique de l'ATS
ATS_CACHE_FILE = _Path(__file__).parent / "ats_cache.json"
try:
    ATS_CACHE = _json.loads(ATS_CACHE_FILE.read_text(encoding="utf-8"))
except Exception:
    ATS_CACHE = {}


def save_ats_cache():
    ATS_CACHE_FILE.write_text(_json.dumps(ATS_CACHE, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def _slugs(name: str, extra=None) -> List[str]:
    import unicodedata
    base = unicodedata.normalize("NFKD", re.sub(r"\(.*?\)", "", name)).encode("ascii", "ignore").decode().strip().lower()
    base = base.replace("'", "")
    base = base.replace("&", "and")
    words = re.findall(r"[a-z0-9]+", base)
    cands = list(extra or []) + ["".join(words), "-".join(words)]
    if len(words) > 1:
        cands.append(words[0])
    # identifiants trop courts (« ey », « bp ») : risque de tomber sur une autre entreprise -> seulement s'ils sont forcés
    return list(dict.fromkeys(c for c in cands if c and (len(c) >= 4 or c in (extra or []))))[:4]


def _probe(s, ats: str, slug: str) -> int:
    """Nombre d'offres trouvées pour ce slug sur cet ATS (-1 = n'existe pas)."""
    try:
        if ats == "greenhouse":
            r = s.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", timeout=15)
            return len(r.json().get("jobs", [])) if r.status_code == 200 else -1
        if ats == "lever":
            r = s.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json", "limit": 1}, timeout=15)
            return len(r.json()) if r.status_code == 200 and isinstance(r.json(), list) else -1
        if ats == "ashby":
            r = s.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", timeout=15)
            return len(r.json().get("jobs", [])) if r.status_code == 200 else -1
        if ats == "smartrecruiters":
            r = s.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", params={"limit": 1}, timeout=15)
            return r.json().get("totalFound", 0) or -1 if r.status_code == 200 else -1
        if ats == "workable":
            r = s.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", timeout=15)
            return len(r.json().get("jobs", [])) if r.status_code == 200 else -1
        if ats == "recruitee":
            r = s.get(f"https://{slug}.recruitee.com/api/offers/", timeout=15)
            return len(r.json().get("offers", [])) if r.status_code == 200 else -1
    except Exception:
        return -1
    return -1


AUTO_ATS = ["greenhouse", "lever", "ashby", "smartrecruiters", "workable", "recruitee"]


def auto(src: Dict, terms: List[str]) -> List[Job]:
    """Entreprise sans ATS connu : on essaie Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee
    avec quelques variantes du nom, une fois ; le résultat est gardé dans ats_cache.json (re-test tous les 14 jours)."""
    name = src["name"]
    c = ATS_CACHE.get(name)
    today_ = date.today()
    if c and c.get("ats"):
        conn = CONNECTORS[c["ats"]]
        key = {"greenhouse": "board", "ashby": "board", "lever": "company", "smartrecruiters": "company",
               "workable": "account", "recruitee": "company"}[c["ats"]]
        return conn({**src, key: c["slug"]}, terms)
    if c and (today_ - date.fromisoformat(c.get("checked", "2000-01-01"))).days < 14:
        return []                                  # déjà cherché récemment, rien trouvé
    s = _session()
    best = None
    for slug in _slugs(name, src.get("slugs")):
        for ats in AUTO_ATS:
            n = _probe(s, ats, slug)
            if n > 0 and (best is None or n > best[2]):
                best = (ats, slug, n)
        if best:
            break
    ATS_CACHE[name] = {"ats": best[0], "slug": best[1], "checked": today_.isoformat()} if best \
        else {"ats": None, "checked": today_.isoformat()}
    return auto(src, terms) if best else []


CONNECTORS.update({"ashby": ashby, "workable": workable, "recruitee": recruitee, "amazon": amazon,
                   "mycareersfuture": mycareersfuture, "seek": seek, "jooble": jooble, "adzuna": adzuna,
                   "careerjet": careerjet, "auto": auto})


# =========================================================================== Hong Kong : sources officielles + alertes e-mail
def _pick(d: dict, *keys) -> str:
    """Première valeur non vide dont le nom de champ contient un des mots donnés (formats de données inconnus)."""
    for k in keys:
        for kk, v in d.items():
            if k in kk.lower() and v not in (None, "", []):
                return v if isinstance(v, str) else (" ".join(map(str, v)) if isinstance(v, list) else str(v))
    return ""


def _records(data):
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for k in ("data", "records", "result", "results", "items", "jobs", "list"):
            if k in data:
                return _records(data[k])
        for v in data.values():
            if isinstance(v, (list, dict)):
                r = _records(v)
                if r:
                    return r
    return []


def hkstp(src: Dict, terms: List[str]) -> List[Job]:
    """Offres des entreprises du Hong Kong Science Park (données ouvertes officielles, data.gov.hk, temps réel)."""
    s = _session()
    data = _get(s, src.get("url", "https://opendata.hkstp.org/corporate/recruitment/v1/")).json()
    out = []
    for i, q in enumerate(_records(data)):
        title = _pick(q, "title", "position", "job_name", "jobname")
        if not title:
            continue
        url = _pick(q, "url", "link", "apply")
        out.append(Job(source=src["name"], company=_pick(q, "company", "employer", "organi") or "HKSTP company",
                       id=str(_pick(q, "id") or url or f"{title}-{i}"), title=title,
                       location=_pick(q, "location", "district") or "Hong Kong", url=url or "https://www.hkstp.org/",
                       posted=_iso(_pick(q, "post", "date", "publish")) or None,
                       description="\n".join(x for x in [_pick(q, "description", "detail", "responsib"),
                                                         _pick(q, "requirement", "qualif")] if x)))
    return out


def hkma(src: Dict, terms: List[str]) -> List[Job]:
    """Offres de la HKMA (API officielle apidocs.hkma.gov.hk)."""
    s = _session()
    out, offset = [], 0
    for _ in range(5):
        d = _get(s, "https://api.hkma.gov.hk/public/recruitment", params={"lang": "en", "offset": offset}).json()
        recs = (d.get("result") or {}).get("records") or _records(d)
        if not recs:
            break
        for q in recs:
            out.append(Job(source=src["name"], company="Hong Kong Monetary Authority", id=q.get("link") or q.get("title"),
                           title=q.get("title", ""), location="Hong Kong", url=q.get("link", ""),
                           posted=_iso(q.get("date")) or None))
        offset += len(recs)
        if len(recs) < 100:
            break
    return out


# ---- alertes e-mail (JobsDB, LinkedIn, eFinancialCareers, Michael Page, Hays, Robert Walters, alertes des banques…)
JOB_LINK_RE = re.compile(
    r"linkedin\.com/(comm/)?jobs/view|jobsdb\.com/(job|hk/en/job)|jobstreet\.com/.*job|efinancialcareers\.[a-z.]+/jobs?-|"
    r"michaelpage\.[a-z.]+/job-detail|roberthalf|robertwalters\.[a-z.]+/.*job|hays\.[a-z.]+/job|morganmckinley\.com/.*job|"
    r"ctgoodjobs\.hk/job|myworkdayjobs\.com/.*job|greenhouse\.io/.*jobs|lever\.co/|indeed\.[a-z.]+/(rc/clk|viewjob|pagead)|"
    r"glassdoor\.[a-z.]+/.*job|selbyjennings|randstad\.[a-z.]+/.*job|links\.hk|/job[s]?/|jobid=|job_id=", re.I)
GENERIC_TXT = re.compile(r"^(view|see|apply|show|more|all|search|unsubscribe|manage|jobs?|here|click|open|details?|"
                         r"voir|postuler|save|share|similar)\b", re.I)


def _alert_jobs(html_: str, sender: str, subject: str, hint: str):
    """Liens d'offres dans un e-mail d'alerte : (titre, lien, texte qui suit = entreprise / lieu)."""
    out = []
    for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html_, re.I | re.S):
        href, inner = html.unescape(m.group(1)), re.sub(r"<[^>]+>", " ", m.group(2))
        title = re.sub(r"\s+", " ", html.unescape(inner)).strip()
        if not (8 <= len(title) <= 140) or GENERIC_TXT.search(title) or not JOB_LINK_RE.search(href):
            continue
        after = re.sub(r"<[^>]+>", "\n", html_[m.end():m.end() + 900])
        lines = [re.sub(r"\s+", " ", html.unescape(x)).strip(" ·•|-") for x in after.split("\n")]
        lines = [x for x in lines if x and len(x) < 120][:3]
        company = lines[0] if lines else ""
        loc = next((x for x in lines if re.search(r"hong ?kong|singapore|tokyo|japan|\bhk\b|central|kowloon", x, re.I)), hint)
        if " · " in company:                                  # « Entreprise · Lieu » (LinkedIn)
            company, _, loc2 = company.partition(" · ")
            loc = loc2 or loc
        out.append((title, href, company or sender, loc or hint))
    return out


def email_alerts(src: Dict, terms: List[str]) -> List[Job]:
    """Lit les alertes e-mail reçues sur une boîte Gmail dédiée (secrets ALERTS_EMAIL + ALERTS_APP_PASSWORD,
    mot de passe d'application Google). Annonce complète non disponible : seulement titre / entreprise / lieu."""
    import email as _email
    import imaplib
    from email.header import decode_header, make_header
    user, pwd = _os.environ.get("ALERTS_EMAIL", "").strip(), _os.environ.get("ALERTS_APP_PASSWORD", "").replace(" ", "")
    if not (user and pwd):
        return []
    days = int(src.get("days", 4))
    since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
    M = imaplib.IMAP4_SSL(src.get("imap_host", "imap.gmail.com"))
    M.login(user, pwd)
    M.select(src.get("folder", "INBOX"), readonly=True)
    _, ids = M.search(None, "SINCE", since)
    out, seen = [], set()
    for mid in (ids[0].split() if ids and ids[0] else [])[-400:]:
        _, msg_data = M.fetch(mid, "(RFC822)")
        msg = _email.message_from_bytes(msg_data[0][1])
        sender = str(make_header(decode_header(msg.get("From", "")))).split("<")[0].strip(' "')
        subject = str(make_header(decode_header(msg.get("Subject", ""))))
        when = ""
        try:
            from email.utils import parsedate_to_datetime
            when = parsedate_to_datetime(msg.get("Date")).date().isoformat()
        except Exception:
            pass
        body = ""
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                body += part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "ignore")
        hint = src.get("location_hint", "Hong Kong")
        for title, href, company, loc in _alert_jobs(body, sender, subject, hint):
            key = re.sub(r"[?#].*$", "", href)
            if key in seen:
                continue
            seen.add(key)
            out.append(Job(source=src["name"] + " · " + sender[:30], company=company[:80], id=key[:300], title=title,
                           location=loc, url=href, posted=when or None,
                           description=""))            # annonce non lue : langues signalées « non vérifiées »
    M.logout()
    return out


CONNECTORS.update({"hkstp": hkstp, "hkma": hkma, "email_alerts": email_alerts})


# =========================================================================== agrégateurs « tout-en-un » (clés API)
def _rotate(items: List[str], n: int) -> List[str]:
    """n mots-clés différents à chaque passage (tourne sur la liste au fil des passages)."""
    if not items:
        return []
    k = (date.today().toordinal() * 4 + datetime.utcnow().hour // 6) * n
    return [items[(k + i) % len(items)] for i in range(min(n, len(items)))]


def jsearch(src: Dict, terms: List[str]) -> List[Job]:
    """JSearch (OpenWeb Ninja) : offres de Google for Jobs = LinkedIn, Indeed, Glassdoor, JobsDB, sites carrières…
    Secret JSEARCH_API_KEY (direct openwebninja.com) ou RAPIDAPI_KEY (via RapidAPI). Gratuit : 200 requêtes / mois,
    d'où `per_run` requêtes par passage (2 par défaut x 4 passages x 30 j = 240 : mets 1 si tu restes en gratuit)."""
    direct, rapid = _os.environ.get("JSEARCH_API_KEY", "").strip(), _os.environ.get("RAPIDAPI_KEY", "").strip()
    if not (direct or rapid):
        return []
    s = _session()
    out, seen = [], set()
    for city, cc in src.get("cities", [["Hong Kong", "hk"]]):
        for term in _rotate(src.get("terms") or terms, int(src.get("per_run", 1))):
            q = f"{term} in {city}"
            if direct:
                r = _get(s, "https://api.openwebninja.com/jsearch/search-v2", headers={"x-api-key": direct},
                         params={"query": q, "country": cc, "date_posted": src.get("date_posted", "week")})
            else:
                r = _get(s, "https://jsearch.p.rapidapi.com/search",
                         headers={"X-RapidAPI-Key": rapid, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"},
                         params={"query": q, "country": cc, "page": 1, "num_pages": 1,
                                 "date_posted": src.get("date_posted", "week")})
            for q_ in _records(r.json()):
                jid = q_.get("job_id") or q_.get("job_apply_link")
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                loc = ", ".join(x for x in [q_.get("job_city"), q_.get("job_state"), q_.get("job_country")] if x) \
                    or q_.get("job_location") or city
                out.append(Job(source=src["name"] + (f" · {q_['job_publisher']}" if q_.get("job_publisher") else ""),
                               company=q_.get("employer_name") or "", id=str(jid), title=q_.get("job_title", ""),
                               location=loc, url=q_.get("job_apply_link") or "",
                               posted=_iso(q_.get("job_posted_at_datetime_utc")) or None,
                               description=q_.get("job_description") or ""))
            time.sleep(PAUSE)
    return out


def google_jobs(src: Dict, terms: List[str]) -> List[Job]:
    """Google for Jobs via SerpApi (secret SERPAPI_KEY). Gratuit : 250 recherches / mois -> `per_run` par passage."""
    key = _os.environ.get("SERPAPI_KEY", "").strip()
    if not key:
        return []
    s = _session()
    out, seen = [], set()
    for city in src.get("cities", ["Hong Kong"]):
        for term in _rotate(src.get("terms") or terms, int(src.get("per_run", 2))):
            d = _get(s, "https://serpapi.com/search.json",
                     params={"engine": "google_jobs", "q": f"{term} {city}", "location": city, "hl": "en",
                             "api_key": key}).json()
            for q in d.get("jobs_results") or []:
                jid = q.get("job_id") or q.get("share_link") or q.get("title")
                if jid in seen:
                    continue
                seen.add(jid)
                link = ((q.get("apply_options") or [{}])[0].get("link") or q.get("share_link") or "")
                posted = ""
                m = re.search(r"(\d+)\s+day", (q.get("detected_extensions") or {}).get("posted_at", ""))
                if m:
                    posted = (date.today() - timedelta(days=int(m.group(1)))).isoformat()
                out.append(Job(source=src["name"] + (f" · {q['via'].replace('via ', '')}" if q.get("via") else ""),
                               company=q.get("company_name") or "", id=str(jid)[:300], title=q.get("title", ""),
                               location=q.get("location") or city, url=link, posted=posted or None,
                               description=q.get("description") or ""))
            time.sleep(PAUSE)
    return out


def fantastic_jobs(src: Dict, terms: List[str]) -> List[Job]:
    """Active Jobs DB (fantastic.jobs) : offres lues directement sur les sites carrières de 58 ATS, Workday compris
    (Fidelity, BlackRock, Citi, DBS…). Secret FANTASTIC_JOBS_KEY. Essai gratuit 14 jours, puis payant."""
    key = _os.environ.get("FANTASTIC_JOBS_KEY", "").strip()
    if not key:
        return []
    s = _session()
    out, cursor = [], None
    for _ in range(int(src.get("max_pages", 3))):
        params = {"time_frame": src.get("time_frame", "24h"), "location": src.get("location", "Hong Kong"),
                  "description_format": "text", "limit": int(src.get("limit", 100))}
        if cursor:
            params["cursor"] = cursor
        d = _get(s, "https://data.fantastic.jobs/v1/active-ats", headers={"Authorization": f"Bearer {key}"},
                 params=params).json()
        recs = _records(d)
        if not recs:
            break
        for q in recs:
            locs = q.get("locations_derived") or q.get("cities_derived") or []
            out.append(Job(source=src["name"] + (f" · {q['source']}" if q.get("source") else ""),
                           company=q.get("organization") or "", id=str(q.get("id")), title=q.get("title", ""),
                           location=" ; ".join(map(str, locs)) if isinstance(locs, list) else str(locs) or "Hong Kong",
                           url=q.get("url") or "", posted=_iso(q.get("date_posted")) or None,
                           description=q.get("description_text") or ""))
        cursor = recs[-1].get("id")
        if len(recs) < params["limit"]:
            break
    return out


CONNECTORS.update({"jsearch": jsearch, "google_jobs": google_jobs, "fantastic_jobs": fantastic_jobs})

# =========================================================================== eFinancialCareers (scraping JSON Next.js)
def efinancialcareers(src: Dict, terms: List[str]) -> List[Job]:
    """Lit les offres depuis une URL de recherche eFinancialCareers (extrait les données JSON de la page)."""
    s = _session()
    s.headers.update({"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
    r = _get(s, src["url"])
    
    # Extraction du JSON embarqué dans la page (Next.js __NEXT_DATA__)
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.S)
    if not m:
        return []
    
    data = _json.loads(m.group(1))
    out, seen = [], set()

    # Parcours récursif pour localiser les objets d'offres (souvent sous jobSearchResponse / data / jobs)
    def find_jobs(obj):
        if isinstance(obj, dict):
            # Identification d'une offre par ses clés standards sur eFinancialCareers
            if ("jobId" in obj or "id" in obj) and ("title" in obj or "jobTitle" in obj) and ("companyName" in obj or "company" in obj):
                yield obj
            for v in obj.values():
                yield from find_jobs(v)
        elif isinstance(obj, list):
            for item in obj:
                yield from find_jobs(item)

    for q in find_jobs(data):
        jid = str(q.get("jobId") or q.get("id") or "")
        title = q.get("title") or q.get("jobTitle") or ""
        if not jid or not title or jid in seen:
            continue
        seen.add(jid)

        comp = q.get("companyName") or (q.get("company") or {}).get("name") or ""
        loc = q.get("location") or q.get("displayLocation") or src.get("location_hint", "Hong Kong")
        slug_url = q.get("detailsPageUrl") or q.get("url") or ""
        full_url = f"https://www.efinancialcareers.fr{slug_url}" if slug_url.startswith("/") else slug_url
        posted = _iso(q.get("postedDate") or q.get("createdDate") or q.get("date")) or None
        teaser = q.get("summary") or q.get("description") or ""

        job = Job(
            source=src["name"],
            company=comp,
            id=jid,
            title=title,
            location=loc,
            url=full_url or src["url"],
            posted=posted,
            description=teaser
        )

        # Chargement de la description complète uniquement si l'offre passe cheap_check
        if full_url and len(teaser) < 300:
            def load(u=full_url, fallback=teaser) -> str:
                try:
                    page = _get(s, u).text
                    m_desc = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', page, re.S)
                    if m_desc:
                        d_json = _json.loads(m_desc.group(1))
                        # Recherche de la description HTML complète dans le JSON de l'offre
                        txt_match = re.search(r'"description"\s*:\s*"((?:[^"\\]|\\.){200,})"', m_desc.group(1))
                        if txt_match:
                            return _json.loads('"' + txt_match.group(1) + '"')
                except Exception:
                    pass
                return fallback
            job.desc_loader = load

        out.append(job)

    return out


CONNECTORS.update({"efinancialcareers": efinancialcareers})
