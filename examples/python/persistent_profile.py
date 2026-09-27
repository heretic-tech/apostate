from pathlib import Path

from apostate import launch_persistent_context

PROFILE = Path(__file__).resolve().parent / "profiles" / "demo"

READ = """() => {
    const gl = document.createElement("canvas").getContext("webgl");
    const info = gl.getExtension("WEBGL_debug_renderer_info");
    return [
        navigator.platform,
        `${navigator.hardwareConcurrency} cores`,
        `${screen.width}x${screen.height}`,
        gl.getParameter(info.UNMASKED_RENDERER_WEBGL),
    ].join(", ");
}"""


def read_machine():
    # No seed: the first launch draws one and writes it to PROFILE/apostate/identity.
    with launch_persistent_context(PROFILE, fingerprint_platform="windows") as context:
        page = context.new_page()
        page.goto("https://example.com")
        return page.evaluate(READ)


first = read_machine()
second = read_machine()
print("identity file ", (PROFILE / "apostate" / "identity").read_text().strip())
print("first launch  ", first)
print("second launch ", second)
print("same machine  ", first == second)
