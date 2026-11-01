import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import { join } from "node:path";
import { build } from "vite";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const project = fileURLToPath(new URL("../", import.meta.url));
const dir = await mkdtemp(join(project, ".panel-tests-"));
try {
  await build({ configFile: false, root: project, logLevel: "error", build: {
    outDir: dir, emptyOutDir: true, minify: false,
    lib: { entry: join(project, "tests/entry.ts"), formats: ["es"], fileName: () => "panel.mjs" },
    rollupOptions: { external: ["react", "react/jsx-runtime"] },
  }, esbuild: { jsx: "automatic" } });
  const { Idle, Panel, present, safeHref, sourceLocation, scenario } = await import(pathToFileURL(join(dir, "panel.mjs")).href);
  const saved = JSON.parse(await readFile(join(project, "../eval/examples/panel_google_verified_pr.json"), "utf8"));
  const render = (result, error = null) => renderToStaticMarkup(React.createElement(Panel, {
    result, error, dark: false, startCollapsed: false, onRefresh() {}, onClose() {}, onVote() {},
  }));
  let passed = 0;
  function check(name, test) { test(); passed++; console.log(`✓ ${name}`); }

  check("transport errors do not show a running spinner", () => {
    assert.equal(present(null, "Offline").state, "error");
    assert.match(render(null, "Offline"), /Analysis unavailable/);
    assert.doesNotMatch(render(null, "Offline"), /class="spinner"/);
  });
  check("partial analysis shows blockers and does not blame the reporter", () => {
    const result = scenario("partial");
    const html = render(result);
    assert.equal(present(result, null).state, "partial");
    assert.match(html, /Analysis incomplete/); assert.match(html, /What blocked verification/);
    assert.match(html, /report itself may already contain enough/);
    assert.match(html, /No assessment was produced/); assert.doesNotMatch(html, />Checking</);
  });
  check("outcome counts include unassessed extracted claims and ignore orphan assessments", () => {
    const result = scenario("complete");
    result.assessments = [result.assessments[0], { ...result.assessments[0], claim_id: "orphan" }];
    assert.deepEqual(present(result, null).counts, { supported: 1, contradicted: 0, inconclusive: 2 });
    result.claims.reverse();
    assert.equal(present(result, null).claims[0].is_primary, true);
  });
  check("technical support and execution remain independent", () => {
    const result = scenario("complete");
    assert.equal(present(result, null).execution, "Not run");
    assert.match(render(result), /Not executed/);
    const reproduced = scenario("reproduced");
    assert.match(render(reproduced), /Execution evidence/);
    assert.match(present(reproduced, null).execution, /1.*reproduced/);
  });
  check("not reproduced is distinct from contradicted", () => {
    const result = scenario("reproduced");
    result.assessments[0].verification = "not_reproduced";
    result.assessments[0].technical = "inconclusive";
    assert.match(present(result, null).execution, /not reproduced/);
    assert.equal(present(result, null).counts.contradicted, 0);
  });
  check("a stopped run with execution evidence does not incorrectly say code was never run", () => {
    const result = scenario("reproduced"); result.status = "partial"; result.assessments = [];
    assert.match(present(result, null).execution, /Execution recorded/);
    assert.equal(present(result, null).counts.inconclusive, 1);
  });
  check("repository and language names do not change outcome derivation", () => {
    for (const [owner, repo] of [["team", "typescript"], ["org", "rust"], ["other", "java"], ["社区", "go-swift-cpp"]]) {
      const result = scenario("complete"); result.target.owner = owner; result.target.repo = repo;
      assert.deepEqual(present(result, null).counts, { supported: 1, contradicted: 1, inconclusive: 1 });
    }
  });
  check("saved-result links cannot execute script or embed credentials", () => {
    for (const url of ["javascript:alert(1)", "data:text/html,hello", "//example.com", "https://user:password@example.com"]) assert.equal(safeHref(url), undefined);
    assert.equal(safeHref("https://github.com/example/repo/blob/abc/file.go#L1"), "https://github.com/example/repo/blob/abc/file.go#L1");
    const result = scenario("complete"); result.target.url = "javascript:alert(1)";
    result.evidence.forEach((e) => { e.permalink = "javascript:alert(2)"; });
    assert.doesNotMatch(render(result), /href="javascript/);
  });
  check("long source paths, inline code and HTML-like report text stay intact and escaped", () => {
    const result = scenario("long"); result.claims[0].text += " <img src=x onerror=alert(1)>";
    const html = render(result);
    assert.match(html, /<code>HandleConnection<\/code>/);
    assert.match(html, /多言語/); assert.match(html, /&lt;img/); assert.doesNotMatch(html, /<img|<script>/);
    assert.match(html, /&lt;script&gt;/);
    assert.equal(sourceLocation("a/b.go", 2, 5), "a/b.go:2–5");
  });
  check("empty and feature reports render with explicit scope", () => {
    assert.match(render(scenario("empty")), /No assessable claims/);
    assert.match(render(scenario("feature")), /Not a bug report/);
    assert.match(render(scenario("feature")), /Feature feasibility/);
  });
  check("missing evidence IDs are disclosed rather than fabricated", () => {
    const result = scenario("complete"); result.assessments[0].evidence_ids = ["nonexistent"];
    const html = render(result);
    assert.match(html, /No evidence cited/); assert.match(html, /Some cited records are unavailable/);
  });
  check("identical blockers are deduplicated without hiding distinct next steps", () => {
    const result = scenario("partial"); const blocker = result.blockers[0];
    result.blockers.push({ ...blocker }, { ...blocker, next_step: "A different next step" });
    assert.equal(present(result, null).blockers.length, 2);
  });
  check("real saved API output preserves its mixed certainty, source snapshot and model provenance", () => {
    const html = render(saved);
    assert.deepEqual(present(saved, null).counts, { supported: 2, contradicted: 0, inconclusive: 3 });
    assert.equal(present(saved, null).execution, "Not run");
    assert.match(html, /The diff backs the main claim/);
    assert.match(html, /gemma-4-26b-a4b-it/); assert.match(html, /v29/);
    assert.match(html, new RegExp(saved.snapshot.sha));
    assert.match(html, /Source context/);
  });
  check("the side panel explains what to do when no issue or PR is open", () => {
    const html = renderToStaticMarkup(React.createElement(Idle, { dark: true, message: "Reload the GitHub tab." }));
    assert.match(html, /Open an issue or pull request/); assert.match(html, /Nothing is posted to GitHub/);
    assert.match(html, /class="root dark"/); assert.match(html, /Reload the GitHub tab/);
  });
  check("the suggested label tile shows evidence labels, says None otherwise, and is absent for PRs", () => {
    assert.match(render(scenario("complete")), /Suggested label.*Confirmed/s);
    assert.match(render(scenario("feature")), /Suggested label.*Invalid/s);
    assert.match(render(scenario("partial")), /Suggested label.*None/s);
    assert.doesNotMatch(render(scenario("long")), /Suggested label/);
    const leaning = scenario("complete");
    leaning.suggested_label = { label: "cannot_reproduce", basis: "leaning", reasons: ["intermittent"] };
    assert.doesNotMatch(render(leaning), /Cannot reproduce/);
  });
  check("the panel header offers refresh and settings, not page-overlay controls", () => {
    const html = renderToStaticMarkup(React.createElement(Panel, {
      result: scenario("complete"), error: null, dark: false, onRefresh() {}, onVote() {}, onSettings() {},
    }));
    assert.match(html, /aria-label="Run analysis again"/); assert.match(html, /aria-label="Settings"/);
    assert.doesNotMatch(html, /Collapse assessment|Hide assessment on this page/);
  });
  console.log(`${passed} panel checks passed (offline; no model or GitHub calls).`);
} finally { await rm(dir, { recursive: true, force: true }); }
