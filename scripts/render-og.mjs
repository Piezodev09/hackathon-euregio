// Renders web/static/img/og.png (1200x630 social preview) from scripts/og/og.html with Playwright.
// The product visual is copied from web/index.html so both stay identical.
//   node scripts/render-og.mjs      (needs the "playwright" package)
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { chromium } from "playwright";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const index = readFileSync(join(root, "web", "index.html"), "utf8");
const svg = index.match(/<svg class="viz"[\s\S]*?<\/svg>/)[0];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, colorScheme: "light" });
await page.goto(pathToFileURL(join(root, "scripts", "og", "og.html")).href);
await page.evaluate((markup) => { document.getElementById("viz").innerHTML = markup; }, svg);
await page.screenshot({ path: join(root, "web", "static", "img", "og.png"), clip: { x: 0, y: 0, width: 1200, height: 630 } });
await browser.close();
console.log("web/static/img/og.png written");
