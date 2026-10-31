import { existsSync } from "node:fs";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { SAMPLE_PDF, SAMPLE_XLSX, shipmentCode } from "./helpers";

const API = "/api/v1";

const DECLARATION_HEADER = {
  "declaration.declaration_no": "108299395961",
  "declaration.registered_at": "02/06/2026 11:00:52",
  "declaration.importer_name": "CôNG TY TNHH Gỗ KINGSMAN VIệT NAM",
  "declaration.exporter_name": "BERTEC ENTERPRISE CO., LTD.",
  "declaration.bill_of_lading_no": "140426HDMUBUEA32635400",
  "declaration.invoice_no": "A - CD2025121801-2",
  "declaration.invoice_total": "23.230",
  "declaration.package_count": "28",
  "declaration.gross_weight": "52.312",
  "declaration.tax_exchange_rate": "26.143",
};

const DECLARATION_GOODS = [
  {
    "goods.hs_code": "44071110",
    "goods.description": "Gỗ thông xẻ (Pinus Taeda)",
    "goods.quantity_1": "46.46",
    "goods.unit_price": "250",
    "goods.invoice_amount": "11.615",
    "goods.customs_value": "303.650.945",
    "goods.origin_country_code": "AR",
  },
];

async function createShipment(request: APIRequestContext, code = shipmentCode()): Promise<number> {
  const res = await request.post(`${API}/shipments`, { data: { code, customer_name: "E2E KH" } });
  expect(res.ok(), await res.text()).toBeTruthy();
  return (await res.json()).id;
}

/** Freezes the test clock-free UI: waits until the shipment has documents and no extraction yet. */
async function waitForDocuments(page: Page, shipmentId: number, count: number): Promise<void> {
  await page.goto(`/shipments/${shipmentId}/upload`);
  await expect(page.getByText(`${count} chứng từ`)).toBeVisible({ timeout: 20_000 });
}

/** Absolute backend URL: /__e2e__ is not part of the app's /api proxy prefix. */
function backendUrl(path: string): string {
  const target = process.env.E2E_BACKEND_URL ?? "http://127.0.0.1:8001";
  return new URL(path, target).toString();
}

test.describe("document upload through the UI", () => {
  test("uploads several files at once", async ({ page }) => {
    const request = page.request;
    const shipmentId = await createShipment(request);
    await waitForDocuments(page, shipmentId, 0);

    const files = [SAMPLE_PDF, ...(existsSync(SAMPLE_XLSX) ? [SAMPLE_XLSX] : [])];
    const chooser = page.waitForEvent("filechooser");
    await page.getByRole("button", { name: "Kéo thả hoặc chọn tệp chứng từ" }).click();
    await (await chooser).setFiles(files);

    await expect(page.getByText(`${files.length} chứng từ`)).toBeVisible();
  });

  test("silently skips files with an unsupported extension", async ({ page }) => {
    const shipmentId = await createShipment(page.request);
    await waitForDocuments(page, shipmentId, 0);

    const chooser = page.waitForEvent("filechooser");
    await page.getByRole("button", { name: "Kéo thả hoặc chọn tệp chứng từ" }).click();
    // The file dialog filters by `accept`, but drag-and-drop bypasses it; feed a file directly.
    await (await chooser).setFiles({ name: "ghi-chu.txt", mimeType: "text/plain", buffer: Buffer.from("hello") });

    await expect(page.getByText("1 tệp không đúng định dạng và đã bị bỏ qua.")).toBeVisible();
    await expect(page.getByText("0 chứng từ")).toBeVisible();
  });
});

test.describe("Tờ khai preview and export", () => {
  test("previews filled cells and exports a workbook", async ({ page, request }) => {
    const shipmentId = await createShipment(request);
    const seeded = await request.post(backendUrl(`/__e2e__/shipments/${shipmentId}/declaration`), {
      data: { header: DECLARATION_HEADER, goods: DECLARATION_GOODS, taxes: [] },
    });
    expect(seeded.ok(), await seeded.text()).toBeTruthy();

    await page.goto(`/shipments/${shipmentId}/export`);
    await page.getByLabel("Mẫu Excel").selectOption("tokhai_import_v1");

    await expect(page.getByText("Ô đã điền")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("heading", { name: "TKN" })).toBeVisible();
    await expect(page.getByText("Gỗ thông xẻ (Pinus Taeda)")).toBeVisible();

    await page.getByRole("button", { name: "Tạo file Excel" }).click();
    await expect(page.getByRole("heading", { name: "Đã tạo file Excel" })).toBeVisible({ timeout: 30_000 });

    const download = page.waitForEvent("download");
    await page.getByRole("link", { name: "Tải file Excel" }).click();
    expect((await download).suggestedFilename()).toMatch(/\.xlsx$/);
  });
});
