from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, JSON, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

def utcnow():
    return datetime.now(timezone.utc)

#it tells SQLAlchemy how to create a database table called suppliers, what columns that table should contain, and how it connects to other tables.
#Instead of manually creating this table using SQL commands, you define a Python class.
#SQLAlchemy uses this class to understand the structure of your database table

#Base - provides the foundation SQLAlchemy needs to identify your Python class as a database model.
class Supplier(Base):
    __tablename__ = "suppliers"
    #mapped_column() is used to tell SQLAlchemy how a Python class attribute should be represented as a column in the actual database table.
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(250), index=True)
    #Since data can be None so nullable = True
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category: Mapped[str | None] = mapped_column(String(150), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    #The next 3 do not create ordinary database columns. They define relationships between Python database models.
    #Basically there can be multiple Documents for same Supplier ID, same goes with Records & Risk Assessment.
    #So we are making sub tables for each section
    #cascade="all, delete-orphan" - all -> means if parent is deleted then delete all the records of that Supplier in all the sub Tables
    # delete-orphan -> means to delet eit from backend (purge in o9 basically)
    documents: Mapped[list["EvidenceDocument"]] = relationship(back_populates="supplier", cascade="all, delete-orphan")
    records: Mapped[list["StructuredRecord"]] = relationship(back_populates="supplier", cascade="all, delete-orphan")
    assessments: Mapped[list["Assessment"]] = relationship(back_populates="supplier", cascade="all, delete-orphan")

class EvidenceDocument(Base):
    __tablename__ = "evidence_documents"
    #This is going to be the Evidence Document ID
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #ForeignKey menas taking the data from the Parent Table
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    supplier: Mapped[Supplier] = relationship(back_populates="documents")

class StructuredRecord(Base):
    __tablename__ = "structured_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    supplier: Mapped[Supplier] = relationship(back_populates="records")

class Assessment(Base):
    __tablename__ = "assessments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    risk_score: Mapped[float] = mapped_column(Float)
    risk_band: Mapped[str] = mapped_column(String(30))
    coverage_pct: Mapped[float] = mapped_column(Float)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    supplier: Mapped[Supplier] = relationship(back_populates="assessments")
