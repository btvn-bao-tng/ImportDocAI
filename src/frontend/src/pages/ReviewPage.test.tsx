import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ExtractionsResponse, ShipmentDetail } from "../api/types";
import ReviewPage from "./ReviewPage";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: {
    getExtractions: vi.fn(),
    getShipment: vi.fn(),
    listTemplates: vi.fn(),
    templatePreview: vi.fn(),
    templateFileUrl: vi.fn((version: string) => `/api/v1/templates/${version}/file`),
    documentGrid: vi.fn(),
    documentFileUrl: vi.fn((id: number) => `/api/v1/documents/${id}/file`),
  },
}));

vi.mock("pdfjs-dist", () => ({ getDocument: vi.fn(), GlobalWorkerOptions: {} }));
vi.mock("pdfjs-dist/build/pdf.worker.min.mjs?url", () => ({ default: "worker.js" }));

const getExtractions = vi.mocked(api.getExtractions);
const getShipment = vi.mocked(api.getShipment);
const listTemplates = vi.mocked(api.listTemplates);
const templatePreview = vi.mocked(api.templatePreview);
const documentGrid = vi.mocked(api.documentGrid);

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
        page_count: 1,
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
          normalized_value: null,
          unit: "kg",
          page: 1,
          bbox: null,
          cell_ref: null,
          confidence: 0.95,
          provenance_type: "TEXT_LAYER",
          grounding_verified: true,
          review_status: "UNREVIEWED",
          critical: true,
        },
      ],
      line_items: [],
    },
    {
      document: {
        id: 11,
        filename: "to-khai.xlsx",
        doc_type: "CUSTOMS_DECLARATION",
        doc_type_source: "USER",
        classification_confidence: null,
        status: "EXTRACTED",
        page_count: 1,
        has_text_layer: true,
        duplicate_of_id: null,
        version_note: null,
        error: null,
        created_at: "2026-10-30T08:01:00Z",
      },
      fields: [],
      line_items: [],
    },
  ],
};

const shipment: ShipmentDetail = {
  id: 1,
  code: "UI-TEST-2026",
  customer_name: "GiaBao",
  status: "PROCESSING",
  created_at: "2026-10-30T08:00:00Z",
  document_count: 2,
  extracted_count: 2,
  failed_count: 0,
  critical_unreviewed: 1,
  documents: extractions.documents.map((d) => d.document),
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/shipments/1/review"]}>
        <Routes>
          <Route path="/shipments/:id/review" element={<ReviewPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ReviewPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getExtractions.mockResolvedValue(extractions);
    getShipment.mockResolvedValue(shipment);
    listTemplates.mockResolvedValue([
      {
        version: "tokhai_import_v1",
        name: "Tờ khai hải quan nhập khẩu (TKN / HANG)",
        sheets: ["TKN", "HANG"],
        file_code_prefix: "TOKHAI",
      },
      {
        version: "uandi_import_v1",
        name: "U&I Import Template v1",
        sheets: ["PACKING LIST"],
        file_code_prefix: "UANDI",
      },
    ]);
    templatePreview.mockResolvedValue({
      version: "tokhai_import_v1",
      name: "Tờ khai hải quan nhập khẩu (TKN / HANG)",
      has_data: true,
      cells: {
        TKN: {
          C1: {
            field: "declaration.declaration_no",
            value: "108299395961",
            evidence: {
              document_id: 10,
              filename: "packing-list.pdf",
              page: 1,
              bbox: [0.1, 0.1, 0.4, 0.2],
              cell_ref: "TKN!C1",
              provenance_type: "TEXT_LAYER",
              review_status: "CONFIRMED",
              critical: true,
              label_vi: "Số tờ khai",
            },
          },
        },
      },
      sheets: [
        {
          name: "TKN",
          rows: [
            [null, null, "Tờ khai hàng hóa nhập khẩu"],
            ["Số tờ khai", 108299395961],
          ],
          merges: ["A1:C1"],
        },
        {
          name: "HANG",
          rows: [["Mã hàng"], ["85176210"]],
          merges: [],
        },
      ],
    });
    documentGrid.mockResolvedValue({ sheets: [{ name: "Sheet1", rows: [["A1"]], merges: [] }] });
  });

  it("shows the source documents on the left and the extracted values on the right", async () => {
    renderPage();

    expect(await screen.findByText("UI-TEST-2026")).toBeTruthy();
    expect(screen.getByRole("group", { name: "Chọn chứng từ" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "packing-list.pdf" })).toBeTruthy();

    const valuePanel = await screen.findByRole("region", { name: "Giá trị trích xuất" });
    expect(valuePanel).toBeTruthy();
    expect(await within(valuePanel).findByText("Trọng lượng cả bì")).toBeTruthy();
    expect(within(valuePanel).getByText("transport.gross_weight_kg")).toBeTruthy();
    expect(within(valuePanel).getByText("1.250")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Mẫu tờ khai cuối" })).toBeNull();

    expect(await screen.findAllByText("1 cần xem lại")).toBeTruthy();
  });

  it("marks the source document when an extracted value is clicked", async () => {
    renderPage();

    const valuePanel = await screen.findByRole("region", { name: "Giá trị trích xuất" });
    fireEvent.click(await within(valuePanel).findByText("transport.gross_weight_kg"));

    expect(await screen.findByText(/Đang đánh dấu: Trọng lượng cả bì/)).toBeTruthy();
  });

  it("marks the evidence from the keyboard as well", async () => {
    renderPage();

    const valuePanel = await screen.findByRole("region", { name: "Giá trị trích xuất" });
    const row = await within(valuePanel).findByRole("row", { name: /Trọng lượng cả bì/ });
    expect(row.getAttribute("tabindex")).toBe("0");
    row.focus();
    fireEvent.keyDown(row, { key: "Enter" });

    expect(await screen.findByText(/Đang đánh dấu: Trọng lượng cả bì/)).toBeTruthy();
  });

  it("switches to the final template and back to the extracted values", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Mẫu tờ khai cuối" }));

    const templatePanel = (await screen.findByRole("heading", { name: "Mẫu tờ khai cuối" })).closest("section");
    expect(templatePanel).toBeTruthy();
    expect(await within(templatePanel as HTMLElement).findByText("Số tờ khai")).toBeTruthy();
    expect(await within(templatePanel as HTMLElement).findByText("108299395961")).toBeTruthy();
    expect(within(templatePanel as HTMLElement).getByRole("link", { name: "Mở tệp mẫu" }).getAttribute("href")).toBe(
      "/api/v1/templates/tokhai_import_v1/file",
    );
    expect(templatePreview).toHaveBeenCalledWith(1, "tokhai_import_v1");

    fireEvent.click(within(templatePanel as HTMLElement).getByRole("button", { name: "← Giá trị trích xuất" }));
    expect(await screen.findByRole("region", { name: "Giá trị trích xuất" })).toBeTruthy();
  });

  it("switches the template sheet when a tab is chosen", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Mẫu tờ khai cuối" }));

    const tabs = await screen.findByRole("tablist", { name: "Chọn sheet mẫu" });
    const hangTab = within(tabs).getByRole("tab", { name: "HANG" });
    fireEvent.click(hangTab);

    expect(await screen.findByText("85176210")).toBeTruthy();
    expect(hangTab.getAttribute("aria-selected")).toBe("true");
  });

  it("exposes an adjustable split separator with keyboard controls", async () => {
    renderPage();

    const separator = await screen.findByRole("separator", { name: "Điều chỉnh độ rộng khung" });
    expect(separator.getAttribute("aria-valuenow")).toBe("50");

    fireEvent.keyDown(separator, { key: "End" });
    await waitFor(() => expect(separator.getAttribute("aria-valuenow")).toBe("85"));

    fireEvent.keyDown(separator, { key: "Home" });
    await waitFor(() => expect(separator.getAttribute("aria-valuenow")).toBe("15"));

    fireEvent.keyDown(separator, { key: "ArrowRight" });
    await waitFor(() => expect(separator.getAttribute("aria-valuenow")).toBe("17"));
  });

  it("shows the spreadsheet viewer for an Excel source document", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "to-khai.xlsx" }));

    expect(await screen.findByText("A1")).toBeTruthy();
    expect(documentGrid).toHaveBeenCalledWith(11);
  });
});

describe("ReviewPage — template values and evidence", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getExtractions.mockResolvedValue(extractions);
    getShipment.mockResolvedValue(shipment);
    listTemplates.mockResolvedValue([
      {
        version: "tokhai_import_v1",
        name: "Tờ khai hải quan nhập khẩu (TKN / HANG)",
        sheets: ["TKN", "HANG"],
        file_code_prefix: "TOKHAI",
      },
    ]);
    documentGrid.mockResolvedValue({
      sheets: [
        {
          name: "TKN",
          rows: [
            [null, null, null, null],
            [null, null, null, null],
            [null, null, null, null],
            [null, null, null, null, "108299395961"],
          ],
          merges: [],
        },
      ],
    });
    templatePreview.mockResolvedValue({
      version: "tokhai_import_v1",
      name: "Tờ khai hải quan nhập khẩu",
      has_data: true,
      sheets: [{ name: "TKN", rows: [[null, "Số tờ khai"]], merges: [] }],
      cells: {
        TKN: {
          A1: {
            field: "declaration.declaration_no",
            value: "108299395961",
            evidence: {
              document_id: 11,
              filename: "to-khai.xlsx",
              page: null,
              bbox: null,
              cell_ref: "TKN!E4",
              provenance_type: "CELL",
              review_status: "CONFIRMED",
              critical: true,
              label_vi: "Số tờ khai",
            },
          },
          B1: {
            field: "declaration.registered_at",
            value: "02/06/2026",
            evidence: null,
          },
        },
      },
    });
  });

  it("highlights the evidence cell in the left panel when a filled value is clicked", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Mẫu tờ khai cuối" }));
    const value = await screen.findByRole("button", { name: /108299395961/ });
    fireEvent.click(value);

    // The source document switches to the evidence's document (the Excel one).
    expect(await screen.findByText("Đang đánh dấu: Số tờ khai · TKN!E4")).toBeTruthy();
    expect(await screen.findByTestId("excel-highlight")).toBeTruthy();
    expect(documentGrid).toHaveBeenCalledWith(11);
  });

  it("shows a notice when a value has no stored evidence", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Mẫu tờ khai cuối" }));
    const value = await screen.findByRole("button", { name: /02\/06\/2026/ });
    fireEvent.click(value);

    expect(await screen.findByText(/Không có chứng cứ để đánh dấu/)).toBeTruthy();
    expect(screen.queryByTestId("excel-highlight")).toBeNull();
  });
});

describe("ReviewPage — merged cells and drag", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getExtractions.mockResolvedValue(extractions);
    getShipment.mockResolvedValue(shipment);
    listTemplates.mockResolvedValue([
      { version: "tokhai_import_v1", name: "Tờ khai", sheets: ["TKN"], file_code_prefix: "TOKHAI" },
    ]);
    documentGrid.mockResolvedValue({ sheets: [{ name: "Sheet1", rows: [["A1"]], merges: [] }] });
    templatePreview.mockResolvedValue({
      version: "tokhai_import_v1",
      name: "Tờ khai",
      has_data: true,
      sheets: [
        {
          name: "TKN",
          // A1:B5 merges past the last row: the span must clamp to the rows that exist.
          rows: [
            ["Tờ khai", null],
            ["Số tờ khai", "108299395961"],
          ],
          merges: ["A1:B5"],
        },
      ],
      cells: {
        TKN: {
          B2: {
            field: "declaration.declaration_no",
            value: "108299395961",
            evidence: null,
          },
        },
      },
    });
  });

  it("clamps merged spans to the rendered rows and keeps the anchor value", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Mẫu tờ khai cuối" }));
    const anchor = await screen.findByRole("cell", { name: "Tờ khai" });
    expect(anchor.getAttribute("rowspan")).toBe("2");
    expect(anchor.getAttribute("colspan")).toBe("2");
    // B1 is covered by the merge and must not be rendered as its own cell.
    expect(screen.queryByRole("cell", { name: "" })).toBeNull();
  });

  it("resizes with pointer drag and clamps to the min/max ratio", async () => {
    renderPage();

    const separator = await screen.findByRole("separator", { name: "Điều chỉnh độ rộng khung" });
    const container = separator.parentElement as HTMLElement;
    Object.defineProperty(container, "getBoundingClientRect", {
      value: () => ({ left: 0, width: 1000, top: 0, height: 600, right: 1000, bottom: 600, x: 0, y: 0, toJSON: () => ({}) }),
    });

    fireEvent.pointerDown(separator, { pointerId: 1, clientX: 420 });
    fireEvent.pointerMove(separator, { pointerId: 1, clientX: 600 });
    expect(separator.getAttribute("aria-valuenow")).toBe("60");

    fireEvent.pointerMove(separator, { pointerId: 1, clientX: 990 });
    expect(separator.getAttribute("aria-valuenow")).toBe("85");

    fireEvent.pointerMove(separator, { pointerId: 1, clientX: 10 });
    expect(separator.getAttribute("aria-valuenow")).toBe("15");

    fireEvent.pointerUp(separator, { pointerId: 1 });
    fireEvent.pointerMove(separator, { pointerId: 1, clientX: 500 });
    expect(separator.getAttribute("aria-valuenow")).toBe("15");
  });
});
