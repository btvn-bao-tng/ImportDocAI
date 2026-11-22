# Kiểm tra chéo chứng từ (Cross-document consistency check) — Design

Date: 2026-11-23 · Status: approved

## Intent
Các chứng từ trong một lô hàng (Invoice, Packing List, B/L, Arrival Notice, Hợp đồng, C/O…)
phải đồng nhất. Tính năng này **cảnh báo** khi chúng ghi lệch nhau để người dùng tự sửa
chứng từ gốc và tải lại bản mới (bản mới thay thế bản cũ qua logic `duplicates.py` sẵn có;
kiểm tra chạy lại và cảnh báo cũ biến mất).

## Goals / Non-goals / Anti-goals
- Goal: danh sách lỗi lệch giữa các chứng từ, mỗi lỗi chỉ rõ chứng từ, trang, giá trị.
- Non-goal: tự sửa dữ liệu, chặn export, gửi báo cáo cho shipper.
- Anti-goal: lỗi "bịa" (tham chiếu dữ liệu không tồn tại) xuất hiện trên UI; kiểm tra lỗi làm hỏng job xử lý.

## Approaches
- A. Bộ quy tắc deterministic — ổn định, giải thích được, nhưng chỉ bắt lỗi đã có quy tắc.
- **B. LLM tìm mâu thuẫn — người dùng chọn.** Linh hoạt, bắt cả mâu thuẫn ngữ nghĩa; rủi ro bịa/không ổn định được giảm bằng kiểm tra phía server.
- C. Mở rộng `AMBIGUOUS` của resolution — ít code nhưng chỉ phủ trường tờ khai, không có dòng hàng/tổng.

## Design (decided)
- **Input:** `ExtractedField` của mọi Document đang hiệu lực trong shipment (loại trừ status
  `IS_DUPLICATE_OF`, `SUPERSEDED_BY`, và chưa EXTRACTED). Mỗi trường: `id`, doc_type,
  filename, page, field_key, giá trị (normalized_value ưu tiên, fallback raw_value), line item
  index nếu có. Không gửi ảnh. Model text (`settings.text_model`).
- **Output schema (JSON):** `findings: [{severity: ERROR|WARNING, category, title, explanation, field_ids: [int]}]`,
  validate bằng pydantic qua `LLMClient.complete_json_validated`.
- **Grounding phía server:** bỏ finding nếu có `field_id` không thuộc input, hoặc các field_ids
  không trải trên ≥ 2 document khác nhau. Giá trị hiển thị lấy từ DB.
- **Trigger:** tự động cuối `run_process_job` sau resolution (lỗi chỉ ghi vào run, không làm
  fail job); nút "Kiểm tra lại" gọi API. Hash input không đổi so với run thành công gần nhất → không gọi LLM.
- **Storage:** bảng `consistency_runs` (shipment_id, status DONE/FAILED, input_hash, error,
  created_at) và `consistency_findings` (run_id, shipment_id, severity, category, title,
  explanation, field_ids JSON, dismissed, dismiss_note, dismissed_at). Lần chạy mới thay findings
  cũ; dismiss được giữ lại nếu finding mới cùng tập field_ids.
- **API:** `GET /shipments/{id}/consistency`, `POST /shipments/{id}/consistency/run`,
  `POST /consistency-findings/{id}/dismiss` (body: note).
- **UI:** panel "Kiểm tra chéo" trên ReviewPage (đếm lỗi/cảnh báo, nút Kiểm tra lại, mỗi finding
  liệt kê giá trị theo chứng từ + trang, bấm để mở chứng từ, nút Bỏ qua). ExportPage hiện Callout
  nhắc nếu còn finding chưa bỏ qua; không chặn export.

## Calls made by Claude (correctable)
- Có chức năng "Bỏ qua" cho báo sai của LLM.
- Không chặn export.
- Input quá lớn → chia batch theo nhóm (header fields gửi kèm mọi batch, line items chia nhỏ).

## Left to the builder
Wording prompt, giới hạn kích thước batch, bố cục chi tiết của panel.
