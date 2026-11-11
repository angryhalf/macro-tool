---
name: code-review
description: Multi-axis code review with an ambitious structural bar. Use before merging any change; when reviewing code written by yourself, another agent, or a human; when asked to review a diff or pull request (even pasted inline); after any bug fix (review the fix and its regression test); and for deep, harsh, strict, or "thermonuclear" maintainability audits. Evaluates correctness, readability, architecture, security, and performance — and pushes for dramatic structural simplification, not just local cleanup.
---

# Code Review

## Standard

Every change is reviewed before merge — no exceptions. Review five axes: **correctness, readability, architecture, security, performance**.

**Approve** when the change definitely improves overall code health, even if imperfect. Don't block because it isn't how you would have written it; if it improves the codebase and follows conventions, approve. Perfection isn't the bar — improvement is.

**One exception:** structural regressions are presumptive blockers (see Approval Bar).

## Stance

Be ambitious about structure. Don't stop at "this could be a bit cleaner" — hunt for **code judo**: restructurings that keep behavior but delete whole branches, helpers, modes, conditionals, or layers. Prefer the solution that feels inevitable in hindsight; deleting complexity beats rearranging it.

Prefer direct, boring, maintainable code over clever or magical code. A rename suggestion is never a substitute for fixing a structural problem. A "cleaner version of the same messy idea" is not the goal when a much simpler idea is plausible.

Tone: direct, serious, demanding — never rude. Say plainly when a change makes the codebase messier or misses a dramatic simplification. Useful phrasings:

- "this pushes the file past 1k lines — can we decompose first?"
- "i think there's a code-judo move here: if we [X], these branches disappear."
- "this feels like feature logic leaking into a shared path — can we isolate it?"
- "why does this need a cast/optional here? can we make the boundary explicit instead?"

## The Five Axes

### 1. Correctness — does it do what it claims?

- Matches spec/task; edge cases handled (null, empty, boundary); error paths, not just the happy path
- Tests pass and test the right things; watch off-by-one, race conditions, state inconsistencies

### 2. Readability & Simplicity — understandable without the author?

- Descriptive names (no bare `temp`/`data`/`result`); straightforward control flow; related code grouped
- Fewer lines where possible — 1000 lines where 100 suffice is a failure
- Abstractions must earn their complexity: don't generalize before the third use case
- Comments only for non-obvious intent; no dead artifacts (`_unused`, compat shims, `// removed` comments)
- No "clever" tricks; no generic "magic" mechanisms that hide simple data-shape assumptions
- A conditional bolted onto an unrelated flow is a design smell, not a nit — push the logic into its own helper, state, or policy
- Repeated conditionals on the same shape signal a missing model or dispatcher; a "temporary" branch is usually permanent debt
- Watch for one-off booleans, nullable modes, or flags complicating existing control flow, and edge-case handling wedged into the middle of an already busy function

### 3. Architecture — does it fit the system?

- Follows existing patterns (justify new ones); clean module boundaries; no circular dependencies; duplication shared; right abstraction level
- **Refactors must reduce complexity, not relocate it.** Count the concepts a reader must hold; if the count is unchanged, it isn't cleaner. Prefer the version where whole branches, modes, or layers disappear; prefer deleting an abstraction to polishing it.
- Feature logic stays out of shared/general-purpose modules; put logic in the layer/package that owns the concept; reuse the canonical helper instead of a near-duplicate; don't normalize architectural drift
- Explicit type boundaries: question gratuitous `any`/`unknown`/optionals/casts and silent fallbacks that paper over unclear invariants
- Watch a previously cohesive module become more coupled, more stateful, or harder to scan
- **Orchestration:** independent work serialized for no reason → ask for parallel; related updates that can leave state half-applied → push for atomic. Flag avoidable orchestration complexity, but don't over-index on micro-optimizations.

### 4. Security (depth: `security-and-hardening`)

- User input validated and sanitized; external data (APIs, logs, user content, config) treated as untrusted and validated at system boundaries before use in logic or rendering
- Secrets out of code, logs, and version control; authn/authz checked; SQL parameterized; outputs encoded (XSS); dependencies trusted and vulnerability-free

### 5. Performance (depth: `performance-optimization`)

- No N+1 queries, unbounded loops/fetches, or large objects in hot paths
- Sync → async where needed; no unnecessary re-renders; pagination on list endpoints

## Structural Remedies

Propose the move, not just the problem — a review that only says "this is complex" leaves the author guessing. Prefer the remedy that removes moving pieces:

| Instead of... | Do this |
|---|---|
| A chain of conditionals | Typed model or explicit dispatcher |
| Duplicate branches | Collapse into one clearer flow |
| Tangled flow | Separate orchestration from business logic |
| Feature logic in a shared module | Move it to the owning package / behind its own abstraction |
| A bespoke near-duplicate | Reuse the canonical helper |
| Silent fallbacks | Make the type boundary explicit |
| A pass-through wrapper | Delete it |
| Special cases | A simpler default flow with fewer exceptions |
| A messy conditional model | Reframe the state model or ownership boundary so the conditionals disappear |
| A sprawling file | Extract helpers, subcomponents, or modules |

## Process

1. **Context.** What is this change trying to accomplish? What spec or task, and what expected behavior change?
2. **Tests first.** Do tests exist, test behavior (not implementation), cover edge cases, have descriptive names, and actually catch regressions? Verify the last one **by experiment, not reading**: invert one condition the change adds (drop a negation, swap `&&` for `||`), run the suite, restore from a copy. A mutation that stays green is a finding — name the missing test. (Project-wide mutation scores: `constraint-driven-development`.)
3. **Implementation.** Walk each changed file against the five axes.
4. **Categorize.** Check every finding against the callers, guards, and tests that reach the changed code — drop what they rule out. Label pre-existing defects separately from regressions this change introduces. Zero findings is a valid result; don't invent one.
5. **Verify the verification.** What tests were run, did the build pass, manual testing, screenshots for UI, before/after comparison?

**Severity labels** — so authors know what's required vs optional:

| Label | Meaning | Author action |
|---|---|---|
| *(no prefix)* | Required change | Must address before merge |
| **Critical:** | Blocks merge | Security vulnerability, data loss, broken functionality |
| **Nit:** | Minor, optional | May ignore — formatting, style |
| **Optional:/Consider:** | Suggestion | Worth considering, not required |
| **FYI** | Informational | No action — future context |

**Order findings by leverage:** correctness & security → structural regressions → missed code-judo opportunities → spaghetti/branching growth → boundary/type problems → file size → modularity → legibility/nits. A few high-conviction comments beat a long list; if you have one structural problem and ten nits, the structural problem *is* the review.

## Change Hygiene

**Sizing:** ~100 changed lines = good (one sitting); ~300 = acceptable if a single logical change; ~1000 = too large, split it. One change = one self-contained modification, its related tests, and a functional system after submission — one part of a feature, not the whole feature.

**File size, not just diff size.** A small diff can push a file past ~1000 *total* lines — an inspection signal, not a hard cap. When a change materially grows an already-large file, extract helpers/subcomponents/modules *first*, then add. Never push a file from under 1k to over 1k lines without a compelling structural reason and a clearly organized result.

**Splitting strategies:** *Stack* (small change, next based on it — sequential deps) · *By file group* (different reviewers — cross-cutting) · *Horizontal* (shared code/stubs first, then consumers — layered) · *Vertical* (full-stack slices — feature work). Large changes are acceptable only for complete file deletions and automated refactors, where the reviewer verifies intent rather than every line.

**Refactor ≠ feature.** A change that refactors and adds new behavior is two changes — submit separately. Small cleanups (renames) at reviewer discretion.

**Descriptions.** First line: short, imperative, standalone ("Delete the FizzBuzz RPC" not "Deleting the…"), informative enough to find in history without the diff. Body: what and why — context, decisions, reasoning not visible in code, links to bugs/benchmarks/design docs, known shortcomings. Anti-patterns: "Fix bug," "Fix build," "Add patch," "Moving code from A to B," "Phase 1," "Add convenience functions."

**Dead code.** After refactors, identify orphaned code, list it explicitly, and ask before deleting ("Should I remove: `formatLegacyDate()`, `OldTaskCard`, `LEGACY_API_URL` — no remaining references?"). Don't leave it lying around; don't silently delete what you're unsure about.

## Dependencies

**Before adding:** Does the existing stack solve it (often yes)? Bundle size? Actively maintained? Known vulnerabilities (`npm audit`)? Compatible license? Prefer the standard library and existing utilities — every dependency is a liability.

**Upgrading** is a code change like any other, and riskiest in bulk:

1. Read the changelog, not the version number — semver is a promise the maintainer may not have kept; for majors, read the migration notes.
2. One dependency per change — obvious cause, clean revert.
3. Let the tests decide: green suite before *and* after. Thin coverage around the dependency is the real finding — add a test first.
4. Review the lockfile diff, not just `package.json` — one direct bump can pull in dozens of transitive changes.
5. Keep the lockfile committed and never hand-edit it.

Supply-chain verdicts (audit triage, typosquatting, compromised maintainers): `security-and-hardening`. This section covers the upgrade *workflow*.

## Etiquette

- **Speed:** respond within one business day (maximum, not target); respond shortly after requests arrive — deep focus is the only exception — so multiple rounds complete in a day; prioritize fast individual responses over quick final approval; ask authors to split large changesets rather than grind through one massive review.
- **Disagreements:** technical facts/data > style guides (absolute authority on style) > engineering principles > codebase consistency (only if health doesn't degrade). Don't accept "I'll clean it up later" — deferred cleanup rarely happens; require it pre-merge unless a genuine emergency, otherwise a filed, self-assigned bug.
- **Honesty:** no rubber-stamp "LGTM" without evidence of review; don't soften real issues ("this might be a minor concern" about a production bug is dishonest); quantify ("this N+1 adds ~50ms per item"); sycophancy is a failure mode — push back on clear problems; accept override gracefully when the author has full context; comment on code, not people.
- **Multi-model review:** A writes → B reviews (correctness, architecture, conventions) → A addresses → human decides. Different models have different blind spots. Example prompt: "Review this change for correctness, security, and adherence to project conventions. The spec says [X]. Flag issues as Critical, Required, Optional, or Nit."

## Rationalizations

| Rationalization | Reality |
|---|---|
| "It works, that's good enough" | Working code that's unreadable, insecure, or architecturally wrong compounds debt. |
| "I wrote it, so I know it's correct" | Authors are blind to their own assumptions. |
| "We'll clean it up later" | Later never comes. Require cleanup before merge. |
| "AI-generated code is probably fine" | AI code needs *more* scrutiny — confident and plausible even when wrong. |
| "The tests pass, so it's good" | Tests miss architecture, security, and readability problems. |
| "The refactor makes it cleaner" | Relocated complexity isn't reduced complexity. |
| "It's only a small addition to this file" | Small diffs still bloat files and tangle flows. Judge the resulting structure. |
| "It's just a version bump" | A bump is behavior change you didn't write. Read the changelog. |
| "I'll upgrade everything in one PR to save time" | Bulk bumps hide the culprit. One dependency per change. |

## Approval Bar

Do not approve merely because behavior seems correct. Approve when the change improves overall health **and**:

- No structural regression, and no obvious missed code-judo opportunity when a simpler path is visible
- No unjustified file-size explosion; no spaghetti growth from special-case branching
- No hacky or magical abstraction; no wrapper/cast/optionality churn obscuring the design
- No architecture-boundary leak; no duplication of a canonical helper or logic in the wrong layer

**Presumptive blockers** — surface them and propose the simpler design; escalate to Required only when the change actively makes structure worse:

- Incidental complexity preserved when a plausible judo move would delete it
- A file crossing from under 1000 to over 1000 lines
- Ad-hoc branching that tangles an existing flow, or feature checks scattered across shared code
- An unnecessary abstraction, wrapper, or cast-heavy contract
- A near-duplicate of an existing canonical helper, or logic in the wrong layer

If the bar isn't met, leave explicit, actionable feedback and push for the cleaner decomposition.

## Checklist

```markdown
- [ ] Context understood; tests reviewed first (mutation check run)
- [ ] Correctness: spec match, edge cases, error paths, real coverage
- [ ] Readability: names, flow, no dead artifacts, no magic
- [ ] Architecture: complexity reduced not moved; right layer; canonical helpers; file size sane; orchestration parallel/atomic where it should be
- [ ] Security: validated inputs, no secrets, parameterized SQL, encoded output, authz
- [ ] Performance: no N+1, no unbounded ops, pagination
- [ ] Findings severity-labeled, leverage-ordered, deduplicated against callers/guards
- [ ] Description: imperative first line, substantive body
- [ ] Verification: tests green, build passes, story documented, dependency/lockfile discipline followed
- [ ] Verdict: Approve / Request changes
```
