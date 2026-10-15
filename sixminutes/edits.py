"""Consultant edits to their billables. Same guardrails as the agent: hours can only go down from the evidence,
project moves are re-checked against R1-R7, and changes to invoiced lines go out as Stripe credit notes."""

from . import billing, policy, store
from .tools import WeekContext, _add_hold


def _find(ledger: dict, entry_id: str) -> dict:
    e = next((e for e in ledger["entries"] if e["id"] == entry_id), None)
    if not e:
        raise ValueError(f"unknown entry {entry_id}")
    return e


def _write_down(ledger: dict, firm: dict, e: dict, new_hours: float, reason: str, by: str) -> dict:
    unit = policy.effective_policy(firm, e["project"])["rounding_hours"]
    new_hours = policy.round_hours(new_hours, unit)
    if new_hours < 0 or new_hours >= e["hours"]:
        raise ValueError(f"hours can only be written down (currently {e['hours']}h; the evidence caps it)")
    delta_h = round(e["hours"] - new_hours, 2)
    delta_amt = round(delta_h * e["rate"], 2)
    adj = {"at": store.now(), "by": by, "reason": reason, "from_hours": e["hours"], "to_hours": new_hours,
           "amount": -delta_amt}
    if e.get("invoice_id"):
        inv = next(i for i in ledger["invoices"] if i["id"] == e["invoice_id"])
        cn = billing.credit_note(inv, f"Write-down {e['id']}: -{delta_h}h · {billing.line_description(firm, e)}",
                                 delta_amt, reason=f"{by}: {reason}", entry_id=e["id"])
        adj["credit_note"] = cn["id"]
        ledger.setdefault("credit_notes", []).append(cn)
    e["hours_by_day"] = {d: round(h * new_hours / e["hours"], 2) for d, h in e["hours_by_day"].items()} if e["hours"] else {}
    e["hours"], e["amount"] = new_hours, round(new_hours * e["rate"], 2)
    e.setdefault("adjustments", []).append(adj)
    store.audit("entry.write_down", entry=e["id"], by=by, reason=reason, **{k: v for k, v in adj.items()
                                                                          if k not in ("at", "by", "reason")})
    return adj


def edit_entry(entry_id: str, by: str, reason: str, hours: float | None = None, project: str | None = None) -> dict:
    if not reason.strip():
        return {"error": "a reason is required (it goes on the audit trail)"}
    firm, ledger = store.load_firm(), store.load_ledger()
    try:
        e = _find(ledger, entry_id)
        if project and project != e["project"]:
            if e.get("invoice_id"):
                return {"error": "already invoiced: write it down instead (credit note), then re-bill"}
            ctx = WeekContext(e["week"])
            ledger["entries"].remove(e)  # free the activity ids for the policy check
            try:
                pol = policy.effective_policy(firm, project)
                evidenced = policy.round_hours(sum(policy.activity_minutes(ctx.by_id[a], pol)
                                                   for a in e["activity_ids"]) / 60, pol["rounding_hours"])
                res = policy.evaluate_entry(firm, ledger, ctx.by_id, e["consultant"], project, evidenced,
                                            e["activity_ids"], 1.0)
            except policy.PolicyError as err:
                store.audit("policy.rejected", tool="edit_entry", rule=err.rule, error=str(err), entry=e["id"], by=by)
                return {"error": str(err), "rule": err.rule}
            new = {**e, "project": project, "role": res["role"], "rate": res["rate"], "hours": res["hours"],
                   "amount": res["amount"], "hours_by_day": res["hours_by_day"], "flags": res["flags"],
                   "decision_ref": res.get("assignment_decision"), "decision_note": None}
            new.setdefault("adjustments", []).append({"at": store.now(), "by": by, "reason": reason,
                                                      "moved_from": e["project"], "moved_to": project})
            ledger["entries"].append(new)
            store.audit("entry.moved", entry=e["id"], by=by, reason=reason, from_project=e["project"],
                        to_project=project, hours=res["hours"])
            e = new
        if hours is not None and float(hours) != e["hours"]:
            _write_down(ledger, firm, e, float(hours), reason, by)
    except ValueError as err:
        return {"error": str(err)}
    store.save_ledger(ledger)
    return {"ok": True, "entry": e}


def remove_entry(entry_id: str, by: str, reason: str) -> dict:
    """Uninvoiced: drop the entry and hold its activities for review. Invoiced: write down to zero (credit note)."""
    if not reason.strip():
        return {"error": "a reason is required (it goes on the audit trail)"}
    firm, ledger = store.load_firm(), store.load_ledger()
    try:
        e = _find(ledger, entry_id)
        if e.get("invoice_id"):
            _write_down(ledger, firm, e, 0, reason, by)
            store.save_ledger(ledger)
            return {"ok": True, "credited": True, "entry": e}
    except ValueError as err:
        return {"error": str(err)}
    ledger["entries"].remove(e)
    hold = _add_hold(ledger, "review", e["consultant"], e["project"], e["activity_ids"], e["hours"],
                     f"Removed from billing by {by}: {reason}", rule="R6")
    store.save_ledger(ledger)
    store.audit("entry.removed", entry=e["id"], by=by, reason=reason, hold=hold["id"])
    return {"ok": True, "hold": hold["id"]}


def week_feed(week: str) -> list[dict]:
    """The raw activity feed, each item tagged with its project and disposition."""
    firm, ledger = store.load_firm(), store.load_ledger()
    disp: dict[str, dict] = {}
    for e in ledger["entries"]:
        for aid in e["activity_ids"]:
            disp[aid] = {"type": "billed", "id": e["id"], "project": e["project"], "rule": e["rule_id"],
                         "invoice_id": e.get("invoice_id"), "decision": e.get("decision_ref")}
    for s in ledger["skipped"]:
        for aid in s["activity_ids"]:
            disp[aid] = {"type": "non_billable", "id": s["id"], "project": None, "rule": s["rule_id"],
                         "reason": s["reason"]}
    for h in ledger["holds"]:
        for aid in h["activity_ids"]:
            if h["type"] == "po_cap" and aid in disp:
                disp[aid]["overage"] = h["id"]
                continue
            if h["status"] in ("released",) and aid in disp:
                continue
            disp.setdefault(aid, {"type": "held" if h["status"] == "open" else h["status"], "id": h["id"],
                                  "project": h["project"], "rule": h["rule"], "reason": h["reason"]})
    pol = policy.effective_policy(firm)
    out = []
    for a in store.load_activity(week):
        signals = policy.match_signals(a, firm)
        out.append({**a, "minutes": policy.activity_minutes(a, pol), "signals": signals,
                    "disposition": disp.get(a["id"], {"type": "pending"})})
    return out
