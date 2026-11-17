"""Connecteurs vers les sites de carrières. Chaque connecteur renvoie une liste de Job
(avec un `desc_loader` pour lire la description seulement si l'offre passe le premier filtre)."""
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


def _get(s, url, **kw):
    for attempt in range(3):
        try:
            r = s.get(url, timeout=TIMEOUT, **kw)
            if r.status_code in (429, 503):
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(1.5)


def _post(s, url, body, **kw):
    for attempt in range(3):
        try:
            r = s.post(url, json=body, timeout=TIMEOUT, **kw)
            if r.status_code in (429, 503):
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(1.5)


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


def workday(src: Dict, terms: List[str]) -> List[Job]:
    """src: name + url (n'importe quelle page du site carrières Workday) ou host/tenant/site."""
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
    seen, jobs = set(), []
    for term in terms:
        offset, total = 0, None
        for _ in range(src.get("max_pages", 120)):
            r = _post(s, f"{base}/jobs", {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": term})
            data = r.json()
            if total is None:
                total = data.get("total", 0)
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
            if offset >= (total or 0):
                break
            time.sleep(PAUSE)
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
