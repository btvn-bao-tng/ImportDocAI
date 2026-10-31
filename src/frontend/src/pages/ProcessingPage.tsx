import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { DocumentSummary, JobProgress } from "../api/types";
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
  StatusBadge,
  Stepper,
} from "../components/ui";
import type { Tone } from "../components/ui";

const POLL_MS = 1500;

const STAGE_LABEL: Record<string, string> = {
  PARSING: "Đọc tệp",
  CLASSIFYING: "Phân loại",
  EXTRACTING: "Trích xuất",
  PERSISTING: "Lưu kết quả",
};

function stageLabel(stage: string | null): string {
  if (!stage) return "Đang chạy";
  return STAGE_LABEL[stage] ?? "Tổng hợp dữ liệu";
}

function docStage(progress: JobProgress | null, docId: number): string | null {
  return progress?.document_stages?.[String(docId)] ?? null;
}

function docStageIndex(progress: JobProgress, stage: string | null): number {
  return Math.max(0, progress.stages.indexOf(stage ?? ""));
}

function docStageTone(stage: string | null): Tone {
  return stage === "EXTRACTING" ? "amber" : stage ? "blue" : "gray";
}

const DOC_STATUS_TONE: Record<string, "green" | "amber" | "red" | "gray"> = {
  EXTRACTED: "green",
  PROCESSING: "amber",
  UPLOADED: "gray",
  CLASSIFIED: "gray",
  IS_DUPLICATE_OF: "amber",
  SUPERSEDED_BY: "gray",
  FAILED: "red",
};

const DOC_STATUS_LABEL: Record<string, string> = {
  EXTRACTED: "Hoàn tất",
  PROCESSING: "Đang xử lý",
  UPLOADED: "Chờ xử lý",
  CLASSIFIED: "Chờ xử lý",
  IS_DUPLICATE_OF: "Bản trùng",
  SUPERSEDED_BY: "Đã thay thế",
  FAILED: "Thất bại",
};

const CARD = "rounded-lg border border-hairline bg-canvas";

function DocumentStageRow({
  doc,
  progress,
  running,
}: {
  doc: DocumentSummary;
  progress: JobProgress | null;
  running: boolean;
}) {
  const stage = docStage(progress, doc.id);
  const inFlight = stage !== null;
  const finished = doc.status === "EXTRACTED" || doc.status === "FAILED";
  const tone = DOC_STATUS_TONE[doc.status] ?? "gray";

  return (
    <li className="border-t border-divider first:border-t-0">
      <div className="flex flex-wrap items-start justify-between gap-3 px-6 py-4">
        <div className="min-w-0 flex-1">
          <p className="type-body truncate text-ink">{doc.filename}</p>
          <p className="type-caption mt-1 text-ink-48">
            {doc.doc_type ?? "Chưa phân loại"}
            {inFlight ? ` · ${stageLabel(stage)}…` : ""}
          </p>
          {inFlight && progress ? (
            <div className="mt-3 max-w-[420px]">
              <Stepper steps={progress.stages.map((s) => stageLabel(s))} current={docStageIndex(progress, stage)} />
            </div>
          ) : null}
          {doc.error ? <p className="type-caption mt-2 text-critical">{doc.error}</p> : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {inFlight ? (
            <>
              <LocalizedStatus tone={docStageTone(stage)}>{stageLabel(stage)}</LocalizedStatus>
              <Spinner label="" />
            </>
          ) : finished ? (
            <LocalizedStatus tone={tone}>{DOC_STATUS_LABEL[doc.status] ?? doc.status}</LocalizedStatus>
          ) : running ? (
            <span className="type-caption text-ink-48">Chờ đến lượt</span>
          ) : (
            <span className="type-caption text-ink-48">Chưa xử lý</span>
          )}
        </div>
      </div>
    </li>
  );
}

export default function ProcessingPage() {
  const params = useParams<{ id: string }>();
  const shipmentId = Number(params.id);
  const validId = Number.isFinite(shipmentId) && shipmentId > 0;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [restarted, setRestarted] = useState(false);
  const navigatedRef = useRef(false);

  const progressQuery = useQuery({
    queryKey: ["progress", shipmentId],
    queryFn: () => api.getProgress(shipmentId),
    enabled: validId,
    refetchInterval: (query) => {
      const job = query.state.data?.job;
      if (!job) return POLL_MS;
      return job.status === "PENDING" || job.status === "RUNNING" ? POLL_MS : false;
    },
  });

  const shipmentQuery = useQuery({
    queryKey: ["shipment", shipmentId],
    queryFn: () => api.getShipment(shipmentId),
    enabled: validId,
    refetchInterval: (query) => {
      const job = progressQuery.data?.job;
      const running = !job || job.status === "PENDING" || job.status === "RUNNING";
      return running && query.state.data?.documents.length ? POLL_MS : false;
    },
  });

  const job = progressQuery.data?.job ?? null;
  const documents = shipmentQuery.data?.documents ?? [];
  const isRunning = job?.status === "PENDING" || job?.status === "RUNNING" || !job;
  const failed = job?.status === "FAILED";

  useEffect(() => {
    if (job?.status !== "DONE" || navigatedRef.current) return;
    navigatedRef.current = true;
    void queryClient.invalidateQueries({ queryKey: ["extractions", shipmentId] });
    void queryClient.invalidateQueries({ queryKey: ["shipment", shipmentId] });
    void queryClient.invalidateQueries({ queryKey: ["template-preview", shipmentId] });
    navigate(`/shipments/${shipmentId}/review`, { replace: true });
  }, [job?.status, shipmentId, navigate, queryClient]);

  async function handleRestart() {
    setRestarted(true);
    try {
      const created = await api.process(shipmentId, undefined, true);
      await queryClient.invalidateQueries({ queryKey: ["progress", shipmentId] });
      navigatedRef.current = false;
      setRestarted(false);
      return created;
    } catch {
      setRestarted(false);
      return null;
    }
  }

  if (!validId) {
    return (
      <Section tone="light">
        <Container width="content">
          <Callout tone="critical" title="Mã lô hàng không hợp lệ">
            Đường dẫn không chứa mã lô hàng hợp lệ.
          </Callout>
        </Container>
      </Section>
    );
  }

  if (progressQuery.isError) {
    return (
      <Section tone="light">
        <Container width="content">
          <Callout tone="critical" title="Không đọc được tiến độ xử lý">
            {progressQuery.error.message}
          </Callout>
        </Container>
      </Section>
    );
  }

  if (!progressQuery.isPending && !job) {
    return (
      <Section tone="light">
        <Container width="content">
          <EmptyState
            title="Lô hàng chưa xử lý lần nào"
            description="Tải lên chứng từ rồi bắt đầu xử lý để trích xuất dữ liệu."
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

  const progress = job?.progress ?? null;
  const total = progress?.document_total ?? documents.length;
  const done = progress?.document_done ?? 0;
  const percent = total > 0 ? Math.round((Math.min(done, total) / total) * 100) : 0;
  const stageSteps = progress ? [...progress.stages.map((s) => stageLabel(s)), stageLabel(progress.final_stage)] : [];
  const busy = documents.filter((doc) => docStage(progress, doc.id) !== null);
  const busyIds = new Set(busy.map((doc) => doc.id));
  const queued = documents.filter(
    (doc) =>
      !busyIds.has(doc.id) && doc.status !== "EXTRACTED" && doc.status !== "FAILED" && !doc.error,
  );
  const finalizing = progress?.current_stage === progress?.final_stage;
  const currentStageIndex = finalizing ? progress?.stages.length ?? 0 : 0;

  const header = (
    <PageHeader
      eyebrow="Đang xử lý"
      title={shipmentQuery.data?.code ?? `Lô #${shipmentId}`}
      subtitle="Hệ thống xử lý song song các chứng từ: đọc tệp, phân loại và trích xuất. Bạn sẽ được đưa sang trang xem lại khi hoàn tất."
      actions={job ? <StatusBadge status={job.status} /> : null}
    />
  );

  if (failed) {
    return (
      <Section tone="light">
        <Container width="content">
          {header}
          <Callout tone="critical" title="Xử lý chứng từ thất bại">
            <p className="mb-4">{job?.error ?? "Worker không hoàn tất được lô hàng này."}</p>
            <div className="flex flex-wrap gap-3">
              <Button variant="primary" onClick={() => void handleRestart()} disabled={restarted}>
                {restarted ? "Đang thử lại…" : "Thử lại"}
              </Button>
              <ButtonLink to={`/shipments/${shipmentId}/upload`} variant="secondary">
                Xem chứng từ
              </ButtonLink>
            </div>
          </Callout>
        </Container>
      </Section>
    );
  }

  return (
    <Section tone="light">
      <Container width="content">
        {header}

        <section className={`${CARD} mb-6 p-6`} role="status" aria-live="polite">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="type-tagline text-ink">
              {isRunning
                ? `Đã xử lý ${Math.min(done, total)}/${total} chứng từ`
                : "Đã trích xuất xong — đang chuyển sang trang xem lại…"}
            </h2>
            <span className="type-caption tabular-nums text-ink-48">{percent}%</span>
          </div>

          <div
            className="mt-3 h-1.5 w-full overflow-hidden rounded-pill bg-hairline"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={total}
            aria-valuenow={Math.min(done, total)}
            aria-label="Tiến độ xử lý chứng từ"
          >
            <div
              className="h-full rounded-pill bg-action transition-[width] duration-300"
              style={{ width: `${percent}%` }}
            />
          </div>

          <div className="mt-5 flex flex-col gap-3">
            <p className="type-caption text-ink-48">
              {job?.status === "PENDING"
                ? "Đang chờ worker nhận job…"
                : busy.length > 0
                  ? `Đang xử lý ${busy.length} chứng từ song song${
                      queued.length > 0 ? `, ${queued.length} chứng từ đang chờ đến lượt` : ""
                    } — mỗi tệp có tiến trình riêng.`
                  : finalizing
                    ? "Đang tổng hợp chứng từ, đối chiếu bên mua/bên bán…"
                    : "Đang chuẩn bị…"}
            </p>
            {finalizing && stageSteps.length > 0 ? (
              <Stepper steps={stageSteps} current={currentStageIndex} />
            ) : null}
          </div>
        </section>

        <h2 className="type-lead mb-4 text-ink">Chứng từ trong lô</h2>
        {shipmentQuery.isPending ? (
          <div className={`${CARD} flex items-center justify-center px-6 py-16`}>
            <Spinner />
          </div>
        ) : documents.length === 0 ? (
          <EmptyState title="Chưa có chứng từ" description="Lô hàng này không còn chứng từ nào để xử lý." />
        ) : (
          <ul className={`${CARD} list-none p-0`}>
            {documents.map((doc) => (
              <DocumentStageRow key={doc.id} doc={doc} progress={progress} running={isRunning} />
            ))}
          </ul>
        )}
      </Container>
    </Section>
  );
}
