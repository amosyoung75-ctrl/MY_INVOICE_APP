from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, Customer, Invoice, InvoiceStatus
import main

engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
SessionTesting = sessionmaker(bind=engine)


def setup_function():
    Base.metadata.create_all(bind=engine)


def teardown_function():
    Base.metadata.drop_all(bind=engine)


def test_dashboard_metrics_uses_90_plus_bucket_for_older_overdue_invoices():
    db = SessionTesting()
    customer = Customer(name="Acme", email="acme@example.com")
    db.add(customer)
    db.flush()

    db.add(
        Invoice(
            invoice_number="INV-OLD-1",
            customer_id=customer.id,
            due_date=date.today() - timedelta(days=45),
            subtotal=100.0,
            tax_total=0.0,
            total_amount=100.0,
            amount_paid=0.0,
            status=InvoiceStatus.UNPAID,
        )
    )
    db.add(
        Invoice(
            invoice_number="INV-OLD-2",
            customer_id=customer.id,
            due_date=date.today() - timedelta(days=75),
            subtotal=150.0,
            tax_total=0.0,
            total_amount=150.0,
            amount_paid=0.0,
            status=InvoiceStatus.UNPAID,
        )
    )
    db.commit()

    metrics = main.get_dashboard_metrics(db)

    assert metrics["aging_report"]["30_days"] == 0.0
    assert metrics["aging_report"]["60_days"] == 100.0
    assert metrics["aging_report"]["90_plus"] == 150.0
