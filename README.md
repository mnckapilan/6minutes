# 6minutes

An agent for consulting firms that turns consultants' email, calendar/Teams and document activity into timesheets and
**Stripe invoices**, follows each project's billing policy, and spots when real staffing has drifted from the plan.
Every pound billed traces back to the activity, the rule and the decision behind it.

> "We found 6 unbilled hours on Acme this week, from someone who wasn't even on the project."
> Revenue recovery, not admin saving. (0.1h, the billing unit, is 6 minutes.)

Built for the Briefcase × Stripe hackathon (Track 02: agents that collect, plus the Cognee memory side quest).

## What it does

1. **Run a week.** The agent reads each consultant's activity, matches it to projects, and bills client work at the
   assignment's rate: one invoice item per time entry, with the activity IDs, rule, decision and memory in `metadata`.
   Invoices are finalised and sent in Stripe test mode.
2. **Holds what it can't bill.** Low-confidence work (R6) and work on projects the consultant isn't assigned to (R7)
   is held, never guessed.
3. **Detects staffing drift.** Sustained unassigned work (hours across days) becomes a proposal:
   *"Sam (Analyst, assigned to Globex) spent ~6.4h on Acme Q3 Audit: 2 client calls, 8 edits to
   Acme_Model_v3.xlsx, 14 emails. Add Sam as Analyst at £120/h from 13 Oct?"* **[Approve] [Not billable] [Ignore]**
4. **Remembers decisions (Cognee).** Approving updates `firm.yaml`, writes the decision to Cognee and bills the held
   hours on a "recovered time" invoice. Next week, Sam's Acme work is billed automatically
   (*"2.2h · Acme Q3 Audit · Sam (Analyst) · assignment approved 15 Oct"*), and a thread marked not billable is
   skipped without asking.
5. **Calibrated judgment (JEV).** The agent's own confidence isn't trusted. Code re-scores every time entry with
   [JEV](https://openrouter.ai/typesafe), TypeSafe's "System One" decisions model (~0.3s, ~$0.00002 per call), and
   holds anything under the project's R6 threshold. JEV also gates staffing proposals (assign / not billable /
   needs info) and scores whether an ambiguous thread is really internal. Its probability goes on the line and into
   the Stripe metadata.
6. **Guardrails in code.** The PO cap stops billing and flags the overage. Hours can't exceed the evidence.
   Activity can't be billed twice. Client work can't be skipped without a manager decision.

## Billing policy (enforced in `sixminutes/policy.py`)

| Rule | |
|---|---|
| R1 Match | activity → project by domain, project code, channel, folder or file, limited to assigned projects |
| R2 Billable | client calls and project document work are billable at the assignment's rate |
| R3 Non-billable | internal-only work and threads marked not billable are skipped |
| R4 Rounding/caps | round to 0.1h; at most 10h per consultant per day; flag anything over the weekly allocation |
| R5 PO cap | stop billing at the project's cap and flag the overage |
| R6 Confidence | below threshold → hold for review, never bill |
| R7 Unassigned | never billed; held, with a staffing proposal for a manager |

Policies stack: firm default → client override → project override (`firm.yaml`).

## Architecture

```
data/activity/W4x.json   mock email / call / Teams / doc-edit feed (scripts/gen_activity.py)
firm.yaml                clients, projects, policies, assignments: the hard facts
sixminutes/
  agent.py               manual tool-use loop (Anthropic SDK; Claude or any model via OpenRouter's
                         Anthropic-compatible endpoint) + a code completeness check
  tools.py               list_activity, recall_memory, propose_time_entry, hold_for_review,
                         mark_non_billable, propose_assignment, create_invoice
  policy.py              R1-R7
  billing.py             Stripe: customers, invoice items with evidence metadata, finalise/send, credit notes
  jev.py                 JEV decisions model via OpenRouter (/api/alpha/decisions): R6 scores, staffing gate
  memory.py              Cognee Cloud remember/recall (project context graph + decisions), local mirror fallback
  decisions.py           manager decisions, project-board assignments
  edits.py               consultant edits: write-downs (credit notes), project moves (policy re-check)
  server.py + static/    FastAPI + one page: Billing, Activity feed, Projects board, People
state/                   working copy of firm.yaml, ledger.json, audit.jsonl, memory mirror (gitignored)
```

System 2 (the LLM agent) plans the week; System 1 (JEV) makes fast, calibrated calls on each item; code enforces
the policy; a manager approves anything one-way. In production JEV would triage every activity for fractions of a
cent and the LLM would only see the exceptions.

The agent chooses the next step. The tools enforce the policy and reject anything that breaks it, quoting the rule.
Every tool call, rejection, decision and memory read/write lands in `state/audit.jsonl`.

## Run it

```bash
cp .env.example .env          # Stripe test key, Anthropic or OpenRouter key, Cognee Cloud URL/key
uv sync
uv run python -m scripts.seed_memory          # once: seed the Cognee project-context graph
uv run uvicorn sixminutes.server:app --port 8066
```

Open http://localhost:8066 → **Run W42** → decide on the held items → **Run W43**.

Out of scope: real email/Teams/docs integrations, auth, deployment. Those are the next steps.
