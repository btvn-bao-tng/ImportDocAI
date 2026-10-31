from __future__ import annotations

"""Field registry for the Tờ khai hải quan nhập khẩu (TKN) output family.

Every canonical key here maps 1:1 to a column on `declarations`, `goods_lines`,
`goods_tax_lines`, `declaration_licenses`, `declaration_instructions` or
`declaration_adjustments`. Keys are dotted like the rest of the app so the review
UI can group them, and the export mapping references them verbatim.
"""

from .fields import FieldSpec, register_extra_fields

DECLARATION_FIELDS = (
    FieldSpec("declaration.declaration_no", "Số tờ khai", "text", critical=True),
    FieldSpec("declaration.first_declaration_no", "Số tờ khai đầu tiên", "text"),
    FieldSpec("declaration.corresponding_declaration_no", "Số tờ khai tạm nhập tái xuất tương ứng", "text"),
    FieldSpec("declaration.registered_at", "Ngày đăng ký", "text", critical=True),
    FieldSpec("declaration.registered_changed_at", "Ngày thay đổi đăng ký", "text"),
    FieldSpec("declaration.declaration_kind", "Mã loại hình", "text"),
    FieldSpec("declaration.check_code", "Mã phân loại kiểm tra", "text"),
    FieldSpec("declaration.customs_office", "Cơ quan hải quan tiếp nhận", "text"),
    FieldSpec("declaration.processing_department_code", "Mã bộ phận xử lý tờ khai", "text"),
    FieldSpec("declaration.representative_hs_code", "Mã HS đại diện", "text"),
    FieldSpec("declaration.reexport_deadline", "Thời hạn tái nhập/ tái xuất", "text"),
    FieldSpec("declaration.importer_tax_code", "Mã người nhập khẩu", "text"),
    FieldSpec("declaration.importer_name", "Người nhập khẩu", "text", critical=True),
    FieldSpec("declaration.importer_postcode", "Mã bưu chính người nhập khẩu", "text"),
    FieldSpec("declaration.importer_address", "Địa chỉ người nhập khẩu", "text"),
    FieldSpec("declaration.importer_phone", "Điện thoại người nhập khẩu", "text"),
    FieldSpec("declaration.entrusted_importer_tax_code", "Mã người ủy thác nhập khẩu", "text"),
    FieldSpec("declaration.entrusted_importer_name", "Tên người ủy thác nhập khẩu", "text"),
    FieldSpec("declaration.exporter_tax_code", "Mã người xuất khẩu", "text"),
    FieldSpec("declaration.exporter_name", "Người xuất khẩu", "text", critical=True),
    FieldSpec("declaration.exporter_postcode", "Mã bưu chính người xuất khẩu", "text"),
    FieldSpec("declaration.exporter_address", "Địa chỉ người xuất khẩu", "text"),
    FieldSpec("declaration.exporter_country_code", "Mã nước xuất khẩu", "text"),
    FieldSpec("declaration.entrusted_exporter_name", "Tên người ủy thác xuất khẩu", "text"),
    FieldSpec("declaration.customs_agent", "Đại lý hải quan", "text"),
    FieldSpec("declaration.customs_officer_code", "Mã nhân viên hải quan", "text"),
    FieldSpec("declaration.bill_of_lading_no", "Số vận đơn", "text", critical=True),
    FieldSpec("declaration.warehouse_code", "Mã địa điểm lưu kho", "text"),
    FieldSpec("declaration.warehouse_name", "Địa điểm lưu kho", "text"),
    FieldSpec("declaration.discharge_port_code", "Mã địa điểm dỡ hàng", "text"),
    FieldSpec("declaration.discharge_port_name", "Địa điểm dỡ hàng", "text"),
    FieldSpec("declaration.loading_port_code", "Mã địa điểm xếp hàng", "text"),
    FieldSpec("declaration.loading_port_name", "Địa điểm xếp hàng", "text"),
    FieldSpec("declaration.transport_vessel", "Phương tiện vận chuyển", "text"),
    FieldSpec("declaration.cargo_arrival_date", "Ngày hàng đến", "text"),
    FieldSpec("declaration.first_warehouse_entry_date", "Ngày được phép nhập kho đầu tiên", "text"),
    FieldSpec("declaration.package_count", "Số lượng kiện", "decimal", critical=True),
    FieldSpec("declaration.package_unit", "Đơn vị số lượng kiện", "text"),
    FieldSpec("declaration.marks_and_numbers", "Ký hiệu và số hiệu", "text"),
    FieldSpec("declaration.gross_weight", "Tổng trọng lượng gross", "decimal", critical=True),
    FieldSpec("declaration.gross_weight_unit", "Đơn vị trọng lượng", "text"),
    FieldSpec("declaration.container_count", "Số lượng container", "int"),
    FieldSpec("declaration.legal_document_codes", "Mã văn bản pháp quy khác", "text"),
    FieldSpec("declaration.invoice_no", "Số hóa đơn", "text", critical=True),
    FieldSpec("declaration.e_invoice_receipt_no", "Số tiếp nhận hóa đơn điện tử", "text"),
    FieldSpec("declaration.invoice_date", "Ngày phát hành hóa đơn", "text"),
    FieldSpec("declaration.payment_method", "Phương thức thanh toán", "text"),
    FieldSpec("declaration.invoice_terms", "Điều khoản hóa đơn", "text"),
    FieldSpec("declaration.invoice_total", "Tổng trị giá hóa đơn", "decimal", critical=True),
    FieldSpec("declaration.total_customs_value", "Tổng trị giá tính thuế", "decimal"),
    FieldSpec("declaration.total_allocation_factor", "Tổng hệ số phân bổ trị giá", "decimal"),
    FieldSpec("declaration.content_check_result_code", "Mã kết quả kiểm tra nội dung", "text"),
    FieldSpec("declaration.valuation_shipper_note", "Chi tiết khai trị giá", "text"),
    FieldSpec("declaration.tax_total_payable", "Tổng tiền thuế phải nộp", "decimal"),
    FieldSpec("declaration.tax_guarantee_amount", "Số tiền bảo lãnh", "decimal"),
    FieldSpec("declaration.tax_exchange_rate_currency", "Tỷ giá tính thuế - đồng tiền", "currency"),
    FieldSpec("declaration.tax_exchange_rate", "Tỷ giá tính thuế", "decimal", critical=True),
    FieldSpec("declaration.tax_deadline_code", "Mã xác định thời hạn nộp thuế", "text"),
    FieldSpec("declaration.audit_request_reason_code", "Mã lý do đề nghị BP", "text"),
    FieldSpec("declaration.taxpayer_code", "Người nộp thuế", "text"),
    FieldSpec("declaration.tax_payment_classification", "Phân loại nộp thuế", "text"),
    FieldSpec("declaration.user_ref_number", "Số quản lý người sử dụng", "text"),
    FieldSpec("declaration.company_internal_ref", "Số quản lý nội bộ doanh nghiệp", "text"),
    FieldSpec("declaration.customs_directive_code", "Phân loại chỉ thị của hải quan", "text"),
    FieldSpec("declaration.note", "Phần ghi chú", "text"),
    FieldSpec("declaration.customs_chief_name", "Tên trưởng đơn vị hải quan", "text"),
    FieldSpec("declaration.licensed_at", "Ngày cấp phép", "text"),
    FieldSpec("declaration.inspection_finished_at", "Ngày hoàn thành kiểm tra", "text"),
    FieldSpec("declaration.post_clearance_classification", "Phân loại thẩm tra sau thông quan", "text"),
    FieldSpec("declaration.expected_days_to_license", "Số ngày mong đợi đến khi cấp phép nhập khẩu", "text"),
    FieldSpec("declaration.late_tax_total", "Tổng số tiền thuế chậm nộp", "decimal"),
)

GOODS_FIELDS = (
    FieldSpec("goods.hs_code", "Mã số hàng hóa", "text", critical=True),
    FieldSpec("goods.private_management_code", "Mã quản lý riêng", "text"),
    FieldSpec("goods.description", "Mô tả hàng hóa", "text", critical=True),
    FieldSpec("goods.adjustment_item_no", "Số của mục khai khoản điều chỉnh", "text"),
    FieldSpec("goods.quantity_1", "Số lượng (1)", "decimal", critical=True),
    FieldSpec("goods.quantity_1_unit", "Đơn vị số lượng (1)", "text"),
    FieldSpec("goods.quantity_2", "Số lượng (2)", "decimal"),
    FieldSpec("goods.quantity_2_unit", "Đơn vị số lượng (2)", "text"),
    FieldSpec("goods.invoice_amount", "Trị giá hóa đơn", "decimal", critical=True),
    FieldSpec("goods.unit_price", "Đơn giá hóa đơn", "decimal", critical=True),
    FieldSpec("goods.unit_price_currency", "Đồng tiền đơn giá", "currency"),
    FieldSpec("goods.customs_value", "Trị giá tính thuế", "decimal", critical=True),
    FieldSpec("goods.customs_value_foreign", "Trị giá tính thuế (M) - ngoại tệ", "decimal"),
    FieldSpec("goods.customs_unit_price", "Đơn giá tính thuế", "decimal"),
    FieldSpec("goods.tax_quantity", "Số lượng tính thuế", "decimal"),
    FieldSpec("goods.import_duty_rate", "Thuế suất nhập khẩu", "text"),
    FieldSpec("goods.import_duty_rate_code", "Mã loại thuế suất nhập khẩu", "text"),
    FieldSpec("goods.absolute_tax_code", "Mã áp dụng thuế tuyệt đối", "text"),
    FieldSpec("goods.origin_country_code", "Mã nước xuất xứ", "text", critical=True),
    FieldSpec("goods.origin_country_name", "Tên nước xuất xứ", "text"),
    FieldSpec("goods.origin_preference_code", "Mã ưu đãi xuất xứ", "text"),
    FieldSpec("goods.duty_exemption_amount", "Số tiền miễn giảm thuế nhập khẩu", "decimal"),
    FieldSpec("goods.duty_exemption_list", "Danh mục miễn thuế nhập khẩu", "text"),
    FieldSpec("goods.duty_exemption_note", "Miễn / Giảm / Không chịu thuế nhập khẩu", "text"),
)

GOODS_TAX_FIELDS = (
    FieldSpec("goods_tax.name", "Tên thuế và thu khác", "text"),
    FieldSpec("goods_tax.rate_code", "Mã áp dụng thuế suất (thuế khác)", "text"),
    FieldSpec("goods_tax.base", "Trị giá tính thuế (thuế khác)", "decimal"),
    FieldSpec("goods_tax.quantity", "Số lượng tính thuế (thuế khác)", "decimal"),
    FieldSpec("goods_tax.rate", "Thuế suất (thuế khác)", "text"),
    FieldSpec("goods_tax.amount", "Số tiền thuế (thuế khác)", "decimal"),
    FieldSpec("goods_tax.exemption_amount", "Số tiền miễn giảm (thuế khác)", "decimal"),
    FieldSpec("goods_tax.exemption_note", "Miễn / Giảm / Không chịu thuế và thu khác", "text"),
)

TAX_FIELDS = (
    FieldSpec("tax.name", "Tên sắc thuế", "text"),
    FieldSpec("tax.amount", "Tổng tiền thuế", "decimal"),
    FieldSpec("tax.line_count", "Số dòng tổng", "int"),
    FieldSpec("tax.currency", "Đồng tiền thuế", "currency"),
)

LICENSE_FIELDS = (
    FieldSpec("license.code", "Mã giấy phép nhập khẩu", "text"),
    FieldSpec("license.number", "Số giấy phép nhập khẩu", "text"),
)

INSTRUCTION_FIELDS = (
    FieldSpec("instruction.date", "Ngày chỉ thị hải quan", "text"),
    FieldSpec("instruction.name", "Tên chỉ thị hải quan", "text"),
    FieldSpec("instruction.content", "Nội dung chỉ thị hải quan", "text"),
)

ADJUSTMENT_FIELDS = (
    FieldSpec("adjustment.code_name", "Mã tên khoản điều chỉnh", "text"),
    FieldSpec("adjustment.classification_code", "Mã phân loại khoản điều chỉnh", "text"),
    FieldSpec("adjustment.value", "Trị giá khoản điều chỉnh", "decimal"),
    FieldSpec("adjustment.allocation_factor", "Tổng hệ số phân bổ điều chỉnh", "decimal"),
)

TOKHAI_FIELDS = (
    DECLARATION_FIELDS
    + GOODS_FIELDS
    + GOODS_TAX_FIELDS
    + TAX_FIELDS
    + LICENSE_FIELDS
    + INSTRUCTION_FIELDS
    + ADJUSTMENT_FIELDS
)

TOKHAI_DOC_TYPES = ("CUSTOMS_DECLARATION", "CUSTOMS_DECLARATION_SCAN")


def register() -> None:
    register_extra_fields(
        TOKHAI_FIELDS,
        doc_types=("CUSTOMS_DECLARATION", "CUSTOMS_DECLARATION_SCAN", "EXPORT_DECLARATION"),
    )


register()
