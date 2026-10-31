from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from .export_excel import load_mapping

CATALOG_VERSION = "tokhai_targets_v1"

ROLE_BUSINESS_INPUT = "BUSINESS_INPUT"
ROLE_CUSTOMS_REFERENCE = "CUSTOMS_REFERENCE"

ORIGIN_INPUT_DERIVED = "INPUT_DERIVED"
ORIGIN_CUSTOMS_GENERATED = "CUSTOMS_GENERATED"
ORIGIN_DERIVED = "DERIVED"

REVIEW_AUTO = "AUTO_FROM_SOURCE"
REVIEW_ON_CONFLICT = "ON_CONFLICT_OR_UNVERIFIED_SCAN"
REVIEW_MANUAL = "MANUAL_ONLY"

BUSINESS_DOC_TYPES = (
    "COMMERCIAL_INVOICE",
    "PACKING_LIST",
    "TRANSPORT_DOCUMENT",
    "ARRIVAL_NOTICE",
    "SALES_CONTRACT_PO",
    "SERVICE_CONTRACT",
    "CERTIFICATE_OF_ORIGIN",
    "INSURANCE_POLICY",
    "PHYTOSANITARY_CERTIFICATE",
    "EXPORT_DECLARATION",
)

# Fields the customs system assigns or that need an explicit business decision. They
# are never predicted for a new draft: the audit counts them separately and a leaked
# reference value would be worse than an empty cell.
CUSTOMS_GENERATED_HEADER = {
    "declaration.declaration_no",
    "declaration.first_declaration_no",
    "declaration.corresponding_declaration_no",
    "declaration.registered_at",
    "declaration.registered_changed_at",
    "declaration.check_code",
    "declaration.customs_office",
    "declaration.processing_department_code",
    "declaration.representative_hs_code",
    "declaration.customs_officer_code",
    "declaration.customs_agent",
    "declaration.warehouse_code",
    "declaration.warehouse_name",
    "declaration.discharge_port_code",
    "declaration.loading_port_code",
    "declaration.content_check_result_code",
    "declaration.total_customs_value",
    "declaration.total_allocation_factor",
    "declaration.tax_total_payable",
    "declaration.tax_guarantee_amount",
    "declaration.tax_exchange_rate_currency",
    "declaration.tax_exchange_rate",
    "declaration.tax_deadline_code",
    "declaration.audit_request_reason_code",
    "declaration.taxpayer_code",
    "declaration.tax_payment_classification",
    "declaration.user_ref_number",
    "declaration.customs_directive_code",
    "declaration.customs_chief_name",
    "declaration.licensed_at",
    "declaration.inspection_finished_at",
    "declaration.post_clearance_classification",
    "declaration.expected_days_to_license",
    "declaration.late_tax_total",
    "declaration.e_invoice_receipt_no",
    "declaration.declaration_kind",
    "declaration.warehouse_code",
}

CUSTOMS_GENERATED_GOODS = {
    "goods.customs_value",
    "goods.customs_value_foreign",
    "goods.customs_unit_price",
    "goods.tax_quantity",
    "goods.import_duty_rate",
    "goods.import_duty_rate_code",
    "goods.absolute_tax_code",
    "goods.duty_exemption_amount",
    "goods.duty_exemption_list",
    "goods.duty_exemption_note",
    "goods.private_management_code",
    "goods.adjustment_item_no",
    "goods.origin_preference_code",
}

# A new draft fills a field only from these source keys. An empty tuple means the
# field has no allowed input source at all and is left MISSING by design.
HEADER_SOURCES: dict[str, tuple[str, ...]] = {
    "declaration.importer_name": ("party.buyer", "party.consignee"),
    "declaration.importer_tax_code": ("party.buyer.tax_code", "party.consignee.tax_code"),
    "declaration.importer_address": ("party.buyer.address", "party.consignee.address"),
    "declaration.importer_phone": ("party.buyer.phone", "party.consignee.phone"),
    "declaration.exporter_name": ("party.seller",),
    "declaration.exporter_tax_code": ("party.seller.tax_code",),
    "declaration.exporter_address": ("party.seller.address",),
    "declaration.exporter_country_code": ("party.seller.country_code",),
    "declaration.bill_of_lading_no": ("transport.waybill_no",),
    "declaration.invoice_no": ("document.doc_number",),
    "declaration.invoice_date": ("document.issue_date",),
    "declaration.invoice_total": ("totals.total_amount",),
    "declaration.payment_method": ("transport.payment_method",),
    "declaration.invoice_terms": ("transport.invoice_terms",),
    "declaration.package_count": ("transport.packages",),
    "declaration.package_unit": ("transport.package_unit",),
    "declaration.gross_weight": ("transport.gross_weight_kg",),
    "declaration.gross_weight_unit": ("transport.gross_weight_unit",),
    "declaration.container_count": ("transport.container_count",),
    "declaration.transport_vessel": ("transport.vessel",),
    "declaration.cargo_arrival_date": ("transport.arrival_date",),
    "declaration.marks_and_numbers": ("transport.container_no",),
}

# Which business document types may supply a field. A contract number must never be
# read as the invoice number just because both use `document.doc_number`.
SOURCE_DOC_TYPES: dict[str, tuple[str, ...]] = {
    "declaration.invoice_no": ("COMMERCIAL_INVOICE",),
    "declaration.invoice_date": ("COMMERCIAL_INVOICE",),
    "declaration.invoice_total": ("COMMERCIAL_INVOICE",),
    "declaration.payment_method": ("COMMERCIAL_INVOICE", "SALES_CONTRACT_PO"),
    "declaration.invoice_terms": ("COMMERCIAL_INVOICE", "SALES_CONTRACT_PO"),
    # A combined commercial bundle (TAEDA FINAL) carries the B/L preview inside the
    # invoice PDF, so the invoice file is an allowed source for the waybill number.
    "declaration.bill_of_lading_no": ("TRANSPORT_DOCUMENT", "ARRIVAL_NOTICE", "COMMERCIAL_INVOICE"),
    "declaration.importer_name": ("COMMERCIAL_INVOICE", "ARRIVAL_NOTICE", "SALES_CONTRACT_PO", "PACKING_LIST"),
    "declaration.importer_tax_code": ("COMMERCIAL_INVOICE", "ARRIVAL_NOTICE", "SALES_CONTRACT_PO", "PACKING_LIST"),
    "declaration.importer_address": ("COMMERCIAL_INVOICE", "ARRIVAL_NOTICE", "SALES_CONTRACT_PO", "PACKING_LIST"),
    "declaration.importer_phone": ("COMMERCIAL_INVOICE", "ARRIVAL_NOTICE", "SALES_CONTRACT_PO", "PACKING_LIST"),
    "declaration.exporter_name": ("COMMERCIAL_INVOICE", "SALES_CONTRACT_PO"),
    "declaration.exporter_tax_code": ("COMMERCIAL_INVOICE", "SALES_CONTRACT_PO"),
    "declaration.exporter_address": ("COMMERCIAL_INVOICE", "SALES_CONTRACT_PO"),
    "declaration.exporter_country_code": ("COMMERCIAL_INVOICE", "SALES_CONTRACT_PO", "TRANSPORT_DOCUMENT"),
    "declaration.package_count": ("PACKING_LIST", "ARRIVAL_NOTICE", "TRANSPORT_DOCUMENT", "COMMERCIAL_INVOICE"),
    "declaration.package_unit": ("PACKING_LIST", "ARRIVAL_NOTICE", "COMMERCIAL_INVOICE"),
    "declaration.gross_weight": ("PACKING_LIST", "ARRIVAL_NOTICE", "TRANSPORT_DOCUMENT", "COMMERCIAL_INVOICE"),
    "declaration.gross_weight_unit": ("PACKING_LIST", "ARRIVAL_NOTICE", "COMMERCIAL_INVOICE"),
    "declaration.container_count": ("PACKING_LIST", "ARRIVAL_NOTICE", "TRANSPORT_DOCUMENT"),
    "declaration.transport_vessel": ("TRANSPORT_DOCUMENT", "ARRIVAL_NOTICE", "COMMERCIAL_INVOICE"),
    "declaration.cargo_arrival_date": ("ARRIVAL_NOTICE", "TRANSPORT_DOCUMENT"),
    "declaration.marks_and_numbers": ("TRANSPORT_DOCUMENT", "PACKING_LIST"),
}

GOODS_SOURCES: dict[str, tuple[str, ...]] = {
    "goods.hs_code": ("line.hs_code",),
    "goods.description": ("line.description",),
    "goods.quantity_1": ("line.quantity",),
    "goods.quantity_1_unit": ("line.unit",),
    "goods.quantity_2": ("line.quantity",),
    "goods.quantity_2_unit": ("line.unit",),
    "goods.invoice_amount": ("line.amount",),
    "goods.unit_price": ("line.unit_price",),
    "goods.unit_price_currency": ("line.currency", "totals.currency"),
    "goods.origin_country_code": ("line.origin_country_code", "transport.origin_country_code"),
}

# Semantics matter as much as coordinates: ETA is not the actual arrival, an invoice
# amount is not an insured amount, and a commercial invoice row is not a tariff line.
SEMANTICS: dict[str, str] = {
    "declaration.cargo_arrival_date": "eta_or_actual_arrival_needs_business_choice",
    "declaration.invoice_total": "commercial_invoice_total_not_customs_value",
    "declaration.bill_of_lading_no": "waybill_as_printed",
    "declaration.representative_hs_code": "four_digit_group_only_never_eight",
    "declaration.package_unit": "customs_code_table_required",
    "declaration.transport_vessel": "vessel_may_differ_between_bl_preview_and_arrival_notice",
    "goods.hs_code": "eight_digit_needs_business_confirmation",
    "goods.quantity_1": "invoice_volume_or_weight_not_pieces",
    "declaration.importer_tax_code": "buyer_tax_code_not_shipper",
    "declaration.exporter_name": "commercial_seller_not_physical_shipper",
}

UNIT_CODES = {
    "cbm": "MTQ",
    "m³": "MTQ",
    "m3": "MTQ",
    "kg": "KGM",
    "kgs": "KGM",
    "kilogram": "KGM",
    "pcs": "PCE",
    "piece": "PCE",
    "pieces": "PCE",
    "pk": "PK",
    "package": "PK",
    "packages": "PK",
    "ctn": "CTN",
    "carton": "CTN",
}


@dataclass(frozen=True)
class TargetField:
    key: str
    value_type: str
    origin: str
    document_role: str
    required_for_new_draft: bool
    review_policy: str
    source_fields: tuple[str, ...] = ()
    semantics: str | None = None
    sheet: str | None = None
    cell: str | None = None
    unit: str | None = None
    source_doc_types: tuple[str, ...] = ()


@dataclass
class TargetCatalog:
    template_version: str
    version: str
    fields: dict[str, TargetField] = field(default_factory=dict)

    def get(self, key: str) -> TargetField | None:
        return self.fields.get(key)

    def required_keys(self) -> list[str]:
        return [k for k, f in self.fields.items() if f.required_for_new_draft]

    def as_dict(self) -> dict:
        return {
            "template_version": self.template_version,
            "version": self.version,
            "fields": {
                k: {
                    "value_type": f.value_type,
                    "origin": f.origin,
                    "document_role": f.document_role,
                    "required_for_new_draft": f.required_for_new_draft,
                    "review_policy": f.review_policy,
                    "source_fields": list(f.source_fields),
                    "semantics": f.semantics,
                    "sheet": f.sheet,
                    "cell": f.cell,
                    "unit": f.unit,
                }
                for k, f in self.fields.items()
            },
        }


def _cells_for(mapping: dict) -> dict[str, str]:
    cells: dict[str, str] = {}
    sm = ((mapping.get("sheets") or {}).get("TKN") or {})
    for ref, spec in (sm.get("scalars") or {}).items():
        cells.setdefault(spec["field"], ref)
    lb = sm.get("line_blocks") or {}
    for ref, spec in (lb.get("columns") or {}).items():
        cells.setdefault(spec["field"], ref)
    return cells


@lru_cache(maxsize=4)
def load_target_catalog(template_version: str = "tokhai_import_v1") -> TargetCatalog:
    from .fields import ALL_FIELD_SPECS, is_critical

    try:
        mapping, _ = load_mapping(template_version)
    except Exception:
        mapping = {}
    cells = _cells_for(mapping)
    catalog = TargetCatalog(template_version=template_version, version=CATALOG_VERSION)

    for key, spec in ALL_FIELD_SPECS.items():
        if key.startswith("declaration."):
            column = key.split(".", 1)[1]
            if column not in _declaration_columns():
                continue
            origin = ORIGIN_CUSTOMS_GENERATED if key in CUSTOMS_GENERATED_HEADER else ORIGIN_INPUT_DERIVED
            sources = () if origin == ORIGIN_CUSTOMS_GENERATED else HEADER_SOURCES.get(key, ())
            catalog.fields[key] = TargetField(
                key=key,
                value_type=spec.kind,
                origin=origin,
                document_role=ROLE_CUSTOMS_REFERENCE if origin == ORIGIN_CUSTOMS_GENERATED else ROLE_BUSINESS_INPUT,
                required_for_new_draft=bool(is_critical(key)) and origin == ORIGIN_INPUT_DERIVED,
                review_policy=REVIEW_AUTO if origin == ORIGIN_CUSTOMS_GENERATED else REVIEW_ON_CONFLICT,
                source_fields=sources,
                semantics=SEMANTICS.get(key),
                sheet="TKN",
                cell=cells.get(key),
                source_doc_types=SOURCE_DOC_TYPES.get(key, BUSINESS_DOC_TYPES),
            )
        elif key.startswith("goods."):
            origin = ORIGIN_CUSTOMS_GENERATED if key in CUSTOMS_GENERATED_GOODS else ORIGIN_INPUT_DERIVED
            sources = () if origin == ORIGIN_CUSTOMS_GENERATED else GOODS_SOURCES.get(key, ())
            catalog.fields[key] = TargetField(
                key=key,
                value_type=spec.kind,
                origin=origin,
                document_role=ROLE_CUSTOMS_REFERENCE if origin == ORIGIN_CUSTOMS_GENERATED else ROLE_BUSINESS_INPUT,
                # Input-derived critical goods fields stay required; customs-assigned
                # valuation/rate fields must never block a new draft.
                required_for_new_draft=bool(is_critical(key)) and origin == ORIGIN_INPUT_DERIVED,
                review_policy=REVIEW_AUTO if origin == ORIGIN_CUSTOMS_GENERATED else REVIEW_ON_CONFLICT,
                source_fields=sources,
                semantics=SEMANTICS.get(key),
                sheet="TKN",
                cell=cells.get(key),
                source_doc_types=SOURCE_DOC_TYPES.get(key, BUSINESS_DOC_TYPES),
            )
    return catalog


@lru_cache(maxsize=1)
def _declaration_columns() -> frozenset[str]:
    from ..models import Declaration

    return frozenset(Declaration.__table__.columns.keys())


def unit_code(value: str | None) -> str | None:
    if not value:
        return None
    return UNIT_CODES.get(str(value).strip().lower())
