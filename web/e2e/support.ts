import { expect, type Page } from "@playwright/test";
import { existsSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";

interface Credentials {
  email: string;
  password: string;
}

function e2eFile(name: string): string {
  const directory = process.env.QALAB_E2E_DATA_DIR;
  if (!directory) throw new Error("Falta el directorio temporal del servidor E2E.");
  return join(directory, name);
}

function credentials(accountName: string): Credentials {
  const accounts = JSON.parse(
    readFileSync(e2eFile("credentials.json"), "utf8"),
  ) as Record<string, Credentials>;
  const account = accounts[accountName];
  if (!account) throw new Error(`No existe el usuario E2E temporal '${accountName}'.`);
  return account;
}

export async function readMfaCode(): Promise<string> {
  const file = e2eFile("mfa-code.txt");
  if (!existsSync(file)) return "";
  return readFileSync(file, "utf8").trim();
}

export async function signInAsAdmin(page: Page, accountName: string): Promise<void> {
  const account = credentials(accountName);
  await page.goto("/");
  await page.getByLabel("Correo electrónico").fill(account.email);
  await page.getByLabel("Contraseña").fill(account.password);
  rmSync(e2eFile("mfa-code.txt"), { force: true });
  await page.getByRole("button", { name: "Continuar" }).click();
  await expect.poll(readMfaCode).toMatch(/^\d{6}$/);
  await page.getByLabel("Código de acceso").fill(await readMfaCode());
  await page.getByRole("button", { name: "Verificar e ingresar" }).click();
  await expect(page.getByRole("heading", { name: /Buen día/ })).toBeVisible();
}

export async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  await expect.poll(() =>
    page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
  ).toBe(0);
}

export function e2eAdminEmail(accountName: string): string {
  return credentials(accountName).email;
}

/** Borra el último código escrito por el emisor de correo falso del servidor E2E. */
export function clearMfaCode(): void {
  rmSync(e2eFile("mfa-code.txt"), { force: true });
}
