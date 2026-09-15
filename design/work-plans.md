# Work plans

**Date:** 2026-08-16
**Reproduced in part.** The discipline below — one step, one file, one
green-gated commit; the design/plan/execute cycle; who may edit a design doc;
what a step file must and must not contain; the commit discipline — is what
this repository practises. The ledger machinery this document also specifies
in the source it was trimmed from — the index schema, the validation harness,
the step-execution orchestrator — lives in the private monorepo this
repository was carved from, and does not ship here.

---

## Purpose

`work_plans/` holds the executable ledger between a design doc and the code: the
fully-specified steps an agent executes, one green-gated commit at a time. This
document specifies **the artifact** — the files, the index schema, the statuses,
the validation contract.

It does not specify the machine that executes it. That is
its companion `orchestration.md`, which is not published in this repository —
it owns modes, context profiles, model routing,
budgets, and build order. The two documents share one vocabulary and neither
repeats the other. Where this document names a mode (`EXECUTE`) or a role
(`worker`, `inspector`), the definition is orchestration's.

It replaces the organization described in `work_plans/README.md` (the required
sections, the lifecycle, the status stamp), which is not published in this
repository. The *worker-scoping discipline* in
that README — one fully-specified, green-gated, committed step per agent, never
an exploratory or multi-part task — is unchanged and is the reason the unit of
everything below is a single step.

### The two failures this fixes

1. **Status stamps go stale.** Plans read `NOT STARTED` for work that was built,
   gated green, and folded to master weeks earlier. The mechanism is always the
   same: updating the stamp is a *separate action* from doing the work, so it is
   the action that gets skipped under load.
2. **Plan files are too large to navigate**, for humans and for LLMs alike. A
   single file carries verified-current-state, per-step edits, test stanzas, and
   a coverage matrix, and the reader must scan all of it to find the one step
   they want.

### The spine principle

> **Every field is either derived, or forced by an action that already happens. A
> free-standing assertion someone must remember to update will rot.**
>
> **Derived values may be stored when the source is immutable and the derivation
> is reliable.** Storing a derived value is a caching decision, not a
> correctness one, *provided nothing hand-maintains it.*

The second clause matters in practice. The index is written only by scripts, so
a copy of a git-derived fact cannot drift from its source — and one file read
beats a subprocess when a context window is being assembled. What the principle
forbids is a *human or a model* asserting something a machine could compute.

---

## 1. The unit is a step

A **step** is one agent work-unit: one fully-specified task, one dispatch, one
`./bin/green` run, one commit. One file holds exactly one step.

That alignment is what makes status nearly self-maintaining. One file = one
dispatch = one commit = one status transition. Any coarser unit (a milestone, an
"effort") reintroduces the gap between doing the work and recording it.

**Steps are minted in batches.** A planning pass writes many step files at once —
roughly one planning pass per design-doc iteration. The batch is the planning
unit; the file is the execution unit. These are deliberately different sizes.

The word **plan** is not a synonym for step. A plan is the whole set: index plus
overview plus steps.

### How big a step is

Size is set by what makes an executing agent reliable, not by line count. Too
large and the agent orients instead of building. Too small and the step pays a
green run — about eighty seconds, once inside the worker and once in the
dispatcher — for no reliability gain. Three tests, applied in order:

1. **Split where the halves share only an interface; merge where they share
   code.** An agent that writes the same function twice marks a split made in the
   wrong place. An agent holding two unrelated state machines marks a step that
   should have been two.
2. **A step must be able to fail its own green.** If a candidate step cannot turn
   its own gate red, it is not a step — it is a paragraph of a neighbouring one.
   This is the floor, and it rules out micro-splitting without needing a line
   budget to do it.
3. **Budget the spec, not the file** — roughly sixty lines of imperative content,
   counted after §12's exclusions.

Order matters: rule 3 usually dissolves the pressure that rules 1 and 2 would
otherwise have to resolve. A step that looks oversized is most often an ordinary
step carrying material §12 says does not belong in it, and it comes back under
budget without being split at all.

---

## 2. The cycle

```
1. DESIGN     — do design. Commit design.
2. PLAN       — probably improve design; maybe commit design.
                Write steps. Validate. Commit steps.
3. EXECUTE    — per step: validate, execute, validate, land.
                Commit code (and the step's new status) per step.
4. On a failed step, or any needed design change → goto 1.
```

This is `orchestration.md` (its companion document, not published in this
repository) §4.1's mode machine at ledger granularity, and
the vocabulary is shared: `DESIGN`, `OUTLINE`/`DISCOVER`, `REVIEW`, `EXECUTE`.
`DESIGN` is the universal recovery state; every failure path returns to it,
carrying a handoff.

**Design is never "dirty."** A design doc is incomplete almost always and
partially unimplemented almost always. Committing a design change is always fine.

What matters is *when* it happens. **A design commit during phase 3 is a phase
violation** — it means the design changed after the plans were finalized, which
indicates something that should go through another design cycle rather than be
patched downstream. This replaces v1's per-step "staleness" alarm with a single
unambiguous signal.

---

## 5. Namespaces: design-driven and self-standing work

Not all work descends from a design doc. A typo fix, a code-level bug fix that
changes no design, a dependency bump — none of these have a design doc to bind
to, and none should have one invented for them. But they should still get the
step file, the validation pipeline, and the script-based check-in.

**The distinction is design-driven vs. self-standing work**, not "big vs. small."
Size is not the variable; provenance is.

A self-standing plan declares itself explicitly:

```yaml
designs: bugfix
```

**Explicit designation, not an optional field.** An absent `designs` key and a
self-standing plan are indistinguishable to a validator, which weakens every
check that depends on the binding. A plan must *say* which world it is in. The
schema therefore accepts `designs:` as either the literal string `bugfix` or a
list of design bindings, and never as absent.

What a `bugfix`-namespace plan gives up: design-version binding, section
anchoring, phase-violation detection, and goal-coverage closure — there is no
design doc for any of them to reference. What it keeps: everything else — in
particular the whole of the validation pipeline and the status vocabulary.
There is exactly **one** step machine; only the bindings vary.

Steps accumulate in `work_plans/bugfix_index.yaml` and its step files
indefinitely. The namespace is never "complete."

---

## 7. Who may edit a design doc

> **An agent may make exactly those design-doc edits that would not bump the
> version.**

Design docs are maintained by the design conversation. A work plan updates the
codebase. "Write a plan to change my own design docs later" is unstable and
unreliable, and a plan is never the place a decision gets made.

This single rule resolves what would otherwise be a contradiction with
`CLAUDE_ALWAYS.md`'s standing instruction that *"Design docs are not read-only:
if a change touches behaviour the design specifies, update the relevant
`design/` file in the same change. Stale docs are bugs."* Both hold:

- **Normative edit** (a decision) — bumps the version. Conversation only. Never
  an executing agent.
- **Descriptive edit** (recording what was built, filling in a table that follows
  from a decision already taken) — does not bump. An agent may and should make
  it, so the stale-docs-are-bugs rule survives intact.

**An agent that finds the design wrong stops.** It abandons the step and reports,
rather than patching the design or working around it. The same holds when a
validator concludes the step is fine but the *design* is incoherent: it reports,
and the fix is a conversation with a version bump — never a step.

**Discovery during planning is expected and is not an exception.** When writing
steps surfaces a design gap, the fold happens in the conversation, the version
bumps, and the steps are written or re-validated against the new version. Before
dispatch, never as a step.

---

## 12. The step file

A step file is **natural language with a soft schema**: required sections, free
prose beneath each. It is not mechanistic, and this is deliberate.

### Why NL, and where the line is

Anything mechanically executable should not be a plan at all — it should just be
the commit. What an NL step buys that a commit cannot:

- **It is reviewable before it is expensive.** Iterating on intent costs seconds;
  iterating on a diff costs an execution run.
- **It carries negative space.** "Do not touch the fold path" is invisible in a
  diff that does not touch the fold path. Constraints have no representation in
  an artifact made of effects.
- **It is the unit of dispatch.** An agent needs a prompt. The step file *is* the
  prompt. A commit cannot be dispatched.
- **It exists before the work does.** The step graph is the decomposition;
  commits can only record one after the fact.

That yields the editorial rule for what goes where — the test is *would a script
do this better?*

| kind of content | goes in | validated by |
|---|---|---|
| executable | nowhere — make the commit | — |
| a checkable claim (ids, deps, goals, design bindings, status) | the index, hard schema | script |
| intent, constraint, judgment | the step file, NL | model |

### Required sections

```markdown
## Task
## Context
## Change
## Tests
## Do not
## Done when
```

- **Task** — one line: *what* this step does. Never *why*.
- **Context** — what the executing agent needs that the overview does not carry.
- **Change** — the edits, in prose, naming files and locations.
- **Tests** — which plan files or stanzas to add or extend, per `design/testing.md`.
- **Do not** — explicit out-of-scope. The section with no home anywhere else, and
  what stops small deviations from compounding across a hundred steps.
- **Done when** — acceptance beyond green.

Presence is checked by the validator, not by a parser; bodies are unconstrained.

### Purpose is withheld from the executing agent

**The step file names no goal and no design section.** Goal linkage (`advances`,
`satisfies`) and design bindings live in the index and are given to the
*validators*, never to the agent doing the work.

This follows `orchestration.md` (its companion document, not published in this
repository) §3's segment 4b: an agent that knows *why* can
rationalize its way around *what*, and the inspector is the only check on an
agent that can pass its tests by weakening them. The validators need the linkage
for the opposite reason — it is exactly the thread between design intent and step
actions that they are checking.

`Task` is the one-line *what* that keeps the Change section from reading as
arbitrary edits. It is not a purpose statement.

**Purpose vocabulary maps cleanly:** orchestration's *step purpose* is this
index's goal linkage for that step; its *arc purpose* is a goal statement in the
index; its *run purpose* is the overview's desired-final-state. The tree is the
same tree, held in the index rather than in headings.

### A step file carries no history

The rule above withholds *why*. This one withholds *was*.

> **A step that has not been dispatched has no history.** Correct it by rewriting
> it and letting git hold the previous text: no banner, no dated parenthetical, no
> change record, no "previously this step said".

The archival instinct behind those banners is sound and is aimed at the wrong
artifact. What deserves freezing is a record someone will read — a handoff, a
landed step, a deprecated plan. The draft history of a step nobody has run has no
such reader, yet it is read in full by every agent that executes the step.

It is not merely dead weight there, it is load-bearing in the wrong direction.
Text recounting how a step was recently wrong invites the agent to verify the step
before trusting it, and an agent that sets out to verify its own prompt reads
instead of building. Provenance in a step file does not just cost tokens; it
changes what the agent does with them.

The same reasoning bars the neighbouring temptations. Rationale for a decision
belongs in the design doc that owns it — the index binds the step to that
section and the validators read the binding, so a step that restates the
reasoning is both a second source of truth and a purpose leak the section above
already forbids. A rejected alternative belongs in §15's `## Alternatives`. What
remains in the step file is the six sections above and nothing else.

**Freezing begins at the first attempt that produced a commit** — not at the first
dispatch. An attempt that landed nothing left no record to protect, so the step is
still a draft and is still rewritten rather than amended. Once a commit exists the
step *is* a record and §15 governs: attempts are commits, and the narrative is the
log. The boundary is a git query, deliberately, so it is not a judgment call.

---

## 15. Commits

**One commit per kind of change**, never mixed, and the first line says which:

| prefix | contents |
|---|---|
| `design:` | `design/*.md` |
| `steps:` | `work_plans/*` |
| `code:` | source and tests |
| `revert:` | undoing a rejected attempt |

It does not have to be perfect — the main purpose of the change is enough. The
checker tolerates a mislabelled commit rather than treating labelling as
load-bearing.

**Never amend, never squash.** Full history is kept. This is what makes recorded
commits stable anchors and what makes the log readable as a record.

### The log is the execution history; the index is current state

Because a rejected attempt is either reverted or patched forward, and a revert
appears in the log as a `revert:` commit, **"which one happened" needs no index
field** — the human arriving at an escalation reads it from the log, at exactly
the point they care.

Attempt *counts* stay in the index because a pre-validation failure produces no
commit at all, so they are not derivable. Attempt *narrative* never does.

**`bin/green` must commit `design/` separately** from its code checkpoint. It
currently sweeps everything dirty into one unlabelled `green checkpoint` commit,
which would make design and code changes indistinguishable in history and break
the enforcement above. Committing design paths first under a `design:` message,
then checkpointing the rest, leaves nothing uncommitted and needs no judgment
about normativity — the version line already carries that.

### Rejected alternatives live in the design doc

The same division, one level up: **git holds the mechanics, prose holds the
judgment.** A rejected approach is not history — it is the negative space of the
current design, and the question it answers (*"did we consider X?"*) is asked by
someone already reading the design doc. So that is where it goes, in a section of
fixed shape:

````markdown
## Alternatives

### <the approach that was rejected>

One paragraph on what it was.

One paragraph on why it was rejected.

`deadbeef` → `beefdead` encodes the change.
````

**The commit pair is the load-bearing part.** It delegates *what changed* to git
and keeps only *why we chose against it* in prose — the one half a diff cannot
reconstruct. Without it the section drifts toward re-describing the old design in
enough detail to be a second source of truth.

**The fixed shape is what limits growth.** A second rejection is a second `###`,
not a longer one, so the section cannot accrete the way a banner stack does.

Nothing else gets an equivalent section. `process/` is the intent layer — where we
are heading — and a rejected alternative is a fact about the design, not about the
trajectory. Plans get none for the reason §12 gives. The single exception is a
whole plan superseded by a successor, which is a fact about neither and already
has a home in the deprecated plan's own banner.
