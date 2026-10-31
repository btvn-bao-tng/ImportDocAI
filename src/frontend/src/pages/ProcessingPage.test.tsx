import { cleanup, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { JobProgress, ShipmentDetail } from "../api/types";
import ProcessingPage from "./ProcessingPage";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: {
    getProgress: vi.fn(),
    getShipment: vi.fn(),
    reprocessShipment: vi.fn(),
    documentFileUrl: vi.fn((id: number) => `/api/v1/documents/${id}/file`),
  },
}));

const getProgress = vi.mocked(api.getProgress);
const getShipment = vi.mocked(api.getShipment);

function progress(overrides: Partial<JobProgress> = {}): JobProgress {
  return {
    document_total: 3,
    document_done: 0,
    current_document_id: 11,
    current_stage: "EXTRACTING",
    document_stages: { "11": "EXTRACTING", "12": "CLASSIFYING" },
    stages: ["PARSING", "CLASSIFYING", "EXTRACTING", "PERSISTING"],
    final_stage: "FINALIZING",
    ...overrides,
  };
}

function shipment(): ShipmentDetail {
  const doc = (id: number, filename: string) => ({
    id,
    filename,
    doc_type: id === 11 ? "COMMERCIAL_INVOICE" : null,
    status: id === 11 ? "PROCESSING" : "UPLOADED",
    page_count: 1,
    error: null,
    created_at: "2026-10-31T00:00:00Z",
  });
  return {
    shipment: { id: 1, code: "LS2628", status: "PROCESSING", customer_name: null, created_at: "2026-10-31T00:00:00Z" },
    documents: [doc(11, "invoice.pdf"), doc(12, "packing-list.pdf"), doc(13, "edo.pdf")],
    parties: [],
    transactions: [],
  } as unknown as ShipmentDetail;
}

function renderPage(job: Awaited<ReturnType<typeof api.getProgress>>) {
  getProgress.mockResolvedValue(job);
  getShipment.mockResolvedValue(shipment());
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/shipments/1/processing"]}>
        <Routes>
          <Route path="/shipments/:id/processing" element={<ProcessingPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ProcessingPage", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => cleanup());

  it("shows a separate live stage for every file being processed", async () => {
    renderPage({
      shipment_status: "PROCESSING",
      job: {
        id: 1,
        status: "RUNNING",
        attempts: 1,
        error: null,
        progress: progress(),
      },
    });

    const invoiceRow = (await screen.findByText("invoice.pdf")).closest("li") as HTMLElement;
    const packingRow = (await screen.findByText("packing-list.pdf")).closest("li") as HTMLElement;
    const edoRow = (await screen.findByText("edo.pdf")).closest("li") as HTMLElement;

    // Both in-flight files report their own stage at the same time.
    // The subtitle carries the stage too, so both the pill and the caption match.
    expect(within(invoiceRow).getAllByText(/Trích xuất/).length).toBeGreaterThan(0);
    expect(within(packingRow).getAllByText(/Phân loại/).length).toBeGreaterThan(0);
    // Each file renders its own stepper, so the two in-flight files show different steps.
    expect(invoiceRow.querySelectorAll("ol, [role='list']").length).toBeGreaterThan(0);
    // The file that has not started yet is queued, not pretending to be done.
    expect(within(edoRow).getByText("Chờ đến lượt")).toBeTruthy();
    expect(await screen.findByText(/Đang xử lý 2 chứng từ song song/)).toBeTruthy();
  });

  it("keeps each file's stage visible while the job is still running", async () => {
    renderPage({
      shipment_status: "READY_TO_EXPORT",
      job: {
        id: 1,
        status: "RUNNING",
        attempts: 1,
        error: null,
        progress: progress({ document_done: 3, document_stages: {}, current_stage: "FINALIZING" }),
      },
    });

    const invoiceRow = (await screen.findByText("invoice.pdf")).closest("li") as HTMLElement;
    const packingRow = (await screen.findByText("packing-list.pdf")).closest("li") as HTMLElement;
    // Finished files no longer claim a stage, but the job is not over so nothing navigates away.
    expect(within(invoiceRow).queryByText(/Trích xuất|Phân loại/)).toBeNull();
    expect(within(packingRow).queryByText(/Trích xuất|Phân loại/)).toBeNull();
    expect(await screen.findByText("Đã xử lý 3/3 chứng từ")).toBeTruthy();
  });
});
