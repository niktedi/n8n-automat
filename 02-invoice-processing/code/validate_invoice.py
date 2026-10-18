# n8n Code node "Validate" (Python, Run Once for All Items)
# Deterministic checks of the data the LLM extracted. No network, no database.
# Input item fields: invoice (LLM output), our_nip, contentBytes, fileName, messageId, ...
# Output: the same item plus file_sha256, amounts (normalized) and checks {errors, warnings}.

import base64
import hashlib
import re
from datetime import date
from decimal import Decimal, InvalidOperation

NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)


def nip_valid(nip):
    if not nip or not re.fullmatch(r"\d{10}", nip):
        return False
    return sum(int(d) * w for d, w in zip(nip, NIP_WEIGHTS)) % 11 == int(nip[9])


def iban_valid(iban):
    """Polish 26-digit account (NRB) or a full IBAN; checks the mod-97 checksum."""
    s = re.sub(r"\s", "", iban or "").upper()
    if re.fullmatch(r"\d{26}", s):
        s = "PL" + s
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", s):
        return False
    moved = s[4:] + s[:4]
    digits = "".join(str(int(c, 36)) for c in moved)
    return int(digits) % 97 == 1


def to_decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def to_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def validate(item):
    inv = item["invoice"]
    errors, warnings = [], []
    seller = inv.get("seller") or {}
    buyer = inv.get("buyer") or {}

    # --- required fields
    for name in ("invoice_number", "issue_date", "currency", "net_amount", "vat_amount", "gross_amount"):
        if not inv.get(name):
            errors.append(f"missing:{name}")

    # --- seller / buyer identity
    if seller.get("nip"):
        if not nip_valid(seller["nip"]):
            errors.append("seller_nip_checksum")
    elif not seller.get("vat_id"):
        errors.append("missing:seller_tax_id")

    if (buyer.get("nip") or "") != item["our_nip"]:
        errors.append("buyer_not_us")

    # --- amounts: printed totals must add up, never "fixed"
    net, vat, gross = (to_decimal(inv.get(k)) for k in ("net_amount", "vat_amount", "gross_amount"))
    if None in (net, vat, gross):
        errors.append("amount_not_a_number")
    else:
        if net + vat != gross:
            errors.append(f"amounts_mismatch: {net} + {vat} != {gross}")
        lines_net = [to_decimal(l.get("net_amount")) for l in inv.get("lines") or []]
        if lines_net and None not in lines_net and sum(lines_net) != net:
            warnings.append(f"lines_sum_mismatch: {sum(lines_net)} != {net}")
        if inv.get("reverse_charge") and vat != 0:
            errors.append("reverse_charge_with_vat")

    # --- dates
    issue, sale, due = (to_date(inv.get(k)) for k in ("issue_date", "sale_date", "due_date"))
    if inv.get("issue_date") and issue is None:
        errors.append("issue_date_invalid")
    if issue and issue > date.today():
        errors.append("issue_date_in_future")
    if issue and due and due < issue:
        errors.append("due_before_issue")
    if not due:
        warnings.append("missing:due_date")

    # --- bank account
    account = inv.get("bank_account")
    if not account:
        warnings.append("missing:bank_account")
    elif not iban_valid(account):
        errors.append("bank_account_checksum")

    # --- file fingerprint for duplicate detection
    sha256 = hashlib.sha256(base64.b64decode(item["contentBytes"])).hexdigest()

    out = {k: v for k, v in item.items() if k != "contentBytes"}
    out.update({
        "file_sha256": sha256,
        "seller_tax_id": seller.get("nip") or seller.get("vat_id"),
        "amounts": {k: (str(v) if v is not None else None) for k, v in (("net", net), ("vat", vat), ("gross", gross))},
        "fx_date_basis": str(sale or issue) if (sale or issue) else None,
        "checks": {"errors": errors, "warnings": warnings, "ok": not errors},
    })
    return out


return [{"json": validate(i["json"])} for i in _items]
