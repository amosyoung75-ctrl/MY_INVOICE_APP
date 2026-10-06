import io
import os
import smtplib
import pandas as pd
from datetime import datetime, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Response, Form
from fastapi.responses import HTMLResponse
from sqlalchemy import create_engine, func, and_
from sqlalchemy.orm import Session, sessionmaker

# --- DATABASE ENGINE ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./invoices.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
app = FastAPI(title="Invoice Engine")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

from models import Base, Customer, Invoice, InvoiceItem, InvoiceStatus
Base.metadata.create_all(bind=engine)

# --- EMAIL SYSTEM ---
def send_invoice_email(to_email: str, customer_name: str, invoice_number: str, pdf_bytes: bytes):
    smtp_server = os.getenv("SMTP_SERVER", "://gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    sender_email = os.getenv("SENDER_EMAIL")
    sender_password = os.getenv("SENDER_PASSWORD")

    if not sender_email or not sender_password:
        return False

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = to_email
    msg['Subject'] = f"New Invoice {invoice_number}"

    body = f"Hello {customer_name},\n\nPlease find attached invoice {invoice_number}.\n\nThank you!"
    msg.attach(MIMEText(body, 'plain'))

    part = MIMEApplication(pdf_bytes, Name=f"{invoice_number}.pdf")
    part['Content-Disposition'] = f'attachment; filename="{invoice_number}.pdf"'
    msg.attach(part)

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception:
        return False

# --- FRONTEND WEBPAGE ---
@app.get("/", response_class=HTMLResponse)
def render_dashboard(db: Session = Depends(get_db)):
    today = date.today()
    db.query(Invoice).filter(and_(Invoice.due_date < today, Invoice.status != InvoiceStatus.PAID)).update({"status": InvoiceStatus.OVERDUE})
    db.commit()

    kpis = db.query(func.sum(Invoice.total_amount).label("total"), func.sum(Invoice.amount_paid).label("paid")).first()
    total_rev = float(kpis.total or 0.0)
    total_col = float(kpis.paid or 0.0)
    total_rec = total_rev - total_col

    recent = db.query(Invoice).order_by(Invoice.created_at.desc()).limit(5).all()
    rows = "".join([
        f"<tr><td>{inv.invoice_number}</td><td>{inv.customer.name}</td><td>${inv.total_amount:,.2f}</td><td>{inv.status.value}</td></tr>" 
        for inv in recent
    ])

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Invoice Hub</title>
        <style>
            body {{ font-family: sans-serif; background: #f4f5f8; margin: 0; padding: 30px; }}
            .box {{ max-width: 1000px; margin: 0 auto; background: white; padding: 25px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }}
            h1 {{ color: #2ca01c; }}
            .grid {{ display: flex; gap: 20px; margin: 20px 0; }}
            .card {{ flex: 1; padding: 15px; background: #fafafa; border-radius: 4px; border-left: 4px solid #2ca01c; }}
            .split {{ display: flex; gap: 30px; margin-top: 30px; }}
            .side {{ flex: 1; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
            th, td {{ padding: 10px; border-bottom: 1px solid #eee; text-align: left; }}
            input, button {{ width: 100%; padding: 10px; margin: 6px 0; box-sizing: border-box; }}
            button {{ background: #2ca01c; color: white; border: none; font-weight: bold; cursor: pointer; }}
        </style>
    </head>
    <body>
        <div class="box">
            <h1>Invoice Manager Workspace</h1>
            <div class="grid">
                <div class="card"><h4>Total Revenue</h4><h3>${total_rev:,.2f}</h3></div>
                <div class="card"><h4>Total Collected</h4><h3>${total_col:,.2f}</h3></div>
                <div class="card"><h4>Accounts Receivable</h4><h3>${total_rec:,.2f}</h3></div>
            </div>
            <div class="split">
                <div class="side">
                    <h3>Import Bulk Excel Data</h3>
                    <form action="/api/invoices/import-excel" method="post" enctype="multipart/form-data">
                        <input type="file" name="file" accept=".xlsx, .xls" required />
                        <button type="submit">Upload Spreadsheet</button>
                    </form>
                    <h3 style="margin-top:30px;">Recent Activity</h3>
                    <table>
                        <thead><tr><th>Invoice</th><th>Customer</th><th>Amount</th><th>Status</th></tr></thead>
                        <tbody>{rows}</tbody>
                    </table>
                </div>
                <div class="side">
                    <h3>Quick Bill Generator</h3>
                    <form action="/web/create" method="post">
                        <label>Customer Name</label><input type="text" name="customer_name" required />
                        <label>Customer Email</label><input type="email" name="customer_email" required />
                        <label>Due Date</label><input type="date" name="due_date" required />
                        <label>Item Description</label><input type="text" name="desc" required />
                        <label>Quantity</label><input type="number" name="qty" value="1" step="0.01" required />
                        <label>Unit Price ($)</label><input type="number" name="price" step="0.01" required />
                        <button type="submit" style="background:#0077c5;">Create & Email Client</button>
                    </form>
                </div>
            </div>
        </div>
    </body>
    </html>
    """

# --- INVOICE CREATION HANDLER ---
@app.post("/web/create")
def web_create_invoice(
    customer_name: str = Form(...), customer_email: str = Form(...), due_date: str = Form(...),
    desc: str = Form(...), qty: float = Form(...), price: float = Form(...), db: Session = Depends(get_db)
):
    from weasyprint import HTML

    customer = db.query(Customer).filter(Customer.email == customer_email).first()
    if not customer:
        customer = Customer(name=customer_name, email=customer_email)
        db.add(customer)
        db.flush()

    invoice_num = f"INV-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    total = qty * price

    inv = Invoice(
        invoice_number=invoice_num, customer_id=customer.id, due_date=datetime.strptime(due_date, "%Y-%m-%d").date(),
        subtotal=total, tax_total=0.0, total_amount=total, amount_paid=0.0, status=InvoiceStatus.UNPAID
    )
    db.add(inv)
    db.flush()

    item = InvoiceItem(invoice_id=inv.id, description=desc, quantity=qty, unit_price=price, line_total=total)
    db.add(item)
    db.commit()

    html_template = f"<html><body><h1>Invoice {invoice_num}</h1><p>Customer: {customer_name}</p><p>Total Due: ${total:,.2f}</p></body></html>"
    pdf_bytes = HTML(string=html_template).write_pdf()

    send_invoice_email(customer_email, customer_name, invoice_num, pdf_bytes)
    return Response("<script>alert('Invoice created successfully!'); window.location.href='/';</script>", media_type="text/html")

# --- EXCEL INGESTION ENGINE ---
@app.post("/api/invoices/import-excel")
async def import_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    contents = await file.read()
    df = pd.read_excel(io.BytesIO(contents))
    for _, row in df.iterrows():
        customer = db.query(Customer).filter(Customer.email == row['customer_email']).first()
        if not customer:
            customer = Customer(name=row['customer_name'], email=row['customer_email'])
            db.add(customer)
            db.flush()
        if db.query(Invoice).filter(Invoice.invoice_number == str(row['invoice_number'])).first():
            continue
        due_dt = pd.to_datetime(row['due_date']).date()
        total = float(row['total_amount'])
        inv = Invoice(
            invoice_number=str(row['invoice_number']), customer_id=customer.id, due_date=due_dt,
            subtotal=total, tax_total=0.0, total_amount=total, amount_paid=float(row.get('amount_paid', 0.0)),
            status=InvoiceStatus.PAID if float(row.get('amount_paid', 0.0)) >= total else InvoiceStatus.UNPAID
        )
        db.add(inv)
    db.commit()
    return Response("<script>alert('Excel records imported successfully!'); window.location.href='/';</script>", media_type="text/html")
