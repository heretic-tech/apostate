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

**Windows 11:** 90 stock Windows families in the core pack. Optional packs:
Microsoft Office fonts (60% of seeds, 14 families), fonts from Windows
optional features (40%, 6 families) and Bitstream fonts (10%, 49 families).

**Windows 10:** the same, without Sans Serif Collection, Segoe Fluent Icons,
Segoe UI Variable and SimSun-ExtG, which only Windows 11 has.

All Windows families come from the
[MauCariApa-com/windows-11-fonts](https://github.com/MauCariApa-com/windows-11-fonts)
repository, except Marlett, which every Windows machine has and the repository
does not include. A host without it simply does not show it. The substitute
names Windows also answers to (Courier, Helvetica, MS Sans Serif, MS Serif and
Times) are hidden on purpose: on a Mac or Linux host they would reach the
host's own fonts.

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
installs its `w11-fonts/` directory:

- Linux: into `~/.local/share/fonts/apostate-windows`, then runs `fc-cache -f`.
- macOS: into `~/Library/Fonts/apostate-windows`.
- Windows: nothing to do, the fonts are already there.

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
