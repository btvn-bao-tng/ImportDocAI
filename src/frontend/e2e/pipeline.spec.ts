import { expect, test, type Page } from "@playwright/test";
import { SAMPLE_PDF, shipmentCode } from "./helpers";

async function createShipment(page: Page, code: string): Promise<string> {
  await page.goto("/");
  await page.getByRole("button", { name: "Tạo lô hàng" }).first().click();
  const form = page.getByRole("form", { name: "Tạo lô hàng mới" });
  await form.getByLabel("Mã lô hàng").fill(code);
  await Promise.all([
    page.waitForURL(/\/shipments\/\d+\/upload$/),
    form.getByRole("button", { name: "Tạo lô hàng" }).click(),
  ]);
  const match = /\/shipments\/(\d+)\/upload$/.exec(page.url());
  if (!match) throw new Error(`unexpected url after create: ${page.url()}`);
  return match[1];
}

async function uploadSample(page: Page): Promise<void> {
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Kéo thả hoặc chọn tệp chứng từ" }).click();
  await (await chooser).setFiles(SAMPLE_PDF);
  await expect(page.getByText("1 chứng từ")).toBeVisible();
}

/** Upload + process, parking the test on the review page. */
async function processSampleDocument(page: Page): Promise<void> {
  await uploadSample(page);
  await page.getByRole("button", { name: "Xử lý chứng từ" }).click();
  await page.waitForURL(/\/processing$/, { timeout: 30_000 });
  await page.waitForURL(/\/review$/, { timeout: 60_000 });
}

test.describe("upload and processing", () => {
  test("uploads, shows processing progress and opens the review page", async ({ page }) => {
    await createShipment(page, shipmentCode());

    await uploadSample(page);
    await expect(page.getByText("Đã tải lên")).toHaveCount(2); // result row + document badge
    await expect(page.getByRole("combobox", { name: "Loại chứng từ cho bill.pdf" })).toBeVisible();

    const process = page.getByRole("button", { name: "Xử lý chứng từ" });
    await expect(process).toBeEnabled();
    await process.click();

    // Upload hands off to the dedicated progress screen, which then lands on review by itself.
    await page.waitForURL(/\/shipments\/\d+\/processing$/, { timeout: 30_000 });
    await expect(page.getByRole("progressbar", { name: "Tiến độ xử lý chứng từ" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Chứng từ trong lô" })).toBeVisible();

    await page.waitForURL(/\/shipments\/\d+\/review$/, { timeout: 60_000 });
    await expect(page.getByRole("region", { name: "Giá trị trích xuất" })).toBeVisible();
  });

  test("explains that processing needs at least one document", async ({ page }) => {
    await createShipment(page, shipmentCode());

    await expect(page.getByRole("button", { name: "Xử lý chứng từ" })).toBeDisabled();
    await expect(page.getByText("Tải lên ít nhất một chứng từ để bắt đầu trích xuất.")).toBeVisible();
  });

  test("re-classifying a document updates its type", async ({ page }) => {
    await createShipment(page, shipmentCode());
    await uploadSample(page);

    // Classification only happens when the document is processed.
    await expect(page.getByRole("combobox", { name: "Loại chứng từ cho bill.pdf" })).toHaveValue("");
    await page.getByRole("button", { name: "Xử lý chứng từ" }).click();
    await page.waitForURL(/\/processing$/, { timeout: 30_000 });
    await page.waitForURL(/\/review$/, { timeout: 60_000 });

    await page.goto(page.url().replace(/\/review$/, "/upload"));
    const classified = page.getByRole("combobox", { name: "Loại chứng từ cho bill.pdf" });
    await expect(classified).toHaveValue("TRANSPORT_DOCUMENT");
    await classified.selectOption("COMMERCIAL_INVOICE");
    await expect(page.getByRole("combobox", { name: "Loại chứng từ cho bill.pdf" })).toHaveValue("COMMERCIAL_INVOICE");
  });
});

test.describe("review page", () => {
  test("shows the source document, the template preview and the verify hand-off", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await createShipment(page, shipmentCode());
    await processSampleDocument(page);

    await expect(page.getByRole("button", { name: "Kiểm tra & xuất file" })).toBeVisible();
    await expect(page.getByRole("complementary", { name: "Chứng từ nguồn" })).toBeVisible();
    await expect(page.getByRole("group", { name: "Chọn chứng từ" }).getByRole("button", { name: "bill.pdf" })).toBeVisible();

    // pdf.js renders page 1 from the proxied file endpoint.
    await expect(page.locator('canvas[aria-label="Trang 1"]')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("Trang 1 / 1")).toBeVisible();

    await expect(page.getByText(/cần xem lại|Đã xử lý xong/).first()).toBeVisible();
    await expect(page.getByRole("region", { name: "Giá trị trích xuất" })).toBeVisible();

    await page.getByRole("group", { name: "Chọn nội dung khung phải" }).getByRole("button", { name: "Mẫu tờ khai cuối" }).click();
    await expect(page.getByRole("heading", { name: "Mẫu tờ khai cuối" })).toBeVisible();
    await expect(page.getByRole("tablist", { name: "Chọn sheet mẫu" })).toBeVisible();
  });

  test("splitter is keyboard adjustable", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await createShipment(page, shipmentCode().replace("E2E", "SPLIT"));
    await processSampleDocument(page);

    const splitter = page.getByRole("separator", { name: "Điều chỉnh độ rộng khung" });
    await expect(splitter).toBeVisible();
    await expect(splitter).toHaveAttribute("aria-valuenow", "50");
    await splitter.focus();
    await page.keyboard.press("ArrowRight");
    await expect(splitter).toHaveAttribute("aria-valuenow", "52");
    await page.keyboard.press("Home");
    await expect(splitter).toHaveAttribute("aria-valuenow", "15");
    await page.keyboard.press("End");
    await expect(splitter).toHaveAttribute("aria-valuenow", "85");
  });

  test("maximize toggles the source panel", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await createShipment(page, shipmentCode().replace("E2E", "MAX"));
    await processSampleDocument(page);

    const source = page.getByRole("complementary", { name: "Chứng từ nguồn" });
    await source.getByRole("button", { name: "Mở rộng" }).click();
    const collapse = source.getByRole("button", { name: "Thu nhỏ" });
    await expect(collapse).toBeVisible();
    await expect(collapse).toHaveAttribute("aria-pressed", "true");
    await collapse.click();
    await expect(source.getByRole("button", { name: "Mở rộng" })).toBeVisible();
  });

  test("verify step generates the final workbook and offers downloads", async ({ page }) => {
    await createShipment(page, shipmentCode());
    await processSampleDocument(page);

    await page.getByRole("button", { name: "Kiểm tra & xuất file" }).click();
    await page.waitForURL(/\/verify$/, { timeout: 30_000 });

    await expect(page.getByRole("region", { name: "Chứng từ gốc" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Giá trị trích xuất" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "File Excel cuối cùng" })).toBeVisible({ timeout: 45_000 });

    const download = page.waitForEvent("download");
    await page.getByRole("link", { name: "Tải file Excel" }).click();
    expect((await download).suggestedFilename()).toMatch(/\.xlsx$/);

    await page.getByRole("link", { name: "Sửa dữ liệu" }).click();
    await page.waitForURL(/\/review$/, { waitUntil: "commit" });
  });
});

test.describe("export page", () => {
  test("exports the Tờ khai as a NEW_DRAFT built from business inputs", async ({ page }) => {
    await createShipment(page, shipmentCode());
    await processSampleDocument(page);
    const shipmentId = Number(/shipments\/(\d+)/.exec(page.url())?.[1]);
    await page.goto(`/shipments/${shipmentId}/export`);

    await expect(page.getByRole("heading", { name: "Xuất file theo mẫu" })).toBeVisible();
    await page.getByLabel("Mẫu Excel").selectOption("tokhai_import_v1");

    // Resolution turns the transport document's business facts (waybill, vessel,
    // weights) into Tờ khai inputs, so the preview is populated even though no
    // customs declaration document exists.
    await expect(page.getByText("Ô đã điền")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("Số vận đơn").first()).toBeVisible();

    await page.getByRole("button", { name: "Tạo file Excel" }).click();
    await expect(page.getByRole("heading", { name: "Đã tạo file Excel" })).toBeVisible({ timeout: 30_000 });
    // Customs-generated values (declaration number, exchange rate) cannot be filled
    // from business inputs, so the workbook stays a reviewable DRAFT.
    await expect(page.getByText("Bản nháp (DRAFT)")).toBeVisible();

    const download = page.waitForEvent("download");
    await page.getByRole("link", { name: "Tải file Excel" }).click();
    expect((await download).suggestedFilename()).toMatch(/\.xlsx$/);
  });

  test("exports the uandi template to a downloadable xlsx", async ({ page }) => {
    await createShipment(page, shipmentCode());
    await processSampleDocument(page);
    const shipmentId = Number(/shipments\/(\d+)/.exec(page.url())?.[1]);
    await page.goto(`/shipments/${shipmentId}/export`);

    await expect(page.getByText("Chưa xuất file lần nào")).toBeVisible();
    await page.getByLabel("Mẫu Excel").selectOption("uandi_import_v1");
    await page.getByRole("button", { name: "Tạo file Excel" }).click();

    await expect(page.getByRole("heading", { name: "Đã tạo file Excel" })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("link", { name: "Xem báo cáo đối soát" })).toBeVisible();

    const download = page.waitForEvent("download");
    await page.getByRole("link", { name: "Tải file Excel" }).click();
    expect((await download).suggestedFilename()).toMatch(/\.xlsx$/);

    await expect(page.getByRole("heading", { name: "Lịch sử xuất file" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Tải xuống" }).first()).toBeVisible();

    await page.getByRole("button", { name: "Tạo bản khác" }).click();
    await expect(page.getByRole("heading", { name: "Đã tạo file Excel" })).not.toBeVisible();
  });

  test("shows the uandi field preview for extracted documents", async ({ page }) => {
    await createShipment(page, shipmentCode());
    await processSampleDocument(page);
    const shipmentId = Number(/shipments\/(\d+)/.exec(page.url())?.[1]);
    await page.goto(`/shipments/${shipmentId}/export`);

    await page.getByLabel("Mẫu Excel").selectOption("uandi_import_v1");
    await expect(page.getByText("Xem trước dữ liệu xuất")).toBeVisible();
    await expect(page.getByText("Thể tích (CBM)")).toBeVisible();
    await expect(page.getByRole("table").first()).toBeVisible();
  });

  test("navigates back to review and upload", async ({ page }) => {
    const shipmentId = await createShipment(page, shipmentCode());
    await page.goto(`/shipments/${shipmentId}/export`);

    await page.getByRole("link", { name: `Lô #${shipmentId}` }).click();
    await page.waitForURL(new RegExp(`/shipments/${shipmentId}/verify$`), { waitUntil: "commit" });
    await expect(page.getByText("Chưa có chứng từ để đối chiếu")).toBeVisible();

    await page.locator("#noi-dung").getByRole("link", { name: "Tải lên chứng từ" }).click();
    await page.waitForURL(new RegExp(`/shipments/${shipmentId}/upload$`), { waitUntil: "commit" });
  });
});

test("unknown routes fall back to the shipment list", async ({ page }) => {
  await page.goto("/khong-ton-tai");
  await page.waitForURL(/\/$/);
  await expect(page.getByRole("heading", { name: "Danh sách lô hàng" })).toBeVisible();
});
