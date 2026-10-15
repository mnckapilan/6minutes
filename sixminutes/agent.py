"""The agent: Claude in a manual tool-use loop. Claude picks the next step; tools enforce the policy."""

import inspect
import json
import os
from collections.abc import AsyncIterator

from anthropic import AsyncAnthropic

from . import policy, store
from .tools import HANDLERS, SCHEMAS, WeekContext, undisposed

MODEL = os.environ.get("SIXMIN_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("SIXMIN_EFFORT", "medium")
# Server-side refusal fallback + progress-note betas only work against the Anthropic API directly.
USE_BETAS = not os.environ.get("ANTHROPIC_BASE_URL") and os.environ.get("SIXMIN_BETAS", "1") == "1"
IS_CLAUDE = "claude" in MODEL
MAX_TURNS = 60

client = AsyncAnthropic()


def system_prompt(week: str) -> str:
    firm = store.load_firm()
    projects = []
    for key, p in firm["projects"].items():
        pol = policy.effective_policy(firm, key)
        projects.append({
            "key": key, "name": p["name"], "client": p.get("client"), "billable": pol["billable"],
            "po_cap_gbp": pol.get("po_cap_gbp"), "confidence_threshold": pol["confidence_threshold"],
            "match": p.get("match"), "non_billable_threads": p.get("non_billable_threads", []),
            "assignments": p.get("assignments", []),
        })
    rules = "\n".join(f"- {k} {v}" for k, v in policy.RULES.items())
    return f"""You are 6minutes, the billing agent for {firm['firm']['name']}, a consulting firm.
Each week you turn consultants' activity (emails, calls, Teams messages, document edits) into time entries and
Stripe invoices, following the firm's billing policy. Every pound you bill must trace back to activity, a rule and,
where relevant, a manager decision or memory.

Billing policy (enforced in code: tools reject proposals that break it and tell you which rule):
{rules}

Firm configuration (source of truth; memory never overrides it):
consultants: {json.dumps(firm['consultants'])}
projects: {json.dumps(projects, default=str)}

How to work through week {week}:
1. For each consultant, call list_activity. Every activity must end up with exactly one disposition: billed
   (propose_time_entry), skipped (mark_non_billable) or held (hold_for_review).
2. Bill client work on assigned projects with propose_time_entry, grouping one consultant's activities per project
   per day. Hours = sum of the activities' minutes / 60, rounded to 0.1h.
3. Internal-only work and threads a manager marked not billable: mark_non_billable (R3), citing the decision.
4. Ambiguous work, for example an internal-looking discussion on a client thread: hold_for_review (R6). Don't guess.
5. Client work on a project the consultant is NOT assigned to: hold_for_review with rule R7 and the project, then,
   if it is sustained, propose_assignment once with concrete evidence (calls, file edits, emails) and the first day.
6. Before deciding on unassigned or ambiguous work, call recall_memory (e.g. "Has Sam been approved to work on Acme
   Q3 Audit?", "Is thread thr-x billable?") and cite what it returns in memory_used.
7. When a consultant's entries are done, move on. After all consultants, call create_invoice once per client project
   that has billed entries this week (period = "{week}").
8. If a tool rejects a proposal, read the rule, fix it (or hold the work) and carry on.
Make independent tool calls in parallel. Keep text between tool calls to one short sentence.
Finish with a 3-5 line summary: billed per project, held items, staffing proposals, guardrails that fired."""


async def run_week(week: str) -> AsyncIterator[dict]:
    """Run the agent over one week, yielding UI events as it goes."""
    ctx = WeekContext(week)
    store.audit("run.start", week=week, model=MODEL)
    yield {"type": "start", "week": week, "model": MODEL}
    messages = [{"role": "user", "content": f"Process week {week}: timesheets for every consultant, then invoices."}]
    kwargs = dict(model=MODEL, max_tokens=16000, system=system_prompt(week), tools=SCHEMAS)
    if not IS_CLAUDE:  # other models through OpenRouter's Anthropic-compatible endpoint: plain tool use
        create = client.messages.create
    elif USE_BETAS:
        kwargs["output_config"] = {"effort": EFFORT}
        kwargs.update(thinking={"type": "adaptive", "display": "updates"}, fallbacks="default",
                      betas=["server-side-fallback-2026-07-01", "thinking-display-updates-2026-08-18"])
        create = client.beta.messages.create
    else:
        kwargs.update(thinking={"type": "adaptive"}, output_config={"effort": EFFORT})
        create = client.messages.create

    nudges = 0
    for _ in range(MAX_TURNS):
        resp = await create(messages=messages, **kwargs)
        messages.append({"role": "assistant", "content": resp.content})
        for block in resp.content:
            if block.type == "text" and block.text.strip():
                yield {"type": "text", "text": block.text}
            elif block.type == "thinking" and getattr(block, "thinking", ""):
                yield {"type": "note", "text": block.thinking}
        if resp.stop_reason == "refusal":
            yield {"type": "error", "text": "model declined the request"}
            break
        if resp.stop_reason == "pause_turn":
            continue
        if resp.stop_reason != "tool_use":
            missing = undisposed(ctx)
            if missing and nudges < 2:  # completeness check in code: every activity needs a disposition
                nudges += 1
                yield {"type": "check", "text": f"Completeness check: {len(missing)} activities have no disposition"}
                messages.append({"role": "user", "content": f"Completeness check failed: these activities have no "
                                 f"disposition yet: {missing}. Bill, skip or hold each one, then finish."})
                continue
            break
        results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            yield {"type": "tool_call", "id": block.id, "name": block.name, "input": block.input}
            handler = HANDLERS.get(block.name)
            try:
                if handler is None:
                    raise ValueError(f"unknown tool {block.name}")
                out = handler(ctx, **block.input)
                if inspect.isawaitable(out):
                    out = await out
                is_error = isinstance(out, dict) and out.get("accepted") in (False, "partial")
            except Exception as e:  # bad arguments etc: tell the model, don't crash the run
                out, is_error = {"error": f"{type(e).__name__}: {e}"}, True
            store.audit("tool", name=block.name, input=block.input, output=out)
            yield {"type": "tool_result", "id": block.id, "name": block.name, "output": out, "is_error": is_error}
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": json.dumps(out, default=str), "is_error": is_error})
        messages.append({"role": "user", "content": results})
    store.audit("run.end", week=week)
    yield {"type": "done", "week": week}
