import io
import pandas as pd
from datetime import datetime, date, timedelta
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import create_engine, select, func, and_, or_
from sqlalchemy.orm import Session, sessionmaker

from models import Base, Customer, Invoice, InvoiceItem, InvoiceStatus

# --- DATABASE SETUP ---
DATABASE_URL = "sqlite:///./invoices.db"
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

# --- ENDPOINTS ---
@app.post("/api/invoices", response_model=dict)
def create_invoice(payload: InvoiceCreate, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.email == payload.customer_email).first()
    if not customer:
        customer = Customer(name=payload.customer_name, email=payload.customer_email)
        db.add(customer)
        db.flush()

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

@app.get("/api/dashboard/metrics")
def get_dashboard_metrics(db: Session = Depends(get_db)):
    today = date.today()
    
    db.query(Invoice).filter(
        and_(Invoice.due_date < today, Invoice.status != InvoiceStatus.PAID)
    ).update({"status": InvoiceStatus.OVERDUE})
    db.commit()

    kpis = db.query(
        func.sum(Invoice.total_amount).label("total"),
        func.sum(Invoice.amount_paid).label("paid")
    ).first()
    
    total_revenue = float(kpis.total or 0.0)
    total_collected = float(kpis.paid or 0.0)
    total_receivable = total_revenue - total_collected

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
            aging_buckets["60_days"] += unpaid_amt # keeping standard logic or fixing
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

@app.post("/api/invoices/import-excel")
async def import_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="Invalid layout. System strictly accepts Excel extensions.")
    
    contents = await file.read()
    df = pd.read_excel(io.BytesIO(contents))
    
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
            continue

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

@app.get("/api/invoices/{invoice_id}/pdf")
def generate_invoice_pdf(invoice_id: int, db: Session = Depends(get_db)):
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice entry not identified.")
        
    from weasyprint import HTML
    
    html_template = f"""
    <html>
    <body>
        <h1>INVOICE {invoice.invoice_number}</h1>
        <p>Customer: {invoice.customer.name}</p>
        <p>Total Due: ${invoice.total_amount:,.2f}</p>
    </body>
    </html>
    """
    pdf_bytes = HTML(string=html_template).write_pdf()
    return Response(content=pdf_bytes, media_type="application/pdf")
