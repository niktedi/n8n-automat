# n8n Code node "Format message" (Python, Run Once for All Items)
# Builds the Telegram notification for each processed invoice.
# Output adds: kind (approved | review | error | duplicate) and text (Telegram HTML).
# approved invoices get no message: they show up in the weekly summary instead.


def esc(value):
    """Escape text for Telegram HTML parse mode."""
    return str(value if value is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def money(amount, currency):
    return f"{esc(amount)} {esc(currency)}"


def build(item):
    inv = item.get("invoice") or {}
    seller = inv.get("seller") or {}
    status = item.get("status")
    invoice_id = item.get("invoice_id")

    # INSERT hit a UNIQUE constraint in the same run -> it is a duplicate after all
    kind = {"needs_review": "review", "error": "error", "duplicate": "duplicate"}.get(status, "approved")
    if status != "duplicate" and invoice_id is None:
        kind = "duplicate"

    head = f"{esc(seller.get('name'))} ({esc(item.get('seller_tax_id'))})\n" \
           f"No. <b>{esc(inv.get('invoice_number'))}</b> · issued {esc(inv.get('issue_date'))} · due {esc(inv.get('due_date'))}"

    amounts = item.get("amounts") or {}
    fx = item.get("fx") or {}
    gross = money(amounts.get("gross"), inv.get("currency"))
    if fx.get("rate"):
        gross += f" = {esc(item.get('gross_pln'))} PLN (NBP {esc(fx.get('table'))}, {esc(fx.get('rate'))})"

    if kind == "duplicate":
        text = f"⚪ <b>Duplicate skipped</b>\n{head}\nAlready in the register. File: {esc(item.get('fileName'))}"
    elif kind == "error":
        errors = "\n".join(f"• {esc(e)}" for e in (item.get("checks") or {}).get("errors", []))
        text = (f"🔴 <b>Invoice failed checks</b> · #{esc(invoice_id)}\n{head}\nGross: {gross}\n\n"
                f"{errors}\n\nAsk the supplier for a corrected invoice. File: {esc(item.get('fileName'))}")
    elif kind == "review":
        wl = item.get("whitelist") or {}
        reasons = "\n".join(f"• {esc(r)}" for r in item.get("review_reasons", []))
        text = (f"🟡 <b>Invoice needs approval</b> · #{esc(invoice_id)}\n{head}\n"
                f"Gross: <b>{gross}</b>\n"
                f"Category: {esc(item.get('category'))} → {esc(item.get('cost_center') or '—')}\n"
                f"VAT white list: {esc(wl.get('status'))}\n\n"
                f"Why:\n{reasons}")
    else:
        text = ""

    return {**item, "kind": kind, "text": text}


return [{"json": build(i["json"])} for i in _items]
