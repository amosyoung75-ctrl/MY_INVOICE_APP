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

# --- FRONTEND VISUAL INTERFACE BASE TEMPLATE ---
DASHBOARD_HTML = """
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
        <nav class="bg-white border-b border-gray-200">
            <div class="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
                <div class="flex h-16 justify-between items-center">
                    <div class="flex items-center space-x-3">
                        <div class="bg-emerald-600 p-2 rounded-lg text-white font-black text-xl tracking-tight">QB</div>
                        <span class="text-xl font-bold tracking-tight text-gray-900">QuickBooks Dashboard Console</span>
                    </div>
                    <div class="text-sm font-medium text-gray-500">##CURRENT_DATE##</div>
                </div>
            </div>
        </nav>

        <main class="py-10">
            <div class="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
                
                <!-- QUICKACCESS NAVIGATION BAR -->
                <div class="bg-white border border-gray-200 rounded-xl shadow-sm p-6 mb-8">
                    <h3 class="text-xs font-bold uppercase tracking-wider text-gray-400 mb-4">Quick Access Navigation Actions</h3>
                    <div class="grid grid-cols-3 gap-4 text-center">
                        <a href="#billing-panel" class="group flex flex-col items-center p-4 rounded-xl border border-gray-100 bg-gray-50 hover:bg-emerald-50 hover:border-emerald-200 transition-all duration-150">
                            <div class="p-3 bg-emerald-600 text-white rounded-xl mb-2 shadow-sm">
                                <svg class="w-6 h-6" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                            </div>
                            <span class="text-sm font-bold text-gray-800">Create Invoice</span>
                        </a>
                        <a href="#soa-panel" class="group flex flex-col items-center p-4 rounded-xl border border-gray-100 bg-gray-50 hover:bg-sky-50 hover:border-sky-200 transition-all duration-150">
                            <div class="p-3 bg-sky-600 text-white rounded-xl mb-2 shadow-sm">
                                <svg class="w-6 h-6" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"></path></svg>
                            </div>
                            <span class="text-sm font-bold text-gray-800">Clients / Customers</span>
                        </a>
                        <div onclick="alert('Supplier Tracking coming soon in updates!')" class="cursor-pointer group flex flex-col items-center p-4 rounded-xl border border-gray-100 bg-gray-50 hover:bg-amber-50 hover:border-amber-200 transition-all duration-150">
                            <div class="p-3 bg-amber-500 text-white rounded-xl mb-2 shadow-sm">
                                <svg class="w-6 h-6" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4"></path></svg>
                            </div>
                            <span class="text-sm font-bold text-gray-800">Suppliers</span>
                        </div>
                    </div>
                </div>

                <!-- KPI METRICS -->
                <div class="grid grid-cols-1 gap-5 sm:grid-cols-3 mb-10">
                    <div class="bg-white px-6 py-5 rounded-xl border border-gray-200 shadow-sm">
                        <dt class="text-sm font-medium text-gray-500 uppercase tracking-wider">Gross Revenue</dt>
                        <dd class="mt-1 text-3xl font-bold text-gray-900">##TOTAL_REV##</dd>
                    </div>
                    <div class="bg-white px-6 py-5 rounded-xl border border-gray-200 shadow-sm">
                        <dt class="text-sm font-medium text-gray-500 uppercase tracking-wider">Total Collected</dt>
                        <dd class="mt-1 text-3xl font-bold text-emerald-600">##TOTAL_COL##</dd>
                    </div>
                    <div class="bg-white px-6 py-5 rounded-xl border border-gray-200 shadow-sm">
                        <dt class="text-sm font-medium text-gray-500 uppercase tracking-wider">Accounts Receivable</dt>
                        <dd class="mt-1 text-3xl font-bold text-amber-600">##TOTAL_REC##</dd>
                    </div>
                </div>

                <div class="grid grid-cols-1 gap-8 lg:grid-cols-12 items-start">
                    <div class="lg:col-span-7 space-y-8">
                        <div class="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
                            <div class="border-b border-gray-200 bg-gray-50/50 px-6 py-4">
                                <h3 class="text-base font-bold text-gray-900">Bulk Ingestion Pipeline</h3>
                            </div>
                            <div class="p-6">
                                <form action="/api/invoices/import-excel" method="post" enctype="multipart/form-data">
                                    <input type="file" name="file" accept=".xlsx, .xls" class="block w-full border border-gray-300 rounded-lg p-2 text-sm bg-gray-50 mb-4" required onchange="this.form.submit()" />
                                    <p class="text-xs text-gray-400">App auto-submits upon spreadsheet insertion selection configuration.</p>
                                </form>
                            </div>
                        </div>

                        <div class="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
                            <div class="border-b border-gray-200 bg-gray-50/50 px-6 py-4">
                                <h3 class="text-base font-bold text-gray-900">Live Accounts Ledger Registry</h3>
                            </div>
                            <div class="overflow-x-auto">
                                <table class="min-w-full divide-y divide-gray-200">
                                    <thead class="bg-gray-50/70 text-left">
                                        <tr>
                                            <th class="px-6 py-3 text-xs font-bold text-gray-500 uppercase tracking-wider">Invoice</th>
                                            <th class="px-6 py-3 text-xs font-bold text-gray-500 uppercase tracking-wider">Customer</th>
                                            <th class="px-6 py-3 text-xs font-bold text-gray-500 uppercase tracking-wider">Amount Due</th>
                                            <th class="px-6 py-3 text-xs font-bold text-gray-500 uppercase tracking-wider">Status</th>
                                        </tr>
                                    </table>
                                    <table class="min-w-full divide-y divide-gray-100 text-left bg-white">
                                        <tbody>##TABLE_ROWS##</tbody>
