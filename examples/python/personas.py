from apostate import launch

READ = """async () => {
    const gl = document.createElement("canvas").getContext("webgl");
    const info = gl.getExtension("WEBGL_debug_renderer_info");
    const fonts = await queryLocalFonts();
    return {
        userAgent: navigator.userAgent,
        platform: navigator.platform,
        gpu: gl.getParameter(info.UNMASKED_RENDERER_WEBGL),
        screen: `${screen.width}x${screen.height} at ${devicePixelRatio}x`,
        fonts: new Set(fonts.map((font) => font.family)).size,
    };
}"""

for persona in ("windows", "macos", "linux"):
    with launch(fingerprint=42, fingerprint_platform=persona) as browser:
        # queryLocalFonts() lists the fonts a page can see once this permission is granted.
        browser.contexts[0].grant_permissions(["local-fonts"])
        page = browser.new_page()
        page.goto("https://example.com")
        print(persona)
        for name, value in page.evaluate(READ).items():
            print(f"  {name:10} {value}")
