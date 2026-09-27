from apostate import launch

READ = """async () => {
    const gl = document.createElement("canvas").getContext("webgl");
    const info = gl.getExtension("WEBGL_debug_renderer_info");
    const adapter = await navigator.gpu?.requestAdapter();

    let voices = speechSynthesis.getVoices();
    if (voices.length === 0) {
        await new Promise((done) => {
            speechSynthesis.addEventListener("voiceschanged", done, { once: true });
            setTimeout(done, 2000);
        });
        voices = speechSynthesis.getVoices();
    }

    const families = new Set((await queryLocalFonts()).map((font) => font.family));
    const devices = await navigator.mediaDevices.enumerateDevices();
    const layout = await navigator.keyboard.getLayoutMap();
    const audio = new AudioContext();
    const baseLatency = audio.baseLatency;
    await audio.close();

    return {
        webglVendor: gl.getParameter(info.UNMASKED_VENDOR_WEBGL),
        webglRenderer: gl.getParameter(info.UNMASKED_RENDERER_WEBGL),
        webgpuAdapter: adapter
            ? [adapter.info.vendor, adapter.info.architecture].join(" ")
            : "none",
        voices: voices.length,
        localVoices: voices.filter((voice) => voice.localService).map((voice) => voice.name),
        fontFamilies: families.size,
        fontCheck: Object.fromEntries(
            ["Segoe UI", "Calibri", "Helvetica Neue", "DejaVu Sans"]
                .map((family) => [family, families.has(family)])),
        mediaDevices: devices.map((device) => device.kind),
        keyboard: Object.fromEntries(["KeyQ", "KeyY", "Semicolon"].map((code) => [code, layout.get(code)])),
        baseLatency,
    };
}"""

# The voice list depends on the locale, so it is set here rather than taken from GeoIP.
with launch(fingerprint=42, fingerprint_platform="windows",
            locale="en-US", timezone="America/New_York", geoip=False) as browser:
    # queryLocalFonts() lists the fonts a page can see once this permission is granted.
    browser.contexts[0].grant_permissions(["local-fonts"])
    page = browser.new_page()
    page.goto("https://example.com")
    for name, value in page.evaluate(READ).items():
        print(f"{name:14} {value}")
