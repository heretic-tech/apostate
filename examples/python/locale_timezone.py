from apostate import launch

READ = """() => {
    const noon = new Date(Date.UTC(2026, 0, 15, 12, 0));
    return {
        language: navigator.language,
        languages: navigator.languages,
        timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        intlLocale: Intl.DateTimeFormat().resolvedOptions().locale,
        date: noon.toLocaleString(),
        number: (1234567.891).toLocaleString(),
        utcOffset: -noon.getTimezoneOffset(),
    };
}"""

# geoip=False skips the lookup; locale and timezone are set here instead.
with launch(fingerprint=42, fingerprint_platform="windows",
            locale="de-DE", timezone="Europe/Berlin", geoip=False) as browser:
    page = browser.new_page()
    response = page.goto("https://example.com")
    for name, value in page.evaluate(READ).items():
        print(f"{name:15} {value}")
    print(f"{'Accept-Language':15} {response.request.all_headers()['accept-language']}")
