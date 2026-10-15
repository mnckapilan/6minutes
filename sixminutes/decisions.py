"""record_decision: the manager's call on a staffing proposal or held item (non-agent endpoint, called from the UI).

Updates firm.yaml through policy-checked code, writes the decision and its reasons to Cognee, and releases held hours.
"""

from datetime import date, timedelta

from . import billing, memory, policy, store
from .tools import WeekContext, _add_entry

APPROVER = "Hannah Cole (Engagement Manager)"


def _date_label(d: date) -> str:
    return f"{d.day} {d.strftime('%b')}"


async def record_decision(target_id: str, action: str, note: str = "") -> dict:
    firm, ledger = store.load_firm(), store.load_ledger()
    today = date.today()
    decision = {"id": store.next_id(ledger, "D"), "target": target_id, "action": action, "by": APPROVER,
                "at": store.now(), "date_label": _date_label(today), "note": note}
    result: dict = {"decision": decision["id"]}

    if target_id.startswith("SP-"):
        prop = next(p for p in ledger["proposals"] if p["id"] == target_id)
        if prop["status"] != "open":
            return {"error": f"{target_id} is already {prop['status']}"}
        cons, proj = firm["consultants"][prop["consultant"]], firm["projects"][prop["project"]]
        ev = prop["evidence"]
        holds = [h for h in ledger["holds"] if h["id"] in prop["hold_ids"] and h["status"] == "open"]
        if action == "approve":
            # config change goes through code: one new assignment, from the proposed date
            proj["assignments"].append({"consultant": prop["consultant"], "role": prop["role"],
                                        "hours_per_week": 10, "start": prop["from_date"], "end": "2026-12-31",
                                        "decision": decision["id"]})
            store.save_firm(firm)
            ledger["decisions"].append(decision)
            text = (f"Decision {today.isoformat()}: {cons['name']} joined {proj['name']} ({prop['project']}) as "
                    f"{prop['role']} at £{prop['rate']}/h from {prop['from_date']}, approved by {APPROVER}. "
                    f"Evidence: {ev['client_calls']} client calls, {ev['doc_edits']} edits to "
                    f"{', '.join(ev['files']) or 'project files'}, {ev['emails']} emails on the project thread.")
            # release held hours: re-run each hold through the policy now that the assignment exists
            released, ctxs = [], {}
            for h in holds:
                ctx = ctxs.setdefault(h["week"], WeekContext(h["week"]))
                h["status"] = "released"  # free the activity ids so the policy check doesn't see them as taken
                try:
                    res = policy.evaluate_entry(firm, ledger, ctx.by_id, h["consultant"], h["project"],
                                                policy.round_hours(sum(policy.activity_minutes(
                                                    ctx.by_id[a], policy.effective_policy(firm, h["project"]))
                                                    for a in h["activity_ids"]) / 60, 0.1),
                                                h["activity_ids"], 1.0)
                except policy.PolicyError as e:
                    h["status"] = "open"
                    result.setdefault("errors", []).append(str(e))
                    continue
                e = _add_entry(ledger, firm, h["week"], h["consultant"], h["project"], res, h["activity_ids"],
                               "R2", 1.0, f"Released from hold {h['id']} after staffing approval",
                               text, decision_ref=decision["id"])
                h["released_entry"] = e["id"]
                released.append(e)
            prop["status"] = "approved"
            # bill the recovered hours straight away, on a supplementary invoice per period
            invoices = []
            for week in sorted({e["week"] for e in released}):
                entries = [e for e in released if e["week"] == week]
                invoices.append(billing.create_invoice(ledger, firm, prop["project"], week, entries,
                                                       memo=f"recovered time · {cons['name']}"))
            result.update(released_entries=[e["id"] for e in released],
                          recovered_gbp=round(sum(e["amount"] for e in released), 2),
                          invoices=[i["id"] for i in invoices])
        elif action == "not_billable":
            ledger["decisions"].append(decision)
            for h in holds:
                h["status"] = "not_billable"
            prop["status"] = "not_billable"
            text = (f"Decision {today.isoformat()}: {cons['name']}'s work on {proj['name']} in {prop['week']} is not "
                    f"billable (decided by {APPROVER}); do not propose this assignment again for that work.")
        else:
            for h in holds:
                h["status"] = "ignored"
            prop["status"] = "ignored"
            text = None
    else:
        hold = next(h for h in ledger["holds"] if h["id"] == target_id)
        if hold["status"] != "open":
            return {"error": f"{target_id} is already {hold['status']}"}
        if action == "not_billable":
            ledger["decisions"].append(decision)
            hold["status"] = "not_billable"
            ctx = WeekContext(hold["week"])
            threads = sorted({ctx.by_id[a]["thread_id"] for a in hold["activity_ids"] if ctx.by_id[a].get("thread_id")})
            proj_key = hold["project"]
            if proj_key and threads:
                nb = firm["projects"][proj_key].setdefault("non_billable_threads", [])
                nb.extend(t for t in threads if t not in nb)
                store.save_firm(firm)
            title = ctx.by_id[hold["activity_ids"][0]]["title"]
            text = (f"Decision {today.isoformat()}: the thread '{title}' ({', '.join(threads) or 'n/a'}) on "
                    f"{firm['projects'][proj_key]['name'] if proj_key else 'client work'} is internal and not billable "
                    f"(decided by {APPROVER}). Skip it in future weeks without asking.")
            result["threads_excluded"] = threads
            decision["threads"] = threads
        elif action == "approve":
            # bill the held item as-is (manager vouches for it)
            ctx = WeekContext(hold["week"])
            hold["status"] = "released"
            try:
                res = policy.evaluate_entry(firm, ledger, ctx.by_id, hold["consultant"], hold["project"],
                                            hold["hours"], hold["activity_ids"], 1.0)
            except policy.PolicyError as e:
                hold["status"] = "open"
                return {"error": str(e)}
            ledger["decisions"].append(decision)
            e = _add_entry(ledger, firm, hold["week"], hold["consultant"], hold["project"], res,
                           hold["activity_ids"], "R2", 1.0, f"Approved from hold {hold['id']}", "",
                           decision_ref=decision["id"])
            result["released_entries"] = [e["id"]]
            text = None
        else:
            hold["status"] = "ignored"
            text = None

    if text:
        decision["memory_text"] = text
    store.save_ledger(ledger)
    store.audit("decision", decision=decision["id"], target=target_id, action=action, by=APPROVER, result=result)
    if text:
        row = await memory.remember(text, kind="decision")
        result["memory"] = {"text": text, "backend": row["backend"]}
    return result


async def assign(consultant: str, project: str, role: str, start: str, hours_per_week: int = 10) -> dict:
    """Manual assignment from the project board. An open agent proposal for the same pair is approved instead."""
    firm, ledger = store.load_firm(), store.load_ledger()
    if consultant not in firm["consultants"] or project not in firm["projects"]:
        return {"error": "unknown consultant or project"}
    prop = next((p for p in ledger["proposals"] if p["status"] == "open" and p["consultant"] == consultant
                 and p["project"] == project), None)
    if prop:
        return await record_decision(prop["id"], "approve", note="approved by drag-and-drop on the project board")
    rate_card = policy.effective_policy(firm, project)["rate_card"]
    if role not in rate_card:
        return {"error": f"unknown role {role}; rate card: {list(rate_card)}"}
    date.fromisoformat(start)
    if policy.assignment_for(firm, project, consultant, start):
        return {"error": f"{consultant} is already assigned to {project} on {start}"}
    decision = {"id": store.next_id(ledger, "D"), "target": f"{consultant}->{project}", "action": "assign",
                "by": APPROVER, "at": store.now(), "date_label": _date_label(date.today()), "note": "project board"}
    firm["projects"][project]["assignments"].append({"consultant": consultant, "role": role,
                                                     "hours_per_week": int(hours_per_week), "start": start,
                                                     "end": "2026-12-31", "decision": decision["id"]})
    store.save_firm(firm)
    ledger["decisions"].append(decision)
    cons, proj = firm["consultants"][consultant], firm["projects"][project]
    text = (f"Decision {date.today().isoformat()}: {cons['name']} assigned to {proj['name']} ({project}) as {role} at "
            f"£{rate_card[role]}/h, {hours_per_week}h/week, from {start} (set by {APPROVER} on the project board).")
    decision["memory_text"] = text
    store.save_ledger(ledger)
    store.audit("decision", decision=decision["id"], action="assign", consultant=consultant, project=project,
                role=role, start=start, by=APPROVER)
    row = await memory.remember(text, kind="decision")
    return {"decision": decision["id"], "memory": {"text": text, "backend": row["backend"]}}


async def unassign(consultant: str, project: str) -> dict:
    """End an assignment from the project board (from today: later work on the project becomes R7)."""
    firm, ledger = store.load_firm(), store.load_ledger()
    today = date.today()
    asgs = [a for a in firm["projects"][project]["assignments"] if a["consultant"] == consultant
            and date.fromisoformat(str(a.get("end") or "2099-12-31")) >= today]
    if not asgs:
        return {"error": f"{consultant} has no current assignment on {project}"}
    decision = {"id": store.next_id(ledger, "D"), "target": f"{consultant}->{project}", "action": "unassign",
                "by": APPROVER, "at": store.now(), "date_label": _date_label(today), "note": "project board"}
    last_day = today - timedelta(days=1)
    for a in asgs:
        if date.fromisoformat(str(a["start"])) > last_day:
            firm["projects"][project]["assignments"].remove(a)
        else:
            a["end"] = last_day.isoformat()
    store.save_firm(firm)
    ledger["decisions"].append(decision)
    cons, proj = firm["consultants"][consultant], firm["projects"][project]
    text = (f"Decision {today.isoformat()}: {cons['name']} rolled off {proj['name']} ({project}) as of "
            f"{today.isoformat()} (set by {APPROVER} on the project board). Later work on it is unassigned.")
    decision["memory_text"] = text
    store.save_ledger(ledger)
    store.audit("decision", decision=decision["id"], action="unassign", consultant=consultant, project=project,
                by=APPROVER)
    row = await memory.remember(text, kind="decision")
    return {"decision": decision["id"], "memory": {"text": text, "backend": row["backend"]}}
