import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { DocumentExtraction, FieldRow, GridSheet, TemplateCell } from "../api/types";
import { DocumentSource } from "../components/DocumentViewer";
import {
  Button,
  Callout,
  Chip,
  Container,
  EmptyState,
  LocalizedStatus,
  Section,
  Spinner,
  StatusBadge,
} from "../components/ui";

const DEFAULT_TEMPLATE = "tokhai_import_v1";

interface EvidenceTarget {
  documentId: number;
  page: number | null;
  bbox: number[] | null;
  cellRef: string | null;
  label: string;
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

const MONO = '[font-family:ui-monospace,"SF Mono",SFMono-Regular,Menlo,monospace]';

/* -------------------------------------------------------------------------- */
/* Merged-cell template grid                                                  */
/* -------------------------------------------------------------------------- */

interface MergeSpan {
  rowStart: number;
  colStart: number;
  rowSpan: number;
  colSpan: number;
}

interface MergeMap {
  anchors: Map<string, MergeSpan>;
  covered: Set<string>;
}

function cellKey(row: number, col: number): string {
  return `${row}:${col}`;
}

function buildMergeMap(merges: string[] | undefined): MergeMap {
  const anchors = new Map<string, MergeSpan>();
  const covered = new Set<string>();
  for (const ref of merges ?? []) {
    const match = /^([A-Za-z]+)(\d+)(?::([A-Za-z]+)(\d+))?$/.exec(ref.trim());
    if (!match) continue;
    const colStart = columnToIndex(match[1]);
    const rowStart = Number(match[2]) - 1;
    const colEnd = match[3] ? columnToIndex(match[3]) : colStart;
    const rowEnd = match[4] ? Number(match[4]) - 1 : rowStart;
    if (rowStart < 0 || colStart < 0 || rowEnd < rowStart || colEnd < colStart) continue;
    const span: MergeSpan = {
      rowStart,
      colStart,
      rowSpan: rowEnd - rowStart + 1,
      colSpan: colEnd - colStart + 1,
    };
    anchors.set(cellKey(rowStart, colStart), span);
    for (let r = rowStart; r <= rowEnd; r += 1) {
      for (let c = colStart; c <= colEnd; c += 1) {
        if (r !== rowStart || c !== colStart) covered.add(cellKey(r, c));
      }
    }
  }
  return { anchors, covered };
}

interface TemplateSheetTableProps {
  sheet: GridSheet;
  cells: Record<string, TemplateCell>;
  selectedField: string | null;
  onValueClick: (cellRef: string, cell: TemplateCell) => void;
}

function TemplateSheetTable({ sheet, cells, selectedField, onValueClick }: TemplateSheetTableProps) {
  const rows = sheet.rows ?? [];
  const columnCount = rows.reduce((max, row) => Math.max(max, row.length), 0);
  const { anchors, covered } = useMemo(() => buildMergeMap(sheet.merges), [sheet.merges]);

  if (rows.length === 0 || columnCount === 0) {
    return (
      <EmptyState title="Sheet không có dữ liệu" description={`Sheet “${sheet.name}” chưa có ô nào.`} />
    );
  }

  return (
    <div className="overflow-auto bg-canvas">
      <table
        className={`w-max border-collapse text-fine-print leading-[1.3] tabular-nums ${MONO}`}
        aria-label={`Mẫu ${sheet.name}`}
      >
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {Array.from({ length: columnCount }, (_, col) => {
                if (covered.has(cellKey(rowIndex, col))) return null;
                const anchor = anchors.get(cellKey(rowIndex, col));
                const rowSpan = anchor ? Math.min(anchor.rowSpan, rows.length - rowIndex) : 1;
                const colSpan = anchor ? Math.min(anchor.colSpan, columnCount - col) : 1;
                const sizeClass = "min-w-6 max-w-[200px]";
                const ref = `${columnIndexToLabel(col)}${rowIndex + 1}`;
                const filled = cells[ref];
                const selected = filled != null && filled.field === selectedField;
                const baseClass = "border px-1 py-0.5 align-top break-words whitespace-pre-wrap";
                if (filled) {
                  const evidence = filled.evidence;
                  const hint = evidence
                    ? `${evidence.label_vi} · ${evidence.filename}${evidence.page ? ` · trang ${evidence.page}` : ""}${
                        evidence.cell_ref ? ` · ${evidence.cell_ref}` : ""
                      }`
                    : `${filled.field} · không lưu nguồn chứng cứ`;
                  return (
                    <td
                      key={col}
                      rowSpan={rowSpan > 1 ? rowSpan : undefined}
                      colSpan={colSpan > 1 ? colSpan : undefined}
                      className={`${baseClass} ${sizeClass} ${
                        selected
                          ? "border-action bg-action/20 font-bold text-ink ring-2 ring-action ring-inset"
                          : evidence
                            ? "border-[#c8dbf5] bg-[#eaf2fd] text-action"
                            : "border-dashed border-[#efdfba] bg-[#fff7e6] text-warning"
                      }`}
                    >
                      <button
                        type="button"
                        onClick={() => onValueClick(ref, filled)}
                        title={hint}
                        aria-label={`${filled.value} — ${hint}`}
                        className="block w-full text-left underline decoration-dotted underline-offset-2 active:opacity-70"
                      >
                        {filled.value}
                      </button>
                    </td>
                  );
                }
                const value = cellLabel(row[col]);
                return (
                  <td
                    key={col}
                    rowSpan={rowSpan > 1 ? rowSpan : undefined}
                    colSpan={colSpan > 1 ? colSpan : undefined}
                    title={value || undefined}
                    className={`${baseClass} ${sizeClass} border-hairline ${value ? "text-ink" : "bg-pearl/40"}`}
                  >
                    {value}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface TemplateViewerProps {
  shipmentId: number;
  version: string;
  name: string;
  selectedField: string | null;
  maximized?: boolean;
  onToggleMaximize?: () => void;
  onValueClick: (cellRef: string, cell: TemplateCell, sheet: string) => void;
}

function TemplateViewer({
  shipmentId,
  version,
  name,
  selectedField,
  maximized = false,
  onToggleMaximize,
  onValueClick,
}: TemplateViewerProps) {
  const previewQuery = useQuery({
    queryKey: ["template-preview", shipmentId, version],
    queryFn: () => api.templatePreview(shipmentId, version),
    enabled: Number.isFinite(shipmentId) && shipmentId > 0,
  });
  const [pickedSheet, setPickedSheet] = useState<string | null>(null);
  const [fitWidth, setFitWidth] = useState(true);
  const [zoom, setZoom] = useState(1);
  const [fitScale, setFitScale] = useState(1);
  const fitContainerRef = useRef<HTMLDivElement | null>(null);


  const sheets = previewQuery.data?.sheets ?? [];
  const cellsBySheet = previewQuery.data?.cells ?? {};
  const current = sheets.find((s) => s.name === pickedSheet) ?? sheets[0] ?? null;
  const filledCount = Object.values(cellsBySheet).reduce((total, cells) => total + Object.keys(cells).length, 0);

  const currentSheetName = current?.name ?? null;

  useEffect(() => {
    if (!fitWidth) return;
    const wrapper = fitContainerRef.current;
    const panel = wrapper?.parentElement;
    const table = wrapper?.querySelector("table");
    if (!panel || !table) return;
    const measure = () => {
      const natural = table.scrollWidth || table.getBoundingClientRect().width;
      const available = panel.clientWidth - 2;
      if (natural > 0 && available > 0) {
        setFitScale(Math.min(1, Math.max(0.35, available / natural)));
      }
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(panel);
    return () => observer.disconnect();
  }, [fitWidth, currentSheetName, filledCount, previewQuery.dataUpdatedAt]);

  return (
    <section className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-lg border border-hairline bg-canvas">
      <header className="flex shrink-0 items-center justify-between gap-2 border-b border-divider px-3 py-2">
        <div className="flex min-w-0 items-baseline gap-2">
          <h2 className="type-caption-strong shrink-0 text-ink">Mẫu tờ khai cuối</h2>
          <p className="type-fine-print truncate text-ink-48" title={`${name} · ${version}`}>
            {version}
            {filledCount > 0 ? ` · ${filledCount} ô đã điền` : " · chưa điền"}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <div className="flex items-center gap-1" role="group" aria-label="Thu phóng mẫu">
            <button
              type="button"
              onClick={() => {
                setFitWidth((value) => {
                  if (value) setZoom(Math.round(fitScale * 10) / 10);
                  return !value;
                });
              }}
              aria-pressed={fitWidth}
              title={fitWidth ? "Tắt thu vừa khung (xem kích thước thật, cuộn ngang)" : "Thu vừa khung để so sánh"}
              className={`type-fine-print rounded-full border px-2 py-0.5 active:scale-95 ${
                fitWidth ? "border-action bg-canvas text-action" : "border-hairline text-ink-48"
              }`}
            >
              Vừa khung
            </button>
            <button
              type="button"
              onClick={() => {
                setFitWidth(false);
                setZoom((value) => Math.max(0.4, Math.round((value - 0.1) * 10) / 10));
              }}
              title="Thu nhỏ"
              className="type-fine-print h-6 w-6 rounded-full border border-hairline text-ink-48 active:scale-95"
            >
              −
            </button>
            <span className="type-fine-print w-9 text-center tabular-nums text-ink-48">
              {Math.round((fitWidth ? fitScale : zoom) * 100)}%
            </span>
            <button
              type="button"
              onClick={() => {
                setFitWidth(false);
                setZoom((value) => Math.min(1.5, Math.round((value + 0.1) * 10) / 10));
              }}
              title="Phóng to"
              className="type-fine-print h-6 w-6 rounded-full border border-hairline text-ink-48 active:scale-95"
            >
              +
            </button>
          </div>
          <a
            href={api.templateFileUrl(version)}
            target="_blank"
            rel="noreferrer"
            className="type-fine-print text-action underline-offset-2 active:opacity-70"
          >
            Mở tệp mẫu
          </a>
          {onToggleMaximize ? (
            <button
              type="button"
              onClick={onToggleMaximize}
              aria-pressed={maximized}
              title={maximized ? "Thu nhỏ khung mẫu" : "Mở rộng khung mẫu"}
              className="type-fine-print rounded-full border border-hairline px-2 py-0.5 text-ink-48 active:scale-95"
            >
              {maximized ? "Thu nhỏ" : "Mở rộng"}
            </button>
          ) : null}
        </div>
      </header>

      {previewQuery.isPending ? (
        <div className="flex flex-1 items-center justify-center">
          <Spinner />
        </div>
      ) : previewQuery.isError ? (
        <div className="px-4 py-4">
          <Callout tone="critical" title="Không tải được mẫu">
            {previewQuery.error.message}
          </Callout>
        </div>
      ) : sheets.length === 0 ? (
        <div className="px-4 py-4">
          <EmptyState title="Mẫu trống" description="Mẫu này không có sheet nào để hiển thị." />
        </div>
      ) : (
        <>
          <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-divider px-3 py-1.5">
            {sheets.length > 1 ? (
              <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Chọn sheet mẫu">
              {sheets.map((sheet) => {
                const active = sheet.name === current?.name;
                return (
                  <button
                    key={sheet.name}
                    type="button"
                    role="tab"
                    id={`template-tab-${sheet.name}`}
                    aria-selected={active}
                    aria-controls="template-tabpanel"
                    onClick={() => setPickedSheet(sheet.name)}
                    className={`type-caption inline-flex min-h-9 items-center rounded-full border px-4 py-1.5 transition-colors active:scale-95 ${
                      active ? "border-action bg-canvas text-action" : "border-hairline bg-canvas text-ink-48"
                    }`}
                  >
                    {sheet.name}
                  </button>
                );
              })}
              </div>
            ) : null}
            <p
              className={`type-fine-print min-w-0 flex-1 truncate text-right ${
                filledCount > 0 ? "text-action" : "text-warning"
              }`}
            >
              {filledCount > 0
                ? "Bấm giá trị màu xanh để xem chứng cứ ở cột trái."
                : "Chưa có dữ liệu trích xuất để điền vào mẫu."}
            </p>
          </div>

          <div
            id="template-tabpanel"
            role="tabpanel"
            aria-labelledby={current ? `template-tab-${current.name}` : undefined}
            className="min-h-0 flex-1 overflow-auto"
          >
            <div
              ref={fitContainerRef}
              style={
                fitWidth
                  ? { zoom: fitScale }
                  : { zoom }
              }
              className="origin-top-left"
            >
            {current ? (
              <TemplateSheetTable
                sheet={current}
                cells={cellsBySheet[current.name] ?? {}}
                selectedField={selectedField}
                onValueClick={(ref, cell) => onValueClick(ref, cell, current.name)}
              />
            ) : null}
            </div>
          </div>
        </>
      )}
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/* Resizable split                                                            */
/* -------------------------------------------------------------------------- */

const MIN_SPLIT = 0.15;
const MAX_SPLIT = 0.85;
const DEFAULT_SPLIT = 0.5;
const KEYBOARD_STEP = 0.02;

function clampSplit(value: number): number {
  return Math.min(MAX_SPLIT, Math.max(MIN_SPLIT, value));
}

export default function ReviewPage() {
  const params = useParams<{ id: string }>();
  const navigate = useNavigate();
  const shipmentId = Number(params.id);
  const validId = Number.isFinite(shipmentId) && shipmentId > 0;

  const [activeDocId, setActiveDocId] = useState<number | null>(null);
  const [split, setSplit] = useState(DEFAULT_SPLIT);
  const [evidence, setEvidence] = useState<EvidenceTarget | null>(null);
  const [evidenceField, setEvidenceField] = useState<string | null>(null);
  const [evidenceNotice, setEvidenceNotice] = useState<string | null>(null);
  const [maximizedPanel, setMaximizedPanel] = useState<"source" | "template" | null>(null);
  const splitRef = useRef<HTMLDivElement | null>(null);
  const draggingRef = useRef(false);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(min-width: 1024px)");
    const reset = () => {
      if (!query.matches) setSplit(DEFAULT_SPLIT);
    };
    reset();
    query.addEventListener("change", reset);
    return () => query.removeEventListener("change", reset);
  }, []);

  const extractionsQuery = useQuery({
    queryKey: ["extractions", shipmentId],
    queryFn: () => api.getExtractions(shipmentId),
    enabled: validId,
  });
  const shipmentQuery = useQuery({
    queryKey: ["shipment", shipmentId],
    queryFn: () => api.getShipment(shipmentId),
    enabled: validId,
  });
  const templatesQuery = useQuery({
    queryKey: ["templates"],
    queryFn: () => api.listTemplates(),
  });

  const documents = useMemo<DocumentExtraction[]>(
    () => extractionsQuery.data?.documents ?? [],
    [extractionsQuery.data],
  );
  const activeDocChanged = activeDocId !== null && !documents.some((d) => d.document.id === activeDocId);
  if (activeDocChanged) setActiveDocId(null);
  const activeDoc = activeDocChanged
    ? documents[0] ?? null
    : documents.find((d) => d.document.id === activeDocId) ?? documents[0] ?? null;

  const allFields = useMemo<FieldRow[]>(
    () => documents.flatMap((d) => [...d.fields, ...d.line_items.flatMap((li) => li.fields)]),
    [documents],
  );

  const criticalAttention = useMemo(
    () =>
      allFields.filter(
        (f) =>
          f.critical &&
          (f.review_status === "UNREVIEWED" || f.review_status === "NEEDS_REVIEW"),
      ).length,
    [allFields],
  );

  const templateVersion = useMemo(() => {
    const templates = templatesQuery.data ?? [];
    if (templates.some((t) => t.version === DEFAULT_TEMPLATE)) return DEFAULT_TEMPLATE;
    return templates[0]?.version ?? DEFAULT_TEMPLATE;
  }, [templatesQuery.data]);

  const templateName = useMemo(() => {
    const templates = templatesQuery.data ?? [];
    return templates.find((t) => t.version === templateVersion)?.name ?? templateVersion;
  }, [templatesQuery.data, templateVersion]);

  function handleTemplateValueClick(cellRef: string, cell: TemplateCell, sheet: string) {
    setEvidenceField(cell.field);
    const ev = cell.evidence;
    if (!ev) {
      setEvidence(null);
      setEvidenceNotice(`“${cell.value}” (${cell.field} · ${sheet}!${cellRef}) không lưu nguồn chứng cứ trong hồ sơ.`);
      return;
    }
    const targetDoc = documents.find((d) => d.document.id === ev.document_id);
    if (!targetDoc) {
      setEvidence(null);
      setEvidenceNotice(`Chứng cứ của “${cell.value}” nằm ở tệp không còn trong lô hàng.`);
      return;
    }
    setEvidenceNotice(null);
    setActiveDocId(ev.document_id);
    setEvidence({
      documentId: ev.document_id,
      page: ev.page,
      bbox: ev.bbox,
      cellRef: ev.cell_ref,
      label: ev.label_vi,
    });
  }

  function updateSplitFromClientX(clientX: number) {
    const el = splitRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0) return;
    setSplit(clampSplit((clientX - rect.left) / rect.width));
  }

  function handleSplitterPointerDown(event: React.PointerEvent<HTMLDivElement>) {
    draggingRef.current = true;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  }

  function handleSplitterPointerMove(event: React.PointerEvent<HTMLDivElement>) {
    if (!draggingRef.current) return;
    updateSplitFromClientX(event.clientX);
  }

  function handleSplitterPointerUp(event: React.PointerEvent<HTMLDivElement>) {
    draggingRef.current = false;
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture?.(event.pointerId);
    }
  }

  function handleSplitterKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      setSplit((value) => clampSplit(value - KEYBOARD_STEP));
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      setSplit((value) => clampSplit(value + KEYBOARD_STEP));
    } else if (event.key === "Home") {
      event.preventDefault();
      setSplit(MIN_SPLIT);
    } else if (event.key === "End") {
      event.preventDefault();
      setSplit(MAX_SPLIT);
    }
  }

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
  const splitPercent = `${(split * 100).toFixed(2)}%`;

  return (
    <Section tone="light" className="pt-4 pb-6 sm:pt-4 sm:pb-6 lg:h-[calc(100dvh-44px)] lg:pb-4">
      <Container width="wide" className="lg:h-full">
        <div className="flex flex-col gap-2 lg:h-full lg:min-h-0">
          <header className="sticky top-11 z-20 -mx-4 shrink-0 border-b border-hairline bg-parchment/80 px-4 backdrop-blur-xl sm:-mx-6 sm:px-6 lg:top-0">
            <div className="flex flex-col gap-1 py-2 lg:flex-row lg:flex-wrap lg:items-center lg:justify-between lg:gap-x-6">
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
                  className="min-h-9 w-full px-4 sm:w-auto"
                  onClick={() => navigate(`/shipments/${shipmentId}/verify`)}
                >
                  Kiểm tra &amp; xuất file
                </Button>
              </div>
            </div>
          </header>

          <div
            ref={splitRef}
            className="flex min-h-0 flex-col items-stretch gap-4 lg:min-h-0 lg:flex-1 lg:flex-row lg:gap-0"
            style={{ ["--split-width" as string]: splitPercent }}
          >
            <aside
              id="source-documents"
              aria-label="Chứng từ nguồn"
              className={`flex w-full min-w-0 flex-col overflow-hidden rounded-lg border border-hairline bg-canvas lg:h-full lg:shrink-0 ${
                maximizedPanel === "source"
                  ? "h-[75vh] lg:w-[calc(100%-0.75rem)]"
                  : maximizedPanel === "template"
                    ? "h-[60vh] lg:hidden"
                    : "h-[60vh] lg:w-[var(--split-width)]"
              }`}
            >
              <div className="flex shrink-0 items-center justify-between gap-2 border-b border-divider px-3 py-1.5">
                <div className="flex min-w-0 items-baseline gap-2">
                  <h2 className="type-caption-strong shrink-0 text-ink">Chứng từ nguồn</h2>
                  <p className="type-fine-print truncate text-ink-48">
                    {activeDoc ? activeDoc.document.filename : "Chưa chọn chứng từ"}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setMaximizedPanel((current) => (current === "source" ? null : "source"))}
                  aria-pressed={maximizedPanel === "source"}
                  title={maximizedPanel === "source" ? "Thu nhỏ khung chứng từ" : "Mở rộng khung chứng từ"}
                  className="type-fine-print shrink-0 rounded-full border border-hairline px-2 py-0.5 text-ink-48 active:scale-95"
                >
                  {maximizedPanel === "source" ? "Thu nhỏ" : "Mở rộng"}
                </button>
              </div>
              <div
                role="group"
                aria-label="Chọn chứng từ"
                className="scrollbar-none relative z-10 flex shrink-0 items-stretch gap-1.5 overflow-x-auto border-b border-divider bg-canvas px-3 py-1.5"
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
                        onClick={() => setActiveDocId(doc.document.id)}
                      >
                        <span className="block truncate">{doc.document.filename}</span>
                      </Chip>
                    </div>
                  );
                })}
              </div>

              <div className="min-h-0 flex-1 overflow-auto bg-parchment p-3">
                {evidenceNotice ? (
                  <p
                    role="status"
                    className="type-fine-print mb-2 truncate rounded-sm border border-[#efdfba] bg-[#fff7e6] px-2 py-1 text-warning"
                    title={evidenceNotice}
                  >
                    Không có chứng cứ để đánh dấu · {evidenceNotice}
                  </p>
                ) : evidence ? (
                  <p className="type-fine-print mb-2 truncate rounded-sm border border-[#c8dbf5] bg-[#eaf2fd] px-2 py-1 text-action">
                    Đang đánh dấu: {evidence.label}
                    {evidence.cellRef ? ` · ${evidence.cellRef}` : evidence.page ? ` · trang ${evidence.page}` : null}
                  </p>
                ) : null}
                {activeDoc ? (
                  <div className="border border-hairline bg-canvas">
                    <DocumentSource doc={activeDoc.document} evidence={evidence} />
                  </div>
                ) : null}
              </div>
            </aside>

            <div
              role="separator"
              aria-orientation="vertical"
              aria-label="Điều chỉnh độ rộng khung"
              aria-controls="source-documents"
              aria-valuemin={Math.round(MIN_SPLIT * 100)}
              aria-valuemax={Math.round(MAX_SPLIT * 100)}
              aria-valuenow={Math.round(split * 100)}
              tabIndex={0}
              onPointerDown={handleSplitterPointerDown}
              onPointerMove={handleSplitterPointerMove}
              onPointerUp={handleSplitterPointerUp}
              onPointerCancel={handleSplitterPointerUp}
              onKeyDown={handleSplitterKeyDown}
              className={`group relative shrink-0 cursor-col-resize touch-none items-center justify-center focus:outline-none focus-visible:ring-2 focus-visible:ring-action ${
                maximizedPanel ? "hidden" : "hidden w-3 lg:flex"
              }`}
            >
              <span
                aria-hidden="true"
                className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-hairline transition-colors group-hover:bg-action group-focus-visible:bg-action group-active:bg-action"
              />
              <span
                aria-hidden="true"
                className="relative h-12 w-1 rounded-full bg-ink-48/50 transition-colors group-hover:bg-action group-focus-visible:bg-action group-active:bg-action"
              />
            </div>

            <div
              className={`min-w-0 flex-1 lg:pl-0 ${
                maximizedPanel === "source" ? "hidden" : ""
              }`}
            >
              <div className={maximizedPanel === "template" ? "h-[75vh] min-h-0 lg:h-full" : "h-[70vh] min-h-0 lg:h-full"}>
                <TemplateViewer
                  shipmentId={shipmentId}
                  version={templateVersion}
                  name={templateName}
                  selectedField={evidenceField}
                  maximized={maximizedPanel === "template"}
                  onToggleMaximize={() =>
                    setMaximizedPanel((current) => (current === "template" ? null : "template"))
                  }
                  onValueClick={handleTemplateValueClick}
                />
              </div>
            </div>
          </div>
        </div>
      </Container>
    </Section>
  );
}
