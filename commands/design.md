# /design — Structured Design Thinking

Force structured thinking before implementation. Produce a design document, not code.

## Invocation

`/design <topic>`

If no topic is given, ask: "What are we designing?"

## Instructions

### Step 1: Understand the Problem

Read available context to understand the design space:
- Current repo state (if relevant)
- Project files (if this relates to an active project)
- Existing code (if modifying something that exists)

### Step 2: Produce Design Document

```
## Design: <topic>

### Problem
<What needs to be solved and why. Be specific — not "we need X" but "Y is broken because Z, causing W.">

### Constraints
- <Hard constraints — must satisfy>
- <Soft constraints — prefer to satisfy>

### Options

**Option A: <name>**
<How it works. Pros. Cons.>

**Option B: <name>**
<How it works. Pros. Cons.>

### Recommendation
<Which option and why. Be direct.>

### Implementation Sketch
<Key steps, not code. What to build, in what order, what to test.>

### Risks
- <What could go wrong and how to detect/mitigate>
```

## Rules

- **No file writes. No code changes.** This skill produces a design document only.
- **Best-effort first pass.** Use available context to produce a useful design. Only ask clarifying questions when ambiguity would materially change the recommendation. Do not ask "what are the requirements?" — infer from context and state assumptions explicitly.
- **Minimum two options.** Even if one is obviously better — showing alternatives validates the choice.
- **Scale to the problem.** A one-paragraph design is fine for a simple problem. A multi-page design is fine for a complex one. Don't pad, don't truncate.
- **Reference existing patterns.** Check per-repo CLAUDE.md guardrails and contextual rules before proposing something that contradicts an established pattern.
- **Implementation sketch is steps, not code.** "Add a migration file, update the router, test the endpoint" — not the actual SQL or Python.
- **State assumptions.** If you're guessing about something, say so. "Assuming the API is already deployed" is better than silently building on that assumption.
