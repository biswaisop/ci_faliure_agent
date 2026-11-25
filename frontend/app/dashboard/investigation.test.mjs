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

const { Diagnosis, ReportMarkdown, readMarkdown } = await import("./investigation-diagnosis.jsx");
const { EvidenceCode } = await import("./evidence-code.jsx");
const { SuggestedFix } = await import("./suggested-fix.jsx");
const { diffLines } = await import("./evidence-format.js");
jsxLoader.deregister();

const evidence = {
  failed_jobs: [], failure_packets: [{ job_id: "12", job_name: "pytest", relevant_log: "TypeError: test" }],
  changed_files: [], correlations: [], comparison_status: "no_previous_success", diff_available: false,
};

test("shows completed report loading and evidence references without explorer links", () => {
  const html = renderToStaticMarkup(createElement(Diagnosis, { status: "completed", evidence, diagnosis: {
    evidence_validated: true,
    evidence_ids: ["log:12"], unsupported_evidence_ids: ["diff:unknown.py"],
  } }));
  assert.match(html, /Loading investigation report/);
  assert.match(html, /Evidence references/);
  assert.match(html, /No earlier successful attempt was found/);
  assert.match(html, /diff:unknown\.py \(unavailable\)/);

  assert.match(html, /<span>log:12<\/span>/);
  assert.doesNotMatch(html, /href="#evidence-/);
});

test("parses Markdown headings, code and lists without explorer links", () => {
  const filename = "next.config.mjs";
  const text = [
    "### What failed\n\n`Setup Pages` failed while running `actions/configure-pages@v5`.",
    "### Likely cause\n\nThe action could not rewrite a function export.",
    `### Suspected file\n\n[${filename}](#evidence-diff%253Anext.config.mjs)`,
    "### Suggested next actions\n\n1. Check `<script>` input.",
    "[external](https://example.test/) ![image](https://example.test/track) <script>unsafe()</script>",
  ].join("\n\n");
  const html = renderToStaticMarkup(createElement(ReportMarkdown, { text }));
  assert.match(html, /<h3>Likely cause<\/h3>/);
  assert.match(html, /<code>Setup Pages<\/code> failed while running <code>actions\/configure-pages@v5<\/code>/);
  assert.match(html, /<ol>\s*<li>Check <code>&lt;script&gt;<\/code>/);
  assert.doesNotMatch(html, /<script>|<img|example\.test/);
  assert.match(html, /&lt;script&gt;unsafe\(\)&lt;\/script&gt;/);
  assert.match(html, /<p>next\.config\.mjs<\/p>/);
  assert.doesNotMatch(html, /href="#evidence-/);
});

test("opens the suspected file's saved diff and failed-job excerpts from the report", () => {
  const filename = "next.config.mjs";
  const text = `### Suspected file\n\n\` ${filename} \`\n\nChanged \`other.ts\` too.`;
  const files = [
    { filename, patch: "@@ -1 +1 @@\n-export default config\n+export default function config(phase) {" },
    { filename: "other.ts", patch: "+unrelated edit" },
  ];
  const html = renderToStaticMarkup(createElement(ReportMarkdown, {
    text, diagnosis: { suspected_file: filename }, evidence: { ...evidence, changed_files: files },
  }));
  assert.match(html, /<details class=.*?<summary[^>]*>.*?next\.config\.mjs.*?<\/summary>/s);
  assert.match(html, /aria-label="Unified diff"/);
  assert.match(html, /\+export default function config\(phase\)/);
  assert.match(html, /aria-label="Reduced job log"/);
  assert.match(html, /TypeError: test/);
  assert.doesNotMatch(html, /unrelated edit/);

  const missing = renderToStaticMarkup(createElement(ReportMarkdown, {
    text, evidence: { ...evidence, logged_changes: [{ path: filename }], failure_packets: [] },
  }));
  assert.match(missing, /<summary[^>]*>.*?next\.config\.mjs/);
  assert.match(missing, /No diff was saved/);
  assert.match(missing, /No failure log excerpts were collected/);
});

test("reads Markdown incrementally without corrupting characters split across chunks", async () => {
  const bytes = new TextEncoder().encode("### What failed\n\nCafé");
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(bytes.slice(0, -1));
      controller.enqueue(bytes.slice(-1));
      controller.close();
    },
  });
  const updates = [];
  await readMarkdown(stream, (text) => updates.push(text));
  assert.deepEqual(updates, ["### What failed\n\nCaf", "### What failed\n\nCafé"]);
});

test("waits for analysis to finish before displaying the report", () => {
  const html = renderToStaticMarkup(createElement(Diagnosis, { status: "running", diagnosis: {}, evidence }));
  assert.match(html, /AI is analyzing/);
  assert.doesNotMatch(html, /Loading investigation report/);
});

test("renders a safe unified diff", () => {
  const patch = "@@ -3,2 +3,2 @@\n line\n-<script>\n+safe";
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

test("renders untested suggestions and the authorized comment link", () => {
  const html = renderToStaticMarkup(createElement(SuggestedFix, {
    fix: { status: "suggested", suggestion: {
      summary: "Fix addition", rationale: "The reported result has the wrong sign.",
      changes: [{
        path: "src/math.py", original_snippet: "return a - b", replacement_snippet: "return a + b",
        explanation: "Use addition.",
      }],
      risks: ["A caller may rely on subtraction."], tests_to_run: ["pytest tests/test_math.py"],
    } },
    publication: { status: "updated", comment_url: "https://github.com/sample/project/pull/4#issuecomment-2" },
  }));
  assert.match(html, /Not tested or applied/);
  assert.match(html, /-return a - b/);
  assert.match(html, /\+return a \+ b/);
  assert.match(html, /pytest tests\/test_math\.py/);
  assert.match(html, /PR comment updated/);
  assert.match(html, /href="https:\/\/github\.com\/sample\/project\/pull\/4#issuecomment-2"/);
  assert.doesNotMatch(html, /<button[^>]*>Apply/);
});
