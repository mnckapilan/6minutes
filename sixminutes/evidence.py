"""Evidence behind every line, and the week-over-week "memory moment".

Both are assembled on demand from the ledger, the activity feeds (including injected items), firm config and the audit
log: nothing here is stored, so it always reflects what the agent actually did.
"""

from . import billing, policy, store

ACTION_WORDS = {"approve": "assignment approved", "assign": "assignment set on the project board",
                "unassign": "roll-off recorded", "not_billable": "marked not billable", "ignore": "ignored"}


def _cited(text: str | None) -> str:
    """The agent writes 'None' / 'n/a' when it relied on no memory: that is not a citation."""
    t = (text or "").strip()
    return "" if t.lower().strip(".") in ("", "none", "n/a", "na", "no", "null") else t


def _headline(d: dict) -> str:
    return f"{ACTION_WORDS.get(d['action'], d['action'])} {d['date_label']} by {d['by']}"


def _decision_view(d: dict | None) -> dict | None:
    if not d:
        return None
    return {"id": d["id"], "action": d["action"], "by": d["by"], "date_label": d["date_label"], "at": d["at"],
            "note": d.get("note", ""), "target": d.get("target"), "headline": _headline(d),
            "memory_text": d.get("memory_text")}


def _activities(firm: dict, ids: list[str]) -> list[dict]:
    pol = policy.effective_policy(firm)
    by_id = {}
    for w in {i.split("-")[0] for i in ids} & set(store.weeks()):
        by_id.update({a["id"]: a for a in store.load_activity(w)})
    out = []
    for i in ids:
        a = by_id.get(i)
        if a:
            out.append({**a, "minutes": policy.activity_minutes(a, pol), "signals": policy.match_signals(a, firm)})
    return out


def _audit_for(ids: set[str], activity_ids: list[str]) -> list[dict]:
    keep = []
    acts = set(activity_ids)
    for r in store.read_audit(3000):
        ev = r["event"]
        if ev in ("tool", "run.start", "run.end", "memory.recall", "memory.remember"):
            continue
        if ids & {r.get("entry"), r.get("hold"), r.get("id"), r.get("invoice"), r.get("proposal")} or (
                acts and acts & set(r.get("activity_ids") or []) and ev in (
                    "time_entry", "hold", "non_billable", "policy.rejected", "hold.overage", "entry.removed")) or (
                ev == "decision" and ids & set((r.get("result") or {}).get("released_entries") or [])):
            keep.append(r)
    return keep


def _invoice_view(ledger: dict, inv_id: str | None) -> dict | None:
    inv = next((i for i in ledger["invoices"] if i["id"] == inv_id), None) if inv_id else None
    if not inv:
        return None
    return {k: inv.get(k) for k in ("id", "number", "status", "amount", "period", "memo", "hosted_invoice_url",
                                    "dashboard_url", "dry_run", "credited", "project")}


def _threshold(firm: dict, project: str | None) -> float:
    return policy.effective_policy(firm, project if project in firm["projects"] else None)["confidence_threshold"]


def _stripe_metadata(e: dict) -> dict:
    return {"entry_id": e["id"], "consultant": e["consultant"], "role": e["role"], "hours": billing._md(e["hours"]),
            "rate_gbp": billing._md(e["rate"]), "rule": e["rule_id"], "activity_ids": ",".join(e["activity_ids"]),
            "decision": e.get("decision_ref") or "", "memory": billing._md(e.get("memory") or ""),
            "confidence": billing._md(e["confidence"]),
            "jev": f"p={e['jev']['p']} {e['jev']['model']} {e['jev']['id']}" if e.get("jev") else ""}


def _base(firm: dict, ledger: dict, kind: str, ident: str, ids: list[str]) -> dict:
    return {"kind": kind, "id": ident, "activities": _activities(firm, ids)}


def entry_evidence(firm: dict, ledger: dict, e: dict) -> dict:
    out = _base(firm, ledger, "entry", e["id"], e["activity_ids"])
    d = next((d for d in ledger["decisions"] if d["id"] == e.get("decision_ref")), None)
    cns = [c for c in ledger.get("credit_notes", []) if c.get("entry") == e["id"]]
    out.update(
        title=f"{firm['consultants'][e['consultant']]['name']} · {firm['projects'][e['project']]['name']} · {e['hours']}h",
        status="billed", consultant=e["consultant"], project=e["project"], week=e["week"],
        summary={"hours": e["hours"], "rate": e["rate"], "amount": e["amount"], "role": e["role"], "days": e["days"],
                 "hours_by_day": e["hours_by_day"], "flags": e.get("flags", []),
                 "line": billing.line_description(firm, e)},
        rule={"id": e["rule_id"], "text": policy.RULES.get(e["rule_id"], "")},
        reason=e["reason"],
        confidence={"jev": e.get("jev"), "agent": e.get("agent_confidence"), "used": e["confidence"],
                    "threshold": _threshold(firm, e["project"])},
        memory={"cited": _cited(e.get("memory")), "decision": _decision_view(d)},
        adjustments=e.get("adjustments", []), credit_notes=cns,
        invoice=_invoice_view(ledger, e.get("invoice_id")), stripe_metadata=_stripe_metadata(e),
    )
    out["audit"] = _audit_for({e["id"]}, e["activity_ids"])
    return out


def hold_evidence(firm: dict, ledger: dict, h: dict) -> dict:
    out = _base(firm, ledger, "hold", h["id"], h["activity_ids"])
    prop = next((p for p in ledger["proposals"] if p["id"] == h.get("proposal_id")), None)
    d = next((d for d in ledger["decisions"] if d.get("target") in (h["id"], h.get("proposal_id"))), None)
    proj = h.get("project")
    label = {"po_cap": "PO cap overage", "unassigned": "Unassigned work", "review": "Held for review"}[h["type"]]
    out.update(
        title=f"{label} · {firm['consultants'][h['consultant']]['name']}"
              + (f" · {firm['projects'][proj]['name']}" if proj in firm["projects"] else "") + f" · {h['hours']}h",
        status=h["status"], consultant=h["consultant"], project=proj, week=h.get("week"),
        summary={"hours": h["hours"], "amount": h.get("amount"), "type": h["type"],
                 "released_entry": h.get("released_entry")},
        rule={"id": h["rule"], "text": policy.RULES.get(h["rule"], "")}, reason=h["reason"],
        confidence={"jev": h.get("jev_internal"), "jev_label": "JEV probability the work is internal",
                    "threshold": _threshold(firm, proj)} if h.get("jev_internal") else
                   {"jev": None, "threshold": _threshold(firm, proj)},
        memory={"cited": "", "decision": _decision_view(d)},
        proposal=({k: prop.get(k) for k in ("id", "status", "role", "rate", "from_date", "agent_evidence",
                                            "evidence", "jev")} if prop else None),
        adjustments=[], credit_notes=[], invoice=None,
    )
    if h.get("released_entry"):
        e = next((e for e in ledger["entries"] if e["id"] == h["released_entry"]), None)
        out["invoice"] = _invoice_view(ledger, e.get("invoice_id")) if e else None
    out["audit"] = _audit_for({h["id"], h.get("proposal_id")} - {None}, h["activity_ids"])
    return out


def skipped_evidence(firm: dict, ledger: dict, s: dict) -> dict:
    out = _base(firm, ledger, "skipped", s["id"], s["activity_ids"])
    threads = {a.get("thread_id") for a in out["activities"]} - {None}
    d = next((d for d in ledger["decisions"] if set(d.get("threads") or []) & threads), None)
    out.update(
        title=f"Not billable · {firm['consultants'][s['consultant']]['name']} · {s['hours']}h",
        status="non_billable", consultant=s["consultant"], project=None, week=s["week"],
        summary={"hours": s["hours"]}, rule={"id": s["rule_id"], "text": policy.RULES.get(s["rule_id"], "")},
        reason=s["reason"], confidence=None, memory={"cited": _cited(s.get("memory")), "decision": _decision_view(d)},
        adjustments=[], credit_notes=[], invoice=None)
    out["audit"] = _audit_for({s["id"]}, s["activity_ids"])
    return out


def invoice_evidence(firm: dict, ledger: dict, inv: dict) -> dict:
    ents = [e for e in ledger["entries"] if e["id"] in inv["entries"]]
    return {"kind": "invoice", "id": inv["id"], "status": inv["status"],
            "title": f"Invoice {inv.get('number') or inv['id'][:14]} · {firm['projects'][inv['project']]['name']} · {inv['period']}",
            "invoice": _invoice_view(ledger, inv["id"]), "activities": [], "audit": _audit_for({inv["id"]}, []),
            "lines": [{"id": e["id"], "line": billing.line_description(firm, e), "amount": e["amount"],
                       "rule": e["rule_id"], "jev": (e.get("jev") or {}).get("p"),
                       "decision": e.get("decision_ref")} for e in ents],
            "credit_notes": [c for c in ledger.get("credit_notes", []) if c["invoice"] == inv["id"]]}


def activity_evidence(firm: dict, ledger: dict, aid: str) -> dict | None:
    """An activity id resolves to whatever disposed of it; an unprocessed one gets a 'waiting for the agent' view."""
    for e in ledger["entries"]:
        if aid in e["activity_ids"]:
            return entry_evidence(firm, ledger, e)
    for s in ledger["skipped"]:
        if aid in s["activity_ids"]:
            return skipped_evidence(firm, ledger, s)
    for h in sorted(ledger["holds"], key=lambda h: h["type"] == "po_cap"):
        if aid in h["activity_ids"] and h["status"] in ("open", "not_billable", "ignored"):
            return hold_evidence(firm, ledger, h)
    acts = _activities(firm, [aid])
    if not acts:
        return None
    return {"kind": "activity", "id": aid, "title": acts[0]["title"], "status": "pending", "activities": acts,
            "consultant": acts[0]["consultant"], "rule": None, "reason": "Not processed yet: the agent has not "
            "looked at this item. Run (or re-run) the week.", "confidence": None, "memory": None, "audit": [],
            "adjustments": [], "credit_notes": [], "invoice": None}


def evidence(ident: str) -> dict | None:
    firm, ledger = store.load_firm(), store.load_ledger()
    if ident.startswith("TE-"):
        e = next((e for e in ledger["entries"] if e["id"] == ident), None)
        return entry_evidence(firm, ledger, e) if e else None
    if ident.startswith("H-"):
        h = next((h for h in ledger["holds"] if h["id"] == ident), None)
        return hold_evidence(firm, ledger, h) if h else None
    if ident.startswith("NB-"):
        s = next((s for s in ledger["skipped"] if s["id"] == ident), None)
        return skipped_evidence(firm, ledger, s) if s else None
    inv = next((i for i in ledger["invoices"] if i["id"] == ident), None)
    if inv:
        return invoice_evidence(firm, ledger, inv)
    return activity_evidence(firm, ledger, ident)


# ---------------------------------------------------------------- week comparison


def _src_week(ledger: dict, d: dict) -> str | None:
    t = d.get("target") or ""
    if t.startswith("SP-"):
        return next((p["week"] for p in ledger["proposals"] if p["id"] == t), None)
    if t.startswith("H-"):
        return next((h["week"] for h in ledger["holds"] if h["id"] == t), None)
    return None


def compare(a: str, b: str) -> dict:
    """What week `b` did differently because of what was decided/remembered in week `a` (and before)."""
    firm, ledger = store.load_firm(), store.load_ledger()
    decs = {d["id"]: d for d in ledger["decisions"]}
    name = lambda c: firm["consultants"][c]["name"].split()[0]  # noqa: E731
    pname = lambda k: firm["projects"][k]["name"] if k in firm["projects"] else k  # noqa: E731
    items: list[dict] = []

    # 1. work billed automatically because a manager approved the assignment
    groups: dict[tuple, list[dict]] = {}
    for e in ledger["entries"]:
        d = decs.get(e.get("decision_ref"))
        if e["week"] != b or not d or str(e["reason"]).startswith("Released from hold"):
            continue
        sw = _src_week(ledger, d)
        if sw and sw >= b:
            continue
        groups.setdefault((d["id"], e["consultant"], e["project"]), []).append(e)
    for (did, cons, proj), es in groups.items():
        d = decs[did]
        prior = [h for h in ledger["holds"] if h["consultant"] == cons and h["project"] == proj
                 and h.get("week") and h["week"] < b and h["type"] in ("unassigned", "review")]
        hrs, amt = round(sum(e["hours"] for e in es), 2), round(sum(e["amount"] for e in es), 2)
        mem = next((_cited(e.get("memory")) for e in es if _cited(e.get("memory"))), "")
        items.append({
            "kind": "auto_billed", "title": f"{name(cons)}'s {pname(proj)} work billed without asking",
            "consultant": cons, "project": proj, "hours": hrs, "amount": amt,
            "before": {"label": a, "text": (f"Held {round(sum(h['hours'] for h in prior), 1)}h as unassigned (R7); "
                                            "a manager had to decide") if prior else
                       f"{name(cons)} was not assigned: this work would have been held under R7",
                       "refs": [h["id"] for h in prior]},
            "after": {"label": b, "text": f"{hrs}h billed automatically · {len(es)} entr{'y' if len(es) == 1 else 'ies'}"
                                          f" · £{amt:,.2f}" + (f" · {es[0].get('decision_note')}" if es[0].get("decision_note") else ""),
                      "refs": [e["id"] for e in es]},
            "decision": _decision_view(d), "memory": mem, "asked_avoided": True})

    # 2. threads a manager marked not billable: skipped without asking
    sk_groups: dict[tuple, list[dict]] = {}
    acts_b = {x["id"]: x for x in store.load_activity(b)}
    for s in ledger["skipped"]:
        if s["week"] != b:
            continue
        threads = {acts_b[i].get("thread_id") for i in s["activity_ids"] if i in acts_b} - {None}
        d = next((d for d in ledger["decisions"] if set(d.get("threads") or []) & threads), None)
        if d or _cited(s.get("memory")):
            sk_groups.setdefault((d["id"] if d else None, s["consultant"]), []).append(s)
    for (did, cons), ss in sk_groups.items():
        d = decs.get(did)
        threads = sorted({acts_b[i].get("thread_id") for s in ss for i in s["activity_ids"] if i in acts_b} - {None})
        prior = [h for h in ledger["holds"] if h.get("week") and h["week"] < b and h["type"] == "review"
                 and h["status"] == "not_billable" and any(
                     x.get("thread_id") in threads for x in store.load_activity(h["week"]) if x["id"] in h["activity_ids"])]
        hrs = round(sum(s["hours"] for s in ss), 2)
        first_act = acts_b.get(ss[0]["activity_ids"][0], {})
        items.append({
            "kind": "skipped_silently", "title": f"“{first_act.get('title', 'thread')}” skipped as not billable",
            "consultant": cons, "project": None, "hours": hrs, "amount": 0,
            "before": {"label": a, "text": (f"Held {round(sum(h['hours'] for h in prior), 1)}h as ambiguous; "
                                            "a manager marked the thread not billable") if prior else
                       "Ambiguous: would have been held for a manager to rule on",
                       "refs": [h["id"] for h in prior]},
            "after": {"label": b, "text": f"{hrs}h skipped under {ss[0]['rule_id']} · no question asked",
                      "refs": [s["id"] for s in ss]},
            "decision": _decision_view(d), "memory": _cited(ss[0].get("memory")), "asked_avoided": True})

    # 3. PO cap: earlier weeks used up the budget
    for h in ledger["holds"]:
        if h["type"] != "po_cap" or h.get("week") != b:
            continue
        cap = policy.effective_policy(firm, h["project"]).get("po_cap_gbp")
        used = round(sum(e["amount"] for e in ledger["entries"] if e["project"] == h["project"] and e["week"] < b), 2)
        billed_b = round(sum(e["amount"] for e in ledger["entries"] if e["project"] == h["project"] and e["week"] == b), 2)
        items.append({
            "kind": "po_cap", "title": f"{pname(h['project'])} hit its PO cap",
            "consultant": h["consultant"], "project": h["project"], "hours": h["hours"], "amount": h.get("amount", 0),
            "before": {"label": a, "text": f"£{used:,.0f} of £{cap:,.0f} cap already billed before {b}" if cap else "",
                       "refs": []},
            "after": {"label": b, "text": f"£{billed_b:,.0f} billed up to the cap; {h['hours']}h (£{h.get('amount', 0):,.0f}) "
                                          "flagged as overage, not billed", "refs": [h["id"]]},
            "decision": None, "memory": "", "asked_avoided": False})

    # 4. any other entry where the agent cited memory
    shown = {r for it in items for r in it["after"]["refs"]}
    mem_groups: dict[tuple, list[dict]] = {}
    for e in ledger["entries"]:
        if e["week"] == b and _cited(e.get("memory")) and e["id"] not in shown:
            mem_groups.setdefault((e["consultant"], e["project"], _cited(e["memory"])), []).append(e)
    for (cons, proj, mem), es in mem_groups.items():
        hrs = round(sum(e["hours"] for e in es), 2)
        items.append({
            "kind": "memory", "title": f"{name(cons)}'s {pname(proj)} work billed using recalled context",
            "consultant": cons, "project": proj, "hours": hrs, "amount": round(sum(e["amount"] for e in es), 2),
            "before": {"label": a, "text": "No such context existed yet", "refs": []},
            "after": {"label": b, "text": f"{hrs}h billed citing memory", "refs": [e["id"] for e in es]},
            "decision": _decision_view(decs.get(es[0].get("decision_ref"))), "memory": mem, "asked_avoided": False})

    return {"a": a, "b": b, "items": items,
            "totals": {"auto_hours": round(sum(i["hours"] for i in items if i["kind"] == "auto_billed"), 2),
                       "auto_amount": round(sum(i["amount"] for i in items if i["kind"] == "auto_billed"), 2),
                       "skipped_hours": round(sum(i["hours"] for i in items if i["kind"] == "skipped_silently"), 2),
                       "questions_avoided": sum(1 for i in items if i["asked_avoided"]),
                       "decisions": len({i["decision"]["id"] for i in items if i["decision"]})}}
