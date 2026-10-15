"""Live-demo overlay: drop a new activity item into a week mid-demo (state/injected.json, cleared by reset).

Presets are derived from the firm config and the existing feeds at call time, so they keep working when firm.yaml
or the activity data change. Nothing is ever written to data/activity/.
"""

import json
from collections import Counter

from . import policy, store


def _base_feed(week: str) -> list[dict]:
    """The week's shipped feed (data/activity), without injected items."""
    return json.loads((store.ACTIVITY / f"{week}.json").read_text())


def _assigned(firm: dict, project: str, day: str) -> list[str]:
    return [a["consultant"] for a in firm["projects"][project].get("assignments", [])
            if policy.assignment_for(firm, project, a["consultant"], day)]


def presets(week: str) -> dict:
    firm = store.load_firm()
    feed = _base_feed(week)
    days = sorted({a["ts"][:10] for a in feed})
    day = days[-1] if days else store.now()[:10]
    client_projects = {k: p for k, p in firm["projects"].items() if p.get("client")}
    firm_domain = firm["firm"]["domain"]
    all_parts = Counter(p for w in store.weeks() for a in _base_feed(w) for p in a.get("participants", []))
    out = []

    def contact(domains):
        known = [(n, p) for p, n in all_parts.items() if p.split("@")[-1] in domains]
        return max(known)[1] if known else f"contact@{domains[0]}"

    # 1. a client emails someone who is on the project
    pick = next(((k, c) for k, p in client_projects.items() if p.get("match", {}).get("domains")
                 for c in _assigned(firm, k, day)), None)
    used = set()
    if pick:
        k, c = pick
        p = client_projects[k]
        who = contact(p["match"]["domains"])
        used.add(c)
        out.append({"id": "client_email", "label": f"Client email from {who}",
                    "hint": "Assigned consultant, clear project signals: should bill automatically.",
                    "item": {"consultant": c, "kind": "email", "title": f"RE: [{p.get('code', k)}] Revenue bridge: follow-up on the numbers",
                             "participants": f"{firm['consultants'][c]['email']}, {who}",
                             "thread_id": f"thr-live-{str(p.get('code', k)).lower()}"}})

    # 2. an email with no project signals at all
    c2 = next((c for c in firm["consultants"] if c not in used), next(iter(firm["consultants"])))
    out.append({"id": "ambiguous", "label": "Ambiguous email, no project signals",
                "hint": "No client domain, code, channel or file: should be held or skipped, never guessed.",
                "item": {"consultant": c2, "kind": "email", "title": "Quick question about next steps",
                         "participants": f"{firm['consultants'][c2]['email']}, someone@example.org",
                         "thread_id": "thr-live-misc"}})

    # 3. someone not on a client project touches its file
    for k, p in client_projects.items():
        m = p.get("match", {})
        path = (m.get("folders") or ["/Clients"])[0].rstrip("/") + "/" + (m.get("files") or ["Working_Notes.docx"])[0]
        c3 = next((c for c in firm["consultants"] if not any(
            a["consultant"] == c for a in p.get("assignments", []))), None)
        if c3 and (m.get("files") or m.get("folders")):
            out.append({"id": "unassigned", "label": f"{firm['consultants'][c3]['name'].split()[0]} touches a {p['name']} file",
                        "hint": "Consultant not assigned to the project: held under R7, never billed.",
                        "item": {"consultant": c3, "kind": "doc_edit", "title": f"Edited {path.rsplit('/', 1)[-1]}",
                                 "path": path, "minutes": 75, "edits": 9}})
            break
    return {"week": week, "day": day, "time": "15:30", "weeks": store.weeks(),
            "consultants": {c: v["name"] for c, v in firm["consultants"].items()}, "presets": out}


def inject(body: dict) -> dict:
    firm = store.load_firm()
    week = body.get("week")
    if week not in store.weeks():
        raise ValueError(f"unknown week {week}")
    c = body.get("consultant")
    if c not in firm["consultants"]:
        raise ValueError(f"unknown consultant {c}")
    kind = body.get("kind")
    if kind not in ("email", "call", "teams", "doc_edit"):
        raise ValueError("kind must be email, call, teams or doc_edit")
    title = (body.get("title") or "").strip()
    if not title:
        raise ValueError("a title is required")
    day, time = body.get("day") or "", body.get("time") or "15:30"
    if not day:
        raise ValueError("a day is required")
    items = store.load_injected()
    n = 1 + max([int(a["id"].rsplit("-", 1)[1]) for a in items if a["id"].startswith(f"{week}-x-")] or [0])
    item = {"id": f"{week}-x-{n:03d}", "consultant": c, "ts": f"{day}T{time}:00"[:19], "kind": kind, "title": title,
            "injected": True, "injected_at": store.now()}
    parts = [p.strip() for p in str(body.get("participants") or "").split(",") if p.strip()]
    if parts:
        item["participants"] = parts
    for k in ("channel", "path", "thread_id", "note"):
        if str(body.get(k) or "").strip():
            item[k] = str(body[k]).strip()
    for k in ("minutes", "edits", "messages"):
        if body.get(k) not in (None, ""):
            item[k] = int(body[k])
    if kind in ("call", "doc_edit") and not item.get("minutes"):
        raise ValueError("calls and doc edits need minutes")
    items.append(item)
    store.save_injected(items)
    store.audit("activity.injected", id=item["id"], consultant=c, kind=kind, title=title)
    return item
