import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { VerifyPreviewResponse } from "../api/types";
import VerifyPage from "./VerifyPage";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: {
    verifyPreview: vi.fn(),
    createExport: vi.fn(),
    exportDownloadUrl: vi.fn((id: number) => `/api/v1/exports/${id}/download`),
    exportReportUrl: vi.fn((id: number) => `/api/v1/exports/${id}/report`),
    documentFileUrl: vi.fn((id: number) => `/api/v1/documents/${id}/file`),
    listExports: vi.fn(),
  },
}));

vi.mock("../components/DocumentViewer", () => ({
  DocumentSource: ({ doc }: { doc: { filename: string } }) => <div>preview:{doc.filename}</div>,
}));

const verifyPreview = vi.mocked(api.verifyPreview);
const createExport = vi.mocked(api.createExport);

function preview(overrides: Partial<VerifyPreviewResponse> = {}): VerifyPreviewResponse {
  const fields = [
    {
      field_key: "transport.gross_weight_kg",
      label_vi: "Trọng lượng cả bì (kg)",
      value: "26400",
      unit: "kg",
      page: 1,
      cell_ref: null,
      bbox: [0.1, 0.2, 0.3, 0.25],
      provenance_type: "TEXT_LAYER",
      review_status: "CONFIRMED",
      critical: true,
    },
    {
      field_key: "transport.waybill_no",
      label_vi: "Số vận đơn",
      value: "YMJAI245332642",
      unit: null,
      page: 1,
      cell_ref: null,
      bbox: null,
      provenance_type: "TEXT_LAYER",
      review_status: "UNREVIEWED",
      critical: true,
    },
  ];
  return {
    shipment: { id: 1, code: "LS2628", status: "NEEDS_REVIEW" },
    documents: [
      {
        id: 10,
        filename: "bill.pdf",
        doc_type: "TRANSPORT_DOCUMENT",
        status: "EXTRACTED",
        error: null,
        page_count: 1,
        has_text_layer: true,
        fields,
      },
    ],
    field_count: fields.length,
    critical_open: 1,
    rows: fields.map((field) => ({ ...field, filename: "bill.pdf", doc_type: "TRANSPORT_DOCUMENT", provenance: "Text Layer" })),
    ...overrides,
  };
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/shipments/1/verify"]}>
        <Routes>
          <Route path="/shipments/:id/verify" element={<VerifyPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("VerifyPage", () => {
  beforeEach(() => {
    cleanup();
    vi.clearAllMocks();
    verifyPreview.mockResolvedValue(preview());
    createExport.mockResolvedValue({
      id: 7,
      mode: "FINAL",
      filled: 2,
      missing: [],
      warnings: [],
      filename: "LS2628_tokhai_import_v1_FINAL_20260101_120000.xlsx",
    });
  });

  it("builds the final xlsx as soon as the page opens", async () => {
    renderPage();

    await screen.findByRole("heading", { name: "File Excel cuối cùng" });
    expect(createExport).toHaveBeenCalledWith(1, "tokhai_import_v1");
    expect(screen.getByRole("link", { name: "Tải file Excel" }).getAttribute("href")).toBe(
      "/api/v1/exports/7/download",
    );
    expect(screen.getByRole("link", { name: "Tải báo cáo JSON" }).getAttribute("href")).toBe(
      "/api/v1/exports/7/report",
    );
  });

  it("lists extracted values beside the source document and highlights on click", async () => {
    renderPage();

    await screen.findByText("Trọng lượng cả bì (kg)");
    expect(screen.getByText("26400")).not.toBeNull();
    expect(screen.getByText("preview:bill.pdf")).not.toBeNull();
    expect(screen.getByText("1 trường cần xem lại")).not.toBeNull();

    fireEvent.click(screen.getByText("Trọng lượng cả bì (kg)"));
    await screen.findByText(/Đang đánh dấu: Trọng lượng cả bì/);
  });

  it("switches the source panel when another document is picked", async () => {
    const base = preview();
    verifyPreview.mockResolvedValue({
      ...base,
      documents: [
        base.documents[0],
        { ...base.documents[0], id: 11, filename: "invoice.pdf", fields: [] },
      ],
    });
    renderPage();

    await screen.findByText("preview:bill.pdf");
    fireEvent.click(screen.getByTitle("invoice.pdf"));
    await screen.findByText("preview:invoice.pdf");
    await screen.findByText("Chưa có dữ liệu trích xuất");
  });

  it("shows the export error with a retry when generation fails", async () => {
    createExport.mockRejectedValueOnce(new Error("Lô hàng chưa có dữ liệu trích xuất"));
    renderPage();

    await screen.findByText("Không tạo được file Excel");
    expect(screen.getByText("Lô hàng chưa có dữ liệu trích xuất")).not.toBeNull();

    createExport.mockResolvedValueOnce({
      id: 8,
      mode: "FINAL",
      filled: 1,
      missing: [],
      warnings: [],
      filename: "retry.xlsx",
    });
    fireEvent.click(screen.getByRole("button", { name: "Thử lại" }));
    await screen.findByRole("heading", { name: "File Excel cuối cùng" });
  });
});
