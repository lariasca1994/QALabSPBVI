import { expect, test } from "@playwright/test";
import { e2eAdminEmail, expectNoHorizontalOverflow, readMfaCode, signInAsAdmin } from "./support";

test("rechaza una clave inválida, autentica con MFA y ejecuta un caso QA en móvil", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await page.goto("/");

  const unauthenticated = await page.request.get("/api/auth/me");
  expect(unauthenticated.status()).toBe(401);

  const accountEmail = e2eAdminEmail("auth");
  await page.getByLabel("Correo electrónico").fill(accountEmail);
  await page.getByLabel("Contraseña").fill("clave incorrecta para la prueba");
  await page.getByRole("button", { name: "Continuar" }).click();
  await expect(page.getByText("Si los datos son válidos, te enviamos un código de acceso al correo.")).toBeVisible();
  await expect.poll(readMfaCode).toBe("");
  const resend = page.getByRole("button", { name: /Reenviar en \d:\d{2}/ });
  await expect(resend).toBeVisible();
  await expect(resend).toBeDisabled();
  await expectNoHorizontalOverflow(page);

  await signInAsAdmin(page, "auth");
  await expect(page.getByRole("heading", { name: /Buen día/ })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expect(page.locator(".sidebar")).toHaveCSS("position", "fixed");

  await page.getByRole("button", { name: "QA" }).click();
  await page.getByLabel("ÉPICA").selectOption("E2E-EPIC");
  await page.getByRole("button", { name: "Ejecutar", exact: true }).click();
  await expect(page.locator(".execution-card")).toContainText("APROBADO");
  await expect(page.locator(".execution-card .method-pill")).toHaveText("GET");
  await expect(page.locator(".execution-card .request-summary")).toContainText("200");
  await expectNoHorizontalOverflow(page);

  // El JSON del CP se edita desde la plataforma y queda versionado.
  await expect(page.getByText("Salud de la API")).toBeVisible();
  await page.getByRole("button", { name: "JSON", exact: true }).click();
  const editor = page.getByLabel("Contrato REST del CP (JSON)");
  const definition = JSON.parse(await editor.inputValue());
  definition.expected_status_codes = [200, 204];
  await editor.fill(JSON.stringify(definition, null, 2));
  await expect(page.getByText("JSON válido.")).toBeVisible();
  await page.getByLabel("Motivo del cambio").fill("Aceptar también 204 en la prueba E2E");
  await page.getByRole("button", { name: "Guardar nueva versión" }).click();
  await expect(page.getByText(/quedó en la versión 2/)).toBeVisible();
  await expect(page.locator(".case-row")).toContainText("v2");
  await page.getByRole("button", { name: "Historial de E2E-CP-001" }).click();
  await expect(page.locator(".version-row")).toHaveCount(2);
  await page.getByRole("button", { name: "Cerrar" }).click();
  await page.getByRole("button", { name: "Ejecutar", exact: true }).click();
  await expect(page.locator(".execution-card")).toContainText("APROBADO");
  await expectNoHorizontalOverflow(page);
});
