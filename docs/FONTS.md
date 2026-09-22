# Font setup

A persona that does not match the host operating system needs that
platform's fonts on the machine. This is a one-time setup step, and the one
to do before debugging anything else.

On a Linux host this applies to a default launch. A Linux machine claims
Windows unless you pass `--fingerprint-platform=linux`, so a default launch
there needs the Windows font set.

A persona without its fonts is one of the most common reasons a session is
blocked. Installed fonts are cheap for a site to check: a page asks whether a
family is present, and if the answer is no on a machine claiming Windows,
that machine is not a Windows machine. The faces a platform ships with cannot
be uninstalled by a real user, so their absence has no innocent explanation.

Fonts are also the part of a cross-OS persona you can fix. The GPU identity
follows the persona on every host, and installing the font files closes the
remaining gap.

Apostate cannot ship them. Apple's and Microsoft's fonts are licensed. Copy
them from a machine you own that has them.

The browser assumes you have done this and does not check the filesystem.
`--fingerprint-explain` names the prerequisite on a cross-OS launch and
cannot confirm you have met it.

## What to install

Install every family your persona's font packs name. The browser removes
families the host lacks rather than inventing them, so a Windows persona on a
machine missing Georgia and Verdana reports a Windows machine without Georgia
or Verdana.

Which packs your persona drew is printed by `--fingerprint-explain`, on the
`font_packs` rows. The families in each pack are the
`fonts.enumeration_allowlist` arrays in
[`resources/profiles/dispersion/font_packs.json`](../resources/profiles/dispersion/font_packs.json).
The core pack is always drawn; the rest depend on the seed.

### Pin a seed so the list stops moving

A bare launch draws a fresh seed, and the optional packs move with it, so
"which fonts do I install" has no fixed answer until you fix the seed. Four
seeds on a Windows persona, as printed by `--fingerprint-explain`:

```
--fingerprint=7      platform-core  microsoft-office  cjk-language-pack
--fingerprint=42     platform-core  microsoft-office  adobe-creative-cloud
--fingerprint=999    platform-core  cjk-language-pack
--fingerprint=12345  platform-core  microsoft-office  libreoffice
```

`platform-core` is in every one of them and is labelled `core` in the
output. The others are labelled `included` and are the seed's choice.

Pick a seed, pass `--fingerprint=<seed>`, run `--fingerprint-explain`,
install the families those packs name. The same seed selects the same packs
on any host, so the install list is settled once. If you run against one
target regularly you want a pinned seed anyway, because a returning visitor
with a new identity every time is its own signal.

A missing optional pack is a different failure from a missing core family.
The filter only removes, so a persona whose `microsoft-office` faces are
absent presents a Windows machine without Microsoft Office, which is an
ordinary Windows machine. A persona missing `platform-core` faces presents a
Windows machine without Arial, which is not a machine that exists.

The report tells you which packs were drawn. Whether those families are on
the machine is yours to know; nothing warns when one is missing.

Skipping this does not break pages. A family the machine does not have fails
to match, and the page falls through its own `font-family` list to the
browser's standard font. Measured on the reference Mac, an absent family
rendered byte-identically to `serif`, Chinese, Hebrew and emoji included,
because character fallback is untouched by the filter. The cost of not
installing a persona's fonts is fingerprint fidelity: pages render, and you
look like a machine missing fonts it should have.

### Windows persona

`platform-core`, 35 families, always drawn:

Arial, Arial Black, Arial Narrow, Calibri, Cambria, Cambria Math, Comic Sans
MS, Consolas, Courier, Courier New, Georgia, Helvetica, Impact, Lucida
Console, Lucida Sans Unicode, MS Gothic, MS PGothic, MS Sans Serif, MS Serif,
Microsoft Sans Serif, Palatino Linotype, Segoe Print, Segoe Script, Segoe UI,
Segoe UI Light, Segoe UI Semibold, Segoe UI Symbol, Tahoma, Times, Times New
Roman, Trebuchet MS, Verdana, Wingdings, Wingdings 2, Wingdings 3

`cjk-language-pack`, 12 families, only if your persona drew it:

DengXian, FangSong, KaiTi, Malgun Gothic, Meiryo, Microsoft JhengHei,
Microsoft YaHei, MingLiU, NSimSun, SimSun, Yu Gothic, Yu Mincho

The other optional packs, by family count: `microsoft-office` 19,
`adobe-creative-cloud` 7, `libreoffice` 8, `developer-cascadia` 4,
`google-fonts-desktop` 10.

Copy the whole of `C:\Windows\Fonts` from a Windows machine you own, not
only the families above. The 35 are what the pack serves today and the pack
is short: Windows 11 ships 63 base families, and 28 of them, Segoe UI Emoji,
Symbol, Webdings, Sylfaen and Marlett among them, are in no pack yet. When
the pack is completed the browser will serve every family the directory
holds, and a persona that has them all installed needs no second visit. See
[docs/LIMITATIONS.md](LIMITATIONS.md#fonts-are-yours-to-install).

Without the fonts, FingerprintJS Pro reads a Windows persona on a Mac as a
machine with no fonts at all: `fonts: []`, against four families for the
host's own persona. Installing the full Windows set changes the served list
but did not change that measurement's tampering verdict, so this step is
necessary and not sufficient on a cross-OS persona. What has been tested is
in [docs/LIMITATIONS.md](LIMITATIONS.md#cross-os-is-a-risk).

### macOS persona

`platform-core` is 184 families, the full set the reference Mac enumerates,
so read the list out of `font_packs.json`. The optional packs are
`microsoft-office` 17, `adobe-creative-cloud` 7, `cjk-language-pack` 9,
`libreoffice` 8, `developer-cascadia` 4 and `google-fonts-desktop` 10.

Menlo, Monaco, Zapfino, PingFang SC and Helvetica Neue are the ones to check
first if you are short of time. They ship with macOS and cannot be removed,
so their absence is the signal with no innocent explanation. They are not a
sufficient set on their own.

Copy them from `/System/Library/Fonts` and `/Library/Fonts` on a Mac you
own.

### Linux persona

`platform-core` is 9 families: Arial, Courier, Courier New, DejaVu Sans,
Helvetica, Liberation Sans, Noto Sans, Times, Times New Roman. Most
distributions already have the free ones; the Microsoft names come from
`ttf-mscorefonts-installer` or an equivalent. Optional packs are
`ubuntu-desktop` 4, `libreoffice` 8, `cjk-language-pack` 5,
`developer-cascadia` 4 and `google-fonts-desktop` 9.

### Do not skip the CJK pack

If your persona includes the CJK language pack, its families matter as much
as the Latin ones. The realistic failure is a persona that renders Latin
text perfectly and Han text in the host's own font, because several of those
families are the ones Windows reaches for when a page uses a character the
requested font does not cover.

## Where to put them

Linux: drop the files in `~/.local/share/fonts` for one user, or
`/usr/share/fonts` system-wide, then rebuild the cache:

```sh
fc-cache -f
```

macOS: copy into `~/Library/Fonts`.

Windows: right-click the files and choose Install, or copy them into
`C:\Windows\Fonts`.

## Check it worked

On Linux, ask fontconfig:

```sh
fc-list | grep -i "segoe ui"
```

From any platform, launch the browser and read the list a page would read:

```js
(await queryLocalFonts()).map(f => f.family)
```

That call needs a permission prompt, so click through it. The families you
installed should appear.

## What the browser does with them

The enumeration filter only removes. It hides families your host has that
the claimed device would not have, and it never adds one, because a family
name with no typeface behind it measures wrong the moment a page draws text
in it. Installing the files is the half you own; filtering is the half the
browser does.

Text measurement follows from the same files. Metrics are read from the real
font at runtime, so any family you install measures the way it does on the
platform it came from.

How glyphs are inked is separate from which files you have, and the browser
owns that half. Antialiasing, the subpixel order, hinting, embedded bitmaps,
subpixel positioning and Skia's text contrast and gamma follow the persona.
How much of it a page can see depends on the host. All six are live on
Linux; on a Mac, Skia's CoreText code drops the contrast curve, the hinting
level and the autohinter before a glyph is drawn, so a Windows identity's
canvas text there is a Mac's. The text engine underneath is always the
host's, CoreText on a Mac and FreeType on Linux, and no setting reaches
DirectWrite from either.

[docs/LIMITATIONS.md](LIMITATIONS.md) covers what remains visible when a
persona is missing faces, under "Fonts are yours to install", and what
rasterisation can and cannot carry, under "Text rendering follows the
persona".
