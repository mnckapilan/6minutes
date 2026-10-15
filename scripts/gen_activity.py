"""Generate the mock activity feed for W42 and W43 (deterministic).

Planted scenarios:
- Sam (assigned to Globex only) works ~6h on Acme in W42 and ~8h in W43  -> staffing drift
- An Acme "ops review" thread that is really internal (one Acme cc)       -> held, then marked not billable
- Enough Hannah Acme work in W43 to hit the PO-4471 cap                     -> R5 guardrail
- Internal work (#general, /Internal, firm-only emails)                    -> R3 skip
- Chloe (W43): a Kestrel email that is really business development, no project
  reference; Kestrel's 0.8 project threshold + JEV                          -> R6 hold
- Everyone else (Maya, Daniel, Tariq, Jordan, Chloe, Elena) has routine, correctly assigned
  work on Brightwater / Kestrel / Lumen / Globex plus some internal time. Brightwater Grid has a
  tight PO cap (£2,800) that W42+W43 approach but do not reach.
"""

import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "activity"
WEEKS = {"W42": date(2026, 10, 12), "W43": date(2026, 10, 19)}

HANNAH, SAM = "hannah@northbeam.co", "sam@northbeam.co"
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


def hannah(week: str, rng: random.Random) -> list[dict]:
    f = Feed(week, "hannah", "p")
    heavy = week == "W43"  # W43: fieldwork week, pushes Acme over its PO cap
    for d in range(5):
        f.add(d, "09:00", "call", "Acme Q3 Audit: daily stand-up with Acme finance", 30 if not heavy else 45,
              participants=[HANNAH, *ACME_PEOPLE[:2]])
        f.add(d, "10:00", "doc_edit", "Edited Acme_Audit_Findings.docx", rng.choice([90, 120, 150]) + (60 if heavy else 0),
              path="/Clients/Acme/Q3/Acme_Audit_Findings.docx", edits=rng.randint(6, 14))
        f.add(d, "14:00", "doc_edit", "Reviewed Acme_Model_v3.xlsx", rng.choice([60, 90]) + (30 if heavy else 0),
              path="/Clients/Acme/Q3/Acme_Model_v3.xlsx", edits=rng.randint(3, 8))
    f.add(0, "16:00", "email", "RE: [ACQ3] Revenue recognition sample selection", participants=[HANNAH, "cfo@acme.com"],
          thread_id=f"thr-acme-revrec")
    f.add(1, "16:30", "call", "Acme Q3 Audit: controls walkthrough", 90, participants=[HANNAH, *ACME_PEOPLE])
    f.add(2, "11:30", "teams", "#acme-q3: inventory count logistics", channel="#acme-q3", messages=6)
    f.add(3, "15:30", "call", "Acme Q3 Audit: findings preview with CFO", 60, participants=[HANNAH, "cfo@acme.com", SAM])
    f.add(4, "12:00", "email", "RE: [ACQ3] Open items list", participants=[HANNAH, "raj.patel@acme.com"], thread_id="thr-acme-open")
    if heavy:
        f.add(1, "18:00", "doc_edit", "Edited Acme_Audit_Findings.docx (evening)", 120,
              path="/Clients/Acme/Q3/Acme_Audit_Findings.docx", edits=11)
        f.add(3, "18:00", "doc_edit", "Reviewed Acme_Model_v3.xlsx (evening)", 90,
              path="/Clients/Acme/Q3/Acme_Model_v3.xlsx", edits=7)
    # The ambiguous one: an Acme-tagged thread that is really internal firm process talk.
    ops_title = "Acme ops review: staffing plan, utilisation and internal process" if week == "W42" \
        else "RE: Acme ops review: staffing plan, utilisation and internal process"
    for d, t in [(1, "17:00"), (2, "17:15"), (3, "17:30")] + ([(4, "16:00")] if week == "W42" else []):
        f.add(d, t, "email", ops_title, participants=[HANNAH, "tom.ward@northbeam.co", "ops@acme.com"],
              thread_id="thr-acme-ops-review", note="internal resourcing discussion; Acme ops alias cc'd")
    f.add(4, "14:30", "call", "Acme ops review sync (internal)", 30,
          participants=[HANNAH, "tom.ward@northbeam.co", "ops@acme.com"], thread_id="thr-acme-ops-review")
    # Internal
    f.add(0, "12:30", "teams", "#general: Friday social planning", channel="#general", messages=4)
    f.add(2, "13:00", "call", "Northbeam managers' meeting", 60, participants=[HANNAH, "tom.ward@northbeam.co", "ceo@northbeam.co"])
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
        f.add(1, "15:00", "call", "Acme Q3 Audit: model walkthrough with Acme FP&A", 60, participants=[SAM, HANNAH, "jane.doe@acme.com"])
        f.add(3, "15:30", "call", "Acme Q3 Audit: findings preview with CFO", 60, participants=[SAM, HANNAH, "cfo@acme.com"])
        for d, mins in [(1, 40), (2, 45), (2, 30), (3, 35), (4, 30)]:
            f.add(d, "16:30", "doc_edit", "Edited Acme_Model_v3.xlsx", mins, path="/Clients/Acme/Q3/Acme_Model_v3.xlsx",
                  edits={40: 2, 45: 2, 30: 1, 35: 2}[mins])
        for i in range(14):
            d = 1 + i % 4
            f.add(d, f"{17 + i % 2}:{(i * 7) % 60:02d}", "email", "RE: [ACQ3] Model assumptions: revenue bridge",
                  participants=[SAM, HANNAH, "jane.doe@acme.com"], thread_id="thr-acme-model")
    else:
        f.add(0, "15:00", "call", "Acme Q3 Audit: revenue bridge review", 60, participants=[SAM, HANNAH, "jane.doe@acme.com"])
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


# ---- the rest of the firm: routine, correctly assigned work (fewer, meatier items) ----

BW_REG = dict(who=["a.nolan@brightwater.com", "h.zhou@brightwater.com"], ch="#bw-regulatory", code="BWRS",
              folder="/Clients/Brightwater/Regulatory")
BW_GRID = dict(who=["t.okoye@bwenergy.com", "grid.ops@bwenergy.com"], ch="#bw-grid", code="BWGR",
               folder="/Clients/Brightwater/Grid")
KES = dict(who=["l.fontaine@kestrelpharma.com", "supplychain@kestrelpharma.com"], ch="#kestrel-trial", code="KTSO",
           folder="/Clients/Kestrel/TrialSupply")
LUM = dict(who=["p.ibarra@lumenretail.com", "cx@lumenretail.com"], ch="#lumen-cx", code="LMCX",
           folder="/Clients/Lumen/CX")
TOM = "tom.ward@northbeam.co"


def email_of(who: str) -> str:
    return f"{who}@northbeam.co"


def daniel(week: str) -> list[dict]:
    f, me, w43 = Feed(week, "daniel", "d"), email_of("daniel"), week == "W43"
    f.add(0, "09:30", "doc_edit", "Edited Brightwater_Submission_Draft.docx", 180 if not w43 else 150,
          path=f"{BW_REG['folder']}/Brightwater_Submission_Draft.docx", edits=12)
    f.add(1, "10:00", "call", "Brightwater Regulatory: evidence workshop with compliance", 90,
          participants=[me, "maya@northbeam.co", *BW_REG["who"]])
    f.add(2, "09:30", "doc_edit", "Edited Grid_Resilience_Model.xlsx", 150 if not w43 else 120,
          path=f"{BW_GRID['folder']}/Grid_Resilience_Model.xlsx", edits=9)
    f.add(2, "15:00", "teams", "#bw-regulatory: evidence tracker questions", channel=BW_REG["ch"], messages=7)
    f.add(3, "14:00", "call", "Brightwater Grid: outage data review", 60 if not w43 else 45, participants=[me, *BW_GRID["who"]])
    f.add(3, "16:30", "email", f"RE: [{BW_GRID['code']}] Substation outage dataset", participants=[me, BW_GRID["who"][0]],
          thread_id="thr-bwgrid-outage")
    f.add(4, "10:00", "doc_edit", "Updated Brightwater_Evidence_Tracker.xlsx", 120,
          path=f"{BW_REG['folder']}/Brightwater_Evidence_Tracker.xlsx", edits=6)
    f.add(4, "15:00", "call", "Consultant career development catch-up", 30, participants=[me, TOM])
    return f.items


def tariq(week: str) -> list[dict]:
    f, me, w43 = Feed(week, "tariq", "t"), email_of("tariq"), week == "W43"
    f.add(0, "09:00", "doc_edit", "Updated Brightwater_Evidence_Tracker.xlsx", 210,
          path=f"{BW_REG['folder']}/Brightwater_Evidence_Tracker.xlsx", edits=15)
    f.add(1, "11:00", "call", "Brightwater Regulatory: data room walkthrough", 60, participants=[me, "daniel@northbeam.co", BW_REG["who"][1]])
    f.add(2, "09:00", "doc_edit", "Edited Brightwater_Submission_Draft.docx", 180,
          path=f"{BW_REG['folder']}/Brightwater_Submission_Draft.docx", edits=10)
    f.add(2, "14:00", "call", "Analyst training: regulatory reporting basics", 60, participants=[me, "training@northbeam.co"])
    f.add(3, "09:30", "doc_edit", "Edited Lumen_CX_Survey_Analysis.xlsx", 150 if not w43 else 180,
          path=f"{LUM['folder']}/Lumen_CX_Survey_Analysis.xlsx", edits=11)
    f.add(3, "16:00", "teams", "#bw-regulatory: tracker status", channel=BW_REG["ch"], messages=8)
    f.add(4, "11:00", "call", "Lumen CX: survey readout prep", 45, participants=[me, LUM["who"][0]])
    f.add(4, "16:30", "teams", "#general: Friday social planning", channel="#general", messages=3)
    return f.items


def maya(week: str) -> list[dict]:
    f, me, w43 = Feed(week, "maya", "m"), email_of("maya"), week == "W43"
    f.add(0, "10:00", "call", "Brightwater Regulatory: weekly status with compliance", 60, participants=[me, *BW_REG["who"]])
    f.add(1, "14:00", "call", "Globex Strategy: sizing review with Globex strategy team", 60,
          participants=[me, "sam@northbeam.co", *GLX_PEOPLE[:2]])
    f.add(1, "16:30", "email", f"RE: [{BW_REG['code']}] Submission timetable", participants=[me, BW_REG["who"][0]],
          thread_id="thr-bwreg-timetable")
    f.add(2, "09:30", "doc_edit", "Reviewed Brightwater_Submission_Draft.docx", 90 if not w43 else 120,
          path=f"{BW_REG['folder']}/Brightwater_Submission_Draft.docx", edits=5)
    f.add(2, "13:00", "call", "Northbeam managers' meeting", 60, participants=[me, "hannah@northbeam.co", TOM, "ceo@northbeam.co"])
    f.add(3, "10:00", "call", "Brightwater Regulatory: submission readiness checkpoint", 75, participants=[me, "daniel@northbeam.co", *BW_REG["who"]])
    f.add(3, "15:00", "doc_edit", "Reviewed Globex_Interviews.docx", 60,
          path="/Clients/Globex/Strategy/Globex_Interviews.docx", edits=4)
    f.add(4, "11:00", "teams", "#bw-regulatory: submission checklist", channel=BW_REG["ch"], messages=6)
    return f.items


def jordan(week: str) -> list[dict]:
    f, me, w43 = Feed(week, "jordan", "j"), email_of("jordan"), week == "W43"
    f.add(1, "11:00", "doc_edit", "Reviewed Grid_Steerco_Pack.pptx", 60,
          path=f"{BW_GRID['folder']}/Grid_Steerco_Pack.pptx", edits=4)
    f.add(2, "10:00", "call", "Brightwater Grid: steering committee", 60, participants=[me, "daniel@northbeam.co", *BW_GRID["who"]])
    f.add(2, "13:00", "call", "Northbeam managers' meeting", 60, participants=[me, "hannah@northbeam.co", TOM, "ceo@northbeam.co"])
    f.add(3, "15:00", "call", "Kestrel Trial Supply: sponsor check-in", 45, participants=[me, "chloe@northbeam.co", KES["who"][0]])
    f.add(3, "17:00", "email", f"RE: [{BW_GRID['code']}] Steerco follow-ups", participants=[me, BW_GRID["who"][0]],
          thread_id="thr-bwgrid-steerco")
    f.add(4, "09:30", "call", "Partner pipeline review", 45, participants=[me, "ceo@northbeam.co", TOM])
    return f.items


def chloe(week: str) -> list[dict]:
    f, me, w43 = Feed(week, "chloe", "c"), email_of("chloe"), week == "W43"
    f.add(0, "09:00", "doc_edit", "Edited Kestrel_Supply_Forecast.xlsx", 180,
          path=f"{KES['folder']}/Kestrel_Supply_Forecast.xlsx", edits=14)
    f.add(1, "10:00", "call", "Kestrel Trial Supply: depot constraints workshop", 90, participants=[me, "elena@northbeam.co", *KES["who"]])
    f.add(1, "14:00", "doc_edit", "Edited Kestrel_Site_Playbook.docx", 120,
          path=f"{KES['folder']}/Kestrel_Site_Playbook.docx", edits=8)
    f.add(2, "11:00", "call", "Lumen CX: journey mapping session", 60, participants=[me, "tariq@northbeam.co", *LUM["who"]])
    f.add(2, "16:00", "email", f"RE: [{KES['code']}] Depot capacity assumptions", participants=[me, KES["who"][0]],
          thread_id="thr-kes-depot")
    f.add(3, "09:30", "doc_edit", "Edited Kestrel_Supply_Forecast.xlsx", 150,
          path=f"{KES['folder']}/Kestrel_Supply_Forecast.xlsx", edits=10)
    f.add(3, "15:00", "call", "Kestrel Trial Supply: forecast review with supply chain", 45 if not w43 else 60, participants=[me, *KES["who"]])
    f.add(4, "11:00", "teams", "#kestrel-trial: site list updates", channel=KES["ch"], messages=6)
    f.add(4, "14:00", "call", "Consultant community of practice", 45, participants=[me, "daniel@northbeam.co", TOM])
    if w43:
        # R6 scenario: client-domain email with no project reference; really business development.
        f.add(2, "17:30", "email", "Quick catch-up: ideas for Kestrel's commercial team", participants=[me, "m.ostrander@kestrelpharma.com"],
              thread_id="thr-kes-followon",
              note="no project code or file; sounds like pitching new work to a different Kestrel team")
    return f.items


def elena(week: str) -> list[dict]:
    f, me, w43 = Feed(week, "elena", "e"), email_of("elena"), week == "W43"
    f.add(0, "09:30", "doc_edit", "Edited Kestrel_Supply_Forecast.xlsx", 210,
          path=f"{KES['folder']}/Kestrel_Supply_Forecast.xlsx", edits=17)
    f.add(1, "10:00", "call", "Kestrel Trial Supply: depot constraints workshop", 90, participants=[me, "chloe@northbeam.co", *KES["who"]])
    f.add(2, "09:30", "doc_edit", "Edited Kestrel_Site_Playbook.docx", 180 if not w43 else 150,
          path=f"{KES['folder']}/Kestrel_Site_Playbook.docx", edits=9)
    f.add(2, "14:00", "call", "Analyst training: regulatory reporting basics", 60, participants=[me, "training@northbeam.co"])
    f.add(3, "10:00", "teams", "#kestrel-trial: data requests", channel=KES["ch"], messages=9)
    f.add(3, "14:30", "doc_edit", "Edited Kestrel_Supply_Forecast.xlsx", 120,
          path=f"{KES['folder']}/Kestrel_Supply_Forecast.xlsx", edits=7)
    f.add(4, "10:30", "email", f"RE: [{KES['code']}] Site list corrections", participants=[me, KES["who"][1]],
          thread_id="thr-kes-sites")
    f.add(4, "16:00", "teams", "#general: Friday social planning", channel="#general", messages=4)
    return f.items


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for week in WEEKS:
        rng = random.Random(f"6minutes-{week}")
        others = daniel(week) + tariq(week) + maya(week) + jordan(week) + chloe(week) + elena(week)
        items = sorted(hannah(week, rng) + sam(week, rng) + others, key=lambda a: (a["consultant"], a["ts"]))
        (OUT / f"{week}.json").write_text(json.dumps(items, indent=1))
        print(week, len(items), "activities")


if __name__ == "__main__":
    main()
