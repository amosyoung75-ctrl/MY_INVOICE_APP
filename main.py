import html
import io
import os
import smtplib
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pandas as pd
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import create_engine, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from models import Base, Customer, Invoice, InvoiceItem, InvoiceStatus


# ============================================================
# DATABASE
# ============================================================

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./invoices.db",
)

# Some hosting platforms still provide postgres://
# Convert it to the SQLAlchemy-supported format.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgres://",
        "postgresql://",
        1,
    )

connect_args = {}

if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


# ============================================================
# APPLICATION
# ============================================================

app = FastAPI(
    title="Invoice Manager",
    description="Simple invoice, customer and payment management system",
    version="1.0.0",
)


# ============================================================
# HELPERS
# ============================================================

def money(value) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0.00")


def money_text(value) -> str:
    return f"${money(value):,.2f}"


def escape(value) -> str:
    return html.escape(str(value or ""))


def generate_invoice_number(db: Session) -> str:
    latest = (
        db.query(Invoice)
        .order_by(Invoice.id.desc())
        .first()
    )

    if latest and latest.invoice_number:
        try:
            number = int(
                latest.invoice_number.replace("INV-", "")
            )
            number += 1
        except ValueError:
            number = latest.id + 1
    else:
        number = 1

    return f"INV-{number:05d}"


def update_invoice_status(invoice: Invoice):
    total = money(invoice.total_amount)
    paid = money(invoice.amount_paid)

    if paid >= total and total > 0:
        invoice.status = InvoiceStatus.PAID

    elif paid > 0:
        invoice.status = InvoiceStatus.PARTIAL

    elif invoice.due_date < date.today():
        invoice.status = InvoiceStatus.OVERDUE

    else:
        invoice.status = InvoiceStatus.UNPAID


def refresh_overdue_invoices(db: Session):
    invoices = (
        db.query(Invoice)
        .filter(
            Invoice.due_date < date.today(),
            Invoice.status != InvoiceStatus.PAID,
        )
        .all()
    )

    for invoice in invoices:
        update_invoice_status(invoice)

    db.commit()


# ============================================================
# EMAIL
# ============================================================

def send_invoice_email(
    to_email: str,
    customer_name: str,
    invoice_number: str,
    pdf_bytes: bytes,
) -> bool:

    smtp_server = os.getenv(
        "SMTP_SERVER",
        "smtp.gmail.com",
    )

    smtp_port = int(
        os.getenv("SMTP_PORT", "587")
    )

    sender_email = os.getenv("SENDER_EMAIL")
    sender_password = os.getenv("SENDER_PASSWORD")

    if not sender_email or not sender_password:
        return False

    message = MIMEMultipart()

    message["From"] = sender_email
    message["To"] = to_email
    message["Subject"] = (
        f"Invoice {invoice_number}"
    )

    body = (
        f"Hello {customer_name},\n\n"
        f"Please find attached invoice "
        f"{invoice_number}.\n\n"
        f"Thank you."
    )

    message.attach(
        MIMEText(body, "plain")
    )

    attachment = MIMEApplication(
        pdf_bytes,
        _subtype="pdf",
    )

    attachment.add_header(
        "Content-Disposition",
        "attachment",
        filename=f"{invoice_number}.pdf",
    )

    message.attach(attachment)

    try:
        with smtplib.SMTP(
            smtp_server,
            smtp_port,
            timeout=30,
        ) as server:

            server.starttls()

            server.login(
                sender_email,
                sender_password,
            )

            server.send_message(message)

        return True

    except Exception:
        return False


# ============================================================
# PDF GENERATION
# ============================================================

def create_invoice_pdf(invoice: Invoice) -> bytes:

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
        Paragraph,
    )

    buffer = io.BytesIO()

    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )

    styles = getSampleStyleSheet()

    story = []

    story.append(
        Paragraph(
            "<b>INVOICE</b>",
            styles["Title"],
        )
    )

    story.append(
        Spacer(1, 10)
    )

    customer = invoice.customer

    customer_address = (
        customer.billing_address or ""
    )

    information = [
        [
            Paragraph(
                f"<b>Invoice:</b> "
                f"{escape(invoice.invoice_number)}",
                styles["Normal"],
            ),
            Paragraph(
                f"<b>Issue Date:</b> "
                f"{invoice.issue_date}",
                styles["Normal"],
            ),
        ],
        [
            Paragraph(
                f"<b>Customer:</b> "
                f"{escape(customer.name)}",
                styles["Normal"],
            ),
            Paragraph(
                f"<b>Due Date:</b> "
                f"{invoice.due_date}",
                styles["Normal"],
            ),
        ],
        [
            Paragraph(
                f"<b>Email:</b> "
                f"{escape(customer.email)}",
                styles["Normal"],
            ),
            Paragraph(
                f"{escape(customer_address)}",
                styles["Normal"],
            ),
        ],
    ]

    info_table = Table(
        information,
        colWidths=[90 * mm, 80 * mm],
    )

    info_table.setStyle(
        TableStyle(
            [
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "TOP",
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    8,
                ),
            ]
        )
    )

    story.append(info_table)

    story.append(
        Spacer(1, 15)
    )

    data = [
        [
            "Description",
            "Qty",
            "Unit Price",
            "Total",
        ]
    ]

    for item in invoice.items:
        data.append(
            [
                item.description,
                f"{item.quantity}",
                money_text(item.unit_price),
                money_text(item.line_total),
            ]
        )

    data.append(
        [
            "",
            "",
            "Subtotal",
            money_text(invoice.subtotal),
        ]
    )

    data.append(
        [
            "",
            "",
            "Tax",
            money_text(invoice.tax_total),
        ]
    )

    data.append(
        [
            "",
            "",
            "<b>Total</b>",
            f"<b>{money_text(invoice.total_amount)}</b>",
        ]
    )

    table = Table(
        data,
        colWidths=[
            80 * mm,
            25 * mm,
            35 * mm,
            35 * mm,
        ],
    )

    table.setStyle(
        TableStyle(
            [
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor("#16a34a"),
                ),
                (
                    "TEXTCOLOR",
                    (0, 0),
                    (-1, 0),
                    colors.white,
                ),
                (
                    "FONTNAME",
                    (0, 0),
                    (-1, 0),
                    "Helvetica-Bold",
                ),
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.5,
                    colors.grey,
                ),
                (
                    "ALIGN",
                    (1, 1),
                    (-1, -1),
                    "RIGHT",
                ),
                (
                    "BACKGROUND",
                    (-2, -3),
                    (-1, -1),
                    colors.HexColor("#f3f4f6"),
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    7,
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    7,
                ),
            ]
        )
    )

    story.append(table)

    story.append(
        Spacer(1, 20)
    )

    story.append(
        Paragraph(
            f"<b>Amount Paid:</b> "
            f"{money_text(invoice.amount_paid)}",
            styles["Normal"],
        )
    )

    remaining = (
        money(invoice.total_amount)
        - money(invoice.amount_paid)
    )

    story.append(
        Paragraph(
            f"<b>Balance Due:</b> "
            f"{money_text(remaining)}",
            styles["Normal"],
        )
    )

    document.build(story)

    return buffer.getvalue()


# ============================================================
# DASHBOARD
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse,
)
def dashboard(
    db: Session = Depends(get_db),
):

    refresh_overdue_invoices(db)

    total_revenue = money(
        db.query(
            func.sum(Invoice.total_amount)
        ).scalar()
    )

    total_collected = money(
        db.query(
            func.sum(Invoice.amount_paid)
        ).scalar()
    )

    total_receivable = (
        total_revenue - total_collected
    )

    invoices = (
        db.query(Invoice)
        .order_by(
            Invoice.created_at.desc()
        )
        .all()
    )

    customers = (
        db.query(Customer)
        .order_by(Customer.name.asc())
        .all()
    )

    rows = ""

    for invoice in invoices:

        if invoice.status == InvoiceStatus.PAID:
            color = "#16a34a"
            background = "#dcfce7"

        elif invoice.status == InvoiceStatus.OVERDUE:
            color = "#dc2626"
            background = "#fee2e2"

        elif invoice.status == InvoiceStatus.PARTIAL:
            color = "#d97706"
            background = "#fef3c7"

        else:
            color = "#2563eb"
            background = "#dbeafe"

        balance = (
            money(invoice.total_amount)
            - money(invoice.amount_paid)
        )

        payment_button = ""

        if invoice.status != InvoiceStatus.PAID:

            payment_button = f"""
            <button
                onclick="recordPayment('{escape(invoice.invoice_number)}', {balance})"
                style="
                    background:#16a34a;
                    width:auto;
                    padding:7px 12px;
                    margin-left:8px;
                "
            >
                Receive Payment
            </button>
            """

        rows += f"""
        <tr>
            <td>{escape(invoice.invoice_number)}</td>

            <td>
                {escape(invoice.customer.name)}
            </td>

            <td>
                {money_text(invoice.total_amount)}
                <br>
                <small>
                    Paid:
                    {money_text(invoice.amount_paid)}
                    <br>
                    Balance:
                    {money_text(balance)}
                </small>
            </td>

            <td>
                <span style="
                    color:{color};
                    background:{background};
                    padding:5px 9px;
                    border-radius:6px;
                    font-size:12px;
                    font-weight:bold;
                ">
                    {escape(invoice.status.value)}
                </span>

                {payment_button}

                <a
                    href="/api/invoices/{invoice.id}/pdf"
                    target="_blank"
                    style="
                        margin-left:8px;
                        color:#2563eb;
                        text-decoration:none;
                    "
                >
                    PDF
                </a>
            </td>
        </tr>
        """

    if not rows:
        rows = """
        <tr>
            <td
                colspan="4"
                style="
                    text-align:center;
                    padding:30px;
                    color:#777;
                "
            >
                No invoices yet.
            </td>
        </tr>
        """

    customer_options = ""

    for customer in customers:
        customer_options += f"""
        <option value="{customer.id}">
            {escape(customer.name)}
        </option>
        """

    today = datetime.now().strftime(
        "%B %d, %Y"
    )

    return f"""
<!DOCTYPE html>

<html>

<head>

<title>Invoice Manager</title>

<meta name="viewport"
      content="width=device-width, initial-scale=1">

<style>

* {{
    box-sizing:border-box;
}}

body {{
    margin:0;
    background:#f4f5f8;
    font-family:
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;
    color:#333;
}}

.nav {{
    background:white;
    padding:16px 30px;
    border-bottom:1px solid #ddd;
    display:flex;
    justify-content:space-between;
    align-items:center;
}}

.logo {{
    font-weight:bold;
    font-size:19px;
}}

.logo span {{
    background:#16a34a;
    color:white;
    padding:7px 10px;
    border-radius:6px;
    margin-right:8px;
}}

.container {{
    max-width:1200px;
    margin:30px auto;
    padding:0 20px;
}}

.cards {{
    display:grid;
    grid-template-columns:
        repeat(3, 1fr);
    gap:20px;
    margin-bottom:25px;
}}

.card {{
    background:white;
    border:1px solid #ddd;
    border-radius:10px;
    padding:22px;
}}

.card h2 {{
    margin:8px 0 0;
}}

.grid {{
    display:grid;
    grid-template-columns:
        1fr 1fr;
    gap:25px;
}}

.panel {{
    background:white;
    border:1px solid #ddd;
    border-radius:10px;
    overflow:hidden;
    margin-bottom:25px;
}}

.panel-title {{
    padding:15px 20px;
    background:#fafafa;
    border-bottom:1px solid #ddd;
    font-weight:bold;
}}

.panel-body {{
    padding:20px;
}}

input,
select,
button {{
    width:100%;
    padding:11px;
    margin:6px 0 12px;
    border:1px solid #ccc;
    border-radius:6px;
    font:inherit;
}}

button {{
    background:#16a34a;
    color:white;
    border:none;
    font-weight:bold;
    cursor:pointer;
}}

button:hover {{
    background:#15803d;
}}

table {{
    width:100%;
    border-collapse:collapse;
}}

th {{
    text-align:left;
    padding:12px;
    background:#fafafa;
    border-bottom:1px solid #ddd;
    font-size:12px;
}}

td {{
    padding:12px;
    border-bottom:1px solid #eee;
}}

.small {{
    color:#777;
    font-size:12px;
}}

@media(max-width:800px) {{

    .cards,
    .grid {{
        grid-template-columns:1fr;
    }}

    .nav {{
        padding:15px;
    }}

    .container {{
        padding:0 10px;
    }}

    table {{
        font-size:13px;
    }}

}}

</style>

</head>

<body>

<div class="nav">

    <div class="logo">
        <span>QB</span>
        Invoice Manager
    </div>

    <div class="small">
        {today}
    </div>

</div>

<div class="container">

    <div class="cards">

        <div class="card">
            <div class="small">
                GROSS REVENUE
            </div>
            <h2>
                {money_text(total_revenue)}
            </h2>
        </div>

        <div class="card">
            <div class="small">
                CASH COLLECTED
            </div>
            <h2 style="color:#16a34a">
                {money_text(total_collected)}
            </h2>
        </div>

        <div class="card">
            <div class="small">
                ACCOUNTS RECEIVABLE
            </div>
            <h2 style="color:#d97706">
                {money_text(total_receivable)}
            </h2>
        </div>

    </div>


    <div class="grid">

        <div>

            <div class="panel">

                <div class="panel-title">
                    Create Invoice
                </div>

                <div class="panel-body">

                    <form
                        action="/web/create"
                        method="post"
                    >

                        <label>
                            Customer Name
                        </label>

                        <input
                            type="text"
                            name="customer_name"
                            required
                        >

                        <label>
                            Customer Email
                        </label>

                        <input
                            type="email"
                            name="customer_email"
                            required
                        >

                        <label>
                            Phone
                        </label>

                        <input
                            type="text"
                            name="customer_phone"
                        >

                        <label>
                            Billing Address
                        </label>

                        <input
                            type="text"
                            name="billing_address"
                        >

                        <label>
                            Due Date
                        </label>

                        <input
                            type="date"
                            name="due_date"
                            required
                        >

                        <label>
                            Item Description
                        </label>

                        <input
                            type="text"
                            name="description"
                            required
                        >

                        <label>
                            Quantity
                        </label>

                        <input
                            type="number"
                            name="quantity"
                            value="1"
                            min="0.01"
                            step="0.01"
                            required
                        >

                        <label>
                            Unit Price
                        </label>

                        <input
                            type="number"
                            name="unit_price"
                            min="0"
                            step="0.01"
                            required
                        >

                        <label>
                            Tax %
                        </label>

                        <input
                            type="number"
                            name="tax_percent"
                            value="0"
                            min="0"
                            step="0.01"
                        >

                        <button type="submit">
                            Create Invoice
                        </button>

                    </form>

                </div>

            </div>


            <div class="panel">

                <div class="panel-title">
                    Excel Import
                </div>

                <div class="panel-body">

                    <p class="small">
                        Upload an Excel file containing
                        invoice information.
                    </p>

                    <form
                        action="/api/invoices/import-excel"
                        method="post"
                        enctype="multipart/form-data"
                    >

                        <input
                            type="file"
                            name="file"
                            accept=".xlsx,.xls"
                            required
                        >

                        <button type="submit">
                            Import Excel
                        </button>

                    </form>

                </div>

            </div>

        </div>


        <div>

            <div class="panel">

                <div class="panel-title">
                    Customers
                </div>

                <div class="panel-body">

                    <p class="small">
                        {len(customers)} customer(s)
                        registered.
                    </p>

                    <div
                        style="
                            max-height:250px;
                            overflow:auto;
                        "
                    >
                        {
                            "".join(
                                f"<div style='padding:8px 0;border-bottom:1px solid #eee;'>"
                                f"<b>{escape(c.name)}</b><br>"
                                f"<span class='small'>{escape(c.email)}</span>"
                                f"</div>"
                                for c in customers
                            )
                            or
                            "<div class='small'>No customers yet.</div>"
                        }
                    </div>

                </div>

            </div>

        </div>

    </div>


    <div class="panel">

        <div class="panel-title">
            Accounts Receivable
        </div>

        <div style="overflow-x:auto;">

            <table>

                <thead>

                    <tr>
                        <th>Invoice</th>
                        <th>Customer</th>
                        <th>Amount</th>
                        <th>Status</th>
                    </tr>

                </thead>

                <tbody>
                    {rows}
                </tbody>

            </table>

        </div>

    </div>

</div>


<script>

function recordPayment(invoiceNumber, balance) {{

    const amount = prompt(
        "Enter payment amount for "
        + invoiceNumber
        + "\\nBalance: $"
        + Number(balance).toFixed(2)
    );

    if (
        amount === null
        || amount.trim() === ""
    ) {{
        return;
    }}

    const value = Number(amount);

    if (
        !Number.isFinite(value)
        || value <= 0
    ) {{
        alert("Please enter a valid payment amount.");
        return;
    }}

    const form = document.createElement("form");

    form.method = "POST";
    form.action = "/api/payments";

    const invoice = document.createElement("input");
    invoice.name = "invoice_number";
    invoice.value = invoiceNumber;

    const payment = document.createElement("input");
    payment.name = "amount";
    payment.value = value;

    form.appendChild(invoice);
    form.appendChild(payment);

    document.body.appendChild(form);

    form.submit();
}}

</script>

</body>

</html>
"""


# ============================================================
# CREATE INVOICE
# ============================================================

@app.post("/web/create")
def create_invoice(
    customer_name: str = Form(...),
    customer_email: str = Form(...),
    customer_phone: str = Form(""),
    billing_address: str = Form(""),
    due_date: str = Form(...),
    description: str = Form(...),
    quantity: float = Form(...),
    unit_price: float = Form(...),
    tax_percent: float = Form(0),
    db: Session = Depends(get_db),
):

    try:
        parsed_due_date = datetime.strptime(
            due_date,
            "%Y-%m-%d",
        ).date()

        quantity_decimal = money(quantity)
        price_decimal = money(unit_price)
        tax_decimal = money(tax_percent)

        if quantity_decimal <= 0:
            raise HTTPException(
                status_code=400,
                detail="Quantity must be greater than zero.",
            )

        if price_decimal < 0:
            raise HTTPException(
                status_code=400,
                detail="Unit price cannot be negative.",
            )

        customer = (
            db.query(Customer)
            .filter(
                Customer.email == customer_email.strip().lower()
            )
            .first()
        )

        if customer is None:

            customer = Customer(
                name=customer_name.strip(),
                email=customer_email.strip().lower(),
                phone=customer_phone.strip() or None,
                billing_address=billing_address.strip() or None,
            )

            db.add(customer)
            db.flush()

        else:

            customer.name = customer_name.strip()

            if customer_phone.strip():
                customer.phone = customer_phone.strip()

            if billing_address.strip():
                customer.billing_address = (
                    billing_address.strip()
                )

        subtotal = (
            quantity_decimal * price_decimal
        ).quantize(Decimal("0.01"))

        tax_total = (
            subtotal * tax_decimal / Decimal("100")
        ).quantize(Decimal("0.01"))

        total = subtotal + tax_total

        invoice = Invoice(
            invoice_number=generate_invoice_number(db),
            customer=customer,
            issue_date=date.today(),
            due_date=parsed_due_date,
            subtotal=subtotal,
            tax_total=tax_total,
            total_amount=total,
            amount_paid=Decimal("0.00"),
            status=InvoiceStatus.UNPAID,
        )

        item = InvoiceItem(
            description=description.strip(),
            quantity=quantity_decimal,
            unit_price=price_decimal,
            line_total=subtotal,
        )

        invoice.items.append(item)

        db.add(invoice)
        db.commit()

        return HTMLResponse(
            f"""
            <html>
            <head>
                <meta http-equiv="refresh"
                      content="2;url=/">
            </head>
            <body style="
                font-family:system-ui;
                text-align:center;
                padding:60px;
            ">
                <h2 style="color:#16a34a;">
                    Invoice Created Successfully
                </h2>

                <p>
                    Invoice number:
                    <b>{escape(invoice.invoice_number)}</b>
                </p>

                <p>
                    Redirecting to dashboard...
                </p>
            </body>
            </html>
            """
        )

    except HTTPException:
        db.rollback()
        raise

    except Exception as exc:
        db.rollback()

        raise HTTPException(
            status_code=400,
            detail=f"Could not create invoice: {str(exc)}",
        )


# ============================================================
# PAYMENT
# ============================================================

@app.post("/api/payments")
def receive_payment(
    invoice_number: str = Form(...),
    amount: float = Form(...),
    db: Session = Depends(get_db),
):

    invoice = (
        db.query(Invoice)
        .filter(
            Invoice.invoice_number
            == invoice_number
        )
        .first()
    )

    if invoice is None:
        raise HTTPException(
            status_code=404,
            detail="Invoice not found.",
        )

    payment = money(amount)

    if payment <= 0:
        raise HTTPException(
            status_code=400,
            detail="Payment must be greater than zero.",
        )

    balance = (
        money(invoice.total_amount)
        - money(invoice.amount_paid)
    )

    if payment > balance:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Payment exceeds balance of "
                f"${balance:,.2f}."
            ),
        )

    invoice.amount_paid = (
        money(invoice.amount_paid)
        + payment
    )

    update_invoice_status(invoice)

    db.commit()

    return HTMLResponse(
        """
        <html>
        <head>
            <meta http-equiv="refresh"
                  content="1;url=/">
        </head>

        <body style="
            font-family:system-ui;
            text-align:center;
            padding:60px;
        ">

            <h2 style="color:#16a34a;">
                Payment Recorded
            </h2>

            <p>
                Returning to dashboard...
            </p>

        </body>
        </html>
        """
    )


# ============================================================
# PDF
# ============================================================

@app.get(
    "/api/invoices/{invoice_id}/pdf"
)
def invoice_pdf(
    invoice_id: int,
    db: Session = Depends(get_db),
):

    invoice = (
        db.query(Invoice)
        .filter(Invoice.id == invoice_id)
        .first()
    )

    if invoice is None:
        raise HTTPException(
            status_code=404,
            detail="Invoice not found.",
        )

    pdf = create_invoice_pdf(invoice)

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition":
                f'inline; filename="{invoice.invoice_number}.pdf"'
        },
    )


# ============================================================
# EMAIL INVOICE
# ============================================================

@app.post(
    "/api/invoices/{invoice_id}/email"
)
def email_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
):

    invoice = (
        db.query(Invoice)
        .filter(Invoice.id == invoice_id)
        .first()
    )

    if invoice is None:
        raise HTTPException(
            status_code=404,
            detail="Invoice not found.",
        )

    pdf = create_invoice_pdf(invoice)

    success = send_invoice_email(
        invoice.customer.email,
        invoice.customer.name,
        invoice.invoice_number,
        pdf,
    )

    if not success:
        raise HTTPException(
            status_code=500,
            detail=(
                "Email could not be sent. "
                "Check SMTP settings."
            ),
        )

    return {
        "success": True,
        "message": "Invoice emailed successfully.",
    }


# ============================================================
# EXCEL IMPORT
# ============================================================

@app.post(
    "/api/invoices/import-excel"
)
async def import_excel(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):

    filename = (
        file.filename or ""
    ).lower()

    if not filename.endswith(
        (".xlsx", ".xls")
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Please upload an Excel "
                "file (.xlsx or .xls)."
            ),
        )

    try:

        content = await file.read()

        dataframe = pd.read_excel(
            io.BytesIO(content)
        )

        if dataframe.empty:
            raise HTTPException(
                status_code=400,
                detail="The Excel file is empty.",
            )

        # Normalize column names.
        dataframe.columns = [
            str(column).strip().lower()
            .replace(" ", "_")
            for column in dataframe.columns
        ]

        required_columns = [
            "customer_name",
            "customer_email",
            "due_date",
            "description",
            "quantity",
            "unit_price",
        ]

        missing = [
            column
            for column in required_columns
            if column not in dataframe.columns
        ]

        if missing:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Missing Excel columns: "
                    + ", ".join(missing)
                ),
            )

        imported = 0

        for _, row in dataframe.iterrows():

            customer_name = str(
                row["customer_name"]
            ).strip()

            customer_email = str(
                row["customer_email"]
            ).strip().lower()

            description = str(
                row["description"]
            ).strip()

            if not customer_name or not customer_email:
                continue

            due_value = row["due_date"]

            if pd.isna(due_value):
                continue

            if isinstance(
                due_value,
                datetime,
            ):
                due_date = due_value.date()

            elif isinstance(
                due_value,
                date,
            ):
                due_date = due_value

            else:
                due_date = pd.to_datetime(
                    due_value
                ).date()

            quantity = money(
                row["quantity"]
            )

            unit_price = money(
                row["unit_price"]
            )

            tax_percent = money(
                row.get("tax_percent", 0)
            )

            if quantity <= 0:
                continue

            subtotal = (
                quantity * unit_price
            ).quantize(Decimal("0.01"))

            tax_total = (
                subtotal
                * tax_percent
                / Decimal("100")
            ).quantize(Decimal("0.01"))

            total = subtotal + tax_total

            customer = (
                db.query(Customer)
                .filter(
                    Customer.email
                    == customer_email
                )
                .first()
            )

            if customer is None:

                customer = Customer(
                    name=customer_name,
                    email=customer_email,
                )

                db.add(customer)
                db.flush()

            invoice = Invoice(
                invoice_number=
                    generate_invoice_number(db),
                customer=customer,
                issue_date=date.today(),
                due_date=due_date,
                subtotal=subtotal,
                tax_total=tax_total,
                total_amount=total,
                amount_paid=Decimal("0.00"),
                status=InvoiceStatus.UNPAID,
            )

            item = InvoiceItem(
                description=description,
                quantity=quantity,
                unit_price=unit_price,
                line_total=subtotal,
            )

            invoice.items.append(item)

            db.add(invoice)

            imported += 1

        db.commit()

        return HTMLResponse(
            f"""
            <html>
            <head>
                <meta http-equiv="refresh"
                      content="2;url=/">
            </head>

            <body style="
                font-family:system-ui;
                text-align:center;
                padding:60px;
            ">

                <h2 style="color:#16a34a;">
                    Excel Import Complete
                </h2>

                <p>
                    Imported
                    <b>{imported}</b>
                    invoice(s).
                </p>

                <p>
                    Returning to dashboard...
                </p>

            </body>
            </html>
            """
        )

    except HTTPException:
        db.rollback()
        raise

    except Exception as exc:
        db.rollback()

        raise HTTPException(
            status_code=400,
            detail=(
                "Excel import failed: "
                f"{str(exc)}"
            ),
        )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "application": "Invoice Manager",
        "version": "1.0.0",
    }
