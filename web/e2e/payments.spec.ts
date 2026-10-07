import { expect, test, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { chooseSpbvi, e2eAdminEmail, expectNoHorizontalOverflow, signInAsAdmin } from "./support";

async function createAccount(
  page: Page,
  accountId: string,
  spbviId: string,
  balanceCents: number,
): Promise<void> {
  await page.getByLabel("Identificador de cuenta").fill(accountId);
  await chooseSpbvi(page, "account", spbviId);
  await page.getByLabel("Saldo inicial (centavos)").fill(String(balanceCents));
  await page.getByRole("button", { name: "Crear cuenta" }).click();
  await expect(page.getByRole("status")).toContainText("cuenta de laboratorio quedó creada");
}

async function registerKey(
  page: Page,
  spbviId: string,
  keyValue: string,
  destinationAccount: string,
  ownerAccount: string,
): Promise<void> {
  await page.getByRole("button", { name: "Llaves" }).click();
  await chooseSpbvi(page, "register", spbviId);
  await page.locator("#register-key-type").selectOption("email");
  await page.getByLabel("Valor de la llave").first().fill(keyValue);
  await page.getByLabel("Producto de depósito").fill(destinationAccount);
  await page.getByLabel("Correo del titular").fill(e2eAdminEmail(ownerAccount));
  await page.getByRole("button", { name: "Registrar llave" }).click();
  await expect(page.getByRole("status")).toContainText("registrada y confirmada");
}

async function responseJson(page: Page): Promise<Record<string, unknown>> {
  const details = page.locator(".execution-card details").first();
  if (!(await details.evaluate((element) => (element as HTMLDetailsElement).open))) {
    await details.locator("summary").click();
  }
  const content = await details.locator("pre").innerText();
  return JSON.parse(content) as Record<string, unknown>;
}

test("crea un pago intra-SPBVI idempotente y verifica su respuesta en móvil", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await signInAsAdmin(page, "intra");
  const suffix = randomUUID().replaceAll("-", "");
  const sourceAccount = `e2e-source-${suffix}`;
  const destinationAccount = `e2e-destination-${suffix}`;
  const spbviId = `e2e-intra-${suffix}`;
  const keyValue = `intra-${suffix}@example.test`;

  await page.getByRole("button", { name: "Pagos" }).click();
  await createAccount(page, sourceAccount, spbviId, 50_000);
  await createAccount(page, destinationAccount, spbviId, 0);
  await registerKey(page, spbviId, keyValue, destinationAccount, "intra");
  await page.getByRole("button", { name: "Pagos" }).click();

  const operationId = `e2e-operation-${suffix}`;
  await page.getByLabel("Tipo de flujo").selectOption("intra");
  await page.getByLabel("Identificador idempotente").fill(operationId);
  await page.getByLabel("Cuenta de origen").selectOption(sourceAccount);
  // Intra: el SPBVI destino queda fijo en el de la cuenta origen.
  await expect(page.locator("#payment-destination-spbvi")).toBeDisabled();
  await expect(page.locator("#payment-destination-spbvi")).toHaveValue(spbviId);
  await page.getByLabel("Tipo de llave destino").selectOption("email");
  await page.locator("#payment-key-value").selectOption(keyValue);
  await page.getByLabel("Monto en centavos").fill("1250");
  await page.getByRole("button", { name: "Ejecutar pago" }).click();
  await expect(page.getByRole("status")).toContainText("procesó correctamente");
  await expect(page.locator(".execution-card .method-pill")).toHaveText("POST");
  await expect(page.locator(".execution-card .request-summary")).toContainText("/payments");
  await expect(await responseJson(page)).toMatchObject({
    operation_id: operationId,
    amount_cents: 1250,
    payment_type: "intra_spbvi",
    replayed: false,
  });

  await page.getByRole("button", { name: "Ejecutar pago" }).click();
  await expect(page.getByRole("status")).toContainText("idempotente reconocida");
  await expect(await responseJson(page)).toMatchObject({
    operation_id: operationId,
    amount_cents: 1250,
    replayed: true,
  });
  await expectNoHorizontalOverflow(page);

  // Llave escrita a mano (prueba negativa): una llave inexistente se rechaza.
  await page.getByLabel("Identificador idempotente").fill(`${operationId}-manual`);
  await page.locator("#payment-key-value").selectOption("__manual__");
  await page.getByLabel("Llave destino escrita").fill(`no-existe-${suffix}@example.test`);
  await page.getByRole("button", { name: "Ejecutar pago" }).click();
  await expect(page.getByRole("alert")).toBeVisible();

  // Operaciones posteriores: pain.002, devolución parcial, cancelación aceptada y camt.054.
  const operation = page.locator("#operation-action");
  await operation.selectOption("status");
  await page.locator("#operation-payment-id").fill(operationId);
  await page.getByRole("button", { name: "Ejecutar operación" }).click();
  await expect(page.locator(".execution-card .request-summary")).toContainText("/status-report");
  await expect(await responseJson(page)).toMatchObject({ transaction_status: "ACSC" });
  await expect(page.locator(".execution-card summary", { hasText: "Mensaje pain.002" })).toBeVisible();

  await operation.selectOption("return");
  await page.locator("#operation-payment-id").fill(operationId);
  await page.locator("#operation-return-amount").fill("250");
  await page.getByRole("button", { name: "Ejecutar operación" }).click();
  await expect(page.locator(".execution-card .request-summary")).toContainText("/returns");
  await expect(page.getByRole("status")).toContainText("Devolución registrada");
  await expect(await responseJson(page)).toMatchObject({ amount_cents: 250, returned_total_cents: 250, reason_code: "MD06" });

  const cancellationId = `e2e-cxl-${suffix}`.slice(0, 60);
  await operation.selectOption("cancel");
  await page.locator("#operation-payment-id").fill(operationId);
  await page.locator("#operation-cancellation-new").fill(cancellationId);
  await page.getByRole("button", { name: "Ejecutar operación" }).click();
  await expect(page.locator(".execution-card .request-summary")).toContainText("202");
  await expect(await responseJson(page)).toMatchObject({ status: "pending", reason_code: "DUPL" });

  await operation.selectOption("resolve");
  await page.locator("#operation-cancellation-id").fill(cancellationId);
  await page.getByRole("button", { name: "Ejecutar operación" }).click();
  await expect(page.getByRole("status")).toContainText("Cancelación aceptada");
  await expect(await responseJson(page)).toMatchObject({
    status: "accepted",
    payment_return: { amount_cents: 1000, reason_code: "FOCR" },
  });
  await expect(page.locator(".execution-card summary", { hasText: "Mensaje pacs.004" })).toBeVisible();

  await operation.selectOption("notifications");
  await page.locator("#operation-account").fill(destinationAccount);
  await page.getByRole("button", { name: "Ejecutar operación" }).click();
  await expect(page.locator(".execution-card .request-summary")).toContainText("/notifications");
  await expect(await responseJson(page)).toMatchObject({ total_credits_cents: 1250, total_debits_cents: 1250 });
  await expectNoHorizontalOverflow(page);

  // Auditoría: todas las solicitudes del pago quedan correlacionadas por su operation_id.
  await page.getByRole("button", { name: "Logs" }).click();
  await page.getByLabel("OPERACIÓN").fill(operationId);
  await page.getByRole("button", { name: "Filtrar" }).click();
  const rows = page.locator(".audit-table tbody tr");
  await expect(rows.filter({ hasText: "/payments/{operation_id}/returns" })).toHaveCount(1);
  await expect(rows.filter({ hasText: "/payments/{operation_id}/cancellation-requests" })).toHaveCount(1);
  await expect(rows.filter({ hasText: "/payments/{operation_id}/status-report" })).toHaveCount(1);
  await expect(rows.filter({ has: page.locator("code", { hasText: /^\/payments$/ }) })).toHaveCount(2);
  await expectNoHorizontalOverflow(page);
});

test("liquida un pago inter-SPBVI con MOL local y presenta los mensajes ISO", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await signInAsAdmin(page, "inter");
  const suffix = randomUUID().replaceAll("-", "");
  const sourceAccount = `e2e-inter-source-${suffix}`;
  const destinationAccount = `e2e-inter-destination-${suffix}`;
  const sourceSpbvi = `e2e-source-spbvi-${suffix}`;
  const destinationSpbvi = `e2e-destination-spbvi-${suffix}`;
  const keyValue = `inter-${suffix}@example.test`;

  await page.getByRole("button", { name: "Pagos" }).click();
  await createAccount(page, sourceAccount, sourceSpbvi, 50_000);
  await createAccount(page, destinationAccount, destinationSpbvi, 0);
  await registerKey(page, destinationSpbvi, keyValue, destinationAccount, "inter");
  await page.getByRole("button", { name: "Pagos" }).click();

  const operationId = `e2e-inter-operation-${suffix}`;
  await page.getByLabel("Tipo de flujo").selectOption("inter");
  await page.getByLabel("Identificador idempotente").fill(operationId);
  await page.getByLabel("Cuenta de origen").selectOption(sourceAccount);
  // Inter: el SPBVI de la cuenta origen no aparece como destino.
  const destination = page.locator("#payment-destination-spbvi");
  await expect(destination.locator(`option[value="${sourceSpbvi}"]`)).toHaveCount(0);
  await destination.selectOption(destinationSpbvi);
  await page.getByLabel("Tipo de llave destino").selectOption("email");
  await page.locator("#payment-key-value").selectOption(keyValue);
  await page.getByLabel("Monto en centavos").fill("875");
  await page.getByRole("button", { name: "Ejecutar pago" }).click();

  await expect(page.getByRole("status")).toContainText("procesó correctamente");
  await expect(page.locator(".execution-card .request-summary")).toContainText("/payments/inter-spbvi");
  const response = await responseJson(page);
  expect(response).toMatchObject({
    operation_id: operationId,
    amount_cents: 875,
    payment_type: "inter_spbvi",
    status: "completed",
    replayed: false,
  });
  expect(String(response.pacs008_xml)).toContain("pacs.008.001.08");
  expect(String(response.pacs002_xml)).toContain("pacs.002.001.10");
  await expectNoHorizontalOverflow(page);
});
