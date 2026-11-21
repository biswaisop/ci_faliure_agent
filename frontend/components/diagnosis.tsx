"use client";

import { useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { ChevronRight, CircleX, FileCode2, FileText, GitPullRequest, Terminal } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";

const views = [
  { name: "Diagnosis", Icon: FileText },
  { name: "Job log", Icon: Terminal },
  { name: "Commit diff", Icon: FileCode2 },
] as const;

export function Diagnosis() {
  const [view, setView] = useState<(typeof views)[number]["name"]>("Diagnosis");
  const reduceMotion = useReducedMotion();

  return (
    <figure id="diagnosis" tabIndex={-1} aria-label="Interactive sample report" className="report">
      <Card className="report-card">
        <CardHeader className="report-header">
          <span><GitPullRequest aria-hidden="true" size={16} /> checkout-api <span className="text-muted">/</span> pull request #42</span>
          <Badge className="failure-badge"><CircleX aria-hidden="true" size={12} /> Failed</Badge>
        </CardHeader>
        <CardContent className="report-body">
          <div className="report-tabs" role="group" aria-label="Report views">
            {views.map(({ name, Icon }) => (
              <button key={name} type="button" aria-pressed={view === name} aria-controls="report-content" onClick={() => setView(name)}>
                <Icon aria-hidden="true" size={15} /> {name}
              </button>
            ))}
          </div>
          <div id="report-content" aria-live="polite" className="report-content">
            <motion.div
              key={view}
              className="report-view"
              initial={view !== "Diagnosis" && !reduceMotion ? { opacity: 0, y: 5 } : false}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: reduceMotion ? 0 : 0.2 }}
            >
              {view === "Diagnosis" ? (
                <>
                  <div className="report-tags">
                    <Badge className="failure-badge">Code bug</Badge>
                    <Badge className="confidence-badge">High confidence</Badge>
                  </div>
                  <h2>Empty carts skip validation</h2>
                  <p>Checkout returned 200 for an empty cart. The test expected 422.</p>
                  <div className="report-evidence">
                    <div><span>pytest.log:184</span><code className="text-failure">assert 200 == 422</code></div>
                    <div><span>checkout.py:61</span><code>Empty-cart guard removed</code></div>
                  </div>
                  <div className="report-action">
                    <ChevronRight aria-hidden="true" size={17} />
                    <p>Restore the guard and rerun <code>test_empty_cart</code>.</p>
                  </div>
                </>
              ) : view === "Job log" ? (
                <>
                  <h2>pytest output</h2>
                  <p>The assertion that failed in the checkout test.</p>
                  <pre className="report-code"><span>tests/test_checkout.py::test_empty_cart</span>{"\n"}<span className="text-failure">FAILED  [100%]</span>{"\n\n"}{"182  response = client.post('/checkout', json=[])\n183  assert response.status_code == 422\n"}<span className="text-failure">184  AssertionError: assert 200 == 422</span></pre>
                </>
              ) : (
                <>
                  <h2>checkout.py</h2>
                  <p>The commit removed the empty-cart check before checkout.</p>
                  <pre className="report-code"><span className="text-muted">@@ -60,5 +60,3 @@ def checkout(cart):</span>{"\n"}<span className="text-failure">{"-    if not cart.items:\n-        raise EmptyCartError()"}</span>{"\n     return process_checkout(cart)"}</pre>
                </>
              )}
            </motion.div>
          </div>
        </CardContent>
      </Card>
    </figure>
  );
}
