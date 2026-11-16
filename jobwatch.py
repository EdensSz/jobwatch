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

import ai_score
import sources
from filters import cheap_check, full_check, html_to_text

HERE = Path(__file__).parent
OUT = HERE / "out"
STATE = HERE / "state.json"


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
    ko = 0
    for src in cfg["sources"]:
        try:
            jobs = sources.CONNECTORS[src["type"]](
                {**src, "max_pages": 1}, (src.get("terms") or cfg["search_terms"])[:1])
            print(f"OK  {src['name']:<28} {len(jobs)} offres (1re page, 1er mot-clé)")
        except Exception as e:
            ko += 1
            print(f"KO  {src['name']:<28} {type(e).__name__}: {str(e)[:120]}")
    print(f"\n{ko} source(s) en échec sur {len(cfg['sources'])}.")
    return ko


def collect(cfg: dict, explain: bool):
    state = load_state()
    today = date.today().isoformat()
    kept, rejected, errors = [], [], []
    max_desc = cfg.get("max_description_fetches_per_source", 150)
    for src in cfg["sources"]:
        try:
            jobs = run_source(src, cfg)
        except Exception as e:
            errors.append(f"{src['name']}: {type(e).__name__}: {str(e)[:150]}")
            print(f"[KO] {src['name']}: {e}", file=sys.stderr)
            continue
        fetched = 0
        n_keep = 0
        seen_ids = set()
        for j in jobs:
            if j.key in seen_ids:
                continue
            seen_ids.add(j.key)
            v = cheap_check(j, cfg)
            if not v.keep:
                rejected.append((j, v))
                continue
            if not j.description and j.desc_loader:
                if fetched >= max_desc:
                    v = type(v)(False, "plafond de lecture de descriptions atteint")
                    rejected.append((j, v))
                    continue
                try:
                    j.description = j.desc_loader()
                except Exception as e:
                    print(f"[!] description illisible {j.url}: {e}", file=sys.stderr)
                fetched += 1
            v = full_check(j, cfg)
            if v.keep:
                n_keep += 1
                kept.append((j, v))
            else:
                rejected.append((j, v))
        print(f"[ok] {src['name']:<28} {len(jobs):>4} lues, {n_keep} retenues")
    # état : première apparition
    for j, v in kept:
        st = state.setdefault(j.key, {"first_seen": today})
        j.first_seen = st["first_seen"]
    kept = apply_ai(kept, rejected, state, cfg)
    new = [(j, v) for j, v in kept if j.first_seen == today]
    STATE.write_text(json.dumps(state, indent=1))
    rk = {"A": 0, "B": 1, "C": 2}
    kept.sort(key=lambda jv: (rk.get(jv[1].rating, 3), jv[0].first_seen), reverse=False)
    return kept, rejected, new, errors



CH_ORDER = {"non": 0, "unknown": 0, "souhaité": 1, "fortement souhaité": 2, "requis": 3}


def apply_ai(kept, rejected, state, cfg):
    """Relecture IA des offres retenues (résultats gardés en cache dans state.json)."""
    ai_cfg = cfg.get("ai", {}) or {}
    picked = None if ai_cfg.get("enabled", True) is False else ai_score.pick_provider(os.environ, ai_cfg.get("provider"))
    if not picked:
        return kept
    provider, key, pcfg = picked
    model = ai_cfg.get("model") or pcfg["model"]
    budget = int(ai_cfg.get("max_per_run", 40 if provider == "openrouter" else 80))
    pause = float(ai_cfg.get("pause_seconds", 5))
    out = []
    for j, v in kept:
        st = state.setdefault(j.key, {"first_seen": date.today().isoformat()})
        h = hashlib.sha1((j.title + (v.description or "")[:3000]).encode("utf-8")).hexdigest()[:12]
        ai = st.get("ai") if st.get("ai_h") == h else None
        if ai is None and budget > 0 and v.description:
            budget -= 1
            ai = ai_score.review(j.title, j.company, j.location, v.description, key, model, provider)
            if ai:
                st["ai"], st["ai_h"] = ai, h
            time.sleep(pause)
        if not ai:
            out.append((j, v))
            continue
        v.note_source = "ia"
        v.ai_model = ai.get("model", "")
        v.note10 = ai["note"]
        v.summary = ai.get("summary", "")
        reasons = [f"IA : {r}" for r in (ai.get("reasons") or [])]
        v.why = reasons + [w for w in v.why if not reasons]
        if CH_ORDER.get(ai["chinese"], 0) > CH_ORDER.get(v.chinese, 0):
            v.chinese = ai["chinese"]
        yrs = ai.get("years_required") or 0
        if v.chinese == "requis":
            v.keep, v.reason = False, "chinois requis (IA)"
            rejected.append((j, v))
            continue
        if yrs > cfg.get("max_years", 3):
            v.keep, v.reason = False, f"{yrs} ans demandés (IA)"
            rejected.append((j, v))
            continue
        out.append((j, v))
    return out


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
                    excluded=excluded, excluded_reason=v.reason if excluded else "",
                    url=j.url, source=j.source, score=v.score)
    rows = [row(jv, False) for jv in kept]
    rows += [row(jv, True) for jv in rejected
             if re.search(r"requis|ans demandés", jv[1].reason or "")]
    (OUT / "jobs.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
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
    a = ap.parse_args()
    if a.detect:
        print(sources.detect(a.detect))
        return
    cfg = yaml.safe_load(open(a.config, encoding="utf-8"))
    if a.check:
        sys.exit(1 if check_sources(cfg) else 0)
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
