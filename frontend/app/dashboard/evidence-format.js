const comparisonMessages = {
  available: "GitHub confirmed the earlier passing commit is an ancestor of the failed commit.",
  partial: "The baseline is verified. Some changed files or patch text may be missing.",
  no_previous_success: "No earlier successful attempt was found for this workflow on this branch.",
  same_commit: "An earlier attempt passed on the same commit, so there is no change to compare.",
  baseline_not_ancestor: "The earlier passing commit is outside the failed commit’s ancestry.",
  comparison_failed: "GitHub could not complete the commit comparison.",
  not_attempted: "No commit comparison was recorded for this attempt.",
};

const reasonMessages = {
  branch_unavailable: "The failed attempt has no branch, so there is no safe baseline search.",
  failure_time_unavailable: "The failed attempt’s completion time is unavailable.",
  history_limit: "GitHub’s workflow history search reached its result limit before finding a baseline.",
  github_rate_limited: "GitHub rate-limited the comparison or history request.",
  github_access_denied: "GitHub denied access to workflow history or commit contents.",
  commit_unavailable: "GitHub could not compare the commits. A commit may be missing or inaccessible.",
  github_unavailable: "GitHub workflow history or commit comparison was temporarily unavailable.",
  invalid_response: "GitHub’s response did not establish a usable comparison.",
};

export function comparisonLabel(evidence) {
  const status = evidence.comparison_status || "not_attempted";
  if (status === "not_attempted" && evidence.diff_available) return "Comparison saved in the earlier evidence format.";
  return reasonMessages[evidence.comparison_reason] || comparisonMessages[status] || comparisonMessages.not_attempted;
}

export function evidenceAnchor(reference) {
  return `evidence-${encodeURIComponent(reference)}`;
}

export function revealEvidence(id) {
  const target = document.getElementById(id);
  if (!target) return;
  target.open = true;
  target.querySelector("summary")?.focus({ preventScroll: true });
  target.scrollIntoView({ block: "start" });
}

export function logLineTone(line) {
  if (/^\s*(?:>\s*)?File ["'].*["'], line \d+|^\s*Traceback \(most recent call last\):/i.test(line)) return "trace";
  if (/(?:^\s*E\s+|\b\w*(?:Error|Exception):|\b(?:ERROR|FAILED|FATAL)\b|::error\b)/.test(line)) return "error";
  return "context";
}

export function diffLines(text) {
  let oldLine = null;
  let newLine = null;
  return text.split("\n").map((line) => {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
    if (hunk) {
      oldLine = Number(hunk[1]);
      newLine = Number(hunk[2]);
      return { line, tone: "trace" };
    }
    if (oldLine === null) return { line, tone: "context" };
    if (line.startsWith("+")) return { line, tone: "added", newLine: newLine++ };
    if (line.startsWith("-")) return { line, tone: "removed", oldLine: oldLine++ };
    if (line.startsWith(" ")) return { line, tone: "context", oldLine: oldLine++, newLine: newLine++ };
    return { line, tone: "context" };
  });
}
