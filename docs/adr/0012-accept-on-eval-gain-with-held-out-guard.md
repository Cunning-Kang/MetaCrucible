# Accept revisions on eval gain with held-out guard

A revision is accepted when the eval split shows at least one binary improvement (`FAIL→PASS`) with no binary regression (`PASS→FAIL`), and the held-out split shows no binary regression. Optional score gates may require epsilon-based score improvement or non-regression, but scores never override binary regressions.

Static review is not the primary acceptance gate, but review profiles that are required by policy or triggered by risk can block acceptance when they produce blocking findings or `BLOCKED` results. Routing revisions and secret/privacy-risk revisions therefore need their triggered static reviews to pass before a candidate can be kept.
