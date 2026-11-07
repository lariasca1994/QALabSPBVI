import { expect, test } from "@playwright/test";
import { createHmac } from "node:crypto";
import { existsSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { credentialsPassword, e2eAdminEmail, expectNoHorizontalOverflow, readMfaCode, signInAsAdmin } from "./support";

function codeFile(): string {
  return join(process.env.QALAB_E2E_DATA_DIR ?? "", "mfa-code.txt");
}

async function freshCode(): Promise<string> {
  await expect.poll(readMfaCode).toMatch(/^\d{6}$/);
  return readMfaCode();
}

function resetLinkFile(): string {
  return join(process.env.QALAB_E2E_DATA_DIR ?? "", "reset-link.txt");
}

async function freshResetLink(): Promise<string> {
  await expect.poll(() => existsSync(resetLinkFile()) ? readFileSync(resetLinkFile(), "utf8").trim() : "").toMatch(/^\/#restablecer=/);
  return readFileSync(resetLinkFile(), "utf8").trim();
}

/** Código TOTP (RFC 6238, SHA-1, 30 s) de una clave base32, como lo calcula la app autenticadora. */
function totp(secret: string, offset = 0): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const char of secret.replace(/\s|=/g, "").toUpperCase()) bits += alphabet.indexOf(char).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g)!.map((byte) => parseInt(byte, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30_000) + offset));
  const digest = createHmac("sha1", key).update(counter).digest();
  const start = digest[digest.length - 1] & 0x0f;
  return String((digest.readUInt32BE(start) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}

test("recupera la contraseña con un enlace de un solo uso y la cambia con sesión iniciada", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const email = e2eAdminEmail("reset");
  const recovered = "clave recuperada para e2e 2026";
  const changed = "clave cambiada desde la app 2026";

  await page.goto("/");
  await page.getByRole("button", { name: "¿Olvidaste tu contraseña?" }).click();
  // Paso 1: solo el correo; la respuesta es la misma exista o no la cuenta.
  await expect(page.getByLabel("Código de recuperación")).toHaveCount(0);
  await page.getByLabel("Correo electrónico").fill("nadie@example.com");
  rmSync(resetLinkFile(), { force: true });
  await page.getByRole("button", { name: "Enviar enlace" }).click();
  await expect(page.getByRole("status")).toContainText("te enviamos un enlace");
  const neutral = await page.getByRole("status").innerText();
  expect(existsSync(resetLinkFile())).toBe(false);
  await page.getByLabel("Correo electrónico").fill(email);
  await page.getByRole("button", { name: "Enviar otro enlace" }).click();
  await expect(page.getByRole("status")).toHaveText(neutral);
  const link = await freshResetLink();
  await expectNoHorizontalOverflow(page);

  // Paso 2: al abrir el enlace, solo la contraseña nueva y su confirmación.
  await page.goto(link);
  await expect(page.getByRole("heading", { name: "Define tu contraseña nueva." })).toBeVisible();
  await expect(page).not.toHaveURL(/restablecer/);
  await expect(page.getByLabel("Correo electrónico")).toHaveCount(0);
  await page.getByLabel("Contraseña nueva", { exact: true }).fill(recovered);
  await page.getByLabel("Repite la contraseña nueva").fill("no coincide con la nueva");
  await page.getByRole("button", { name: "Guardar contraseña nueva" }).click();
  await expect(page.getByRole("alert")).toContainText("no coinciden");
  await page.getByLabel("Repite la contraseña nueva").fill(recovered);
  await page.getByRole("button", { name: "Guardar contraseña nueva" }).click();
  await expect(page.getByText("Contraseña restablecida")).toBeVisible();
  await expectNoHorizontalOverflow(page);

  // El enlace ya no sirve una segunda vez.
  await page.goto(link);
  await expect(page.getByRole("alert")).toContainText("no es válido o ya venció");
  await page.getByRole("button", { name: "Volver al inicio de sesión" }).click();

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

test("perdí mi autenticador: entra con un código por correo y configura una app nueva", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const email = e2eAdminEmail("recovery");
  const password = credentialsPassword("recovery");

  await page.goto("/");
  await page.getByLabel("Correo electrónico").fill(email);
  await page.getByLabel("Contraseña").fill(password);
  rmSync(codeFile(), { force: true });
  await page.getByRole("button", { name: "Continuar" }).click();
  await expect(page.getByRole("heading", { name: "Revisa tu correo." })).toBeVisible();
  await expect.poll(readMfaCode).toMatch(/^\d{6}$/);

  // El enlace está siempre en la pantalla del segundo factor.
  rmSync(codeFile(), { force: true });
  await page.getByRole("button", { name: "Perdí mi autenticador" }).click();
  await expect(page.getByRole("heading", { name: "Recupera tu acceso." })).toBeVisible();
  const recoveryCode = await freshCode();
  await page.getByLabel("Código de recuperación").fill(recoveryCode === "000000" ? "111111" : "000000");
  await page.getByRole("button", { name: "Verificar código" }).click();
  await expect(page.getByRole("alert")).toContainText("no es valido");
  await page.getByLabel("Código de recuperación").fill(recoveryCode);
  await page.getByRole("button", { name: "Verificar código" }).click();

  // Obliga a configurar la app nueva (QR y clave) antes de entrar.
  await expect(page.getByRole("img", { name: "Código QR para la app autenticadora" })).toBeVisible();
  const secret = (await page.locator(".totp-secret").innerText()).replace(/\s/g, "");
  await expectNoHorizontalOverflow(page);
  await page.getByLabel("Código de la app nueva").fill(totp(secret));
  await page.getByRole("button", { name: "Activar app nueva e ingresar" }).click();
  await expect(page.getByRole("heading", { name: /Buen día/ })).toBeVisible();

  // Siguiente ingreso: se pide la app nueva y el enlace de recuperación sigue a mano.
  await page.context().clearCookies();
  await page.goto("/");
  await page.getByLabel("Correo electrónico").fill(email);
  await page.getByLabel("Contraseña").fill(password);
  await page.getByRole("button", { name: "Continuar" }).click();
  await expect(page.getByRole("heading", { name: "Abre tu app autenticadora." })).toBeVisible();
  await expect(page.getByRole("button", { name: "Perdí mi autenticador" })).toBeVisible();
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
