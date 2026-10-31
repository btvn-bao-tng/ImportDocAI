import { expect, test } from "@playwright/test";
import { shipmentCode } from "./helpers";

test.describe("shipment list", () => {
  test("loads and offers the empty state or the table", async ({ page }) => {
    await page.goto("/");

    await expect(page.getByRole("heading", { name: "Danh sách lô hàng" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Tạo lô hàng" }).first()).toBeVisible();
    await expect(page.getByRole("searchbox", { name: "Tìm kiếm lô hàng" })).toBeVisible();
    await expect(page.getByRole("group", { name: "Lọc theo trạng thái" })).toBeVisible();

    // Either the empty state or the populated table renders once the query settles.
    await expect(
      page.getByRole("table").or(page.getByText("Chưa có lô hàng nào")),
    ).toBeVisible();
  });

  test("creates a shipment and lands on its upload page", async ({ page }) => {
    const code = shipmentCode();
    await page.goto("/");

    await page.getByRole("button", { name: "Tạo lô hàng" }).first().click();
    const form = page.getByRole("form", { name: "Tạo lô hàng mới" });
    await expect(form).toBeVisible();

    // Submit is disabled until a code is typed.
    const submit = form.getByRole("button", { name: "Tạo lô hàng" });
    await expect(submit).toBeDisabled();

    await form.getByLabel("Mã lô hàng").fill(code);
    await form.getByLabel("Khách hàng").fill("Khách hàng E2E");

    await Promise.all([
      page.waitForURL(/\/shipments\/\d+\/upload$/),
      submit.click(),
    ]);

    await expect(page.getByRole("heading", { name: code })).toBeVisible();
    await expect(page.getByRole("button", { name: /Kéo thả hoặc chọn tệp chứng từ/ })).toBeVisible();
    await expect(page.getByText("Chưa có chứng từ")).toBeVisible();
  });

  test("rejects a duplicate shipment code with an error callout", async ({ page }) => {
    const code = shipmentCode("DUP");
    await page.goto("/");

    await page.getByRole("button", { name: "Tạo lô hàng" }).first().click();
    const form = page.getByRole("form", { name: "Tạo lô hàng mới" });
    await form.getByLabel("Mã lô hàng").fill(code);
    await Promise.all([page.waitForURL(/\/upload$/), form.getByRole("button", { name: "Tạo lô hàng" }).click()]);

    await page.goto("/");
    await page.getByRole("button", { name: "Tạo lô hàng" }).first().click();
    const again = page.getByRole("form", { name: "Tạo lô hàng mới" });
    await again.getByLabel("Mã lô hàng").fill(code);
    await again.getByRole("button", { name: "Tạo lô hàng" }).click();

    await expect(page.getByText("Không tạo được lô hàng")).toBeVisible();
    await expect(page.getByText(`Shipment ${code} đã tồn tại`)).toBeVisible();
  });

  test("filters the list by search text", async ({ page }) => {
    const code = shipmentCode("SRCH");
    await page.goto("/");
    await page.getByRole("button", { name: "Tạo lô hàng" }).first().click();
    const form = page.getByRole("form", { name: "Tạo lô hàng mới" });
    await form.getByLabel("Mã lô hàng").fill(code);
    await Promise.all([page.waitForURL(/\/upload$/), form.getByRole("button", { name: "Tạo lô hàng" }).click()]);

    await page.goto("/");
    const search = page.getByRole("searchbox", { name: "Tìm kiếm lô hàng" });
    await search.fill(code);
    await expect(page.getByRole("link", { name: code })).toBeVisible();

    await search.fill("KHÔNG-CÓ-MÃ-NÀY-999");
    await expect(page.getByText("Không có lô hàng phù hợp với bộ lọc hiện tại.")).toBeVisible();
  });
});
