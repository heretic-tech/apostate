# Apostate: working agreement

Apostate is a Chromium fork for running browser sessions that look like a real
machine of a chosen kind (a "persona"): Windows, macOS or Linux, with a matching
GPU, screen, fonts, voices, locale and so on. Values are changed in Chromium's
C++ where they are produced. Python and Node wrappers download the binary and
launch it through Playwright.

It is free and open source. Its job is to pass the detectors real operators meet
(FingerprintJS Pro first) while staying simple to use.

## Read this first

- `.internal/DIRECTIVES.md` (local, not committed) holds the owner's
  instructions. They override everything in `docs/`. If a doc disagrees with
  them, the doc is wrong: fix the doc.
- Owner instructions given in prose are requirements, not suggestions. When a
  directive is high level, fill in the details yourself, without inventing
  policy.
- Write any correction the owner gives you into `.internal/DIRECTIVES.md` right
  away, so the next session has it.

## Rules

1. **Change the source of a value, not the JavaScript that reads it.** No
   injected scripts, no CDP overrides, no redefined getters. Find the C++ that
   produces the value.
2. **Serve what the persona claims.** If a persona claims a GPU, it gets that
   GPU's WebGL and WebGPU values. If it claims Windows, it gets Windows voices,
   fonts, system colours and screen layout. Never fall back to the host's value,
   and never serve null, just because the host cannot back the claim. The only
   mode that shows host values is `--fingerprint=host`.
3. **No per-call randomness.** Two reads of the same thing in one session agree.
   The same seed gives the same machine; a persistent profile keeps its machine.
4. **Be practical.** Prefer the simple fix that makes real detectors pass. Do
   not emulate another platform's internals (font rasterisers, CPU arithmetic)
   unless a measurement shows a detector depends on it. Do not write docs that
   explain why something cannot be done; find a way or say plainly that it is
   not done yet.
5. **Measure.** A change is done when it is measured on the real target:
   FingerprintJS Pro's suspect score and flags, plus a probe diff against our
   real captures. Report numbers as they are.
6. **Add no new tell.** A fix must not add something a page or the host can
   see that stock Chrome does not have: a command-line switch visible to the
   page, a new mojo interface, an odd process name, a timing change.

## How the pieces fit

- Fonts: each platform has an allowlist of real system fonts and everything
  else is hidden. The fonts must be installed on the host
  (`apostate fonts install windows` clones
  `github.com/MauCariApa-com/windows-11-fonts`). Missing fonts are the user's
  problem; we do not fake them.
- GPU: four GPU families were captured on real hardware. Within a family, the
  renderer string can be swapped for any model of that family with no other
  change. WebGL and WebGPU values come from the family.
- Screen: a few real resolutions per platform. Windows always has a taskbar gap
  (`availHeight < height`).
- Voices: from our real captures. If per-profile variation gets complicated, all
  profiles of a platform get the same set.
- Display: on a Linux host with no display, the wrapper starts Xvfb sized to the
  persona's screen and cleans it up. The user only needs Xvfb installed.

## Working mode

Run the agreed plan to completion without checking in between steps, and
parallelise where the work allows. Do not leave work half done. Stop for the
owner only for a decision that changes scope, architecture or whether the
product works, and then ask with a recommendation, not a list of options.

## Evidence

- Our own captures of real machines (`resources/fingerprints/raw/`), taken with
  `capture/`: a Windows NVIDIA desktop, an Apple M4 Max laptop, a Windows Intel
  laptop, plus cloud machines covering the GPU families.
- A large public corpus of older Windows fingerprints, kept outside the tree.
- Never commit a third-party dataset. Never hand-edit `out/` or `.workspace/src`;
  source changes are patches in `patches/`, listed in `patches/series`.

## Build

Every input is pinned in `build/`. Every step is a script in `scripts/`. Never
`gclient sync` without the pins and never let depot_tools self-update
(`DEPOT_TOOLS_UPDATE=0`). A full build takes hours: check patches with
`scripts/checkfile.sh` before building.

`.workspace/src` is shared by everything that works on patches. Leave it in the
state you found it.

## Commits

Small and focused. The message says what changed and why, in plain words. No
attribution trailers. Never commit `.internal/` and never put its contents
(plans, positioning, competitor notes) into a public file, comment or commit
message. Pointing at `.internal/DIRECTIVES.md` as a file to read is fine.
