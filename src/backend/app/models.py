from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
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


class LineItem(Base):
    __tablename__ = "line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
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
    section_id: Mapped[int | None] = mapped_column(ForeignKey("document_sections.id"))
    section_type: Mapped[str | None] = mapped_column(String(64))
    source_row_index: Mapped[int | None] = mapped_column(Integer)
    container_no: Mapped[str | None] = mapped_column(String(32))
    evidence_state: Mapped[str | None] = mapped_column(String(24))


class FieldValueHistory(Base):
    __tablename__ = "field_value_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    field_id: Mapped[int] = mapped_column(ForeignKey("extracted_fields.id"), index=True)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(128), default="local-user")
    reason: Mapped[str | None] = mapped_column(Text)
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

    One declaration per shipment export family. The schema mirrors the TKN page-1
    layout cell-for-cell (see templates_config/tokhai_import_v1). Values are
    strings because the source system prints them verbatim ("23.230",
    "A12  2 [ 4 ]").
    """

    __tablename__ = "declarations"
    # Files of one shipment are processed in parallel and each declaration mirror is an
    # upsert; without this key two of them could both insert a header row.
    __table_args__ = (UniqueConstraint("shipment_id", name="uq_declaration_shipment"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    declaration_no: Mapped[str | None] = mapped_column(String(64), index=True)
    first_declaration_no: Mapped[str | None] = mapped_column(String(64))
    corresponding_declaration_no: Mapped[str | None] = mapped_column(String(64))
    registered_at: Mapped[str | None] = mapped_column(String(32))
    registered_changed_at: Mapped[str | None] = mapped_column(String(32))
    declaration_kind: Mapped[str | None] = mapped_column(String(64))
    check_code: Mapped[str | None] = mapped_column(String(16))
    customs_office: Mapped[str | None] = mapped_column(String(64))
    processing_department_code: Mapped[str | None] = mapped_column(String(16))
    representative_hs_code: Mapped[str | None] = mapped_column(String(32))
    reexport_deadline: Mapped[str | None] = mapped_column(String(32))
    importer_tax_code: Mapped[str | None] = mapped_column(String(32))
    importer_name: Mapped[str | None] = mapped_column(String(512))
    importer_postcode: Mapped[str | None] = mapped_column(String(32))
    importer_address: Mapped[str | None] = mapped_column(String(1024))
    importer_phone: Mapped[str | None] = mapped_column(String(64))
    entrusted_importer_tax_code: Mapped[str | None] = mapped_column(String(32))
    entrusted_importer_name: Mapped[str | None] = mapped_column(String(512))
    exporter_tax_code: Mapped[str | None] = mapped_column(String(32))
    exporter_name: Mapped[str | None] = mapped_column(String(512))
    exporter_postcode: Mapped[str | None] = mapped_column(String(32))
    exporter_address: Mapped[str | None] = mapped_column(String(1024))
    exporter_country_code: Mapped[str | None] = mapped_column(String(8))
    entrusted_exporter_name: Mapped[str | None] = mapped_column(String(512))
    customs_agent: Mapped[str | None] = mapped_column(String(256))
    customs_officer_code: Mapped[str | None] = mapped_column(String(16))
    bill_of_lading_no: Mapped[str | None] = mapped_column(String(128))
    warehouse_code: Mapped[str | None] = mapped_column(String(32))
    warehouse_name: Mapped[str | None] = mapped_column(String(256))
    discharge_port_code: Mapped[str | None] = mapped_column(String(16))
    discharge_port_name: Mapped[str | None] = mapped_column(String(256))
    loading_port_code: Mapped[str | None] = mapped_column(String(16))
    loading_port_name: Mapped[str | None] = mapped_column(String(256))
    transport_vessel: Mapped[str | None] = mapped_column(String(256))
    cargo_arrival_date: Mapped[str | None] = mapped_column(String(32))
    first_warehouse_entry_date: Mapped[str | None] = mapped_column(String(32))
    package_count: Mapped[str | None] = mapped_column(String(32))
    package_unit: Mapped[str | None] = mapped_column(String(16))
    marks_and_numbers: Mapped[str | None] = mapped_column(String(512))
    gross_weight: Mapped[str | None] = mapped_column(String(32))
    gross_weight_unit: Mapped[str | None] = mapped_column(String(16))
    container_count: Mapped[str | None] = mapped_column(String(16))
    legal_document_codes: Mapped[str | None] = mapped_column(String(128))
    invoice_no: Mapped[str | None] = mapped_column(String(128))
    e_invoice_receipt_no: Mapped[str | None] = mapped_column(String(128))
    invoice_date: Mapped[str | None] = mapped_column(String(32))
    payment_method: Mapped[str | None] = mapped_column(String(16))
    invoice_terms: Mapped[str | None] = mapped_column(String(128))
    invoice_total: Mapped[str | None] = mapped_column(String(32))
    total_customs_value: Mapped[str | None] = mapped_column(String(32))
    total_allocation_factor: Mapped[str | None] = mapped_column(String(32))
    content_check_result_code: Mapped[str | None] = mapped_column(String(16))
    valuation_shipper_note: Mapped[str | None] = mapped_column(String(512))
    tax_total_payable: Mapped[str | None] = mapped_column(String(32))
    tax_guarantee_amount: Mapped[str | None] = mapped_column(String(32))
    tax_exchange_rate_currency: Mapped[str | None] = mapped_column(String(8))
    tax_exchange_rate: Mapped[str | None] = mapped_column(String(32))
    tax_deadline_code: Mapped[str | None] = mapped_column(String(8))
    audit_request_reason_code: Mapped[str | None] = mapped_column(String(8))
    taxpayer_code: Mapped[str | None] = mapped_column(String(16))
    tax_payment_classification: Mapped[str | None] = mapped_column(String(8))
    user_ref_number: Mapped[str | None] = mapped_column(String(64))
    company_internal_ref: Mapped[str | None] = mapped_column(String(64))
    customs_directive_code: Mapped[str | None] = mapped_column(String(8))
    note: Mapped[str | None] = mapped_column(Text)
    customs_chief_name: Mapped[str | None] = mapped_column(String(256))
    licensed_at: Mapped[str | None] = mapped_column(String(32))
    inspection_finished_at: Mapped[str | None] = mapped_column(String(32))
    post_clearance_classification: Mapped[str | None] = mapped_column(String(32))
    expected_days_to_license: Mapped[str | None] = mapped_column(String(16))
    late_tax_total: Mapped[str | None] = mapped_column(String(32))
    review_status: Mapped[str] = mapped_column(String(16), default="UNREVIEWED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DeclarationLicense(Base):
    """Khối 'Giấy phép nhập khẩu' (TKN!D50..R51): 5 slot mã + số."""

    __tablename__ = "declaration_licenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    declaration_id: Mapped[int] = mapped_column(ForeignKey("declarations.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    code: Mapped[str | None] = mapped_column(String(32))
    number: Mapped[str | None] = mapped_column(String(128))


class DeclarationAdjustment(Base):
    """Khối 'Các khoản điều chỉnh' (TKN!D58..U62): 5 dòng mã tên/phân loại/trị giá/hệ số."""

    __tablename__ = "declaration_adjustments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    declaration_id: Mapped[int] = mapped_column(ForeignKey("declarations.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    code_name: Mapped[str | None] = mapped_column(String(32))
    classification_code: Mapped[str | None] = mapped_column(String(16))
    value: Mapped[str | None] = mapped_column(String(32))
    allocation_factor: Mapped[str | None] = mapped_column(String(32))


class DeclarationInstruction(Base):
    """Khối 'Phân loại chỉ thị của Hải quan' (TKN!D90..R117): 10 dòng, stride 3."""

    __tablename__ = "declaration_instructions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    declaration_id: Mapped[int] = mapped_column(ForeignKey("declarations.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    date: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(256))
    content: Mapped[str | None] = mapped_column(Text)


class DeclarationTaxLine(Base):
    """Bảng 'Tên sắc thuế / Tổng tiền thuế / Số dòng tổng' (TKN!D68...)."""

    __tablename__ = "declaration_tax_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    declaration_id: Mapped[int] = mapped_column(ForeignKey("declarations.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str | None] = mapped_column(String(128))
    amount: Mapped[str | None] = mapped_column(String(32))
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
    private_management_code: Mapped[str | None] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text)
    adjustment_item_no: Mapped[str | None] = mapped_column(String(16))
    quantity_1: Mapped[str | None] = mapped_column(String(32))
    quantity_1_unit: Mapped[str | None] = mapped_column(String(16))
    quantity_2: Mapped[str | None] = mapped_column(String(32))
    quantity_2_unit: Mapped[str | None] = mapped_column(String(16))
    invoice_amount: Mapped[str | None] = mapped_column(String(32))
    unit_price: Mapped[str | None] = mapped_column(String(32))
    unit_price_currency: Mapped[str | None] = mapped_column(String(8))
    customs_value: Mapped[str | None] = mapped_column(String(32))
    customs_value_foreign: Mapped[str | None] = mapped_column(String(32))
    customs_unit_price: Mapped[str | None] = mapped_column(String(32))
    tax_quantity: Mapped[str | None] = mapped_column(String(32))
    import_duty_rate: Mapped[str | None] = mapped_column(String(16))
    import_duty_rate_code: Mapped[str | None] = mapped_column(String(8))
    absolute_tax_code: Mapped[str | None] = mapped_column(String(16))
    origin_country_code: Mapped[str | None] = mapped_column(String(8))
    origin_country_name: Mapped[str | None] = mapped_column(String(128))
    origin_preference_code: Mapped[str | None] = mapped_column(String(16))
    duty_exemption_amount: Mapped[str | None] = mapped_column(String(32))
    duty_exemption_list: Mapped[str | None] = mapped_column(String(128))
    duty_exemption_note: Mapped[str | None] = mapped_column(String(256))
    review_status: Mapped[str] = mapped_column(String(16), default="UNREVIEWED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GoodsTaxLine(Base):
    """Khối 'Thuế và thu khác' của một dòng hàng (TKN!H167..I191, 5 slot mỗi khối)."""

    __tablename__ = "goods_tax_lines"
    __table_args__ = (Index("ix_goods_tax_lines_goods_seq", "goods_line_id", "seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    goods_line_id: Mapped[int] = mapped_column(ForeignKey("goods_lines.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str | None] = mapped_column(String(128))
    rate_code: Mapped[str | None] = mapped_column(String(32))
    base: Mapped[str | None] = mapped_column(String(32))
    quantity: Mapped[str | None] = mapped_column(String(32))
    rate: Mapped[str | None] = mapped_column(String(16))
    amount: Mapped[str | None] = mapped_column(String(32))
    exemption_amount: Mapped[str | None] = mapped_column(String(32))
    exemption_note: Mapped[str | None] = mapped_column(String(256))


class JobDocumentStage(Base):
    """Live pipeline stage of one file inside a running job (deleted when the file ends).

    One row per in-flight document instead of a JSON map on `jobs`: SQLite has no row
    locks, and concurrent files updating a shared map lose entries.
    """

    __tablename__ = "job_document_stages"
    __table_args__ = (
        UniqueConstraint("job_id", "document_id", name="uq_job_document_stage"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    document_id: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(32))
    # `jobs.attempts` of the run that wrote this row: a worker whose lease expired must
    # not publish stages, or count completions, for the retry that replaced it.
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DocumentSection(Base):
    """A logical document inside one uploaded file (pages are never split on disk).

    A 10-page bundle may hold Contract, Invoice, Packing List, B/L and certificates.
    The original `Document` and its file stay intact; sections only annotate ranges.
    """

    __tablename__ = "document_sections"
    __table_args__ = (
        UniqueConstraint("document_id", "page_start", "page_end", name="uq_document_section_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    page_start: Mapped[int] = mapped_column(Integer, default=0)
    page_end: Mapped[int] = mapped_column(Integer, default=0)
    section_type: Mapped[str] = mapped_column(String(64), default="UNKNOWN")
    classification_state: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    classification_evidence: Mapped[str | None] = mapped_column(Text)
    text_chars: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExtractionRevision(Base):
    """One extraction run of a document/section: reproducible by its input versions."""

    __tablename__ = "extraction_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    section_id: Mapped[int | None] = mapped_column(ForeignKey("document_sections.id"))
    source_hash: Mapped[str] = mapped_column(String(64), default="")
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(64))
    schema_version: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="DONE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResolvedField(Base):
    """The canonical value of one target field for one shipment and template.

    Only `ShipmentResolutionService` writes this table. Review edits here, and the
    exporter reads exactly this row, so a human edit cannot diverge from the workbook.
    """

    __tablename__ = "resolved_fields"
    __table_args__ = (
        UniqueConstraint(
            "shipment_id", "target_key", "template_version", name="uq_resolved_field_target"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"), index=True)
    template_version: Mapped[str] = mapped_column(String(64), default="tokhai_import_v1")
    target_key: Mapped[str] = mapped_column(String(128), index=True)
    value: Mapped[str | None] = mapped_column(Text)
    value_type: Mapped[str] = mapped_column(String(16), default="text")
    unit: Mapped[str | None] = mapped_column(String(32))
    # RESOLVED | MISSING | AMBIGUOUS | NEEDS_REVIEW | STALE_REVIEW
    state: Mapped[str] = mapped_column(String(24), default="MISSING")
    # INPUT_DERIVED | CUSTOMS_GENERATED | DERIVED
    origin: Mapped[str] = mapped_column(String(24), default="INPUT_DERIVED")
    # BUSINESS_INPUT | CUSTOMS_REFERENCE
    document_role: Mapped[str] = mapped_column(String(24), default="BUSINESS_INPUT")
    # DIRECT | NORMALIZED | CALCULATED | LLM_SUGGESTED | USER_EDITED
    resolution_method: Mapped[str] = mapped_column(String(24), default="DIRECT")
    resolved_by: Mapped[str] = mapped_column(String(64), default="system")
    review_status: Mapped[str] = mapped_column(String(16), default="UNREVIEWED")
    source_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DeclarationFieldSource(Base):
    """Field-level evidence for a resolved field (PRIMARY / SUPPORTING / CONFLICT)."""

    __tablename__ = "declaration_field_sources"
    __table_args__ = (Index("ix_decl_field_sources_resolved", "resolved_field_id", "evidence_role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resolved_field_id: Mapped[int] = mapped_column(ForeignKey("resolved_fields.id"), index=True)
    source_field_id: Mapped[int | None] = mapped_column(ForeignKey("extracted_fields.id"))
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    section_id: Mapped[int | None] = mapped_column(ForeignKey("document_sections.id"))
    page: Mapped[int | None] = mapped_column(Integer)
    cell_ref: Mapped[str | None] = mapped_column(String(32))
    bbox: Mapped[Any | None] = mapped_column(JSON)
    evidence_role: Mapped[str] = mapped_column(String(16), default="PRIMARY")
    evidence_state: Mapped[str | None] = mapped_column(String(24))
    note: Mapped[str | None] = mapped_column(Text)


class ResolvedFieldHistory(Base):
    """Append-only history of a resolved field (edits, confirmations, resolutions)."""

    __tablename__ = "resolved_field_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resolved_field_id: Mapped[int] = mapped_column(ForeignKey("resolved_fields.id"), index=True)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(128), default="local-user")
    reason: Mapped[str | None] = mapped_column(Text)
    resolution_method: Mapped[str | None] = mapped_column(String(24))
    source_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
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
    document_total: Mapped[int] = mapped_column(Integer, default=0)
    document_done: Mapped[int] = mapped_column(Integer, default=0)
    current_document_id: Mapped[int | None] = mapped_column(Integer)
    current_stage: Mapped[str | None] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(256), index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
