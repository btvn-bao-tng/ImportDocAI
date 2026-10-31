from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label_vi: str
    kind: str  # text | date | decimal | currency | unit | int
    critical: bool = False
    enum: tuple[str, ...] | None = None


PARTY_ROLES = ("SELLER", "BUYER", "SHIPPER", "CONSIGNEE", "NOTIFY", "ISSUER", "INSPECTOR")

COMMON_FIELDS = (
    FieldSpec("document.doc_number", "Số chứng từ", "text", critical=True),
    FieldSpec("document.issue_date", "Ngày phát hành", "date"),
    FieldSpec("document.issuer", "Bên phát hành", "text"),
)

TRANSPORT_FIELDS = (
    FieldSpec("transport.vessel", "Tàu", "text", critical=True),
    FieldSpec("transport.voyage", "Chuyến", "text"),
    FieldSpec("transport.waybill_no", "Số vận đơn", "text", critical=True),
    FieldSpec("transport.container_no", "Số container", "text", critical=True),
    FieldSpec("transport.seal_no", "Số seal", "text"),
    FieldSpec("transport.packages", "Số kiện", "int"),
    FieldSpec("transport.gross_weight_kg", "Gross weight (kg)", "decimal", critical=True),
    FieldSpec("transport.net_weight_kg", "Net weight (kg)", "decimal", critical=True),
    FieldSpec("transport.volume_cbm", "Thể tích (m³)", "decimal", critical=True),
)

LINE_FIELDS = (
    FieldSpec("line.no", "STT", "int"),
    FieldSpec("line.description", "Mô tả hàng", "text"),
    FieldSpec("line.spec", "Quy cách", "text"),
    FieldSpec("line.components", "Quy cách thành phần", "text"),
    FieldSpec("line.quantity", "Số lượng", "decimal", critical=True),
    FieldSpec("line.unit", "Đơn vị", "unit"),
    FieldSpec("line.unit_price", "Đơn giá", "decimal", critical=True),
    FieldSpec("line.amount", "Thành tiền", "decimal", critical=True),
)

PARTY_FIELDS = (
    FieldSpec("party.seller", "Bên bán", "text"),
    FieldSpec("party.buyer", "Bên mua", "text"),
    FieldSpec("party.shipper", "Người gửi hàng", "text"),
    FieldSpec("party.consignee", "Người nhận hàng", "text"),
    FieldSpec("party.notify", "Bên thông báo", "text"),
)

TOTAL_FIELDS = (
    FieldSpec("totals.total_amount", "Tổng trị giá", "decimal", critical=True),
    FieldSpec("totals.currency", "Đồng tiền", "currency", critical=True),
)

EXTRACTABLE_FIELDS: dict[str, tuple[FieldSpec, ...]] = {
    "COMMERCIAL_INVOICE": COMMON_FIELDS + TRANSPORT_FIELDS + PARTY_FIELDS + LINE_FIELDS + TOTAL_FIELDS,
    "PACKING_LIST": COMMON_FIELDS + TRANSPORT_FIELDS + LINE_FIELDS,
    "TRANSPORT_DOCUMENT": COMMON_FIELDS + TRANSPORT_FIELDS + PARTY_FIELDS,
    "SALES_CONTRACT_PO": COMMON_FIELDS + PARTY_FIELDS + LINE_FIELDS + TOTAL_FIELDS,
    "ARRIVAL_NOTICE": COMMON_FIELDS + TRANSPORT_FIELDS,
    "SERVICE_CONTRACT": COMMON_FIELDS + PARTY_FIELDS + TOTAL_FIELDS,
    "EXPORT_DECLARATION": COMMON_FIELDS + TRANSPORT_FIELDS + PARTY_FIELDS + TOTAL_FIELDS,
    "CERTIFICATE_OF_ORIGIN": COMMON_FIELDS,
    "INSURANCE_POLICY": COMMON_FIELDS + TOTAL_FIELDS,
    "PHYTOSANITARY_CERTIFICATE": COMMON_FIELDS,
    "CUSTOMS_DECLARATION_SCAN": COMMON_FIELDS + PARTY_FIELDS + TOTAL_FIELDS,
    "OTHER": COMMON_FIELDS,
}

DOCUMENT_LEVEL_ONLY = {
    "CERTIFICATE_OF_ORIGIN",
    "PHYTOSANITARY_CERTIFICATE",
    "CUSTOMS_DECLARATION_SCAN",
    "OTHER",
}

ALL_FIELD_SPECS: dict[str, FieldSpec] = {}
for specs in EXTRACTABLE_FIELDS.values():
    for spec in specs:
        ALL_FIELD_SPECS[spec.key] = spec

# Shipment-level aliases used by the template mapping. They resolve to a party field from a
# single preferred document and are critical: a company name must be reviewed before FINAL.
SHIPMENT_ALIASES: dict[str, str] = {
    "contract.seller": "party.seller",
    "contract.buyer": "party.buyer",
    "contract.shipper": "party.shipper",
    "contract.consignee": "party.consignee",
}

for alias, target in SHIPMENT_ALIASES.items():
    base = ALL_FIELD_SPECS[target]
    ALL_FIELD_SPECS[alias] = FieldSpec(alias, base.label_vi, base.kind, critical=True)


def resolve_alias(field_key: str) -> str:
    return SHIPMENT_ALIASES.get(field_key, field_key)


def _register_output_families() -> None:
    """Register every extra output family once, deterministically.

    Imported here (not by callers) so `fields_for()` and `is_critical()` are correct
    in the API, the worker and tests regardless of import order.
    """
    try:
        from . import tokhai_fields  # noqa: F401  (registers on import)
    except Exception:  # pragma: no cover - registration must never break the app
        import logging

        logging.getLogger("importdoc.fields").exception("cannot register Tờ khai fields")


def fields_for(doc_type: str) -> tuple[FieldSpec, ...]:
    return EXTRACTABLE_FIELDS.get(doc_type, COMMON_FIELDS)


def register_extra_fields(specs, doc_types: tuple[str, ...]) -> None:
    """Register field specs from another output family (e.g. Tờ khai) into the registry.

    Idempotent: re-registering the same key replaces the spec in place.
    """
    for spec in specs:
        ALL_FIELD_SPECS[spec.key] = spec
    for doc_type in doc_types:
        current = EXTRACTABLE_FIELDS.setdefault(doc_type, COMMON_FIELDS)
        known = {s.key for s in current}
        EXTRACTABLE_FIELDS[doc_type] = current + tuple(s for s in specs if s.key not in known)


def is_critical(field_key: str) -> bool:
    spec = ALL_FIELD_SPECS.get(field_key)
    return bool(spec and spec.critical)


_register_output_families()
