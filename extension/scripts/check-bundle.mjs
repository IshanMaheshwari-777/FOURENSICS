// Post-build guard: bundle-size budgets and a least-privilege manifest.
// Run after `npm run build`. Fails when the extension gets heavier (slower on every GitHub page)
// or asks for more browser access than it needs.
import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

const dist = fileURLToPath(new URL("../dist/", import.meta.url));

// Measured when added: content.js ~172 KB, background.js ~21 KB. Budgets leave headroom for
// features, but catch an accidental heavy dependency or a duplicated bundle.
const BUDGET_BYTES = { "content.js": 230_000, "background.js": 40_000 };
const ALLOWED_PERMISSIONS = ["storage"];
const ALLOWED_HOSTS = ["http://localhost:8787/*", "http://127.0.0.1:8787/*"];

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log(`✓ ${name}`); }

for (const [file, budget] of Object.entries(BUDGET_BYTES)) {
  await check(`${file} is within ${Math.round(budget / 1000)} KB`, async () => {
    const { size } = await stat(join(dist, file));
    assert.ok(size <= budget, `${file} is ${size} bytes; budget is ${budget}. Look for a new heavy dependency.`);
  });
}

const manifest = JSON.parse(await readFile(join(dist, "manifest.json"), "utf8"));

await check("Manifest V3 with a module service worker", () => {
  assert.equal(manifest.manifest_version, 3);
  assert.equal(manifest.background?.service_worker, "background.js");
});

await check("only the storage permission is requested", () => {
  assert.deepEqual([...(manifest.permissions ?? [])].sort(), ALLOWED_PERMISSIONS);
});

await check("host access is limited to the local backend", () => {
  assert.deepEqual([...(manifest.host_permissions ?? [])].sort(), [...ALLOWED_HOSTS].sort());
  const all = JSON.stringify(manifest);
  assert.ok(!all.includes("<all_urls>"), "never request <all_urls>");
});

await check("the content script runs only on github.com", () => {
  const matches = (manifest.content_scripts ?? []).flatMap((c) => c.matches);
  assert.deepEqual(matches, ["https://github.com/*"]);
});

await check("bundles contain no eval or Function constructor", async () => {
  for (const file of Object.keys(BUDGET_BYTES)) {
    const code = await readFile(join(dist, file), "utf8");
    assert.ok(!/\beval\(|new Function\(/.test(code), `${file} uses eval/new Function (blocked by MV3 CSP)`);
  }
});

console.log(`${passed} bundle checks passed.`);
