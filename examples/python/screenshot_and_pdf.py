from pathlib import Path

from apostate import launch

OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)

with launch(fingerprint=42, fingerprint_platform="windows") as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.screenshot(path=OUT / "example.png", full_page=True)
    # page.pdf() works only in a headless browser, which is the default.
    page.pdf(path=OUT / "example.pdf", format="A4")

for name in ("example.png", "example.pdf"):
    print(f"out/{name}  {(OUT / name).stat().st_size} bytes")
