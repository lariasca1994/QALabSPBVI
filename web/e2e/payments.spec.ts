import { expect, test, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { e2eAdminEmail, expectNoHorizontalOverflow, signInAsAdmin } from "./support";

async function createAccount(
  page: Page,
  accountId: string,
  spbviId: string,
  balanceCents: number,
): Promise<void> {
  await page.getByLabel("Identificador de cuenta").fill(accountId);
  await page.getByLabel("SPBVI", { exact: true }).fill(spbviId);
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
  await page.getByLabel("SPBVI de origen").fill(spbviId);
  await page.locator("#register-key-type").selectOption("email");
  await page.getByLabel("Valor de la llave").first().fill(keyValue);
  await page.getByLabel("Producto de depósito").fill(destinationAccount);
  await page.getByLabel("Correo del titular").fill(e2eAdminEmail(ownerAccount));
  await page.getByRole("button", { name: "Registrar llave" }).click();
  await expect(page.getByRole("status")).toContainText("registrada y confirmada");
}

async function responseJson(page: Page): Promise<Record<string, unknown>> {
  const details = page.locator(".execution-card details").last();
  if (!(await details.evaluate((element) => (element as HTMLDetailsElement).open))) {
    await details.locator("summary").click();
  }
  const content = await page.locator(".execution-card pre").last().innerText();
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
  await page.getByLabel("Cuenta de origen").fill(sourceAccount);
  await page.getByLabel("Tipo de llave destino").selectOption("email");
  await page.locator("#payment-key-value").fill(keyValue);
  await page.getByLabel("Monto en centavos").fill("1250");
  await page.getByRole("button", { name: "Ejecutar pago" }).click();
  await expect(page.getByRole("status")).toContainText("procesó en el entorno local");
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
  await page.getByLabel("Cuenta de origen").fill(sourceAccount);
  await page.getByLabel("Tipo de llave destino").selectOption("email");
  await page.locator("#payment-key-value").fill(keyValue);
  await page.getByLabel("Monto en centavos").fill("875");
  await page.getByRole("button", { name: "Ejecutar pago" }).click();

  await expect(page.getByRole("status")).toContainText("procesó en el entorno local");
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
