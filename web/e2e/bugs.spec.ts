import { expect, test } from "@playwright/test";
import { expectNoHorizontalOverflow, signInAsAdmin } from "./support";

test("un integrante reporta un bug, registra el fix y lo cierra con retest aprobado", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await signInAsAdmin(page, "bugs");

  await page.getByRole("button", { name: "Bugs y fixes" }).click();
  await page.getByLabel("ÉPICA").selectOption("E2E-EPIC");
  await expect(page.getByText("Sin bugs reportados")).toBeVisible();

  await page.getByRole("button", { name: "Reportar bug" }).click();
  await page.getByLabel("Caso de prueba").selectOption("E2E-CP-001");
  await page.getByLabel("Título").fill("La salud no informa la versión");
  await page.getByLabel("Descripción").fill("Se esperaba el campo version en la respuesta de /health.");
  await page.getByLabel("Severidad").selectOption("minor");
  await page.getByRole("button", { name: "Reportar", exact: true }).click();
  await expect(page.getByText(/Bug reportado/)).toBeVisible();

  await page.locator(".bug-row", { hasText: "La salud no informa la versión" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.locator(".bug-meta")).toContainText("Abierto");
  await dialog.getByRole("button", { name: "Iniciar corrección" }).click();
  await expect(dialog.locator(".bug-meta")).toContainText("En fix");

  await dialog.getByRole("button", { name: "Registrar fix" }).click();
  await page.getByLabel("Título").fill("Agregar version a /health");
  await page.getByLabel("Qué se corrigió").fill("La respuesta incluye la versión desplegada.");
  await page.getByRole("button", { name: "Registrar fix" }).click();
  await expect(dialog.locator(".fix-row")).toContainText("En curso");
  await expect(dialog.getByRole("button", { name: "Enviar a retest" })).toBeDisabled();

  await dialog.getByRole("button", { name: "Marcar listo" }).click();
  await expect(dialog.locator(".fix-row")).toContainText("Listo para retest");
  await dialog.getByRole("button", { name: "Enviar a retest" }).click();
  await expect(dialog.locator(".bug-meta")).toContainText("Listo para retest");
  await dialog.getByRole("button", { name: "Retest aprobado" }).click();
  await expect(dialog.locator(".bug-meta")).toContainText("Cerrado");
  await expect(dialog.locator(".bug-history li")).toHaveCount(6);
  await dialog.getByRole("button", { name: "Cerrar", exact: true }).click();

  await page.getByRole("button", { name: "Cerrados" }).click();
  await expect(page.locator(".bug-row")).toHaveCount(1);
  await page.setViewportSize({ width: 360, height: 760 });
  await expectNoHorizontalOverflow(page);
});
