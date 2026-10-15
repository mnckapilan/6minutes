"""Stripe (test mode): one Customer per client, one invoice item per time entry with the evidence in metadata."""

import os
import uuid

import stripe

from . import store

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
DRY_RUN = not stripe.api_key
if stripe.api_key and "_test_" not in stripe.api_key:
    raise RuntimeError("6minutes only runs against Stripe test mode keys")


def dashboard_url(invoice_id: str) -> str:
    return f"https://dashboard.stripe.com/test/invoices/{invoice_id}"


def _md(value) -> str:
    return str(value)[:500]


def ensure_customer(ledger: dict, firm: dict, client_key: str) -> str:
    if client_key in ledger["customers"]:
        return ledger["customers"][client_key]
    client = firm["clients"][client_key]
    if DRY_RUN:
        cid = f"cus_dry_{uuid.uuid4().hex[:10]}"
    else:
        cid = stripe.Customer.create(
            name=client["name"], email=client["billing_email"],
            metadata={"sixminutes_client": client_key, "domains": ",".join(client.get("domains", []))},
        ).id
    ledger["customers"][client_key] = cid
    store.audit("stripe.customer", client=client_key, customer=cid)
    return cid


def line_description(firm: dict, e: dict) -> str:
    who = firm["consultants"][e["consultant"]]["name"].split()[0]
    proj = firm["projects"][e["project"]]["name"]
    days = e["days"][0][5:] if len(e["days"]) == 1 else f"{e['days'][0][5:]}..{e['days'][-1][5:]}"
    desc = f"{e['hours']}h · {proj} · {who} ({e['role']}) · {days}"
    if e.get("decision_note"):
        desc += f" · {e['decision_note']}"
    return desc


def create_invoice(ledger: dict, firm: dict, project_key: str, period: str, entries: list[dict], memo: str = "") -> dict:
    proj = firm["projects"][project_key]
    customer = ensure_customer(ledger, firm, proj["client"])
    total = round(sum(e["amount"] for e in entries), 2)
    meta = {"project": project_key, "po_number": proj.get("po_number", ""), "period": period,
            "entries": ",".join(e["id"] for e in entries), "generated_by": "6minutes agent"}
    description = f"{proj['name']} ({proj.get('po_number', '')}) · {period}" + (f" · {memo}" if memo else "")

    if DRY_RUN:
        inv = {"id": f"in_dry_{uuid.uuid4().hex[:10]}", "hosted_invoice_url": None, "number": None, "status": "open"}
    else:
        draft = stripe.Invoice.create(
            customer=customer, collection_method="send_invoice", days_until_due=30, currency="gbp",
            description=description, metadata=meta, pending_invoice_items_behavior="exclude",
            custom_fields=[{"name": "PO", "value": proj.get("po_number", "-")[:30]}],
        )
        for e in entries:
            stripe.InvoiceItem.create(
                customer=customer, invoice=draft.id, currency="gbp",
                amount=int(round(e["amount"] * 100)),
                description=line_description(firm, e),
                metadata={
                    "entry_id": e["id"], "consultant": e["consultant"], "role": e["role"],
                    "hours": _md(e["hours"]), "rate_gbp": _md(e["rate"]), "rule": e["rule_id"],
                    "activity_ids": _md(",".join(e["activity_ids"])),
                    "reason": _md(e["reason"]),
                    "decision": _md(e.get("decision_ref") or ""),
                    "memory": _md(e.get("memory") or ""),
                    "confidence": _md(e["confidence"]),
                },
            )
        stripe.Invoice.finalize_invoice(draft.id)
        sent = stripe.Invoice.send_invoice(draft.id)
        inv = {"id": sent.id, "hosted_invoice_url": sent.hosted_invoice_url, "number": sent.number, "status": sent.status}

    record = {
        "id": inv["id"], "number": inv["number"], "project": project_key, "client": proj["client"], "period": period,
        "amount": total, "entries": [e["id"] for e in entries], "status": inv["status"],
        "hosted_invoice_url": inv["hosted_invoice_url"], "dashboard_url": None if DRY_RUN else dashboard_url(inv["id"]),
        "memo": memo, "created": store.now(), "dry_run": DRY_RUN,
    }
    ledger["invoices"].append(record)
    for e in entries:
        e["invoice_id"] = inv["id"]
    store.audit("stripe.invoice", invoice=inv["id"], project=project_key, period=period, amount=total,
                entries=record["entries"], dry_run=DRY_RUN)
    return record


def mark_paid(ledger: dict, invoice_id: str) -> dict:
    inv = next(i for i in ledger["invoices"] if i["id"] == invoice_id)
    if not DRY_RUN:
        stripe.Invoice.pay(invoice_id, paid_out_of_band=True)
    inv["status"] = "paid"
    store.audit("stripe.paid", invoice=invoice_id, amount=inv["amount"])
    return inv
