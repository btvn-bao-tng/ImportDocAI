export type ShipmentStatus = "DRAFT" | "PROCESSING" | "NEEDS_REVIEW" | "READY_TO_EXPORT" | "EXPORTED";

export interface ShipmentSummary {
  id: number;
  code: string;
  customer_name: string | null;
  status: ShipmentStatus;
  created_at: string;
  document_count: number;
  extracted_count: number;
  failed_count: number;
  critical_unreviewed: number;
}

export interface DocumentSummary {
  id: number;
  filename: string;
  doc_type: string | null;
  doc_type_source: string | null;
  classification_confidence: number | null;
  status: string;
  page_count: number;
  has_text_layer: boolean;
  duplicate_of_id: number | null;
  version_note: string | null;
  error: string | null;
  created_at: string;
}

export interface ShipmentDetail extends ShipmentSummary {
  documents: DocumentSummary[];
}

export type ProcessingStage =
  | "PARSING"
  | "CLASSIFYING"
  | "EXTRACTING"
  | "PERSISTING"
  | "FINALIZING"
  | null;

export interface JobProgress {
  document_total: number;
  document_done: number;
  current_document_id: number | null;
  current_stage: ProcessingStage;
  stages: string[];
  final_stage: string;
}

export interface JobStatus {
  id: number;
  status: string;
  error: string | null;
  attempts: number;
  progress: JobProgress;
}

export interface ShipmentProgress {
  shipment_status: ShipmentStatus;
  job: JobStatus | null;
}

export interface VerifyRow {
  field_key: string;
  label_vi: string;
  value: string | null;
  unit: string | null;
  page: number | null;
  cell_ref: string | null;
  bbox: number[] | null;
  provenance_type: string;
  review_status: string;
  critical: boolean;
}

export interface VerifyDocument {
  id: number;
  filename: string;
  doc_type: string | null;
  status: string;
  error: string | null;
  page_count: number;
  has_text_layer: boolean;
  fields: VerifyRow[];
}

export interface VerifyPreviewResponse {
  shipment: { id: number; code: string; status: ShipmentStatus };
  documents: VerifyDocument[];
  field_count: number;
  critical_open: number;
  rows: (VerifyRow & { filename: string; doc_type: string | null; provenance: string })[];
}

export interface FieldRow {
  id: number;
  document_id: number;
  line_item_id: number | null;
  field_key: string;
  label_vi: string;
  raw_value: string | null;
  normalized_value: string | null;
  unit: string | null;
  page: number | null;
  bbox: number[] | null;
  cell_ref: string | null;
  confidence: number | null;
  provenance_type: "TEXT_LAYER" | "IMAGE" | "CELL";
  grounding_verified: boolean;
  review_status: "UNREVIEWED" | "CONFIRMED" | "EDITED" | "NEEDS_REVIEW";
  critical: boolean;
}

export interface LineItemRow {
  id: number;
  seq: number;
  description: string | null;
  raw_description: string | null;
  spec: string | null;
  quantity: string | null;
  unit: string | null;
  unit_price: string | null;
  amount: string | null;
  fields: FieldRow[];
}

export interface DocumentExtraction {
  document: DocumentSummary;
  fields: FieldRow[];
  line_items: LineItemRow[];
}

export interface ExtractionsResponse {
  shipment_id: number;
  documents: DocumentExtraction[];
}

export interface PartyRow {
  id: number;
  raw_name: string;
  canonical_name: string | null;
  roles: string[];
  documents: { document_id: number; filename: string | null; role: string }[];
}

export interface GridSheet {
  name: string;
  rows: unknown[][];
  merges?: string[];
}

export interface GridResponse {
  sheets: GridSheet[];
}

export interface TemplateEvidence {
  document_id: number;
  filename: string;
  page: number | null;
  bbox: number[] | null;
  cell_ref: string | null;
  provenance_type: "TEXT_LAYER" | "IMAGE" | "CELL";
  review_status: string;
  critical: boolean;
  label_vi: string;
}

export interface TemplateCell {
  field: string;
  value: string;
  evidence: TemplateEvidence | null;
}

export interface TemplatePreviewResponse {
  version: string;
  name: string;
  has_data: boolean;
  sheets: GridSheet[];
  cells: Record<string, Record<string, TemplateCell>>;
}

export interface ExportSummary {
  id: number;
  mode: "DRAFT" | "FINAL";
  template_version: string;
  status: string;
  created_at: string;
  filename: string | null;
  warnings: string[];
}
