"""Seed Cognee with the project context graph (run once before recording; remember() is slow-ish)."""

import asyncio

from sixminutes import memory, store

CONTEXT = [
    "Acme Q3 Audit (project ACME-Q3, code ACQ3, PO-4471) is an engagement for client Acme Corp (domain acme.com). "
    "Its Teams channel is #acme-q3, its folder is /Clients/Acme/Q3, and its key files are Acme_Model_v3.xlsx and "
    "Acme_Audit_Findings.docx. Priya Shah is the engagement manager.",
    "Globex Strategy (project GLX-STRAT, code GLXS) is an engagement for client Globex Industries (domain globex.com). "
    "Its Teams channel is #globex-strategy, its folder is /Clients/Globex/Strategy, and its key files are "
    "Globex_Market_Sizing.pptx and Globex_Interviews.docx. Sam Lee is the analyst on it.",
    "Northbeam Advisory internal work (project INTERNAL) is not billable: the #general and #ops channels, the /Internal "
    "folder, managers' meetings, training and utilisation reporting.",
    "Email thread thr-acme-model ('Model assumptions: revenue bridge') is client work on Acme Q3 Audit with Jane Doe of Acme FP&A.",
    "Email thread thr-acme-ops-review ('Acme ops review') is a thread between Priya Shah and Tom Ward (Northbeam ops) "
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
