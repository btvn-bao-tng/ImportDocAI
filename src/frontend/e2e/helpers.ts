import { fileURLToPath } from "node:url";
import path from "node:path";

export const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");

/** Smallest real fixture the pipeline accepts (PDF with a text layer). */
export const SAMPLE_PDF = path.join(REPO_ROOT, "data", "sample", "input", "bill.pdf");
export const SAMPLE_XLSX = path.join(REPO_ROOT, "data", "sample", "input", "箱单发票.xls");

/** Unique per run so a reused scratch DB never trips the 409 duplicate-code guard. */
export function shipmentCode(prefix = "E2E"): string {
  return `${prefix}-${Date.now().toString(36).toUpperCase()}`;
}
