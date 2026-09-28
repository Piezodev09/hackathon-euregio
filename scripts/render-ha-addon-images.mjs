// Renders the Home Assistant add-on images from web/static/img/icon.svg with Playwright:
//   icon.png 128x128 and logo.png 250x100 in integrations/home-assistant/bike-station-agent/
//   node scripts/render-ha-addon-images.mjs      (needs the "playwright" package)
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { chromium } from "playwright";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const out = join(root, "integrations", "home-assistant", "bike-station-agent");
const svg = readFileSync(join(root, "web", "static", "img", "icon.svg"), "utf8");
const font = pathToFileURL(join(root, "web", "static", "fonts", "inter-latin-wght-normal.woff2")).href;
const css = `@font-face{font-family:Inter;src:url(${font}) format("woff2");font-weight:100 900}
  html,body{margin:0;background:transparent}
  .logo{display:flex;align-items:center;gap:12px;box-sizing:border-box;width:250px;height:100px;padding:0 16px;
    border-radius:16px;background:#fff;font-family:Inter,sans-serif;color:#111827}  /* readable in light and dark themes */
  .logo svg{width:64px;height:64px;flex:none}
  .logo b{display:block;font-size:20px;font-weight:700;letter-spacing:-.02em;line-height:1.1}
  .logo span{display:block;font-size:14px;font-weight:500;color:#4b5563}`;

const browser = await chromium.launch();
const page = await browser.newPage({ deviceScaleFactor: 1 });
await page.setContent(`<style>${css}</style><div id="i" style="width:128px;height:128px">${svg.replace("<svg ", '<svg width="128" height="128" ')}</div>`);
await page.locator("#i").screenshot({ path: join(out, "icon.png"), omitBackground: true });
await page.setContent(`<style>${css}</style><div class="logo" id="l">${svg}<div><b>Smart Bike<br>Station</b><span>agent</span></div></div>`);
await page.evaluate(() => document.fonts.ready);
await page.locator("#l").screenshot({ path: join(out, "logo.png"), omitBackground: true });
await browser.close();
console.log("icon.png and logo.png written to", out);
