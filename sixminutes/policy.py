"""Billing policy R1-R7, enforced in code. The agent proposes; this module decides whether a proposal is allowed."""

from collections import defaultdict
from datetime import date

RULES = {
    "R1": "Match: activity -> project by domain, project code, channel, folder or file, limited to assigned projects",
    "R2": "Billable: client calls and project document work are billable at the assignment's rate",
    "R3": "Non-billable: internal-only work and threads marked not billable are skipped",
    "R4": "Rounding/caps: round to 0.1h; max 10h per consultant per day; flag over weekly allocation",
    "R5": "PO cap: stop billing at the project's cap and flag the overage",
    "R6": "Confidence: below threshold -> hold for review, never bill",
    "R7": "Unassigned work: never billed; held, with a staffing proposal for a manager",
}


class PolicyError(Exception):
    def __init__(self, rule: str, message: str):
        super().__init__(f"{rule}: {message}")
        self.rule = rule


def effective_policy(firm: dict, project_key: str | None = None) -> dict:
    """Firm default -> client override -> project override."""
    pol = dict(firm["firm"]["policy"])
    if project_key:
        proj = firm["projects"][project_key]
        if proj.get("client"):
            pol.update(firm["clients"][proj["client"]].get("policy") or {})
        pol.update(proj.get("policy") or {})
        pol["billable"] = proj.get("billable", True)
    return pol


def day_of(activity: dict) -> str:
    return activity["ts"][:10]


def activity_minutes(a: dict, pol: dict) -> int:
    if a["kind"] == "email":
        return pol["minutes_per_email"]
    if a["kind"] == "teams":
        return pol["minutes_per_teams_message"] * a.get("messages", 1)
    return a.get("minutes", 0)


def _domain(email: str) -> str:
    return email.split("@")[-1].lower()


def internal_only(a: dict, firm: dict) -> bool:
    firm_domain = firm["firm"]["domain"]
    parts = a.get("participants") or []
    if parts:
        return all(_domain(p) == firm_domain for p in parts)
    internal = firm["projects"].get("INTERNAL", {}).get("match", {})
    if a.get("channel") in internal.get("channels", []):
        return True
    return any((a.get("path") or "").startswith(f) for f in internal.get("folders", []))


def match_signals(a: dict, firm: dict) -> dict[str, list[str]]:
    """R1 signals: which projects this activity points at, and why."""
    out: dict[str, list[str]] = defaultdict(list)
    firm_domain = firm["firm"]["domain"]
    domains = {_domain(p) for p in a.get("participants") or []} - {firm_domain}
    path = a.get("path") or ""
    for key, proj in firm["projects"].items():
        m = proj.get("match", {})
        if key == "INTERNAL":
            if internal_only(a, firm):
                out[key].append("internal-only (firm participants / internal channel or folder)")
            continue
        for d in sorted(domains & set(m.get("domains", []))):
            out[key].append(f"client domain {d}")
        if proj.get("code") and f"[{proj['code']}]" in a.get("title", ""):
            out[key].append(f"project code {proj['code']}")
        if a.get("channel") in m.get("channels", []):
            out[key].append(f"channel {a['channel']}")
        if any(path.startswith(f) for f in m.get("folders", [])):
            out[key].append("project folder")
        if path and path.rsplit("/", 1)[-1] in m.get("files", []):
            out[key].append(f"key file {path.rsplit('/', 1)[-1]}")
    return dict(out)


def assignment_for(firm: dict, project_key: str, consultant: str, day: str) -> dict | None:
    d = date.fromisoformat(day)
    for asg in firm["projects"][project_key].get("assignments", []):
        if asg["consultant"] != consultant:
            continue
        start, end = date.fromisoformat(str(asg["start"])), date.fromisoformat(str(asg.get("end") or "2099-12-31"))
        if start <= d <= end:
            return asg
    return None


def rate_for(firm: dict, project_key: str, asg: dict) -> float:
    return float(asg.get("rate") or effective_policy(firm, project_key)["rate_card"][asg["role"]])


def round_hours(hours: float, unit: float) -> float:
    return round(round(hours / unit) * unit, 2)


def evaluate_entry(firm: dict, ledger: dict, acts: dict[str, dict], consultant: str, project: str,
                   hours: float, activity_ids: list[str], confidence: float) -> dict:
    """Check a proposed time entry against R1-R6 (and R7). Returns the billable result or raises PolicyError."""
    if project not in firm["projects"]:
        raise PolicyError("R1", f"unknown project {project}")
    if not activity_ids:
        raise PolicyError("R1", "no activity ids given")
    pol = effective_policy(firm, project)
    proj = firm["projects"][project]

    used = {aid for e in ledger["entries"] for aid in e["activity_ids"]}
    used |= {aid for s in ledger["skipped"] for aid in s["activity_ids"]}
    used |= {aid for h in ledger["holds"] if h["status"] == "open" for aid in h["activity_ids"]}
    items = []
    for aid in activity_ids:
        a = acts.get(aid)
        if not a:
            raise PolicyError("R1", f"activity {aid} not found in this week's feed")
        if a["consultant"] != consultant:
            raise PolicyError("R1", f"activity {aid} belongs to {a['consultant']}, not {consultant}")
        if aid in used:
            raise PolicyError("R1", f"activity {aid} already has a disposition (no double billing)")
        items.append(a)

    if not pol["billable"]:
        raise PolicyError("R3", f"{proj['name']} is non-billable; use mark_non_billable")
    for a in items:
        if a.get("thread_id") and a["thread_id"] in proj.get("non_billable_threads", []):
            raise PolicyError("R3", f"thread {a['thread_id']} is marked not billable for {proj['name']}; use mark_non_billable")
        if internal_only(a, firm):
            raise PolicyError("R3", f"activity {a['id']} is internal-only; use mark_non_billable")
        if project not in match_signals(a, firm):
            raise PolicyError("R1", f"activity {a['id']} has no match signal for {proj['name']}")

    asgs = {}
    for a in items:
        asg = assignment_for(firm, project, consultant, day_of(a))
        if not asg:
            raise PolicyError("R7", f"{consultant} is not assigned to {proj['name']} on {day_of(a)}: "
                                    "hold_for_review and propose_assignment instead")
        asgs[day_of(a)] = asg

    if confidence < pol["confidence_threshold"]:
        raise PolicyError("R6", f"confidence {confidence:.2f} is below threshold {pol['confidence_threshold']}: hold_for_review")

    # R4: hours come from the evidence, capped per day, rounded to 0.1h
    flags = []
    by_day: dict[str, float] = defaultdict(float)
    for a in items:
        by_day[day_of(a)] += activity_minutes(a, pol) / 60
    billed_by_day: dict[str, float] = defaultdict(float)
    for e in ledger["entries"]:
        if e["consultant"] == consultant:
            for d, h in e.get("hours_by_day", {}).items():
                billed_by_day[d] += h
    for d in list(by_day):
        room = max(0.0, pol["max_hours_per_day"] - billed_by_day[d])
        if by_day[d] > room:
            flags.append(f"R4: {d} capped at {pol['max_hours_per_day']}h/day ({by_day[d] - room:.1f}h not billed)")
            by_day[d] = room
    evidenced = round_hours(sum(by_day.values()), pol["rounding_hours"])
    if abs(hours - evidenced) > pol["rounding_hours"] + 1e-9:
        raise PolicyError("R4", f"proposed {hours}h but the evidence supports {evidenced}h; re-propose with {evidenced}")
    hours = evidenced
    if hours <= 0:
        raise PolicyError("R4", "nothing billable left after the daily cap")

    asg = next(iter(asgs.values()))
    rate = rate_for(firm, project, asg)

    # R4 weekly allocation (flag only)
    week_hours = sum(e["hours"] for e in ledger["entries"]
                     if e["consultant"] == consultant and e["project"] == project and e["week"] == items[0]["id"][:3])
    if asg.get("hours_per_week") and week_hours + hours > asg["hours_per_week"]:
        flags.append(f"R4: {consultant} now at {week_hours + hours:.1f}h on {proj['name']} this week "
                     f"(allocation {asg['hours_per_week']}h)")

    # R5 PO cap
    amount = round(hours * rate, 2)
    overage_hours = 0.0
    cap = pol.get("po_cap_gbp")
    if cap:
        billed = sum(e["amount"] for e in ledger["entries"] if e["project"] == project)
        room = max(0.0, cap - billed)
        if amount > room:
            billable_hours = round(int(room / rate / pol["rounding_hours"]) * pol["rounding_hours"], 2)
            overage_hours = round(hours - billable_hours, 2)
            hours, amount = billable_hours, round(billable_hours * rate, 2)
            flags.append(f"R5: {proj.get('po_number')} cap £{cap:,.0f} reached; {overage_hours}h "
                         f"(£{overage_hours * rate:,.2f}) held as overage for the engagement manager")
            scale = hours / max(sum(by_day.values()), 1e-9)
            by_day = {d: h * scale for d, h in by_day.items()}

    return {
        "hours": hours,
        "rate": rate,
        "amount": amount,
        "role": asg["role"],
        "assignment_decision": asg.get("decision"),
        "hours_by_day": {d: round(h, 2) for d, h in by_day.items()},
        "days": sorted(by_day),
        "flags": flags,
        "overage_hours": overage_hours,
    }


def staffing_evidence(firm: dict, acts: list[dict], consultant: str, project: str) -> dict:
    """Summarise a consultant's activity on a project they are not assigned to."""
    pol = effective_policy(firm, project)
    mine = [a for a in acts if a["consultant"] == consultant and project in match_signals(a, firm)
            and not internal_only(a, firm)]
    calls = [a for a in mine if a["kind"] == "call"]
    edits = sum(a.get("edits", 1) for a in mine if a["kind"] == "doc_edit")
    files = sorted({a["path"].rsplit("/", 1)[-1] for a in mine if a.get("path")})
    emails = [a for a in mine if a["kind"] == "email"]
    hours = sum(activity_minutes(a, pol) for a in mine) / 60
    return {
        "activity_ids": [a["id"] for a in mine],
        "hours": round(hours, 1),
        "days": len({day_of(a) for a in mine}),
        "first_day": min((day_of(a) for a in mine), default=None),
        "client_calls": len(calls),
        "doc_edits": edits,
        "files": files,
        "emails": len(emails),
        "sustained": hours >= pol["staffing_min_hours"] and len({day_of(a) for a in mine}) >= pol["staffing_min_days"],
    }
