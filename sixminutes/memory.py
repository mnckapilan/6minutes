"""Cognee memory: fuzzy project context + the history of staffing/billing decisions.

Hard facts (rates, caps, assignments) live in firm.yaml and are enforced in code; memory never overrides them.
Two datasets: a persistent project-context graph, and a per-demo-run decisions dataset (so a reset starts clean).
Every write is mirrored to state/memory.jsonl so the UI can show what was remembered; if Cognee is unreachable or
its graph isn't ready yet, recall falls back to keyword search over that mirror (and says so).
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv

from . import store

load_dotenv(Path.home() / ".cognee" / ".env")

CONTEXT_DATASET = "sixminutes_context"
MIRROR = store.STATE / "memory.jsonl"
SESSION = store.STATE / "memory_session.json"

_served = False
_serve_lock = asyncio.Lock()


def enabled() -> bool:
    return bool(os.environ.get("COGNEE_BASE_URL") and os.environ.get("COGNEE_API_KEY")) and os.environ.get("COGNEE_DISABLED") != "1"


def decisions_dataset() -> str:
    if not SESSION.exists():
        SESSION.write_text(json.dumps({"decisions_dataset": f"sixminutes_decisions_{int(time.time())}"}))
    return json.loads(SESSION.read_text())["decisions_dataset"]


def reset() -> None:
    """New decisions dataset; keep the seeded project context."""
    SESSION.unlink(missing_ok=True)
    kept = [r for r in mirror() if r["kind"] == "context"]
    MIRROR.write_text("".join(json.dumps(r) + "\n" for r in kept))


async def _serve():
    global _served
    import cognee
    async with _serve_lock:
        if not _served:
            await cognee.serve(url=os.environ["COGNEE_BASE_URL"], api_key=os.environ["COGNEE_API_KEY"])
            _served = True
    return cognee


def _reconnect() -> None:
    global _served
    _served = False


def mirror() -> list[dict]:
    if not MIRROR.exists():
        return []
    return [json.loads(line) for line in MIRROR.read_text().splitlines() if line.strip()]


async def remember(text: str, kind: str = "decision") -> dict:
    dataset = CONTEXT_DATASET if kind == "context" else decisions_dataset()
    row = {"ts": store.now(), "kind": kind, "dataset": dataset, "text": text, "backend": "local"}
    if enabled():
        for attempt in range(2):
            try:
                cognee = await _serve()
                await asyncio.wait_for(cognee.remember(text, dataset_name=dataset), timeout=180)
                row["backend"], row["error"] = "cognee", None
                break
            except Exception as e:  # never block the demo on memory; reconnect once
                row["error"] = str(e)[:200]
                _reconnect()
    store.STATE.mkdir(exist_ok=True)
    with MIRROR.open("a") as f:
        f.write(json.dumps(row) + "\n")
    store.audit("memory.remember", kind=kind, text=text, backend=row["backend"], dataset=dataset)
    return row


def _local_recall(question: str) -> list[str]:
    words = {w for w in re.findall(r"[a-z0-9_.#]+", question.lower()) if len(w) > 2}
    scored = []
    for row in mirror():
        hits = len(words & set(re.findall(r"[a-z0-9_.#]+", row["text"].lower())))
        if hits:
            scored.append((hits, row["text"]))
    return [t for _, t in sorted(scored, reverse=True)[:4]]


async def recall(question: str) -> dict:
    out = {"question": question, "answers": [], "backend": "local"}
    if enabled():
        try:
            cognee = await _serve()
            res = await asyncio.wait_for(
                cognee.recall(query_text=question, datasets=[CONTEXT_DATASET, decisions_dataset()]), timeout=45)
            out["answers"] = [r.get("text") if isinstance(r, dict) else str(r) for r in res][:4]
            out["answers"] = [a for a in out["answers"] if a]
            out["backend"] = "cognee"
        except Exception as e:
            out["error"] = str(e)[:200]
    if not out["answers"]:
        # graph not built yet / Cognee unavailable: fall back to the mirror of what was remembered
        local = _local_recall(question)
        if local:
            out["answers"], out["backend"] = local, out["backend"] + "+mirror"
    store.audit("memory.recall", question=question, answers=out["answers"], backend=out["backend"],
                error=out.get("error"))
    return out
