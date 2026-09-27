"""Launch a Windows persona and print what a page reads.

--headed launches with a window, on the Xvfb display the package starts when
the container has no display.
--profile DIR keeps the machine, cookies and storage in DIR between runs.
"""

import argparse

from apostate import launch, launch_persistent_context

READ = """() => {
    const gl = document.createElement("canvas").getContext("webgl");
    const info = gl.getExtension("WEBGL_debug_renderer_info");
    const ctx = document.createElement("canvas").getContext("2d");
    const hasFont = (family) => {
        ctx.font = "72px monospace";
        const width = ctx.measureText("mmmmmmmmmmlli").width;
        ctx.font = `72px "${family}", monospace`;
        return ctx.measureText("mmmmmmmmmmlli").width !== width;
    };
    return {
        userAgent: navigator.userAgent,
        platform: navigator.platform,
        cores: navigator.hardwareConcurrency,
        memory: navigator.deviceMemory,
        languages: navigator.languages,
        timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        screen: [screen.width, screen.height, screen.availWidth, screen.availHeight],
        gpu: gl.getParameter(info.UNMASKED_RENDERER_WEBGL),
        segoeUI: hasFont("Segoe UI"),
    };
}"""

parser = argparse.ArgumentParser()
parser.add_argument("--headed", action="store_true")
parser.add_argument("--profile", metavar="DIR")
args = parser.parse_args()

options = dict(
    fingerprint_platform="windows",
    locale="en-US",
    timezone="America/New_York",
    headless=not args.headed,
)
if args.profile:
    browser = launch_persistent_context(args.profile, **options)
else:
    browser = launch(fingerprint=42, **options)

with browser:
    page = browser.new_page()
    page.goto("https://example.com")
    for name, value in page.evaluate(READ).items():
        print(f"{name:10} {value}")
