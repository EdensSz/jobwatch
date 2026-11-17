#!/usr/bin/env python3
"""jobwatch : agrège les offres des sites carrières, filtre (lieu, séniorité, profil, pas de chinois)
et produit un flux RSS (out/jobs.rss) + JSON + CSV.

Usage :
  python jobwatch.py                 # run normal
  python jobwatch.py --check         # teste chaque source (OK / KO) sans rien filtrer
  python jobwatch.py --explain       # écrit aussi out/rejected.csv (offres rejetées + raison)
  python jobwatch.py --detect URL    # identifie le type de site carrières et propose la ligne de config
"""
import argparse
import hashlib
import csv
import json
import re
import os
import smtplib
import sys
import time
import traceback
from datetime import date, datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

import yaml

from concurrent.futures import ThreadPoolExecutor

import ai_score
import sources
from filters import RULES_VERSION, cheap_check, city_of, full_check, html_to_text

HERE = Path(__file__).parent
OUT = HERE / "out"
STATE = HERE / "state.json"


CACHED_ROWS, SRC_STATS, RUN = [], [], {"ai_calls": 0}


def fix_why(why) -> list:
    """Répare les raisons « IA : T », « IA : h », … (texte IA découpé lettre par lettre par une ancienne version)."""
    if isinstance(why, str):
        why = [why]
    why = [w for w in (why or []) if isinstance(w, str)]
    ia = [w for w in why if w.startswith("IA : ")]
    if len(ia) >= 4 and sum(1 for w in ia if len(w) <= 6) >= 0.8 * len(ia):
        text = "".join(w[5:] for w in ia)
        return [f"IA : {r}" for r in ai_score.as_list(text)] + [w for w in why if not w.startswith("IA : ")]
    return why


def fix_row(r: dict) -> dict:
    r["why"] = fix_why(r.get("why"))
    r["flags"] = [r["flags"]] if isinstance(r.get("flags"), str) else (r.get("flags") or [])
    r["other_langs"] = ai_score.as_list(r.get("other_langs"))
    return r


def load_prev() -> dict:
    """Offres déjà traitées au passage précédent (out/jobs.json), indexées par clé."""
    try:
        rows = json.loads((OUT / "jobs.json").read_text(encoding="utf-8"))
        return {r["key"]: fix_row(r) for r in rows
                if isinstance(r, dict) and r.get("key") and r.get("rv") == RULES_VERSION}
    except Exception:
        return {}


def is_fresh(day: str, days: int) -> bool:
    try:
        return (date.today() - date.fromisoformat(day)).days <= days
    except (TypeError, ValueError):
        return False


def too_old(row: dict, max_age) -> bool:
    if not max_age or not row.get("posted"):
        return False
    try:
        return (date.today() - date.fromisoformat(row["posted"])).days > max_age
    except ValueError:
        return False


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def run_source(src: dict, cfg: dict):
    conn = sources.CONNECTORS.get(src["type"])
    if not conn:
        raise ValueError(f"type inconnu : {src['type']}")
    terms = src.get("terms") or cfg["search_terms"]
    return conn(src, terms)


def check_sources(cfg: dict) -> int:
    ko, srcs = 0, all_sources(cfg)
    for src in srcs:
        try:
            terms = (cfg.get("role_terms") if src.get("aggregator") else None) or src.get("terms") or cfg["search_terms"]
            jobs = sources.CONNECTORS[src["type"]]({**src, "max_pages": 1}, terms[:1])
            extra = ""
            if src["type"] == "auto":
                c = sources.ATS_CACHE.get(src["name"], {})
                extra = f"  ({c.get('ats')} : {c.get('slug')})" if c.get("ats") else "  (aucun ATS public trouvé)"
            print(f"OK  {src['name']:<28} {len(jobs)} offres{extra}")
        except Exception as e:
            ko += 1
            print(f"KO  {src['name']:<28} {type(e).__name__}: {str(e)[:120]}")
    sources.save_ats_cache()
    print(f"\n{ko} source(s) en échec sur {len(srcs)}.")
    return ko


class Skipped(Exception):
    pass


def all_sources(cfg: dict) -> list:
    """Sources de config.yaml + grandes entreprises en découverte automatique (section companies) + agrégateurs à la fin
    (pour qu'une offre trouvée sur le site de l'entreprise gagne sur son doublon d'agrégateur)."""
    srcs = list(cfg.get("sources") or [])
    names = {x["name"] for x in srcs}
    for group, items in (cfg.get("companies") or {}).items():
        for it in items or []:
            name, _, slugs = str(it).partition("|")
            name = name.strip()
            if name and name not in names:
                names.add(name)
                srcs.append({"name": name, "type": "auto", "group": group,
                             "slugs": [x.strip() for x in slugs.split(",") if x.strip()]})
    aggs = [dict(x, aggregator=True) for x in cfg.get("aggregators") or []]
    return srcs + aggs


def fetch_all(cfg: dict, health: dict):
    """Lit les listes d'offres de toutes les sources en parallèle. Une source en panne 3 passages de suite
    n'est plus réessayée qu'une fois par jour (« quarantaine »)."""
    today = date.today().isoformat()

    def one(src):
        h = health.get(src["name"], {})
        if h.get("fail", 0) >= 3 and h.get("last_try") == today:
            return src, None, Skipped(f"en quarantaine ({h['fail']} échecs de suite)")
        try:
            terms = (cfg.get("role_terms") if src.get("aggregator") else None) or src.get("terms") or cfg["search_terms"]
            conn = sources.CONNECTORS.get(src["type"])
            if not conn:
                raise ValueError(f"type inconnu : {src['type']}")
            return src, conn(src, terms), None
        except Exception as e:
            return src, None, e
    srcs = all_sources(cfg)
    with ThreadPoolExecutor(max_workers=int(cfg.get("workers", 16))) as ex:
        res = list(ex.map(one, srcs))
    for src, jobs, err in res:
        h = health.setdefault(src["name"], {})
        if isinstance(err, Skipped):
            continue
        h["last_try"] = today
        h["fail"] = h.get("fail", 0) + 1 if err is not None else 0
    try:
        sources.save_ats_cache()
    except Exception:
        pass
    return res


def norm_key(j) -> str:
    """Clé de doublon entre sources : entreprise + intitulé + ville."""
    co = re.sub(r"\b(limited|ltd|pte|inc|llc|plc|group|holdings?|co|sa|ag|nv|the)\b|[^a-z0-9]", "", (j.company or "").lower())
    ti = re.sub(r"[^a-z0-9]", "", (j.title or "").lower())
    return f"{co[:20]}|{ti}|{city_of(j.location or '')}"


def collect(cfg: dict, explain: bool):
    t0 = time.time()
    state = load_state()
    if RUN.get("reset"):                       # remise à zéro demandée : on garde seulement les dates de 1re apparition
        state = {k: {"first_seen": v.get("first_seen")} for k, v in state.items() if v.get("first_seen")}
    today = date.today().isoformat()
    kept, rejected, errors = [], [], []
    max_desc = cfg.get("max_description_fetches_per_source", 150)
    prev = {} if RUN.get("reset") else load_prev()
    refresh_days = int(cfg.get("refresh_days", 7))
    ai_cfg = cfg.get("ai", {}) or {}
    ai_on = ai_cfg.get("enabled", True) is not False and ai_cfg.get("auto_score", True) is not False \
        and bool(ai_score.available(os.environ, ai_cfg))
    CACHED_ROWS.clear()
    SRC_STATS.clear()
    pending = []      # (nom de source, offre, verdict provisoire)
    stats = {}
    health = state.setdefault("_sources", {})
    seen_norm = set()
    for src, jobs, err in fetch_all(cfg, health):
        name = src["name"]
        if isinstance(err, Skipped):
            old = [r for r in prev.values() if r.get("source") == name]
            CACHED_ROWS.extend(old)
            stats[name] = {"name": name, "ok": False, "skipped": True, "read": 0, "kept": 0, "reused": len(old),
                           "error": str(err)}
            continue
        if err is not None:
            errors.append(f"{name}: {type(err).__name__}: {str(err)[:150]}")
            print(f"[KO] {name}: {err}", file=sys.stderr)
            # source en panne : on garde ses offres du passage précédent
            old = [r for r in prev.values() if r.get("source") == name]
            CACHED_ROWS.extend(old)
            stats[name] = {"name": name, "ok": False, "read": 0, "kept": 0, "reused": len(old), "error": str(err)[:120]}
            continue
        st_src = stats[name] = {"name": name, "ok": True, "read": len(jobs), "kept": 0, "reused": 0}
        if src.get("type") == "auto":
            c = sources.ATS_CACHE.get(name) or {}
            st_src["ats"] = c.get("ats") or ""
            st_src["no_ats"] = not c.get("ats")
        if src.get("aggregator"):
            st_src["aggregator"] = True
        fetched = 0
        seen_ids = set()
        for j in jobs:
            if j.key in seen_ids:
                continue
            seen_ids.add(j.key)
            nk = norm_key(j)
            if nk in seen_norm:                  # même offre déjà vue sur une autre source
                continue
            seen_norm.add(nk)
            row = prev.get(j.key)
            needs_ai = bool(row) and ai_on and not row.get("excluded") and \
                (row.get("note_source") != "ia" or row.get("ai_v", 0) < ai_score.AI_VERSION)
            if row and not needs_ai and is_fresh(row.get("checked_at", ""), refresh_days) \
                    and not too_old(row, cfg.get("max_age_days")):
                CACHED_ROWS.append(row)          # déjà analysée récemment : on ne la retraite pas
                st_src["reused"] += 1
                continue
            st0 = state.get(j.key, {})
            if st0.get("rej") and st0.get("rv") == RULES_VERSION and is_fresh(st0.get("checked", ""), refresh_days):
                st_src["reused"] += 1            # déjà jugée hors profil récemment
                continue
            v = cheap_check(j, cfg)
            if not v.keep:
                rejected.append((j, v))
                continue
            if not j.description and j.desc_loader:
                if fetched >= max_desc:
                    rejected.append((j, type(v)(False, "plafond de lecture de descriptions atteint")))
                    continue
                fetched += 1
            pending.append((name, j, v))
    # lecture des annonces en parallèle (c'est l'étape la plus lente)
    def load(item):
        j = item[1]
        if not j.description and j.desc_loader:
            try:
                j.description = j.desc_loader()
            except Exception as e:
                print(f"[!] description illisible {j.url}: {e}", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=int(cfg.get("workers", 8))) as ex:
        list(ex.map(load, pending))
    for name, j, _ in pending:
        v = full_check(j, cfg)
        if v.keep:
            stats[name]["kept"] += 1
            kept.append((j, v))
        else:
            rejected.append((j, v))
            st = state.setdefault(j.key, {"first_seen": today})
            st["rej"], st["checked"], st["rv"] = v.reason, today, RULES_VERSION
    for src in all_sources(cfg):
        s = stats.get(src["name"])
        if s:
            SRC_STATS.append(s)
            print(f"[{'ok' if s['ok'] else 'KO'}] {s['name']:<28} {s['read']:>4} lues, {s['kept']} retenues, {s['reused']} déjà connues")
    # état : première apparition
    for j, v in kept:
        st = state.setdefault(j.key, {"first_seen": today})
        j.first_seen = st["first_seen"]
    kept = apply_ai(kept, rejected, state, cfg)
    new = [(j, v) for j, v in kept if j.first_seen == today]
    STATE.write_text(json.dumps(state, indent=1))
    kept.sort(key=lambda jv: (jv[0].posted or jv[0].first_seen or ""), reverse=True)   # plus récentes d'abord
    RUN["seconds"] = round(time.time() - t0)
    return kept, rejected, new, errors



CH_ORDER = {"non": 0, "unknown": 0, "souhaité": 1, "fortement souhaité": 2, "requis": 3}


LANG_CANON = [(r"mandarin|cantonese|cantonais|chinese|chinois|putonghua", "chinois"), (r"japan|japonais", "japonais"),
              (r"korea|coréen", "coréen"), (r"thai", "thaï"), (r"vietnam", "vietnamien"),
              (r"bahasa|indonesia|malay", "indonésien / malais"), (r"german|allemand", "allemand"),
              (r"spanish|espagnol", "espagnol")]


def canon_lang(x: str) -> str:
    for pat, lab in LANG_CANON:
        if re.search(pat, x, re.I):
            return lab
    return x.strip().lower()


def apply_ai(kept, rejected, state, cfg):
    """Relecture IA des offres retenues, les plus récentes d'abord (résultats en cache dans state.json)."""
    ai_cfg = cfg.get("ai", {}) or {}
    provs = [] if ai_cfg.get("enabled", True) is False or ai_cfg.get("auto_score", True) is False \
        else ai_score.available(os.environ, ai_cfg)
    if not provs:
        return kept
    RUN["provider"] = provs[0]
    budgets = {"gemini": 100, "openrouter": 12, "groq": 60, **(ai_cfg.get("max_per_run") or {})}
    pauses = {"gemini": 4.5, "openrouter": 3.5, "groq": 2.5, **(ai_cfg.get("pause_seconds") or {})}
    budget = sum(int(budgets.get(p, 20)) for p in provs)
    used = {}
    out = []
    order = sorted(kept, key=lambda jv: (jv[0].posted or jv[0].first_seen or ""), reverse=True)
    for j, v in order:
        st = state.setdefault(j.key, {"first_seen": date.today().isoformat()})
        h = hashlib.sha1((j.title + (v.description or "")[:4000]).encode("utf-8")).hexdigest()[:12]
        cached = st.get("ai") if st.get("ai_h") == h else None
        ai = ai_score.normalize(cached) if cached else None
        if ai and ai.get("v", 0) < ai_score.AI_VERSION:
            ai = None                                   # ancien format : à refaire
        if ai is None and budget > 0 and v.description and len(ai_score.EXHAUSTED) < len(provs):
            budget -= 1
            RUN["ai_calls"] += 1
            hints = "; ".join(v.other_langs + v.flags + ([f"{v.min_years} ans demandés"] if v.min_years else []))
            ai, prov = ai_score.review(j.title, j.company, j.location, v.description, os.environ, ai_cfg, hints)
            if ai:
                st["ai"], st["ai_h"] = ai, h
                used[prov] = used.get(prov, 0) + 1
                if used[prov] >= int(budgets.get(prov, 20)):
                    ai_score.EXHAUSTED.add(prov)        # budget du passage atteint pour ce fournisseur
            time.sleep(float(pauses.get(prov or provs[0], 3)))
        if not ai:
            out.append((j, v))
            continue
        v.note_source = "ia"
        v.ai_model = ai.get("model", "")
        v.ai_v = ai.get("v", 0)
        v.note10, v.parts = ai_score.parts_of(ai)
        v.rating = "A" if v.note10 >= 8 else "B" if v.note10 >= 6 else "C"
        v.summary, v.pros, v.cons, v.keywords = ai["summary"], ai["pros"], ai["cons"], ai["keywords"]
        v.role, v.level = ai["role"] or v.role, ai["level"] or v.level
        v.why = [f"{p['label']} {p['pts']:+d} : {p['why']}" if not p["max"] else f"{p['label']} {p['pts']}/{p['max']} : {p['why']}"
                 for p in v.parts]
        langs = [f"{canon_lang(x)} requis" for x in ai["languages_required"]] + \
                [f"{canon_lang(x)} souhaité" for x in ai["languages_preferred"]]
        seen = {l.rsplit(" ", 1)[0] for l in langs}
        v.other_langs = list(dict.fromkeys(langs + [l for l in (v.other_langs or []) if l.rsplit(" ", 1)[0] not in seen]))
        v.langs_ok = not v.other_langs
        zh = re.compile(r"mandarin|cantonais|cantonese|chinois|chinese", re.I)
        if any(zh.search(x) for x in ai["languages_required"]):
            v.chinese = "requis"
        elif any(zh.search(x) for x in ai["languages_preferred"]) and v.chinese in ("non", "unknown"):
            v.chinese = "souhaité"
        yrs = ai["years_required"]
        if yrs and (v.min_years is None or yrs > v.min_years):
            v.min_years = yrs
        if ai["exclude"] or yrs > cfg.get("max_years", 3) or ai["scores"]["metier"] <= 1:
            v.keep = False
            v.reason = (ai["exclude_reason"] or (f"{yrs} ans demandés" if yrs > cfg.get("max_years", 3)
                                                 else "métier hors cible")) + " (IA)"
            rejected.append((j, v))
            continue
        out.append((j, v))
    return out


REJ_CATS = [("lieu", r"^lieu"), ("langue", r"requis|souhaité|langue|japonais|chinois"),
            ("niveau", r"senior|ans d'exp|ans demandés|relationship manager"), ("metier", r"métier|hors cible|périmètre"),
            ("ancienne", r"ancienne")]


def funnel(rejected, live) -> dict:
    """Chiffres du passage pour les jauges de la page : annonces lues, écartées par motif, retenues."""
    rej = {k: 0 for k, _ in REJ_CATS}
    rej["autre"] = 0
    for _, v in rejected:
        cat = next((k for k, pat in REJ_CATS if re.search(pat, v.reason or "", re.I)), "autre")
        rej[cat] += 1
    return {"read": sum(s.get("read", 0) for s in SRC_STATS), "reused": sum(s.get("reused", 0) for s in SRC_STATS),
            "rejected": rej, "kept": len(live), "langs_ok": sum(1 for r in live if r.get("langs_ok")),
            "sources_ok": sum(1 for s in SRC_STATS if s.get("ok")), "sources_total": len(SRC_STATS)}


def write_outputs(kept, rejected, errors, cfg, explain):
    OUT.mkdir(exist_ok=True)
    now = format_datetime(datetime.now(timezone.utc))
    items = []
    for j, v in kept:
        flags = (" ⚠ " + " ; ".join(v.flags)) if v.flags else ""
        desc = (f"[{v.rating}] {j.company} — {j.location}{flags}\n\n"
                + html_to_text(j.description)[:1500])
        items.append(
            f"<item><title>{escape(f'[{v.rating}] {j.title} — {j.company}')}</title>"
            f"<link>{escape(j.url)}</link><guid isPermaLink=\"false\">{escape(j.key)}</guid>"
            f"<description>{escape(desc)}</description>"
            f"<pubDate>{format_datetime(datetime.fromisoformat(j.first_seen).replace(tzinfo=timezone.utc))}</pubDate></item>")
    for r in CACHED_ROWS:
        if r.get("excluded"):
            continue
        try:
            pub = format_datetime(datetime.fromisoformat(r.get("first_seen") or date.today().isoformat()).replace(tzinfo=timezone.utc))
        except ValueError:
            continue
        items.append(
            f"<item><title>{escape('[' + str(r.get('rating', '')) + '] ' + r.get('title', '') + ' — ' + r.get('company', ''))}</title>"
            f"<link>{escape(r.get('url', ''))}</link><guid isPermaLink=\"false\">{escape(r.get('key', ''))}</guid>"
            f"<description>{escape((r.get('summary') or '') + chr(10) + (r.get('description') or '')[:1500])}</description>"
            f"<pubDate>{pub}</pubDate></item>")
    rss = ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
           f"<title>Jobwatch – offres filtrées</title><link>https://example.invalid/</link>"
           f"<description>Analyst / Associate, HK-SG, sans chinois</description><lastBuildDate>{now}</lastBuildDate>"
           + "".join(items) + "</channel></rss>")
    (OUT / "jobs.rss").write_text(rss, encoding="utf-8")
    def row(jv, excluded):
        j, v = jv
        return dict(company=j.company, title=j.title, location=j.location, posted=j.posted or "",
                    first_seen=getattr(j, "first_seen", "") or date.today().isoformat(),
                    is_new=(getattr(j, "first_seen", "") == date.today().isoformat()),
                    note10=v.note10, rating=v.rating, chinese=v.chinese, other_langs=v.other_langs,
                    years=v.min_years, flags=v.flags, why=v.why, excerpt=v.excerpt,
                    description=v.description, summary=v.summary, note_source=v.note_source, ai_model=v.ai_model,
                    ai_v=getattr(v, "ai_v", 0), rv=RULES_VERSION, parts=v.parts, role=v.role, level=v.level,
                    langs_ok=v.langs_ok, pros=v.pros, cons=v.cons, keywords=v.keywords,
                    city=city_of(j.location + " " + (j.extra_location or "")),
                    excluded=excluded, excluded_reason=v.reason if excluded else "",
                    url=j.url, source=j.source, score=v.score, key=j.key, checked_at=date.today().isoformat())
    rows = [row(jv, False) for jv in kept]
    rows += [row(jv, True) for jv in rejected            # exclues, visibles avec le filtre « Exclues »
             if re.search(r"requis|souhaité|ans d'exp|ans demandés|\(IA\)", jv[1].reason or "")]
    have = {r["key"] for r in rows}
    today_s = date.today().isoformat()
    for r in CACHED_ROWS:
        if r.get("key") not in have:
            r = dict(r, is_new=(r.get("first_seen") == today_s))
            rows.append(r)
            have.add(r["key"])
    (OUT / "jobs.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    now_utc = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        paris = now_utc.astimezone(ZoneInfo("Europe/Paris")).strftime("%d/%m/%Y %H:%M")
    except Exception:
        paris = ""
    live = [r for r in rows if not r.get("excluded")]
    meta = {"generated_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "generated_paris": paris,
            "offers_total": len(rows), "offers_kept": len(live),
            "new_today": sum(1 for r in live if r.get("is_new")),
            "reused_from_previous": len(CACHED_ROWS), "ai_calls": RUN["ai_calls"], "sources": SRC_STATS,
            "seconds": RUN.get("seconds"), "ai_provider": RUN.get("provider", ""),
            "ai_reviewed": sum(1 for r in live if r.get("note_source") == "ia"),
            "ai_pending": sum(1 for r in live if r.get("note_source") != "ia"),
            "ai_models": sorted({r.get("ai_model") for r in live if r.get("ai_model")}),
            "ai_error": ai_score.LAST_ERROR,
            "funnel": funnel(rejected, live)}
    (OUT / "ai_prompt.json").write_text(json.dumps(ai_score.prompt_parts(), ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(OUT / "jobs.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["rating"])
        w.writeheader()
        w.writerows(rows)
    if explain:
        with open(OUT / "rejected.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["company", "title", "location", "reason", "url"])
            for j, v in rejected:
                w.writerow([j.company, j.title, j.location, v.reason, j.url])
    if errors:
        (OUT / "errors.txt").write_text("\n".join(errors), encoding="utf-8")
    elif (OUT / "errors.txt").exists():
        (OUT / "errors.txt").unlink()


def send_digest(new, errors):
    host, user, pwd, to = (os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "MAIL_TO"))
    if not (host and user and pwd and to) or (not new and not errors):
        return
    lines = []
    for j, v in new:
        fl = f"  ⚠ {' ; '.join(v.flags)}" if v.flags else ""
        lines.append(f"[{v.rating}] {j.title} — {j.company} ({j.location}){fl}\n{j.url}\n")
    if errors:
        lines.append("Sources en échec :\n" + "\n".join(errors))
    msg = EmailMessage()
    msg["Subject"] = f"Jobwatch : {len(new)} nouvelle(s) offre(s)"
    msg["From"], msg["To"] = user, to
    msg.set_content("\n".join(lines))
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587))) as s:
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("--detect")
    ap.add_argument("--reset-ai", action="store_true",
                    help="oublie toutes les analyses IA et les offres déjà traitées : tout est réévalué et réanalysé")
    a = ap.parse_args()
    if a.detect:
        print(sources.detect(a.detect))
        return
    cfg = yaml.safe_load(open(a.config, encoding="utf-8"))
    if a.check:
        sys.exit(1 if check_sources(cfg) else 0)
    if a.reset_ai:
        RUN["reset"] = True
    kept, rejected, new, errors = collect(cfg, a.explain)
    if ai_score.LAST_ERROR:
        errors.append("IA : " + ai_score.LAST_ERROR)
    write_outputs(kept, rejected, errors, cfg, a.explain)
    try:
        send_digest(new, errors)
    except Exception:
        traceback.print_exc()
    print(f"\n{len(kept)} offres retenues dont {len(new)} nouvelles. Flux : out/jobs.rss")
    if errors:
        print(f"{len(errors)} source(s) en échec — voir out/errors.txt (lance --check)")


if __name__ == "__main__":
    main()
