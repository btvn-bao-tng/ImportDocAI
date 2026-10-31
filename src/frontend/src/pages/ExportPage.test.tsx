import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ExtractionsResponse } from "../api/types";
import ExportPage from "./ExportPage";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: {
    listTemplates: vi.fn(),
    listExports: vi.fn(),
    getExtractions: vi.fn(),
    createExport: vi.fn(),
    exportDownloadUrl: vi.fn((id: number) => `/api/v1/exports/${id}/download`),
    exportReportUrl: vi.fn((id: number) => `/api/v1/exports/${id}/report`),
  },
}));

const listTemplates = vi.mocked(api.listTemplates);
const listExports = vi.mocked(api.listExports);
const getExtractions = vi.mocked(api.getExtractions);
const createExport = vi.mocked(api.createExport);

const extractions: ExtractionsResponse = {
  shipment_id: 1,
  documents: [
    {
      document: {
        id: 10,
        filename: "packing-list.pdf",
        doc_type: "PACKING_LIST",
        doc_type_source: "EXTRACTED",
        classification_confidence: 0.98,
        status: "EXTRACTED",
        page_count: 2,
        has_text_layer: true,
        duplicate_of_id: null,
        version_note: null,
        error: null,
        created_at: "2026-10-30T08:00:00Z",
      },
      fields: [
        {
          id: 100,
          document_id: 10,
          line_item_id: null,
          field_key: "transport.gross_weight_kg",
          label_vi: "Trọng lượng cả bì",
          raw_value: "1.250",
          normalized_value: "1250",
          unit: "kg",
          page: 1,
          bbox: null,
          cell_ref: null,
          confidence: 0.95,
          provenance_type: "TEXT_LAYER",
          grounding_verified: true,
          review_status: "CONFIRMED",
          critical: true,
        },
        {
          id: 101,
          document_id: 10,
          line_item_id: null,
          field_key: "transport.volume_cbm",
          label_vi: "Thể tích",
          raw_value: null,
          normalized_value: null,
          unit: "CBM",
          page: null,
          bbox: null,
          cell_ref: null,
          confidence: null,
          provenance_type: "TEXT_LAYER",
          grounding_verified: false,
          review_status: "UNREVIEWED",
          critical: true,
        },
      ],
      line_items: [
        {
          id: 1,
          seq: 1,
          description: "Máy bơm nước",
          raw_description: "Máy bơm nước",
          spec: null,
          quantity: "2",
          unit: "PCE",
          unit_price: "100",
          amount: "200",
          fields: [],
        },
      ],
    },
  ],
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/shipments/1/export"]}>
        <Routes>
          <Route path="/shipments/:id/export" element={<ExportPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ExportPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listTemplates.mockResolvedValue([
      {
        version: "uandi_import_v1",
        name: "Biểu mẫu nhập khẩu U&I",
        sheets: ["PACKING LIST", "INVOICE", "HOP DONG"],
        file_code_prefix: "uandi",
      },
    ]);
    listExports.mockResolvedValue([]);
    getExtractions.mockResolvedValue(extractions);
    createExport.mockResolvedValue({
      id: 1,
      mode: "DRAFT",
      filled: 5,
      missing: ["transport.volume_cbm"],
      warnings: [],
      filename: "x.xlsx",
    });
  });

  it("renders the template version and shows missing fields after export", async () => {
    renderPage();

    expect((await screen.findByText("uandi_import_v1 — Biểu mẫu nhập khẩu U&I")).textContent).toContain(
      "uandi_import_v1",
    );
    expect(await screen.findByText("packing-list.pdf")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Tạo file Excel" }));

    await waitFor(() => expect(createExport).toHaveBeenCalledWith(1, "uandi_import_v1"));

    const missingTitle = await screen.findByText("Trường thiếu nguồn — không tự điền");
    const missingAlert = missingTitle.closest("[role='alert']");
    expect(missingAlert?.textContent ?? "").toContain("transport.volume_cbm");
    expect(screen.getAllByText("transport.volume_cbm").length).toBeGreaterThan(0);
    expect(screen.getByText("Bản nháp (DRAFT)")).toBeTruthy();

    expect(screen.getByRole("link", { name: "Tải file Excel" }).getAttribute("href")).toBe(
      "/api/v1/exports/1/download",
    );
    expect(screen.getByRole("link", { name: "Xem báo cáo đối soát" }).getAttribute("href")).toBe(
      "/api/v1/exports/1/report",
    );
  });
});
