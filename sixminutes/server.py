"""FastAPI app: one page, live agent steps over SSE, decisions, Stripe links."""

import asyncio
import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from . import agent, billing, decisions, memory, policy, store

app = FastAPI(title="6minutes")
_run_lock = asyncio.Lock()


@app.get("/")
def index():
    return FileResponse(store.ROOT / "static" / "index.html")


@app.get("/api/state")
def state():
    firm, ledger = store.load_firm(), store.load_ledger()
    projects = {k: {**p, "effective_policy": policy.effective_policy(firm, k)} for k, p in firm["projects"].items()}
    recovered = sum(e["amount"] for e in ledger["entries"] if e.get("decision_ref"))
    return {
        "firm": {**firm, "projects": projects}, "ledger": ledger, "audit": store.read_audit(400)[::-1],
        "memory": memory.mirror()[::-1], "rules": policy.RULES,
        "stripe": {"dry_run": billing.DRY_RUN}, "cognee": memory.enabled(), "model": agent.MODEL,
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
    if week not in ("W42", "W43"):
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
