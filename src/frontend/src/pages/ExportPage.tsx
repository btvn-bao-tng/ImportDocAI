import { useId, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { DocumentExtraction, ExportSummary, FieldRow, LineItemRow } from "../api/types";
import {
  Button,
  ButtonLink,
  Callout,
  Container,
  EmptyState,
  LocalizedStatus,
  PageHeader,
  Section,
  Spinner,
  Stat,
  StatusBadge,
  Table,
  TableHeadCell,
} from "../components/ui";

const DEFAULT_TEMPLATE = "uandi_import_v1";

type TemplateRow = Awaited<ReturnType<typeof api.listTemplates>>[number];
type CreateExportResult = Awaited<ReturnType<typeof api.createExport>>;

const dateFormatter = new Intl.DateTimeFormat("vi-VN", {
  dateStyle: "short",
  timeStyle: "short",
});

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : dateFormatter.format(d);
}

const TRANSPORT_KEYS: { key: string; label: string }[] = [
  { key: "transport.volume_cbm", label: "Thể tích (CBM)" },
  { key: "transport.net_weight_kg", label: "Trọng lượng tịnh (kg)" },
  { key: "transport.gross_weight_kg", label: "Trọng lượng cả bì (kg)" },
];

const PARTY_KEYS: { key: string; label: string }[] = [
  { key: "parties.seller_name", label: "Người bán" },
  { key: "parties.buyer_name", label: "Người mua" },
  { key: "parties.seller_address", label: "Địa chỉ người bán" },
  { key: "parties.buyer_address", label: "Địa chỉ người mua" },
];

function findField(fields: FieldRow[], key: string): FieldRow | undefined {
  return fields.find((field) => field.field_key === key);
}

function needsReview(field: FieldRow): boolean {
  return field.critical && field.review_status !== "CONFIRMED";
}

interface PreviewRow {
  key: string;
  target: string;
  label: string;
  value: string;
  unit: string | null;
  sourcePage: number | null;
  critical: boolean;
  reviewStatus: FieldRow["review_status"] | null;
  flag: boolean;
}

function rowFromField(key: string, label: string, field: FieldRow | undefined): PreviewRow {
  return {
    key,
    target: key,
    label,
    value: field?.normalized_value ?? field?.raw_value ?? "",
    unit: field?.unit ?? null,
    sourcePage: field?.page ?? null,
    critical: field?.critical ?? true,
    reviewStatus: field?.review_status ?? null,
    flag: field ? needsReview(field) : true,
  };
}

function lineRows(item: LineItemRow): PreviewRow[] {
  const base = `line_items[${item.seq}]`;
  const toRow = (key: string, label: string, fallback: string | null): PreviewRow => {
    const field = findField(item.fields, key);
    return {
      key: `${base}.${key}`,
      target: `${base}.${key}`,
      label,
      value: field?.normalized_value ?? field?.raw_value ?? fallback ?? "",
      unit: field?.unit ?? null,
      sourcePage: field?.page ?? null,
      critical: field?.critical ?? true,
      reviewStatus: field?.review_status ?? null,
      flag: field ? needsReview(field) : fallback === null,
    };
  };

  return [
    toRow("description", "Diễn giải", item.description),
    toRow("quantity", "Số lượng", item.quantity),
    toRow("amount", "Thành tiền", item.amount),
  ];
}

function valueOrDash(value: string): string {
  return value.trim() ? value : "—";
}

const CARD = "rounded-lg border border-hairline bg-canvas";
const CELL = "py-3 pr-4 align-top";

function DocumentPreview({ extraction }: { extraction: DocumentExtraction }) {
  const rows = useMemo<PreviewRow[]>(() => {
    const transport = TRANSPORT_KEYS.map(({ key, label }) =>
      rowFromField(key, label, findField(extraction.fields, key)),
    );
    const parties = PARTY_KEYS.map(({ key, label }) =>
      rowFromField(key, label, findField(extraction.fields, key)),
    ).filter((row) => row.value.trim() !== "" || row.flag === false);
    const lines = extraction.line_items.flatMap(lineRows);
    return [...transport, ...parties, ...lines];
  }, [extraction]);

  const reviewCount = rows.filter((row) => row.flag).length;

  return (
    <section className={`${CARD} px-6 pb-2`}>
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-divider py-5">
        <div className="min-w-0">
          <h3 className="type-body-strong truncate text-ink">{extraction.document.filename}</h3>
          <p className="type-caption mt-1 text-ink-48">
            {extraction.document.doc_type ?? "Chưa phân loại"}
            {extraction.document.page_count > 0 ? ` · ${extraction.document.page_count} trang` : ""}
          </p>
        </div>
        {reviewCount > 0 ? (
          <LocalizedStatus tone="red">{reviewCount} dòng cần xem lại</LocalizedStatus>
        ) : (
          <LocalizedStatus tone="green">Đủ dữ liệu</LocalizedStatus>
        )}
      </header>

      <div className="pt-3">
        <Table
          minWidth={720}
          head={
            <tr>
              <TableHeadCell>Ô đích</TableHeadCell>
              <TableHeadCell>Nguồn</TableHeadCell>
              <TableHeadCell>Giá trị</TableHeadCell>
              <TableHeadCell>Trạng thái</TableHeadCell>
            </tr>
          }
        >
          {rows.map((row) => (
            <tr key={row.key} className="border-t border-divider">
              <td
                className={`${CELL} pl-3 ${row.flag ? "border-l-[3px] border-action" : "border-l-[3px] border-transparent"}`}
              >
                <span className="type-caption-strong text-ink">{row.label}</span>
                <code className="type-fine-print mt-0.5 block text-ink-48">{row.target}</code>
              </td>
              <td className={`${CELL} type-caption text-ink-48`}>
                {row.sourcePage !== null ? `Trang ${row.sourcePage}` : "—"}
              </td>
              <td className={`${CELL} type-body tabular-nums text-ink-80`}>
                {valueOrDash(row.value)}
                {row.unit ? <span className="type-caption ml-1 text-ink-48">{row.unit}</span> : null}
              </td>
              <td className={CELL}>
                {row.flag ? (
                  <LocalizedStatus tone="red">Cần xem lại</LocalizedStatus>
                ) : row.reviewStatus === "CONFIRMED" ? (
                  <LocalizedStatus tone="green">Đã xác nhận</LocalizedStatus>
                ) : (
                  <span className="type-caption text-ink-48">Không bắt buộc</span>
                )}
              </td>
            </tr>
          ))}
        </Table>
      </div>
    </section>
  );
}

function ResultPanel({ result, onReset }: { result: CreateExportResult; onReset: () => void }) {
  return (
    <div className={`${CARD} p-6`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h2 className="type-tagline text-ink">Đã tạo file Excel</h2>
          <StatusBadge status={result.mode} />
        </div>
        <p className="type-caption text-ink-48">{result.filename}</p>
      </div>

      <div className="mt-6 grid gap-4 sm:grid-cols-3">
        <Stat
          label="Chế độ"
          value={result.mode === "FINAL" ? "Bản chính thức (FINAL)" : "Bản nháp (DRAFT)"}
        />
        <Stat label="Số ô đã điền" value={result.filled} />
        <Stat
          label="Trường thiếu nguồn"
          value={result.missing.length}
          tone={result.missing.length > 0 ? "critical" : "default"}
        />
      </div>

      {result.missing.length > 0 && (
        <div className="mt-5">
          <Callout tone="warning" title="Trường thiếu nguồn — không tự điền">
            <ul className="list-disc space-y-1 pl-5">
              {result.missing.map((key) => (
                <li key={key}>
                  <code>{key}</code>
                </li>
              ))}
            </ul>
          </Callout>
        </div>
      )}

      {result.warnings.length > 0 && (
        <div className="mt-4">
          <Callout tone="warning" title="Cảnh báo khi xuất">
            <ul className="list-disc space-y-1 pl-5">
              {result.warnings.map((warning, index) => (
                <li key={`${index}-${warning}`}>{warning}</li>
              ))}
            </ul>
          </Callout>
        </div>
      )}

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <ButtonLink href={api.exportDownloadUrl(result.id)} variant="primary" download>
          Tải file Excel
        </ButtonLink>
        <ButtonLink href={api.exportReportUrl(result.id)} variant="secondary">
          Xem báo cáo đối soát
        </ButtonLink>
        <Button variant="ghost" onClick={onReset}>
          Tạo bản khác
        </Button>
      </div>
    </div>
  );
}

function HistoryTable({ exports }: { exports: ExportSummary[] }) {
  return (
    <div className={`${CARD} px-6 pb-2`}>
      <Table
        minWidth={760}
        head={
          <tr>
            <TableHeadCell>Tên file</TableHeadCell>
            <TableHeadCell>Chế độ</TableHeadCell>
            <TableHeadCell>Mẫu</TableHeadCell>
            <TableHeadCell>Thời gian tạo</TableHeadCell>
            <TableHeadCell align="right">Cảnh báo</TableHeadCell>
            <TableHeadCell align="right">Tải xuống</TableHeadCell>
          </tr>
        }
      >
        {exports.map((row) => (
          <tr key={row.id} className="border-t border-divider">
            <td className={`${CELL} pt-4 pb-4 pl-0 type-body text-ink`}>
              {row.filename ?? <span className="text-ink-48">—</span>}
            </td>
            <td className={`${CELL} pt-4 pb-4`}>
              <StatusBadge status={row.mode} />
            </td>
            <td className={`${CELL} pt-4 pb-4 type-caption text-ink-80`}>{row.template_version}</td>
            <td className={`${CELL} pt-4 pb-4 type-caption whitespace-nowrap tabular-nums text-ink-48`}>
              {formatDate(row.created_at)}
            </td>
            <td className={`${CELL} pt-4 pb-4 text-right type-body tabular-nums`}>
              {row.warnings.length > 0 ? (
                <LocalizedStatus tone="amber">{row.warnings.length} cảnh báo</LocalizedStatus>
              ) : (
                <span className="text-ink-48">0</span>
              )}
            </td>
            <td className={`${CELL} pt-4 pb-4 text-right`}>
              <a
                href={api.exportDownloadUrl(row.id)}
                download
                className="type-caption text-action active:opacity-70"
              >
                Tải xuống
              </a>
            </td>
          </tr>
        ))}
      </Table>
    </div>
  );
}

export default function ExportPage() {
  const { id } = useParams<{ id: string }>();
  const shipmentId = Number(id ?? "");
  const templateSelectId = useId();
  const queryClient = useQueryClient();
  const [pickedVersion, setPickedVersion] = useState<string | null>(null);
  const [result, setResult] = useState<CreateExportResult | null>(null);

  const templatesQuery = useQuery({
    queryKey: ["templates"],
    queryFn: () => api.listTemplates(),
  });

  const exportsQuery = useQuery({
    queryKey: ["exports", shipmentId],
    queryFn: () => api.listExports(shipmentId),
    enabled: Number.isFinite(shipmentId) && shipmentId > 0,
  });

  const extractionsQuery = useQuery({
    queryKey: ["extractions", shipmentId],
    queryFn: () => api.getExtractions(shipmentId),
    enabled: Number.isFinite(shipmentId) && shipmentId > 0,
  });

  const templatesData = templatesQuery.data;
  const templates = useMemo<TemplateRow[]>(() => templatesData ?? [], [templatesData]);

  const createMutation = useMutation({
    mutationFn: (version: string) => api.createExport(shipmentId, version),
    onSuccess: async (created) => {
      setResult(created);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["exports", shipmentId] }),
        queryClient.invalidateQueries({ queryKey: ["shipment", shipmentId] }),
      ]);
    },
  });

  const documents = extractionsQuery.data?.documents;
  const extractions = useMemo(() => documents ?? [], [documents]);

  const selectedVersion = useMemo<string>(() => {
    if (templates.length === 0) return pickedVersion ?? DEFAULT_TEMPLATE;
    if (pickedVersion && templates.some((t) => t.version === pickedVersion)) return pickedVersion;
    if (templates.some((t) => t.version === DEFAULT_TEMPLATE)) return DEFAULT_TEMPLATE;
    return templates[0].version;
  }, [templates, pickedVersion]);

  const selectedTemplate = templates.find((t) => t.version === selectedVersion) ?? null;

  const reviewCount = useMemo(
    () =>
      extractions.reduce((total, extraction) => {
        const criticalFields = extraction.fields.filter(
          (field) => field.critical && field.review_status !== "CONFIRMED",
        ).length;
        return total + criticalFields;
      }, 0),
    [extractions],
  );

  const invalidId = !Number.isFinite(shipmentId) || shipmentId <= 0;

  function handleCreate() {
    if (createMutation.isPending || !selectedVersion) return;
    createMutation.mutate(selectedVersion);
  }

  if (invalidId) {
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

  return (
    <Section tone="light">
      <Container width="content">
        <PageHeader
          eyebrow={
            <span className="type-caption normal-case tracking-normal">
              <Link to="/" className="text-action active:opacity-70">
                Lô hàng
              </Link>
              <span className="mx-2 text-ink-48">/</span>
              <Link to={`/shipments/${shipmentId}/review`} className="text-action active:opacity-70">
                Lô #{shipmentId}
              </Link>
            </span>
          }
          title="Xuất file Excel"
          subtitle="Chọn mẫu, kiểm tra các giá trị sẽ được điền vào file rồi xuất. Giá trị quan trọng chưa xác nhận sẽ khiến file được đánh dấu DRAFT."
        />

        <section className={`${CARD} mb-6 p-6`}>
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div className="flex min-w-[280px] flex-1 flex-col gap-1.5">
              <label htmlFor={templateSelectId} className="type-caption-strong text-ink">
                Mẫu Excel
              </label>
              <select
                id={templateSelectId}
                value={selectedVersion}
                onChange={(e) => {
                  setPickedVersion(e.target.value);
                  setResult(null);
                }}
                disabled={templatesQuery.isPending || templates.length === 0}
                className="h-11 rounded-md border border-hairline bg-canvas px-3 type-body text-ink transition-colors duration-150 focus:border-action focus:outline-none disabled:opacity-40"
              >
                {templates.length === 0 && <option value={DEFAULT_TEMPLATE}>{DEFAULT_TEMPLATE}</option>}
                {templates.map((template) => (
                  <option key={template.version} value={template.version}>
                    {template.version} — {template.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <Button
                variant="primary"
                onClick={handleCreate}
                disabled={createMutation.isPending || templatesQuery.isPending}
              >
                {createMutation.isPending ? "Đang xuất…" : "Tạo file Excel"}
              </Button>
            </div>
          </div>

          {templatesQuery.isError ? (
            <div className="mt-4">
              <Callout tone="critical" title="Không tải được danh sách mẫu">
                {templatesQuery.error.message}
              </Callout>
            </div>
          ) : null}

          {selectedTemplate ? (
            <p className="type-caption mt-4 text-ink-48">
              <span className="type-caption-strong text-ink-80">{selectedTemplate.version}</span> —{" "}
              {selectedTemplate.name} · {selectedTemplate.sheets.join(" / ")}
            </p>
          ) : templatesQuery.isPending ? (
            <div className="mt-4">
              <Spinner />
            </div>
          ) : null}

          {reviewCount > 0 ? (
            <div className="mt-5">
              <Callout tone="warning" title="Còn giá trị quan trọng chưa xác nhận">
                {reviewCount} trường quan trọng chưa được xác nhận. File xuất sẽ được đánh dấu{" "}
                <span className="type-body-strong">DRAFT</span> cho đến khi các giá trị này được xác nhận.
              </Callout>
            </div>
          ) : null}

          {createMutation.isError ? (
            <div className="mt-4">
              <Callout tone="critical" title="Không xuất được file">
                {createMutation.error.message}
              </Callout>
            </div>
          ) : null}
        </section>

        {result ? (
          <div className="mb-6">
            <ResultPanel result={result} onReset={() => setResult(null)} />
          </div>
        ) : null}

        <section className="mb-12">
          <h2 className="type-lead text-ink">Xem trước dữ liệu xuất</h2>
          <p className="type-body mt-2 mb-5 max-w-[720px] text-ink-48">
            Danh sách dưới đây là các giá trị then chốt sẽ được điền vào mẫu. Ô có nhãn “Cần xem lại” là
            trường quan trọng chưa được xác nhận nguồn.
          </p>

          {extractionsQuery.isPending ? (
            <div className={`${CARD} flex items-center justify-center px-6 py-16`}>
              <Spinner />
            </div>
          ) : extractionsQuery.isError ? (
            <Callout tone="critical" title="Không tải được dữ liệu trích xuất">
              {extractionsQuery.error.message}
            </Callout>
          ) : extractions.length === 0 ? (
            <EmptyState
              title="Chưa có dữ liệu để xuất"
              description="Hãy xử lý chứng từ trước khi xuất file Excel."
              action={
                <ButtonLink to={`/shipments/${shipmentId}/upload`} variant="secondary">
                  Tải lên chứng từ
                </ButtonLink>
              }
            />
          ) : (
            <div className="flex flex-col gap-4">
              {extractions.map((extraction) => (
                <DocumentPreview key={extraction.document.id} extraction={extraction} />
              ))}
            </div>
          )}
        </section>

        <section>
          <h2 className="type-lead mb-4 text-ink">Lịch sử xuất file</h2>
          {exportsQuery.isPending ? (
            <div className={`${CARD} flex items-center justify-center px-6 py-16`}>
              <Spinner />
            </div>
          ) : exportsQuery.isError ? (
            <Callout tone="critical" title="Không tải được lịch sử xuất">
              {exportsQuery.error.message}
            </Callout>
          ) : (exportsQuery.data ?? []).length === 0 ? (
            <EmptyState
              title="Chưa xuất file lần nào"
              description="File xuất đầu tiên của lô hàng này sẽ xuất hiện ở đây."
            />
          ) : (
            <HistoryTable exports={exportsQuery.data ?? []} />
          )}
        </section>
      </Container>
    </Section>
  );
}
