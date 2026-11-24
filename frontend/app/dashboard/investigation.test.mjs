import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const jsxLoader = registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("./") && context.parentURL?.endsWith(".jsx")) {
      const url = new URL(specifier, context.parentURL);
      for (const suffix of [".jsx", ".js"]) {
        const candidate = new URL(url.href + suffix);
        if (existsSync(candidate)) return nextResolve(candidate.href, context);
      }
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (!url.endsWith(".jsx")) return nextLoad(url, context);
    const { outputText } = ts.transpileModule(readFileSync(new URL(url), "utf8"), {
      fileName: "component.jsx",
      compilerOptions: { allowJs: true, jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ESNext },
    });
    return { format: "module", source: outputText, shortCircuit: true };
  },
});

const { Diagnosis } = await import("./investigation-diagnosis.jsx");
const { EvidenceExplorer } = await import("./investigation-evidence.jsx");
const { EvidenceCode } = await import("./evidence-code.jsx");
const { diffLines, evidenceAnchor, revealEvidence } = await import("./evidence-format.js");
jsxLoader.deregister();

const evidence = {
  failed_jobs: [], failure_packets: [{ job_id: "12", job_name: "pytest", relevant_log: "TypeError: test" }],
  changed_files: [], correlations: [], comparison_status: "no_previous_success", diff_available: false,
};

test("renders diagnosis hierarchy, distinct confidence, and safe evidence links", (t) => {
  const html = renderToStaticMarkup(createElement(Diagnosis, { status: "completed", evidence, diagnosis: {
    summary: "The checkout test raised a `TypeError` while calculating the invoice total. The failure interrupted the test before its output could be compared with the expected total.",
    root_cause: "A string was passed to `total()`.",
    confidence: "high", regression_attribution: "insufficient_evidence", suggested_actions: ["Check `<script>` input."],
    limitations: ["The failing line was visible in the log."], evidence_validated: true,
    evidence_ids: ["log:12"], unsupported_evidence_ids: ["diff:unknown.py"],
  } }));
  const sections = ["The checkout test raised", "Most likely root cause", "Failure mechanism confidence", "Regression attribution",
    "Suggested next actions", "Evidence references"];
  const positions = sections.map((label) => html.indexOf(label));
  assert.ok(positions.every((position, index) => position >= 0 && (index === 0 || position > positions[index - 1])));
  assert.match(html, />high<.*>insufficient evidence</);
  assert.match(html, /<ol\b.*&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /No earlier successful attempt was found/);
  assert.match(html, /diff:unknown\.py \(unavailable\)/);

  const id = evidenceAnchor("log:12");
  assert.match(html, new RegExp(`href="#${encodeURIComponent(id)}"`));
  let focused = false;
  let scrolled = false;
  const details = {
    open: false, querySelector: () => ({ focus: () => { focused = true; } }),
    scrollIntoView: ({ block }) => { scrolled = block === "start"; },
  };
  globalThis.document = { getElementById: (key) => key === id ? details : null };
  t.after(() => { delete globalThis.document; });
  revealEvidence(id);
  revealEvidence("unavailable-id");
  assert.equal(details.open && focused && scrolled, true);
});

test("renders comparison outcomes and the corresponding safe unified diff", () => {
  const unavailable = renderToStaticMarkup(createElement(EvidenceExplorer, { evidence }));
  assert.match(unavailable, /No earlier successful attempt was found/);
  const patch = "@@ -3,2 +3,2 @@\n line\n-<script>\n+safe";
  const file = { filename: "src/my file.py", status: "modified", patch };
  const available = renderToStaticMarkup(createElement(EvidenceExplorer, { evidence: {
    ...evidence, comparison_status: "available", diff_available: true, changed_files: [file],
  } }));
  assert.match(available, /Verified ancestor/);
  assert.ok(available.includes(`id="${evidenceAnchor(`diff:${file.filename}`)}"`));
  assert.match(available, /src\/my file\.py · modified/);
  const rows = diffLines(patch);
  assert.deepEqual(rows.map(({ tone, oldLine, newLine }) => [tone, oldLine, newLine]), [
    ["trace", undefined, undefined], ["context", 3, 3], ["removed", 4, undefined], ["added", undefined, 4],
  ]);
  const diff = renderToStaticMarkup(createElement(EvidenceCode, { text: patch, kind: "diff" }));
  assert.match(diff, /@@ -3,2 \+3,2 @@/);
  assert.match(diff, /-&lt;script&gt;/);
  assert.match(diff, /\+safe/);
});

test("highlights traceback and exception rows while preserving literal log content", () => {
  const log = `Traceback (most recent call last):\n  File "demo.py", line 4\nTypeError: <unsafe>\n`;
  const html = renderToStaticMarkup(createElement(EvidenceCode, { text: log, kind: "log" }));
  assert.match(html, /text-sky-200.*Traceback/);
  assert.match(html, /text-failure.*TypeError: &lt;unsafe&gt;/);
  assert.equal((html.match(/class="block min-h-5 border-l-2/g) || []).length, 4);
  assert.match(html, /Copy log/);
  const longLog = Array.from({ length: 120 }, (_, i) => `line ${i}`).join("\n");
  const preview = renderToStaticMarkup(createElement(EvidenceCode, { text: longLog, kind: "log" }));
  assert.match(preview, /Show all 120 lines/);
  assert.doesNotMatch(preview, /line 119/);
});

test("renders the complete failed-job log as separate rows in one scroll pane", () => {
  const text = Array.from({ length: 510 }, (_, i) => `output_${i + 1}`).join("\r\n");
  const html = renderToStaticMarkup(createElement(EvidenceCode, { text, kind: "log", full: true }));
  assert.match(html, /Full job log · 510 lines/);
  assert.match(html, /output_500/);
  assert.match(html, /output_510/);
  assert.equal((html.match(/class="block min-h-5 border-l-2/g) || []).length, 510);
  assert.doesNotMatch(html, /Next lines|Show all|Show full job log/);
});
