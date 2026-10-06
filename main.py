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

# --- COMPACT SYNTAX-SAFE DASHBOARD PAGE ---
@app.get("/", response_class=HTMLResponse)
def render_dashboard(db: Session = Depends(get_db)):
    today = date.today()
    db.query(Invoice).filter(and_(Invoice.due_date < today, Invoice.status != InvoiceStatus.PAID)).update({"status": InvoiceStatus.OVERDUE})
    db.commit()

    kpis = db.query(func.sum(Invoice.total_amount).label("total"), func.sum(Invoice.amount_paid).label("paid")).first()
    total_rev = float(kpis.total or 0.0)
    total_col = float(kpis.paid or 0.0)
    total_rec = total_rev - total_col

    recent_invoices = db.query(Invoice).order_by(Invoice.created_at.desc()).all()
    customers_list = db.query(Customer).order_by(Customer.name.asc()).all()
    
    rows = ""
    for inv in recent_invoices:
        badge = "color:#d97706;background:#fef3c7;padding:4px 8px;border-radius:6px;"
        if inv.status == InvoiceStatus.PAID:
            badge = "color:#16a34a;background:#dcfce7;padding:4px 8px;border-radius:6px;"
        elif inv.status == InvoiceStatus.OVERDUE:
            badge = "color:#dc2626;background:#fee2e2;padding:4px 8px;border-radius:6px;"
            
        pay_btn = ""
        if inv.status != InvoiceStatus.PAID:
            rem = inv.total_amount - inv.amount_paid
            pay_btn = f"<button onclick='openPaymentModal(\"{inv.invoice_number}\", {rem})' style='margin-left:15px;background:none;color:#16a34a;border:none;cursor:pointer;font-weight:bold;'>[Receive Payment]</button>"

        rows += f"<tr style='border-bottom:1px solid #eee;'><td style='padding:12px;font-weight:bold;'>{inv.invoice_number}</td><td style='padding:12px;color:#555;'>{inv.customer.name}</td><td style='padding:12px;font-weight:bold;'>${inv.total_amount:,.2f}<br><span style='font-size:11px;color:#888;'>Paid: ${inv.amount_paid:,.2f}</span></td><td style='padding:12px;'><span style='{badge}'>{inv.status.value}</span>{pay_btn}</td></tr>"

    if not rows:
        rows = "<tr><td colspan='4' style='padding:30px;text-align:center;color:#999;'>No transactions found. Use the Excel tool or form below to start.</td></tr>"

    customer_options = "".join([f"<option value='{c.id}'>{c.name}</option>" for c in customers_list])
    current_date = datetime.now().strftime('%B %d, %Y')

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>QuickBooks Console</title>
        <style>
            body {{ font-family: system-ui, sans-serif; background: #f4f5f8; margin: 0; padding: 20px; color: #333; }}
            .nav {{ background: white; padding: 15px 30px; border-bottom: 1px solid #ddd; display: flex; justify-content: space-between; align-items: center; }}
            .logo {{ display: flex; align-items: center; gap: 10px; font-weight: bold; font-size: 18px; }}
            .qb {{ background: #16a34a; color: white; padding: 6px 12px; border-radius: 6px; font-black: true; }}
            .main {{ max-width: 1100px; margin: 30px auto; }}
            .shortcuts {{ background: white; border: 1px solid #ddd; padding: 20px; border-radius: 8px; margin-bottom: 25px; }}
            .grid-3 {{ display: flex; gap: 20px; margin-bottom: 25px; }}
            .card {{ flex: 1; background: white; border: 1px solid #ddd; padding: 20px; border-radius: 8px; }}
            .flex-container {{ display: flex; gap: 25px; align-items: start; }}
            .left-side {{ flex: 7; display: flex; flex-direction: column; gap: 25px; }}
            .right-side {{ flex: 5; display: flex; flex-direction: column; gap: 25px; }}
            .panel {{ background: white; border: 1px solid #ddd; border-radius: 8px; overflow: hidden; }}
            .title {{ padding: 12px 20px; border-bottom: 1px solid #ddd; font-weight: bold; background: #fafafa; }}
            .content {{ padding: 20px; }}
            table {{ width: 100%; border-collapse: collapse; text-align: left; }}
            th {{ background: #fafafa; padding: 10px; border-bottom: 1px solid #ddd; font-size: 12px; text-transform: uppercase; color: #666; }}
            input, select, button {{ width: 100%; padding: 10px; margin: 8px 0; border: 1px solid #ccc; border-radius: 6px; box-sizing: border-box; }}
            button {{ background: #16a34a; color: white; border: none; font-weight: bold; cursor: pointer; }}
            button:hover {{ background: #148a3e; }}
            .btn-lnk {{ display: inline-block; background: #e2e8f0; color: #333; text-decoration: none; text-align: center; font-weight: bold; padding: 12px 20px; border-radius: 6px; width: 28%; margin-right: 3%; }}
            .modal {{ display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.5); justify-content: center; align-items: center; }}
        </style>
    </head>
    <body>
        <div class="nav">
            <div class="logo"><div class="qb">QB</div> QuickBooks Workspace</div>
            <div style="color: #666;">{current_date}</div>
        </div>
        <div class="main">
            <div class="shortcuts">
                <h4 style="margin:0 0 15px 0;color:#666;font-size:12px;text-transform:uppercase;">Quick Access Actions</h4>
                <a href="#billing-panel" class="btn-lnk" style="background:#dcfce7;color:#15803d;">➕ Create Invoice</a>
                <a href="#soa-panel" class="btn-lnk" style="background:#e0f2fe;color:#0369a1;">👤 Customers / SOA</a>
                <div onclick="alert('Supplier Tracking coming soon!')" class="btn-lnk" style="background:#fef3c7;color:#b45309;cursor:pointer;">🏢 Suppliers</div>
            </div>
            <div class="grid-3">
                <div class="card"><small style="color:#777;">GROSS REVENUE</small><h2 style="margin:5px 0 0 0;">${total_rev:,.2f}</h2></div>
                <div class="card"><small style="color:#777;">CASH COLLECTED</small><h2 style="margin:5px 0 0 0;color:#16a34a;">${total_col:,.2f}</h2></div>
                <div class="card"><small style="color:#777;">ACCOUNTS RECEIVABLE</small><h2 style="margin:5px 0 0 0;color:#b45309;">${total_rec:,.2f}</h2></div>
            </div>
            <div class="flex-container">
                <div class="left-side">
                    <div class="panel">
                        <div class="title">Bulk Excel Import Ingestion</div>
                        <div class="content">
                            <form action="/api/invoices/import-excel" method="post" enctype="multipart/form-data">
                                <input type="file" name="file" accept=".xlsx, .xls" required onchange="this.form.submit()" />
                                <small style="color:#888;">System will automatically parse numbers upon file selection upload.</small>
                            </form>
                        </div>
                    </div>
                    <div class="panel">
                        <div class="title">Accounts Receivable Transaction Registry</div>
                        <table style="width:100%;">
                            <thead><tr><th>Invoice No</th><th>Customer</th><th>Amount Due</th><th>Status</th></tr></thead>
                            <tbody>{rows}</tbody>
                        </table>
                    </div>
                </div>
                <div class="right-side">
                    <div id="billing-panel" class="panel">
                        <div class="title">Billing Form Generator</div>
                        <form action="/web/create" method="post" style="padding:20px;">
                            <label>Customer Name</label><input type="text" name="customer_name" required />
                            <label>Customer Email</label><input type="email" name="customer_email" required />
                            <label>Due Date</label><input type="date" name="due_date" required />
                            <label>Item Description</label><input type="text" name="desc" required />
