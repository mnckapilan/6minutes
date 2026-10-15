"""FastAPI app: one page, live agent steps over SSE, decisions, Stripe links."""

import asyncio
import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from datetime import date

from . import agent, billing, decisions, edits, evidence, inject, memory, policy, store
from .tools import WeekContext, undisposed

app = FastAPI(title="6minutes")
_run_lock = asyncio.Lock()


@app.get("/")
def index():
    return FileResponse(store.ROOT / "static" / "index.html")


def week_info() -> list[dict]:
    """Every week with a feed: date range label, size, how many items still await the agent, whether it has run."""
    ran = {}
    for r in store.read_audit(100000):
        if r["event"] == "run.end":
            ran[r["week"]] = ran.get(r["week"], 0) + 1
    out = []
    for w in store.weeks():
        ctx = WeekContext(w)
        days = sorted({a["ts"][:10] for a in ctx.activity})
        d0, d1 = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
        label = f"{d0.day}–{d1.day} {d1.strftime('%b')}" if d0.month == d1.month else \
            f"{d0.day} {d0.strftime('%b')}–{d1.day} {d1.strftime('%b')}"
        out.append({"id": w, "label": label, "items": len(ctx.activity), "pending": len(undisposed(ctx)),
                    "injected": sum(1 for a in ctx.activity if a.get("injected")), "runs": ran.get(w, 0)})
    return out


@app.get("/api/state")
def state():
    firm, ledger = store.load_firm(), store.load_ledger()
    projects = {k: {**p, "effective_policy": policy.effective_policy(firm, k)} for k, p in firm["projects"].items()}
    recovered = sum(e["amount"] for e in ledger["entries"] if e.get("decision_ref"))
    return {
        "firm": {**firm, "projects": projects}, "ledger": ledger, "audit": store.read_audit(400)[::-1],
        "memory": memory.mirror()[::-1], "rules": policy.RULES,
        "stripe": {"dry_run": billing.DRY_RUN}, "weeks": week_info(), "cognee": memory.enabled(), "model": agent.MODEL,
        "stats": {
            "billed": round(sum(e["amount"] for e in ledger["entries"]), 2),
            "hours": round(sum(e["hours"] for e in ledger["entries"]), 1),
            "recovered": round(recovered, 2),
            "held": round(sum(h["hours"] for h in ledger["holds"] if h["status"] == "open"), 1),
            "invoiced": round(sum(i["amount"] for i in ledger["invoices"]), 2),
        },
    }


@app.get("/api/run/{week}")
async def run(week: str):
    if week not in store.weeks():
        raise HTTPException(404, "unknown week")
    if _run_lock.locked():
        raise HTTPException(409, "a run is already in progress")

    async def events():
        async with _run_lock:
            try:
                async for ev in agent.run_week(week):
                    yield {"data": json.dumps(ev, default=str)}
            except Exception as e:
                store.audit("run.error", week=week, error=str(e))
                yield {"data": json.dumps({"type": "error", "text": f"{type(e).__name__}: {e}"})}
                yield {"data": json.dumps({"type": "done", "week": week})}

    return EventSourceResponse(events())


class Decision(BaseModel):
    target: str
    action: str  # approve | not_billable | ignore
    note: str = ""


@app.post("/api/decision")
async def decide(d: Decision):
    if d.action not in ("approve", "not_billable", "ignore"):
        raise HTTPException(400, "bad action")
    return await decisions.record_decision(d.target, d.action, d.note)


class Assignment(BaseModel):
    consultant: str
    project: str
    role: str = ""
    start: str = ""
    hours_per_week: int = 10


@app.post("/api/assignments")
async def assign(a: Assignment):
    return await decisions.assign(a.consultant, a.project, a.role, a.start, a.hours_per_week)


@app.post("/api/assignments/end")
async def unassign(a: Assignment):
    return await decisions.unassign(a.consultant, a.project)


@app.get("/api/activity/{week}")
def activity(week: str):
    if week not in store.weeks():
        raise HTTPException(404, "unknown week")
    return edits.week_feed(week)


@app.get("/api/evidence/{ident}")
def get_evidence(ident: str):
    """Everything behind one line: entry (TE-), hold (H-), skipped (NB-), invoice (in_) or an activity id."""
    ev = evidence.evidence(ident)
    if not ev:
        raise HTTPException(404, f"nothing found for {ident}")
    return ev


@app.get("/api/compare")
def compare(a: str | None = None, b: str | None = None):
    """What the later week did differently because of the earlier week's decisions and memory."""
    ws = store.weeks()
    if len(ws) < 2 and not (a and b):
        raise HTTPException(400, "need two weeks to compare")
    a, b = a or ws[-2], b or ws[-1]
    if a not in ws or b not in ws:
        raise HTTPException(404, "unknown week")
    return evidence.compare(a, b)


@app.get("/api/inject/presets")
def inject_presets(week: str | None = None):
    week = week or store.weeks()[-1]
    if week not in store.weeks():
        raise HTTPException(404, "unknown week")
    return inject.presets(week)


@app.post("/api/inject")
def inject_item(body: dict):
    """Drop a new activity item into a week (state overlay; the agent picks it up on the next run of that week)."""
    try:
        return inject.inject(body)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))


class EntryEdit(BaseModel):
    by: str
    reason: str
    hours: float | None = None
    project: str | None = None


@app.post("/api/entries/{entry_id}/edit")
def edit_entry(entry_id: str, body: EntryEdit):
    return edits.edit_entry(entry_id, body.by, body.reason, body.hours, body.project)


@app.post("/api/entries/{entry_id}/remove")
def remove_entry(entry_id: str, body: EntryEdit):
    return edits.remove_entry(entry_id, body.by, body.reason)


@app.post("/api/invoices/{invoice_id}/paid")
def paid(invoice_id: str):
    ledger = store.load_ledger()
    inv = billing.mark_paid(ledger, invoice_id)
    store.save_ledger(ledger)
    return inv


@app.post("/api/reset")
def reset():
    if _run_lock.locked():
        raise HTTPException(409, "a run is in progress")
    store.reset()
    memory.reset()
    return {"ok": True}
