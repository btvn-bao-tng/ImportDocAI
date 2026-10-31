import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { DocumentSummary, VerifyDocument, VerifyRow } from "../api/types";
import { DocumentSource } from "../components/DocumentViewer";
import {
  Button,
  ButtonLink,
  Callout,
  Chip,
  Container,
  EmptyState,
  LocalizedStatus,
  Section,
  Spinner,
  Stat,
  StatusBadge,
  Table,
  TableHeadCell,
} from "../components/ui";

interface EvidenceTarget {
  documentId: number;
  page: number | null;
  bbox: number[] | null;
  cellRef: string | null;
  label: string;
}

const VERIFY_TEMPLATE = "tokhai_import_v1";
const CARD = "rounded-lg border border-hairline bg-canvas";
const CELL = "py-2.5 pr-4 align-top";

function toSummary(doc: VerifyDocument): DocumentSummary {
  return {
    id: doc.id,
    filename: doc.filename,
    doc_type: doc.doc_type,
    doc_type_source: null,
    classification_confidence: null,
    status: doc.status,
    page_count: doc.page_count,
    has_text_layer: doc.has_text_layer,
    duplicate_of_id: null,
    version_note: null,
    error: doc.error,
    created_at: "",
  };
}

function reviewTone(row: VerifyRow): "green" | "red" | "gray" {
  if (row.review_status === "CONFIRMED") return "green";
  if (row.critical) return "red";
  return "gray";
}

function reviewLabel(row: VerifyRow): string {
  if (row.review_status === "CONFIRMED") return "Đã xác nhận";
  if (row.critical) return "Cần xem lại";
  return "Chưa xem";
}

function evidenceOf(doc: VerifyDocument, row: VerifyRow): EvidenceTarget {
  return { documentId: doc.id, page: row.page, bbox: row.bbox, cellRef: row.cell_ref, label: row.label_vi };
}

function VerifyResult({
  filename,
  filled,
  missing,
  exportId,
  warnings,
}: {
  filename: string;
  filled: number;
  missing: string[];
  exportId: number;
  warnings: string[];
}) {
  return (
    <div className={`${CARD} p-6`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="type-tagline text-ink">File Excel cuối cùng</h2>
          <LocalizedStatus tone="green">Sẵn sàng tải</LocalizedStatus>
        </div>
        <p className="type-caption truncate text-ink-48">{filename}</p>
      </div>
      <div className="mt-5 grid gap-4 sm:grid-cols-3">
        <Stat label="Trường đã điền" value={String(filled)} tone="success" />
        <Stat
          label="Trường còn trống"
          value={String(missing.length)}
          tone={missing.length > 0 ? "critical" : "default"}
        />
        <Stat label="Cảnh báo" value={String(warnings.length)} />
      </div>
      {missing.length > 0 ? (
        <div className="mt-4">
          <Callout tone="warning" title="Trường chưa có giá trị">
            <ul className="list-disc space-y-1 pl-5">
              {missing.slice(0, 12).map((key) => (
                <li key={key}>
                  <code>{key}</code>
                </li>
              ))}
              {missing.length > 12 ? <li>… và {missing.length - 12} trường khác</li> : null}
            </ul>
          </Callout>
        </div>
      ) : null}
      <div className="mt-6 flex flex-wrap items-center gap-3">
        <ButtonLink href={api.exportDownloadUrl(exportId)} variant="primary" download>
          Tải file Excel
        </ButtonLink>
        <ButtonLink href={api.exportReportUrl(exportId)} variant="secondary">
          Tải báo cáo JSON
        </ButtonLink>
      </div>
    </div>
  );
}

export default function VerifyPage() {
  const { id } = useParams<{ id: string }>();
  const shipmentId = Number(id ?? "");
  const validId = Number.isFinite(shipmentId) && shipmentId > 0;
  const queryClient = useQueryClient();
  const [activeDocId, setActiveDocId] = useState<number | null>(null);
  const [evidence, setEvidence] = useState<EvidenceTarget | null>(null);
  const generatedRef = useRef(false);

  const previewQuery = useQuery({
    queryKey: ["verify-preview", shipmentId],
    queryFn: () => api.verifyPreview(shipmentId),
    enabled: validId,
  });

  const documents = previewQuery.data?.documents ?? [];
  const activeDoc = documents.find((doc) => doc.id === activeDocId) ?? documents[0] ?? null;

  const generate = useMutation({
    mutationFn: () => api.createExport(shipmentId, VERIFY_TEMPLATE),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["exports", shipmentId] });
    },
  });

  // The verify step ends in a final xlsx: build it once when the page opens.
  useEffect(() => {
    if (generatedRef.current) return;
    if (!previewQuery.data || previewQuery.data.rows.length === 0) return;
    generatedRef.current = true;
    generate.mutate();
  }, [previewQuery.data, generate]);

  if (!validId) {
    return (
      <Section tone="light">
        <Container width="content">
          <Callout tone="critical" title="Mã lô hàng không hợp lệ">
            Không xác định được lô hàng từ đường dẫn.
          </Callout>
        </Container>
      </Section>
    );
  }

  if (previewQuery.isPending) {
    return (
      <Section tone="light">
        <Container width="wide">
          <div className="flex items-center justify-center py-24">
            <Spinner />
          </div>
        </Container>
      </Section>
    );
  }

  if (previewQuery.isError) {
    return (
      <Section tone="light">
        <Container width="wide">
          <Callout tone="critical" title="Không tải được dữ liệu đối chiếu">
            {previewQuery.error.message}
          </Callout>
        </Container>
      </Section>
    );
  }

  const preview = previewQuery.data;
  if (documents.length === 0) {
    return (
      <Section tone="light">
        <Container width="wide">
          <EmptyState
            title="Chưa có chứng từ để đối chiếu"
            description="Hãy tải lên và xử lý chứng từ trước khi kiểm tra file xuất."
            action={
              <ButtonLink to={`/shipments/${shipmentId}/upload`} variant="primary">
                Tải lên chứng từ
              </ButtonLink>
            }
          />
        </Container>
      </Section>
    );
  }

  const exportResult = generate.data ?? null;
  const incomplete = preview.critical_open > 0;

  return (
    <Section tone="light">
      <Container width="wide">
        <header className="flex flex-col gap-4 pb-8 sm:flex-row sm:items-end sm:justify-between">
          <div className="flex min-w-0 flex-col gap-2">
            <p className="type-caption uppercase tracking-[0.06em] text-ink-48">
              <Link to="/" className="text-action active:opacity-70">
                Lô hàng
              </Link>
              <span className="mx-2">/</span>
              <Link to={`/shipments/${shipmentId}/review`} className="text-action active:opacity-70">
                Lô #{shipmentId}
              </Link>
              <span className="mx-2">/</span>
              Đối chiếu
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="type-display-md text-ink">{preview.shipment.code}</h1>
              <StatusBadge status={preview.shipment.status} />
            </div>
            <p className="type-body max-w-[720px] text-ink-48">
              Đối chiếu từng giá trị trích xuất với chứng từ gốc ở cột trái, rồi tải file Excel cuối cùng ở cột
              phải.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            {incomplete ? (
              <LocalizedStatus tone="red">{preview.critical_open} trường cần xem lại</LocalizedStatus>
            ) : (
              <LocalizedStatus tone="green">Đủ dữ liệu</LocalizedStatus>
            )}
            <ButtonLink to={`/shipments/${shipmentId}/review`} variant="secondary">
              Sửa dữ liệu
            </ButtonLink>
          </div>
        </header>

        <div className="mb-6 flex flex-col gap-4">
          {generate.isPending ? (
            <div className={`${CARD} flex items-center gap-3 px-6 py-5`}>
              <Spinner label="Đang tạo file Excel từ dữ liệu trích xuất…" />
            </div>
          ) : null}
          {generate.isError ? (
            <Callout tone="critical" title="Không tạo được file Excel">
              {generate.error.message}
              <div className="mt-3">
                <Button variant="secondary" size="sm" onClick={() => generate.mutate()}>
                  Thử lại
                </Button>
              </div>
            </Callout>
          ) : null}
          {exportResult ? (
            <VerifyResult
              filename={exportResult.filename}
              filled={exportResult.filled}
              missing={exportResult.missing}
              warnings={exportResult.warnings}
              exportId={exportResult.id}
            />
          ) : null}
        </div>

        <div className="grid gap-5 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
          <section
            aria-label="Chứng từ gốc"
            className={`${CARD} flex min-h-[420px] flex-col overflow-hidden lg:h-[calc(100dvh-6rem)] lg:sticky lg:top-4`}
          >
            <div className="flex shrink-0 items-center justify-between gap-2 border-b border-divider px-4 py-3">
              <h2 className="type-caption-strong text-ink">Chứng từ gốc</h2>
              <p className="type-fine-print truncate text-ink-48">
                {activeDoc ? activeDoc.filename : "Chưa chọn chứng từ"}
              </p>
            </div>
            <div
              role="group"
              aria-label="Chọn chứng từ để đối chiếu"
              className="scrollbar-none flex shrink-0 items-center gap-1.5 overflow-x-auto border-b border-divider px-3 py-2"
            >
              {documents.map((doc) => (
                <Chip
                  key={doc.id}
                  active={doc.id === activeDoc?.id}
                  className="!inline-flex max-w-[200px] justify-start"
                  title={doc.filename}
                  onClick={() => setActiveDocId(doc.id)}
                >
                  <span className="block truncate">{doc.filename}</span>
                </Chip>
              ))}
            </div>
            <div className="min-h-0 flex-1 overflow-auto bg-parchment p-3">
              {evidence && evidence.documentId === activeDoc?.id ? (
                <p className="type-fine-print mb-2 truncate rounded-sm border border-[#c8dbf5] bg-[#eaf2fd] px-2 py-1 text-action">
                  Đang đánh dấu: {evidence.label}
                  {evidence.cellRef ? ` · ${evidence.cellRef}` : evidence.page ? ` · trang ${evidence.page}` : null}
                </p>
              ) : null}
              {activeDoc ? (
                <div className="border border-hairline bg-canvas">
                  <DocumentSource doc={toSummary(activeDoc)} evidence={evidence} />
                </div>
              ) : null}
            </div>
          </section>

          <section aria-label="Giá trị trích xuất" className={`${CARD} overflow-hidden`}>
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-divider px-4 py-3">
              <h2 className="type-caption-strong text-ink">
                {activeDoc ? `Giá trị trích xuất · ${activeDoc.filename}` : "Giá trị trích xuất"}
              </h2>
              <p className="type-fine-print text-ink-48">
                {activeDoc?.fields.length ?? 0} trường · bấm để xem nguồn
              </p>
            </div>
            {activeDoc && activeDoc.error ? (
              <div className="px-4 py-3">
                <Callout tone="critical">{activeDoc.error}</Callout>
              </div>
            ) : null}
            {!activeDoc || activeDoc.fields.length === 0 ? (
              <div className="px-4 py-4">
                <EmptyState
                  title="Chưa có dữ liệu trích xuất"
                  description="Chứng từ này chưa được trích xuất thành công."
                />
              </div>
            ) : (
              <div className="px-4 pb-2">
                <Table
                  minWidth={620}
                  head={
                    <tr>
                      <TableHeadCell>Trường</TableHeadCell>
                      <TableHeadCell>Giá trị</TableHeadCell>
                      <TableHeadCell>Vị trí</TableHeadCell>
                      <TableHeadCell>Trạng thái</TableHeadCell>
                    </tr>
                  }
                >
                  {activeDoc.fields.map((row, index) => {
                    const active = evidence?.documentId === activeDoc.id && evidence.label === row.label_vi;
                    return (
                      <tr
                        key={`${row.field_key}-${index}`}
                        className={`cursor-pointer border-t border-divider transition-colors hover:bg-pearl ${
                          active ? "bg-[#eaf2fd]" : ""
                        }`}
                        onClick={() => {
                          setActiveDocId(activeDoc.id);
                          setEvidence(evidenceOf(activeDoc, row));
                        }}
                      >
                        <td className={CELL}>
                          <span className="type-caption-strong text-ink">{row.label_vi}</span>
                          <code className="type-fine-print mt-0.5 block text-ink-48">{row.field_key}</code>
                        </td>
                        <td className={`${CELL} type-body tabular-nums text-ink-80`}>
                          {row.value?.trim() ? row.value : <span className="text-warning">—</span>}
                          {row.unit ? <span className="type-caption ml-1 text-ink-48">{row.unit}</span> : null}
                        </td>
                        <td className={`${CELL} type-caption whitespace-nowrap text-ink-48`}>
                          {row.cell_ref ?? (row.page ? `Trang ${row.page}` : "—")}
                        </td>
                        <td className={CELL}>
                          <LocalizedStatus tone={reviewTone(row)}>{reviewLabel(row)}</LocalizedStatus>
                        </td>
                      </tr>
                    );
                  })}
                </Table>
              </div>
            )}
          </section>
        </div>
      </Container>
    </Section>
  );
}
