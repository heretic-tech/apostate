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
import subprocess
import sys
import tempfile
import unittest
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
    ConfigurationError,
    GeoIPError,
    LaunchError,
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

    def test_unknown_fingerprint_switch_is_refused_before_launch(self) -> None:
        # Chromium ignores an unknown switch silently, which would leave the
        # surface host-inherited while the caller believed it was set.
        launch_module = importlib.import_module("apostate.launch")
        plan = launch_module._resolve_plan(
            translate_options(fingerprint=7, args=["--fingerprint-gpu-vendr=Apple"], geoip=False)
        )
        with self.assertRaisesRegex(ConfigurationError, "not a switch this browser reads"):
            launch_module._native_args(plan)

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
            ("a result with no locale", lambda proxy, timeout: {"timezone": "Europe/Berlin"},
             ["--fingerprint-timezone=Europe/Berlin"], "resolved no locale"),
            # freeipapi answers with a UTC offset, which cannot drive the
            # switch: unresolved, not adjusted into something that looks like an
            # identifier.
            ("an offset instead of an identifier",
             lambda proxy, timeout: {"locale": "de-DE", "timezone": "+02:00"},
             ["--fingerprint-locale=de-DE"], "resolved no timezone"),
            # A country code derives its locale from the Apostate-owned table
            # that scripts/geoip.py and the npm package share, so a German exit
            # is de-DE rather than an invented en-DE or en-US.
            ("a country code and a timezone",
             lambda proxy, timeout: {"country_code": "DE", "timezone": "Europe/Berlin"},
             ["--fingerprint-locale=de-DE", "--fingerprint-timezone=Europe/Berlin"], None),
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

    def test_unpublished_package_manifest_fails_before_download(self) -> None:
        calls: list[str] = []
        manifest = {
            "package_version": "0.1.0",
            "chromium_version": CHROMIUM_VERSION,
            "catalogue_version": CATALOGUE_VERSION,
            "artifacts": {},
            "status": "unpublished",
        }
        with tempfile.TemporaryDirectory() as temporary:
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

    def test_launch_reports_the_unpublished_package_before_the_missing_driver(self) -> None:
        """The true blocker first, not whichever check happened to run first.

        Nothing is published yet, so every launch hits this. Loading the
        driver first sent a new user to install Patchright when the real
        answer was that there is no binary to drive, and no developer machine
        could reproduce it because a driver is always already importable.
        This is asserted through launch(), not through BinaryManager: the
        ordering is what broke, and only a caller can see it.
        """
        launch_module = importlib.import_module("apostate.launch")
        binary_module = importlib.import_module("apostate.binary")
        with tempfile.TemporaryDirectory() as temporary:
            # launch() has no search_roots parameter, so the documented
            # locations are emptied for the duration: see the note above.
            with mock.patch.object(binary_module, "_well_known_roots", lambda target: ()):
                with self.assertRaises(UnpublishedArtifactError):
                    launch_module.launch(cache_dir=temporary, geoip=False, fingerprint="host",
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

    def test_packaged_release_manifest_agrees_with_the_package_catalogue_version(self) -> None:
        # A stale catalogue_version here turns "no artifact is published yet"
        # into "this manifest is for another package", which is a lie.
        with tempfile.TemporaryDirectory() as temporary:
            manager = BinaryManager(cache_dir=temporary, search_roots=())
            with self.assertRaisesRegex(UnpublishedArtifactError, "unpublished"):
                manager.ensure(target="macos-arm64")

    def test_cache_paths_and_clear_cache_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            manager = BinaryManager(cache_dir=cache)
            root, install, marker = manager._paths(
                "linux-x64",
                {"chromium_version": CHROMIUM_VERSION},
                {"artifact": "apostate-linux-x64.tar.zst"},
            )
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
            # nothing is downloaded to find that out.
            def refuse(source: str) -> bytes:
                raise AssertionError(f"downloaded {source} despite an install on disk")

            manager = BinaryManager(cache_dir=cache, search_roots=[roots], downloader=refuse)
            self.assertEqual(manager.ensure(target="linux-x64"), ours)
            manager.assert_published(target="linux-x64")

            # `info` still answers the question a user is asking -- where is
            # the browser -- on a release whose manifest publishes nothing.
            info = manager.info(target="linux-x64")
            self.assertFalse(info["available"])
            self.assertEqual(info["executable"], str(ours))
            self.assertEqual(info["executable_source"], "well-known")

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
