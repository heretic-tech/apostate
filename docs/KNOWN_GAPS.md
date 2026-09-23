# Known gaps

What Apostate does not do yet or cannot hide, and what you can do about each.
`--fingerprint-explain` lists the ones that apply to a particular launch.

## The host shows through

**ARM hosts give Windows and Linux personas an ARM CPU.** On Apple silicon and
Linux arm64, a Windows or Linux persona reports the `arm` architecture
(`Linux aarch64` on Linux), because a page can tell the CPU family from
arithmetic, such as the sign of a computed NaN, and from Web Audio output,
which is rendered by the host CPU. The GPU stays a desktop Intel or NVIDIA
one, and FingerprintJS scores that pair as a rare device. Run Windows personas
on an x86 Linux server or a Windows machine. A macOS persona always reports
Apple silicon, so run it on a Mac.

**Cores and memory are capped at the host's.** A persona never claims more
than the host has, so on a small server every persona reports the server's
core count next to a desktop GPU. Use a host with at least as many cores and
as much memory as the machines you want to present.

**Rendering is the host's.** Canvas, WebGL and WebGPU pixels, and rendering
speed, come from the host's GPU, or from the SwiftShader software renderer when
there is none. A page that times rendering or compares images with real
hardware can see this. Personas of one platform on one host draw the same
canvas image unless their fonts differ; `--fingerprint-noise` makes the hashes
differ. WebGL extensions with methods, such as `WEBGL_provoking_vertex`, are
missing when the host GPU lacks them.

**WebGL limits above an Apple GPU's.** WebGL reports the persona's limits on
every host, and a Windows or Linux persona claims more than Metal allows. On
Apple silicon, 8x multisampling fails (the GPU has 4), a shader with more than
1024 vertex uniform vectors fails to link, points above 511 pixels and
viewports above 16384 are clamped, and textures above 16384 pixels fail,
although a Linux persona claims 32768. SwiftShader and llvmpipe, the software
renderers on a server without a GPU, refused no size in testing.

**WebGPU without a GPU.** On a host with no GPU the claimed adapter is served
with SwiftShader underneath. Features and limits the software device cannot
back fail when a page calls `requestDevice()`.

**The browser window is the host's.** The persona sets `screen`, but the
window's size, position and frame (`outerWidth`, `screenX` and so on) come
from the real window. Keep the window inside the persona's screen, for example
with `--window-size`. With `headless=False` on a server, Xvfb is 1920x1080
unless you set `--fingerprint-screen-width` and `--fingerprint-screen-height`.

## Fonts and text

**Fonts that are not installed are absent.** A persona shows only the listed
fonts the host has, and nothing warns you. For a Windows persona, run
`apostate fonts install windows` ([FONTS.md](FONTS.md)).

**Text is drawn by the host's font engine.** Even with the real Windows fonts,
FreeType (Linux) or CoreText (macOS) draws the glyphs, so glyph metrics and
edges can differ slightly from Windows. Only a Windows host avoids it.

## Persona details

**Windows voices are English.** A Windows persona lists the local voices of a
US or UK English install, so one set to another language lacks its own voice.

**The keyboard layout is US.** `navigator.keyboard.getLayoutMap()` returns
the US layout of the persona's platform whatever the language. A profile you
write can carry another in `keyboard.layout_map`.

**Cameras and microphones are synthetic.** A claimed camera sends a dim
synthetic picture and a claimed microphone a quiet noise floor.

**Web Share on Linux.** Linux has no share sheet, so under a Windows or macOS
persona `navigator.share()` rejects at once, as if the user closed the sheet.

**An update changes the machine.** The seed is hashed with the Chromium and
catalogue versions, so after an update every seed and persistent profile
presents a different machine. Pin the package version to keep one.

## Sessions and network

**`new_context()` is incognito.** Pages from `launch()`'s `new_page()` open in
a normal profile, a temporary one deleted when the browser closes. A context
from `new_context()` is off-the-record, as in Playwright, and sites can tell.
Use `new_page()`, or `launch_persistent_context()` to keep a profile.

**WebRTC needs a SOCKS5 proxy with UDP.** Behind an HTTP, HTTPS or SOCKS4
proxy, or a SOCKS5 proxy without UDP ASSOCIATE, WebRTC gets no UDP at all.

**Widevine on Windows hosts is not verified.** It works on macOS arm64, Linux
x64 and Linux arm64. If it fails on Windows, the browser starts without DRM.

## Not done or not measured

- No mobile personas, no Intel Mac build and no 32-bit Windows build.
- A Windows or macOS GPU family on a Linux host with a real GPU has not been
  compared with a real machine of that family.
- The Windows NVIDIA family was measured on Chrome 153, and the Linux NVIDIA
  and SwiftShader families on 152.0.7977.82, not on 152.0.7977.83. Values that
  change between releases can differ.
- The packages' install path on a Windows machine has only been tested against
  a test archive.
