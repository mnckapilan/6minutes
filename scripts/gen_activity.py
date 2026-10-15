"""Generate the mock activity feed for W42 and W43 (deterministic).

Planted scenarios:
- Sam (assigned to Globex only) works ~6h on Acme in W42 and ~8h in W43  -> staffing drift
- An Acme "ops review" thread that is really internal (one Acme cc)       -> held, then marked not billable
- Enough Priya Acme work in W43 to hit the PO-4471 cap                     -> R5 guardrail
- Internal work (#general, /Internal, firm-only emails)                    -> R3 skip
"""

import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "activity"
WEEKS = {"W42": date(2026, 10, 12), "W43": date(2026, 10, 19)}

PRIYA, SAM = "priya@northbeam.co", "sam@northbeam.co"
ACME_PEOPLE = ["jane.doe@acme.com", "raj.patel@acme.com", "cfo@acme.com"]
GLX_PEOPLE = ["m.chen@globex.com", "olu.ade@globex.com", "strategy@globex.com"]


class Feed:
    def __init__(self, week: str, who: str, prefix: str):
        self.week, self.who, self.prefix = week, who, prefix
        self.items: list[dict] = []

    def add(self, day: int, hhmm: str, kind: str, title: str, minutes: int = 0, **kw):
        start = WEEKS[self.week] + timedelta(days=day)
        h, m = map(int, hhmm.split(":"))
        item = {
            "id": f"{self.week}-{self.prefix}-{len(self.items) + 1:03d}",
            "consultant": self.who,
            "ts": datetime(start.year, start.month, start.day, h, m).isoformat(),
            "kind": kind,
            "title": title,
        }
        if minutes:
            item["minutes"] = minutes
        item.update(kw)
        self.items.append(item)


def priya(week: str, rng: random.Random) -> list[dict]:
    f = Feed(week, "priya", "p")
    heavy = week == "W43"  # W43: fieldwork week, pushes Acme over its PO cap
    for d in range(5):
        f.add(d, "09:00", "call", "Acme Q3 Audit: daily stand-up with Acme finance", 30 if not heavy else 45,
              participants=[PRIYA, *ACME_PEOPLE[:2]])
        f.add(d, "10:00", "doc_edit", "Edited Acme_Audit_Findings.docx", rng.choice([90, 120, 150]) + (60 if heavy else 0),
              path="/Clients/Acme/Q3/Acme_Audit_Findings.docx", edits=rng.randint(6, 14))
        f.add(d, "14:00", "doc_edit", "Reviewed Acme_Model_v3.xlsx", rng.choice([60, 90]) + (30 if heavy else 0),
              path="/Clients/Acme/Q3/Acme_Model_v3.xlsx", edits=rng.randint(3, 8))
    f.add(0, "16:00", "email", "RE: [ACQ3] Revenue recognition sample selection", participants=[PRIYA, "cfo@acme.com"],
          thread_id=f"thr-acme-revrec")
    f.add(1, "16:30", "call", "Acme Q3 Audit: controls walkthrough", 90, participants=[PRIYA, *ACME_PEOPLE])
    f.add(2, "11:30", "teams", "#acme-q3: inventory count logistics", channel="#acme-q3", messages=6)
    f.add(3, "15:30", "call", "Acme Q3 Audit: findings preview with CFO", 60, participants=[PRIYA, "cfo@acme.com", SAM])
    f.add(4, "12:00", "email", "RE: [ACQ3] Open items list", participants=[PRIYA, "raj.patel@acme.com"], thread_id="thr-acme-open")
    if heavy:
        f.add(1, "18:00", "doc_edit", "Edited Acme_Audit_Findings.docx (evening)", 120,
              path="/Clients/Acme/Q3/Acme_Audit_Findings.docx", edits=11)
        f.add(3, "18:00", "doc_edit", "Reviewed Acme_Model_v3.xlsx (evening)", 90,
              path="/Clients/Acme/Q3/Acme_Model_v3.xlsx", edits=7)
    # The ambiguous one: an Acme-tagged thread that is really internal firm process talk.
    ops_title = "Acme ops review: staffing plan, utilisation and internal process" if week == "W42" \
        else "RE: Acme ops review: staffing plan, utilisation and internal process"
    for d, t in [(1, "17:00"), (2, "17:15"), (3, "17:30")] + ([(4, "16:00")] if week == "W42" else []):
        f.add(d, t, "email", ops_title, participants=[PRIYA, "tom.ward@northbeam.co", "ops@acme.com"],
              thread_id="thr-acme-ops-review", note="internal resourcing discussion; Acme ops alias cc'd")
    f.add(4, "14:30", "call", "Acme ops review sync (internal)", 30,
          participants=[PRIYA, "tom.ward@northbeam.co", "ops@acme.com"], thread_id="thr-acme-ops-review")
    # Internal
    f.add(0, "12:30", "teams", "#general: Friday social planning", channel="#general", messages=4)
    f.add(2, "13:00", "call", "Northbeam managers' meeting", 60, participants=[PRIYA, "tom.ward@northbeam.co", "ceo@northbeam.co"])
    f.add(4, "17:00", "doc_edit", "Updated /Internal/Utilisation_Q4.xlsx", 30, path="/Internal/Utilisation_Q4.xlsx", edits=3)
    return f.items


def sam(week: str, rng: random.Random) -> list[dict]:
    f = Feed(week, "sam", "s")
    for d in range(5):
        f.add(d, "09:30", "doc_edit", "Edited Globex_Market_Sizing.pptx", rng.choice([150, 180, 210]),
              path="/Clients/Globex/Strategy/Globex_Market_Sizing.pptx", edits=rng.randint(8, 20))
        f.add(d, "13:30", "teams", "#globex-strategy: research updates", channel="#globex-strategy", messages=rng.randint(5, 9))
    f.add(0, "11:00", "call", "Globex Strategy: kickoff of market sizing sprint", 60, participants=[SAM, *GLX_PEOPLE[:2]])
    f.add(1, "11:00", "call", "Globex Strategy: expert interview", 45, participants=[SAM, "olu.ade@globex.com"])
    f.add(2, "10:30", "doc_edit", "Edited Globex_Interviews.docx", 90, path="/Clients/Globex/Strategy/Globex_Interviews.docx", edits=9)
    f.add(3, "11:00", "call", "Globex Strategy: steerco prep", 60, participants=[SAM, *GLX_PEOPLE])
    f.add(4, "10:00", "email", "RE: [GLXS] Draft sizing for review", participants=[SAM, "m.chen@globex.com"], thread_id="thr-glx-sizing")

    # Staffing drift: Sam helps on Acme without being assigned.
    if week == "W42":
        # 2 client calls, 9 edits to the model, 14 emails on the Acme thread  (~6h)
        f.add(1, "15:00", "call", "Acme Q3 Audit: model walkthrough with Acme FP&A", 60, participants=[SAM, PRIYA, "jane.doe@acme.com"])
        f.add(3, "15:30", "call", "Acme Q3 Audit: findings preview with CFO", 60, participants=[SAM, PRIYA, "cfo@acme.com"])
        for d, mins in [(1, 40), (2, 45), (2, 30), (3, 35), (4, 30)]:
            f.add(d, "16:30", "doc_edit", "Edited Acme_Model_v3.xlsx", mins, path="/Clients/Acme/Q3/Acme_Model_v3.xlsx",
                  edits={40: 2, 45: 2, 30: 1, 35: 2}[mins])
        for i in range(14):
            d = 1 + i % 4
            f.add(d, f"{17 + i % 2}:{(i * 7) % 60:02d}", "email", "RE: [ACQ3] Model assumptions: revenue bridge",
                  participants=[SAM, PRIYA, "jane.doe@acme.com"], thread_id="thr-acme-model")
    else:
        f.add(0, "15:00", "call", "Acme Q3 Audit: revenue bridge review", 60, participants=[SAM, PRIYA, "jane.doe@acme.com"])
        f.add(2, "15:00", "call", "Acme Q3 Audit: sensitivity analysis with FP&A", 45, participants=[SAM, "jane.doe@acme.com", "raj.patel@acme.com"])
        for d, mins in [(0, 60), (1, 75), (2, 45), (3, 90), (4, 60)]:
            f.add(d, "16:00", "doc_edit", "Edited Acme_Model_v3.xlsx", mins, path="/Clients/Acme/Q3/Acme_Model_v3.xlsx", edits=3)
        for i in range(10):
            f.add(i % 5, f"17:{(i * 5) % 60:02d}", "email", "RE: [ACQ3] Model assumptions: revenue bridge",
                  participants=[SAM, "jane.doe@acme.com"], thread_id="thr-acme-model")
    # Internal
    f.add(2, "12:30", "call", "Analyst training: Excel modelling", 60, participants=[SAM, "training@northbeam.co"])
    f.add(4, "16:00", "teams", "#general: Friday social planning", channel="#general", messages=3)
    return f.items


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for week in WEEKS:
        rng = random.Random(f"6minutes-{week}")
        items = sorted(priya(week, rng) + sam(week, rng), key=lambda a: (a["consultant"], a["ts"]))
        (OUT / f"{week}.json").write_text(json.dumps(items, indent=1))
        print(week, len(items), "activities")


if __name__ == "__main__":
    main()
