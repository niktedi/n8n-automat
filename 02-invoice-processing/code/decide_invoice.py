# n8n Code node "Decide" (Python, Run Once for All Items)
# Routing rules in one place. Input: everything collected so far
# (checks, same_file/same_number, whitelist, fx, category, cost_center, approval_threshold_pln).
# Output adds: gross_pln, status, review_reasons.
#
# status:
#   duplicate     - already in the register (same file, or same seller + number) -> not inserted
#   error         - data is invalid (failed checks) -> stored, nobody can approve it as is
#   needs_review  - data is valid, but a person must decide (Telegram approval)
#   approved      - everything fine, booked automatically

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def decide(item):
    gross_raw = (item.get("amounts") or {}).get("gross")
    gross = Decimal(gross_raw) if gross_raw is not None else None
    rate = (item.get("fx") or {}).get("rate")
    if gross is None:
        gross_pln = None                      # unreadable amount -> invoice is an "error" anyway
    elif rate:
        gross_pln = (gross * Decimal(str(rate))).quantize(CENT, ROUND_HALF_UP)
    else:
        gross_pln = gross
    threshold = Decimal(str(item["approval_threshold_pln"]))

    reasons = []
    if (item.get("whitelist") or {}).get("status") == "NIE":
        reasons.append("bank account is not on the VAT white list")
    if item.get("category") in (None, "", "other") or not item.get("cost_center"):
        reasons.append("category not recognised")
    if gross_pln is not None and gross_pln > threshold:
        reasons.append(f"gross {gross_pln} PLN is above the approval threshold {threshold} PLN")
    reasons += [f"warning: {w}" for w in item["checks"]["warnings"]]

    if item.get("same_file") or item.get("same_number"):
        status = "duplicate"
    elif not item["checks"]["ok"]:
        status = "error"
    elif reasons:
        status = "needs_review"
    else:
        status = "approved"

    return {**item, "gross_pln": str(gross_pln) if gross_pln is not None else None, "status": status, "review_reasons": reasons}


return [{"json": decide(i["json"])} for i in _items]
