// Accessibility and layout check with axe-core (WCAG 2.2 A/AA + best practice) in a real browser.
// Checks landing page, sign-in, legal page, kiosk display (light/dark) and the portal pages of the
// demo organisation, fails on serious/critical findings, console/CSP errors or horizontal scrolling
// on a 390 px phone.
//   scripts/dev.sh                       # in another terminal (demo data)
//   npm install --no-save playwright axe-core
//   node scripts/check-a11y.mjs [http://127.0.0.1:8000] [demo@example.org] [password]
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { chromium } from "playwright";

const require = createRequire(import.meta.url);
const axeSource = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const [BASE = "http://127.0.0.1:8000", EMAIL = "demo@example.org", PASSWORD = "Bike-Parking-Euregio-2026!"] = process.argv.slice(2);
const TAGS = ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa", "best-practice"];
let failures = 0;

const browser = await chromium.launch();

async function audit(page, name, errors) {
  await page.waitForTimeout(800);
  await page.evaluate(axeSource);
  const res = await page.evaluate(async (tags) => await window.axe.run(document, { runOnly: tags }), TAGS);
  const serious = res.violations.filter((v) => ["critical", "serious"].includes(v.impact));
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  const other = res.violations.filter((v) => !serious.includes(v)).map((v) => `${v.id}(${v.impact})`);
  const ok = !serious.length && !errors.length && overflow <= 0;
  console.log(`${ok ? "ok  " : "FAIL"} ${name}${other.length ? `  (minor: ${other.join(", ")})` : ""}`);
  for (const v of serious) for (const n of v.nodes.slice(0, 3)) console.log(`       ${v.id}: ${n.target.join(" ")}`);
  for (const e of errors) console.log(`       console: ${e}`);
  if (overflow > 0) console.log(`       horizontal overflow: ${overflow}px`);
  if (!ok) failures++;
  errors.length = 0;
}

async function context(opts = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, ...opts });
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(e.message));
  return { ctx, page, errors };
}

const meta = await (await fetch(`${BASE}/api/v1/meta`)).json();
const display = meta.demo?.display_url ? new URL(meta.demo.display_url) : null;

// Public pages
for (const scheme of ["light", "dark"]) {
  for (const [path, wait] of [["/", "h1"], ["/app#/login", "form"], ["/legal/privacy", "main"]]) {
    const { ctx, page, errors } = await context({ colorScheme: scheme });
    await page.goto(BASE + path);
    await page.waitForSelector(wait);
    await audit(page, `${path} [${scheme}]`, errors);
    await ctx.close();
  }
  if (display) {
    const { ctx, page, errors } = await context({ colorScheme: scheme });
    await page.goto(BASE + display.pathname + display.hash);
    await page.waitForSelector(".slots");
    await audit(page, `/display [${scheme}]`, errors);
    await ctx.close();
  }
}

// Portal pages of the demo organisation, desktop and phone
for (const [label, opts] of [["desktop", {}], ["phone", { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true }]]) {
  const { ctx, page, errors } = await context(opts);
  await page.goto(`${BASE}/app#/login`);
  await page.fill("input[name=email]", EMAIL);
  await page.fill("input[name=password]", PASSWORD);
  await page.click("button[type=submit]");
  await page.waitForSelector(".kpi");
  await audit(page, `portal overview [${label}]`, errors);
  const station = await page.getAttribute(".card a[href^='#/stations/st_']", "href");
  for (const [hash, wait] of [[station, ".slots .slot"], [`${station}/settings`, "form"], ["#/events", "h1"],
    ["#/devices", "h1"], ["#/integrations", "#int-rest"], ["#/team", "table"], ["#/security", "h2"], ["#/audit", "h1"]]) {
    await page.goto(`${BASE}/app${hash}`);
    await page.waitForSelector(wait);
    await audit(page, `portal ${hash.replace(/st_[\w-]+/, "st_…")} [${label}]`, errors);
  }
  await ctx.close();
}

await browser.close();
console.log(failures ? `\n${failures} page(s) failed` : "\nall pages passed");
process.exit(failures ? 1 : 0);
