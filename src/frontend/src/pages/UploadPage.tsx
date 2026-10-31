import { useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { DocumentSummary } from "../api/types";
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
} from "../components/ui";

const DOC_TYPES = [
  { value: "COMMERCIAL_INVOICE", label: "Hóa đơn thương mại" },
  { value: "PACKING_LIST", label: "Phiếu đóng gói" },
  { value: "TRANSPORT_DOCUMENT", label: "Chứng từ vận tải" },
  { value: "ARRIVAL_NOTICE", label: "Giấy báo hàng đến" },
  { value: "SALES_CONTRACT_PO", label: "Hợp đồng mua bán / PO" },
  { value: "SERVICE_CONTRACT", label: "Hợp đồng dịch vụ" },
  { value: "EXPORT_DECLARATION", label: "Tờ khai xuất khẩu" },
  { value: "CERTIFICATE_OF_ORIGIN", label: "Giấy chứng nhận xuất xứ" },
  { value: "INSURANCE_POLICY", label: "Chứng từ bảo hiểm" },
  { value: "PHYTOSANITARY_CERTIFICATE", label: "Giấy chứng nhận kiểm dịch thực vật" },
  { value: "OTHER", label: "Khác" },
] as const;

const ACCEPT = ".pdf,.xls,.xlsx,.xlsm,.jpg,.jpeg,.png";
const ALLOWED_TYPES = new Set(["pdf", "xls", "xlsx", "xlsm", "jpg", "jpeg", "png"]);

type UploadResult = { filename: string; status: string; document_id?: number; error?: string };

const CARD = "rounded-lg border border-hairline bg-canvas";

const UPLOAD_STATUS_LABEL: Record<string, string> = {
  UPLOADED: "Đã tải lên",
  DUPLICATE: "Trùng lặp",
  REJECTED: "Bị từ chối",
  FAILED: "Thất bại",
};

function uploadTone(status: string): "green" | "amber" | "red" {
  if (status === "UPLOADED") return "green";
  if (status === "DUPLICATE") return "amber";
  return "red";
}

function isFileAllowed(file: File): boolean {
  const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
  return ALLOWED_TYPES.has(ext);
}

function UploadResults({ results }: { results: UploadResult[] }) {
  if (results.length === 0) return null;
  return (
    <ul className={`${CARD} mt-4 list-none p-0`}>
      {results.map((result, index) => (
        <li
          key={`${result.filename}-${index}`}
          className={`flex items-start justify-between gap-4 px-6 py-4 ${index > 0 ? "border-t border-divider" : ""}`}
        >
          <div className="min-w-0">
            <p className="type-body truncate text-ink">{result.filename}</p>
            {result.error && <p className="type-caption mt-1 text-critical">{result.error}</p>}
          </div>
          <div className="shrink-0">
            <LocalizedStatus tone={uploadTone(result.status)}>
              {UPLOAD_STATUS_LABEL[result.status] ?? result.status}
            </LocalizedStatus>
          </div>
        </li>
      ))}
    </ul>
  );
}

function DocumentRow({ shipmentId, doc }: { shipmentId: number; doc: DocumentSummary }) {
  const queryClient = useQueryClient();

  const reclassify = useMutation({
    mutationFn: (docType: string) => api.reclassify(doc.id, docType),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["shipment", shipmentId] }),
  });

  const resolve = useMutation({
    mutationFn: (keepThis: boolean) => api.resolveDuplicate(doc.id, keepThis),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["shipment", shipmentId] }),
  });

  const isDuplicate = doc.status === "IS_DUPLICATE_OF";
  const selectValue = doc.doc_type && DOC_TYPES.some((t) => t.value === doc.doc_type) ? doc.doc_type : "";

  return (
    <div className="px-6 py-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <p className="type-body-strong truncate text-ink">{doc.filename}</p>
          <p className="type-caption mt-1 text-ink-48">
            {doc.page_count} trang · {doc.has_text_layer ? "bản văn bản" : "bản quét"}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <select
            value={selectValue}
            onChange={(e) => {
              const next = e.target.value;
              if (next && next !== doc.doc_type) reclassify.mutate(next);
            }}
            disabled={reclassify.isPending}
            aria-label={`Loại chứng từ cho ${doc.filename}`}
            className="h-11 rounded-pill border border-hairline bg-canvas px-4 type-caption text-ink transition-colors duration-150 focus:border-action focus:outline-none disabled:opacity-40"
          >
            <option value="" disabled>
              Chọn loại chứng từ
            </option>
            {DOC_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
          <StatusBadge status={doc.status} />
        </div>
      </div>

      {reclassify.isError && <p className="type-caption mt-2 text-critical">{reclassify.error.message}</p>}
      {resolve.isError && <p className="type-caption mt-2 text-critical">{resolve.error.message}</p>}

      {doc.error && (
        <div className="mt-3">
          <Callout tone="critical">{doc.error}</Callout>
        </div>
      )}

      {isDuplicate && (
        <div className="mt-4">
          <Callout tone="warning" title="Có thể là phiên bản của cùng chứng từ">
            {doc.version_note && <p className="mb-3">{doc.version_note}</p>}
            <div className="flex flex-wrap gap-3">
              <Button
                variant="primary"
                size="sm"
                disabled={resolve.isPending}
                onClick={() => resolve.mutate(true)}
              >
                Giữ bản này
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={resolve.isPending}
                onClick={() => resolve.mutate(false)}
              >
                Giữ bản kia
              </Button>
            </div>
          </Callout>
        </div>
      )}
    </div>
  );
}

export default function UploadPage() {
  const params = useParams<{ id: string }>();
  const shipmentId = Number(params.id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [uploadResults, setUploadResults] = useState<UploadResult[]>([]);
  const [localError, setLocalError] = useState<string | null>(null);

  const shipmentQuery = useQuery({
    queryKey: ["shipment", shipmentId],
    queryFn: () => api.getShipment(shipmentId),
    enabled: Number.isFinite(shipmentId),
  });

  const uploadMutation = useMutation({
    mutationFn: (files: File[]) => api.uploadDocuments(shipmentId, files),
    onSuccess: async (response) => {
      setUploadResults(response.documents);
      await queryClient.invalidateQueries({ queryKey: ["shipment", shipmentId] });
    },
  });

  const shipment = shipmentQuery.data;
  const hasProcessed = shipment?.documents.some((doc) => doc.status === "EXTRACTED") ?? false;

  const processMutation = useMutation({
    // Once anything is extracted, the run is a re-process: without force the
    // backend skips every EXTRACTED document and the job finishes as a no-op.
    mutationFn: () => api.process(shipmentId, undefined, hasProcessed),
    onSuccess: () => {
      // The progress page owns the run: it polls the job, shows each stage and
      // hands off to the review page when the extraction is done.
      navigate(`/shipments/${shipmentId}/processing`);
    },
  });

  function handleFiles(fileList: FileList | File[] | null) {
    if (!fileList) return;
    const files = Array.from(fileList);
    if (files.length === 0) return;

    setLocalError(null);
    const allowed = files.filter(isFileAllowed);
    const rejected = files.length - allowed.length;
    if (rejected > 0) {
      setLocalError(`${rejected} tệp không đúng định dạng và đã bị bỏ qua.`);
    }
    if (allowed.length > 0) uploadMutation.mutate(allowed);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragging(false);
    handleFiles(event.dataTransfer?.files ?? null);
  }

  function handleChange(event: ChangeEvent<HTMLInputElement>) {
    handleFiles(event.target.files);
    event.target.value = "";
  }

  const isProcessing = processMutation.isPending;

  if (shipmentQuery.isPending) {
    return (
      <Section tone="light">
        <Container width="content">
          <div className={`${CARD} flex items-center justify-center px-6 py-20`}>
            <Spinner />
          </div>
        </Container>
      </Section>
    );
  }

  if (shipmentQuery.isError || !shipment) {
    return (
      <Section tone="light">
        <Container width="content">
          <Callout tone="critical" title="Không tải được lô hàng">
            {shipmentQuery.error?.message ?? "Lô hàng không tồn tại."}
          </Callout>
        </Container>
      </Section>
    );
  }

  return (
    <Section tone="light">
      <Container width="content">
        <PageHeader
          eyebrow="Tải lên chứng từ"
          title={shipment.code}
          subtitle={shipment.customer_name ?? undefined}
        />

        <div
          onDragOver={(e) => {
            e.preventDefault();
            setIsDragging(true);
          }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={handleDrop}
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              inputRef.current?.click();
            }
          }}
          role="button"
          tabIndex={0}
          aria-label="Kéo thả hoặc chọn tệp chứng từ"
          className={
            `${CARD} mb-4 flex cursor-pointer flex-col items-center justify-center px-8 py-16 text-center ` +
            "transition-colors duration-150 " +
            (isDragging ? "border-action" : "")
          }
        >
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={ACCEPT}
            onChange={handleChange}
            className="hidden"
          />
          <p className="type-tagline text-ink">Kéo thả chứng từ vào đây</p>
          <p className="type-body mt-2 text-ink-48">hoặc nhấn để chọn tệp</p>
          <p className="type-caption mt-4 text-ink-48">PDF, XLS, XLSX, XLSM, JPG, JPEG, PNG</p>
          {uploadMutation.isPending && (
            <div className="mt-5">
              <Spinner label="Đang tải lên…" />
            </div>
          )}
        </div>

        {localError && (
          <div className="mb-4">
            <Callout tone="warning">{localError}</Callout>
          </div>
        )}
        {uploadMutation.isError && (
          <div className="mb-4">
            <Callout tone="critical" title="Tải lên thất bại">
              {uploadMutation.error.message}
            </Callout>
          </div>
        )}
        <UploadResults results={uploadResults} />

        <div className="mt-12 mb-4 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h2 className="type-tagline text-ink">Chứng từ của lô hàng</h2>
            <p className="type-caption mt-1 text-ink-48">{shipment.documents.length} chứng từ</p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            {hasProcessed ? (
              <ButtonLink to={`/shipments/${shipmentId}/review`} variant="secondary">
                Xem lại kết quả
              </ButtonLink>
            ) : null}
            <Button
              variant="primary"
              onClick={() => processMutation.mutate()}
              disabled={isProcessing || shipment.documents.length === 0}
            >
              {isProcessing ? "Đang bắt đầu…" : hasProcessed ? "Xử lý lại" : "Xử lý chứng từ"}
            </Button>
          </div>
        </div>

        {processMutation.isError && (
          <div className="mb-4">
            <Callout tone="critical" title="Không xử lý được chứng từ">
              {processMutation.error.message}
            </Callout>
          </div>
        )}

        {shipment.documents.length === 0 ? (
          <EmptyState
            title="Chưa có chứng từ"
            description="Tải lên ít nhất một chứng từ để bắt đầu trích xuất."
          />
        ) : (
          <ul className={`${CARD} list-none p-0`}>
            {shipment.documents.map((doc, index) => (
              <li key={doc.id} className={index > 0 ? "border-t border-divider" : ""}>
                <DocumentRow shipmentId={shipmentId} doc={doc} />
              </li>
            ))}
          </ul>
        )}
      </Container>
    </Section>
  );
}
