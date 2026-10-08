import { expect, test } from "@playwright/test";
import { expectNoHorizontalOverflow, signInAsAdmin } from "./support";

test("la app es instalable (manifiesto, íconos y service worker) y abre sin conexión", async ({ page, context }) => {
  await page.setViewportSize({ width: 1280, height: 800 }); // escritorio (Windows, macOS, Linux)
  await page.goto("/");
  const manifestHref = await page.locator('link[rel="manifest"]').getAttribute("href");
  expect(manifestHref).toBe("/manifest.webmanifest");
  const manifest = await (await page.request.get(manifestHref ?? "")).json();
  expect(manifest).toMatchObject({ name: "QALabSPBVI · Laboratorio de pagos", short_name: "QALabSPBVI", start_url: "/", display: "standalone", lang: "es" });
  const sizes = manifest.icons.map((icon: { sizes: string; purpose: string }) => `${icon.sizes}:${icon.purpose}`);
  expect(sizes).toEqual(expect.arrayContaining(["192x192:any", "512x512:any", "512x512:maskable"]));
  for (const icon of [...manifest.icons, { src: "/icons/apple-touch-icon.png" }]) {
    expect((await page.request.get(icon.src)).status(), icon.src).toBe(200);
  }
  await expect(page.locator('link[rel="apple-touch-icon"]')).toHaveCount(1);

  // Service worker activo y controlando la página.
  expect(await page.evaluate(async () => Boolean((await navigator.serviceWorker.ready).active))).toBe(true);
  await page.reload();
  await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true);

  // Con sesión, la app usa la API; nada de /api queda en la caché del service worker.
  await signInAsAdmin(page, "pwa");
  const cached = await page.evaluate(async () => {
    const urls: string[] = [];
    for (const name of await caches.keys()) {
      for (const request of await (await caches.open(name)).keys()) urls.push(new URL(request.url).pathname);
    }
    return urls;
  });
  expect(cached).toEqual(expect.arrayContaining(["/", "/offline.html", "/manifest.webmanifest"]));
  expect(cached.filter((path) => path.startsWith("/api"))).toEqual([]);

  // Sin conexión, la navegación responde desde la caché en lugar del error del navegador.
  await context.setOffline(true);
  const offline = await page.goto("/");
  expect(offline?.ok()).toBe(true);
  await expect(page.locator("#root, main")).toHaveCount(1);
  await context.setOffline(false);
});

test("el resumen cabe en un celular con la letra del sistema agrandada", async ({ page }) => {
  // 280 px: lo que queda útil en un celular de 390 px con la letra al ~140 % (Android/iOS).
  await page.setViewportSize({ width: 280, height: 700 });
  await signInAsAdmin(page, "pwa");
  const title = page.locator(".epic-row-copy strong").first();
  await expect(title).toContainText("Implementar y validar programa");
  // El título largo se corta con "…" dentro de su tarjeta en vez de ensancharla.
  expect(await title.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);
  await expectNoHorizontalOverflow(page);
});
