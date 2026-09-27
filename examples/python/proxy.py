import os
import sys

from apostate import launch

proxy = os.environ.get("APOSTATE_PROXY")
if not proxy:
    print("APOSTATE_PROXY is not set. Set it to a proxy URL, for example "
          "socks5://user:pass@proxy.example:1080, and run this again.")
    sys.exit(0)

# With a proxy, the package looks up the proxy's exit and sets the locale and
# timezone from it before the browser starts.
with launch(fingerprint=42, fingerprint_platform="windows", proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://api.ipify.org/?format=json")
    print("exit IP   ", page.evaluate("JSON.parse(document.body.innerText).ip"))
    print("timezone  ", page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone"))
    print("languages ", page.evaluate("navigator.languages"))
