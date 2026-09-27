from apostate import launch

# Only the sections named here change. Every other value is the host's.
PROFILE = {
    "cpu": {"logical_cores": 4},
    "memory": {"total_bytes": 8 * 1024**3},
    "screen": {
        "width": 1366, "height": 768,
        "avail_left": 0, "avail_top": 0, "avail_width": 1366, "avail_height": 728,
        "device_pixel_ratio": 1, "color_depth": 24,
    },
    "locale": {"application": "en-GB", "timezone": "Europe/London"},
}

READ = """() => ({
    cores: navigator.hardwareConcurrency,
    memory: navigator.deviceMemory,
    screen: [screen.width, screen.height, screen.availWidth, screen.availHeight],
    pixelRatio: devicePixelRatio,
    languages: navigator.languages,
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
})"""

with launch(profile=PROFILE, geoip=False) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    for name, value in page.evaluate(READ).items():
        print(f"{name:10} {value}")
