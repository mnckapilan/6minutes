"""Seed Cognee with the project context graph (run once before recording; remember() is slow-ish)."""

import asyncio

from sixminutes import memory, store

CONTEXT = [
    "Acme Q3 Audit (project ACME-Q3, code ACQ3, PO-4471) is an engagement for client Acme Corp (domain acme.com). "
    "Its Teams channel is #acme-q3, its folder is /Clients/Acme/Q3, and its key files are Acme_Model_v3.xlsx and "
    "Acme_Audit_Findings.docx. Hannah Cole is the engagement manager.",
    "Globex Strategy (project GLX-STRAT, code GLXS) is an engagement for client Globex Industries (domain globex.com). "
    "Its Teams channel is #globex-strategy, its folder is /Clients/Globex/Strategy, and its key files are "
    "Globex_Market_Sizing.pptx and Globex_Interviews.docx. Sam Lee is the analyst on it.",
    "Brightwater Regulatory Submission (project BW-REG, code BWRS, PO-BW-3310) is an engagement for client Brightwater "
    "Utilities (domain brightwater.com). Its Teams channel is #bw-regulatory, its folder is /Clients/Brightwater/Regulatory, "
    "and its key files are Brightwater_Submission_Draft.docx and Brightwater_Evidence_Tracker.xlsx. Maya Okafor manages it "
    "with Daniel Reyes and Tariq Hassan.",
    "Brightwater Grid Resilience Study (project BW-GRID, code BWGR, PO-BW-3355) is a separate engagement for Brightwater "
    "Utilities' energy arm (domain bwenergy.com). Its channel is #bw-grid, its folder is /Clients/Brightwater/Grid, and its "
    "key files are Grid_Resilience_Model.xlsx and Grid_Steerco_Pack.pptx. Jordan Blake is the partner and Daniel Reyes the "
    "consultant. Its PO cap is small.",
    "Kestrel Trial Supply Optimisation (project KES-TRIAL, code KTSO, PO-KP-7781) is an engagement for client Kestrel Pharma "
    "(domain kestrelpharma.com) on clinical trial supply logistics. Its channel is #kestrel-trial, its folder is "
    "/Clients/Kestrel/TrialSupply, and its key files are Kestrel_Supply_Forecast.xlsx and Kestrel_Site_Playbook.docx. Chloe "
    "Whitfield and Elena Rossi staff it. Pitching new work to other Kestrel teams is business development, not project work.",
    "Lumen Customer Experience Review (project LUM-CX, code LMCX, PO-LR-1204) is an engagement for client Lumen Retail "
    "(domain lumenretail.com). Its channel is #lumen-cx, its folder is /Clients/Lumen/CX, and its key files are "
    "Lumen_CX_Survey_Analysis.xlsx and Lumen_Journey_Map.pptx. Chloe Whitfield and Tariq Hassan staff it.",
    "Email thread thr-kes-followon ('Quick catch-up: ideas for Kestrel's commercial team') is an informal conversation "
    "with a different Kestrel team about possible follow-on work; it is not Kestrel Trial Supply project time.",
    "Northbeam Advisory internal work (project INTERNAL) is not billable: the #general and #ops channels, the /Internal "
    "folder, managers' meetings, partner pipeline reviews, training, career catch-ups, community of practice and utilisation reporting.",
    "Email thread thr-acme-model ('Model assumptions: revenue bridge') is client work on Acme Q3 Audit with Jane Doe of Acme FP&A.",
    "Email thread thr-acme-ops-review ('Acme ops review') is a thread between Hannah Cole and Tom Ward (Northbeam ops) "
    "about staffing and utilisation; Acme's ops alias is only cc'd.",
]


async def main() -> None:
    store.ensure()
    for text in CONTEXT:
        row = await memory.remember(text, kind="context")
        print(row["backend"], row.get("error", ""), text[:60])
    print(await memory.recall("Which project does Acme_Model_v3.xlsx belong to?"))


if __name__ == "__main__":
    asyncio.run(main())
