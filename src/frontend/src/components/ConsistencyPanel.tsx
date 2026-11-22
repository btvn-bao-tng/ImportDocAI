import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ConsistencyFinding, ConsistencyReport, ConsistencyValue } from "../api/types";
import { Button, Callout, EmptyState, Spinner } from "./ui";

/** Cross-document check: warnings only, the reviewer fixes the source documents. */
export default function ConsistencyPanel({
  shipmentId,
  onValueClick,
}: {
  shipmentId: number;
  onValueClick: (value: ConsistencyValue) => void;
}) {
  const queryClient = useQueryClient();
  const queryKey = ["consistency", shipmentId];
  const query = useQuery({ queryKey, queryFn: () => api.getConsistency(shipmentId) });

  const store = (report: ConsistencyReport) => queryClient.setQueryData(queryKey, report);
  const runMutation = useMutation({ mutationFn: () => api.runConsistency(shipmentId), onSuccess: store });
  const dismissMutation = useMutation({
    mutationFn: (findingId: number) => api.dismissFinding(findingId, undefined),
    onSuccess: store,
  });

  const report = query.data;
  const counts = report?.counts ?? { error: 0, warning: 0 };

  return (
    <section
      aria-label="Kiểm tra chéo"
      className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-lg border border-hairline bg-canvas"
    >
      <header className="flex shrink-0 items-center justify-between gap-2 border-b border-divider px-3 py-2">
        <div className="flex min-w-0 items-baseline gap-2">
          <h2 className="type-caption-strong shrink-0 text-ink">Kiểm tra chéo</h2>
          <p className="type-fine-print truncate text-ink-48">
            {counts.error} lỗi · {counts.warning} cảnh báo
          </p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => runMutation.mutate()}
          disabled={runMutation.isPending}
        >
          {runMutation.isPending ? "Đang kiểm tra…" : "Kiểm tra lại"}
        </Button>
      </header>

      <div className="min-h-0 flex-1 space-y-3 overflow-auto px-3 py-3">
        {query.isPending ? <Spinner /> : null}
        {query.isError ? (
          <Callout tone="critical" title="Không tải được kết quả kiểm tra chéo">
            {query.error.message}
          </Callout>
        ) : null}
        {runMutation.isError ? (
          <Callout tone="critical" title="Không chạy được kiểm tra chéo">
            {runMutation.error.message}
          </Callout>
        ) : null}
        {report?.stale && report.run?.status !== "FAILED" ? (
          <Callout tone="info" title="Chứng từ đã thay đổi sau lần kiểm tra gần nhất">
            Kết quả bên dưới có thể đã cũ. Bấm “Kiểm tra lại” để đối chiếu lại.
          </Callout>
        ) : null}
        {report?.run?.status === "FAILED" ? (
          <Callout tone="warning" title="Lần kiểm tra gần nhất thất bại">
            {report.run.error ?? "Không rõ lỗi."} Kết quả bên dưới (nếu có) là của lần kiểm tra trước.
          </Callout>
        ) : null}
        {report && report.findings.length === 0 && report.run?.status !== "FAILED" ? (
          <EmptyState
            title="Không phát hiện thông tin lệch giữa các chứng từ"
            description={
              report.run
                ? "Các chứng từ trong lô hàng đang thống nhất với nhau."
                : "Chưa kiểm tra. Bấm “Kiểm tra lại” để đối chiếu các chứng từ."
            }
          />
        ) : null}
        {report?.findings.map((finding) => (
          <FindingCard
            key={finding.id}
            finding={finding}
            onValueClick={onValueClick}
            onDismiss={() => dismissMutation.mutate(finding.id)}
            dismissing={dismissMutation.isPending && dismissMutation.variables === finding.id}
          />
        ))}
      </div>
    </section>
  );
}

function FindingCard({
  finding,
  onValueClick,
  onDismiss,
  dismissing,
}: {
  finding: ConsistencyFinding;
  onValueClick: (value: ConsistencyValue) => void;
  onDismiss: () => void;
  dismissing: boolean;
}) {
  const isError = finding.severity === "ERROR";
  return (
    <article
      className={`rounded-md border px-3 py-2 ${
        finding.dismissed ? "border-hairline opacity-60" : isError ? "border-critical-line" : "border-warning-line"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="type-caption-strong flex items-center gap-2 text-ink">
            <span
              aria-hidden="true"
              className={`inline-block h-2 w-2 shrink-0 rounded-full ${isError ? "bg-critical" : "bg-warning"}`}
            />
            <span className="sr-only">{isError ? "Lỗi:" : "Cảnh báo:"}</span>
            {finding.title}
          </p>
          {finding.explanation ? <p className="type-caption mt-0.5 text-ink-80">{finding.explanation}</p> : null}
        </div>
        {finding.dismissed ? (
          <span className="type-fine-print shrink-0 text-ink-48">Đã bỏ qua</span>
        ) : (
          <button
            type="button"
            onClick={onDismiss}
            disabled={dismissing}
            aria-label={`Bỏ qua: ${finding.title}`}
            className="type-fine-print shrink-0 rounded-full border border-hairline px-2 py-0.5 text-ink-48 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action active:scale-95 disabled:opacity-40"
          >
            Bỏ qua
          </button>
        )}
      </div>
      <ul className="mt-2 space-y-1">
        {finding.values.map((value) => (
          <li key={value.field_id}>
            <button
              type="button"
              onClick={() => onValueClick(value)}
              title="Bấm để xem nguồn"
              className="type-caption flex w-full items-baseline justify-between gap-3 rounded-sm px-2 py-1 text-left hover:bg-pearl focus-visible:bg-pearl focus-visible:outline-2 focus-visible:outline-action"
            >
              <span className="min-w-0 truncate text-ink-48">
                {value.filename}
                {value.page ? ` · tr.${value.page}` : ""}
              </span>
              <span className="type-caption-strong shrink-0 text-ink">{value.value}</span>
            </button>
          </li>
        ))}
      </ul>
      {finding.dismissed && finding.dismiss_note ? (
        <p className="type-fine-print mt-1 text-ink-48">Ghi chú: {finding.dismiss_note}</p>
      ) : null}
    </article>
  );
}
