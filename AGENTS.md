# Apostate — working agreement

Apostate is an anti-detect Chromium fork: a real browser binary whose
fingerprint is modified in C++ at the source level. Free, open source, no paid
tier. It exists so that real operators can run sessions that pass the
detectors they actually meet, and every trade-off in this repository is
settled by that use, not by how a value was obtained.

Read `docs/METHODOLOGY.md` before doing anything else. It defines what
"correct" means here, and this file will not repeat it.

## The three axioms

1. **Provenance** — patch the emitter, never the accessor. No JS injection, no
   CDP override, no wrappers or redefined descriptors. If a value is wrong,
   find the C++ that produced it.
2. **Coherence** — correct means "a page cannot tell this from a real machine
   of the kind it claims". Every surface agrees with every other surface, and
   nothing of the host shows through a composed identity. That is the bar; the
   source of a value is not.
3. **Determinism** — no per-call randomness, ever. Two reads of one surface in
   one launch agree. Variation lives at the profile boundary: a different seed
   is a different machine, the same seed is the same machine, and a persistent
   profile keeps its machine.

If a change cannot satisfy all three, it does not land. A partially coherent
browser is more detectable than an unmodified one, because incoherence is
itself the signal — which is also why leaving a surface as the host's "until
we have a capture" is not the safe choice. The host leaking through a fabricated
machine is the loudest incoherence there is.

## Where values come from

The catalogue is built from a small number of real captures used as bases,
widened by rules that are written down. A capture from a physical machine is
the strongest evidence. A cloud capture of a GPU family is evidence about that
family. A public corpus or a working competitor is evidence about what real
populations look like and what passes. A derivation by a documented rule —
D3D11 capability limits are feature-level constants, a card's PCI id is public,
a WASAPI buffer is ten milliseconds — is evidence too. The ledger records which
of these a value rests on so the next person knows how much to trust it. It
does not decide whether the value may be served; usefulness to an operator
decides that.

What this rules out is inventing a value with no rule behind it and calling
it plausible. What it does not rule out is serving a value we did not measure
on physical hardware when the rule that produces it is sound. An entry says
which it is.

## Hard rules

- **Never introduce a new observable.** A patch that fixes one surface while
  adding a command-line switch the page can see, a novel mojo interface, an
  unusual process name, or a timing change is a net loss. Cross-process
  plumbing goes through the profile loader; nothing else invents its own path.
- **Never let the host show through a composed identity.** A composed launch
  gets a defined value on every surface the profile covers. Only
  `--fingerprint=host` inherits.
- **Never treat `resources/fingerprints/*.json` as ground truth.** They are
  templates. See the `PROVENANCE.md` beside them.
- **Never commit a third-party dataset verbatim.** Options harvested from one
  go into the catalogue with their source named; the dataset itself stays out
  of the tree. A T0 row means a capture we took ourselves, with consent, using
  `capture/`; nothing else is labelled T0.
- **Never hand-edit anything under `out/` or `src/`.** Those are generated. All
  source changes are patches in `patches/`, listed in `patches/series`.
- **Never commit to `.internal/`.** It is gitignored. This repository's history
  is public and permanent; internal planning, positioning, and strategy live
  there and nowhere else. Do not reference their contents in public docs,
  commit messages, or code comments.

## Repository layout

```
build/        Pinned versions and GN args. The build contract — see docs/BUILD.md
patches/      The fork. One patch per concern, ordered by patches/series
config/       profile.schema.json — DERIVED from the ledger, not hand-written
capture/      The capture pipeline. Also the V3 conformance harness
corpus/       Oracle builder. Turns our captures into a queryable DB
ledger/       Surface ledger, emitter index, coherence graph, and their schemas
resources/    surfaces.json (input map) and fingerprint templates
scripts/      Every operational step. Nothing is done by hand
docs/         METHODOLOGY.md is authoritative; ARCHITECTURE.md, BUILD.md follow
```

## Build discipline

The build pipeline is reproducible and fully scripted. This is not a
preference — an environment configured outside the repo is an environment that
drifts, and drift in a fingerprinting project produces silent, unattributable
failures.

- Every input is pinned in `build/`: Chromium revision, depot_tools revision,
  toolchain, GN args, container base image digest.
- Every step is an idempotent script in `scripts/`. If a step needs a human to
  run a command by hand, that is a bug in the script.
- Two builds from the same pins produce byte-identical output.
  `scripts/verify-reproducible.sh` is the check, and it runs in CI.
- Never `gclient sync` without the pins. Never let depot_tools self-update
  (`DEPOT_TOOLS_UPDATE=0`).

Builds are checkpoints, not a debugging loop. Gate work at V0/V1
(`scripts/checkfile.sh`) and batch it; a full build should be expected to pass,
not tried to see what happens.

## Verification

Nothing is "done" until it passes the tier its ledger row names — V0 through
V4, defined in `docs/METHODOLOGY.md` §6. V3 (corpus conformance) is the
scoreboard: launch with profile P, collect, diff against P's corpus row.

Report results as they are. A patch that compiles is not a patch that works,
and a V3 diff with three red fields is reported as three red fields.

## Working mode

Run the agreed plan to completion without checking in between steps, and
parallelize wherever the work allows. Stop for the user only when a finding
forces a **material decision** — something that changes scope, architecture, or
whether the product is viable. Progress updates, permission to continue, and
confirmation of an obvious next step are not material decisions.

When genuinely blocked, ask with a recommendation rather than a survey.

## The shared checkout

`.workspace/src` is shared: mapping work cites line numbers from it while patch
work applies patches to it. Those conflict, and it has already cost real
rework — a shard was mapping a file while a patch was applied underneath it, so
its citations pointed at lines that no longer existed.

Rule: **the checkout stays pristine while mapping is in flight.** Apply patches
only to compile-check, then reset. Ledger citations always name the pristine
tree at `CHROMIUM_VERSION`; the patch that changes a line is linked through the
row's `patch_id`, not by re-citing the patched line.

## Parallel work

Delegated units return JSON validating against a schema in `ledger/schema/`,
written to `ledger/inbox/`. Workers never write the ledger directly; a single
arbiter merges. Shard by category. When uncertain, mark `escalate` — never
silently drop a surface.

## Commits

Small, focused, and frequent. Message says what changed and why, in plain
language. No attribution or co-author trailers.
