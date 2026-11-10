---
name: review-codebase-architecture
description: Review the architecture of any codebase and write a prioritized markdown report of friction points and improvement candidates. Use this skill whenever the user asks to review, assess, audit, or critique their codebase or project structure, mentions architectural tangles, tech debt, or messy code, asks "is my architecture any good" or "what should I refactor first", or hands over a repo, a folder, or pasted snippets and asks what is structurally wrong — even if they never say the word "architecture". It needs no git, no sub-agents, no particular files or directories, and adapts to whatever access you actually have.
---

# Review Codebase Architecture

Review a codebase for architectural friction and propose improvement candidates: refactors that turn shallow modules into deep ones, repair dependency direction, and make the code easier to test and to navigate — for humans and AI agents alike. The review is written to a markdown file; that file, not the chat, is the deliverable.

This skill is self-contained. It requires no other skills, no sub-agents, no git history, no particular files or directories (no glossary, no decision records, no docs folder), no OS-specific commands, and no internet. Whatever is missing, skip it gracefully and record the skip in the report. Whatever is present, use it.

## Ground rules

1. **The review lands in a markdown file.** The one exception: if you have no filesystem access at all, emit the identical report as a single fenced markdown block in chat and tell the user to save it as `ARCHITECTURE-REVIEW.md`.
2. **Check, never assume.** Before relying on any capability — reading files, writing them, running git, spawning helper agents, creating a directory — confirm you actually have it. Missing a capability is normal and fine; silently assuming it is the one failure this skill treats as a bug. Every skip goes into the report's limitations.
3. **Cite only what you saw.** Every path, module, and claim in the report must come from code you actually read. No guessed line numbers, no invented files. If your only input is the user's description or pasted snippets, say so and review that.
4. **Write one file.** The report — and the `docs/` directory holding it — is the only thing you create by default. Never touch source code; never create glossaries or decision records unless the user asks for them. If you run the code to gather evidence, keep the repo clean while you do it: run from a scratch location outside the repo, or with caches and output files suppressed, and say in the report how you ran it. If you cannot run it without dirtying the repo, don't run it — record that under limitations.
5. **Stop at the question.** The report proposes candidates; it does not design interfaces and it does not implement anything. It ends by asking the user which candidate to explore.

## Vocabulary — use these words exactly

Every finding and every suggestion is written in this vocabulary. It is what keeps the review precise; loose words produce loose reviews.

| Term | Meaning |
| --- | --- |
| **module** | Anything with an interface and an implementation. Deliberately scale-agnostic: a function, a class, a file, a package, a vertical slice. |
| **interface** | Everything a caller must know to use the module correctly: the signature, plus invariants, ordering constraints, error modes, required config, performance characteristics. |
| **depth** | Leverage at the interface: how much behaviour a caller or test can exercise per unit of interface it must learn. **Deep** = much behaviour behind a small interface. **Shallow** = interface nearly as complex as the implementation. |
| **seam** | A place where behaviour can be altered without editing in that place — the *location* of an interface. Where the seam goes is its own decision, separate from what sits behind it. |
| **adapter** | One concrete thing satisfying an interface at a seam. It names a role, not a kind: an in-memory fake and a database-backed module are both adapters. |
| **leverage** | What callers get from depth: more capability per unit of interface learned. |
| **locality** | What maintainers get from depth: changes, bugs, and verification stay in one place, so one fix covers every caller. |

Do not drift into "component", "service", "API", "boundary", or "unit" — unless you are quoting an actual name that appears in the code.

Alongside the depth lens, run the classic structural checks. Express what they find in the vocabulary above where you can — a god module is an extremely wide interface; a layering violation is a seam leak:

- **Dependency direction** — stable, low-level code (storage, utilities, infra) importing volatile, high-level code (domain, features). The arrow points backwards.
- **Layering** — callers crossing seams they should go around: an entry point reaching into storage details, a UI module talking to the database directly.
- **God modules** — one module with a huge interface, unrelated responsibilities, and fan-in from everywhere. Maximum width, minimum locality.
- **Cycles** — module A depending on B while B depends on A, directly or through a chain. Nothing in a cycle has a seam you can move independently.

### Principles

- **Depth is a property of the interface, not the implementation.** A deep module can be built internally from small swappable parts; callers just don't see them.
- **The deletion test.** Imagine deleting the module. If the complexity simply disappears, the module was a pass-through — shallow, and a candidate to inline or absorb. If the complexity reappears across every caller, the module was hiding real complexity — it is deep; keep it.
- **The interface is the test surface.** Callers and tests cross the same seam. If testing a module requires reaching past its interface, the module is the wrong shape, not the test.
- **One adapter means a hypothetical seam; two means a real one.** Don't cut a seam until something actually varies across it. A single-adapter seam is just indirection.

## Process

### 1. Orient — establish what you actually have

First, work out your access level — it shapes every later step and the report states it:

- **A** — you can read the codebase (fully or largely) and write files.
- **B** — you can read some files or folders, or the user pasted several.
- **C** — you have only the user's description or a handful of snippets.

Then scope before you scan. Deepening a module pays off by making future changes to it easier, so put extra weight on the parts of the codebase that are likely to change. Decide *where* to look before you look:

- If the user named a direction — a module, a subsystem, a pain point — take it and skip the inference below.
- Otherwise, if you can read commit history that actually covers the code under review — git or anything else — find the hot spots: the files and areas that keep coming up in commits, and let those pull your attention first. History that doesn't include these files (a parent repo that never tracked them, for instance) is no history: fall through to the next rule. If the changes are scattered with no clear hot spot, widen the net.
- If you have files but no history, infer: entry points, the modules with the most importers (fan-in), the biggest concentrations of code.
- At level C, review what you were given and list what you couldn't see.

Look briefly for documentation — a glossary, decision records (ADRs), a README, design notes. If you find some, use them: their terms name domain concepts, and their decisions mark ground not to re-litigate. If you find none, don't invent them — take your domain vocabulary from the code's own names. A brief look around is enough; don't hunt exhaustively.

If the codebase is large, prefer one subsystem done properly over a skim of everything, and say which subsystem and why.

### 2. Explore — walk the code, collect friction

Walk the code yourself, the way a new maintainer would. (If you happen to have sub-agents, you may send them exploring to save context — it is optional, and you verify what they report before citing it. If you don't have sub-agents, that is what this step expected all along.)

Explore organically; the lists below are signals to keep in mind, not a mandatory sweep:

*Deepening lens — where does it hurt?*

- Understanding one concept requires bouncing between many small modules.
- A module is shallow: its interface is nearly as complex as its implementation. Run the deletion test to check.
- Pure functions were extracted for testability, but the real bugs hide in how they are called — no locality.
- Tightly coupled modules leak across their seams.
- Parts of the codebase are untested, or hard to test through their current interface.

*Classic lens — where is the structure wrong?*

- Backwards dependency arrows (infra importing domain).
- Layer skips.
- A god module absorbing responsibilities that belong behind its callers' own interfaces.
- Import or dependency cycles.

For every suspicion, gather evidence before you keep it: the actual files and functions you read, the importers you found. Run the deletion test on anything you suspect is shallow.

You will notice more than is worth reporting. Keep **3–6 candidates** — the ones with real, observed friction in areas likely to change. A theoretical refactor in a corner nobody touches is not a candidate.

### 3. Write the review to a markdown file

Resolve the output location in this order:

1. The user already named a path or filename for the report — that wins; use it.
2. You can write files at the root of the codebase under review — create `docs/` there if it doesn't exist, and write `docs/architecture-review-YYYY-MM-DD.md`. When the codebase sits inside a larger workspace, the codebase the user handed you is the root, not the workspace. Use the current date if you know it; if you genuinely cannot establish a date, ask the user rather than guess.
3. Creating the directory fails (read-only root, permissions) — write `ARCHITECTURE-REVIEW.md` at the working directory.
4. You can write files but nowhere sensible — write wherever the user will find them, and tell them the absolute path.
5. No filesystem at all — emit the report as one fenced markdown block in chat, and tell the user to save it as `ARCHITECTURE-REVIEW.md`.

If the target file already exists, do not overwrite it — pick a distinguishing name or ask.

Use this exact report structure:

````markdown
# Architecture Review — <project> (<date>)

## Scope and method
What was reviewed, and how: access level (A/B/C), which capabilities
were actually available (git, sub-agents — or neither), how the focus area
was chosen, and what was NOT seen.

## Summary
Three to five sentences, the last naming the top recommendation: the
overall shape of the codebase, the biggest friction, how many candidates
follow.

## Candidates

### 1. <short, plain name for the change>
**Recommendation:** Strong | Worth exploring | Speculative
**Files:** <only files you actually read>
**Problem:** <the friction, in vocabulary terms, with evidence>
**Solution:** <plain English: what would change — no interface design>
**Benefits:** <locality, leverage, test-surface gains; how tests improve>
**Before / After:**
<ASCII sketch of the current structure>
<ASCII sketch of the proposed structure>

### 2. <name>
<repeat the card fields above for each candidate — 3–6 total, strongest first>

## Top recommendation
Which candidate to tackle first, and why: payoff, effort, risk.

## What I couldn't verify
Every limitation: no git history, partial access, inferences made,
anything you could not read or run.
````

While writing:

- **Domain words.** Name modules with the codebase's own domain vocabulary — "the Order intake module", not "the FooBarHandler" and not "the Order service". If you found a glossary, its terms take priority; check your wording against it.
- **Decision-record conflicts** — only if you actually found and read decision records. Surface a conflicting candidate only when the friction is real enough to justify revisiting the decision, and mark it clearly: "Conflicts with <record> — worth reopening because…". Don't list every refactor a record forbids.
- **Diagrams.** Plain-text box-and-arrow sketches in fenced code blocks: one sketch for Before, one for After. Draw only the modules involved in the candidate, not the whole repo. Keep each sketch under roughly 30 lines and 60 columns, and label the arrows with what crosses them. A good sketch beats three paragraphs of prose — be visual within plain text.
- **Recommendation strength** means exactly one of these, and nothing else:
  - **Strong** — the friction was concretely observed and evidenced; the fix is well understood and small-to-medium; the gain — in locality, leverage, or test surface — is clear enough that no design conversation is needed first; no decision-record conflict. A test-surface gain counts even where no tests exist yet: making tests possible is the gain.
  - **Worth exploring** — the friction is real, but the target shape or the blast radius is uncertain; the design conversation comes first.
  - **Speculative** — the gain is contingent on a future direction the codebase doesn't show yet; name what would have to become true.
- **No interfaces yet.** The Solution field stays plain English. Interface design happens in the discussion, after the user picks a candidate.

After writing the file, tell the user the absolute path, give a one-paragraph digest of the top recommendation, and ask: **"Which of these would you like to explore?"**

### 4. Discuss whichever candidate the user picks

Work the chosen candidate through together, as a design interview conducted in rounds:

- **Facts versus decisions.** Anything the code can answer is your job: go read the code instead of asking. Decisions belong to the user: put them as questions and wait. An agent that answers its own decision questions has broken this step.
- **Ask in rounds.** A few independent questions at a time, then wait for the answers. Never dump every question at once, and never ask a question whose meaning depends on an answer you don't have yet. Number the questions and put your recommended answer directly under each, so the user can reply "1 yes, 2 option b, 3 no".
- Cover, in whatever order the conversation demands: **constraints** (technical, deadlines, team), **dependencies** (what must move first), **the shape of the deepened module**, **what sits behind the seam** (which adapters exist, or which second adapter would make the seam real), **what tests survive the change** and which new ones the interface enables.
- A question without your recommendation attached is a missed opportunity; an interview that is all questions and no recommendations has failed.
- The session ends when the user confirms a shared understanding — or rejects the candidate, or defers. Do not start implementing; the deliverable of this skill is the review file plus a settled next step, not code.
- **Rejection with a load-bearing reason** — a reason a future review would actually need (not "not now", not the self-evident): offer to append it to the report under a "Rejected" heading so future reviews don't re-suggest the same thing. Skip ephemeral reasons.
- If new domain terms crystallize during the discussion, offer to note them in the report (or in the project's existing glossary, if it has one and the user agrees). Never create other project files unbidden.

## Appendix — one candidate card, annotated

This is what a finished card should read like (a fictitious review; the notes after it explain the choices):

````markdown
### 1. Break up the validation grab-bag
**Recommendation:** Worth exploring
**Files:** `src/validation.py` (412 lines, read in full); its importers,
found by search: 23 modules across `src/orders/`, `src/users/`,
`src/payments/`
**Problem:** One shallow module owns "validation" for the whole
codebase. Its interface is nearly as wide as the implementation —
deleting it would scatter its twenty helpers back into three domains
that each use only a third of them, so the deletion test says
pass-through, not deep. Each domain's tests must re-cover the others'
rules, and understanding "what makes an Order valid" means bouncing
between `src/orders/` and the grab-bag.
**Solution:** Move each domain's rules behind that domain's own
interface, as an Order-validation seam inside the Order module. The
grab-bag keeps only the genuinely shared primitives.
**Benefits:** Locality — Order validation and its tests live with the
Order module. Leverage — the Order interface gains validation behaviour
at near-zero added surface. The seam gets a natural second adapter:
a test fake per domain.
**Before / After:**

Before:
validation.py — one wide, shallow module
├── order rules ──── imported by src/orders/
├── user rules ───── imported by src/users/
├── payment rules ── imported by src/payments/
└── shared helpers ─ imported by all three

After:
src/orders/            src/users/        src/payments/
[Order module]         [User module]     [Payment module]
  own rules              own rules         own rules
  validate()             validate()        validate()
      │                      │                 │
      └──────────────────────┼─────────────────┘
                        validation.py
                 (shared primitives only — deep)
````

Why the card reads the way it does:

- **Files** names only files that were actually read, plus the importer search that produced the 23 — evidence, not vibes.
- **Problem** runs the deletion test out loud, ties the friction to the vocabulary (shallow, locality), and names the concrete pain (tests re-covering other domains' rules).
- **Solution** stays plain English — no signatures, no sketches of the new interface. That work happens after the user picks this candidate.
- **Benefits** speaks in locality and leverage, names the second adapter (which is what would make the seam real), and says how tests improve.
- The sketches draw only the modules in this candidate, label what crosses each arrow, and stay small — the Before shows one wide box feeding everyone; the After shows three deep modules over a slim shared core.
- **Worth exploring**, not Strong: the shape is clear, but 23 importer sites is a real blast radius — exactly the "target shape known, blast radius uncertain" case.
