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

# --- COMPACT SYNTAX-SAFE HTML LAYOUT STRUCTURE ---
def get_dashboard_html():
    html_segments = [
        '<!DOCTYPE html><html class="h-full bg-gray-50"><head>',
        '<title>QuickBooks Workspace Platform</title><meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        '<script src="https://tailwindcss.com"></script></head>',
        '<body class="h-full font-sans antialiased text-gray-900 bg-gray-50">',
        '<nav class="bg-white border-b border-gray-200"><div class="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">',
        '<div class="flex h-16 justify-between items-center"><div class="flex items-center space-x-3">',
        '<div class="bg-emerald-600 p-2 rounded-lg text-white font-black text-xl">QB</div>',
        '<span class="text-xl font-bold text-gray-900">QuickBooks Dashboard Console</span>',
        '</div><div class="text-sm font-medium text-gray-500">##CURRENT_DATE##</div></div></div></nav>',
        '<main class="py-10"><div class="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">',
        '<div class="bg-white border border-gray-200 rounded-xl shadow-sm p-6 mb-8">',
        '<h3 class="text-xs font-bold uppercase tracking-wider text-gray-400 mb-4">Quick Access Navigation Actions</h3>',
        '<div class="grid grid-cols-3 gap-4 text-center">',
        '<a href="#billing-panel" class="flex flex-col items-center p-4 rounded-xl border border-gray-100 bg-gray-50 hover:bg-emerald-50">',
        '<span class="text-sm font-bold text-gray-800">➕ Create Invoice</span></a>',
        '<a href="#soa-panel" class="flex flex-col items-center p-4 rounded-xl border border-gray-100 bg-gray-50 hover:bg-sky-50">',
        '<span class="text-sm font-bold text-gray-800">👤 Clients / Customers</span></a>',
        '<div onclick="alert(\'Supplier feature coming in v1.2!\')" class="cursor-pointer flex flex-col items-center p-4 rounded-xl border border-gray-100 bg-gray-50 hover:bg-amber-50">',
        '<span class="text-sm font-bold text-gray-800">🏢 Suppliers</span></div></div></div>',
        '<div class="grid grid-cols-1 gap-5 sm:grid-cols-3 mb-10">',
        '<div class="bg-white px-6 py-5 rounded-xl border border-gray-200 shadow-sm"><dt class="text-sm font-medium text-gray-500 uppercase">Gross Revenue</dt><dd class="mt-1 text-3xl font-bold text-gray-900">##TOTAL_REV##</dd></div>',
        '<div class="bg-white px-6 py-5 rounded-xl border border-gray-200 shadow-sm"><dt class="text-sm font-medium text-gray-500 uppercase">Total Collected</dt><dd class="mt-1 text-3xl font-bold text-emerald-600">##TOTAL_COL##</dd></div>',
        '<div class="bg-white px-6 py-5 rounded-xl border border-gray-200 shadow-sm"><dt class="text-sm font-medium text-gray-500 uppercase">Accounts Receivable</dt><dd class="mt-1 text-3xl font-bold text-amber-600">##TOTAL_REC##</dd></div>',
        '</div><div class="grid grid-cols-1 gap-8 lg:grid-cols-12 items-start"><div class="lg:col-span-7 space-y-8">',
        '<div class="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden"><div class="border-b border-gray-200 bg-gray-50/50 px-6 py-4"><h3 class="text-base font-bold text-gray-900">Bulk Ingestion Pipeline</h3></div>',
        '<div class="p-6"><form action="/api/invoices/import-excel" method="post" enctype="multipart/form-data">',
        '<input type="file" name="file" accept=".xlsx, .xls" class="block w-full border border-gray-300 rounded-lg p-2 text-sm bg-gray-50 mb-4" required onchange="this.form.submit()" />',
        '</form></div></div><div class="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">',
        '<div class="border-b border-gray-200 bg-gray-50/50 px-6 py-4"><h3 class="text-base font-bold text-gray-900">Live Accounts Ledger Registry</h3></div>',
        '<div class="overflow-x-auto"><table class="min-w-full divide-y divide-gray-200"><thead class="bg-gray-50/70 text-left">',
        '<tr><th class="px-6 py-3 text-xs font-bold text-gray-500 uppercase">Invoice</th><th class="px-6 py-3 text-xs font-bold text-gray-500 uppercase">Customer</th><th class="px-6 py-3 text-xs font-bold text-gray-500 uppercase">Amount Due</th><th class="px-6 py-3 text-xs font-bold text-gray-500 uppercase">Status</th></tr>',
        '</thead><tbody class="bg-white divide-y divide-gray-100">##TABLE_ROWS##</tbody></table></div></div></div>',
        '<div class="lg:col-span-5 space-y-8"><div id="billing-panel" class="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">',
        '<div class="border-b border-gray-200 bg-gray-50/50 px-6 py-4"><h3 class="text-base font-bold text-gray-900">Billing Engine Form</h3></div>',
        '<form action="/web/create" method="post" class="p-6 space-y-4">',
        '<div><label class="block text-xs font-bold uppercase text-gray-600 mb-1">Customer Account Name</label><input type="text" name="customer_name" class="block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm" required /></div>',
        '<div><label class="block text-xs font-bold uppercase text-gray-600 mb-1">Notification Email</label><input type="email" name="customer_email" class="block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm" required /></div>',
        '<div><label class="block text-xs font-bold uppercase text-gray-600 mb-1">Settlement Due Date</label><input type="date" name="due_date" class="block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm" required /></div>',
        '<div><label class="block text-xs font-bold uppercase text-gray-600 mb-1">Line Description</label><input type="text" name="desc" class="block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm" required /></div>',
        '<div class="grid grid-cols-2 gap-4"><div><label class="block text-xs font-bold uppercase text-gray-600 mb-1">Volume Qty</label><input type="number" name="qty" value="1" step="0.01" class="block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm" required /></div>',
        '<div><label class="block text-xs font-bold uppercase text-gray-600 mb-1">Unit Valuation ($)</label><input type="number" name="price" step="0.01" class="block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm" required /></div></div>',
        '<button type="submit" class="w-full rounded-lg bg-emerald-600 px-4 py-2.5 text-sm font-bold text-white hover:bg-emerald-500">Compile & Dispatch Invoice</button></form></div>',
        '<div id="soa-panel" class="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden"><div class="border-b border-gray-200 bg-gray-50/50 px-6 py-4"><h3 class="text-base font-bold text-gray-900">Statement of Account (SOA)</h3></div>',
        '<form action="/web/soa" method="get" class="p-6 space-y-4"><div><label class="block text-xs font-bold uppercase text-gray-600 mb-1">Select Client Entity Target</label>',
        '<select name="customer_id" class="block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm" required><option value="">-- Choose Active Client --</option>##CUSTOMER_OPTIONS##</select></div>',
        '<button type="submit" class="w-full rounded-lg bg-sky-600 px-4 py-2.5 text-sm font-bold text-white hover:bg-sky-500">Compile Statement Report View</button></form></div></div></div></div></main></div>',
        '<div id="payment-modal" class="hidden fixed inset-0 bg-gray-500 bg-opacity-75 flex items-center justify-center p-4">',
        '<div class="bg-white rounded-xl overflow-hidden shadow-xl max-w-md w-full p-6"><h3 class="text-lg font-bold text-gray-900 mb-2">Process Ledger Cash Collection</h3>',
        '<form action="/web/pay" method="post" class="space-y-4"><input type="hidden" id="modal-invoice-number" name="invoice_number" />',
