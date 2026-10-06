import io
import pandas as pd
from datetime import datetime, date, timedelta
from typing import List, Optional
from enum import Enum

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import create_engine, select, func, and_, or_
from sqlalchemy.orm import Session, sessionmaker

# --- DATABASE SETUP ---
DATABASE_URL = "sqlite:///./invoices.db"  # Swap with postgresql:// for production
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="QuickBooks-Style Invoice API Engine", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- REUSE THE DATABASE MODELS DEFINED IN PRIOR TURNS ---
# Base, Customer, Invoice, InvoiceItem, InvoiceStatus are applied here
from models import Base, Customer, Invoice, InvoiceItem, InvoiceStatus
Base.metadata.create_all(bind=engine)

# --- PYDANTIC SCHEMAS ---
class ItemCreate(BaseModel):
    description: str
    quantity: float = Field(gt=0)
    unit_price: float = Field(gt=0)

class InvoiceCreate(BaseModel):
    customer_email: EmailStr
    customer_name: str
    due_date: date
    items: List[ItemCreate]
    tax_rate: float = Field(default=0.0, ge=0)

# --- 1. CORE INVOICE & CUSTOMER CREATION ENDPOINTS ---

@app.post("/api/invoices", response_model=dict)
def create_invoice(payload: InvoiceCreate, db: Session = Depends(get_db)):
    # Resolve or provision customer automatically
    customer = db.query(Customer).filter(Customer.email == payload.customer_email).first()
    if not customer:
        customer = Customer(name=payload.customer_name, email=payload.customer_email)
        db.add(customer)
        db.flush()

    # Create distinct sequential system tracking code
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    invoice_num = f"INV-{timestamp}"

    subtotal = 0.0
    invoice_items = []
    
    for item in payload.items:
        line_total = item.quantity * item.unit_price
        subtotal += line_total
        invoice_items.append(InvoiceItem(
            description=item.description,
            quantity=item.quantity,
            unit_price=item.unit_price,
            line_total=line_total
        ))

    tax_total = subtotal * (payload.tax_rate / 100)
    total_amount = subtotal + tax_total

    new_invoice = Invoice(
        invoice_number=invoice_num,
        customer_id=customer.id,
        due_date=payload.due_date,
        subtotal=subtotal,
        tax_total=tax_total,
        total_amount=total_amount,
        amount_paid=0.0,
        status=InvoiceStatus.UNPAID,
        items=invoice_items
    )
    
    db.add(new_invoice)
    db.commit()
    return {"message": "Invoice built successfully", "invoice_number": invoice_num}

# --- 2. THE DASHBOARD AGING & KPI ENGINE ---

@app.get("/api/dashboard/metrics")
def get_dashboard_metrics(db: Session = Depends(get_db)):
    today = date.today()
    
    # Refresh systemic overdue statuses prior to compiling reports
    db.query(Invoice).filter(
        and_(Invoice.due_date < today, Invoice.status != InvoiceStatus.PAID)
    ).update({"status": InvoiceStatus.OVERDUE})
    db.commit()

    # Compute high-level visual matrix KPIs
    kpis = db.query(
        func.sum(Invoice.total_amount).label("total"),
        func.sum(Invoice.amount_paid).label("paid")
    ).first()
    
    total_revenue = float(kpis.total or 0.0)
    total_collected = float(kpis.paid or 0.0)
    total_receivable = total_revenue - total_collected

    # Compile the 30-60-90 Day Accounts Receivable Aging Report
    aging_buckets = {"current": 0.0, "30_days": 0.0, "60_days": 0.0, "90_plus": 0.0}
    overdue_invoices = db.query(Invoice).filter(Invoice.status == InvoiceStatus.OVERDUE).all()
    
    for inv in overdue_invoices:
        days_past = (today - inv.due_date).days
        unpaid_amt = float(inv.total_amount - inv.amount_paid)
        if days_past <= 30:
            aging_buckets["30_days"] += unpaid_amt
        elif days_past <= 60:
            aging_buckets["60_days"] += unpaid_amt
        elif days_past <= 90:
            aging_buckets["90_days"] += unpaid_amt
        else:
            aging_buckets["90_plus"] += unpaid_amt

    return {
        "summary": {
            "total_revenue": total_revenue,
            "total_collected": total_collected,
            "total_receivable": total_receivable
        },
        "aging_report": aging_buckets
    }

# --- 3. EXCEL FILE INGESTION PIPELINE ---

@app.post("/api/invoices/import-excel")
async def import_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="Invalid layout. System strictly accepts Excel extensions.")
    
    contents = await file.read()
    df = pd.read_excel(io.BytesIO(contents))
    
    # Dynamic ingestion validation framework
    required = ['invoice_number', 'customer_name', 'customer_email', 'due_date', 'total_amount']
    if not all(col in df.columns for col in required):
         raise HTTPException(status_code=400, detail=f"Missing structural parameters. Required layout columns: {required}")
    
    imported_count = 0
    for _, row in df.iterrows():
        customer = db.query(Customer).filter(Customer.email == row['customer_email']).first()
        if not customer:
            customer = Customer(name=row['customer_name'], email=row['customer_email'])
            db.add(customer)
            db.flush()

        if db.query(Invoice).filter(Invoice.invoice_number == str(row['invoice_number'])).first():
            continue  # Keep data integrity pristine by ignoring historical line duplication

        due_dt = pd.to_datetime(row['due_date']).date()
        total = float(row['total_amount'])
        paid = float(row.get('amount_paid', 0.0))
        status = InvoiceStatus.PAID if paid >= total else (InvoiceStatus.OVERDUE if due_dt < date.today() else InvoiceStatus.UNPAID)

        inv = Invoice(
            invoice_number=str(row['invoice_number']),
            customer_id=customer.id,
            due_date=due_dt,
            subtotal=total,
            tax_total=0.0,
            total_amount=total,
            amount_paid=paid,
            status=status
        )
        db.add(inv)
        imported_count += 1
        
    db.commit()
    return {"status": "Success", "records_processed": imported_count}

# --- 4. PIXEL-PERFECT PDF GENERATION FOR DOWNLOADS ---

@app.get("/api/invoices/{invoice_id}/pdf")
def generate_invoice_pdf(invoice_id: int, db: Session = Depends(get_db)):
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice entry not identified.")
        
    # Lazy import WeasyPrint to keep microcontainer light during standard queries
    from weasyprint import HTML
    
    # Inline functional styling template matching QuickBooks' clean interface
    html_template = f"""
    <html>
    <head>
        <style>
            body {{ font-family: 'Helvetica Neue', Arial, sans-serif; padding: 30px; color: #333; }}
            .header {{ display: flex; justify-content: space-between; border-bottom: 2px solid #2ca01c; padding-bottom: 20px; }}
            .company-title {{ font-size: 28px; font-weight: bold; color: #2ca01c; }}
            .details-table {{ width: 100%; margin-top: 40px; border-collapse: collapse; }}
            .details-table th {{ background-color: #f4f5f8; padding: 12px; text-align: left; font-size: 14px; border-bottom: 2px solid #ddd; }}
            .details-table td {{ padding: 12px; border-bottom: 1px solid #eee; font-size: 13px; }}
            .totals {{ float: right; width: 30%; margin-top: 30px; font-size: 14px; }}
            .totals-row {{ display: flex; justify-content: space-between; padding: 6px 0; }}
            .grand-total {{ font-size: 18px; font-weight: bold; color: #2ca01c; border-top: 1px solid #ddd; padding-top: 8px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <div>
                <div class="company-title">INVOICE</div>
                <p><strong>To:</strong> {invoice.customer.name} ({invoice.customer.email})</p>
            </div>
            <div style="text-align: right;">
                <p><strong>Invoice No:</strong> {invoice.invoice_number}</p>
                <p><strong>Date:</strong> {invoice.issue_date}</p>
                <p><strong>Due Date:</strong> {invoice.due_date}</p>
                <p><strong>Status:</strong> <span style="color: {'#2ca01c' if invoice.status == InvoiceStatus.PAID else '#e53935'}">{invoice.status.value}</span></p>
            </div>
        </div>
        <table class="details-table">
            <thead>
                <tr>
                    <th>Item Description</th>
                    <th>Quantity</th>
                    <th>Unit Price</th>
                    <th>Total</th>
                </tr>
            </thead>
            <tbody>
                {"".join([f"<tr><td>{item.description}</td><td>{item.quantity}</td><td>${item.unit_price:,.2f}</td><td>${item.line_total:,.2f}</td></tr>" for item in invoice.items])}
            </tbody>
        </table>
        <div class="totals">
            <div class="totals-row"><span>Subtotal:</span> <strong>${invoice.subtotal:,.2f}</strong></div>
            <div class="totals-row"><span>Tax:</span> <strong>${invoice.tax_total:,.2f}</strong></div>
