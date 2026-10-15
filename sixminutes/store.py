"""Persistent state: firm config (state/firm.yaml), ledger (state/ledger.json) and audit trail (state/audit.jsonl)."""

import json
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=True)

STATE = Path(os.environ.get("SIXMIN_STATE_DIR") or ROOT / "state")  # override to run a throwaway copy
FIRM_BASE = ROOT / "firm.yaml"
FIRM = STATE / "firm.yaml"
LEDGER = STATE / "ledger.json"
AUDIT = STATE / "audit.jsonl"
INJECTED = STATE / "injected.json"   # live-demo overlay: activity dropped in mid-demo (cleared by reset)
ACTIVITY = ROOT / "data" / "activity"

_lock = threading.RLock()

EMPTY_LEDGER = {
    "entries": [],      # billable time entries
    "skipped": [],      # non-billable dispositions (R3)
    "holds": [],        # held for review (R6/R7) and PO-cap overages (R5)
    "proposals": [],    # staffing proposals
    "decisions": [],    # manager decisions
    "invoices": [],     # Stripe invoices
    "customers": {},    # client key -> Stripe customer id
    "seq": {},
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def reset() -> None:
    with _lock:
        STATE.mkdir(exist_ok=True)
        customers = load_ledger().get("customers", {}) if LEDGER.exists() else {}
        shutil.copy(FIRM_BASE, FIRM)
        save_ledger({**json.loads(json.dumps(EMPTY_LEDGER)), "customers": customers})
        AUDIT.write_text("")
        INJECTED.unlink(missing_ok=True)


def ensure() -> None:
    if not FIRM.exists() or not LEDGER.exists():
        reset()


def load_firm() -> dict:
    ensure()
    return yaml.safe_load(FIRM.read_text())


def save_firm(firm: dict) -> None:
    with _lock:
        FIRM.write_text(yaml.safe_dump(firm, sort_keys=False, allow_unicode=True))


def load_ledger() -> dict:
    return json.loads(LEDGER.read_text())


def save_ledger(ledger: dict) -> None:
    with _lock:
        tmp = LEDGER.with_suffix(".tmp")
        tmp.write_text(json.dumps(ledger, indent=1, default=str))
        tmp.replace(LEDGER)


def next_id(ledger: dict, prefix: str) -> str:
    n = ledger["seq"].get(prefix, 0) + 1
    ledger["seq"][prefix] = n
    return f"{prefix}-{n:03d}"


def audit(event: str, **fields) -> dict:
    row = {"ts": now(), "event": event, **fields}
    with _lock:
        with AUDIT.open("a") as f:
            f.write(json.dumps(row, default=str) + "\n")
    return row


def read_audit(limit: int = 500) -> list[dict]:
    ensure()
    lines = AUDIT.read_text().splitlines()
    return [json.loads(line) for line in lines[-limit:] if line.strip()]


def weeks() -> list[str]:
    """The weeks that have an activity feed (data/activity/W*.json), oldest first. The one place weeks are defined."""
    return sorted(p.stem for p in ACTIVITY.glob("W*.json"))


def load_injected() -> list[dict]:
    if not INJECTED.exists():
        return []
    try:
        return json.loads(INJECTED.read_text())
    except ValueError:
        return []


def save_injected(items: list[dict]) -> None:
    with _lock:
        STATE.mkdir(exist_ok=True)
        INJECTED.write_text(json.dumps(items, indent=1))


def load_activity(week: str) -> list[dict]:
    """The week's feed: data/activity/<week>.json plus anything injected live (state/injected.json)."""
    base = json.loads((ACTIVITY / f"{week}.json").read_text())
    return base + [a for a in load_injected() if a["id"].startswith(week + "-")]
