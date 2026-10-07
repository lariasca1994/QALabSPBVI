import { expect, test } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { chooseSpbvi, e2eAdminEmail, expectNoHorizontalOverflow, signInAsAdmin } from "./support";

test("administra el ciclo de vida de una llave sin desbordamiento en móvil", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await signInAsAdmin(page, "keys");
  await page.getByRole("button", { name: "Llaves" }).click();

  const suffix = randomUUID().replaceAll("-", "");
  const keyValue = `e2e-${suffix}@example.test`;
  const spbviId = `e2e-spbvi-${suffix}`;
  const ownerEmail = e2eAdminEmail("keys");

  await chooseSpbvi(page, "register", spbviId);
  await expect(page.locator("#register-key-type option")).toHaveText([
    "Documento de identidad", "Celular", "Correo electrónico", "Llave alfanumérica", "Código de comercio",
  ]);
  await page.locator("#register-key-type").selectOption("email");
  await page.getByLabel("Valor de la llave").first().fill(keyValue);
  await page.getByLabel("Producto de depósito").fill(`e2e-account-${suffix}`);
  await page.getByLabel("Correo del titular").fill(ownerEmail);
  await page.getByRole("button", { name: "Registrar llave" }).click();
  await expect(page.getByRole("status")).toContainText("registrada y confirmada");
  await expect(page.locator(".operation-grid")).toBeVisible();
  await expectNoHorizontalOverflow(page);

  await page.getByLabel("Operación").selectOption("lookup");
  await chooseSpbvi(page, "manage", spbviId);
  await page.getByLabel("Valor de la llave").last().fill(keyValue);
  await page.getByRole("button", { name: "Consultar llave" }).click();
  await expect(page.locator(".execution-card")).toContainText("200");
  await page.getByText("Ver JSON de respuesta").click();
  await expect(page.locator(".execution-card pre")).toContainText('"status": "active"');

  await page.getByLabel("Operación").selectOption("suspend-administrative");
  await page.getByLabel("Motivo").fill("Suspensión temporal E2E");
  await page.getByRole("button", { name: "Suspender llave" }).click();
  await expect(page.locator(".execution-card pre")).toContainText("suspended_administrative");

  await page.getByLabel("Operación").selectOption("reactivate-administrative");
  await page.getByLabel("Motivo").fill("Reactivación E2E");
  await page.getByRole("button", { name: "Reactivar llave" }).click();
  await expect(page.locator(".execution-card pre")).toContainText('"status": "confirmed"');

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByLabel("Operación").selectOption("delete");
  await page.getByLabel("Motivo").fill("Limpieza E2E");
  await page.getByRole("button", { name: "Eliminar llave" }).click();
  await expect(page.getByRole("status")).toContainText("se eliminó");

  await page.getByLabel("Operación").selectOption("lookup");
  await page.getByRole("button", { name: "Consultar llave" }).click();
  await expect(page.getByRole("alert")).toContainText("No se encontro la llave confirmada");
  await expectNoHorizontalOverflow(page);
});
