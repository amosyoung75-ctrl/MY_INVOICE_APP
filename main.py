import io
import os
import smtplib
import pandas as pd
from datetime import datetime, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Response, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import create_engine, func, and_
from sqlalchemy.orm import Session, sessionmaker

# --- DATABASE ENGINE ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./invoices.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
app = FastAPI(title="Invoice Engine Engine")

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

    recent_invoices = db.query(Invoice).order_by(Invoice.created_at.desc()).all()
    customers_list = db.query(Customer).order_by(Customer.name.asc()).all()
    
    rows = ""
    for inv in recent_invoices:
        if inv.status == InvoiceStatus.PAID:
            badge_cls = "bg-green-50 text-green-700 ring-green-600/20"
        elif inv.status == InvoiceStatus.OVERDUE:
            badge_cls = "bg-red-50 text-red-700 ring-red-600/10"
        else:
            badge_cls = "bg-yellow-50 text-yellow-800 ring-yellow-600/15"
            
        pay_btn = ""
        if inv.status != InvoiceStatus.PAID:
            pay_btn = f"""
            <button onclick="openPaymentModal('{inv.invoice_number}', {inv.total_amount - inv.amount_paid})" class="ml-2 text-xs font-semibold text-emerald-600 hover:text-emerald-700 hover:underline">
                [Receive Payment]
            </button>
            """

        rows += f"""
        <tr class="hover:bg-gray-50 transition-colors duration-150">
            <td class="px-6 py-4 whitespace-nowrap text-sm font-semibold text-gray-900">{inv.invoice_number}</td>
            <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-600">{inv.customer.name}</td>
            <td class="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">${inv.total_amount:,.2f} <span class="text-xs text-gray-400 block">Paid: ${inv.amount_paid:,.2f}</span></td>
            <td class="px-6 py-4 whitespace-nowrap text-sm flex items-center">
                <span class="inline-flex items-center rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset {badge_cls}">{inv.status.value}</span>
                {pay_btn}
            </td>
        </tr>
        """

    customer_options = "".join([f"<option value='{c.id}'>{c.name} ({c.email})</option>" for c in customers_list])

    if not rows:
        rows = '<tr><td colspan="4" class="px-6 py-8 text-center text-sm text-gray-400">No records found. Run scripts or templates to populate.</td></tr>'

    return f"""
    <!DOCTYPE html>
    <html class="h-full bg-gray-50">
    <head>
        <title>QuickBooks Workspace Platform</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script src="https://tailwindcss.com"></script>
    </head>
    <body class="h-full font-sans antialiased text-gray-900">
        <div class="min-h-full">
            <!-- Navigation Header -->
            <nav class="bg-white border-b border-gray-200">
                <div class="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
                    <div class="flex h-16 justify-between items-center">
                        <div class="flex items-center space-x-3">
                            <div class="bg-emerald-600 p-2 rounded-lg text-white font-black text-xl tracking-tight">QB</div>
                            <span class="text-xl font-bold tracking-tight text-gray-900">QuickBooks Dashboard Console</span>
                        </div>
                        <div class="text-sm font-medium text-gray-500">{datetime.now().strftime('%B %d, %Y')}</div>
                    </div>
                </div>
            </nav>

            <main class="py-10">
                <div class="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
                    
                    <!-- QUICKBOOKS STYLE SHORTCUT ICONS BAR -->
                    <div class="bg-white border border-gray-200 rounded-xl shadow-sm p-6 mb-8">
                        <h3 class="text-xs font-bold uppercase tracking-wider text-gray-400 mb-4">Quick Access Navigation Actions</h3>
                        <div class="grid grid-cols-3 gap-4 text-center">
                            <a href="#billing-panel" class="group flex flex-col items-center p-4 rounded-xl border border-gray-100 bg-gray-50 hover:bg-emerald-50 hover:border-emerald-200 transition-all duration-150">
                                <div class="p-3 bg-emerald-600 text-white rounded-xl mb-2 shadow-sm group-hover:scale-105 transition-transform duration-150">
                                    <svg class="w-6 h-6" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                                </div>
                                <span class="text-sm font-bold text-gray-800">Create Invoice</span>
                            </a>
                            <a href="#soa-panel" class="group flex flex-col items-center p-4 rounded-xl border border-gray-100 bg-gray-50 hover:bg-sky-50 hover:border-sky-200 transition-all duration-150">
                                <div class="p-3 bg-sky-600 text-white rounded-xl mb-2 shadow-sm group-hover:scale-105 transition-transform duration-150">
                                    <svg class="w-6 h-6" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"></path></svg>
                                </div>
                                <span class="text-sm font-bold text-gray-800">Clients / Customers</span>
                            </a>
                            <div onclick="alert('Supplier Management tracking panel configuration is coming soon in v1.2 updates!')" class="cursor-pointer group flex flex-col items-center p-4 rounded-xl border border-gray-100 bg-gray-50 hover:bg-amber-50 hover:border-amber-200 transition-all duration-150">
                                <div class="p-3 bg-amber-500 text-white rounded-xl mb-2 shadow-sm group-hover:scale-105 transition-transform duration-150">
                                    <svg class="w-6 h-6" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4"></path></svg>
                                </div>
                                <span class="text-sm font-bold text-gray-800">Suppliers</span>
                            </div>
                        </div>
                    </div>

                    <!-- KPI Analytics Cards Grid -->
                    <div class="grid grid-cols-1 gap-5 sm:grid-cols-3 mb-10">
                        <div class="bg-white px-6 py-5 rounded-xl border border-gray-200 shadow-sm">
                            <dt class="text-sm font-medium text-gray-500 uppercase tracking-wider">Gross Revenue</dt>
                            <dd class="mt-1 text-3xl font-bold text-gray-900">${total_rev:,.2f}</dd>
                        </div>
                        <div class="bg-white px-6 py-5 rounded-xl border border-gray-200 shadow-sm">
                            <dt class="text-sm font-medium text-gray-500 uppercase tracking-wider">Total Collected</dt>
