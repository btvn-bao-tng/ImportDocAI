from __future__ import annotations

"""Cross-document consistency check (kiểm tra chéo chứng từ).

The documents of one shipment must agree with each other. This module asks the
text model to point out values that disagree across documents and stores the
answer as warnings. It never changes extracted data and never blocks an export.

Guard rails, because the model can invent problems:
- the model only sees field ids + values we send, and must cite those ids;
- a finding citing an unknown id, or ids from fewer than two documents, is dropped;
- the values shown to the user are read back from the database, not from the model.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ConsistencyFinding, ConsistencyRun, Document, ExtractedField, LineItem
from .llm import LLMError
from .resolution import document_role
from .target_catalog import ROLE_BUSINESS_INPUT

log = logging.getLogger("importdoc.consistency")

ACTIVE_DOCUMENT_STATUS = "EXTRACTED"
DEFAULT_BATCH_FIELDS = 400
SEVERITY_ORDER = {"ERROR": 0, "WARNING": 1}


@dataclass(frozen=True)
class CheckField:
    id: int
    document_id: int
    filename: str
    doc_type: str
    page: int | None
    field_key: str
    value: str
    line_seq: int | None


class FindingOut(BaseModel):
    severity: Literal["ERROR", "WARNING"]
    category: str
    title: str
    explanation: str = ""
    field_ids: list[int] = Field(default_factory=list)


class ConsistencyOut(BaseModel):
    findings: list[FindingOut] = Field(default_factory=list)


def parse_consistency(raw: Any) -> ConsistencyOut:
    """Lenient validator: one malformed item is skipped instead of failing the run.

    Only a response without a `findings` list is rejected (and retried by the client).
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("findings"), list):
        raise ValueError('response must be an object with a "findings" list')
    out: list[FindingOut] = []
    for item in raw["findings"]:
        if not isinstance(item, dict):
            continue
        ids = item.get("field_ids")
        if not isinstance(ids, list):
            continue
        try:
            field_ids = [int(i) for i in ids]
        except (TypeError, ValueError):
            continue
        title = str(item.get("title") or "").strip()
        if not title or not field_ids:
            continue
        severity = str(item.get("severity") or "").strip().upper()
        out.append(
            FindingOut(
                severity=severity if severity in SEVERITY_ORDER else "WARNING",
                category=str(item.get("category") or "other").strip().lower() or "other",
                title=title,
                explanation=str(item.get("explanation") or "").strip(),
                field_ids=field_ids,
            )
        )
    return ConsistencyOut(findings=out)


async def collect_fields(session: AsyncSession, shipment_id: int) -> list[CheckField]:
    docs = (
        await session.execute(
            select(Document).where(Document.shipment_id == shipment_id, Document.status == ACTIVE_DOCUMENT_STATUS)
        )
    ).scalars().all()
    docs_by_id = {d.id: d for d in docs if document_role(d) == ROLE_BUSINESS_INPUT}
    if not docs_by_id:
        return []
    rows = (
        await session.execute(
            select(ExtractedField, LineItem.seq)
            .outerjoin(LineItem, ExtractedField.line_item_id == LineItem.id)
            .where(ExtractedField.document_id.in_(docs_by_id))
        )
    ).all()
    out: list[CheckField] = []
    for field, seq in rows:
        value = field.normalized_value if field.normalized_value is not None else field.raw_value
        if value is None or str(value).strip() in ("", "None"):
            continue
        doc = docs_by_id[field.document_id]
        out.append(
            CheckField(
                id=field.id,
                document_id=doc.id,
                filename=doc.filename,
                doc_type=doc.doc_type or "OTHER",
                page=field.page,
                field_key=field.field_key,
                value=str(value).strip(),
                line_seq=seq if field.line_item_id is not None else None,
            )
        )
    out.sort(key=lambda f: (f.document_id, -1 if f.line_seq is None else f.line_seq, f.field_key, f.id))
    return out


def input_hash(fields: list[CheckField]) -> str:
    payload = [(f.id, f.document_id, f.doc_type, f.field_key, f.value) for f in fields]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()


def build_batches(fields: list[CheckField], max_fields: int = DEFAULT_BATCH_FIELDS) -> list[list[CheckField]]:
    header = [f for f in fields if f.line_seq is None]
    # Group by line number across documents, so line N of the invoice and line N of
    # the packing list always land in the same batch and can be compared.
    groups: dict[int, list[CheckField]] = {}
    for f in fields:
        if f.line_seq is not None:
            groups.setdefault(f.line_seq, []).append(f)
    if not groups:
        return [header] if header else []
    batches: list[list[CheckField]] = []
    current: list[CheckField] = []
    for _, group in sorted(groups.items()):
        if current and len(header) + len(current) + len(group) > max_fields:
            batches.append(header + current)
            current = []
        current.extend(group)
    if current:
        batches.append(header + current)
    return batches


def ground_findings(findings: list[FindingOut], fields_by_id: dict[int, CheckField]) -> list[FindingOut]:
    out: list[FindingOut] = []
    seen: set[tuple[int, ...]] = set()
    # Header fields are repeated in every batch, so the same header mismatch can come
    # back from several batches with different id subsets: merge them by field keys.
    header_index: dict[frozenset[str], int] = {}
    for finding in findings:
        ids = tuple(sorted(set(finding.field_ids)))
        if not ids or any(i not in fields_by_id for i in ids):
            continue
        if len({fields_by_id[i].document_id for i in ids}) < 2:
            continue
        if ids in seen:
            continue
        seen.add(ids)
        if all(fields_by_id[i].line_seq is None for i in ids):
            keys = frozenset(fields_by_id[i].field_key for i in ids)
            if keys in header_index:
                pos = header_index[keys]
                prior = out[pos]
                merged = sorted(set(prior.field_ids) | set(ids))
                severity = min(prior.severity, finding.severity, key=lambda v: SEVERITY_ORDER[v])
                out[pos] = prior.model_copy(update={"field_ids": merged, "severity": severity})
                continue
            header_index[keys] = len(out)
        out.append(finding.model_copy(update={"field_ids": list(ids)}))
    return out


def finding_signature(field_ids: list[int], fields_by_id: dict[int, CheckField]) -> list[list]:
    return sorted(
        ([f.document_id, f.field_key, f.line_seq] for f in (fields_by_id[i] for i in field_ids)),
        key=lambda row: (row[0], row[1], -1 if row[2] is None else row[2]),
    )


def _dismiss_key(signature: Any, field_ids: Any) -> str:
    return json.dumps(signature if signature else ["ids", sorted(field_ids or [])], ensure_ascii=False)


def _prompt(batch: list[CheckField]) -> str:
    rows = [
        {
            "id": f.id,
            "doc_type": f.doc_type,
            "filename": f.filename,
            "page": f.page,
            "field": f.field_key,
            "line": f.line_seq,
            "value": f.value,
        }
        for f in batch
    ]
    return (
        "Bạn là chuyên viên kiểm tra đối chiếu bộ chứng từ nhập khẩu của MỘT lô hàng. "
        "Các chứng từ (Invoice, Packing List, B/L, Arrival Notice, Hợp đồng, C/O, ...) phải đồng nhất.\n"
        "Dữ liệu là danh sách trường đã trích xuất; 'line' là số thứ tự dòng hàng (null = thông tin chung).\n"
        "Nhiệm vụ: tìm những chỗ CÙNG một thông tin (bên bán, bên mua, số B/L, số container, số seal, số kiện, "
        "trọng lượng net/gross, CBM, cảng xếp/dỡ, tàu/chuyến, số và ngày invoice, tiền tệ, tổng tiền, "
        "mô tả/số lượng/đơn giá/thành tiền của dòng hàng) bị ghi KHÁC NHAU giữa ít nhất 2 chứng từ.\n"
        "Quy tắc:\n"
        "- Giá trị số đã chuẩn hoá (dấu chấm thập phân). Không báo khác biệt chỉ do định dạng, chữ hoa/thường, "
        "dấu câu hay viết tắt pháp nhân (CO., LTD = COMPANY LIMITED).\n"
        "- Không báo thông tin chỉ xuất hiện ở một chứng từ.\n"
        "- severity ERROR khi chắc chắn lệch, WARNING khi nghi ngờ (ví dụ có thể do chuyển tải, làm tròn).\n"
        "- field_ids phải liệt kê id của MỌI giá trị liên quan, chỉ dùng id có trong dữ liệu.\n"
        "- title ngắn, explanation 1-2 câu, đều bằng tiếng Việt; category là một từ tiếng Anh viết thường "
        "(party, transport, weight, package, port, vessel, invoice, amount, line_item, other).\n"
        'Trả về JSON: {"findings": [{"severity", "category", "title", "explanation", "field_ids"}]}. '
        'Không có lỗi thì trả {"findings": []}.\n\n'
        "Dữ liệu:\n" + json.dumps(rows, ensure_ascii=False)
    )


async def _latest_run(session: AsyncSession, shipment_id: int, status: str | None = None) -> ConsistencyRun | None:
    stmt = select(ConsistencyRun).where(ConsistencyRun.shipment_id == shipment_id)
    if status is not None:
        stmt = stmt.where(ConsistencyRun.status == status)
    return (await session.execute(stmt.order_by(ConsistencyRun.id.desc()).limit(1))).scalar_one_or_none()


async def _replace_findings(
    session: AsyncSession,
    run: ConsistencyRun,
    findings: list[FindingOut],
    fields_by_id: dict[int, CheckField] | None = None,
) -> None:
    old = (
        await session.execute(select(ConsistencyFinding).where(ConsistencyFinding.shipment_id == run.shipment_id))
    ).scalars().all()
    dismissed: dict[str, ConsistencyFinding] = {}
    for r in old:
        if r.dismissed:
            dismissed[_dismiss_key(r.signature, r.field_ids)] = r
            dismissed.setdefault(_dismiss_key(None, r.field_ids), r)
    await session.execute(delete(ConsistencyFinding).where(ConsistencyFinding.shipment_id == run.shipment_id))
    for finding in findings:
        signature = finding_signature(finding.field_ids, fields_by_id) if fields_by_id else None
        prior = dismissed.get(_dismiss_key(signature, finding.field_ids)) or dismissed.get(
            _dismiss_key(None, finding.field_ids)
        )
        session.add(
            ConsistencyFinding(
                run_id=run.id,
                shipment_id=run.shipment_id,
                severity=finding.severity,
                category=finding.category[:64],
                title=finding.title[:512],
                explanation=finding.explanation,
                field_ids=finding.field_ids,
                signature=signature,
                dismissed=prior is not None,
                dismiss_note=prior.dismiss_note if prior else None,
                dismissed_at=prior.dismissed_at if prior else None,
            )
        )
    run.finding_count = len(findings)


async def run_consistency_check(session: AsyncSession, llm, shipment_id: int, force: bool = False) -> ConsistencyRun:
    # Nothing is written before the model answers: an open SQLite write transaction
    # would block every other writer (worker, review edits) for the whole LLM call.
    fields = await collect_fields(session, shipment_id)
    digest = input_hash(fields)
    if not force:
        latest = await _latest_run(session, shipment_id)
        if latest is not None and latest.status == "DONE" and latest.input_hash == digest:
            return latest

    if len({f.document_id for f in fields}) < 2:
        run = ConsistencyRun(shipment_id=shipment_id, status="DONE", input_hash=digest)
        session.add(run)
        await session.flush()
        await _replace_findings(session, run, [])
        await session.commit()
        return run

    # End the read transaction too, so the later write does not upgrade a stale snapshot.
    await session.commit()
    fields_by_id = {f.id: f for f in fields}
    collected: list[FindingOut] = []
    run = ConsistencyRun(shipment_id=shipment_id, status="DONE", input_hash=digest)
    try:
        for batch in build_batches(fields):
            result: ConsistencyOut = await llm.complete_json_validated(
                _prompt(batch),
                "consistency_check",
                ConsistencyOut.model_json_schema(),
                parse_consistency,
                model_override=llm.settings.text_model,
            )
            collected.extend(result.findings)
    except LLMError as exc:
        log.warning("consistency check failed for shipment %s: %s", shipment_id, exc)
        run.status = "FAILED"
        run.error = str(exc)
        session.add(run)
        await session.commit()
        return run

    session.add(run)
    await session.flush()
    await _replace_findings(session, run, ground_findings(collected, fields_by_id), fields_by_id)
    await session.commit()
    return run


def _run_dict(run: ConsistencyRun | None) -> dict | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "status": run.status,
        "error": run.error,
        "finding_count": run.finding_count,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


async def consistency_report(session: AsyncSession, shipment_id: int) -> dict:
    run = await _latest_run(session, shipment_id)
    rows = (
        await session.execute(select(ConsistencyFinding).where(ConsistencyFinding.shipment_id == shipment_id))
    ).scalars().all()
    rows = sorted(rows, key=lambda r: (SEVERITY_ORDER.get(r.severity, 9), r.id))
    current = await collect_fields(session, shipment_id)
    active_ids = {f.id for f in current}
    ids = {i for r in rows for i in (r.field_ids or []) if i in active_ids}
    fields: dict[int, tuple[ExtractedField, Document]] = {}
    if ids:
        for field, doc in (
            await session.execute(
                select(ExtractedField, Document)
                .join(Document, ExtractedField.document_id == Document.id)
                .where(ExtractedField.id.in_(ids))
            )
        ).all():
            fields[field.id] = (field, doc)

    findings = []
    counts = {"error": 0, "warning": 0}
    for r in rows:
        values = []
        for fid in r.field_ids or []:
            pair = fields.get(fid)
            if pair is None:
                continue
            field, doc = pair
            values.append(
                {
                    "field_id": field.id,
                    "document_id": doc.id,
                    "filename": doc.filename,
                    "doc_type": doc.doc_type,
                    "page": field.page,
                    "bbox": field.bbox,
                    "cell_ref": field.cell_ref,
                    "field_key": field.field_key,
                    "value": field.normalized_value if field.normalized_value is not None else field.raw_value,
                }
            )
        values.sort(key=lambda v: (v["document_id"], v["field_id"]))
        # A finding whose fields were deleted (re-extraction) or whose document was
        # superseded no longer compares two documents: it is not shown or counted.
        if len({v["document_id"] for v in values}) < 2:
            continue
        if not r.dismissed:
            counts["error" if r.severity == "ERROR" else "warning"] += 1
        findings.append(
            {
                "id": r.id,
                "severity": r.severity,
                "category": r.category,
                "title": r.title,
                "explanation": r.explanation,
                "dismissed": r.dismissed,
                "dismiss_note": r.dismiss_note,
                "values": values,
            }
        )
    stale = run is not None and run.input_hash != input_hash(current)
    return {"run": _run_dict(run), "stale": stale, "findings": findings, "counts": counts}


async def dismiss_finding(session: AsyncSession, finding: ConsistencyFinding, note: str | None) -> None:
    finding.dismissed = True
    finding.dismiss_note = note
    finding.dismissed_at = datetime.now(timezone.utc)
