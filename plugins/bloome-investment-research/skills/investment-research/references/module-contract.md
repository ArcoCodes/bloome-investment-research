# Research Module Contract

A module is one bounded evidence question, not a report chapter. The parent creates 3–5 non-overlapping modules after a landscape search and records them in `plan.json`:

```json
{
  "topic": "research topic",
  "modules": [{
    "id": "demand",
    "question": "What changes demand?",
    "scope": "Included and excluded subjects",
    "support_hypothesis": "Evidence that would support the thesis",
    "challenge_hypothesis": "Evidence that would challenge it",
    "evidence_needed": ["metric or source needed"],
    "query_seeds": ["bounded search seed"]
  }]
}
```

IDs must be unique filesystem-safe strings. Questions, scopes, support hypotheses, and challenge hypotheses must be distinct across modules.

Each worker handles exactly one module and writes only `modules/<id>.md`. Use these headings:

```markdown
# Direct answer
# Claim–evidence pairs
# Metrics
# Conflicts and date reconciliation
# Invalidating conditions
# Remaining gaps
```

Every material claim and number needs an exact source citation and enough evidence fields for the parent to merge it into `evidence.json`: stance, kind, corpus, chunk ID, report ID, exact quote, source type, title, source path, publication date, and page or line locator. Seek both support and challenge evidence. Prefer the newest item for conflicts within the same evidence chain; preserve independent disagreement.

Workers must not write an executive summary, chapter, outline, `evidence.json`, or final report. Their response to the parent contains only a short direct answer and the memo path; the memo retains the full evidence detail.
