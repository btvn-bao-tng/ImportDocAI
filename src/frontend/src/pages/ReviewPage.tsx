import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getDocument, GlobalWorkerOptions, type PDFDocumentProxy } from "pdfjs-dist";
import workerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { api } from "../api/client";
import type { DocumentExtraction, DocumentSummary, FieldRow, LineItemRow, PartyRow } from "../api/types";
import {
  Button,
  Callout,
  Chip,
  Container,
  EmptyState,
  LocalizedStatus,
  ProvenanceBadge,
  ReviewBadge,
  Section,
  Spinner,
  StatusBadge,
  Table,
  TableHeadCell,
} from "../components/ui";

GlobalWorkerOptions.workerSrc = workerSrc;

const roleLabels: Record<string, string> = {
  SELLER: "Bên bán",
  BUYER: "Bên mua",
  SHIPPER: "Người gửi hàng",
  CONSIGNEE: "Người nhận hàng",
  NOTIFY: "Bên thông báo",
  ISSUER: "Bên phát hành",
  INSPECTOR: "Đơn vị kiểm định",
};

function roleLabel(role: string): string {
  return roleLabels[role] ?? role;
}

type DocKind = "pdf" | "excel" | "image" | "other";

const excelExt = /\.(xlsx|xlsm|xls)$/i;
const imageExt = /\.(jpe?g|png)$/i;
const pdfExt = /\.pdf$/i;

function docKind(doc: DocumentSummary): DocKind {
  const name = doc.filename;
  if (pdfExt.test(name)) return "pdf";
  if (excelExt.test(name)) return "excel";
  if (imageExt.test(name)) return "image";
  if (!doc.has_text_layer && doc.page_count <= 1) return "image";
  return "other";
}

function fieldValue(field: FieldRow): string {
  return field.normalized_value ?? field.raw_value ?? "";
}

function sourceText(field: FieldRow): string {
  if (field.provenance_type === "CELL") {
    return field.cell_ref ? `Ô ${field.cell_ref}` : "ô bảng tính";
  }
  if (field.provenance_type === "IMAGE") return "ảnh quét";
  if (field.page !== null) return `Trang ${field.page}`;
  return "không rõ nguồn";
}

function isNeedsAttention(field: FieldRow): boolean {
  return field.review_status === "UNREVIEWED" || field.review_status === "NEEDS_REVIEW";
}

type FieldGroupKey = "document." | "transport." | "totals.";

const MATCHERS: { key: FieldGroupKey; title: string }[] = [
  { key: "document.", title: "Thông tin chứng từ" },
  { key: "transport.", title: "Thông tin lô hàng" },
  { key: "totals.", title: "Tổng trị giá" },
];

const UTILITY_CARD = "rounded-lg border border-hairline bg-canvas";
const CONTROL =
  "rounded-sm border border-hairline bg-canvas text-ink transition-colors focus:border-action-focus focus:ring-2 focus:ring-action-focus/30 focus:outline-none";
const MONO = '[font-family:ui-monospace,"SF Mono",SFMono-Regular,Menlo,monospace]';

function useSyncedValue(key: string, source: () => string): [string, (next: string) => void] {
  const [draft, setDraft] = useState<{ key: string; value: string }>({ key, value: source() });
  const current = draft.key === key ? draft : { key, value: source() };
  return [current.value, (next: string) => setDraft({ key, value: next })];
}

function fieldSyncKey(field: FieldRow | undefined): string {
  return `${field?.id ?? "none"}:${field?.normalized_value ?? ""}:${field?.raw_value ?? ""}`;
}

interface PdfViewerProps {
  docId: number;
  selectedField: FieldRow | null;
}

function PdfViewer({ docId, selectedField }: PdfViewerProps) {
  const url = useMemo(() => api.documentFileUrl(docId), [docId]);
  const pdfQuery = useQuery({
    queryKey: ["pdf", "document", docId],
    queryFn: async () => {
      const task = getDocument({ url });
      const loaded = await task.promise;
      return { pdf: loaded, task };
    },
    gcTime: 0,
  });
  const pageRefs = useRef<Record<number, HTMLDivElement | null>>({});

  const pdf = pdfQuery.data?.pdf ?? null;
  const resolvedError = pdfQuery.isError ? pdfQuery.error.message : null;

  useEffect(() => {
    const task = pdfQuery.data?.task;
    if (!task) return;
    return () => {
      void task.destroy();
    };
  }, [pdfQuery.data]);

  const highlightPage = selectedField?.page ?? null;
  const highlightBbox =
    selectedField?.bbox && selectedField.bbox.length === 4 ? selectedField.bbox : null;

  useEffect(() => {
    if (highlightPage === null) return;
    const node = pageRefs.current[highlightPage];
    if (node) node.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [highlightPage, docId]);

  if (resolvedError) {
    return (
      <div className="px-4 py-4">
        <Callout tone="critical" title="Không hiển thị được PDF">
          {resolvedError}
        </Callout>
      </div>
    );
  }

  if (!pdf) {
    return (
      <div className="flex justify-center px-4 py-16">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6 px-4 py-5">
      {Array.from({ length: pdf.numPages }, (_, index) => {
        const pageNumber = index + 1;
        const showBox = highlightBbox !== null && highlightPage === pageNumber;
        return (
          <div
            key={pageNumber}
            ref={(node) => {
              pageRefs.current[pageNumber] = node;
            }}
            className="flex flex-col items-center"
          >
            <PdfPage pdf={pdf} pageNumber={pageNumber} highlight={showBox ? highlightBbox : null} />
            <span className="type-fine-print mt-2 text-ink-48">
              Trang {pageNumber} / {pdf.numPages}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function PdfPage({
  pdf,
  pageNumber,
  highlight,
}: {
  pdf: PDFDocumentProxy;
  pageNumber: number;
  highlight: number[] | null;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [rendered, setRendered] = useState<{ pdf: PDFDocumentProxy; page: number } | null>(null);
  const [size, setSize] = useState<{ page: number; width: number; height: number } | null>(null);
  const [renderError, setRenderError] = useState<{ page: number; message: string } | null>(null);
  const error = renderError?.page === pageNumber ? renderError.message : null;
  const isRendered = rendered?.pdf === pdf && rendered.page === pageNumber;
  const viewSize = size?.page === pageNumber ? size : null;

  useEffect(() => {
    let cancelled = false;
    let renderTask: { cancel: () => void } | null = null;

    pdf
      .getPage(pageNumber)
      .then((page) => {
        if (cancelled) return;
        const base = page.getViewport({ scale: 1 });
        const maxWidth = 760;
        const scale = Math.min(1.6, Math.max(0.5, maxWidth / base.width));
        const viewport = page.getViewport({ scale });
        const canvas = canvasRef.current;
        if (!canvas) return;
        const ratio = window.devicePixelRatio || 1;
        canvas.width = Math.floor(viewport.width * ratio);
        canvas.height = Math.floor(viewport.height * ratio);
        canvas.style.width = `${Math.floor(viewport.width)}px`;
        canvas.style.height = `${Math.floor(viewport.height)}px`;
        setSize({ page: pageNumber, width: viewport.width, height: viewport.height });
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        const task = page.render({
          canvas,
          canvasContext: ctx,
          viewport,
          transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : undefined,
        });
        renderTask = task;
        task.promise.then(
          () => {
            if (!cancelled) setRendered({ pdf, page: pageNumber });
          },
          (err: unknown) => {
            if (!cancelled && (err as { name?: string } | null)?.name !== "RenderingCancelledException") {
              setRenderError({ page: pageNumber, message: err instanceof Error ? err.message : "Không hiển thị được trang" });
            }
          },
        );
      })
      .catch((err: unknown) => {
        if (!cancelled) setRenderError({ page: pageNumber, message: err instanceof Error ? err.message : "Không tải được trang" });
      });

    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [pdf, pageNumber]);

  const box =
    highlight && viewSize
      ? {
          left: highlight[0] * viewSize.width,
          top: highlight[1] * viewSize.height,
          width: Math.max(2, (highlight[2] - highlight[0]) * viewSize.width),
          height: Math.max(2, (highlight[3] - highlight[1]) * viewSize.height),
        }
      : null;

  return (
    <div
      className="relative inline-block max-w-full border border-hairline bg-canvas"
      style={{ width: viewSize ? `${Math.floor(viewSize.width)}px` : undefined, minHeight: viewSize ? undefined : 480 }}
    >
      <canvas ref={canvasRef} className="block max-w-full" aria-label={`Trang ${pageNumber}`} />
      {!isRendered && !error ? (
        <div className="absolute inset-0 flex items-center justify-center bg-pearl/70">
          <Spinner />
        </div>
      ) : null}
      {error ? (
        <div className="absolute inset-0 flex items-center justify-center px-6 text-center text-caption text-critical">
          {error}
        </div>
      ) : null}
      {box ? (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute border border-action bg-action/15"
          style={{
            left: `${box.left}px`,
            top: `${box.top}px`,
            width: `${box.width}px`,
            height: `${box.height}px`,
          }}
        />
      ) : null}
    </div>
  );
}

const columnToIndex = (letters: string): number =>
  letters
    .toUpperCase()
    .split("")
    .reduce((acc, ch) => acc * 26 + (ch.charCodeAt(0) - 64), 0) - 1;

function parseCellRef(cellRef: string | null): { sheet: string; row: number; col: number } | null {
  if (!cellRef) return null;
  const bang = cellRef.indexOf("!");
  if (bang < 0) return null;
  const sheet = cellRef.slice(0, bang).trim();
  const coord = cellRef.slice(bang + 1).trim();
  const match = /^([A-Za-z]+)(\d+)$/.exec(coord);
  if (!match) return null;
  return { sheet, row: Number(match[2]) - 1, col: columnToIndex(match[1]) };
}

function cellLabel(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function ExcelViewer({ docId, selectedField }: { docId: number; selectedField: FieldRow | null }) {
  const gridQuery = useQuery({
    queryKey: ["grid", docId],
    queryFn: () => api.documentGrid(docId),
  });
  const [pickedSheet, setPickedSheet] = useState<string | null>(null);
  const selectedCellRef = selectedField?.cell_ref ?? null;
  const target = useMemo(() => parseCellRef(selectedCellRef), [selectedCellRef]);
  const cellRef = useRef<HTMLTableCellElement | null>(null);

  if (gridQuery.isPending) {
    return (
      <div className="flex justify-center px-4 py-16">
        <Spinner />
      </div>
    );
  }

  if (gridQuery.isError) {
    return (
      <div className="px-4 py-4">
        <Callout tone="critical" title="Không đọc được bảng tính">
          {gridQuery.error.message}
        </Callout>
      </div>
    );
  }

  const sheets = gridQuery.data?.sheets ?? [];
  if (sheets.length === 0) {
    return (
      <div className="px-4 py-4">
        <EmptyState title="Bảng tính trống" description="Không có sheet nào để hiển thị." />
      </div>
    );
  }

  const current = sheets.find((s) => s.name === (target?.sheet ?? pickedSheet)) ?? sheets[0];
  const rows = current.rows ?? [];
  const columnCount = rows.reduce((max, row) => Math.max(max, row.length), 0);

  return (
    <div className="flex flex-col gap-3 px-4 py-4">
      {sheets.length > 1 ? (
        <div className="flex flex-wrap gap-2" role="tablist" aria-label="Chọn sheet">
          {sheets.map((sheet) => {
            const active = sheet.name === current.name;
            return (
              <button
                key={sheet.name}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setPickedSheet(sheet.name)}
                className={`type-caption inline-flex min-h-11 items-center rounded-full border px-4 py-2 transition-colors active:scale-95 ${
                  active
                    ? "border-action bg-canvas text-action"
                    : "border-hairline bg-canvas text-ink-48"
                }`}
              >
                {sheet.name}
              </button>
            );
          })}
        </div>
      ) : null}

      {rows.length === 0 || columnCount === 0 ? (
        <EmptyState title="Sheet không có dữ liệu" description={`Sheet “${current.name}” chưa có ô nào.`} />
      ) : (
        <div className="max-h-[70vh] overflow-auto border border-hairline bg-canvas">
          <table className={`border-collapse text-caption tabular-nums ${MONO}`}>
            <thead>
              <tr>
                <th className="sticky top-0 left-0 z-10 border-r border-b border-hairline bg-pearl px-2 py-1" />
                {Array.from({ length: columnCount }, (_, col) => (
                  <th
                    key={col}
                    scope="col"
                    className="sticky top-0 z-[5] border-r border-b border-hairline bg-pearl px-2 py-1 text-fine-print font-normal text-ink-48"
                  >
                    {String.fromCharCode(65 + (col % 26)).padStart(Math.floor(col / 26) + 1, String.fromCharCode(65 + (col % 26)))}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  <th
                    scope="row"
                    className="sticky left-0 z-[1] border-r border-b border-hairline bg-pearl px-2 py-1 text-fine-print font-normal text-ink-48"
                  >
                    {rowIndex + 1}
                  </th>
                  {Array.from({ length: columnCount }, (_, col) => {
                    const isTarget =
                      target !== null &&
                      target.sheet === current.name &&
                      target.row === rowIndex &&
                      target.col === col;
                    return (
                      <td
                        key={col}
                        ref={isTarget ? cellRef : undefined}
                        className={`max-w-[280px] truncate border-r border-b border-divider px-2 py-1 text-ink-80 ${
                          isTarget ? "bg-action/15 ring-1 ring-action ring-inset" : ""
                        }`}
                        title={cellLabel(row[col])}
                      >
                        {cellLabel(row[col])}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {selectedField?.cell_ref ? (
        <p className="type-fine-print text-ink-48">Đang chỉ ô {selectedField.cell_ref}</p>
      ) : null}
    </div>
  );
}

function ImageViewer({ docId }: { docId: number }) {
  return (
    <div className="flex justify-center px-4 py-5">
      <img
        src={api.documentFileUrl(docId)}
        alt="Chứng từ dạng ảnh"
        className="max-h-[75vh] max-w-full border border-hairline bg-canvas object-contain"
      />
    </div>
  );
}

function DocumentSource({ doc, selectedField }: { doc: DocumentSummary; selectedField: FieldRow | null }) {
  const kind = docKind(doc);
  const noticeGlyph = selectedField?.provenance_type === "IMAGE";

  return (
    <div className="flex flex-col gap-3">
      {noticeGlyph ? (
        <div className="px-4 pt-4">
          <Callout tone="info" title="Ghi chú nguồn">
            Vị trí nguồn chỉ xác định ở mức trang
          </Callout>
        </div>
      ) : null}
      {kind === "pdf" ? (
        <PdfViewer docId={doc.id} selectedField={selectedField} />
      ) : kind === "excel" ? (
        <ExcelViewer docId={doc.id} selectedField={selectedField} />
      ) : kind === "image" ? (
        <ImageViewer docId={doc.id} />
      ) : (
        <div className="mx-4 my-4 flex flex-col items-center gap-3 border border-hairline bg-canvas px-6 py-10 text-center">
          <p className="type-body text-ink-80">
            Không có bản xem trước dạng nhúng cho tệp này.
          </p>
          <a href={api.documentFileUrl(doc.id)} target="_blank" rel="noreferrer" className="type-body text-action underline">
            Mở tệp gốc
          </a>
        </div>
      )}
    </div>
  );
}

interface FieldRowProps {
  shipmentId: number;
  field: FieldRow;
  selected: boolean;
  onSelect: (field: FieldRow) => void;
}

const IMPORTANT_DOT = (
  <span className="inline-flex items-center gap-1 text-fine-print font-normal text-ink-48">
    <span className="h-1 w-1 rounded-full bg-action" aria-hidden="true" />
    quan trọng
  </span>
);

function ScalarFieldRow({ shipmentId, field, selected, onSelect }: FieldRowProps) {
  const queryClient = useQueryClient();
  const [value, setValue] = useSyncedValue(
    `${shipmentId}:${fieldSyncKey(field)}`,
    () => fieldValue(field),
  );
  const [error, setError] = useState<string | null>(null);

  const saveMutation = useMutation({
    mutationFn: () => api.patchField(shipmentId, field.id, value),
    onSuccess: async () => {
      setError(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["extractions", shipmentId] }),
        queryClient.invalidateQueries({ queryKey: ["shipment", shipmentId] }),
      ]);
    },
    onError: (err: Error) => setError(err.message),
  });

  const confirmMutation = useMutation({
    mutationFn: () => api.confirmField(shipmentId, field.id),
    onSuccess: async () => {
      setError(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["extractions", shipmentId] }),
        queryClient.invalidateQueries({ queryKey: ["shipment", shipmentId] }),
      ]);
    },
    onError: (err: Error) => setError(err.message),
  });

  const dirty = value !== fieldValue(field);
  const busy = saveMutation.isPending || confirmMutation.isPending;

  return (
    <div
      onClick={() => onSelect(field)}
      className={`cursor-pointer border-b border-divider px-6 py-4 last:border-b-0 ${
        selected ? "border-l-[3px] border-l-action pl-[21px]" : ""
      }`}
    >
      <div className="min-w-0">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <label htmlFor={`field-${field.id}`} className="type-body-strong min-w-0 text-ink">
            {field.label_vi}
          </label>
          {field.critical ? IMPORTANT_DOT : null}
          <span className="flex items-center gap-1.5">
            <ProvenanceBadge type={field.provenance_type} />
            <ReviewBadge status={field.review_status} critical={field.critical} />
          </span>
        </div>

        <div className="mt-2 flex min-w-0 flex-wrap items-center gap-2" onClick={(e) => e.stopPropagation()}>
          <input
            id={`field-${field.id}`}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onFocus={() => onSelect(field)}
            aria-label={`Giá trị ${field.label_vi}`}
            className={`type-body min-h-11 min-w-0 flex-1 px-3 py-2 ${CONTROL} ${
              isNeedsAttention(field) && field.critical ? "border-critical/40" : ""
            }`}
          />
          {field.unit ? <span className="type-caption shrink-0 text-ink-48">{field.unit}</span> : null}
          <div className="flex shrink-0 items-center gap-2" onClick={(e) => e.stopPropagation()}>
            <Button size="sm" variant="ghost" disabled={!dirty || busy} onClick={() => saveMutation.mutate()}>
              {saveMutation.isPending ? "Đang lưu…" : "Lưu"}
            </Button>
            <Button size="sm" variant="primary" disabled={busy} onClick={() => confirmMutation.mutate()}>
              {confirmMutation.isPending ? "Đang xác nhận…" : "Xác nhận"}
            </Button>
          </div>
        </div>

        <div className="mt-2 flex flex-wrap items-baseline gap-x-2 border-t border-divider pt-2">
          <p className="type-fine-print text-ink-48">
            Nguồn: {sourceText(field)}
            {field.confidence !== null ? ` · độ tin cậy ${Math.round(field.confidence * 100)}%` : ""}
            {field.grounding_verified ? "" : " · chưa đối chiếu"}
          </p>
          {error ? <p className="type-fine-print text-critical">{error}</p> : null}
        </div>
      </div>
    </div>
  );
}

const LINE_COLUMNS: { key: string; title: string; fallback: (li: LineItemRow) => string | null; numeric: boolean }[] = [
  { key: "line.description", title: "Mô tả", fallback: (li) => li.description ?? li.raw_description, numeric: false },
  { key: "line.spec", title: "Quy cách", fallback: (li) => li.spec, numeric: false },
  { key: "line.quantity", title: "Số lượng", fallback: (li) => li.quantity, numeric: true },
  { key: "line.unit_price", title: "Đơn giá", fallback: (li) => li.unit_price, numeric: true },
  { key: "line.amount", title: "Thành tiền", fallback: (li) => li.amount, numeric: true },
];

function LineCell({
  shipmentId,
  field,
  fallback,
  numeric,
  selected,
  onSelect,
}: {
  shipmentId: number;
  field: FieldRow | undefined;
  fallback: string | null;
  numeric: boolean;
  selected: boolean;
  onSelect: (field: FieldRow) => void;
}) {
  const queryClient = useQueryClient();
  const [value, setValue] = useSyncedValue(
    `${shipmentId}:${fieldSyncKey(field)}`,
    () => (field ? fieldValue(field) : ""),
  );
  const [error, setError] = useState<string | null>(null);

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!field) throw new Error("Không có trường để lưu");
      return api.patchField(shipmentId, field.id, value);
    },
    onSuccess: async () => {
      setError(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["extractions", shipmentId] }),
        queryClient.invalidateQueries({ queryKey: ["shipment", shipmentId] }),
      ]);
    },
    onError: (err: Error) => setError(err.message),
  });

  if (!field) {
    return (
      <td className={`type-body px-3 py-2 align-top text-ink-48 ${numeric ? "text-right tabular-nums" : ""}`}>
        {fallback ?? "—"}
      </td>
    );
  }

  const dirty = value !== fieldValue(field);

  return (
    <td className={`px-3 py-2 align-top ${selected ? "border-l-[3px] border-l-action pl-[9px]" : ""}`}>
      <div className="flex flex-col gap-1">
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onFocus={() => onSelect(field)}
          onClick={(e) => e.stopPropagation()}
          aria-label={`${field.label_vi} dòng ${field.line_item_id ?? ""}`}
          className={`type-body min-h-11 w-full min-w-[72px] px-2 py-1 text-ink ${CONTROL} ${
            isNeedsAttention(field) && field.critical ? "border-critical/40" : ""
          } ${numeric ? "text-right tabular-nums" : ""}`}
        />
        <div className="flex items-center justify-between gap-1 px-0.5" onClick={(e) => e.stopPropagation()}>
          <span className="truncate type-fine-print text-ink-48">{sourceText(field)}</span>
          <button
            type="button"
            disabled={!dirty || saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
            className="type-fine-print text-action underline decoration-1 underline-offset-2 disabled:text-ink-48 disabled:no-underline"
          >
            Lưu
          </button>
        </div>
        {error ? <span className="px-0.5 type-fine-print text-critical">{error}</span> : null}
      </div>
    </td>
  );
}

function LineItemsTable({
  shipmentId,
  lineItems,
  selectedFieldId,
  onSelect,
}: {
  shipmentId: number;
  lineItems: LineItemRow[];
  selectedFieldId: number | null;
  onSelect: (field: FieldRow) => void;
}) {
  if (lineItems.length === 0) {
    return (
      <div className="px-6 py-4">
        <EmptyState title="Chưa có dòng hàng" description="Chứng từ chưa trích xuất được dòng hàng nào." />
      </div>
    );
  }

  return (
    <div className="px-6 py-1">
      <Table
        minWidth={600}
        head={
          <>
            <TableHeadCell>STT</TableHeadCell>
            {LINE_COLUMNS.map((col) => (
              <TableHeadCell key={col.key} align={col.numeric ? "right" : "left"}>
                {col.title}
              </TableHeadCell>
            ))}
          </>
        }
      >
        {lineItems.map((li) => (
          <tr key={li.id} className="border-b border-divider last:border-b-0">
            <td className="type-body px-3 py-2 align-top tabular-nums text-ink-48">{li.seq}</td>
            {LINE_COLUMNS.map((col) => (
              <LineCell
                key={col.key}
                shipmentId={shipmentId}
                field={li.fields.find((f) => f.field_key === col.key)}
                fallback={col.fallback(li)}
                numeric={col.numeric}
                selected={selectedFieldId !== null && li.fields.some((f) => f.field_key === col.key && f.id === selectedFieldId)}
                onSelect={onSelect}
              />
            ))}
          </tr>
        ))}
      </Table>
    </div>
  );
}

function PartiesSection({ parties }: { parties: PartyRow[] }) {
  if (parties.length === 0) {
    return <p className="type-caption px-6 py-4 text-ink-48">Chưa trích xuất được bên tham gia nào.</p>;
  }

  return (
    <div className="flex flex-col gap-3 px-6 py-4">
      <Callout tone="info" title="Không tự động gộp công ty">
        Hệ thống liệt kê các bên theo từng chứng từ. Các tên có thể trùng cùng một công ty nhưng không được tự động gộp — hãy kiểm tra thủ công trước khi xuất.
      </Callout>
      <ul className="divide-y divide-divider">
        {parties.map((party) => (
          <li key={party.id} className="py-3">
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="type-body text-ink">{party.canonical_name || party.raw_name}</span>
              <span className="type-fine-print text-ink-48">({party.raw_name})</span>
            </div>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {party.roles.map((role) => (
                <span
                  key={role}
                  className="type-fine-print inline-flex items-center rounded-md bg-pearl px-2 py-1 text-ink-48"
                >
                  {roleLabel(role)}
                </span>
              ))}
            </div>
            {party.documents.length > 0 ? (
              <ul className="mt-1.5 flex flex-col gap-0.5">
                {party.documents.map((d, index) => (
                  <li key={`${d.document_id}-${d.role}-${index}`} className="type-fine-print text-ink-48">
                    {roleLabel(d.role)} · {d.filename ?? `Chứng từ #${d.document_id}`}
                  </li>
                ))}
              </ul>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function DataCard({
  title,
  caption,
  children,
}: {
  title: string;
  caption?: string;
  children: ReactNode;
}) {
  return (
    <section className={`${UTILITY_CARD} overflow-hidden`}>
      <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-b border-divider px-6 py-4">
        <h2 className="type-tagline text-ink">{title}</h2>
        {caption ? <p className="type-caption text-ink-48">{caption}</p> : null}
      </header>
      {children}
    </section>
  );
}

export default function ReviewPage() {
  const params = useParams<{ id: string }>();
  const navigate = useNavigate();
  const shipmentId = Number(params.id);
  const validId = Number.isFinite(shipmentId) && shipmentId > 0;

  const [activeDocId, setActiveDocId] = useState<number | null>(null);
  const [selectedFieldId, setSelectedFieldId] = useState<number | null>(null);

  const extractionsQuery = useQuery({
    queryKey: ["extractions", shipmentId],
    queryFn: () => api.getExtractions(shipmentId),
    enabled: validId,
  });
  const partiesQuery = useQuery({
    queryKey: ["parties", shipmentId],
    queryFn: () => api.getParties(shipmentId),
    enabled: validId,
  });
  const shipmentQuery = useQuery({
    queryKey: ["shipment", shipmentId],
    queryFn: () => api.getShipment(shipmentId),
    enabled: validId,
  });

  const documents = useMemo<DocumentExtraction[]>(
    () => extractionsQuery.data?.documents ?? [],
    [extractionsQuery.data],
  );
  const activeDocChanged = activeDocId !== null && !documents.some((d) => d.document.id === activeDocId);
  if (activeDocChanged) setActiveDocId(null);
  const activeDoc = activeDocChanged ? documents[0] ?? null : documents.find((d) => d.document.id === activeDocId) ?? documents[0] ?? null;

  const allFields = useMemo<FieldRow[]>(
    () => documents.flatMap((d) => [...d.fields, ...d.line_items.flatMap((li) => li.fields)]),
    [documents],
  );

  const selectedField = allFields.find((f) => f.id === selectedFieldId) ?? null;

  function selectField(field: FieldRow) {
    setSelectedFieldId(field.id);
    if (field.document_id !== activeDoc?.document.id) setActiveDocId(field.document_id);
  }

  const groupedFields = useMemo(() => {
    const source = activeDoc?.fields ?? [];
    return MATCHERS.map(({ key, title }) => ({
      key,
      title,
      fields: source.filter((f) => f.field_key.startsWith(key)),
    }));
  }, [activeDoc]);

  const criticalAttention = useMemo(
    () =>
      allFields.filter(
        (f) =>
          f.critical &&
          (f.review_status === "UNREVIEWED" || f.review_status === "NEEDS_REVIEW"),
      ).length,
    [allFields],
  );

  if (!validId) {
    return (
      <Section tone="light">
        <Container width="wide">
          <Callout tone="critical" title="Mã lô hàng không hợp lệ">
            Đường dẫn không chứa mã lô hàng hợp lệ.
          </Callout>
        </Container>
      </Section>
    );
  }

  if (extractionsQuery.isPending) {
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

  if (extractionsQuery.isError) {
    return (
      <Section tone="light">
        <Container width="wide">
          <Callout tone="critical" title="Không tải được dữ liệu trích xuất">
            {extractionsQuery.error.message}
          </Callout>
        </Container>
      </Section>
    );
  }

  if (documents.length === 0) {
    return (
      <Section tone="light">
        <Container width="wide">
          <EmptyState
            title="Chưa có chứng từ để xem lại"
            description="Hãy tải lên và xử lý chứng từ trước khi xem lại dữ liệu."
            action={
              <Button variant="primary" onClick={() => navigate(`/shipments/${shipmentId}/upload`)}>
                Tải lên chứng từ
              </Button>
            }
          />
        </Container>
      </Section>
    );
  }

  const shipment = shipmentQuery.data;
  const lineItemCount = activeDoc?.line_items.length ?? 0;
  const lineFieldCount = activeDoc?.line_items.reduce((sum, li) => sum + li.fields.length, 0) ?? 0;
  const lineAttention = activeDoc?.line_items.reduce(
    (sum, li) => sum + li.fields.filter((f) => f.critical && isNeedsAttention(f)).length,
    0,
  ) ?? 0;

  return (
    <Section tone="light">
      <Container width="wide">
        <div className="flex flex-col gap-6">
          <header className="sticky top-0 z-20 -mx-4 border-b border-hairline bg-parchment/80 px-4 backdrop-blur-xl sm:-mx-6 sm:px-6">
            <div className="flex min-h-[64px] flex-col gap-2 py-3 lg:flex-row lg:flex-wrap lg:items-center lg:justify-between lg:gap-x-6">
              <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                <h1 className="type-tagline text-ink">{shipment?.code ?? `#${shipmentId}`}</h1>
                {shipment ? <StatusBadge status={shipment.status} /> : null}
                {shipment?.customer_name ? (
                  <p className="type-caption w-full truncate text-ink-48">{shipment.customer_name}</p>
                ) : null}
              </div>
              <div className="flex min-w-0 flex-wrap items-center gap-3 lg:flex-1 lg:justify-end">
                {criticalAttention > 0 ? (
                  <LocalizedStatus tone="red" className="shrink-0">
                    {criticalAttention} cần xem lại
                  </LocalizedStatus>
                ) : (
                  <LocalizedStatus tone="green" className="shrink-0">
                    Đã xử lý xong
                  </LocalizedStatus>
                )}
                <Button
                  variant="primary"
                  className="min-h-11 w-full px-6 sm:w-auto"
                  onClick={() => navigate(`/shipments/${shipmentId}/export`)}
                >
                  Chuyển đến xuất Excel
                </Button>
              </div>
            </div>
          </header>

          <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
            <aside className="flex min-w-0 flex-col gap-3 lg:sticky lg:top-[88px] lg:h-[calc(100vh-104px)]">
              <section className={`${UTILITY_CARD} flex min-h-0 flex-1 flex-col overflow-hidden`}>
                <div
                  role="group"
                  aria-label="Chọn chứng từ"
                  className="scrollbar-none relative z-10 flex shrink-0 items-stretch gap-2 overflow-x-auto border-b border-divider bg-canvas px-4 py-3"
                >
                  {documents.map((doc) => {
                    const active = doc.document.id === activeDoc?.document.id;
                    return (
                      <div
                        key={doc.document.id}
                        className="flex min-w-0 flex-none flex-col items-start"
                        aria-current={active ? "true" : undefined}
                      >
                        <Chip
                          active={active}
                          className="!inline-flex max-w-[220px] justify-start"
                          title={doc.document.filename}
                          onClick={() => {
                            setActiveDocId(doc.document.id);
                            setSelectedFieldId(null);
                          }}
                        >
                          <span className="block truncate">{doc.document.filename}</span>
                        </Chip>
                      </div>
                    );
                  })}
                </div>

                <div className="min-h-0 flex-1 overflow-y-auto bg-parchment p-3">
                  {activeDoc ? (
                    <div className="border border-hairline bg-canvas">
                      <DocumentSource doc={activeDoc.document} selectedField={selectedField} />
                    </div>
                  ) : null}
                </div>
              </section>
            </aside>

            <main className="flex min-w-0 flex-col gap-6">
              {activeDoc ? (
                <>
                  {groupedFields.map((group) =>
                    group.fields.length === 0 ? null : (
                      <DataCard
                        key={group.key}
                        title={group.title}
                        caption={`${group.fields.length} trường · ${group.fields.filter((f) => isNeedsAttention(f)).length} cần xem lại`}
                      >
                        <div>
                          {group.fields.map((field) => (
                            <ScalarFieldRow
                              key={field.id}
                              shipmentId={shipmentId}
                              field={field}
                              selected={field.id === selectedFieldId}
                              onSelect={selectField}
                            />
                          ))}
                        </div>
                      </DataCard>
                    ),
                  )}

                  <DataCard
                    title="Dòng hàng"
                    caption={`${lineItemCount} dòng · ${lineFieldCount} trường · ${lineAttention} cần xem lại`}
                  >
                    <LineItemsTable
                      shipmentId={shipmentId}
                      lineItems={activeDoc.line_items}
                      selectedFieldId={selectedFieldId}
                      onSelect={selectField}
                    />
                  </DataCard>
                </>
              ) : null}

              <DataCard title="Các bên tham gia">
                {partiesQuery.isPending ? (
                  <div className="flex justify-center px-6 py-8">
                    <Spinner />
                  </div>
                ) : partiesQuery.isError ? (
                  <div className="px-6 py-4">
                    <Callout tone="critical" title="Không tải được danh sách các bên">
                      {partiesQuery.error.message}
                    </Callout>
                  </div>
                ) : (
                  <PartiesSection parties={partiesQuery.data ?? []} />
                )}
              </DataCard>
            </main>
          </div>
        </div>
      </Container>
    </Section>
  );
}
