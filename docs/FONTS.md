# Fonts

Each persona shows pages a fixed list of font families for its platform, and
hides every other font on the machine. The browser does not supply fonts. A
family on the list shows up only if it is installed on the host, so
installing the fonts is part of setup.

## What each platform shows

The lists are the `fonts.enumeration_allowlist` arrays in
[`resources/profiles/dispersion/font_packs.json`](../resources/profiles/dispersion/font_packs.json).
Every persona gets its platform's core pack. Each optional pack is included
for a share of seeds, and the same seed always gives the same packs.

**Windows 11:** 90 stock Windows families in the core pack, and no optional
packs. A Windows persona shows the fonts of a clean Windows install and
nothing else. A handful of Office families without the rest of Office is a
font list no real machine has, so no Office, optional-feature or Bitstream
fonts are added.

**Windows 10:** the same, without Sans Serif Collection, Segoe Fluent Icons,
Segoe UI Variable and SimSun-ExtG, which only Windows 11 has.

All Windows families come from the
[MauCariApa-com/windows-11-fonts](https://github.com/MauCariApa-com/windows-11-fonts)
repository, except Marlett, which every Windows machine has and the repository
does not include. Add it from a Windows Fonts folder (below). A host without
it simply does not show it.

A Windows persona also answers the other names Windows answers to, with the
family Windows uses for them: Courier is Courier New, MS Sans Serif is
Microsoft Sans Serif, MS Serif and Times are Times New Roman, and Helvetica is
Arial. Franklin Gothic is Franklin Gothic Medium, because Windows files that
font under both names. Arial Narrow is Arial at its normal width, as on a
Windows machine without the narrow face. Each such name shows only when the
family behind it is on the list, and none of them ever reaches a host font of
that name. On a Mac host only the first four work this way; Helvetica,
Franklin Gothic and Arial Narrow stay hidden there.

**macOS:** the 184 families of a real Mac (Apple M4 Max, macOS 26.6.2). No
optional packs.

**Linux:** Arial, Courier, Courier New, DejaVu Sans, Helvetica, Liberation
Sans, Noto Sans, Times and Times New Roman. For 54% of seeds also Cantarell,
Ubuntu, Ubuntu Condensed and Ubuntu Mono, as on an Ubuntu desktop.

`--fingerprint-explain` lists the packs a launch drew on its `font_packs`
rows. Pass `--fingerprint=<seed>` to keep the same packs every time.

## Install them

### Windows persona

A default launch on a Linux host is a Windows persona, so this is the one
setup step there. On a Linux or macOS host, run one command:

```sh
apostate fonts install windows        # pip
npx apostate fonts install windows    # npm
```

It clones https://github.com/MauCariApa-com/windows-11-fonts with `git` and
installs the files in its `w11-fonts/` directory whose family is in the
Windows core pack, and no others:

- Linux: into `~/.local/share/fonts/apostate-windows`, then runs `fc-cache -f`.
- macOS: into `~/Library/Fonts/apostate-windows`.
- Windows: nothing to do, the fonts are already there.

A file counts by the family Windows lists it under, so the Office font Arial
Narrow stays out even though its files also carry the name Arial. Files left in
that directory by an older install that are not core fonts are removed. The
command ends by listing the core families the host still lacks.

To add what the repository lacks, such as Marlett, copy a real Windows
machine's `C:\Windows\Fonts` folder to the host and install from it. This
takes the core families from that folder instead of cloning:

```sh
apostate fonts install windows --from ~/windows-fonts
```

Apostate does not ship these fonts. The repository has its own licence, and
the command clones it on your machine. The fonts are ordinary user fonts, so
other programs on the host can use them too.

### macOS persona on a Linux host

The macOS fonts have to come from a Mac. On a Mac you own:

```sh
apostate fonts export-macos ~/mac-fonts
```

This copies the Mac's font files into `~/mac-fonts`: everything in
`/System/Library/Fonts`, `/System/Library/Fonts/Supplemental` and
`/Library/Fonts`, plus the fonts macOS keeps elsewhere (PingFang, and the
downloaded fonts under `/System/Library/AssetsV2`).
Copy that directory to the Linux host, then run:

```sh
apostate fonts install macos --from ~/mac-fonts
```

It installs the files into `~/.local/share/fonts/apostate-macos` and runs
`fc-cache -f`. The npm package takes the same commands through
`npx apostate fonts ...`. A macOS persona on a Mac needs nothing.

### Linux persona

Most Linux desktops already have DejaVu, Liberation and Noto. Arial, Courier
New and Times New Roman come from `ttf-mscorefonts-installer` or from the
Windows install above, and the Ubuntu desktop families from the `fonts-ubuntu`
and `fonts-cantarell` packages.

## If fonts are missing

A page sees only the families that are on the list and installed on the host.
Anything missing is just absent, and nothing warns you. The browser does not
fake a font it does not have. A Windows persona on a Linux host with no
Windows fonts installed shows almost no fonts, which no real Windows machine
does. Getting the fonts onto the host is your responsibility.

## Check

On Linux:

```sh
fc-list | grep -i "segoe ui"
```

In the browser, this lists what a page can see (it asks for permission
first):

```js
(await queryLocalFonts()).map(f => f.family)
```
