import { expect, test, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { chooseSpbvi, expectNoHorizontalOverflow, signInAsAdmin } from "./support";

// Cámara simulada: getUserMedia devuelve el video de un canvas que dibuja la imagen indicada en
// window.__fakeCameraImage (el QR generado por la app). El lector real (jsQR) lo procesa igual.
const FAKE_CAMERA = `
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: {
    getUserMedia: async () => {
      const canvas = document.createElement("canvas");
      canvas.width = 480; canvas.height = 480;
      const context = canvas.getContext("2d");
      const image = new Image();
      const draw = () => {
        context.fillStyle = "#fff"; context.fillRect(0, 0, 480, 480);
        if (window.__fakeCameraImage && image.src !== window.__fakeCameraImage) image.src = window.__fakeCameraImage;
        if (image.complete && image.naturalWidth) context.drawImage(image, 80, 80, 320, 320);
      };
      setInterval(draw, 50);
      return canvas.captureStream(15);
    },
  } });
`;

async function createAccount(page: Page, accountId: string, spbviId: string, balanceCents: number): Promise<void> {
  await page.getByLabel("Identificador de cuenta").fill(accountId);
  await chooseSpbvi(page, "account", spbviId);
  await page.getByLabel("Saldo inicial (centavos)").fill(String(balanceCents));
  await page.getByRole("button", { name: "Crear cuenta" }).click();
  await expect(page.getByRole("status")).toContainText("cuenta de laboratorio quedó creada");
}

async function generateQr(page: Page, spbviId: string, kind: string, amount?: number): Promise<string> {
  await page.locator("#qr-kind").selectOption(kind);
  await page.locator("#qr-spbvi").selectOption(spbviId);
  await page.locator("#qr-key-type").selectOption("alias");
  await expect(page.locator("#qr-key")).toBeEnabled();
  await page.getByLabel("Nombre que verá quien paga").fill("Tienda E2E");
  if (amount !== undefined) await page.locator("#qr-amount").fill(String(amount));
  const previousPayload = (await page.locator("#qr-payload-text").count()) ? await page.locator("#qr-payload-text").textContent() : null;
  const previousImage = (await page.locator(".qr-image").count()) ? await page.locator(".qr-image").getAttribute("src") : null;
  await page.getByRole("button", { name: "Generar QR" }).click();
  // Espera el QR nuevo (contenido e imagen), no el anterior que sigue en pantalla.
  await expect(page.locator("#qr-payload-text")).not.toHaveText(previousPayload ?? "__ninguno__");
  await expect(page.locator(".qr-image")).toBeVisible();
  if (previousImage) await expect(page.locator(".qr-image")).not.toHaveAttribute("src", previousImage);
  return (await page.locator("#qr-payload-text").textContent()) ?? "";
}

async function payPreview(page: Page, source: string, amount?: number): Promise<void> {
  await page.locator("#qr-source").selectOption(source);
  if (amount !== undefined) await page.getByLabel("Monto en centavos", { exact: true }).last().fill(String(amount));
  await page.getByRole("button", { name: "Pagar", exact: true }).click();
}

test("cobra y paga con QR leyendo por texto, imagen y cámara", async ({ page }) => {
  await page.addInitScript(FAKE_CAMERA);
  await page.setViewportSize({ width: 390, height: 844 });
  await signInAsAdmin(page, "qr");
  const suffix = randomUUID().replaceAll("-", "").slice(0, 10);
  const spbviId = `e2e-qr-${suffix}`;
  const shop = `e2e-qr-shop-${suffix}`;
  const payer = `e2e-qr-payer-${suffix}`;

  await page.getByRole("button", { name: "Pagos", exact: true }).click();
  await createAccount(page, payer, spbviId, 100_000);
  await createAccount(page, shop, spbviId, 0);
  await page.getByRole("button", { name: "Llaves" }).click();
  await chooseSpbvi(page, "register", spbviId);
  await page.locator("#register-key-type").selectOption("alias");
  await page.getByLabel("Valor de la llave").first().fill(`@qr${suffix}`);
  await page.getByLabel("Producto de depósito").fill(shop);
  await page.getByRole("button", { name: "Registrar llave" }).click();
  await expect(page.getByRole("status")).toContainText("registrada y confirmada");

  await page.getByRole("button", { name: "QR", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Pagos con QR" })).toBeVisible();

  // 1) Cobro dinámico pagado pegando el contenido del QR.
  const dynamic = await generateQr(page, spbviId, "dynamic", 2500);
  expect(dynamic).toMatch(/^000201010212/);
  await expect(page.locator(".qr-charge-status .qr-status")).toHaveText("Pendiente de pago");
  await page.getByRole("tab", { name: "Texto" }).click();
  await page.getByLabel("Contenido del QR").fill(dynamic);
  await page.getByRole("button", { name: "Leer QR" }).click();
  await expect(page.locator(".qr-preview-summary")).toContainText("Cobro dinámico de un solo uso");
  await expect(page.locator(".qr-preview-summary")).toContainText("$ 25,00");
  await expect(page.locator("#qr-pay-amount")).toHaveCount(0); // el monto no se edita
  await payPreview(page, payer);
  await expect(page.getByRole("status").filter({ hasText: "Pago con QR aprobado (intra-SPBVI)" })).toBeVisible();
  // El cobro se actualiza solo a "Pagado" y no admite un segundo pago.
  await expect(page.locator(".qr-charge-status .qr-status")).toHaveText("Pagado", { timeout: 10_000 });
  await expect(page.getByRole("button", { name: "Pagar", exact: true })).toBeDisabled();
  await expectNoHorizontalOverflow(page);

  // 2) QR estático leído desde una imagen: quien paga define el monto.
  await page.getByRole("button", { name: "Leer otro QR" }).click();
  await generateQr(page, spbviId, "static");
  const image = await page.locator(".qr-image").getAttribute("src");
  await page.getByRole("tab", { name: "Imagen" }).click();
  await page.getByLabel("Imagen del QR").setInputFiles({
    name: "qr.png",
    mimeType: "image/png",
    buffer: Buffer.from((image ?? "").split(",")[1], "base64"),
  });
  await expect(page.locator(".qr-preview-summary")).toContainText("QR estático · eliges el monto");
  await payPreview(page, payer, 700);
  await expect(page.getByRole("status").filter({ hasText: "Pago con QR aprobado" })).toContainText("$ 7,00");

  // 3) Cobro dinámico leído con la cámara.
  await page.getByRole("button", { name: "Leer otro QR" }).click();
  await generateQr(page, spbviId, "dynamic", 1200);
  const cameraImage = await page.locator(".qr-image").getAttribute("src");
  await page.evaluate((src) => { (window as unknown as { __fakeCameraImage: string }).__fakeCameraImage = src ?? ""; }, cameraImage);
  await page.getByRole("tab", { name: "Cámara" }).click();
  await page.getByRole("button", { name: "Activar cámara" }).click();
  await expect(page.locator(".qr-preview-summary")).toContainText("$ 12,00", { timeout: 15_000 });
  await payPreview(page, payer);
  await expect(page.getByRole("status").filter({ hasText: "Pago con QR aprobado" })).toBeVisible();
  await expect(page.locator(".qr-charge-status .qr-status")).toHaveText("Pagado", { timeout: 10_000 });

  // QR alterado: la lectura lo rechaza sin mover dinero.
  await page.getByRole("button", { name: "Leer otro QR" }).click();
  await page.getByRole("tab", { name: "Texto" }).click();
  await page.getByLabel("Contenido del QR").fill(dynamic.replace("540525.00", "540599.00"));
  await page.getByRole("button", { name: "Leer QR" }).click();
  await expect(page.getByRole("alert")).toContainText("CRC");
  await expectNoHorizontalOverflow(page);

  // Con la app oculta (otra pestaña, app minimizada o pantalla apagada) no sale ninguna
  // consulta del cobro: la API y las bases pueden dormir. Al volver, se retoma.
  await generateQr(page, spbviId, "dynamic", 900);
  const chargeRequests: string[] = [];
  page.on("request", (request) => { if (request.url().includes("/api/qr/charges/")) chargeRequests.push(request.url()); });
  const setVisibility = (state: "hidden" | "visible") => page.evaluate((value) => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => value });
    document.dispatchEvent(new Event("visibilitychange"));
  }, state);
  await setVisibility("hidden");
  chargeRequests.length = 0;
  await page.waitForTimeout(7_000);
  expect(chargeRequests).toEqual([]);
  await setVisibility("visible");
  await expect.poll(() => chargeRequests.length).toBeGreaterThan(0);
});
