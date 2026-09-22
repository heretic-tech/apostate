from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import importlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any
from unittest import mock

# ``python -m unittest discover python/tests`` starts at the repository root;
# make the checkout package importable without requiring an editable install.
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

import apostate.config as config_module  # noqa: E402
import apostate.resolver as resolver_module  # noqa: E402
from apostate import (  # noqa: E402
    CATALOGUE_VERSION,
    CHROMIUM_VERSION,
    DEFAULT_PERSONA_BY_HOST,
    PROFILE_SCHEMA_VERSION,
    BinaryManager,
    BinaryNotFoundError,
    ConfigurationError,
    GeoIPError,
    LaunchError,
    ManifestError,
    ProfileError,
    UnpublishedArtifactError,
    UnsupportedArchiveError,
    default_persona_for_host,
    host_persona,
    launch,
    launch_async,
    launch_persistent_context,
    load_catalogue,
    resolve_geoip,
    resolve_profile,
    target_platform,
    translate_options,
)


class _FakeAsyncChromium:
    def __init__(self) -> None:
        self.options: dict[str, Any] | None = None

    async def launch(self, **options: Any) -> object:
        self.options = options
        return object()


class _FakeAsyncPlaywright:
    def __init__(self) -> None:
        self.chromium = _FakeAsyncChromium()

    async def start(self) -> "_FakeAsyncPlaywright":
        return self

    async def stop(self) -> None:
        return None


class _FakeSyncChromium:
    def __init__(self) -> None:
        self.options: dict[str, Any] | None = None

    def launch(self, **options: Any) -> dict[str, Any]:
        self.options = options
        return options

    def launch_persistent_context(self, **options: Any) -> dict[str, Any]:
        self.options = options
        return options


class _FakeSyncPlaywright:
    def __init__(self) -> None:
        self.chromium = _FakeSyncChromium()

    def start(self) -> "_FakeSyncPlaywright":
        return self

    def stop(self) -> None:
        return None


class _FakeResponse:
    """What ``urllib.request.urlopen`` hands back, and no more of it."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, amount: int | None = None) -> bytes:
        return self._payload if amount is None else self._payload[:amount]

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


@contextlib.contextmanager
def _release(assets: dict[str, bytes] | None = None) -> Any:
    """Serve exactly these URLs, 404 the rest, and reach no network at all.

    Yields the list of URLs asked for, in order, which is how a test asserts
    that the tag was tried before ``latest`` -- and that a pinned manifest
    asked for nothing.
    """
    served = dict(assets or {})
    requested: list[str] = []

    def fake_urlopen(url: Any, timeout: Any = None) -> _FakeResponse:
        target = url if isinstance(url, str) else url.full_url
        requested.append(target)
        if target not in served:
            # Closed before it is raised: urllib's response objects are
            # tempfile wrappers, so an unclosed one warns from the garbage
            # collector and buries the test output in ResourceWarnings.
            missing = urllib.error.HTTPError(target, 404, "Not Found", None, io.BytesIO(b""))
            missing.close()
            raise missing
        return _FakeResponse(served[target])

    with mock.patch("urllib.request.urlopen", fake_urlopen):
        yield requested


class _FakeSocks5Server:
    """One connection, one RFC 1928 handshake, one HTTP response.

    Small enough to read in full, which is the point: it is the thing that
    says whether the transport's handshake is right, so it must not be a
    second implementation of the same misunderstanding.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.credentials: tuple[bytes, bytes] | None = None
        self.address_type: int | None = None
        self.host: bytes | None = None
        self.port_requested: int | None = None
        self.request = b""
        self._socket = socket.socket()
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(1)
        self.port = self._socket.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> "_FakeSocks5Server":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> bool:
        self._thread.join(timeout=5)
        self._socket.close()
        return False

    def _serve(self) -> None:
        connection, _address = self._socket.accept()
        with connection:
            connection.settimeout(5)
            count = connection.recv(2)[1]
            connection.recv(count)
            connection.sendall(b"\x05\x02")
            connection.recv(1)
            user = connection.recv(connection.recv(1)[0])
            password = connection.recv(connection.recv(1)[0])
            self.credentials = (user, password)
            connection.sendall(b"\x01\x00")
            header = connection.recv(4)
            self.address_type = header[3]
            if self.address_type == 3:
                self.host = connection.recv(connection.recv(1)[0])
            else:
                self.host = connection.recv(4 if self.address_type == 1 else 16)
            self.port_requested = int.from_bytes(connection.recv(2), "big")
            connection.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x50")
            while b"\r\n\r\n" not in self.request:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                self.request += chunk
            body = json.dumps(self.payload).encode("utf-8")
            connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                               b"Content-Length: " + str(len(body)).encode("ascii")
                               + b"\r\nConnection: close\r\n\r\n" + body)


class _FakeGeoIPEndpoint:
    """One GeoIP site, serving a fixed reply and counting what reached it.

    Real sockets rather than an injected transport: the cascade's whole job is
    to survive a site that is down, and "down" is an HTTP status or a closed
    connection, not an exception a double chose to raise.
    """

    def __init__(self, payload: dict[str, Any] | None = None, *, status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.requests = 0
        self._socket = socket.socket()
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(8)
        self.url = f"http://127.0.0.1:{self._socket.getsockname()[1]}/json"
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> "_FakeGeoIPEndpoint":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> bool:
        self._stop.set()
        self._socket.close()
        self._thread.join(timeout=5)
        return False

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _address = self._socket.accept()
            except OSError:
                return
            with connection:
                connection.settimeout(5)
                self.requests += 1
                request = b""
                while b"\r\n\r\n" not in request:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    request += chunk
                body = json.dumps(self.payload if self.payload is not None else {}).encode("utf-8")
                connection.sendall(
                    f"HTTP/1.1 {self.status} X\r\nContent-Type: application/json\r\n".encode("ascii")
                    + b"Content-Length: " + str(len(body)).encode("ascii")
                    + b"\r\nConnection: close\r\n\r\n" + body)


class PackageContractTests(unittest.TestCase):
    def test_translate_options_uses_canonical_snake_case_and_stable_seed(self) -> None:
        config = translate_options(
            fingerprint="stable-seed:1",
            fingerprint_platform="win32",
            locale="en-US",
            timezone="America/New_York",
            geoip=False,
            proxy={"server": "http://proxy.example:8080", "bypass": "localhost"},
            headless=False,
            user_data_dir=Path("~/apostate-profile"),
            args=("--one", "--two"),
        )
        self.assertEqual(config.fingerprint, "stable-seed:1")
        self.assertEqual(config.fingerprint_platform, "windows")
        self.assertEqual(config.user_data_dir, str(Path("~/apostate-profile").expanduser()))
        self.assertEqual(config.to_dict()["fingerprint_platform"], "windows")
        self.assertEqual(config.args, ("--one", "--two"))
        with self.assertRaises(ConfigurationError):
            translate_options(fingerprint=-1)
        with self.assertRaises(ConfigurationError):
            translate_options(fingerprint="unstable seed")
        with self.assertRaises(ConfigurationError):
            translate_options(fingerprint=True)

    def test_launch_refuses_a_persistent_profile_before_touching_the_driver(self) -> None:
        """A persistent profile is a context, so launch() cannot carry one.

        Playwright refuses --user-data-dir too, but only after the binary is
        resolved and the driver started, and the error it raises names its
        own API. Both spellings are refused here first, and the refusal is
        observed with no binary and no driver available, which is what proves
        it fires before either is consulted.
        """
        launch_module = importlib.import_module("apostate.launch")
        for label, kwargs in (("keyword", {"user_data_dir": "/tmp/profile"}),
                              ("switch", {"args": ["--user-data-dir=/tmp/profile"]}),
                              ("bare switch", {"args": ["--user-data-dir"]})):
            with self.subTest(case=label):
                with self.assertRaises(ConfigurationError) as raised:
                    launch_module.launch(binary_path="/nonexistent/chrome", geoip=False,
                                         driver="a-driver-that-is-not-installed", **kwargs)
                self.assertIn("launch_persistent_context", str(raised.exception))

    def test_packaged_profile_schema_matches_authoritative_schema(self) -> None:
        packaged = PACKAGE_ROOT / "apostate" / "assets" / "profile.schema.json"
        authoritative = PACKAGE_ROOT.parent / "config" / "profile.schema.json"
        self.assertEqual(packaged.read_bytes(), authoritative.read_bytes())

    def test_installed_package_assets_resolve_without_source_resources(self) -> None:
        package_dir = PACKAGE_ROOT / "apostate"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staged_site = root / "site"
            shutil.copytree(package_dir, staged_site / "apostate")
            script = """
from apostate.profile_validation import validate_profile
from apostate.resolver import load_catalogue, resolve_profile
catalogue = load_catalogue()
assert catalogue['model'] == 'anchors+dispersion', catalogue['model']
assert catalogue['catalogue_version'] == 2, catalogue['catalogue_version']
validate_profile({'id': 'staged', 'platform': {'name': 'macOS'}})
# A seed and persona are handed to the browser process, which is the compositor;
# the package still composes nothing of its own, so the profile stays empty.
resolution = resolve_profile(fingerprint='staged-seed', fingerprint_platform='macos')
assert resolution.profile_id == 'native-composed', resolution.profile_id
assert resolution.profile == {}, resolution.profile
assert resolve_profile(fingerprint='host').profile_id == 'host-inherited'
print(catalogue['browser_build'])
"""
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(staged_site)
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), CHROMIUM_VERSION)

    def test_catalogue_exposes_the_version_two_anchor_and_dispersion_shape(self) -> None:
        catalogue = load_catalogue()
        self.assertEqual(catalogue["catalogue_version"], CATALOGUE_VERSION)
        self.assertEqual(catalogue["profile_schema_version"], PROFILE_SCHEMA_VERSION)
        self.assertEqual(catalogue["browser_build"], CHROMIUM_VERSION)
        self.assertEqual(catalogue["model"], "anchors+dispersion")
        for anchor in catalogue["anchors"]:
            self.assertEqual(
                set(anchor), {"id", "platform", "backend", "members", "rotation_status"}
            )
            self.assertIn(anchor["platform"], {"windows", "macos", "linux"})
            self.assertIn(anchor["rotation_status"], {"measured-safe", "single-member"})
            self.assertTrue(anchor["members"])
        self.assertIn(
            "macos-metal-apple-850a91233555", {anchor["id"] for anchor in catalogue["anchors"]}
        )
        # The exact axis list is not restated here: load_catalogue() already
        # refuses a catalogue whose axes are not the contract's, in order, so an
        # equality assertion would only duplicate the loader. What is worth
        # pinning is that the PACKAGED catalogue satisfies it -- the packages
        # ship a copy of resources/profiles/catalogue.json, and a copy that went
        # stale against the resolver is the regression that actually happens.
        axes = [axis["axis"] for axis in catalogue["axes"]]
        self.assertEqual(axes, list(resolver_module._DISPERSION_AXES))
        self.assertEqual(len(axes), len(set(axes)))
        anchor_axis = next(axis for axis in catalogue["axes"] if axis["axis"] == "gpu_identity")
        self.assertEqual(anchor_axis["conditioned_on"], ["anchor"])
        self.assertEqual(anchor_axis["servability"], "anchor-member")
        self.assertEqual(sorted(catalogue["policies"]), ["locale", "theme"])
        self.assertIn("en-us", catalogue["policies"]["locale"])

    def test_catalogue_axes_must_be_complete_and_ordered(self) -> None:
        # A dropped or reordered axis means the package and the browser disagree
        # about what was composed, which is worse than refusing to launch.
        contract = list(resolver_module._DISPERSION_AXES)
        reordered = [contract[1], contract[0], *contract[2:]]
        for label, axes in (("reordered", reordered), ("truncated", contract[:-1]),
                            ("unknown-axis", [*contract[:-1], "not_an_axis"])):
            with self.subTest(rejected=label), self.assertRaises(ProfileError):
                load_catalogue(self._catalogue(axes=[
                    {"axis": axis, "selection": "single", "servability": "none", "conditioned_on": []}
                    for axis in axes
                ]))

    @staticmethod
    def _catalogue(**overrides: Any) -> dict[str, Any]:
        catalogue = {
            "catalogue_id": "apostate",
            "catalogue_version": CATALOGUE_VERSION,
            "version": CATALOGUE_VERSION,
            "profile_schema_version": PROFILE_SCHEMA_VERSION,
            "browser_build": CHROMIUM_VERSION,
            "model": "anchors+dispersion",
            "anchors": [{
                "id": "macos-metal-apple-test", "platform": "macos", "backend": "ANGLE/Metal",
                "members": ["Apple M4 Max"], "member_count": 1, "rotation_status": "single-member",
            }],
            "axes": [
                {"axis": axis, "selection": "single", "servability": "none", "conditioned_on": []}
                for axis in resolver_module._DISPERSION_AXES
            ],
            "policies": {
                "locale": [{"id": "en-us"}],
                "theme": [{"id": "light"}],
            },
        }
        catalogue.update(overrides)
        return catalogue

    def test_catalogue_rejects_version_disagreement_and_the_retired_family_model(self) -> None:
        self.assertEqual(load_catalogue(self._catalogue())["model"], "anchors+dispersion")
        for label, catalogue in (
            ("catalogue_version", self._catalogue(catalogue_version=1, version=1)),
            ("profile_schema_version", self._catalogue(profile_schema_version=2)),
            ("families", self._catalogue(families=[{"id": "apple-metal-m2"}])),
            ("family_count", self._catalogue(family_count=14)),
            ("compatibility_acceptance", self._catalogue(compatibility_acceptance={})),
            ("model", self._catalogue(model="families")),
        ):
            with self.subTest(rejected=label), self.assertRaises(ProfileError):
                load_catalogue(catalogue)

    def test_seed_and_persona_reach_the_native_composition_switches(self) -> None:
        launch_module = importlib.import_module("apostate.launch")
        # The browser process is the compositor and the switches exist in the
        # shipped binary, so a seed is delivered rather than refused. Measured
        # on macos-arm64 152.0.7977.83: --fingerprint=42 yields en-GB /
        # Europe/London and repeats across launches.
        resolution = resolve_profile(fingerprint=12345, fingerprint_platform="windows")
        self.assertEqual(resolution.profile_id, "native-composed")
        self.assertEqual(resolution.profile, {})
        plan = launch_module._resolve_plan(
            translate_options(fingerprint=12345, fingerprint_platform="windows", geoip=False)
        )
        args = launch_module._native_args(plan)
        self.assertIn("--fingerprint=12345", args)
        self.assertIn("--fingerprint-platform=windows", args)
        # An envelope outranks the seed, so none may be sent alongside it.
        self.assertFalse([item for item in args if item.startswith("--apostate-profile=")])

        # A bare launch composes too, and says the identity will not persist.
        bare = resolve_profile()
        self.assertEqual(bare.profile_id, "native-composed")
        self.assertIn("does not persist", " ".join(bare.warnings))
        self.assertFalse([item for item in launch_module._native_args(
            launch_module._resolve_plan(translate_options(geoip=False))
        ) if item.startswith("--fingerprint=")])

        # Retired catalogue ids stay refused: there is no such thing to resolve.
        with self.assertRaisesRegex(ProfileError, "retired with catalogue version 1"):
            resolve_profile(profile="apple-metal-m2", fingerprint_platform="macos")

    #: The default claimed persona per host platform token, transcribed from
    #: ``DefaultPersonaForHost()`` in ``base/apostate/compose.cc`` (patch 0102)
    #: as given by its author. Declared here and deliberately NOT imported from
    #: ``apostate.DEFAULT_PERSONA_BY_HOST``: a test that reads the table the
    #: implementation uses only proves the implementation agrees with itself.
    #:
    #: The mapping lives in no file both implementations can read. The
    #: dispersion tables under ``resources/profiles/`` key option sets on a
    #: platform and so pin the token set, but not the host-to-persona edge, and
    #: 0102's author declined to add a data file that only tests would consume.
    #: This transcription is therefore the only pin, and the coupling is known
    #: and deliberate: if the C++ table moves, this literal must be moved with
    #: it, and until it is, the Python and Node mirrors fail here.
    _COMPOSE_CC_DEFAULT_PERSONA = {
        "macos": "macos",
        "windows": "windows",
        # The one default that is not the host's own OS.
        "linux": "windows",
    }
    #: What ``platform.system()`` reports for each of those host tokens, which
    #: is the other half of the chain: a correct table read from a misdetected
    #: host still claims the wrong OS.
    _HOST_SYSTEM_TOKENS = {"Darwin": "macos", "Windows": "windows", "Linux": "linux"}

    def test_resolver_default_platform_mirrors_the_browser_default(self) -> None:
        # The launch path sends no --fingerprint-platform when the caller named
        # none, so the browser applies its own default. A local resolution has
        # no browser to ask and must reproduce that default; these two
        # implementations of one table can drift silently, so pin them.
        self.assertEqual(dict(DEFAULT_PERSONA_BY_HOST), self._COMPOSE_CC_DEFAULT_PERSONA)

        for host_token, expected in self._COMPOSE_CC_DEFAULT_PERSONA.items():
            with self.subTest(host=host_token):
                self.assertEqual(default_persona_for_host(host_token), expected)
                with mock.patch.object(resolver_module, "host_persona", return_value=host_token):
                    # Every shape that reaches the default: no selector at all,
                    # a seed, and an explicit profile that declares no platform.
                    self.assertEqual(resolve_profile().platform, expected)
                    self.assertEqual(resolve_profile(fingerprint=12345).platform, expected)
                    self.assertEqual(
                        resolve_profile(profile={"id": "no-platform"}).platform, expected
                    )
                    # An explicit persona still outranks the table outright,
                    # exactly as request.platform does in Compose().
                    self.assertEqual(
                        resolve_profile(fingerprint=1, fingerprint_platform="linux").platform,
                        "linux",
                    )
                    # Host inheritance composes nothing, so the page really does
                    # see the host and reporting the host is correct. This is a
                    # deliberate exception to the table, not an oversight.
                    self.assertEqual(resolve_profile(fingerprint="host").platform, host_token)

        for system, host_token in self._HOST_SYSTEM_TOKENS.items():
            with self.subTest(system=system):
                with mock.patch.object(config_module.host_platform, "system", return_value=system):
                    self.assertEqual(host_persona(), host_token)
                    self.assertEqual(
                        resolve_profile(fingerprint=7).platform,
                        self._COMPOSE_CC_DEFAULT_PERSONA[host_token],
                    )

        # An unrecognised or empty host token is returned unchanged rather than
        # defaulted to windows. The C++ does the same so that its own
        # IsKnownPlatform() check fails and the launch inherits the host; a
        # mirror that defaulted here would compose profiles for platforms with
        # no corpus behind them.
        for unknown in ("", "freebsd", "android", "WINDOWS"):
            with self.subTest(unknown=unknown):
                self.assertEqual(default_persona_for_host(unknown), unknown)

    def test_default_launch_leaves_the_platform_switch_to_the_browser(self) -> None:
        # Inspect the built argv, not the source: the point is what the browser
        # receives. On a Linux host a default launch must emit no persona switch
        # at all, because that absence is what lets 0102's host-conditional
        # default apply. Passing --fingerprint-platform=linux here is
        # indistinguishable inside the browser from the pre-0102 default.
        launch_module = importlib.import_module("apostate.launch")
        with mock.patch.object(config_module.host_platform, "system", return_value="Linux"):
            args = launch_module._native_args(
                launch_module._resolve_plan(translate_options(fingerprint=12345, geoip=False))
            )
            self.assertEqual([item for item in args if "--fingerprint-platform" in item], [])
            # An explicit persona is still passed through unchanged, which is
            # the documented way to opt out of the Windows default on Linux.
            explicit = launch_module._native_args(
                launch_module._resolve_plan(
                    translate_options(fingerprint=12345, fingerprint_platform="linux", geoip=False)
                )
            )
            self.assertIn("--fingerprint-platform=linux", explicit)
            # And an alias is normalised to a token compose.cc accepts, rather
            # than forwarded verbatim for IsKnownPlatform() to reject.
            aliased = launch_module._native_args(
                launch_module._resolve_plan(
                    translate_options(fingerprint=1, fingerprint_platform="win32", geoip=False)
                )
            )
            self.assertIn("--fingerprint-platform=windows", aliased)

    def test_unknown_or_misspelled_fingerprint_switch_is_refused_at_config_time(self) -> None:
        # Chromium ignores an unknown switch silently, which would leave the
        # surface host-inherited while the caller believed it was set. A one
        # letter typo of a known switch is the same failure, so it is refused
        # by name; a switch that is neither is Chromium's business.
        with self.assertRaisesRegex(ConfigurationError, "not a switch this browser reads"):
            translate_options(args=["--fingerprint-gpu-vendr=Apple"], geoip=False)
        with self.assertRaisesRegex(ConfigurationError, "typo of --fingerprint-platform"):
            translate_options(args=["--fingeprint-platform=windows"], geoip=False)
        for accepted in ("--fingerprint-noise", "--fingerprint-platform=windows", "--disable-http2", "--lang=en-US"):
            with self.subTest(switch=accepted):
                self.assertEqual(translate_options(args=[accepted], geoip=False).args, (accepted,))

    def test_an_off_looking_noise_value_is_refused_because_it_turns_noise_on(self) -> None:
        # The binary tests whether --fingerprint-noise is present, never what
        # it is set to, so --fingerprint-noise=false enables readback noise.
        # A competitor's documentation recommends that exact string, so it
        # arrives in real scripts meaning the opposite of what it does.
        for spelling in ("--fingerprint-noise=false", "--fingerprint-noise=0", "--fingerprint-noise=off"):
            with self.subTest(switch=spelling):
                with self.assertRaisesRegex(ConfigurationError, "turns readback noise ON"):
                    translate_options(args=[spelling], geoip=False)
        self.assertEqual(translate_options(args=["--fingerprint-noise"], geoip=False).args,
                         ("--fingerprint-noise",))

    def test_host_inheritance_accepts_every_spelling_the_binary_accepts(self) -> None:
        for token in ("host", "off", "false", "0", "disable", "DISABLED"):
            with self.subTest(token=token):
                self.assertEqual(resolve_profile(fingerprint=token).profile_id, "host-inherited")
        self.assertEqual(resolve_profile(fingerprint=0).profile_id, "host-inherited")

    def test_host_inheritance_is_explicit_and_sends_no_profile_envelope(self) -> None:
        launch_module = importlib.import_module("apostate.launch")
        resolution = resolve_profile(fingerprint="host")
        self.assertEqual(resolution.profile, {})
        self.assertEqual(resolution.profile_id, "host-inherited")
        self.assertEqual(resolution.catalogue_version, CATALOGUE_VERSION)
        plan = launch_module._resolve_plan(translate_options(fingerprint="host", geoip=False))
        self.assertFalse(
            [item for item in launch_module._native_args(plan) if item.startswith("--apostate-profile=")]
        )
        localized = launch_module._resolve_plan(
            translate_options(fingerprint="host", locale="en-GB,en", timezone="Europe/London", geoip=False)
        )
        self.assertEqual(
            localized.profile,
            {"locale": {"accept_languages": "en-GB,en", "timezone": "Europe/London"}},
        )
        argument = next(item for item in launch_module._native_args(localized)
                        if item.startswith("--apostate-profile="))
        self.assertEqual(
            json.loads(base64.b64decode(argument.split("=", 1)[1]))["locale"]["timezone"],
            "Europe/London",
        )
        with self.assertRaises(ProfileError):
            resolve_profile(fingerprint="host", fingerprint_platform="windows")

    def test_locale_travels_as_an_override_never_as_a_partial_envelope(self) -> None:
        # The regression test for a whole class of defect. The browser's
        # InstallComposedProfile() returns early whenever --apostate-profile
        # carries device content and base/apostate/profile.cc leaves absent
        # fields absent, so a locale-only envelope means composition never runs:
        # eleven of twelve
        # axes fall back to the host and --fingerprint is silently ignored.
        # geoip defaults on, so that was the shape of almost every launch. The
        # assertion is on the emitted argv, because argv is what the browser
        # actually reads.
        launch_module = importlib.import_module("apostate.launch")

        def geoip(proxy: Any, timeout: Any) -> dict[str, Any]:
            return {"ip": "1.2.3.4", "languages": "en-US,en", "timezone": "Europe/London"}

        cases = (
            ("nothing requested", {"geoip": False}, [], False),
            ("seed only", {"fingerprint": 12345, "geoip": False}, ["--fingerprint=12345"], False),
            ("explicit locale", {"locale": "en-US", "geoip": False},
             ["--fingerprint-locale=en-US"], False),
            ("explicit timezone", {"timezone": "Europe/London", "geoip": False},
             ["--fingerprint-timezone=Europe/London"], False),
            ("geoip default, the common shape", {},
             ["--fingerprint-locale=en-US,en", "--fingerprint-timezone=Europe/London"], False),
            ("geoip default plus a seed", {"fingerprint": 12345},
             ["--fingerprint=12345", "--fingerprint-locale=en-US,en",
              "--fingerprint-timezone=Europe/London"], False),
            # Host mode composes nothing, so an envelope suppresses nothing
            # there and is the only carrier a locale has. Per-field overrides
            # are refused by the binary under host mode, so none are sent.
            ("host seed with a locale",
             {"fingerprint": "host", "locale": "en-GB,en", "geoip": False},
             ["--fingerprint=host"], True),
            # A profile the user authored is the one legitimate envelope: they
            # own its coherence, and bypassing composition is the documented
            # consequence rather than an accident.
            ("user-authored profile",
             {"profile": {"id": "mine", "platform": {"name": "macOS"}}, "geoip": False},
             [], True),
        )
        for label, options, expected, envelope in cases:
            with self.subTest(case=label):
                extra = {} if options.get("geoip") is False else {"geoip_provider": geoip}
                plan = launch_module._resolve_plan(translate_options(**options), **extra)
                args = launch_module._native_args(plan)
                self.assertEqual(
                    [item for item in args if item.startswith("--fingerprint")], expected)
                self.assertEqual(
                    bool([item for item in args if item.startswith("--apostate-profile=")]),
                    envelope)

        # An envelope and a seed are alternatives, not layers, and the browser
        # cannot report the conflict: it drops the seed without a word.
        with self.assertRaisesRegex(ProfileError, "cannot be combined"):
            launch_module._native_args(launch_module._resolve_plan(
                translate_options(profile={"id": "mine"}, args=["--fingerprint=99"], geoip=False)))

    def test_geoip_failure_sends_no_override_rather_than_raising_or_inventing(self) -> None:
        # Two behaviours met here. This package used to raise ``GeoIPError``,
        # which the spec permits -- the prohibition is on inventing, not on
        # continuing -- and the npm package invented en-US/UTC, which it does
        # not permit. Both now warn and send no override at all. That is a real
        # state only because GeoIP drives --fingerprint-locale and
        # --fingerprint-timezone rather than an --apostate-profile envelope: an
        # envelope suppressed composition whether or not it carried a locale, so
        # "send nothing" used to be indistinguishable from "send en-US/UTC".
        # Sending neither switch now leaves the composed profile's own drawn
        # pair in place, coherent because the same seed drew it. The npm package
        # pins the same seven cases in test/index.test.mjs.
        launch_module = importlib.import_module("apostate.launch")

        def failing(proxy: Any, timeout: Any) -> dict[str, Any]:
            raise RuntimeError("connect ECONNREFUSED 203.0.113.9:443")

        def timing_out(proxy: Any, timeout: Any) -> dict[str, Any]:
            raise TimeoutError("the read timed out")

        class _NoLookupMethod:
            pass

        cases = (
            ("lookup failure", failing, [], "GeoIP lookup failed"),
            ("timeout", timing_out, [], "timed out"),
            ("unavailable provider", _NoLookupMethod(), [], "no lookup method"),
            # Nothing failed in the next three: the provider answered, without
            # one of the two fields. The npm package re-invented the pair here
            # from a second site independent of its failure handler, and this
            # package discarded the field it did get by raising.
            ("a result with no timezone", lambda proxy, timeout: {"locale": "de-DE,de"},
             ["--fingerprint-locale=de-DE,de"], "resolved no timezone"),
            # A lookup never returns a locale: the launcher infers one from
            # the country. So "no locale" is only ever "no country", and that
            # is what the line says.
            ("a result with no country", lambda proxy, timeout: {"timezone": "Europe/Berlin"},
             ["--fingerprint-timezone=Europe/Berlin"], "returned no country"),
            # freeipapi answers with a UTC offset, which cannot drive the
            # switch: unresolved, not adjusted into something that looks like an
            # identifier.
            ("an offset instead of an identifier",
             lambda proxy, timeout: {"locale": "de-DE", "timezone": "+02:00"},
             ["--fingerprint-locale=de-DE"], "resolved no timezone"),
            # A country code derives its locale from config/country-locales.json,
            # which both packages ship, so a German exit is de-DE rather than
            # an invented en-DE or en-US.
            ("a country code and a timezone",
             lambda proxy, timeout: {"country_code": "DE", "timezone": "Europe/Berlin"},
             ["--fingerprint-locale=de-DE", "--fingerprint-timezone=Europe/Berlin"], None),
            # And a Malaysian one is ms. The 45-country hand table this
            # replaced named neither MY nor GT, so both exits used to be
            # announced as unresolved.
            ("a country the old hand table did not name",
             lambda proxy, timeout: {"country_code": "MY", "timezone": "Asia/Kuala_Lumpur"},
             ["--fingerprint-locale=ms", "--fingerprint-timezone=Asia/Kuala_Lumpur"], None),
            ("a country whose language is regional",
             lambda proxy, timeout: {"country_code": "GT", "timezone": "America/Guatemala"},
             ["--fingerprint-locale=es-419", "--fingerprint-timezone=America/Guatemala"], None),
        )
        for label, provider, expected, warning in cases:
            with self.subTest(case=label):
                stream = io.StringIO()
                with contextlib.redirect_stderr(stream):
                    plan = launch_module._resolve_plan(
                        translate_options(fingerprint=4242), geoip_provider=provider)
                args = launch_module._native_args(plan)
                self.assertEqual(
                    [item for item in args
                     if item.startswith("--fingerprint-locale")
                     or item.startswith("--fingerprint-timezone")],
                    expected)
                self.assertIn("--fingerprint=4242", args)
                self.assertFalse(any("en-US" in item or "UTC" in item for item in args))
                warnings = plan.diagnostics["warnings"]
                if warning is None:
                    self.assertEqual(warnings, [])
                    self.assertEqual(stream.getvalue(), "")
                else:
                    self.assertTrue(any(warning in entry for entry in warnings), warnings)
                    self.assertIn(warning, stream.getvalue())

        # A non-positive timeout is a caller bug rather than a network failure,
        # so it still raises, and a direct resolve_geoip() call keeps raising on
        # every failure: the launch path is what stopped raising, not the
        # primitive. Both classes stay exported for the direct callers.
        with self.assertRaisesRegex(GeoIPError, "greater than zero"):
            launch_module._resolve_plan(translate_options(), geoip_provider=failing,
                                        geoip_timeout=0)
        with self.assertRaisesRegex(GeoIPError, "GeoIP lookup failed"):
            resolve_geoip(failing)

    def test_explicit_inline_profile_validates_and_reaches_the_native_envelope(self) -> None:
        launch_module = importlib.import_module("apostate.launch")
        plan = launch_module._resolve_plan(
            translate_options(profile={"id": "explicit", "platform": {"name": "macOS"}},
                              fingerprint_platform="macos", geoip=False)
        )
        argument = next(item for item in launch_module._native_args(plan)
                        if item.startswith("--apostate-profile="))
        self.assertEqual(
            json.loads(base64.b64decode(argument.split("=", 1)[1])),
            {"id": "explicit", "platform": {"name": "macOS"}},
        )
        self.assertEqual(plan.diagnostics["profile_id"], "explicit")
        self.assertEqual(plan.diagnostics["platform"], "macos")
        self.assertIn("bypasses composition", " ".join(plan.diagnostics["warnings"]))
        with self.assertRaises(ProfileError):
            resolve_profile(profile={"id": "explicit", "unsupported_section": {}})

    def test_explicit_mapping_and_file_platform_must_match_request(self) -> None:
        profile = {"id": "explicit", "platform": {"name": "Windows"}}
        with self.assertRaises(ProfileError):
            resolve_profile(profile=profile, fingerprint_platform="macos")
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as profile_file:
            json.dump(profile, profile_file)
            profile_file.flush()
            with self.assertRaises(ProfileError):
                resolve_profile(profile=profile_file.name, fingerprint_platform="macos")

    def test_native_payload_strips_source_capture_without_mutating_diagnostics(self) -> None:
        launch_module = importlib.import_module("apostate.launch")
        profile = {"id": "explicit", "source_capture": "capture.json", "platform": {"name": "macOS"}}
        plan = launch_module._resolve_plan(
            translate_options(fingerprint_platform="macos", geoip=False),
            resolver=lambda config: profile,
        )
        argument = next(item for item in launch_module._native_args(plan) if item.startswith("--apostate-profile="))
        native = json.loads(base64.b64decode(argument.split("=", 1)[1]))
        self.assertEqual(native["id"], "explicit")
        self.assertNotIn("source_capture", native)
        self.assertEqual(plan.profile["source_capture"], "capture.json")
        self.assertEqual(plan.diagnostics["profile_id"], "explicit")

    def test_a_pinned_manifest_is_never_replaced_by_a_fetched_one(self) -> None:
        # Passing a manifest is how a caller says "this exact build". Going to
        # the network for a different digest when that one publishes nothing
        # would unpin it, so the refusal stands and no URL is touched.
        calls: list[str] = []
        manifest = {
            "package_version": "0.1.0",
            "chromium_version": CHROMIUM_VERSION,
            "catalogue_version": CATALOGUE_VERSION,
            "artifacts": {},
            "status": "unpublished",
        }
        with tempfile.TemporaryDirectory() as temporary, _release() as requested:
            # search_roots=() because discovery is deliberately not hermetic:
            # a browser in /Applications or /opt/apostate answers the
            # publication question the other way, and on a machine that has
            # one this assertion is testing the wrong thing. Passing no roots
            # is how a caller says "only what I configured".
            manager = BinaryManager(cache_dir=temporary, manifest=manifest, search_roots=(),
                                    downloader=lambda source: calls.append(source))
            with self.assertRaisesRegex(UnpublishedArtifactError, "unpublished"):
                manager.ensure(target="macos-arm64")
        self.assertEqual(calls, [])
        self.assertEqual(requested, [])

    def test_an_unpublished_baked_manifest_installs_from_the_release_manifest(self) -> None:
        """The dead end 0.1.0 shipped, and the way out of it.

        The manifest baked into a package is written when the release is cut,
        which is after the package is built, so 0.1.0 shipped
        ``{"status": "unpublished"}`` and every install said so. A launcher
        version is not a binary version: 0.1.1 installs the binaries
        published as v0.1.0. So when the baked manifest publishes nothing,
        the manifest the release published beside the archive answers -- the
        tag for this package's own version first, then ``latest``.
        """
        binary_module = importlib.import_module("apostate.binary")
        archive_bytes = self._zip_archive({
            "apostate-152.0.7977.83-macos-arm64/Chromium.app/Contents/MacOS/Chromium": b"native binary",
        })
        urls = dict(binary_module.release_manifest_urls("macos-arm64"))
        latest = urls["release-latest"]
        served = {
            latest: self._artifact_manifest(archive_bytes),
            latest.removesuffix(".manifest.json"): archive_bytes,
        }
        with tempfile.TemporaryDirectory() as temporary:
            manager = BinaryManager(cache_dir=temporary, search_roots=())
            stream = io.StringIO()
            with _release(served) as requested, contextlib.redirect_stderr(stream):
                executable = manager.ensure(target="macos-arm64")
            self.assertEqual(executable.read_bytes(), b"native binary")
            # The tag for this package's version is asked for first and 404s,
            # because there is no v0.1.1 binary release; `latest` answers.
            self.assertEqual(requested[:2], [urls["release-tag"], latest])
            # And the archive came from the release the manifest came from,
            # not from `latest` resolved a second time.
            self.assertEqual(requested[2], latest.removesuffix(".manifest.json"))
            # An install that did not use a pinned digest says so, once,
            # before it downloads 150 MB on the strength of a weaker one.
            self.assertEqual(
                stream.getvalue(),
                f"apostate: release manifest fetched from {latest} (no published "
                "manifest is baked into this package). A fetched manifest verifies "
                "transport integrity only; for provenance run: gh attestation verify "
                "apostate-152.0.7977.83-macos-arm64.zip --repo heretic-tech/apostate\n")

            with _release(served):
                report = manager.info(target="macos-arm64")
            self.assertTrue(report["available"])
            self.assertTrue(report["cached"])
            self.assertEqual(report["manifest_source"], "release-latest")
            self.assertEqual(report["manifest_url"], latest)
            self.assertEqual(report["manifest_urls_tried"],
                             [urls["release-tag"], latest])
            self.assertEqual(report["manifest_trust"], "transport-integrity")
            self.assertEqual(
                report["provenance"],
                "gh attestation verify apostate-152.0.7977.83-macos-arm64.zip "
                "--repo heretic-tech/apostate")
            # The manifest is the binary release's, so it keeps that release's
            # version while this package is 0.1.1. They are different lines.
            self.assertEqual(report["package_version"], "0.1.0")
            self.assertNotEqual(report["package_version"], config_module.PACKAGE_VERSION)
            # A cached install stays visible with no network at all: the
            # marker vouches for it on its own.
            with _release() as offline:
                self.assertEqual(manager.ensure(target="macos-arm64"), executable)
            self.assertEqual(offline, [])

            # A mirror still wins for the bytes, and only for the bytes: the
            # manifest keeps coming from the release, so the digest and the
            # archive stay two different places even behind one.
            with mock.patch.dict(os.environ,
                                 {"APOSTATE_DOWNLOAD_BASE_URL": "https://mirror.test/a/"}):
                with _release(served) as mirrored:
                    report = manager.info(target="macos-arm64")
            self.assertEqual(report["artifact_url"],
                             "https://mirror.test/a/apostate-152.0.7977.83-macos-arm64.zip")
            self.assertEqual(mirrored, [urls["release-tag"], latest])

    def test_an_unreachable_release_manifest_names_both_urls_it_tried(self) -> None:
        binary_module = importlib.import_module("apostate.binary")
        calls: list[str] = []
        urls = [url for _source, url in binary_module.release_manifest_urls("macos-arm64")]
        with tempfile.TemporaryDirectory() as temporary:
            manager = BinaryManager(cache_dir=temporary, search_roots=(),
                                    downloader=lambda source: calls.append(source))
            with _release() as requested:
                with self.assertRaises(UnpublishedArtifactError) as raised:
                    manager.ensure(target="macos-arm64")
        self.assertEqual(
            str(raised.exception),
            "Release manifest is unpublished; Apostate binary artifacts are not "
            "available for acquisition. No release manifest for "
            "apostate-152.0.7977.83-macos-arm64.zip could be fetched. Tried: "
            + ", ".join(urls))
        self.assertEqual(requested, urls)
        self.assertEqual(calls, [])

    def test_a_fetched_manifest_for_another_build_is_refused_by_name(self) -> None:
        # An integrity check that accepts any manifest it is handed is not
        # one. Every field that says which bytes these are is checked --
        # except package_version, which is the whole point of fetching.
        binary_module = importlib.import_module("apostate.binary")
        urls = dict(binary_module.release_manifest_urls("macos-arm64"))
        latest = urls["release-latest"]
        archive_bytes = b"not really an archive"
        for label, overrides, detail in (
            ("another Chromium", {"chromium_version": "152.0.7977.84"},
             "chromium_version is 152.0.7977.84, not 152.0.7977.83"),
            ("another catalogue", {"catalogue_version": 3},
             "catalogue_version is 3, not 2"),
            ("another platform", {"platform": "linux-x64"},
             "platform is linux-x64, not macos-arm64"),
            ("another archive", {"artifact": "apostate-152.0.7977.83-linux-x64.tar.zst"},
             "artifact is apostate-152.0.7977.83-linux-x64.tar.zst, not "
             "apostate-152.0.7977.83-macos-arm64.zip"),
            ("an uppercase digest", {"sha256": "A" * 64},
             "sha256 is not a 64-character lowercase digest"),
            ("no platform at all", {"platform": None},
             "platform is absent, not macos-arm64"),
        ):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as temporary:
                manager = BinaryManager(cache_dir=temporary, search_roots=())
                served = {latest: self._artifact_manifest(archive_bytes, **overrides)}
                with _release(served):
                    with self.assertRaises(ManifestError) as raised:
                        manager.ensure(target="macos-arm64")
                self.assertEqual(
                    str(raised.exception),
                    f"release manifest at {latest} does not describe this package: {detail}")

    def test_launch_reports_the_unpublished_package_before_the_missing_driver(self) -> None:
        """The true blocker first, not whichever check happened to run first.

        Loading the driver first sent a new user to install Patchright when
        the real answer was that there is no binary to drive, and no
        developer machine could reproduce it because a driver is always
        already importable. This is asserted through launch(), not through
        BinaryManager: the ordering is what broke, and only a caller can see
        it.
        """
        launch_module = importlib.import_module("apostate.launch")
        binary_module = importlib.import_module("apostate.binary")
        with tempfile.TemporaryDirectory() as temporary:
            # launch() has no search_roots parameter, so the documented
            # locations are emptied for the duration: see the note above.
            with mock.patch.object(binary_module, "_well_known_roots", lambda target: ()):
                with _release():
                    with self.assertRaises(UnpublishedArtifactError):
                        launch_module.launch(cache_dir=temporary, geoip=False,
                                             fingerprint="host",
                                             driver="a-driver-that-is-not-installed")

    def test_unpublished_platform_refuses_other_platform_artifacts(self) -> None:
        records = [{
            "platform": target,
            "artifact": f"apostate-{CHROMIUM_VERSION}-{target}.tar.zst",
            "sha256": "0" * 64,
        } for target in ("linux-x64", "linux-arm64", "macos-arm64")]
        identity = {
            "package_version": "0.1.0",
            "chromium_version": CHROMIUM_VERSION,
            "catalogue_version": CATALOGUE_VERSION,
        }
        calls: list[str] = []
        with tempfile.TemporaryDirectory() as temporary:
            for shape in (
                {"artifacts": {record["platform"]: record for record in records}},
                {"artifacts": records},
                records[0],
            ):
                with self.subTest(shape=shape):
                    manifest = {**identity, **shape}
                    manager = BinaryManager(cache_dir=temporary, manifest=manifest,
                                            search_roots=(),
                                            downloader=lambda source: calls.append(source))
                    with self.assertRaisesRegex(UnpublishedArtifactError, "windows-x64.*not published for this release"):
                        manager.ensure(target="windows-x64")
        self.assertEqual(calls, [])

    def test_the_packaged_release_manifest_still_describes_this_package(self) -> None:
        """The baked asset must not drift away from the package around it.

        A stale ``catalogue_version`` or ``chromium_version`` here turns "no
        artifact is published yet" into "this manifest is for another
        package", which is a lie. ``package_version`` is deliberately not in
        this list: the manifest names the binary release it installs from,
        and this package's version moves without it.
        """
        binary_module = importlib.import_module("apostate.binary")
        packaged = json.loads(
            (Path(binary_module.__file__).parent / "assets"
             / "release-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(packaged["chromium_version"], CHROMIUM_VERSION)
        self.assertEqual(packaged["catalogue_version"], CATALOGUE_VERSION)

    def test_cache_paths_and_clear_cache_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            manager = BinaryManager(cache_dir=cache)
            root, install, marker = manager._paths("linux-x64", CHROMIUM_VERSION)
            self.assertEqual(root, cache / CHROMIUM_VERSION / "linux-x64")
            self.assertEqual(install, root / "install")
            self.assertEqual(marker, root / "install.json")
            (cache / "stale").mkdir(parents=True)
            manager.clear()
            self.assertFalse(cache.exists())
        self.assertEqual(target_platform("darwin-arm64"), "macos-arm64")

    def test_install_keeps_the_whole_distribution_not_just_the_executable(self) -> None:
        # Chromium cannot start from a lone copied executable: it needs its
        # framework, ICU data and .pak resources. A cache that holds only the
        # executable is a cache that cannot launch.
        archive_bytes = self._zip_archive({
            "apostate-test/Chromium.app/Contents/MacOS/Chromium": b"native binary",
            "apostate-test/Chromium.app/Contents/Resources/icudtl.dat": b"icu",
            "apostate-test/resources/en-US.pak": b"pak",
        })
        with tempfile.TemporaryDirectory() as temporary:
            manager = BinaryManager(cache_dir=temporary,
                                    manifest=self._zip_manifest(archive_bytes),
                                    downloader=lambda source: archive_bytes)
            executable = manager.ensure(target="macos-arm64")
            install = Path(temporary) / CHROMIUM_VERSION / "macos-arm64" / "install"
            # The wrapper directory is stripped; everything inside it survives.
            self.assertEqual(executable, install / "Chromium.app/Contents/MacOS/Chromium")
            self.assertEqual((install / "Chromium.app/Contents/Resources/icudtl.dat").read_bytes(), b"icu")
            self.assertEqual((install / "resources/en-US.pak").read_bytes(), b"pak")
            self.assertTrue(os.access(executable, os.X_OK))

    def test_tampered_cached_binary_is_rebuilt(self) -> None:
        archive_bytes = self._zip_archive({
            "apostate-test/Chromium.app/Contents/MacOS/Chromium": b"native binary",
        })
        with tempfile.TemporaryDirectory() as temporary:
            calls: list[str] = []
            manager = BinaryManager(
                cache_dir=temporary,
                manifest=self._zip_manifest(archive_bytes),
                downloader=lambda source: calls.append(source) or archive_bytes,
            )
            executable = manager.ensure(target="macos-arm64")
            executable.write_bytes(b"tampered")
            rebuilt = manager.ensure(target="macos-arm64")
            self.assertEqual(rebuilt.read_bytes(), b"native binary")
            self.assertEqual(len(calls), 2)

    def test_extraction_refuses_an_escaping_symlink_but_keeps_a_contained_one(self) -> None:
        # The macOS bundle reaches its framework through five relative symlinks,
        # so prohibition is not an option; containment is what is enforced.
        contained = self._zip_archive(
            {"apostate-test/Chromium.app/Contents/MacOS/Chromium": b"native binary"},
            links={"apostate-test/Chromium.app/Contents/Frameworks/Current": "../MacOS"},
        )
        escaping = self._zip_archive(
            {"apostate-test/Chromium.app/Contents/MacOS/Chromium": b"native binary"},
            links={"apostate-test/escape": "../../../../etc"},
        )
        absolute = self._zip_archive(
            {"apostate-test/Chromium.app/Contents/MacOS/Chromium": b"native binary"},
            links={"apostate-test/absolute": "/etc/passwd"},
        )
        with tempfile.TemporaryDirectory() as temporary:
            manager = BinaryManager(cache_dir=Path(temporary) / "ok",
                                    manifest=self._zip_manifest(contained),
                                    downloader=lambda source: contained)
            executable = manager.ensure(target="macos-arm64")
            link = executable.parent.parent / "Frameworks" / "Current"
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.readlink(link), "../MacOS")
            for label, payload in (("escaping", escaping), ("absolute", absolute)):
                with self.subTest(rejected=label):
                    rejecting = BinaryManager(cache_dir=Path(temporary) / label,
                                              manifest=self._zip_manifest(payload),
                                              downloader=lambda source, data=payload: data)
                    with self.assertRaises(UnsupportedArchiveError):
                        rejecting.ensure(target="macos-arm64")

    def _zip_archive(self, files: dict[str, bytes],
                     links: dict[str, str] | None = None) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, payload in files.items():
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (0o100755 << 16)
                archive.writestr(info, payload)
            for name, target in (links or {}).items():
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (0o120777 << 16)
                archive.writestr(info, target)
        return buffer.getvalue()

    def _zip_manifest(self, archive_bytes: bytes) -> dict[str, Any]:
        return {
            "package_version": "0.1.0",
            "chromium_version": CHROMIUM_VERSION,
            "catalogue_version": CATALOGUE_VERSION,
            "platform": "macos-arm64",
            "artifact": "apostate-test.zip",
            "sha256": hashlib.sha256(archive_bytes).hexdigest(),
        }

    def _artifact_manifest(self, archive_bytes: bytes, target: str = "macos-arm64",
                           **overrides: Any) -> bytes:
        """What a release publishes beside each archive, as bytes.

        ``package_version`` is the binary release's -- 0.1.0 -- and stays
        there while the launcher is 0.1.1, because that difference is the
        thing the fallback exists to serve.
        """
        binary_module = importlib.import_module("apostate.binary")
        payload: dict[str, Any] = {
            "artifact": binary_module.policy_artifact_name(target),
            "build_manifest_sha256": "b" * 64,
            "catalogue_version": CATALOGUE_VERSION,
            "chromium_version": CHROMIUM_VERSION,
            "package_version": "0.1.0",
            "patch_series_sha256": "c" * 64,
            "platform": target,
            "sha256": hashlib.sha256(archive_bytes).hexdigest(),
            "source_revision": "d" * 40,
        }
        payload.update(overrides)
        return json.dumps({key: value for key, value in payload.items()
                           if value is not None}).encode("utf-8")

    def _plant_payload(self, root: Path, *, version: str = CHROMIUM_VERSION,
                       build_record: bool = True, resources: bool = True,
                       executable: bool = True) -> Path:
        """Write a linux-x64 payload tree and return its ``chrome``.

        Mirrors what scripts/package-artifact.sh stages: the executable, the
        build record under ``build/``, and ``resources/profiles/``.
        """
        root.mkdir(parents=True, exist_ok=True)
        chrome = root / "chrome"
        # A real shell script, so a fallback `--version` probe has something
        # to answer with when no build record is planted.
        chrome.write_text(f'#!/bin/sh\necho "Chromium {version}"\n', encoding="utf-8")
        if executable:
            chrome.chmod(0o755)
        if build_record:
            (root / "build").mkdir(exist_ok=True)
            (root / "build" / "MANIFEST.lock").write_text(
                "# Generated by scripts/build.sh. Records what this build resolved.\n"
                f'chromium_version     = "{version}"\n'
                f'patch_series_sha256  = "{"a" * 64}"\n'
                "\n[outputs]\n"
                '"chrome" = "0123"\n',
                encoding="utf-8")
        if resources:
            (root / "resources" / "profiles").mkdir(parents=True, exist_ok=True)
            (root / "resources" / "profiles" / "catalogue.json").write_text(
                "{}", encoding="utf-8")
        return chrome

    def test_discovery_adopts_an_apostate_payload_and_never_a_stock_chromium(self) -> None:
        """Stock Chrome must never be adopted, and that is the whole point.

        The executable is named ``chrome``, the bundle ``Chromium.app``, and
        Chromium 152.0.7977.83 exists upstream too, so nothing about the file
        distinguishes ours from stock. What does is the payload staged beside
        it. Getting this wrong is not a missing feature: launching stock
        Chrome with Apostate's switches is a session with no protection at
        all and nothing on screen to say so.
        """
        binary_module = importlib.import_module("apostate.binary")
        with tempfile.TemporaryDirectory() as temporary:
            roots = Path(temporary) / "roots"
            cache = Path(temporary) / "cache"
            ours = self._plant_payload(roots / "apostate-152-linux-x64")
            stock = self._plant_payload(roots / "stock", build_record=False, resources=False)
            wrong = self._plant_payload(roots / "old-build", version="151.0.0.1")

            report = binary_module.discovery_report(target="linux-x64", cache_dir=cache,
                                                    search_roots=[roots])
            self.assertEqual(report["found"], {
                "executable": str(ours),
                "source": "well-known",
                "chromium_version": CHROMIUM_VERSION,
                "payload_root": str(roots / "apostate-152-linux-x64"),
            })
            self.assertEqual(report["order"], ["argument", "environment", "cache", "well-known"])
            reasons = {item["path"]: item["reason"] for item in report["rejected"]}
            self.assertEqual(reasons[str(stock)],
                             "no Apostate payload beside it "
                             "(build/MANIFEST.lock or resources/profiles/catalogue.json)")
            self.assertEqual(reasons[str(wrong)],
                             f"reports Chromium 151.0.0.1, not {CHROMIUM_VERSION}")

            # And a launch takes it: an unpublished manifest no longer answers
            # "nothing is published" over a browser the user already has, and
            # nothing is downloaded -- or fetched -- to find that out.
            def refuse(source: str) -> bytes:
                raise AssertionError(f"downloaded {source} despite an install on disk")

            manager = BinaryManager(cache_dir=cache, search_roots=[roots], downloader=refuse)
            with _release() as requested:
                self.assertEqual(manager.ensure(target="linux-x64"), ours)
                manager.assert_published(target="linux-x64")
            self.assertEqual(requested, [])

            # `info` still answers the question a user is asking -- where is
            # the browser -- on a release that publishes nothing anywhere,
            # and says which URLs it asked.
            with _release():
                info = manager.info(target="linux-x64")
            self.assertFalse(info["available"])
            self.assertEqual(info["executable"], str(ours))
            self.assertEqual(info["executable_source"], "well-known")
            self.assertEqual(
                info["manifest_urls_tried"],
                [url for _source, url in binary_module.release_manifest_urls("linux-x64")])

    def test_discovery_finds_a_macos_bundle_by_its_info_plist(self) -> None:
        # The macOS payload carries no build record inside the bundle, so the
        # version comes from Info.plist -- and nothing is executed to get it,
        # which is what keeps a stock Chromium.app from ever being started.
        binary_module = importlib.import_module("apostate.binary")
        with tempfile.TemporaryDirectory() as temporary:
            roots = Path(temporary) / "Applications"
            bundle = roots / "Chromium.app" / "Contents"
            (bundle / "MacOS").mkdir(parents=True)
            executable = bundle / "MacOS" / "Chromium"
            executable.write_bytes(b"native binary")
            executable.chmod(0o755)
            plist = ("<plist><dict><key>CFBundleShortVersionString</key>"
                     f"<string>{CHROMIUM_VERSION}</string></dict></plist>")
            (bundle / "Info.plist").write_text(plist, encoding="utf-8")
            (roots / "resources" / "profiles").mkdir(parents=True)
            (roots / "resources" / "profiles" / "catalogue.json").write_text("{}",
                                                                             encoding="utf-8")
            cache = Path(temporary) / "cache"
            found = binary_module.discover_binary(target="macos-arm64", cache_dir=cache,
                                                  search_roots=[roots])
            self.assertEqual(found, executable)

            # Remove the marker and the same bundle is refused: the version on
            # its own cannot tell ours from upstream's.
            (roots / "resources" / "profiles" / "catalogue.json").unlink()
            report = binary_module.discovery_report(target="macos-arm64", cache_dir=cache,
                                                    search_roots=[roots])
            self.assertIsNone(report["found"])
            self.assertEqual([item["reason"] for item in report["rejected"]],
                             ["no Apostate payload beside it "
                              "(build/MANIFEST.lock or resources/profiles/catalogue.json)"])

    def test_a_named_bundle_or_payload_root_resolves_to_the_browser_inside(self) -> None:
        """Three spellings of "this browser", because users have all three.

        ``Chromium.app`` is a directory, so naming the one thing in the
        archive that looks like the browser was refused for not being a file
        -- true of every macOS application, and useless as an answer. The
        directory the archive unpacks to was refused the same way, which left
        someone who had extracted it by hand with a tree on disk and no way
        to say "that one".
        """
        binary_module = importlib.import_module("apostate.binary")
        launch_module = importlib.import_module("apostate.launch")
        with tempfile.TemporaryDirectory() as temporary:
            payload_root = Path(temporary) / "apostate-152.0.7977.83-macos-arm64"
            bundle = payload_root / "Chromium.app"
            executable = bundle / "Contents" / "MacOS" / "Chromium"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            empty = Path(temporary) / "empty"
            empty.mkdir()
            missing = Path(temporary) / "missing"

            for named in (executable, bundle, payload_root):
                with self.subTest(named=named.name):
                    self.assertEqual(
                        binary_module.resolve_named_binary(named, "binary_path", "macos-arm64"),
                        (executable, None))
                    self.assertEqual(
                        launch_module._resolve_executable(named, target="macos-arm64"),
                        executable)
                    with mock.patch.dict(os.environ, {"APOSTATE_BINARY": str(named)}):
                        manager = BinaryManager(cache_dir=temporary, search_roots=())
                        self.assertEqual(manager.ensure(target="macos-arm64"), executable)
                        self.assertEqual(manager.discovery(target="macos-arm64")["found"],
                                         {"executable": str(executable),
                                          "source": "environment",
                                          "chromium_version": None, "payload_root": None})

            self.assertEqual(
                binary_module.resolve_named_binary(empty, "the configured path", "macos-arm64"),
                (None, "the configured path names a directory with no browser inside it "
                       "(expected Chromium.app, chrome or chrome.exe)"))
            self.assertEqual(
                binary_module.resolve_named_binary(missing, "APOSTATE_BINARY", "macos-arm64"),
                (None, "APOSTATE_BINARY does not name a file"))
            with mock.patch.dict(os.environ, {"APOSTATE_BINARY": str(empty)}):
                with self.assertRaises(BinaryNotFoundError) as refused:
                    BinaryManager(cache_dir=temporary,
                                  search_roots=()).ensure(target="macos-arm64")
            self.assertEqual(
                str(refused.exception),
                "APOSTATE_BINARY names a directory with no browser inside it "
                f"(expected Chromium.app, chrome or chrome.exe): {empty}")
            with self.assertRaises(LaunchError) as failed:
                launch_module._resolve_executable(empty, target="macos-arm64")
            self.assertTrue(str(failed.exception).startswith(
                "binary_path names a directory with no browser inside it "
                f"(expected Chromium.app, chrome or chrome.exe): {empty}."),
                str(failed.exception))

    def test_the_stdlib_transport_speaks_socks5_with_username_password_auth(self) -> None:
        """A residential SOCKS5 exit is the commonest proxy this package sees.

        The standard library has no SOCKS client, so ``geoip=True`` behind
        one used to fail as an unsupported configuration, and requiring
        PySocks would make the headline feature an optional extra. The
        transport performs RFC 1928 CONNECT with RFC 1929 authentication
        itself; this drives it against a socket that speaks the protocol, so
        the handshake bytes are asserted rather than assumed.
        """
        geoip_module = importlib.import_module("apostate._prelaunch_geoip")
        for scheme, endpoint, address_type, expected_host in (
            ("socks5h", "http://exit.example.test/json", 3, b"exit.example.test"),
            # The load-bearing one. ``socks5://`` is the spelling a caller
            # writes, because it is what Chromium's --proxy-server takes, and
            # this used to resolve the name here and send a literal. On a dual
            # stacked endpoint getaddrinfo answered with the AAAA record first,
            # and a residential exit with no IPv6 route refused the CONNECT
            # with "general SOCKS server failure" -- so geoip=True failed
            # behind a proxy that curl reached through socks5h:// at the same
            # moment. The name goes to the proxy under either spelling now,
            # which also keeps the lookup's own DNS off this network.
            ("socks5", "http://exit.example.test/json", 3, b"exit.example.test"),
            ("socks5", "http://127.0.0.1:9/json", 1, b"\x7f\x00\x00\x01"),
        ):
            with self.subTest(scheme=scheme):
                server = _FakeSocks5Server({"ip": "203.0.113.7", "country_code": "MY"})
                with server:
                    result = geoip_module.resolve_prelaunch_geoip(
                        proxy=f"{scheme}://geo%20user:p%40ss%2Fword@127.0.0.1:{server.port}",
                        endpoint=endpoint, timeout=5.0)
                self.assertEqual(result.country_code, "MY")
                self.assertEqual(result.lookup_mode, "proxy")
                # RFC 1929 is sent decoded, not as the percent-encoded URL text.
                self.assertEqual(server.credentials, (b"geo user", b"p@ss/word"))
                self.assertEqual(server.address_type, address_type)
                self.assertEqual(server.host, expected_host)
                self.assertEqual(server.port_requested, 80 if address_type == 3 else 9)
                self.assertIn(b"Host: ", server.request)
                # And the credential never reaches a diagnostic.
                self.assertEqual(result.diagnostics.proxy,
                                 f"{scheme}://127.0.0.1:{server.port}")

    def test_a_dead_endpoint_is_retried_then_the_walk_moves_on_and_resolves(self) -> None:
        """One site being down must not cost the launch its localization.

        The lookup used to be one request to one HTTPS site, so anything that
        refused it -- an outage, a rate limit, a proxy that would not carry
        :443 -- ended with no locale and no timezone, which means the browser
        serves the host's own from a foreign exit. That is the leak the lookup
        exists to close, so a failure now costs an attempt rather than the
        answer. npm/test/index.test.mjs pins the same behaviour against the
        same fixture shape.
        """
        geoip_module = importlib.import_module("apostate._prelaunch_geoip")
        with _FakeGeoIPEndpoint(status=503) as down, \
                _FakeGeoIPEndpoint({"country_code": "MY", "timezone": "Asia/Kuala_Lumpur",
                                    "ip": "203.0.113.7"}) as up:
            result = geoip_module.resolve_prelaunch_geoip(
                endpoints=(down.url, up.url), timeout=10.0)
        self.assertEqual(result.country_code, "MY")
        self.assertEqual(result.timezone, "Asia/Kuala_Lumpur")
        self.assertEqual(result.locale, "ms")
        self.assertEqual(result.timezone_source, "geoip")
        # The failing site was tried twice before the walk gave up on it, and
        # the one that answered was asked once. Literal counts: the retry is
        # the behaviour under test, so reading it back off the constant would
        # make this pass whatever the constant said.
        self.assertEqual((down.requests, up.requests), (2, 1))
        # Diagnostics name the site that actually answered, not the first tried.
        self.assertEqual(result.diagnostics.request_url, up.url)

    def test_an_answer_missing_the_timezone_yields_to_a_later_site(self) -> None:
        """A country with no timezone is half an answer, and half is not enough.

        There is no country-to-timezone table in this repository, so deriving
        the missing half would mean inventing a zone -- which is exactly the
        contradiction with the exit IP that detectors score. The walk carries
        on instead, and only falls back to the half it has once nothing better
        arrives.
        """
        geoip_module = importlib.import_module("apostate._prelaunch_geoip")
        with _FakeGeoIPEndpoint({"country_code": "MY", "ip": "203.0.113.7"}) as partial, \
                _FakeGeoIPEndpoint({"country_code": "DE", "timezone": "Europe/Berlin"}) as full:
            both = geoip_module.resolve_prelaunch_geoip(
                endpoints=(partial.url, full.url), timeout=10.0)
            # A site that answered is not retried: it would answer the same.
            self.assertEqual(partial.requests, 1)
            alone = geoip_module.resolve_prelaunch_geoip(
                endpoints=(partial.url,), timeout=10.0)
        self.assertEqual((both.country_code, both.timezone), ("DE", "Europe/Berlin"))
        # Nothing invented for the half that never arrived.
        self.assertEqual((alone.country_code, alone.locale), ("MY", "ms"))
        self.assertIsNone(alone.timezone)

    def test_no_working_endpoint_sends_no_override_instead_of_a_fabricated_one(self) -> None:
        """The honest failure survives, and now says how hard it tried.

        A launch that cannot reach any site keeps sending no override at all:
        the browser's own precedence settles locale and timezone, and nothing
        is invented. What the message adds is the count, because "the lookup
        failed" used to be indistinguishable from "the lookup was tried once".
        """
        geoip_module = importlib.import_module("apostate._prelaunch_geoip")
        launch_module = importlib.import_module("apostate.launch")
        with _FakeGeoIPEndpoint(status=503) as first, _FakeGeoIPEndpoint(status=500) as second:
            urls = (first.url, second.url)

            def provider(proxy: Any = None, timeout: float = 10.0) -> Any:
                return geoip_module.resolve_prelaunch_geoip(
                    proxy=proxy, endpoints=urls, timeout=timeout)

            stream = io.StringIO()
            with contextlib.redirect_stderr(stream):
                plan = launch_module._resolve_plan(
                    translate_options(fingerprint=4242), geoip_provider=provider)
        args = launch_module._native_args(plan)
        self.assertEqual([item for item in args
                          if item.startswith("--fingerprint-locale")
                          or item.startswith("--fingerprint-timezone")], [])
        self.assertFalse(any("en-US" in item or "UTC" in item for item in args))
        self.assertIn("--fingerprint=4242", args)
        warning = plan.diagnostics["warnings"][0]
        self.assertIn("every GeoIP endpoint failed: 4 attempts across 2 endpoints", warning)
        self.assertIn("No locale or timezone override is sent and none is invented", warning)
        self.assertIn(warning, stream.getvalue())

    def test_a_socks_credential_travels_in_the_envelope_and_not_to_the_driver(self) -> None:
        """Playwright refuses a socks server that carries a username.

        "Browser does not support socks5 proxy authentication" is raised
        before the browser is started, because upstream Chromium has no way
        to supply one -- so every authenticated residential SOCKS5 proxy, the
        commonest thing this package is pointed at, failed at the driver.
        The browser this package ships does support it, through the
        ``--apostate-profile`` envelope, which is also where the credential
        stays out of NetLog and socket-pool keys.
        """
        launch_module = importlib.import_module("apostate.launch")
        proxy = "socks5://geo%20user:p%40ss@proxy.example.test:12000"
        plan = launch_module._resolve_plan(
            translate_options(fingerprint=4242, geoip=False, proxy=proxy))
        args = launch_module._native_args(plan)
        self.assertIn("--proxy-server=socks5://proxy.example.test:12000", args)
        envelope = next(item for item in args if item.startswith("--apostate-profile="))
        self.assertEqual(
            json.loads(base64.b64decode(envelope.split("=", 1)[1])),
            {"device_profile": {},
             "proxy_credentials": {"password": "p@ss", "username": "geo user"}})
        # Credentials are not a device claim, so the seed still composes.
        self.assertIn("--fingerprint=4242", args)
        # Nothing on the command line carries the credential.
        self.assertFalse(any("p@ss" in item or "geo user" in item
                             for item in args if not item.startswith("--apostate-profile=")))
        self.assertEqual(launch_module._playwright_proxy(proxy),
                         {"server": "socks5://proxy.example.test:12000"})
        self.assertEqual(
            launch_module._playwright_proxy({"server": "socks5://proxy.example.test:12000",
                                             "username": "geo user", "password": "p@ss"}),
            {"server": "socks5://proxy.example.test:12000"})
        # http keeps its credential: there the driver is what answers the 407.
        self.assertEqual(
            launch_module._playwright_proxy("http://user:secret@proxy.example.test:8080"),
            {"server": "http://proxy.example.test:8080",
             "username": "user", "password": "secret"})

    def test_every_territory_resolves_a_locale_and_a_countryless_lookup_says_so(self) -> None:
        """The inference is total, and when it cannot run it says why.

        A provider never returns a locale; the launcher derives one from the
        country. The hand table that used to do it named 45 countries, so a
        Malaysian exit was reported as "resolved no locale" -- the launcher
        announcing its own gap as the network's. config/country-locales.json
        names every ISO-3166 territory, so the only way to have no locale is
        to have no country.
        """
        geoip_module = importlib.import_module("apostate._prelaunch_geoip")
        launch_module = importlib.import_module("apostate.launch")
        packaged = json.loads(
            (Path(config_module.__file__).parent / "assets"
             / "country-locales.json").read_text(encoding="utf-8"))
        self.assertEqual(len(packaged["locales"]), 257)
        for country, locale, languages in (("MY", "ms", "ms"),
                                           ("GT", "es-419", "es-419,es"),
                                           ("IN", "en-IN", "en-IN,en"),
                                           ("DE", "de-DE", "de-DE,de")):
            with self.subTest(country=country):
                result = geoip_module.GeoIPResolver(
                    transport=lambda request, payload={"ip": "203.0.113.7",
                                                       "country_code": country}: (200, json.dumps(payload)),
                    endpoint="http://exit.example.test/json", timeout=0.25).resolve()
                self.assertEqual(result.country_code, country)
                self.assertEqual(result.locale, locale)
                self.assertEqual(result.accept_languages, languages)
                self.assertEqual(result.locale_source, "geoip")

        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            plan = launch_module._resolve_plan(
                translate_options(fingerprint=4242),
                geoip_provider=lambda proxy, timeout: {"ip": "203.0.113.7",
                                                       "timezone": "Europe/Berlin"})
        self.assertEqual(
            plan.diagnostics["warnings"],
            ["the GeoIP lookup returned no country, so no locale is derived; the "
             "host's own is served for that field. Pass locale explicitly to "
             "guarantee a match."])
        self.assertEqual(
            stream.getvalue(),
            "apostate: the GeoIP lookup returned no country, so no locale is derived; "
            "the host's own is served for that field. Pass locale explicitly to "
            "guarantee a match.\n")

    def test_provisioned_widevine_survives_a_forced_reinstall(self) -> None:
        # The CDM is stored outside the install tree precisely so that
        # `install --force` and a Chromium upgrade, which both replace that
        # tree, do not silently remove DRM and turn a working launch into a
        # NotSupportedError a site can read in one call.
        from apostate import widevine
        archive_bytes = self._zip_archive({
            "apostate-test/Chromium.app/Contents/MacOS/Chromium": b"native binary",
        })
        with tempfile.TemporaryDirectory() as temporary:
            manager = BinaryManager(cache_dir=temporary,
                                    manifest=self._zip_manifest(archive_bytes),
                                    downloader=lambda source: archive_bytes)
            manager.ensure(target="macos-arm64")
            store = widevine.store_path(temporary)
            platform_dir = store / "_platform_specific" / "mac_arm64"
            platform_dir.mkdir(parents=True)
            (platform_dir / "libwidevinecdm.dylib").write_bytes(b"cdm")
            (store / "manifest.json").write_text('{"version": "4.10.3050.0"}', encoding="utf-8")

            install = Path(temporary) / CHROMIUM_VERSION / "macos-arm64" / "install"
            installed = widevine.apply_to_install(
                install, "macos-arm64", cache_dir=temporary,
                chromium_version=CHROMIUM_VERSION,
            )
            self.assertIsNotNone(installed)
            library = installed / "_platform_specific" / "mac_arm64" / "libwidevinecdm.dylib"
            self.assertEqual(library.read_bytes(), b"cdm")
            # No version directory: the browser reads the version from
            # manifest.json, and Google Chrome's own bundled copy has none.
            self.assertEqual(sorted(p.name for p in installed.iterdir()),
                             ["_platform_specific", "manifest.json"])

            manager.ensure(target="macos-arm64", force=True)
            self.assertEqual(library.read_bytes(), b"cdm")

    def test_widevine_provisioning_refuses_a_directory_without_a_library(self) -> None:
        from apostate.widevine import WidevineError, provision
        with tempfile.TemporaryDirectory() as temporary:
            empty = Path(temporary) / "WidevineCdm"
            empty.mkdir()
            with self.assertRaises(WidevineError):
                provision(target="macos-arm64", source=empty, cache_dir=temporary,
                          chromium_version=CHROMIUM_VERSION, install=Path(temporary) / "install")

    def test_provisioning_without_an_install_path_targets_this_package_s_own(self) -> None:
        # `apostate provision-drm` with no --source and no install path is the
        # documented way to do this, and it is the one path that has to work
        # out what the install directory is rather than being handed it. That
        # calculation went stale once and nothing noticed: it is the cache
        # keyed by Chromium version, and it must not need a manifest, because
        # on a release that publishes nothing the install has already
        # succeeded by the time it is asked for.
        from apostate import widevine
        archive_bytes = self._zip_archive({
            "apostate-test/Chromium.app/Contents/MacOS/Chromium": b"native binary",
        })
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "WidevineCdm"
            platform_dir = source / "_platform_specific" / "mac_arm64"
            platform_dir.mkdir(parents=True)
            (platform_dir / "libwidevinecdm.dylib").write_bytes(b"cdm")
            (source / "manifest.json").write_text('{"version": "4.10.3050.0"}',
                                                  encoding="utf-8")
            manager = BinaryManager(cache_dir=temporary,
                                    manifest=self._zip_manifest(archive_bytes),
                                    downloader=lambda url: archive_bytes)
            binary_module = importlib.import_module("apostate.binary")
            with mock.patch.object(binary_module, "BinaryManager",
                                   lambda **kwargs: manager):
                with _release() as requested:
                    result = widevine.provision(target="macos-arm64", source=source,
                                                cache_dir=temporary)
            self.assertEqual(requested, [])
            self.assertEqual(
                result["installed"],
                str(Path(temporary) / CHROMIUM_VERSION / "macos-arm64" / "install"
                    / "Chromium.app" / "Contents" / "Frameworks"
                    / f"Chromium Framework.framework/Versions/{CHROMIUM_VERSION}"
                    / "Libraries" / "WidevineCdm"))

    def test_component_update_switch_is_dropped_from_driver_defaults(self) -> None:
        # Playwright passes --disable-component-update by default, and it blocks
        # ComponentInstaller::Register outright, so a provisioned Widevine CDM is
        # silently inert. Measured: same install and same code, Patchright
        # resolved and Playwright rejected NotSupportedError. Patchright does not
        # pass the switch, which is why the first version of this feature passed
        # its own test and would still have failed for a plain-Playwright user.
        launch_module = importlib.import_module("apostate.launch")
        fake = _FakeSyncPlaywright()
        with tempfile.NamedTemporaryFile() as executable:
            os.chmod(executable.name, 0o755)
            with mock.patch.object(launch_module, "_load_sync_backend",
                                      return_value=launch_module.DriverSelection("patchright", lambda: fake)):
                launch(geoip=False, binary_path=executable.name, fingerprint="host")
        assert fake.chromium.options is not None
        self.assertIn("--disable-component-update", fake.chromium.options["ignore_default_args"])

    def test_an_explicitly_requested_component_update_switch_is_honoured(self) -> None:
        # Suppressing a switch the caller asked for would be the package
        # overriding an explicit decision; only the driver's default is removed.
        launch_module = importlib.import_module("apostate.launch")
        fake = _FakeSyncPlaywright()
        with tempfile.NamedTemporaryFile() as executable:
            os.chmod(executable.name, 0o755)
            with mock.patch.object(launch_module, "_load_sync_backend",
                                      return_value=launch_module.DriverSelection("patchright", lambda: fake)):
                launch(geoip=False, binary_path=executable.name, fingerprint="host",
                       args=["--disable-component-update"])
        assert fake.chromium.options is not None
        self.assertIsNone(fake.chromium.options["ignore_default_args"])
        self.assertIn("--disable-component-update", fake.chromium.options["args"])

    def test_patchright_is_the_default_driver_and_the_choice_is_inspectable(self) -> None:
        # The driver default is the package's to choose and is most of the point
        # of shipping a package rather than a bare binary: the browser patches
        # close the protocol-side tells, but nothing in the browser can remove
        # what a driver CREATES -- main-world bindings, Runtime.addBinding,
        # evaluation-script names in stack traces, its automation argv.
        launch_module = importlib.import_module("apostate.launch")
        self.assertEqual(launch_module.DRIVERS[0], "patchright")
        info = launch_module.driver_info()
        self.assertEqual(info["recommended"], "patchright")
        self.assertEqual(info["preference_order"], list(launch_module.DRIVERS))
        # Whatever is installed, the selection must be the first preference
        # present, never an arbitrary one.
        if info["installed"]:
            self.assertEqual(info["selected"], info["installed"][0])
            self.assertEqual(
                info["installed"],
                [name for name in launch_module.DRIVERS if name in info["installed"]],
            )
        else:
            self.assertIsNone(info["selected"])

    def test_an_unknown_driver_is_refused_by_name(self) -> None:
        launch_module = importlib.import_module("apostate.launch")
        with self.assertRaisesRegex(ConfigurationError, "unknown driver"):
            launch_module._load_sync_backend("selenium")

    def test_driver_default_viewport_is_not_allowed_to_overwrite_the_geometry(self) -> None:
        # Playwright's default context reports screen == inner == avail with
        # devicePixelRatio flattened to 1, and Puppeteer's reports an inner
        # viewport LARGER than its own window. No real machine does either.
        # Measured on macos-arm64 with --fingerprint=42: letting the real window
        # size through restored avail 1710x1079 against screen 1710x1112 and a
        # dpr of 2, on both drivers.
        launch_module = importlib.import_module("apostate.launch")
        recorded: dict[str, Any] = {}

        class _Browser:
            def new_page(self, **kwargs: Any) -> dict[str, Any]:
                recorded.update(kwargs)
                return kwargs

            def close(self) -> None:
                return None

        wrapped = launch_module._coherent_viewport(_Browser())
        wrapped.new_page()
        self.assertTrue(recorded["no_viewport"])
        # An explicit request is the caller's decision and must survive.
        recorded.clear()
        wrapped.new_page(viewport={"width": 1024, "height": 768})
        self.assertNotIn("no_viewport", recorded)
        self.assertEqual(recorded["viewport"], {"width": 1024, "height": 768})

    def test_a_context_closes_the_browser_it_was_created_from(self) -> None:
        # launch_context hands back a context, and closing a non-persistent
        # context does not close its browser. Without this the browser and its
        # driver outlive the context, and the driver's installed event loop
        # makes the next sync launch fail with "Sync API inside the asyncio
        # loop" -- found by exercising this path for the first time.
        launch_module = importlib.import_module("apostate.launch")
        closed = []

        class _Browser:
            def close(self) -> None:
                closed.append("browser")

        class _Context:
            def close(self) -> None:
                closed.append("context")

        context = launch_module._context_owns_browser(_Context(), _Browser())
        context.close()
        self.assertEqual(closed, ["context", "browser"])

    def test_async_launch_delegates_to_async_backend(self) -> None:
        launch_module = importlib.import_module("apostate.launch")
        fake = _FakeAsyncPlaywright()
        with tempfile.NamedTemporaryFile() as executable:
            os.chmod(executable.name, 0o755)
            with mock.patch.object(launch_module, "_load_async_backend",
                                      return_value=launch_module.DriverSelection("patchright", lambda: fake)):
                result = asyncio.run(
                    launch_async(
                        profile={"id": "async-explicit", "platform": {"name": "macOS"}},
                        fingerprint_platform="macos",
                        geoip=False,
                        binary_path=executable.name,
                    )
                )
        self.assertIsNotNone(result)
        assert fake.chromium.options is not None
        self.assertEqual(fake.chromium.options["executable_path"], executable.name)
        self.assertTrue(any(argument.startswith("--apostate-profile=") for argument in fake.chromium.options["args"]))

    def test_missing_binary_fails_honestly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "does-not-exist"
            with self.assertRaises(LaunchError):
                asyncio.run(
                    launch_async(
                        geoip=False,
                        binary_path=missing,
                        resolver=lambda config: {},
                    )
                )

    def test_persistent_context_creates_user_data_dir(self) -> None:
        launch_module = importlib.import_module("apostate.launch")
        fake = _FakeSyncPlaywright()
        with tempfile.TemporaryDirectory() as temporary:
            profile_path = Path(temporary) / "profile"
            with tempfile.NamedTemporaryFile() as executable:
                os.chmod(executable.name, 0o755)
                with mock.patch.object(launch_module, "_load_sync_backend",
                                      return_value=launch_module.DriverSelection("patchright", lambda: fake)):
                    context = launch_persistent_context(
                        profile_path,
                        geoip=False,
                        binary_path=executable.name,
                        resolver=lambda config: {},
                    )
            self.assertTrue(profile_path.is_dir())
            self.assertEqual(context["user_data_dir"], str(profile_path))
            assert fake.chromium.options is not None
            self.assertEqual(fake.chromium.options["user_data_dir"], str(profile_path))

    def test_a_composed_launch_is_given_a_locale_environment_and_never_inherits_one(self) -> None:
        """Only host mode inherits the host's locale environment.

        Chromium resolves its application locale from LANGUAGE, LC_ALL,
        LC_MESSAGES and LANG, sets ICU's default locale from that, and every
        `Intl` constructor resolves its own default against ICU's. So those
        four variables decide `Intl.DateTimeFormat`, `Intl.NumberFormat`,
        `Intl.Collator` and every `toLocaleString`, and a composed persona that
        inherited them would serve the operator's real locale: a Bangkok host
        with `LANG=th_TH.UTF-8` behind a Mexican exit would format dates and
        numbers in Thai under a synthetic Windows identity.

        All four are asserted because each moves the application locale on its
        own, so one left at the host's value is enough to lose the surface.
        """
        launch_module = importlib.import_module("apostate.launch")
        host_shell = {"LANGUAGE": "th", "LC_ALL": "th_TH.UTF-8",
                      "LC_MESSAGES": "th_TH.UTF-8", "LANG": "th_TH.UTF-8"}

        def plan_for(**options: Any) -> Any:
            return launch_module._resolve_plan(translate_options(geoip=False, **options))

        with mock.patch.dict(os.environ, host_shell, clear=False):
            # A composed launch that named no locale gets the composed default,
            # not the shell's. "Nothing resolved" has to mean a defined value
            # or the served locale becomes a property of the operator's shell.
            composed = launch_module._locale_environment(plan_for(fingerprint=12345))
            self.assertEqual(
                {"LANGUAGE": "en-US", "LC_ALL": "en_US.UTF-8",
                 "LC_MESSAGES": "en_US.UTF-8", "LANG": "en_US.UTF-8"}, composed)

            # A named locale reaches all four, in the form each expects.
            named = launch_module._locale_environment(plan_for(fingerprint=1, locale="de-DE,de"))
            self.assertEqual(
                {"LANGUAGE": "de-DE", "LC_ALL": "de_DE.UTF-8",
                 "LC_MESSAGES": "de_DE.UTF-8", "LANG": "de_DE.UTF-8"}, named)

            # Host mode is the one launch that inherits, because there the host
            # is what is being presented rather than what must not show.
            self.assertEqual({}, launch_module._locale_environment(plan_for(fingerprint="host")))

            # And the environment actually handed to the browser carries it,
            # with the caller's own env still winning last.
            environment = launch_module._launch_environment(
                plan_for(fingerprint=12345, timezone="America/Mexico_City"), {"LANG": "caller"})
            self.assertEqual("en-US", environment["LANGUAGE"])
            self.assertEqual("en_US.UTF-8", environment["LC_ALL"])
            self.assertEqual("America/Mexico_City", environment["TZ"])
            self.assertEqual("caller", environment["LANG"])


if __name__ == "__main__":
    unittest.main()
