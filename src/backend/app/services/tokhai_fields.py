from __future__ import annotations

"""Field registry for the Tờ khai hải quan nhập khẩu (TKN) output family.

Every canonical key here maps 1:1 to a column on `declarations` or `goods_lines`.
Keys are dotted like the rest of the app so the review UI can group them, and the
export mapping references them verbatim.
"""

from .fields import FieldSpec, register_extra_fields

DECLARATION_FIELDS = (
    FieldSpec("declaration.declaration_no", "Số tờ khai", "text", critical=True),
    FieldSpec("declaration.registered_at", "Ngày đăng ký", "text", critical=True),
    FieldSpec("declaration.registered_changed_at", "Ngày thay đổi đăng ký", "text"),
    FieldSpec("declaration.declaration_kind", "Mã loại hình", "text"),
    FieldSpec("declaration.check_code", "Mã phân loại kiểm tra", "text"),
    FieldSpec("declaration.customs_office", "Cơ quan hải quan tiếp nhận", "text"),
    FieldSpec("declaration.representative_hs_code", "Mã HS đại diện", "text"),
    FieldSpec("declaration.importer_tax_code", "Mã người nhập khẩu", "text"),
    FieldSpec("declaration.importer_name", "Người nhập khẩu", "text", critical=True),
    FieldSpec("declaration.importer_postcode", "Mã bưu chính người nhập khẩu", "text"),
    FieldSpec("declaration.importer_address", "Địa chỉ người nhập khẩu", "text"),
    FieldSpec("declaration.importer_phone", "Điện thoại người nhập khẩu", "text"),
    FieldSpec("declaration.exporter_name", "Người xuất khẩu", "text", critical=True),
    FieldSpec("declaration.exporter_address", "Địa chỉ người xuất khẩu", "text"),
    FieldSpec("declaration.exporter_country_code", "Mã nước xuất khẩu", "text"),
    FieldSpec("declaration.customs_agent", "Đại lý hải quan", "text"),
    FieldSpec("declaration.bill_of_lading_no", "Số vận đơn", "text", critical=True),
    FieldSpec("declaration.warehouse_code", "Mã địa điểm lưu kho", "text"),
    FieldSpec("declaration.warehouse_name", "Địa điểm lưu kho", "text"),
    FieldSpec("declaration.discharge_port_code", "Mã địa điểm dỡ hàng", "text"),
    FieldSpec("declaration.discharge_port_name", "Địa điểm dỡ hàng", "text"),
    FieldSpec("declaration.loading_port_code", "Mã địa điểm xếp hàng", "text"),
    FieldSpec("declaration.loading_port_name", "Địa điểm xếp hàng", "text"),
    FieldSpec("declaration.transport_vessel", "Phương tiện vận chuyển", "text"),
    FieldSpec("declaration.cargo_arrival_date", "Ngày hàng đến", "text"),
    FieldSpec("declaration.package_count", "Số lượng kiện", "decimal", critical=True),
    FieldSpec("declaration.package_unit", "Đơn vị số lượng kiện", "text"),
    FieldSpec("declaration.marks_and_numbers", "Ký hiệu và số hiệu", "text"),
    FieldSpec("declaration.gross_weight", "Tổng trọng lượng gross", "decimal", critical=True),
    FieldSpec("declaration.gross_weight_unit", "Đơn vị trọng lượng", "text"),
    FieldSpec("declaration.container_count", "Số lượng container", "int"),
    FieldSpec("declaration.legal_document_codes", "Mã văn bản pháp quy khác", "text"),
    FieldSpec("declaration.invoice_no", "Số hóa đơn", "text", critical=True),
    FieldSpec("declaration.invoice_date", "Ngày phát hành hóa đơn", "text"),
    FieldSpec("declaration.payment_method", "Phương thức thanh toán", "text"),
    FieldSpec("declaration.invoice_currency_code", "Đồng tiền hóa đơn", "currency"),
    FieldSpec("declaration.invoice_total", "Tổng trị giá hóa đơn", "decimal", critical=True),
    FieldSpec("declaration.total_customs_value", "Tổng trị giá tính thuế", "decimal"),
    FieldSpec("declaration.total_allocation_factor", "Tổng hệ số phân bổ trị giá", "decimal"),
    FieldSpec("declaration.valuation_shipper_note", "Chi tiết khai trị giá", "text"),
    FieldSpec("declaration.tax_total_payable", "Tổng tiền thuế phải nộp", "decimal"),
    FieldSpec("declaration.tax_exchange_rate_currency", "Tỷ giá tính thuế - đồng tiền", "currency"),
    FieldSpec("declaration.tax_exchange_rate", "Tỷ giá tính thuế", "decimal", critical=True),
    FieldSpec("declaration.tax_exchange_rate_value", "Tỷ giá tính thuế (giá trị)", "decimal"),
    FieldSpec("declaration.page_count", "Tổng số trang", "int"),
    FieldSpec("declaration.goods_line_count", "Tổng số dòng hàng", "int"),
    FieldSpec("declaration.import_license_numbers", "Giấy phép nhập khẩu", "text"),
    FieldSpec("declaration.note", "Phần ghi chú", "text"),
    FieldSpec("declaration.customs_chief_name", "Tên trưởng đơn vị hải quan", "text"),
    FieldSpec("declaration.licensed_at", "Ngày cấp phép", "text"),
    FieldSpec("declaration.inspection_finished_at", "Ngày hoàn thành kiểm tra", "text"),
    FieldSpec("declaration.company_internal_ref", "Số quản lý nội bộ doanh nghiệp", "text"),
)

GOODS_FIELDS = (
    FieldSpec("goods.hs_code", "Mã số hàng hóa", "text", critical=True),
    FieldSpec("goods.description", "Mô tả hàng hóa", "text", critical=True),
    FieldSpec("goods.quantity_1", "Số lượng (1)", "decimal", critical=True),
    FieldSpec("goods.quantity_1_unit", "Đơn vị số lượng (1)", "text"),
    FieldSpec("goods.quantity_2", "Số lượng (2)", "decimal"),
    FieldSpec("goods.invoice_amount", "Trị giá hóa đơn", "decimal", critical=True),
    FieldSpec("goods.unit_price", "Đơn giá hóa đơn", "decimal", critical=True),
    FieldSpec("goods.unit_price_currency", "Đồng tiền đơn giá", "currency"),
    FieldSpec("goods.customs_value", "Trị giá tính thuế", "decimal", critical=True),
    FieldSpec("goods.customs_unit_price", "Đơn giá tính thuế", "decimal"),
    FieldSpec("goods.import_duty_rate", "Thuế suất nhập khẩu", "text"),
    FieldSpec("goods.origin_country_code", "Mã nước xuất xứ", "text", critical=True),
    FieldSpec("goods.origin_country_name", "Tên nước xuất xứ", "text"),
    FieldSpec("goods.origin_preference_code", "Mã ưu đãi xuất xứ", "text"),
    FieldSpec("goods.tax_name", "Tên thuế", "text"),
    FieldSpec("goods.tax_rate_code", "Mã áp dụng thuế suất", "text"),
    FieldSpec("goods.tax_base", "Trị giá tính thuế (thuế khác)", "decimal"),
    FieldSpec("goods.tax_rate", "Thuế suất", "text"),
    FieldSpec("goods.tax_amount", "Số tiền thuế", "decimal"),
)

TAX_FIELDS = (
    FieldSpec("tax.name", "Tên sắc thuế", "text"),
    FieldSpec("tax.amount", "Tổng tiền thuế", "decimal"),
    FieldSpec("tax.payable_total", "Tổng tiền thuế phải nộp", "decimal"),
    FieldSpec("tax.line_count", "Số dòng tổng", "int"),
    FieldSpec("tax.currency", "Đồng tiền thuế", "currency"),
)

TOKHAI_FIELDS = DECLARATION_FIELDS + GOODS_FIELDS + TAX_FIELDS

TOKHAI_DOC_TYPES = ("CUSTOMS_DECLARATION", "CUSTOMS_DECLARATION_SCAN")


def register() -> None:
    register_extra_fields(
        TOKHAI_FIELDS,
        doc_types=("CUSTOMS_DECLARATION", "CUSTOMS_DECLARATION_SCAN", "EXPORT_DECLARATION"),
    )


register()
