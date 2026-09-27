from apostate import launch

READ = """() => {
    const gl = document.createElement("canvas").getContext("webgl");
    const info = gl.getExtension("WEBGL_debug_renderer_info");
    return {
        userAgent: navigator.userAgent,
        platform: navigator.platform,
        cores: navigator.hardwareConcurrency,
        memory: navigator.deviceMemory,
        screen: [screen.width, screen.height, screen.availWidth, screen.availHeight],
        gpu: gl.getParameter(info.UNMASKED_RENDERER_WEBGL),
    };
}"""

with launch(fingerprint=42, fingerprint_platform="windows") as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    for name, value in page.evaluate(READ).items():
        print(f"{name:10} {value}")
