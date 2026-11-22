# Kiểm tra chéo chứng từ Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sau khi xử lý một lô hàng, dùng LLM tìm thông tin lệch nhau giữa các chứng từ và hiển thị cảnh báo (không chặn export).

**Architecture:** Service mới `app/services/consistency.py` gom `ExtractedField` của các chứng từ business đang hiệu lực, gửi cho model text qua `LLMClient.complete_json_validated`, lọc finding bịa phía server, lưu vào 2 bảng mới. Chạy tự động cuối `run_process_job` và qua API. Frontend thêm tab "Kiểm tra chéo" ở pane phải của ReviewPage và Callout ở ExportPage.

**Tech Stack:** FastAPI, SQLAlchemy async (SQLite), pydantic v2, pytest-asyncio; React + TanStack Query + Vitest.

**Spec:** `docs/superpowers/specs/2026-11-23-cross-document-check-design.md`

## Global Constraints
- Không chặn export; chỉ cảnh báo.
- Finding chỉ được hiển thị khi mọi `field_id` thuộc input của lần chạy đó VÀ các field_ids trải trên ≥ 2 document khác nhau.
- Giá trị hiển thị lấy từ DB (`ExtractedField`), không lấy từ text LLM.
- Lỗi của bước kiểm tra không được làm job xử lý FAILED và không xoá kết quả trích xuất/resolution.
- Hash input không đổi so với lần chạy DONE gần nhất → không gọi LLM (trừ `force=True`).
- Text UI tiếng Việt. Model: `llm.settings.text_model`, không gửi ảnh.
- Không commit trừ khi người dùng yêu cầu.

## Review Focus
1. Lô chỉ có 1 chứng từ business hiệu lực → không gọi LLM, run DONE, 0 finding (test ở Task 1).
2. LLM trả `field_ids` thuộc chứng từ đã bị thay thế (`SUPERSEDED_BY`) hoặc id không tồn tại → finding bị loại (Task 1).
3. Người dùng tải bản chứng từ đã sửa, bản cũ thành `SUPERSEDED_BY` → lần chạy sau hash đổi, finding cũ biến mất (Task 1).
4. LLM timeout / JSON sai → run FAILED có `error`, findings của lần DONE trước vẫn hiển thị, job vẫn DONE (Task 1 + Task 2).
5. Người dùng sửa giá trị một trường ở Review → bấm "Kiểm tra lại" thực sự gọi lại LLM vì hash đổi (Task 2).

---

### Task 1: Models + service kiểm tra chéo

**Files:**
- Modify: `src/backend/app/models.py` (thêm 2 class cuối file)
- Create: `src/backend/app/services/consistency.py`
- Test: `src/backend/tests/test_consistency.py`

**Interfaces:**
- Produces (models):
  - `ConsistencyRun` (`consistency_runs`): `id`, `shipment_id` (FK, index), `status: str` ("DONE"|"FAILED"), `input_hash: str(64)`, `error: Text|None`, `finding_count: int=0`, `created_at`.
  - `ConsistencyFinding` (`consistency_findings`): `id`, `run_id` (FK), `shipment_id` (FK, index), `severity: str(16)`, `category: str(64)`, `title: str(512)`, `explanation: Text`, `field_ids: JSON (list[int], sorted)`, `dismissed: bool=False`, `dismiss_note: Text|None`, `dismissed_at: DateTime|None`, `created_at`.
  - Bảng mới được `create_all` tạo tự động (db.py đã xử lý bảng/cột mới).
- Produces (service, `app/services/consistency.py`):
  - `@dataclass CheckField: id:int, document_id:int, filename:str, doc_type:str, page:int|None, field_key:str, value:str, line_seq:int|None`
  - `class FindingOut(BaseModel): severity: Literal["ERROR","WARNING"]; category: str; title: str; explanation: str; field_ids: list[int]`
  - `class ConsistencyOut(BaseModel): findings: list[FindingOut]`
  - `async def collect_fields(session, shipment_id: int) -> list[CheckField]` — chỉ Document có `status == "EXTRACTED"` và `resolution.document_role(doc) == ROLE_BUSINESS_INPUT`; bỏ giá trị rỗng/`"None"`; value = normalized_value ?? raw_value; `line_seq` = `LineItem.seq` nếu `line_item_id`. Sắp theo (document_id, line_seq or -1, field_key, id).
  - `def input_hash(fields: list[CheckField]) -> str` — sha256 của JSON (id, document_id, doc_type, field_key, value).
  - `def build_batches(fields, max_fields: int = 400) -> list[list[CheckField]]` — field `line_seq is None` (header) có trong mọi batch; field dòng hàng chia theo nhóm (document_id, line_seq) giữ nguyên nhóm, mỗi batch ≤ max_fields tính cả header (luôn ≥ 1 nhóm/batch).
  - `def ground_findings(findings: list[FindingOut], fields_by_id: dict[int, CheckField]) -> list[FindingOut]` — loại finding có id ngoài input hoặc < 2 document_id khác nhau; chuẩn hoá `field_ids` sorted unique; khử trùng theo tuple field_ids (giữ bản đầu).
  - `async def run_consistency_check(session, llm, shipment_id: int, force: bool = False) -> ConsistencyRun` — commit trước khi trả.
  - `async def consistency_report(session, shipment_id: int) -> dict` — shape ở Task 2.

- [ ] **Step 1: Write failing tests** trong `tests/test_consistency.py` dùng fixture `db_session` (conftest) và một `StubLLM` có `settings = SimpleNamespace(text_model="sonya", max_tokens=32000)`, `calls: list[str]`, `complete_json_validated(...)` ghi prompt vào `calls` và trả `validator(self.response)` (hoặc raise `self.error` nếu set). Helper `_seed(session)` tạo Shipment + Document 1 (PACKING_LIST, EXTRACTED) với `transport.gross_weight_kg = "26400"` và Document 2 (TRANSPORT_DOCUMENT, EXTRACTED) với `transport.gross_weight_kg = "26040"`, trả các id.

```python
async def test_single_document_skips_llm(db_session):      # chỉ seed doc 1
    run = await run_consistency_check(db_session, llm, shipment_id)
    assert run.status == "DONE" and run.finding_count == 0 and llm.calls == []

async def test_grounded_finding_is_stored(db_session):
    llm.response = {"findings": [{"severity": "ERROR", "category": "weight", "title": "Gross lệch",
                                  "explanation": "...", "field_ids": [f2, f1]}]}
    run = await run_consistency_check(db_session, llm, sid)
    report = await consistency_report(db_session, sid)
    assert run.finding_count == 1
    assert [v["value"] for v in report["findings"][0]["values"]] == ["26400", "26040"]
    assert report["counts"] == {"error": 1, "warning": 0}

async def test_hallucinated_ids_and_single_document_findings_are_dropped(db_session):
    # field_ids [f1, 999999] và [f1] (chỉ 1 document) -> cả hai bị loại
    assert run.finding_count == 0

async def test_superseded_document_fields_are_excluded(db_session):
    # doc2.status = "SUPERSEDED_BY" -> collect_fields không chứa f2; LLM không được gọi (còn 1 doc)

async def test_unchanged_input_skips_llm_and_force_reruns(db_session):
    await run_consistency_check(...); await run_consistency_check(...)
    assert len(llm.calls) == 1
    await run_consistency_check(..., force=True)
    assert len(llm.calls) == 2

async def test_dismissal_survives_rerun_for_same_fields(db_session):
    # dismiss finding (set dismissed=True, dismiss_note="ok"), sửa value f1 -> "26401", chạy lại cùng response
    assert report["findings"][0]["dismissed"] is True

async def test_llm_failure_records_failed_run_and_keeps_previous_findings(db_session):
    # lần 1 DONE có 1 finding; đổi value để hash đổi; llm.error = LLMError("boom")
    run = await run_consistency_check(...)
    assert run.status == "FAILED" and "boom" in run.error
    report = await consistency_report(db_session, sid)
    assert report["run"]["status"] == "FAILED" and len(report["findings"]) == 1

def test_build_batches_keeps_header_in_every_batch():
    # 2 header + 3 nhóm dòng hàng x 2 field, max_fields=4 -> 3 batch, mỗi batch có đủ 2 header
```

- [ ] **Step 2: Run to verify fail**
  Run: `cd src/backend && python -m pytest tests/test_consistency.py -v`
  Expected: FAIL (ImportError `app.services.consistency`).

- [ ] **Step 3: Add models** `ConsistencyRun`, `ConsistencyFinding` theo Interfaces.

- [ ] **Step 4: Implement `app/services/consistency.py`**
  - `run_consistency_check`: collect → hash → nếu không force và run DONE mới nhất cùng hash → trả run đó. Nếu < 2 document_id khác nhau → tạo run DONE, xoá findings cũ, 0 finding. Ngược lại với mỗi batch gọi `llm.complete_json_validated(prompt, "consistency_check", ConsistencyOut.model_json_schema(), ConsistencyOut.model_validate, model_override=llm.settings.text_model)`; gộp và `ground_findings`. Thành công: lưu tập `field_ids` đã dismiss của findings cũ (kèm note/thời điểm), xoá findings cũ của shipment, ghi findings mới (copy dismiss nếu trùng field_ids), run DONE. `LLMError` (gồm Timeout/Truncated) → run FAILED với `error=str(exc)`, giữ findings cũ.
  - Prompt (tiếng Việt): vai trò kiểm tra đối chiếu chứng từ nhập khẩu; dữ liệu là JSON list `{id, doc_type, filename, page, field, line, value}`; chỉ báo khi CÙNG một thông tin (bên bán/mua, số B/L, container, số kiện, trọng lượng, CBM, cảng, tàu, số invoice, tiền tệ, tổng tiền, dòng hàng) ghi khác nhau giữa ≥ 2 chứng từ; giá trị số đã được chuẩn hoá (dấu chấm thập phân) — không báo khác biệt chỉ do định dạng hay viết tắt pháp nhân (CO., LTD = COMPANY LIMITED); ERROR khi chắc chắn lệch, WARNING khi nghi ngờ; mỗi finding liệt kê `field_ids` của MỌI giá trị liên quan; không có lỗi → `{"findings": []}`.
  - `consistency_report`: run mới nhất (hoặc `None`), findings của shipment sắp ERROR trước rồi theo id; `values` dựng từ `ExtractedField` + `Document` hiện có (bỏ id không còn tồn tại): `{field_id, document_id, filename, doc_type, page, bbox, cell_ref, field_key, value}`; `counts` chỉ đếm finding chưa dismiss.

- [ ] **Step 5: Run tests** — Run: `cd src/backend && python -m pytest tests/test_consistency.py -v` → Expected: all PASS. Rồi `python -m pytest -q` → không có test cũ nào hỏng.

---

### Task 2: Gắn vào job runner + API

**Files:**
- Modify: `src/backend/app/services/job_runner.py:209-235` (sau resolution, trước `refresh_shipment_status`)
- Create: `src/backend/app/api/consistency.py`
- Modify: `src/backend/app/main.py:141` (include router)
- Test: `src/backend/tests/test_consistency_api.py`

**Interfaces:**
- Consumes: `run_consistency_check`, `consistency_report`, `ConsistencyFinding` (Task 1).
- Produces (HTTP, prefix `/api/v1`, tag `consistency`):
  - `GET /shipments/{shipment_id}/consistency` → `{"run": {"id","status","error","created_at","finding_count"} | null, "findings": [{"id","severity","category","title","explanation","dismissed","dismiss_note","values":[...]}], "counts": {"error": int, "warning": int}}`; 404 nếu shipment không tồn tại.
  - `POST /shipments/{shipment_id}/consistency/run` → chạy `force=False` (đồng bộ), trả cùng shape như GET. 404 nếu không có shipment.
  - `POST /consistency-findings/{finding_id}/dismiss` body `{"note": str | null}` → đặt `dismissed=True`, `dismiss_note`, `dismissed_at`; `record_audit(session, "consistency_finding", id, "DISMISSED", payload={"note": note})`; trả report của shipment. 404 nếu không có finding.

- [ ] **Step 1: Write failing tests** (`test_consistency_api.py`), theo mẫu `tests/test_api_flow.py` (`create_app`, `ASGITransport`, override `get_llm` bằng stub như Task 1, seed qua session của app):

```python
async def test_get_consistency_empty_shipment_returns_null_run(client):
    r = await client.get(f"/api/v1/shipments/{sid}/consistency")
    assert r.status_code == 200 and r.json() == {"run": None, "findings": [], "counts": {"error": 0, "warning": 0}}

async def test_run_then_dismiss(client):
    body = (await client.post(f"/api/v1/shipments/{sid}/consistency/run")).json()
    fid = body["findings"][0]["id"]
    body = (await client.post(f"/api/v1/consistency-findings/{fid}/dismiss", json={"note": "đúng"})).json()
    assert body["findings"][0]["dismissed"] is True and body["counts"]["error"] == 0

async def test_edited_field_triggers_new_llm_call(client):
    # run; PATCH /shipments/{sid}/fields/{f1} value "26401"; run again -> stub.calls == 2

async def test_unknown_ids_return_404(client):
    assert (await client.get("/api/v1/shipments/9999/consistency")).status_code == 404
    assert (await client.post("/api/v1/consistency-findings/9999/dismiss", json={})).status_code == 404
```

  Thêm vào `tests/test_worker.py` (hoặc file test job runner hiện có dùng `run_process_job`): `test_consistency_failure_does_not_fail_job` — monkeypatch `app.services.consistency.run_consistency_check` để raise `RuntimeError`, job vẫn `DONE` và `job.error` không chứa lỗi trích xuất.

- [ ] **Step 2: Run to verify fail** — `cd src/backend && python -m pytest tests/test_consistency_api.py -v` → FAIL (404 route).

- [ ] **Step 3: Implement router** `app/api/consistency.py` dùng `get_session`, `get_llm` từ `..deps`; include trong `main.py`.

- [ ] **Step 4: Hook job runner** — trong block `if shipment_id:` sau try resolution: `try: await run_consistency_check(session, llm, shipment_id) except Exception: log.exception(...); await session.rollback()`. Không thêm vào `job.error` (cảnh báo, không phải lỗi job).

- [ ] **Step 5: Run tests** — `python -m pytest tests/test_consistency_api.py tests/test_worker.py -v` rồi `python -m pytest -q` → all PASS.

---

### Task 3: Frontend — tab "Kiểm tra chéo" và nhắc ở Export

**Files:**
- Modify: `src/frontend/src/api/types.ts`, `src/frontend/src/api/client.ts`
- Create: `src/frontend/src/components/ConsistencyPanel.tsx`
- Modify: `src/frontend/src/pages/ReviewPage.tsx:610,836-860,996-1018`
- Modify: `src/frontend/src/pages/ExportPage.tsx`
- Test: `src/frontend/src/components/ConsistencyPanel.test.tsx`, `src/frontend/src/pages/ReviewPage.test.tsx`, `src/frontend/src/pages/ExportPage.test.tsx`

**Interfaces:**
- Consumes: HTTP shape Task 2.
- Produces:
  - types: `ConsistencyValue`, `ConsistencyFinding`, `ConsistencyReport` (khớp JSON Task 2).
  - `api.getConsistency(shipmentId): Promise<ConsistencyReport>`, `api.runConsistency(shipmentId)`, `api.dismissFinding(findingId, note?: string)`.
  - `ConsistencyPanel({ shipmentId, onValueClick }: { shipmentId: number; onValueClick: (v: ConsistencyValue) => void })` — query key `["consistency", shipmentId]`.

- [ ] **Step 1: Write failing tests** (mock `../api/client` như ReviewPage.test.tsx):

```tsx
it("lists findings with values per document and counts", ...)
  // report 1 ERROR với 2 values -> thấy "Gross lệch", "packing-list.pdf · tr.1", "26400", "26040", "1 lỗi"
it("calls onValueClick when a value is clicked", ...)
it("re-runs the check", ...)          // bấm "Kiểm tra lại" -> api.runConsistency(1)
it("dismisses a finding", ...)        // bấm "Bỏ qua" -> api.dismissFinding(id, undefined); finding hiện "Đã bỏ qua"
it("shows the failure notice when the last run failed", ...) // run.status FAILED -> "Lần kiểm tra gần nhất thất bại"
it("shows an empty state when there are no findings", ...)  // "Không phát hiện thông tin lệch giữa các chứng từ"
```
  ReviewPage.test.tsx: `it("switches to the cross-check pane")` — bấm nút "Kiểm tra chéo" → panel hiển thị (thêm `getConsistency` vào mock).
  ExportPage.test.tsx: `it("warns about unresolved cross-check findings but keeps export enabled")` — counts `{error:1, warning:0}` → thấy Callout chứa "1 lỗi kiểm tra chéo", nút xuất vẫn enabled.

- [ ] **Step 2: Run to verify fail** — `cd src/frontend && npx vitest run src/components/ConsistencyPanel.test.tsx src/pages/ReviewPage.test.tsx src/pages/ExportPage.test.tsx` → FAIL.

- [ ] **Step 3: Implement**
  - Types + client methods.
  - `ConsistencyPanel`: header "Kiểm tra chéo" + `"{n} lỗi · {m} cảnh báo"` + nút "Kiểm tra lại" (disabled khi đang chạy, invalidate query sau khi xong); mỗi finding: chấm màu (ERROR đỏ, WARNING cam), title, explanation, danh sách value `"{filename} · tr.{page}: {value}"` dạng button gọi `onValueClick`, nút "Bỏ qua"; finding đã dismiss hiện mờ với "Đã bỏ qua". Dùng `Callout`, `EmptyState` từ `components/ui.tsx` và các class typography sẵn có.
  - ReviewPage: `rightPane` thêm `"consistency"`; thêm tab `{ key: "consistency", label: "Kiểm tra chéo" }`; render `ConsistencyPanel` với `onValueClick` = `setActiveDocId(v.document_id)` + `setEvidence({documentId, page, bbox, cellRef: v.cell_ref, label: v.field_key})` + `setEvidenceField(v.field_key)`.
  - ExportPage: query `getConsistency`; nếu `counts.error + counts.warning > 0` hiện `Callout tone="warning"`: "Còn {error} lỗi kiểm tra chéo và {warning} cảnh báo giữa các chứng từ. Vẫn có thể xuất file." kèm link về `/shipments/:id/review`. Không đổi điều kiện enable nút xuất.

- [ ] **Step 4: Run tests** — lệnh Step 2 → PASS; rồi `npx vitest run` và `npx tsc -b` → không lỗi.
