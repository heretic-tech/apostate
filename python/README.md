# apostate

Python package for [Apostate](https://docs.apostate.dev), a Chromium build that
presents a Windows, macOS or Linux machine you choose. The package downloads
and verifies the browser, looks up the locale and timezone of your connection
or proxy, and returns a Playwright browser driven through Patchright.

Free and open source under GPL-3.0.

## Install

```bash
pip install apostate
apostate install                   # download the browser now instead of on first launch
apostate fonts install windows     # Linux and macOS hosts, for a Windows persona
```

Do not run `playwright install`: Apostate brings its own browser. On Python
3.10 to 3.13 on Linux, install `zstandard` or the `zstd` tool so the archive
can be unpacked.

## Launch

```python
from apostate import launch

browser = launch(fingerprint=42, fingerprint_platform="windows")
page = browser.new_page()
page.goto("https://example.com")
print(page.evaluate("navigator.platform"))   # Win32
browser.close()
```

- `fingerprint` is the seed: the same seed gives the same machine on every
  launch and host. Leave it out for a new machine each launch.
- `fingerprint_platform` is `windows`, `macos` or `linux`.
- `proxy` takes `http://`, `https://` or `socks5://` with the credential in
  the URL. GeoIP looks up the exit's locale and timezone; pass `locale` and
  `timezone` to set them yourself.
- `headless` is on by default. Headed on a Linux host with no display starts
  Xvfb for you.
- Use `browser.new_page()`. `new_context()` opens an incognito context, which
  sites can tell apart.

Keep a machine, its cookies and its logins between runs:

```python
from apostate import launch_persistent_context

context = launch_persistent_context("./profiles/alice", fingerprint_platform="windows")
```

Async versions: `launch_async()`, `launch_context_async()`,
`launch_persistent_context_async()`. Other keyword arguments go to
Playwright's `launch_persistent_context`.

## Command line

```bash
apostate install | path | info | clear
apostate run -- --fingerprint=42 --fingerprint-explain
apostate fonts install windows [--from DIR]
apostate fonts export-macos DIR
apostate fonts install macos --from DIR
apostate provision-drm [--list | --source DIR]
```

## Documentation

- [Python guide](https://docs.apostate.dev/guides/python) and
  [API reference](https://docs.apostate.dev/reference/python-api)
- [Personas](https://docs.apostate.dev/concepts/personas),
  [proxies](https://docs.apostate.dev/guides/proxies),
  [fonts](https://docs.apostate.dev/guides/fonts),
  [Linux servers](https://docs.apostate.dev/guides/linux-servers)
- [Installation and verifying downloads](https://docs.apostate.dev/installation)
- [Known gaps](https://docs.apostate.dev/known-gaps)

Source: https://github.com/heretic-tech/apostate
