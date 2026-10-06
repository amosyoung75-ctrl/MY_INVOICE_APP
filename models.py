from datetime import datetime, date
from enum import Enum
from typing import List
from sqlalchemy import ForeignKey, String, Numeric, Date, DateTime, Enum as SQLEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class InvoiceStatus(str, Enum):
    DRAFT = "Draft"
    UNPAID = "Unpaid"
    PARTIAL = "Partially Paid"
    PAID = "Paid"
    OVERDUE = "Overdue"

class Customer(Base):
    __tablename__ = "customers"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    phone: Mapped[str] = mapped_column(String(50), nullable=True)
    billing_address: Mapped[str] = mapped_column(String(500), nullable=True)
    
    invoices: Mapped[List["Invoice"]] = relationship(back_populates="customer")

class Invoice(Base):
    __tablename__ = "invoices"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_number: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    issue_date: Mapped[date] = mapped_column(Date, default=date.today)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    
    subtotal: Mapped[float] = mapped_column(Numeric(10, 2), default=0.00)
    tax_total: Mapped[float] = mapped_column(Numeric(10, 2), default=0.00)
    total_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0.00)
    amount_paid: Mapped[float] = mapped_column(Numeric(10, 2), default=0.00)
    
    status: Mapped[InvoiceStatus] = mapped_column(SQLEnum(InvoiceStatus), default=InvoiceStatus.UNPAID)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    customer: Mapped["Customer"] = relationship(back_populates="invoices")
    items: Mapped[List["InvoiceItem"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")

class InvoiceItem(Base):
    __tablename__ = "invoice_items"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(10, 2), default=1.00)
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    line_total: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    
    invoice: Mapped["Invoice"] = relationship(back_populates="items")
