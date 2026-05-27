// Compile the version-controlled dataset into the static artifacts the CDN serves:
//   public/scholarships.json   { meta, results: [active records] }
//   public/meta.json           meta only
//   public/index.html          docs page (copied from site/)
//
// Only `active` records are published — needs-review / archived stay internal. Zero dependencies
// (Node stdlib only) so the Netlify build is trivial and can't rot.
import { readFileSync, writeFileSync, mkdirSync, readdirSync, statSync, cpSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const DATA = join(ROOT, "data");
const SITE = join(ROOT, "site");
const OUT = join(ROOT, "public");

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (name.endsWith(".json")) out.push(p);
  }
  return out;
}

const records = walk(DATA)
  .map((p) => JSON.parse(readFileSync(p, "utf8")))
  .filter((r) => r.status === "active")
  .sort((a, b) => a.name.localeCompare(b.name));

const meta = {
  name: "Open Scholarships",
  version: "0.1.0",
  license: "CC-BY-4.0",
  license_url: "https://creativecommons.org/licenses/by/4.0/",
  attribution_required: "Open Scholarships by Grudged LLC - https://github.com/Grudged/open-scholarships (CC BY 4.0)",
  source_repo: "https://github.com/Grudged/open-scholarships",
  count: records.length,
  states: [...new Set(records.map((r) => r.geo?.state).filter(Boolean))].sort(),
  generated_at: new Date().toISOString(),
};

mkdirSync(OUT, { recursive: true });
if (existsSync(SITE)) cpSync(SITE, OUT, { recursive: true });
writeFileSync(join(OUT, "scholarships.json"), JSON.stringify({ meta, results: records }, null, 2));
writeFileSync(join(OUT, "meta.json"), JSON.stringify(meta, null, 2));
console.log(`built ${records.length} active record(s) -> public/  (states: ${meta.states.join(", ") || "none"})`);
