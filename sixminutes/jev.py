"""JEV (TypeSafe's "System One" decisions model) via OpenRouter: fast, calibrated judgments with typed answers.

The agent (System 2) plans; JEV gives an independent probability for each judgment call; code applies the policy
thresholds. If JEV is unreachable, callers get None and treat the item as low-confidence (hold, never guess).
"""

import json
import os

import httpx

from . import store

URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = os.environ.get("SIXMIN_JEV_MODEL", "typesafe/jev-1.14")


def enabled() -> bool:
    return bool(_key()) and os.environ.get("SIXMIN_JEV", "1") == "1"


def _key() -> str:
    return os.environ.get("OPENROUTER_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")


def decide(purpose: str, state, questions: dict) -> dict | None:
    if not enabled():
        return None
    body = {"model": MODEL, "state": state if isinstance(state, str) else json.dumps(state, default=str),
            "questions": questions}
    try:
        r = httpx.post(URL, json=body, timeout=8, headers={"Authorization": f"Bearer {_key()}"})
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        store.audit("jev.error", purpose=purpose, error=str(e)[:200])
        return None
    out = {"model": data.get("model"), "id": data.get("id"), "answers": data.get("answers", {})}
    store.audit("jev", purpose=purpose, model=out["model"], answers=out["answers"],
                cost=(data.get("usage") or {}).get("cost"))
    return out


def _project_brief(firm: dict, key: str) -> dict:
    p = firm["projects"][key]
    client = firm["clients"].get(p.get("client") or "", {})
    return {"project": p["name"], "client": client.get("name"), "client_domains": client.get("domains"),
            "channels": p["match"].get("channels"), "folders": p["match"].get("folders"),
            "key_files": p["match"].get("files")}


def _acts(acts: list[dict]) -> list[dict]:
    keep = ("kind", "title", "minutes", "participants", "channel", "path", "thread_id", "note", "edits", "messages")
    return [{k: a[k] for k in keep if a.get(k)} for a in acts]


def entry_confidence(firm: dict, consultant: str, project: str, acts: list[dict]) -> dict | None:
    """R6: probability that these activities are billable client work on this project."""
    state = {"consultant": firm["consultants"][consultant]["name"], "firm": firm["firm"]["name"],
             "firm_domain": firm["firm"]["domain"], "candidate_project": _project_brief(firm, project),
             "activities": _acts(acts)}
    res = decide("entry_confidence", state, {"billable": {
        "type": "noul",
        "instructions": f"These activities are hands-on client work for {state['candidate_project']['client']} on "
                        f"the project '{firm['projects'][project]['name']}', which the client should be billed for "
                        "(not internal firm business, and belonging to this project rather than another).",
    }})
    if not res or "billable" not in res["answers"]:
        return None
    return {"p": round(res["answers"]["billable"]["noul"], 3), "model": res["model"], "id": res["id"]}


def staffing_judgment(firm: dict, consultant: str, project: str, evidence: dict, acts: list[dict]) -> dict | None:
    cons = firm["consultants"][consultant]
    current = [p["name"] for k, p in firm["projects"].items() if k != "INTERNAL"
               and any(a["consultant"] == consultant for a in p["assignments"])]
    state = {"consultant": f"{cons['name']} ({cons['title']})", "currently_assigned_to": current,
             "project": _project_brief(firm, project), "evidence": {k: v for k, v in evidence.items()
                                                                   if k != "activity_ids"},
             "activities": _acts(acts)}
    res = decide("staffing", state, {
        "staffing": {"type": "choice", "instructions": "What should the billing manager do about this work?",
                     "criteria": {"assign": f"{cons['name']} is materially working on this project; add them to it",
                                  "not_billable": "Incidental help or internal work; do not bill the client",
                                  "needs_info": "Not enough evidence to decide"}},
        "sustained": {"type": "noul", "instructions": f"{cons['name']} is doing sustained, hands-on work on this "
                                                      "project (not just being cc'd)."},
    })
    if not res or "staffing" not in res["answers"]:
        return None
    a = res["answers"]
    return {"choice": a["staffing"]["choice"], "p_assign": a["staffing"]["probabilities"].get("assign"),
            "probabilities": a["staffing"]["probabilities"], "confidence": a["staffing"].get("confidence"),
            "sustained": a.get("sustained", {}).get("noul"), "model": res["model"], "id": res["id"]}


def internal_probability(firm: dict, project: str, acts: list[dict]) -> dict | None:
    state = {"firm": firm["firm"]["name"], "firm_domain": firm["firm"]["domain"],
             "client_project": _project_brief(firm, project), "activities": _acts(acts)}
    res = decide("internal_check", state, {"internal": {
        "type": "noul",
        "instructions": "These activities are internal firm business (staffing, utilisation, process) rather than "
                        "work delivered for the client, even though the client is mentioned or cc'd.",
    }})
    if not res or "internal" not in res["answers"]:
        return None
    return {"p": round(res["answers"]["internal"]["noul"], 3), "model": res["model"], "id": res["id"]}
