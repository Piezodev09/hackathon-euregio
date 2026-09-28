// Checks the UI translations: every language has the same keys and every t("key") used in the
// portal/landing JavaScript exists in English (the reference language).
//   node scripts/check-i18n.mjs
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const js = join(dirname(fileURLToPath(import.meta.url)), "..", "web", "static", "js");
const { STRINGS } = await import(pathToFileURL(join(js, "i18n.js")));
let problems = 0;
const ref = new Set(Object.keys(STRINGS.en));
for (const [lang, table] of Object.entries(STRINGS)) {
  const keys = new Set(Object.keys(table));
  const missing = [...ref].filter((k) => !keys.has(k));
  const extra = [...keys].filter((k) => !ref.has(k));
  if (missing.length || extra.length) { problems++; console.log(`${lang}: missing ${missing.join(" ") || "-"} | extra ${extra.join(" ") || "-"}`); }
}
const skip = new Set(["i18n.js", "display.js", "display-i18n.js"]);
for (const file of readdirSync(js).filter((f) => f.endsWith(".js") && !skip.has(f))) {
  const src = readFileSync(join(js, file), "utf8");
  for (const m of src.matchAll(/\bt\("([a-z_]+\.[A-Za-z0-9_]+)"\s*[,)]/g)) {
    if (!ref.has(m[1])) { problems++; console.log(`${file}: unknown key ${m[1]}`); }
  }
}
// Kiosk display texts
const kiosk = readFileSync(join(js, "display-i18n.js"), "utf8");
globalThis.window = {};
new Function(kiosk)();
const kref = Object.keys(window.I18N.en);
for (const [lang, table] of Object.entries(window.I18N)) {
  const miss = kref.filter((k) => !(k in table));
  if (miss.length) { problems++; console.log(`kiosk ${lang}: missing ${miss.join(" ")}`); }
}
console.log(problems ? `${problems} problem(s)` : "i18n OK");
process.exit(problems ? 1 : 0);
