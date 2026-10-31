import type {
  DocumentSummary,
  ExportSummary,
  ExtractionsResponse,
  FieldRow,
  PartyRow,
  ShipmentDetail,
  ShipmentSummary,
} from "./types";

const BASE = "/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: init?.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      message = body.detail || body.error || message;
    } catch {
      /* keep default */
    }
    throw new Error(message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  listShipments: (params: { q?: string; status?: string } = {}) => {
    const qs = new URLSearchParams();
    if (params.q) qs.set("q", params.q);
    if (params.status) qs.set("status", params.status);
    const suffix = qs.toString() ? `?${qs}` : "";
    return request<ShipmentSummary[]>(`/shipments${suffix}`);
  },
  getShipment: (id: number) => request<ShipmentDetail>(`/shipments/${id}`),
  createShipment: (body: { code: string; customer_name?: string }) =>
    request<{ id: number; code: string; status: string }>("/shipments", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  uploadDocuments: (shipmentId: number, files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    return request<{ documents: { filename: string; status: string; document_id?: number; error?: string }[] }>(
      `/shipments/${shipmentId}/documents`,
      { method: "POST", body: form },
    );
  },
  process: (shipmentId: number, documentIds?: number[]) =>
    request<{ job_id: number; status: string; reused?: boolean }>(`/shipments/${shipmentId}/process`, {
      method: "POST",
      body: JSON.stringify(documentIds ? { document_ids: documentIds } : {}),
    }),
  getJob: (id: number) =>
    request<{ id: number; status: string; error: string | null; attempts: number }>(`/jobs/${id}`),
  getExtractions: (shipmentId: number) => request<ExtractionsResponse>(`/shipments/${shipmentId}/extractions`),
  getParties: (shipmentId: number) => request<PartyRow[]>(`/shipments/${shipmentId}/parties`),
  patchField: (shipmentId: number, fieldId: number, value: string, reason?: string) =>
    request<FieldRow>(`/shipments/${shipmentId}/fields/${fieldId}`, {
      method: "PATCH",
      body: JSON.stringify({ value, reason }),
    }),
  confirmField: (shipmentId: number, fieldId: number) =>
    request<FieldRow>(`/shipments/${shipmentId}/fields/${fieldId}/confirm`, { method: "POST" }),
  fieldHistory: (shipmentId: number, fieldId: number) =>
    request<{ id: number; old_value: string | null; new_value: string | null; reason: string | null; created_at: string }[]>(
      `/shipments/${shipmentId}/fields/${fieldId}/history`,
    ),
  reclassify: (documentId: number, docType: string) =>
    request<{ id: number; doc_type: string; status: string }>(`/documents/${documentId}/reclassify`, {
      method: "POST",
      body: JSON.stringify({ doc_type: docType }),
    }),
  resolveDuplicate: (documentId: number, keepThis: boolean, note?: string) =>
    request<{ id: number; status: string }>(`/documents/${documentId}/resolve-duplicate`, {
      method: "POST",
      body: JSON.stringify({ keep_this: keepThis, note }),
    }),
  documentGrid: (documentId: number) =>
    request<{ sheets: { name: string; rows: unknown[][] }[] }>(`/documents/${documentId}/grid`),
  documentFileUrl: (documentId: number) => `${BASE}/documents/${documentId}/file`,
  listExports: (shipmentId: number) => request<ExportSummary[]>(`/shipments/${shipmentId}/exports`),
  createExport: (shipmentId: number, templateVersion = "uandi_import_v1") =>
    request<{ id: number; mode: string; filled: number; missing: string[]; warnings: string[]; filename: string }>(
      `/shipments/${shipmentId}/exports`,
      { method: "POST", body: JSON.stringify({ template_version: templateVersion }) },
    ),
  exportDownloadUrl: (exportId: number) => `${BASE}/exports/${exportId}/download`,
  exportReportUrl: (exportId: number) => `${BASE}/exports/${exportId}/report`,
  listTemplates: () =>
    request<{ version: string; name: string; sheets: string[]; file_code_prefix: string }[]>("/templates"),
};

export type { DocumentSummary };
