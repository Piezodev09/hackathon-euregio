// Captures the evidence screenshots in docs/competition/screenshots/ from the demo organisation.
// Creates an API key and a webhook in the demo organisation, so run it on fresh demo data:
//   (cd server && python3 -m app.cli demo --reset) and scripts/dev.sh, then
//   npm install --no-save playwright && node scripts/capture-screenshots.mjs [http://127.0.0.1:8000]
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const [BASE = "http://127.0.0.1:8000", EMAIL = "demo@example.org", PW = "Bike-Parking-Euregio-2026!"] = process.argv.slice(2);
const OUT = join(dirname(fileURLToPath(import.meta.url)), "..", "docs", "competition", "screenshots");
const meta = await (await fetch(`${BASE}/api/v1/meta`)).json();
if (!meta.demo?.display_url) throw new Error("No demo station - run: python3 -m app.cli demo --reset");
const display = new URL(meta.demo.display_url);
const DISPLAY = BASE + display.pathname + display.hash;
const browser = await chromium.launch();
const errors = [];
async function newPage(opts = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, ...opts });
  const page = await ctx.newPage();
  page.on("console", (m) => { if (["error"].includes(m.type())) errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push("pageerror " + e.message));
  return page;
}
const shot = async (page, name) => { await page.waitForTimeout(700); await page.screenshot({ path: `${OUT}/${name}.png` }); console.log("wrote", name); };

// landing
let p = await newPage();
await p.goto(BASE + "/"); await p.waitForSelector("h1");
await p.waitForTimeout(1500);
await shot(p, "01-landing");

// portal
p = await newPage({ viewport: { width: 1280, height: 860 } });
await p.goto(BASE + "/app#/login");
await p.fill("input[name=email]", EMAIL); await p.fill("input[name=password]", PW);
await p.click("button[type=submit]"); await p.waitForSelector(".kpi");
const href = await p.getAttribute(".card a[href^='#/stations/st_']", "href");
await p.goto(BASE + "/app" + href); await p.waitForSelector(".slots .slot");
await shot(p, "02-station-live");
await p.click("button:text-is('Typical week')"); await p.waitForSelector("table.heatmap.week");
await p.locator("table.heatmap.week").scrollIntoViewIfNeeded();
await shot(p, "03-heatmap-week");
// integrations with a realistic setup
await p.goto(BASE + "/app#/integrations"); await p.waitForSelector("#int-keys");
await p.fill("section[aria-labelledby=int-keys] input[type=text]", "Town hall dashboard");
await p.click("section[aria-labelledby=int-keys] button[type=submit]");
await p.waitForSelector("section[aria-labelledby=int-keys] .alert-box.warn");
const hook = "section[aria-labelledby=int-hooks]";
await p.fill(`${hook} input[type=text]`, "Caretaker phone (Home Assistant)");
await p.fill(`${hook} input[type=url]`, "http://192.168.1.20:8123/api/webhook/bike-station-caretaker");
await p.click(`${hook} button[type=submit]`); await p.waitForSelector(`${hook} .alert-box.warn`);
await p.goto(BASE + "/app#/events"); await p.goto(BASE + "/app#/integrations"); await p.waitForSelector(`${hook} table`);
// reload so the one-time secrets are no longer on screen
await shot(p, "07-integrations");
await p.goto(BASE + "/app#/events"); await p.waitForSelector("table");
await shot(p, "06-events");
// German station view
await p.click(".sidebar .lang button[lang=de]");
await p.goto(BASE + "/app" + href); await p.waitForSelector(".slots .slot");
await shot(p, "08-station-de");
await p.click(".sidebar .lang button[lang=en]");
await p.goto(BASE + "/app#/security"); await p.waitForSelector("h2");
await shot(p, "09-security");

// kiosk + phone
p = await newPage({ viewport: { width: 1280, height: 800 } });
await p.goto(DISPLAY); await p.waitForSelector(".slots");
await shot(p, "04-kiosk");
p = await newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
await p.goto(DISPLAY); await p.waitForSelector(".slots");
await shot(p, "05-phone");
if (errors.length) console.log("console errors:", JSON.stringify(errors));
await browser.close();
