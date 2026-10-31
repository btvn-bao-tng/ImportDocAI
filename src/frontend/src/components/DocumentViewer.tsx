import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getDocument, GlobalWorkerOptions, type PDFDocumentProxy } from "pdfjs-dist";
import workerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { api } from "../api/client";
import type { DocumentSummary } from "../api/types";
import { Callout, EmptyState, Spinner } from "./ui";

GlobalWorkerOptions.workerSrc = workerSrc;

interface EvidenceTarget {
  documentId: number;
  page: number | null;
  bbox: number[] | null;
  cellRef: string | null;
  label: string;
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



/** Bring `node` into view inside its own scroll container on both axes, without moving the page. */
function scrollWithinContainer(node: HTMLElement | null, block: ScrollLogicalPosition = "start") {
  if (!node) return;
  let container = node.parentElement;
  while (container) {
    const style = window.getComputedStyle(container);
    const scrollable = /(auto|scroll)/.test(style.overflowY) || /(auto|scroll)/.test(style.overflowX);
    const overflows = container.scrollHeight > container.clientHeight || container.scrollWidth > container.clientWidth;
    if (scrollable && overflows) break;
    container = container.parentElement;
  }
  if (!container || container === document.body || container === document.documentElement) return;

  // offsetLeft/offsetTop stay in the container's own scroll coordinate space, which
  // keeps the math correct when the sheet is scaled with CSS zoom.
  let left = 0;
  let top = 0;
  let current: HTMLElement | null = node;
  while (current && current !== container) {
    left += current.offsetLeft;
    top += current.offsetTop;
    current = current.offsetParent as HTMLElement | null;
    if (current && !container.contains(current)) break;
  }

  const targetTop =
    block === "center" ? top - container.clientHeight / 2 + node.offsetHeight / 2 : top - 12;
  const targetLeft = left - container.clientWidth / 2 + node.offsetWidth / 2;
  container.scrollTo({
    top: Math.max(0, targetTop),
    left: Math.max(0, targetLeft),
    behavior: "smooth",
  });
}

interface PdfViewerProps {
  docId: number;
  evidence: EvidenceTarget | null;
}

function PdfViewer({ docId, evidence }: PdfViewerProps) {
  const url = useMemo(() => api.documentFileUrl(docId), [docId]);
  const pageRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const highlightPage = evidence?.documentId === docId ? evidence.page : null;
  const highlightBox = evidence?.documentId === docId ? evidence.bbox : null;

  useEffect(() => {
    if (!highlightPage) return;
    scrollWithinContainer(pageRefs.current[highlightPage] ?? null);
  }, [highlightPage]);
  const pdfQuery = useQuery({
    queryKey: ["pdf", "document", docId],
    queryFn: async () => {
      const task = getDocument({ url });
      const loaded = await task.promise;
      return { pdf: loaded, task };
    },
    gcTime: 0,
  });

  const pdf = pdfQuery.data?.pdf ?? null;
  const resolvedError = pdfQuery.isError ? pdfQuery.error.message : null;

  useEffect(() => {
    const task = pdfQuery.data?.task;
    if (!task) return;
    return () => {
      void task.destroy();
    };
  }, [pdfQuery.data]);

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
        return (
          <div
            key={pageNumber}
            ref={(node) => {
              pageRefs.current[pageNumber] = node;
            }}
            className="flex flex-col items-center"
          >
            <PdfPage
              pdf={pdf}
              pageNumber={pageNumber}
              highlight={highlightPage === pageNumber ? highlightBox : null}
            />
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
  highlight?: number[] | null;
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

  return (
    <div
      className="relative inline-block max-w-full border border-hairline bg-canvas"
      style={{ width: viewSize ? `${Math.floor(viewSize.width)}px` : undefined, minHeight: viewSize ? undefined : 480 }}
    >
      <canvas ref={canvasRef} className="block max-w-full" aria-label={`Trang ${pageNumber}`} />
      {highlight && highlight.length === 4 ? (
        <div
          data-testid={`pdf-highlight-${pageNumber}`}
          className="pointer-events-none absolute rounded-xs border-2 border-action bg-action/20 shadow-[0_0_0_3px_rgba(0,113,227,0.15)]"
          style={{
            left: `${highlight[0] * 100}%`,
            top: `${highlight[1] * 100}%`,
            width: `${Math.max(0, highlight[2] - highlight[0]) * 100}%`,
            height: `${Math.max(0, highlight[3] - highlight[1]) * 100}%`,
          }}
        />
      ) : null}
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
    </div>
  );
}

const columnToIndex = (letters: string): number =>
  letters
    .toUpperCase()
    .split("")
    .reduce((acc, ch) => acc * 26 + (ch.charCodeAt(0) - 64), 0) - 1;

function columnIndexToLabel(col: number): string {
  let label = "";
  let n = col;
  while (n >= 0) {
    label = String.fromCharCode(65 + (n % 26)) + label;
    n = Math.floor(n / 26) - 1;
  }
  return label;
}

function cellLabel(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function parseCellRef(ref: string | null): { sheet: string; row: number; col: number } | null {
  if (!ref) return null;
  const [sheet, coord] = ref.split("!", 2);
  const match = /^([A-Za-z]+)(\d+)$/.exec((coord ?? "").trim());
  if (!sheet || !match) return null;
  return { sheet, row: Number(match[2]) - 1, col: columnToIndex(match[1]) };
}

function ExcelViewer({ docId, evidence }: { docId: number; evidence: EvidenceTarget | null }) {
  const gridQuery = useQuery({
    queryKey: ["grid", docId],
    queryFn: () => api.documentGrid(docId),
  });
  const [pickedSheet, setPickedSheet] = useState<string | null>(null);
  const cellRefs = useRef<Record<string, HTMLTableCellElement | null>>({});
  const target = evidence?.documentId === docId ? parseCellRef(evidence.cellRef) : null;

  const targetRow = target?.row;
  const targetCol = target?.col;
  useEffect(() => {
    if (targetRow === undefined || targetCol === undefined) return;
    scrollWithinContainer(cellRefs.current[cellKey(targetRow, targetCol)] ?? null, "center");
  }, [targetRow, targetCol]);

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

  const evidenceSheet = target && sheets.some((s) => s.name === target.sheet) ? target.sheet : null;
  const current =
    sheets.find((s) => s.name === evidenceSheet) ?? sheets.find((s) => s.name === pickedSheet) ?? sheets[0];
  const rows = current.rows ?? [];
  const columnCount = rows.reduce((max, row) => Math.max(max, row.length), 0);
  const isTarget = (row: number, col: number) =>
    target !== null && evidenceSheet === current.name && target.row === row && target.col === col;

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
        <div className="h-full min-h-[320px] overflow-auto border border-hairline bg-canvas">
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
                    {columnIndexToLabel(col)}
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
                    const highlighted = isTarget(rowIndex, col);
                    return (
                      <td
                        key={col}
                        ref={(node) => {
                          cellRefs.current[cellKey(rowIndex, col)] = node;
                        }}
                        data-testid={highlighted ? "excel-highlight" : undefined}
                        aria-current={highlighted ? "true" : undefined}
                        className={`max-w-[280px] truncate border-r border-b px-2 py-1 ${
                          highlighted
                            ? "border-action bg-action/15 font-bold text-ink ring-2 ring-action ring-inset"
                            : "border-divider text-ink-80"
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
    </div>
  );
}

function cellKey(row: number, col: number): string {
  return `${row}:${col}`;
}

const MONO = '[font-family:ui-monospace,"SF Mono",SFMono-Regular,Menlo,monospace]';

function ImageViewer({ docId, evidence }: { docId: number; evidence: EvidenceTarget | null }) {
  const bbox = evidence?.documentId === docId ? evidence.bbox : null;
  return (
    <div className="flex justify-center px-4 py-5">
      <div className="relative inline-block max-w-full">
        <img
          src={api.documentFileUrl(docId)}
          alt="Chứng từ dạng ảnh"
          className="block max-h-[75vh] max-w-full border border-hairline bg-canvas object-contain"
        />
        {bbox && bbox.length === 4 ? (
          <div
            data-testid="image-highlight"
            className="pointer-events-none absolute rounded-xs border-2 border-action bg-action/20"
            style={{
              left: `${bbox[0] * 100}%`,
              top: `${bbox[1] * 100}%`,
              width: `${Math.max(0, bbox[2] - bbox[0]) * 100}%`,
              height: `${Math.max(0, bbox[3] - bbox[1]) * 100}%`,
            }}
          />
        ) : null}
      </div>
    </div>
  );
}

export function DocumentSource({ doc, evidence }: { doc: DocumentSummary; evidence: EvidenceTarget | null }) {
  const kind = docKind(doc);

  if (kind === "pdf") return <PdfViewer docId={doc.id} evidence={evidence} />;
  if (kind === "excel") return <ExcelViewer docId={doc.id} evidence={evidence} />;
  if (kind === "image") return <ImageViewer docId={doc.id} evidence={evidence} />;
  return (
    <div className="mx-4 my-4 flex flex-col items-center gap-3 border border-hairline bg-canvas px-6 py-10 text-center">
      <p className="type-body text-ink-80">Không có bản xem trước dạng nhúng cho tệp này.</p>
      <a href={api.documentFileUrl(doc.id)} target="_blank" rel="noreferrer" className="type-body text-action underline">
        Mở tệp gốc
      </a>
    </div>
  );
}
