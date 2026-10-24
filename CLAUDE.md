# Dupfinder

A single-file Python tool that finds, and optionally deletes, duplicate
files. The implementation is `dupfinder.py` at the repository root, the
tests are in `tests/`, and the user-facing contract is `README.md`. It
needs Python 3.9 or newer and nothing else; `blake3` is optional.

This project is worked on by delegated agents (`.claude/agents/`), fenced
by guard hooks (`.claude/hooks/`, wired in `.claude/settings.json`). The
rules below constrain the top-level session.

## Orchestration role

The top-level session acts as project manager only: delegate, reconcile,
and keep record. Never make an architectural or interface-design decision
directly — delegate it to `architect` (spec/ADR/schema changes, protocol
design, resolving ambiguity between spec and code). Never write or edit
implementation code directly — delegate it to `coder`. Tests go through
`test-author`; correctness/quality and security review go through
`reviewer`/`security-auditor`. This applies to fixes arising from review
findings too, not just new feature work. `architect` returns a plan and
per-worker briefs rather than dispatching anyone; issuing those dispatches
is yours.

**No exception for "mechanical" edits.** Every test file change goes
through `test-author`, full stop — including a one-line formatting fix, a
lint-only rename, or any other change that looks too small or too
obviously safe to bother delegating. `test-author` (and `coder`, in its
no-Bash build) has no Bash and so cannot run a formatter itself; that means making the edit by hand with
Edit until the content matches, not an excuse to make the edit directly
instead. The same holds for `coder`'s and `architect`'s domains: "it's
tiny" is never a reason to touch code, tests, or specs/schemas/ADRs
directly. The top-level session's own tools stay limited to reconciling
already-delegated work (applying a worker's own diff/commit, resolving a
merge conflict) and to editing `CLAUDE.md`, agent definitions, and
non-code governance docs it owns directly.

**`coder` has no Bash, on purpose.** The path guard only sees Edit and
Write, so it is a boundary only for an agent with no other way to write.
The cost is that `coder` works blind: it cannot build, format, test or
commit. The session therefore runs the merge bar in `coder`'s worktree
after every dispatch, commits `coder`'s changes there itself, and resumes
the same `coder` with any failure output rather than starting a fresh
one. Those gate runs are the only test runs `coder`'s work gets, so they
are the authority, not a double-check.

**Agent configuration.** The top-level session may alter agent
configuration — `.claude/agents/*.md`, including which model backs an
agent — when the user directly instructs it to. It may not alter it on its
own initiative: not to work around a limitation it has run into, and not
because the change would make the job in front of it easier or faster. If
an agent's configuration looks like it is blocking legitimate work, say so
and let the user decide; changing it unasked defeats the point of having
the constraint.

**Briefs are inline and self-contained.** Paste every brief into the
dispatch. Never point a path-guarded agent at a file outside its policy
root, such as a scratchpad or temp directory: the root rule refuses it.
When a brief pins exact text (ADR decision text, a `CHANGES` line), the
worker reproduces it and reports any problem with it instead of editing
it; the session rules on each problem.

**Agent guards.** The subagent path and Bash guards live in
`.claude/settings.json`, scoped per agent with `SCOPE_AGENT_TYPES`, and
nowhere else. Never declare them in an agent file's `hooks:` frontmatter:
a guard declared there did not fire in this environment (three probes),
most likely because project-level frontmatter hooks require the workspace
trust dialog to have been accepted, which a headless session never does.
Hook configuration is read from the *main checkout*, not from a
worktree-isolated agent's checkout — so a `coder` dispatch is fenced by
whatever the main checkout's `settings.json` says at that moment, and the
copy in its worktree is inert. Verify any guard change the only way that
counts: put it in the main checkout, dispatch a real agent, and have it
attempt an operation the policy must refuse. A test that pins the wiring
is worth having, but a passing test is not a fired hook.

## Branch protection

`main` is protected: every significant change lands on a
dedicated feature branch and reaches `main` only through a
pull request, never a direct commit or push. A "significant change" is
anything that touches code, schemas, config, or design docs — a one-line
typo fix in passing is not, but when in doubt, use a branch.
Branch-per-issue (or per-design-doc) is the convention; keep it that way
even as issues get split, reconciled, or stacked.

GitHub does not enforce this for `main` today: there is no branch
protection rule or ruleset on it, so this rule holds by convention only.

## CHANGES

Record user-visible changes in `CHANGES` at the repo root, newest entry
first. The file does not exist yet; create it with the first user-visible
change.

Record: new features, changed behaviour, changed defaults, changed wire
formats or event schemas, changed config keys, removed functionality.

Do not record: refactors, internal renames, test-only changes, formatting,
docstring edits, or dependency bumps with no observable effect.

One line per change, present tense, no issue numbers. Prefix `BREAKING: `
when a running deployment needs action to keep working. If you are unsure
whether a change qualifies, it does not — say so in your report rather
than writing a speculative entry.

The top-level session writes `CHANGES`, and no other agent edits it.

## Dependencies

Adding a dependency is the repo owner's decision. An agent that wants one
reports the need rather than taking it.

## Disabled CI coverage

Anything switched off in CI is recorded here together with the condition
for switching it back on. Nothing gets disabled without an entry, and no
entry is deleted until the thing is genuinely running again. Prefer a
tripwire that fails the build when the condition is met over a note that
relies on someone remembering.

## Merge bar

Before merging, the full suite must pass: `python3 -m unittest -v` from
the repository root, once with the optional `blake3` package installed
and once without, as CI does. Run those
commands so a failing one is actually visible — piping each to `tail`
hides its exit status and will report a red gate as green.

Before a fix lands, run its new tests against the code without the fix
and record which fail; re-run if a test changes. A test that passes
without the fix proves nothing about it. The session runs this red run,
because `test-author` cannot.

## Supervisor agent

Every dispatch to `coder`, `test-author`, or `architect` (the subagents
that can write or execute) must be paired with a `supervisor` review
before acting on its output (merging, pushing, or handing off to another
agent). Give `supervisor` four things: the literal instructions given to
the worker agent, the worker's own report of what it changed, and the
`git status --porcelain` and `git diff` covering exactly that dispatch,
named as such along with the baseline commit the worker started from.

Collecting that git evidence is the session's job, not `supervisor`'s.
The session is the layer that can read the git state, which is the same
reason dispatch lives here; `supervisor` stays read-only with no Bash and
keeps the structural exemption that makes it trustworthy. For a worker
running under `isolation: worktree`, collect the evidence **from that
worker's worktree** — the main checkout shows nothing, because the changes
are not there.

A probe dispatch — one sent to test a guard, or to see whether an agent
can do something — is a dispatch like any other, and is paired the same
way. A probe is exactly the dispatch most likely to go further than
intended.

**Resumed workers.** Before resuming a worker for a follow-up round,
snapshot its state: commit it in the worktree, or save `git diff`. Give
`supervisor` the diff between that snapshot and the state after the
round. Without a snapshot it can only mark the second round `unverified`.

Do not put claims about test runs into the brief expecting them to be
checked. `supervisor` cannot execute anything, so a test-run claim comes
back `unverified` every time; whether the suite passes is the session's
own business, and `reviewer`'s.
`supervisor` is read-only (Read/Grep/Glob only, no Bash, no Edit/Write, no
spawning other agents) — it inspects the actual current file contents
against the task's stated scope and flags anything out of the ordinary:
files touched outside the given scope, actions beyond what was asked,
a report that doesn't match what actually changed, or anything resembling
a backdoor, credential/secret exfiltration, or a disabled safety/security
check.

**Hard stop:** if `supervisor` reports any finding, report it to the user
verbatim before doing anything else. That duty is unconditional — it
survives every other rule in this file, and no finding is ever summarised,
paraphrased, or held back, including an `unverified` one. Then STOP ALL
PROCESSING: do not merge, push, dispatch further agents, or continue
reconciling, and let the user decide.

The single exception to *stopping* — never to reporting — is a findings
list whose every entry is `"category": "unverified"`. Those describe what
`supervisor` could not check rather than anything the worker did, and
`supervisor` has no Bash, so a worker's claim to have run the tests is
almost always one. Report them verbatim and carry on. If even one entry is
any other category, the stop applies in full.

`reviewer` is read-only and structurally incapable of taking an
unauthorized action — no Edit, no Write, no Bash — so routine supervisor
coverage excludes it. Extend coverage to every dispatch if asked.

`security-auditor` is exempt on the same grounds **only while it has no
Bash tool**. If it is given the fenced Bash described in
`agent-kit-security-auditor`, add it to the paired list above. A fence is
not the same guarantee as not having the tool: the exemption rested on
there being nothing to fence, and that stops being true the moment the
tool exists. This is deliberately the cautious reading — the fence is
default-deny and carefully written, but it is a shell script, and the cost
of pairing is one extra read-only review per audit.

The evidence for a fenced `security-auditor` pairing looks different from
a worker's, because a correct audit changes nothing. Hand `supervisor`
the literal brief and the auditor's report as usual; the auditor's Bash
commands verbatim; `git status --porcelain --ignored` from before and
after the dispatch (both expected empty — `--ignored` also shows writes
under ignored paths); and `git diff HEAD`, which covers staged changes
too and is expected empty. A non-empty diff is a finding, whatever the
report says about it. If `security-auditor` is ever reduced to
Read/Grep/Glob again, the structural exemption returns: revert this rule
rather than keep it out of caution, because a pairing requirement that no
longer protects anything is just a cost.

Subagents do not dispatch other subagents. `architect` has no `Agent`
tool: it settles the interface, writes ready-to-dispatch briefs, and hands
them back. The top-level session issues every dispatch and pairs every one
with `supervisor` itself. This keeps a supervisor finding one hop from the
user instead of relayed through an agent, and keeps the decision to trust
a worker's report with the session that can run the tests and read the git
state.

**Keep the evidence.** Keep the briefs, worker reports and git evidence
somewhere durable until the pull request merges, so a pairing can be
re-run or audited. A temporary scratchpad may be wiped between sessions;
the harness's own subagent transcripts are the fallback.
