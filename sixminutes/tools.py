"""Agent tools. Claude chooses the next step; these functions enforce the policy and reject anything that breaks it."""

from collections import defaultdict

from . import billing, jev, memory, policy, store


class WeekContext:
    """Per-run state: the week's activity feed and the memory the agent has recalled."""

    def __init__(self, week: str):
        self.week = week
        self.activity = store.load_activity(week)
        self.by_id = {a["id"]: a for a in self.activity}
        self.recalls: list[dict] = []


def _week_entries(ledger: dict, week: str) -> list[dict]:
    return [e for e in ledger["entries"] if e["week"] == week]


# ---------------------------------------------------------------- tools


def list_activity(ctx: WeekContext, consultant: str, week: str) -> dict:
    if week != ctx.week:
        return {"error": f"this run is processing {ctx.week}"}
    firm = store.load_firm()
    if consultant not in firm["consultants"]:
        return {"error": f"unknown consultant {consultant}; known: {list(firm['consultants'])}"}
    ledger = store.load_ledger()
    done = {aid for e in ledger["entries"] for aid in e["activity_ids"]}
    done |= {aid for s in ledger["skipped"] for aid in s["activity_ids"]}
    done |= {aid for h in ledger["holds"] if h["status"] == "open" for aid in h["activity_ids"]}

    assigned = sorted({k for k, p in firm["projects"].items()
                       for asg in p.get("assignments", []) if asg["consultant"] == consultant})
    rows = []
    for a in ctx.activity:
        if a["consultant"] != consultant:
            continue
        signals = policy.match_signals(a, firm)
        row = {
            "id": a["id"], "day": a["ts"][:10], "time": a["ts"][11:16], "kind": a["kind"], "title": a["title"],
            "minutes": policy.activity_minutes(a, policy.effective_policy(firm)),
            "signals": signals,
        }
        for k in ("participants", "thread_id", "channel", "path", "edits", "note"):
            if a.get(k):
                row[k] = a[k]
        flags = []
        for key in signals:
            if key != "INTERNAL" and not policy.assignment_for(firm, key, consultant, row["day"]):
                flags.append(f"not assigned to {key} on {row['day']}")
            if a.get("thread_id") in firm["projects"][key].get("non_billable_threads", []):
                flags.append(f"thread marked not billable on {key} (manager decision)")
        if flags:
            row["flags"] = flags
        if a["id"] in done:
            row["already_handled"] = True
        rows.append(row)
    return {
        "consultant": consultant, "week": week,
        "assignments": {k: [asg for asg in firm["projects"][k]["assignments"] if asg["consultant"] == consultant]
                        for k in assigned},
        "activity": rows,
    }


async def recall_memory(ctx: WeekContext, question: str) -> dict:
    res = await memory.recall(question)
    ctx.recalls.append(res)
    return res


def propose_time_entry(ctx: WeekContext, consultant: str, project: str, hours: float, activity_ids: list[str],
                       rule_id: str, confidence: float, reason: str, memory_used: str = "") -> dict:
    firm, ledger = store.load_firm(), store.load_ledger()
    judgment = None
    try:
        # structural rules first (R1/R3/R4/R5/R7), then R6 on JEV's calibrated probability, not the agent's own
        res = policy.evaluate_entry(firm, ledger, ctx.by_id, consultant, project, float(hours), activity_ids, 1.0)
        pol = policy.effective_policy(firm, project)
        judgment = jev.entry_confidence(firm, consultant, project, [
            {**ctx.by_id[a], "minutes": policy.activity_minutes(ctx.by_id[a], pol)} for a in activity_ids])
        score = judgment["p"] if judgment else float(confidence)
        if score < pol["confidence_threshold"]:
            src = f"JEV p={score:.2f}" if judgment else f"agent confidence {score:.2f} (JEV unavailable)"
            raise policy.PolicyError("R6", f"{src} is below the {pol['confidence_threshold']} threshold for "
                                           f"{firm['projects'][project]['name']}: hold_for_review instead")
    except policy.PolicyError as e:
        store.audit("policy.rejected", tool="propose_time_entry", rule=e.rule, error=str(e), consultant=consultant,
                    project=project, activity_ids=activity_ids, reason=reason, jev=judgment)
        out = {"accepted": False, "rule": e.rule, "error": str(e)}
        if judgment:
            out["jev"] = judgment
        return out

    out: dict = {"accepted": True, "flags": res["flags"]}
    if res["hours"] > 0:
        entry = _add_entry(ledger, firm, ctx.week, consultant, project, res, activity_ids, rule_id,
                           judgment["p"] if judgment else confidence, reason, memory_used)
        entry["agent_confidence"], entry["jev"] = confidence, judgment
        out.update(entry_id=entry["id"], hours=entry["hours"], amount_gbp=entry["amount"],
                   line=billing.line_description(firm, entry), jev=judgment)
    if res["overage_hours"]:
        # one running overage item per project/week for the engagement manager
        hold = next((h for h in ledger["holds"] if h["type"] == "po_cap" and h["status"] == "open"
                     and h["project"] == project and h["week"] == ctx.week), None)
        if hold:
            hold["activity_ids"] += activity_ids
            hold["hours"] = round(hold["hours"] + res["overage_hours"], 2)
            hold["amount"] = round(hold.get("amount", 0) + res["overage_hours"] * res["rate"], 2)
            store.audit("hold.overage", hold=hold["id"], hours=res["overage_hours"], activity_ids=activity_ids)
        else:
            hold = _add_hold(ledger, "po_cap", consultant, project, activity_ids, res["overage_hours"],
                             f"{firm['projects'][project].get('po_number')} cap reached: work beyond the cap "
                             "not billed; engagement manager to raise a change order", rule="R5")
            hold["amount"] = round(res["overage_hours"] * res["rate"], 2)
        out["overage_hold"] = hold["id"]
        if res["hours"] == 0:
            out["accepted"] = False
            out["error"] = "R5: PO cap already reached; overage recorded and flagged"
    store.save_ledger(ledger)
    return out


def _add_entry(ledger, firm, week, consultant, project, res, activity_ids, rule_id, confidence, reason, memory_used,
               decision_ref=None):
    decision_ref = decision_ref or res.get("assignment_decision")
    note = None
    if decision_ref:
        d = next((d for d in ledger["decisions"] if d["id"] == decision_ref), None)
        if d:
            note = f"assignment approved {d['date_label']}"
    entry = {
        "id": store.next_id(ledger, "TE"), "week": week, "consultant": consultant, "project": project,
        "role": res["role"], "hours": res["hours"], "rate": res["rate"], "amount": res["amount"],
        "hours_by_day": res["hours_by_day"], "days": res["days"], "activity_ids": activity_ids,
        "rule_id": rule_id or "R2", "confidence": confidence, "reason": reason, "memory": memory_used,
        "decision_ref": decision_ref, "decision_note": note, "flags": res["flags"], "invoice_id": None,
        "created": store.now(),
    }
    ledger["entries"].append(entry)
    store.audit("time_entry", entry=entry["id"], consultant=consultant, project=project, hours=entry["hours"],
                amount=entry["amount"], rule=entry["rule_id"], activity_ids=activity_ids, reason=reason,
                confidence=confidence, memory=memory_used, decision=decision_ref, flags=res["flags"])
    return entry


def _add_hold(ledger, kind, consultant, project, activity_ids, hours, reason, rule):
    hold = {"id": store.next_id(ledger, "H"), "type": kind, "consultant": consultant, "project": project,
            "activity_ids": activity_ids, "hours": round(hours, 2), "reason": reason, "rule": rule,
            "status": "open", "proposal_id": None, "week": activity_ids[0][:3] if activity_ids else None,
            "created": store.now()}
    ledger["holds"].append(hold)
    store.audit("hold", hold=hold["id"], type=kind, consultant=consultant, project=project, hours=hold["hours"],
                activity_ids=activity_ids, reason=reason, rule=rule)
    return hold


def hold_for_review(ctx: WeekContext, activity_ids: list[str], reason: str, project: str = "",
                    rule_id: str = "R6") -> dict:
    firm, ledger = store.load_firm(), store.load_ledger()
    acts = [ctx.by_id.get(aid) for aid in activity_ids]
    if not activity_ids or None in acts:
        return {"accepted": False, "error": "unknown activity ids"}
    consultants = {a["consultant"] for a in acts}
    if len(consultants) != 1:
        return {"accepted": False, "error": "hold one consultant's activities at a time"}
    consultant = consultants.pop()
    pol = policy.effective_policy(firm, project or None)
    hours = sum(policy.activity_minutes(a, pol) for a in acts) / 60
    kind = "unassigned" if rule_id == "R7" else "review"
    if project and kind == "review" and project in firm["projects"]:
        if all(policy.assignment_for(firm, project, consultant, policy.day_of(a)) is None for a in acts):
            kind, rule_id = "unassigned", "R7"
    hold = _add_hold(ledger, kind, consultant, project or None, activity_ids, hours, reason, rule_id)
    if kind == "review" and project in firm["projects"] and firm["projects"][project].get("client"):
        hold["jev_internal"] = jev.internal_probability(firm, project, [
            {**a, "minutes": policy.activity_minutes(a, pol)} for a in acts])
    # attach to an open proposal for the same consultant/project, if one exists
    for p in ledger["proposals"]:
        if p["status"] == "open" and p["consultant"] == consultant and p["project"] == project:
            hold["proposal_id"] = p["id"]
            p["hold_ids"].append(hold["id"])
    store.save_ledger(ledger)
    out = {"accepted": True, "hold_id": hold["id"], "hours": hold["hours"], "type": kind}
    if hold.get("jev_internal"):
        out["jev_internal_p"] = hold["jev_internal"]["p"]
    return out


def mark_non_billable(ctx: WeekContext, activity_ids: list[str], rule_id: str, reason: str,
                      memory_used: str = "") -> dict:
    firm, ledger = store.load_firm(), store.load_ledger()
    acts = [ctx.by_id.get(aid) for aid in activity_ids]
    if not activity_ids or None in acts:
        return {"accepted": False, "error": "unknown activity ids"}
    # R3 is only for genuinely non-billable work: internal-only, internal project, or a thread a manager excluded.
    blocked = []
    for a in acts:
        signals = policy.match_signals(a, firm)
        excluded = any(a.get("thread_id") in firm["projects"][k].get("non_billable_threads", []) for k in signals)
        if not (policy.internal_only(a, firm) or set(signals) <= {"INTERNAL"} or excluded):
            blocked.append(a["id"])
    rejection = None
    if blocked:
        store.audit("policy.rejected", tool="mark_non_billable", rule="R3", activity_ids=blocked, reason=reason)
        rejection = (f"{blocked} look like client work (client signals, no manager exclusion). Bill them, or "
                     "hold_for_review if unsure: skipping client work needs a manager decision.")
        activity_ids = [aid for aid in activity_ids if aid not in blocked]
        acts = [ctx.by_id[aid] for aid in activity_ids]
        if not acts:
            return {"accepted": False, "rule": "R3", "error": rejection}
    pol = policy.effective_policy(firm)
    hours = round(sum(policy.activity_minutes(a, pol) for a in acts) / 60, 2)
    row = {"id": store.next_id(ledger, "NB"), "week": ctx.week, "consultant": acts[0]["consultant"],
           "activity_ids": activity_ids, "hours": hours, "rule_id": rule_id or "R3", "reason": reason,
           "memory": memory_used, "created": store.now()}
    ledger["skipped"].append(row)
    store.save_ledger(ledger)
    store.audit("non_billable", id=row["id"], activity_ids=activity_ids, hours=hours, rule=row["rule_id"],
                reason=reason, memory=memory_used)
    out = {"accepted": True, "id": row["id"], "hours": hours, "skipped_ids": activity_ids}
    if rejection:
        out.update(accepted="partial", rule="R3", rejected_ids=blocked, error=rejection)
    return out


def undisposed(ctx: WeekContext) -> list[str]:
    ledger = store.load_ledger()
    done = {aid for e in ledger["entries"] for aid in e["activity_ids"]}
    done |= {aid for s in ledger["skipped"] for aid in s["activity_ids"]}
    done |= {aid for h in ledger["holds"] for aid in h["activity_ids"]}
    return [a["id"] for a in ctx.activity if a["id"] not in done]


def propose_assignment(ctx: WeekContext, consultant: str, project: str, role: str, from_date: str,
                       evidence: str) -> dict:
    firm, ledger = store.load_firm(), store.load_ledger()
    if project not in firm["projects"] or consultant not in firm["consultants"]:
        return {"accepted": False, "error": "unknown consultant or project"}
    if policy.assignment_for(firm, project, consultant, from_date):
        return {"accepted": False, "error": f"{consultant} is already assigned to {project} on {from_date}"}
    if any(p for p in ledger["proposals"] if p["status"] == "open" and p["consultant"] == consultant
           and p["project"] == project):
        return {"accepted": False, "error": "an open proposal already exists for this consultant and project"}
    ev = policy.staffing_evidence(firm, ctx.activity, consultant, project)
    if not ev["sustained"]:
        pol = policy.effective_policy(firm, project)
        return {"accepted": False, "error": f"not sustained: {ev['hours']}h over {ev['days']} day(s); need "
                                            f"{pol['staffing_min_hours']}h across {pol['staffing_min_days']} days"}
    pol = policy.effective_policy(firm, project)
    judgment = jev.staffing_judgment(firm, consultant, project, ev, [
        {**ctx.by_id[a], "minutes": policy.activity_minutes(ctx.by_id[a], pol)} for a in ev["activity_ids"]])
    if judgment and (judgment["choice"] != "assign" or judgment["p_assign"] < pol["confidence_threshold"]):
        store.audit("policy.rejected", tool="propose_assignment", rule="R6", jev=judgment, consultant=consultant,
                    project=project)
        return {"accepted": False, "rule": "R6", "jev": judgment,
                "error": f"JEV judges this '{judgment['choice']}' (assign p={judgment['p_assign']:.2f}): keep the "
                         "work held for review instead of proposing an assignment"}
    rate_card = pol["rate_card"]
    if role not in rate_card:
        return {"accepted": False, "error": f"unknown role {role}; rate card: {list(rate_card)}"}
    proposal = {
        "id": store.next_id(ledger, "SP"), "consultant": consultant, "project": project, "role": role,
        "rate": rate_card[role], "from_date": from_date, "agent_evidence": evidence, "evidence": ev,
        "hold_ids": [h["id"] for h in ledger["holds"] if h["status"] == "open" and h["consultant"] == consultant
                     and h["project"] == project and h["type"] in ("unassigned", "review")],
        "status": "open", "week": ctx.week, "created": store.now(), "jev": judgment,
    }
    for h in ledger["holds"]:
        if h["id"] in proposal["hold_ids"]:
            h["proposal_id"] = proposal["id"]
    ledger["proposals"].append(proposal)
    store.save_ledger(ledger)
    store.audit("staffing.proposal", proposal=proposal["id"], consultant=consultant, project=project, role=role,
                from_date=from_date, evidence=ev, agent_evidence=evidence)
    return {"accepted": True, "proposal_id": proposal["id"], "jev": judgment,
            "evidence": {k: v for k, v in ev.items() if k != "activity_ids"}}


def create_invoice(ctx: WeekContext, project: str, period: str) -> dict:
    firm, ledger = store.load_firm(), store.load_ledger()
    if project not in firm["projects"] or not firm["projects"][project].get("client"):
        return {"accepted": False, "error": f"{project} is not a billable client project"}
    entries = [e for e in _week_entries(ledger, period) if e["project"] == project and not e["invoice_id"]]
    if not entries:
        return {"accepted": False, "error": f"no uninvoiced time entries for {project} in {period}"}
    open_holds = [h["id"] for h in ledger["holds"] if h["status"] == "open" and h["project"] == project]
    inv = billing.create_invoice(ledger, firm, project, period, entries)
    store.save_ledger(ledger)
    return {"accepted": True, "invoice_id": inv["id"], "amount_gbp": inv["amount"], "lines": len(entries),
            "open_holds_not_billed": open_holds, "status": inv["status"]}


# ---------------------------------------------------------------- schemas

_ids = {"type": "array", "items": {"type": "string"}}

SCHEMAS = [
    {"name": "list_activity",
     "description": "Read one consultant's activity feed for the week (emails, calls, Teams, doc edits). Each item "
                    "carries R1 match signals (which projects it points at and why) and flags such as 'not assigned'.",
     "input_schema": {"type": "object", "properties": {"consultant": {"type": "string"}, "week": {"type": "string"}},
                      "required": ["consultant", "week"]}},
    {"name": "recall_memory",
     "description": "Ask Cognee memory a question: project context (which project a file/thread/channel belongs to) "
                    "and past manager decisions (approved assignments, threads marked not billable).",
     "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
    {"name": "propose_time_entry",
     "description": "Bill a group of one consultant's activities to a project (ideally one entry per consultant/"
                    "project/day). Hours must match the evidence (activity minutes, rounded to 0.1h). Code checks "
                    "R1-R7 and rejects violations with the rule id. Cite any memory you relied on in memory_used.",
     "input_schema": {"type": "object", "properties": {
         "consultant": {"type": "string"}, "project": {"type": "string", "description": "project key, e.g. ACME-Q3"},
         "hours": {"type": "number"}, "activity_ids": _ids,
         "rule_id": {"type": "string", "description": "the billing rule applied, usually R2"},
         "confidence": {"type": "number", "description": "0-1 your own confidence; code re-scores it independently with JEV (R6)"},
         "reason": {"type": "string", "description": "one-line explanation for the invoice line"},
         "memory_used": {"type": "string", "description": "memory recalled that supports this entry, if any"}},
         "required": ["consultant", "project", "hours", "activity_ids", "rule_id", "confidence", "reason"]}},
    {"name": "hold_for_review",
     "description": "Park activities for a human: low confidence (R6) or unassigned work (R7). Never bills.",
     "input_schema": {"type": "object", "properties": {
         "activity_ids": _ids, "reason": {"type": "string"},
         "project": {"type": "string", "description": "the project the work most likely belongs to"},
         "rule_id": {"type": "string", "enum": ["R6", "R7"]}},
         "required": ["activity_ids", "reason", "rule_id"]}},
    {"name": "mark_non_billable",
     "description": "Record activities as non-billable under R3: internal-only work, the Internal project, or a "
                    "thread a manager has marked not billable. Rejected for client work.",
     "input_schema": {"type": "object", "properties": {
         "activity_ids": _ids, "rule_id": {"type": "string"}, "reason": {"type": "string"},
         "memory_used": {"type": "string"}}, "required": ["activity_ids", "rule_id", "reason"]}},
    {"name": "propose_assignment",
     "description": "Raise a staffing proposal for a manager when a consultant is doing sustained work on a project "
                    "they are not assigned to. Code checks the evidence is sustained (hours across days).",
     "input_schema": {"type": "object", "properties": {
         "consultant": {"type": "string"}, "project": {"type": "string"}, "role": {"type": "string"},
         "from_date": {"type": "string", "description": "YYYY-MM-DD, first day of the work"},
         "evidence": {"type": "string"}}, "required": ["consultant", "project", "role", "from_date", "evidence"]}},
    {"name": "create_invoice",
     "description": "Build, finalise and send the Stripe invoice for one project's uninvoiced time entries in the "
                    "period. Call once per client project after all its entries for the week are proposed.",
     "input_schema": {"type": "object", "properties": {"project": {"type": "string"}, "period": {"type": "string"}},
                      "required": ["project", "period"]}},
]

HANDLERS = {
    "list_activity": list_activity, "recall_memory": recall_memory, "propose_time_entry": propose_time_entry,
    "hold_for_review": hold_for_review, "mark_non_billable": mark_non_billable,
    "propose_assignment": propose_assignment, "create_invoice": create_invoice,
}
