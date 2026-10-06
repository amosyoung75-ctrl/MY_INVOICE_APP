import io
import os
import smtplib
import pandas as pd
from datetime import datetime, date
from typing import List
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Response, Form
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import create_engine, func, and_
from sqlalchemy.orm import Session, sessionmaker

# --- DATABASE SETUP (AUTO-SWITCHES TO POSTGRES) ---
# Automatically pulls the Render Postgres database link if present, defaults to SQLite
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./invoices.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="QuickBooks-Style Invoice Engine")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

from models import Base, Customer, Invoice, InvoiceItem, InvoiceStatus
Base.metadata.create_all(bind=engine)

# --- AUTOMATED EMAIL DELIVERY SYSTEM ---
def send_invoice_email(to_email: str, customer_name: str, invoice_number: str, pdf_bytes: bytes):
    smtp_server = os.getenv("SMTP_SERVER", "://gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    sender_email = os.getenv("SENDER_EMAIL")
    sender_password = os.getenv("SENDER_PASSWORD")

    if not sender_email or not sender_password:
        print("Email settings missing. Skipping live delivery.")
        return False

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = to_email
    msg['Subject'] = f"New Invoice {invoice_number} from Your Company"

    body = f"Hello {customer_name},\n\nPlease find attached your invoice {invoice_number}.\n\nThank you for your business!"
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
    except Exception as e:
        print(f"SMTP Error: {e}")
        return False

# --- 3. FULLY DESIGNED VISUAL FRONTEND (HOMEPAGE) ---
@app.get("/", response_class=HTMLResponse)
def render_dashboard(db: Session = Depends(get_db)):
    today = date.today()
    
    # Auto-update overdue records
    db.query(Invoice).filter(and_(Invoice.due_date < today, Invoice.status != InvoiceStatus.PAID)).update({"status": InvoiceStatus.OVERDUE})
    db.commit()

    # Metrics
    kpis = db.query(func.sum(Invoice.total_amount).label("total"), func.sum(Invoice.amount_paid).label("paid")).first()
    total_revenue = float(kpis.total or 0.0)
    total_collected = float(kpis.paid or 0.0)
    total_receivable = total_revenue - total_collected

    # Recent activity list
    recent_invoices = db.query(Invoice).order_by(Invoice.created_at.desc()).limit(5).all()
    rows = "".join([
        f"<tr><td>{inv.invoice_number}</td><td>{inv.customer.name}</td><td>${inv.total_amount:,.2f}</td><td><span class='badge {inv.status.value.lower()}'>{inv.status.value}</span></td></tr>" 
        for inv in recent_invoices
    ])

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>QuickBooks Style Dashboard</title>
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f4f5f8; margin: 0; padding: 30px; color: #333; }}
            .container {{ max-width: 1100px; margin: 0 auto; }}
            .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; }}
            h1 {{ color: #2ca01c; margin: 0; }}
            .stats {{ display: flex; gap: 20px; margin-bottom: 30px; }}
            .card {{ flex: 1; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); border-left: 5px solid #2ca01c; }}
            .card.unpaid {{ border-left-color: #ef5350; }}
            .card.receivable {{ border-left-color: #ffb300; }}
            .card h3 {{ margin: 0 0 10px 0; color: #666; font-size: 14px; text-transform: uppercase; }}
            .card div {{ font-size: 24px; font-weight: bold; }}
            .workspace {{ display: flex; gap: 30px; }}
            .panel {{ flex: 1; background: white; padding: 25px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }}
            h2 {{ margin-top: 0; color: #444; border-bottom: 2px solid #f4f5f8; padding-bottom: 10px; }}
            label {{ display: block; margin: 12px 0 6px 0; font-weight: 600; font-size: 14px; }}
            input, select {{ width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; }}
            button {{ background: #2ca01c; color: white; border: none; padding: 12px 20px; font-size: 15px; font-weight: bold; border-radius: 4px; cursor: pointer; width: 100%; margin-top: 15px; }}
            button:hover {{ background: #248216; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
            th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #eee; }}
            th {{ background: #f8f9fa; }}
            .badge {{ padding: 4px 8px; border-radius: 12px; font-size: 12px; font-weight: bold; }}
            .badge.paid {{ background: #e8f5e9; color: #2e7d32; }}
            .badge.unpaid {{ background: #ffebee; color: #c62828; }}
            .badge.overdue {{ background: #fff8e1; color: #f57f17; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Invoice Management Workspace</h1>
            </div>
            
            <div class="stats">
                <div class="card"><h3>Total Revenue</h3><div>${total_revenue:,.2f}</div></div>
                <div class="card unpaid"><h3>Total Collected</h3><div>${total_collected:,.2f}</div></div>
                <div class="card receivable"><h3>Total Accounts Receivable</h3><div>${total_receivable:,.2f}</div></div>
            </div>

            <div class="workspace">
                <div class="panel">
                    <h2>Import Bulk Excel Data</h2>
                    <form action="/api/invoices/import-excel" method="post" enctype="multipart/form-data">
                        <label>Select Spreadsheet File (.xlsx)</label>
                        <input type="file" name="file" accept=".xlsx, .xls" required />
                        <button type="submit">Upload & Process Engine</button>
                    </form>
                    
                    <h2 style="margin-top:40px;">Recent Activity</h2>
                    <table>
                        <thead><tr><th>Invoice</th><th>Customer</th><th>Amount</th><th>Status</th></tr></thead>
                        <tbody>{rows}</tbody>
                    </table>
                </div>

                <div class="panel">
                    <h2>Quick Bill Generator</h2>
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

# --- WEB FORM HANDLER WITH INTEGRATED PDF AND EMAIL ---
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

    # Generate custom PDF invoice structure dynamically
    html_template = f"<html><body><h1>Invoice {invoice_num}</h1><p>Customer: {customer_name}</p><p>Total Due: ${total:,.2f}</p></body></html>"
    pdf_bytes = HTML(string=html_template).write_pdf()

    # Triggers automatic emailing function instantly on submission
    send_invoice_email(customer_email, customer_name, invoice_num, pdf_bytes)

    return Response("<script>alert('Invoice created and email step processed!'); window.location.href='/';</script>", media_type="text/html")

# --- KEEP THE REST OF EXCEL ENDPOINTS LIVE ---
@app.post("/api/invoices/import-excel")
