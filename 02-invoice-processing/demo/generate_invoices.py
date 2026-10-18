"""Generate demo invoices (PDF) for scenario 02-invoice-processing.

Each invoice exercises one branch of the workflow; demo/expected.csv says which.
Real seller NIPs are used so the VAT white list check can pass: the script asks
the public Ministry of Finance API (wl-api.mf.gov.pl) for each seller's name,
address and a bank account that is on the white list. Every page carries a
"DEMO" watermark so nobody mistakes these files for real invoices.

Usage:
    pip install reportlab
    python generate_invoices.py                  # writes ./invoices and ./expected.csv
    python generate_invoices.py --date 2026-10-17
    python generate_invoices.py --offline        # no API call, fake accounts (white list will fail)
"""

import argparse
import csv
import json
import random
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# --- who is who -------------------------------------------------------------

SELLER_NIPS = {
    "startup": "5213641211",
    "telco": "5261040567",
    "catering": "1182198220",
}

# fictional buyer: the company whose mailbox receives the invoices
BUYER = {
    "name": "Raketa Automation Sp. z o.o.",
    "address": "ul. Testowa 1, 00-001 Warszawa",
    "nip": "1234563218",          # valid checksum, fictional
}

# fictional foreign supplier (EU reverse charge, no Polish NIP)
FOREIGN = {
    "name": "Nordwind Cloud GmbH",
    "address": "Beispielstrasse 7, 10115 Berlin, Germany",
    "vat_id": "DE123456789",
    "iban": "DE89 3704 0044 0532 0130 00",
}

WL_URL = "https://wl-api.mf.gov.pl/api/search/nip/{nip}?date={day}"

# --- helpers ----------------------------------------------------------------

D = Decimal
CENT = D("0.01")


def money(x) -> Decimal:
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


def fmt_pl(x: Decimal) -> str:
    """1234.5 -> '1 234,50' (Polish format, non-breaking space)."""
    s = f"{x:,.2f}"
    return s.replace(",", "\u00a0").replace(".", ",")


def fmt_en(x: Decimal) -> str:
    return f"{x:,.2f}"


def nip_ok(nip: str) -> bool:
    weights = (6, 5, 7, 2, 3, 4, 5, 6, 7)
    if len(nip) != 10 or not nip.isdigit():
        return False
    return sum(int(a) * w for a, w in zip(nip, weights)) % 11 == int(nip[9])


def random_nrb(rng: random.Random) -> str:
    """Random Polish account number (26 digits) with a valid IBAN checksum."""
    bban = "".join(str(rng.randint(0, 9)) for _ in range(24))
    # IBAN check: BBAN + 'PL' (P=25, L=21) + '00'
    check = 98 - int(bban + "252100") % 97
    return f"{check:02d}{bban}"


def group_nrb(nrb: str) -> str:
    return nrb[:2] + " " + " ".join(nrb[i:i + 4] for i in range(2, 26, 4))


def fetch_subject(nip: str, day: date) -> dict:
    with urllib.request.urlopen(WL_URL.format(nip=nip, day=day.isoformat()), timeout=20) as r:
        data = json.load(r)
    subject = data["result"]["subject"]
    if not subject:
        raise SystemExit(f"NIP {nip} not found in the VAT white list")
    if not subject.get("accountNumbers"):
        raise SystemExit(f"NIP {nip} has no bank accounts on the white list")
    return {
        "name": subject["name"],
        "address": subject.get("workingAddress") or subject.get("residenceAddress") or "",
        "nip": nip,
        "account": subject["accountNumbers"][0],
    }


def offline_subject(key: str, nip: str, rng: random.Random) -> dict:
    return {
        "name": f"Demo Seller {key.title()} Sp. z o.o.",
        "address": "ul. Przykładowa 10, 00-950 Warszawa",
        "nip": nip,
        "account": random_nrb(rng),
    }


# --- invoice model ----------------------------------------------------------

@dataclass
class Line:
    name: str
    qty: Decimal
    unit: str
    unit_net: Decimal
    vat_rate: Decimal  # 23 -> 23 %, 0 -> reverse charge

    @property
    def net(self) -> Decimal:
        return money(self.qty * self.unit_net)


@dataclass
class Invoice:
    file_stem: str
    number: str
    seller: dict
    issue: date
    sale: date
    due: date
    lines: list
    currency: str = "PLN"
    language: str = "pl"
    seller_nip_override: str = ""       # printed NIP differs from the real one
    gross_error: Decimal = D("0")       # added to the printed gross amount
    stamp: str = ""                     # extra stamp, e.g. "KOPIA"
    expected: str = ""
    reason: str = ""
    notes: list = field(default_factory=list)

    @property
    def printed_nip(self) -> str:
        return self.seller_nip_override or self.seller.get("nip", "")

    @property
    def net(self) -> Decimal:
        return sum((l.net for l in self.lines), D("0"))

    @property
    def vat(self) -> Decimal:
        # Polish practice: VAT on the sum of net amounts per rate
        by_rate = {}
        for l in self.lines:
            by_rate[l.vat_rate] = by_rate.get(l.vat_rate, D("0")) + l.net
        return sum((money(n * r / 100) for r, n in by_rate.items()), D("0"))

    @property
    def gross_true(self) -> Decimal:
        return self.net + self.vat

    @property
    def gross_printed(self) -> Decimal:
        return self.gross_true + self.gross_error


# --- PDF rendering ----------------------------------------------------------

def register_font() -> tuple[str, str]:
    candidates = [
        ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
    ]
    for regular, bold in candidates:
        if Path(regular).exists() and Path(bold).exists():
            pdfmetrics.registerFont(TTFont("Inv", regular))
            pdfmetrics.registerFont(TTFont("Inv-Bold", bold))
            return "Inv", "Inv-Bold"
    raise SystemExit("No TTF font with Polish characters found (Arial or DejaVu Sans)")


LABELS = {
    "pl": {
        "title": "Faktura VAT nr", "seller": "Sprzedawca", "buyer": "Nabywca",
        "issue": "Data wystawienia", "sale": "Data sprzedaży", "due": "Termin płatności",
        "account": "Numer rachunku", "nip": "NIP", "no": "Lp.", "item": "Nazwa towaru / usługi",
        "qty": "Ilość", "unit": "J.m.", "price": "Cena netto", "net": "Wartość netto",
        "rate": "VAT %", "vat": "Kwota VAT", "gross": "Wartość brutto",
        "total": "Razem", "to_pay": "Do zapłaty", "method": "Sposób płatności: przelew",
    },
    "en": {
        "title": "Invoice no.", "seller": "Supplier", "buyer": "Customer",
        "issue": "Issue date", "sale": "Date of supply", "due": "Due date",
        "account": "IBAN", "nip": "VAT ID", "no": "#", "item": "Description",
        "qty": "Qty", "unit": "Unit", "price": "Unit price", "net": "Net",
        "rate": "VAT %", "vat": "VAT", "gross": "Gross",
        "total": "Total", "to_pay": "Amount due", "method": "Payment: bank transfer",
    },
}


def render(inv: Invoice, path: Path, font: str, bold: str) -> None:
    L = LABELS[inv.language]
    fmt = fmt_pl if inv.language == "pl" else fmt_en
    cur = inv.currency

    base = ParagraphStyle("b", fontName=font, fontSize=9, leading=12)
    small = ParagraphStyle("s", parent=base, fontSize=8, leading=10)
    head = ParagraphStyle("h", parent=base, fontName=bold, fontSize=15, leading=19)
    lab = ParagraphStyle("l", parent=base, fontName=bold)

    def watermark(canvas, _doc):
        canvas.saveState()
        canvas.setFont(bold, 24)
        canvas.setFillColor(colors.Color(0.85, 0.15, 0.15, alpha=0.18))
        canvas.translate(A4[0] / 2, A4[1] / 2)
        canvas.rotate(35)
        canvas.drawCentredString(0, 0, "DEMO — test data, not a real invoice")
        if inv.stamp:
            canvas.setFont(bold, 40)
            canvas.drawCentredString(0, -70, inv.stamp)
        canvas.restoreState()
        canvas.setFont(font, 7)
        canvas.setFillColor(colors.grey)
        canvas.drawString(15 * mm, 10 * mm,
                          "Generated by n8n-automat / 02-invoice-processing demo. Not a fiscal document.")

    s = inv.seller
    seller_id = inv.printed_nip if inv.language == "pl" else s.get("vat_id", "")
    party = Table([
        [Paragraph(L["seller"], lab), Paragraph(L["buyer"], lab)],
        [Paragraph(f"{s['name']}<br/>{s['address']}<br/>{L['nip']}: {seller_id}", base),
         Paragraph(f"{BUYER['name']}<br/>{BUYER['address']}<br/>"
                   + (f"NIP: {BUYER['nip']}" if inv.language == "pl" else f"VAT ID: PL{BUYER['nip']}"), base)],
    ], colWidths=[90 * mm, 90 * mm])
    party.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))

    dates = Table([
        [L["issue"], inv.issue.isoformat()],
        [L["sale"], inv.sale.isoformat()],
        [L["due"], inv.due.isoformat()],
    ], colWidths=[40 * mm, 40 * mm], hAlign="LEFT")
    dates.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), font), ("FONTSIZE", (0, 0), (-1, -1), 9)]))

    th = ParagraphStyle("th", parent=small, fontName=bold)
    rows = [[Paragraph(L[k], th) for k in ("no", "item", "qty", "unit", "price", "net", "rate", "vat", "gross")]]
    for i, l in enumerate(inv.lines, 1):
        line_vat = money(l.net * l.vat_rate / 100)
        rate = f"{l.vat_rate}" if l.vat_rate else ("np" if inv.language == "pl" else "RC")
        rows.append([str(i), Paragraph(l.name, small), fmt(l.qty).rstrip("0").rstrip(",.") or "0",
                     l.unit, fmt(l.unit_net), fmt(l.net), rate, fmt(line_vat), fmt(l.net + line_vat)])
    rows.append(["", L["total"], "", "", "", fmt(inv.net), "", fmt(inv.vat), fmt(inv.gross_printed)])

    items = Table(rows, colWidths=[8 * mm, 48 * mm, 12 * mm, 11 * mm, 20 * mm, 22 * mm, 12 * mm, 20 * mm, 24 * mm],
                  repeatRows=1)
    items.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTNAME", (0, 0), (-1, 0), bold),
        ("FONTNAME", (0, -1), (-1, -1), bold),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.92, 0.92, 0.92)),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    account = group_nrb(s["account"]) if inv.language == "pl" else s["iban"]
    story = [
        Paragraph(f"{L['title']} {inv.number}", head),
        Spacer(1, 4 * mm), dates, Spacer(1, 6 * mm), party, Spacer(1, 8 * mm), items, Spacer(1, 6 * mm),
        Paragraph(f"<b>{L['to_pay']}: {fmt(inv.gross_printed)} {cur}</b>", ParagraphStyle(
            "p", parent=base, fontName=bold, fontSize=11, leading=14)),
        Spacer(1, 3 * mm),
        Paragraph(L["method"], base),
        Paragraph(f"{L['account']}: {account}", base),
    ]
    for note in inv.notes:
        story += [Spacer(1, 2 * mm), Paragraph(note, small)]

    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                      topMargin=15 * mm, bottomMargin=18 * mm,
                      title=f"Invoice {inv.number} (DEMO)").build(
        story, onFirstPage=watermark, onLaterPages=watermark)


# --- the demo set -------------------------------------------------------------

def build_set(sellers: dict, today: date, rng: random.Random) -> list[Invoice]:
    st, tm, ca = sellers["startup"], sellers["telco"], sellers["catering"]
    d = lambda n: today - timedelta(days=n)  # noqa: E731
    vat = D("23")

    def inv(stem, number, seller, issue, lines, due_days=14, **kw):
        return Invoice(file_stem=stem, number=number, seller=seller, issue=issue, sale=issue,
                       due=issue + timedelta(days=due_days), lines=lines, **kw)

    invoices = [
        inv("01", "FV/2026/10/0142", tm, d(12),
            [Line("Abonament internet światłowodowy 1 Gb/s", D(1), "szt.", D("99.00"), vat),
             Line("Abonament telefonia komórkowa", D(2), "szt.", D("45.00"), vat)],
            expected="booked", reason="all checks pass"),
        inv("02", "FS/27/10/2026", ca, d(10),
            [Line("Lunch firmowy - zestaw dnia", D(18), "szt.", D("32.52"), D("8"))],
            due_days=7, expected="booked", reason="all checks pass, VAT 8%"),
        inv("03", "2026/10/017", st, d(9),
            [Line("Wdrożenie automatyzacji n8n - etap 1", D(1), "usł.", D("2400.00"), vat)],
            expected="booked", reason="all checks pass"),
        inv("04", "FV/2026/10/0198", tm, d(6),
            [Line("Dodatkowy pakiet danych 50 GB", D(1), "szt.", D("40.65"), vat)],
            expected="booked", reason="all checks pass, small amount"),
        inv("05", "FS/31/10/2026", ca, d(4),
            [Line("Catering - przerwa kawowa", D(1), "usł.", D("380.00"), D("8")),
             Line("Woda mineralna 0,5 l", D(24), "szt.", D("2.03"), vat)],
            due_days=7, expected="booked", reason="all checks pass, two VAT rates"),
        Invoice(file_stem="06", number="NWC-2026-10-0081",
                seller=dict(FOREIGN, nip=""), issue=d(7), sale=d(8), due=d(7) + timedelta(days=30),
                lines=[Line("Managed Kubernetes cluster, October 2026", D(1), "mo", D("189.00"), D("0")),
                       Line("Object storage 500 GB", D(1), "mo", D("11.50"), D("0"))],
                currency="EUR", language="en",
                notes=["Reverse charge: VAT to be accounted for by the recipient (Art. 196 VAT Directive)."],
                expected="booked", reason="EUR: NBP rate from the business day before the date of supply"),
        inv("07", "2026/10/021", st, d(3),
            [Line("Licencja roczna - platforma automatyzacji procesów", D(1), "szt.", D("14800.00"), vat)],
            expected="needs_review", reason="gross above approval threshold -> Telegram approval"),
        inv("08", "FS/27/10/2026", ca, d(10),
            [Line("Lunch firmowy - zestaw dnia", D(18), "szt.", D("32.52"), D("8"))],
            due_days=7, stamp="KOPIA",
            expected="duplicate", reason="same seller NIP + number as invoice 02, different file"),
        inv("09", "FV/2026/10/0211", tm, d(2),
            [Line("Router Wi-Fi 6", D(1), "szt.", D("329.27"), vat)],
            gross_error=D("10.00"),
            expected="error", reason="net + VAT != gross (printed gross is 10.00 too high)"),
        inv("10", "2026/10/024", dict(st, account=random_nrb(rng)), d(2),
            [Line("Konsultacje techniczne", D(8), "godz.", D("250.00"), vat)],
            expected="needs_review", reason="bank account not on the VAT white list"),
        inv("11", "FV/2026/10/0219", tm, d(1),
            [Line("Usługa konfiguracji VPN", D(1), "usł.", D("150.00"), vat)],
            seller_nip_override=tm["nip"][:9] + str((int(tm["nip"][9]) + 1) % 10),
            expected="error", reason="seller NIP has an invalid checksum"),
        inv("12", "FS/35/10/2026", ca, d(1),
            [Line("Catering - spotkanie zespołu", D(1), "usł.", D("520.00"), D("8"))],
            due_days=-5, expected="error", reason="due date is before the issue date"),
    ]
    assert not nip_ok(invoices[10].printed_nip), "invoice 11 must have a broken NIP"
    return invoices


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default=str(Path(__file__).parent / "invoices"))
    p.add_argument("--date", default=date.today().isoformat(), help="'today' for the demo set (YYYY-MM-DD)")
    p.add_argument("--offline", action="store_true", help="do not call the white list API")
    p.add_argument("--seed", type=int, default=2026)
    args = p.parse_args()

    today = date.fromisoformat(args.date)
    rng = random.Random(args.seed)
    for key, nip in SELLER_NIPS.items():
        assert nip_ok(nip), f"{key}: NIP {nip} has an invalid checksum"
    assert nip_ok(BUYER["nip"])

    sellers = {}
    for key, nip in SELLER_NIPS.items():
        sellers[key] = offline_subject(key, nip, rng) if args.offline else fetch_subject(nip, today)
        print(f"{key:9} {nip}  {sellers[key]['name']}  account ...{sellers[key]['account'][-4:]}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    font, bold = register_font()
    invoices = build_set(sellers, today, rng)

    with open(out.parent / "expected.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "invoice_number", "seller_nip", "currency", "net", "vat", "gross_printed",
                    "expected_status", "reason"])
        for inv in invoices:
            name = f"{inv.file_stem}_{inv.number.replace('/', '-')}.pdf"
            render(inv, out / name, font, bold)
            w.writerow([name, inv.number, inv.printed_nip or inv.seller.get("vat_id", ""), inv.currency,
                        inv.net, inv.vat, inv.gross_printed, inv.expected, inv.reason])
            print(f"  {name:32} {inv.currency} {inv.gross_printed:>10}  -> {inv.expected}")

    print(f"\n{len(invoices)} invoices in {out}, expectations in {out.parent / 'expected.csv'}")


if __name__ == "__main__":
    main()
