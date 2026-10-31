from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Shipment(Base):
    __tablename__ = "shipments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    customer_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"), index=True)
    filename: Mapped[str] = mapped_column(String(512))
    file_ext: Mapped[str] = mapped_column(String(16))
    storage_path: Mapped[str] = mapped_column(String(1024))
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    has_text_layer: Mapped[bool] = mapped_column(default=False)
    doc_type: Mapped[str | None] = mapped_column(String(64))
    doc_type_source: Mapped[str | None] = mapped_column(String(16))
    classification_confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))
    status: Mapped[str] = mapped_column(String(32), default="UPLOADED")
    error: Mapped[str | None] = mapped_column(Text)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    version_note: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Party(Base):
    __tablename__ = "parties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"), index=True)
    raw_name: Mapped[str] = mapped_column(String(512))
    canonical_name: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DocumentParty(Base):
    __tablename__ = "document_parties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"), index=True)
    role: Mapped[str] = mapped_column(String(32))


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"), index=True)
    seller_party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id"))
    buyer_party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id"))
    kind: Mapped[str] = mapped_column(String(32), default="GOODS")
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    currency: Mapped[str | None] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LineItem(Base):
    __tablename__ = "line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"))
    seq: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str | None] = mapped_column(String(1024))
    raw_description: Mapped[str | None] = mapped_column(String(1024))
    spec: Mapped[str | None] = mapped_column(String(255))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    unit: Mapped[str | None] = mapped_column(String(32))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    currency: Mapped[str | None] = mapped_column(String(8))


class ExtractedField(Base):
    __tablename__ = "extracted_fields"
    __table_args__ = (Index("ix_extracted_fields_doc_key", "document_id", "field_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    line_item_id: Mapped[int | None] = mapped_column(ForeignKey("line_items.id"))
    field_key: Mapped[str] = mapped_column(String(128))
    raw_value: Mapped[str | None] = mapped_column(Text)
    normalized_value: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(32))
    page: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[Any | None] = mapped_column(JSON)
    cell_ref: Mapped[str | None] = mapped_column(String(16))
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))
    provenance_type: Mapped[str] = mapped_column(String(16), default="TEXT_LAYER")
    grounding_verified: Mapped[bool] = mapped_column(default=False)
    review_status: Mapped[str] = mapped_column(String(16), default="UNREVIEWED")
    critical: Mapped[bool] = mapped_column(default=False)


class FieldValueHistory(Base):
    __tablename__ = "field_value_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("extracted_fields.id"), index=True)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(128), default="local-user")
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(64), unique=True)
    storage_path: Mapped[str] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExportJob(Base):
    __tablename__ = "export_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"), index=True)
    template_version: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(16), default="DRAFT")
    mapping_snapshot: Mapped[Any] = mapped_column(JSON)
    output_path: Mapped[str | None] = mapped_column(String(1024))
    report_path: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Declaration(Base):
    """Tờ khai hải quan nhập khẩu (TKN) header.

    One declaration per shipment export family. Values are strings because the source
    system prints them verbatim ("23.230", "A12  2 [ 4 ]"); typed but parseable numbers
    also keep a Decimal sidecar where the exporter needs arithmetic.
    """

    __tablename__ = "declarations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    declaration_no: Mapped[str | None] = mapped_column(String(64), index=True)
    declaration_no_digits: Mapped[str | None] = mapped_column(String(32))
    registered_at: Mapped[str | None] = mapped_column(String(32))
    registered_changed_at: Mapped[str | None] = mapped_column(String(32))
    declaration_kind: Mapped[str | None] = mapped_column(String(64))
    check_code: Mapped[str | None] = mapped_column(String(16))
    customs_office: Mapped[str | None] = mapped_column(String(64))
    representative_hs_code: Mapped[str | None] = mapped_column(String(32))
    importer_tax_code: Mapped[str | None] = mapped_column(String(32))
    importer_name: Mapped[str | None] = mapped_column(String(512))
    importer_postcode: Mapped[str | None] = mapped_column(String(32))
    importer_address: Mapped[str | None] = mapped_column(String(1024))
    importer_phone: Mapped[str | None] = mapped_column(String(64))
    exporter_name: Mapped[str | None] = mapped_column(String(512))
    exporter_address: Mapped[str | None] = mapped_column(String(1024))
    exporter_country_code: Mapped[str | None] = mapped_column(String(8))
    customs_agent: Mapped[str | None] = mapped_column(String(256))
    bill_of_lading_no: Mapped[str | None] = mapped_column(String(128))
    warehouse_code: Mapped[str | None] = mapped_column(String(32))
    warehouse_name: Mapped[str | None] = mapped_column(String(256))
    discharge_port_code: Mapped[str | None] = mapped_column(String(16))
    discharge_port_name: Mapped[str | None] = mapped_column(String(256))
    loading_port_code: Mapped[str | None] = mapped_column(String(16))
    loading_port_name: Mapped[str | None] = mapped_column(String(256))
    transport_vessel: Mapped[str | None] = mapped_column(String(256))
    cargo_arrival_date: Mapped[str | None] = mapped_column(String(32))
    package_count: Mapped[str | None] = mapped_column(String(32))
    package_unit: Mapped[str | None] = mapped_column(String(16))
    marks_and_numbers: Mapped[str | None] = mapped_column(String(512))
    gross_weight: Mapped[str | None] = mapped_column(String(32))
    gross_weight_unit: Mapped[str | None] = mapped_column(String(16))
    container_count: Mapped[str | None] = mapped_column(String(16))
    legal_document_codes: Mapped[str | None] = mapped_column(String(128))
    invoice_no: Mapped[str | None] = mapped_column(String(128))
    invoice_date: Mapped[str | None] = mapped_column(String(32))
    payment_method: Mapped[str | None] = mapped_column(String(16))
    invoice_currency_code: Mapped[str | None] = mapped_column(String(8))
    invoice_total: Mapped[str | None] = mapped_column(String(32))
    total_customs_value: Mapped[str | None] = mapped_column(String(32))
    total_allocation_factor: Mapped[str | None] = mapped_column(String(32))
    valuation_shipper_note: Mapped[str | None] = mapped_column(String(512))
    tax_total_payable: Mapped[str | None] = mapped_column(String(32))
    tax_exchange_rate_currency: Mapped[str | None] = mapped_column(String(8))
    tax_exchange_rate: Mapped[str | None] = mapped_column(String(32))
    tax_exchange_rate_value: Mapped[str | None] = mapped_column(String(32))
    page_count: Mapped[str | None] = mapped_column(String(8))
    goods_line_count: Mapped[str | None] = mapped_column(String(8))
    import_license_numbers: Mapped[str | None] = mapped_column(String(256))
    note: Mapped[str | None] = mapped_column(Text)
    customs_chief_name: Mapped[str | None] = mapped_column(String(256))
    licensed_at: Mapped[str | None] = mapped_column(String(32))
    inspection_finished_at: Mapped[str | None] = mapped_column(String(32))
    company_internal_ref: Mapped[str | None] = mapped_column(String(64))
    template_version: Mapped[str] = mapped_column(String(64), default="tokhai_import_v1")
    review_status: Mapped[str] = mapped_column(String(16), default="UNREVIEWED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DeclarationTaxLine(Base):
    """Bảng 'Tên sắc thuế / Tổng tiền thuế / Số dòng tổng' (TKN!D68...)."""

    __tablename__ = "declaration_tax_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    declaration_id: Mapped[int] = mapped_column(ForeignKey("declarations.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str | None] = mapped_column(String(128))
    amount: Mapped[str | None] = mapped_column(String(32))
    payable_total: Mapped[str | None] = mapped_column(String(32))
    line_count: Mapped[str | None] = mapped_column(String(16))
    currency: Mapped[str | None] = mapped_column(String(8))


class GoodsLine(Base):
    """Dòng hàng hóa: mỗi dòng chiếm một khối trang TKN và một dòng sheet HANG."""

    __tablename__ = "goods_lines"
    __table_args__ = (Index("ix_goods_lines_declaration_seq", "declaration_id", "seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    declaration_id: Mapped[int] = mapped_column(ForeignKey("declarations.id"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    seq: Mapped[int] = mapped_column(Integer, default=0)
    hs_code: Mapped[str | None] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text)
    quantity_1: Mapped[str | None] = mapped_column(String(32))
    quantity_1_unit: Mapped[str | None] = mapped_column(String(16))
    quantity_2: Mapped[str | None] = mapped_column(String(32))
    invoice_amount: Mapped[str | None] = mapped_column(String(32))
    unit_price: Mapped[str | None] = mapped_column(String(32))
    unit_price_currency: Mapped[str | None] = mapped_column(String(8))
    customs_value: Mapped[str | None] = mapped_column(String(32))
    customs_unit_price: Mapped[str | None] = mapped_column(String(32))
    import_duty_rate: Mapped[str | None] = mapped_column(String(16))
    origin_country_code: Mapped[str | None] = mapped_column(String(8))
    origin_country_name: Mapped[str | None] = mapped_column(String(128))
    origin_preference_code: Mapped[str | None] = mapped_column(String(16))
    tax_name: Mapped[str | None] = mapped_column(String(128))
    tax_rate_code: Mapped[str | None] = mapped_column(String(32))
    tax_base: Mapped[str | None] = mapped_column(String(32))
    tax_rate: Mapped[str | None] = mapped_column(String(16))
    tax_amount: Mapped[str | None] = mapped_column(String(32))
    review_status: Mapped[str] = mapped_column(String(16), default="UNREVIEWED")
    source_located: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(128), default="local-user")
    payload: Mapped[Any | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[Any] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(256), index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
