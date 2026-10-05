import { expect, test } from "@playwright/test";
import { rmSync } from "node:fs";
import { join } from "node:path";
import { e2eAdminEmail, expectNoHorizontalOverflow, readMfaCode, signInAsAdmin } from "./support";

function codeFile(): string {
  return join(process.env.QALAB_E2E_DATA_DIR ?? "", "mfa-code.txt");
}

async function freshCode(): Promise<string> {
  await expect.poll(readMfaCode).toMatch(/^\d{6}$/);
  return readMfaCode();
}

test("recupera la contraseña desde el login y la cambia con sesión iniciada", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const email = e2eAdminEmail("reset");
  const recovered = "clave recuperada para e2e 2026";
  const changed = "clave cambiada desde la app 2026";

  await page.goto("/");
  await page.getByRole("button", { name: "¿Olvidaste tu contraseña?" }).click();
  await page.getByLabel("Correo electrónico").fill(email);
  rmSync(codeFile(), { force: true });
  await page.getByRole("button", { name: "Enviar código" }).click();
  await expect(page.getByRole("status")).toContainText("te enviamos un código");
  await page.getByLabel("Código de recuperación").fill(await freshCode());
  await page.getByLabel("Contraseña nueva", { exact: true }).fill(recovered);
  await page.getByLabel("Repite la contraseña nueva").fill(recovered);
  await page.getByRole("button", { name: "Restablecer contraseña" }).click();
  await expect(page.getByText("Contraseña restablecida")).toBeVisible();
  await expectNoHorizontalOverflow(page);

  // Inicia sesión con la contraseña recuperada.
  await page.getByLabel("Correo electrónico").fill(email);
  await page.getByLabel("Contraseña").fill(recovered);
  rmSync(codeFile(), { force: true });
  await page.getByRole("button", { name: "Continuar" }).click();
  await page.getByLabel("Código de acceso").fill(await freshCode());
  await page.getByRole("button", { name: "Verificar e ingresar" }).click();
  await expect(page.getByRole("heading", { name: /Buen día/ })).toBeVisible();

  await page.getByRole("button", { name: "Cambiar contraseña" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Contraseña actual").fill("no es la actual");
  await dialog.getByLabel("Contraseña nueva", { exact: true }).fill(changed);
  await dialog.getByLabel("Repite la contraseña nueva").fill(changed);
  await dialog.getByRole("button", { name: "Guardar contraseña" }).click();
  await expect(dialog.getByRole("alert")).toContainText("La contraseña actual no es correcta.");
  await dialog.getByLabel("Contraseña actual").fill(recovered);
  await dialog.getByRole("button", { name: "Guardar contraseña" }).click();
  await expect(page.getByText("Contraseña actualizada.")).toBeVisible();
  await expectNoHorizontalOverflow(page);
  // Deja el archivo de códigos limpio para las pruebas siguientes.
  rmSync(codeFile(), { force: true });
});

test("el admin desactiva y reactiva una cuenta", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await signInAsAdmin(page, "auth");
  const target = e2eAdminEmail("temporal");
  await page.getByRole("button", { name: "Usuarios y roles" }).click();
  const row = page.locator(".users-table tbody tr", { hasText: target });
  await row.getByRole("button", { name: "Desactivar" }).click();
  await expect(page.getByRole("status")).toContainText("quedó inactiva");
  await expect(row).toContainText("Inactivo");
  await row.getByRole("button", { name: "Activar" }).click();
  await expect(row).toContainText("Activo");
  // El admin no puede gestionarse a sí mismo.
  await expect(page.locator(".users-table tbody tr", { hasText: e2eAdminEmail("auth") }).getByRole("button")).toHaveCount(0);
});

test("cambiar contraseña y cerrar sesión están a mano en tablet vertical y en móvil", async ({ page }) => {
  await signInAsAdmin(page, "keys");
  for (const [width, height] of [[800, 1280], [768, 1024], [375, 812], [320, 740]]) {
    await page.setViewportSize({ width, height });
    await page.reload();
    await expect(page.getByRole("heading", { name: /Buen día/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "Cambiar contraseña" }).filter({ visible: true })).toHaveCount(1);
    await expect(page.getByRole("button", { name: "Cerrar sesión" }).filter({ visible: true })).toHaveCount(1);
    await expectNoHorizontalOverflow(page);
  }
});

test("una persona se registra como usuario y queda pendiente de épica", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const email = `demo-${Date.now()}@example.com`;
  const password = "clave de la demo e2e 2026";

  await page.goto("/");
  await page.getByRole("button", { name: "Crear cuenta" }).click();
  await page.getByLabel("Nombre").fill("Persona Demo");
  await page.getByLabel("Correo electrónico").fill(email);
  await page.getByLabel("Contraseña", { exact: true }).fill(password);
  await page.getByLabel("Repite la contraseña").fill(password);
  await page.getByRole("button", { name: "Crear cuenta" }).click();
  await expect(page.getByText(/Cuenta creada/)).toBeVisible();
  await expectNoHorizontalOverflow(page);

  await page.getByLabel("Correo electrónico").fill(email);
  await page.getByLabel("Contraseña").fill(password);
  rmSync(codeFile(), { force: true });
  await page.getByRole("button", { name: "Continuar" }).click();
  await page.getByLabel("Código de acceso").fill(await freshCode());
  await page.getByRole("button", { name: "Verificar e ingresar" }).click();
  await expect(page.getByText("Tu cuenta está pendiente de asignación.")).toBeVisible();
  await expect(page.locator(".sidebar-user")).toContainText("usuario");
  rmSync(codeFile(), { force: true });

  // El admin la ve en Usuarios y roles como pendiente de épica.
  await page.context().clearCookies();
  await page.setViewportSize({ width: 1280, height: 900 });
  await signInAsAdmin(page, "auth");
  await page.getByRole("button", { name: "Usuarios y roles" }).click();
  await expect(page.locator(".users-table tbody tr", { hasText: email })).toContainText("Sin épica · pendiente");
});
