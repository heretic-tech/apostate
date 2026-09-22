"""Sync and async Patchright-compatible launch APIs for Apostate."""

from __future__ import annotations

import asyncio
import base64
import copy
import importlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from .binary import BinaryManager, ensure_binary, resolve_named_binary, target_platform
from .config import (LaunchConfig, check_fingerprint_switches, is_host_seed,
                     translate_options)
from .errors import ConfigurationError, GeoIPError, LaunchError, ProfileError
from .geoip import GeoIPResult, resolve_geoip
from .profile_validation import validate_profile
from .resolver import DeterministicResolver, ProfileResolution


def _proxy_url(value: str | Mapping[str, Any] | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        if not value.strip():
            raise ConfigurationError("proxy must be a non-empty URL")
        return value.strip()
    if not isinstance(value, Mapping):
        raise ConfigurationError("proxy must be a URL or mapping")
    server = value.get("server") or value.get("url")
    if not isinstance(server, str) or not server.strip():
        raise ConfigurationError("proxy mapping requires a non-empty server or url")
    parsed = urlsplit(server.strip())
    if not parsed.scheme or not parsed.hostname:
        raise ConfigurationError("proxy server must include a scheme and host")
    username, password = value.get("username"), value.get("password")
    if username is not None and not isinstance(username, str):
        raise ConfigurationError("proxy username must be a string")
    if password is not None and not isinstance(password, str):
        raise ConfigurationError("proxy password must be a string")
    userinfo = ""
    if username is not None:
        userinfo = quote(username, safe="")
        if password is not None:
            userinfo += ":" + quote(password, safe="")
        userinfo += "@"
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    return urlunsplit((parsed.scheme, f"{userinfo}{host}{port}", "", "", ""))


def _proxy_server_arg(value: str | Mapping[str, Any] | None) -> str | None:
    raw = _proxy_url(value)
    if raw is None:
        return None
    parsed = urlsplit(raw)
    if not parsed.hostname:
        raise ConfigurationError("proxy server host is required")
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    return urlunsplit((parsed.scheme, f"{host}{port}", "", "", ""))


def _playwright_proxy(value: str | Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The driver's proxy option, which is not where a SOCKS credential goes.

    The browser already has it: ``_native_args`` puts it in the
    ``--apostate-profile`` envelope, which is the route that keeps a
    credential out of NetLog, socket-pool group keys and error strings
    (docs/FLAGS.md, "The proxy"). Playwright, meanwhile, refuses to start at
    all when a socks server carries a username -- "Browser does not support
    socks5 proxy authentication" -- because upstream Chromium has no way to
    supply one. Handing the driver a credential it will not use, and failing
    a launch the browser can serve, is the worst of both. http and https keep
    theirs, because there the driver is what answers the 407.
    """
    if value is None:
        return None
    if isinstance(value, Mapping):
        result = dict(value)
        server = result.get("server") or result.get("url")
        if not isinstance(server, str) or not server.strip():
            raise ConfigurationError("proxy mapping requires a non-empty server or url")
        result["server"] = server.strip()
        result.pop("url", None)
        for key in ("username", "password", "bypass"):
            if key in result and result[key] is not None and not isinstance(result[key], str):
                raise ConfigurationError(f"proxy {key} must be a string")
        if urlsplit(result["server"]).scheme.startswith("socks"):
            result.pop("username", None)
            result.pop("password", None)
        return result
    raw = _proxy_url(value)
    assert raw is not None
    parsed = urlsplit(raw)
    server = _proxy_server_arg(raw)
    assert server is not None
    result: dict[str, Any] = {"server": server}
    if parsed.scheme.startswith("socks"):
        return result
    if parsed.username is not None:
        result["username"] = unquote(parsed.username)
    if parsed.password is not None:
        result["password"] = unquote(parsed.password)
    return result


def _profile_dict(profile: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The validated device payload, or ``None`` when it describes no device."""
    if profile is None:
        return None
    validated = validate_profile(profile)
    if not validated:
        # An empty envelope is not "no profile". It describes no device, so it
        # can only either suppress composition -- which the shipped loader does
        # for any envelope at all -- or be a no-op once a no-device payload
        # composes normally. Neither is worth a switch, and the first would make
        # this package carry a second, hidden host-inheritance default, so send
        # nothing and let --fingerprint stand on its own.
        return None

    def native_value(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: native_value(item) for key, item in value.items() if key != "source_capture"}
        if isinstance(value, list):
            return [native_value(item) for item in value]
        return copy.deepcopy(value)

    return native_value(validated)


def _encode_envelope(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True).encode("utf-8")
    return base64.b64encode(encoded).decode("ascii")


def _proxy_credentials(value: str | Mapping[str, Any] | None) -> dict[str, str] | None:
    """The proxy's credential, for the envelope rather than the command line.

    The endpoint goes on the command line credential-free and the credential
    travels inside ``--apostate-profile``; docs/FLAGS.md "The proxy" is why.
    This package used to have no such channel and handed the credential to the
    driver instead, which works for http and cannot work for SOCKS: Playwright
    refuses to start at all when a socks server carries a username, because
    upstream Chromium has no way to supply one. Every authenticated
    residential SOCKS5 proxy -- the commonest thing this package is pointed at
    -- failed before the browser existed.
    """
    raw = _proxy_url(value)
    if raw is None:
        return None
    parsed = urlsplit(raw)
    if not parsed.username and not parsed.password:
        return None
    try:
        username = unquote(parsed.username or "", errors="strict")
        password = unquote(parsed.password or "", errors="strict")
    except (UnicodeDecodeError, ValueError) as exc:
        raise ConfigurationError("proxy credentials must be valid URL-encoded text") from exc
    if len(username) > 4096 or len(password) > 4096:
        raise ConfigurationError("proxy credentials must be at most 4096 characters")
    return {"password": password, "username": username}


def _geoip_dict(value: GeoIPResult | Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, GeoIPResult):
        return value.to_dict()
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
    if not isinstance(value, Mapping):
        raise ProfileError("geoip provider must return a mapping or GeoIPResult")
    return value


@dataclass(frozen=True)
class LaunchPlan:
    config: LaunchConfig
    profile: dict[str, Any]
    resolution: ProfileResolution | None
    geoip: GeoIPResult | None
    diagnostics: dict[str, Any]


# The environment variables that decide Chromium's application locale, and
# through it ICU's default locale and every `Intl` constructor's. All four,
# because each moves it on its own: leaving one at the operator's value lets
# the host decide through a variable nobody wrote.
_LOCALE_ENV_VARS = ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")

# What a composed launch that resolved no locale gets. Not the host's, and not
# nothing: absence of a resolved locale has to mean a defined default, or the
# served locale becomes a property of the operator's shell.
COMPOSED_DEFAULT_LOCALE = "en-US"


def _posix_locale(tag: str) -> str:
    """`de-DE` -> `de_DE.UTF-8`, for the three LC_* variables that expect it.

    A tag with no region stays region-free rather than acquiring an invented
    one. If the host has not generated the named locale, setlocale falls back
    to C and `LANGUAGE` -- which takes the tag as written and needs nothing
    generated -- still decides the application locale. Either way the host's
    own value is gone, which is the point of writing these at all.
    """
    language, _, region = tag.partition("-")
    base = f"{language}_{region}" if region else language
    return f"{base}.UTF-8"


def _locale_environment(plan: LaunchPlan) -> dict[str, str]:
    """The locale environment a launch is given, or nothing for host mode.

    Only host inheritance inherits the host's locale environment, because only
    there is the host the thing being presented. A composed persona gets these
    written explicitly, and the reason is a leak rather than tidiness: an
    operator in Bangkok with LANG=th_TH.UTF-8, composing a Windows persona
    through a Mexican exit, would otherwise serve Thai language preferences and
    Thai date and number formatting from a Mexican IP under a synthetic Windows
    identity. That is the host showing through a composed profile, which is the
    one thing this product may not do.

    Chromium resolves its application locale from these, sets ICU's default
    locale from that, and every `Intl` constructor resolves its own default
    against ICU's (v8/src/execution/isolate.cc:8123). So this is the only lever
    that moves `Intl.DateTimeFormat`, `Intl.NumberFormat`, `Intl.Collator` and
    `toLocaleString` together with `navigator.languages`; `--lang` moves none
    of them, measured on stock Chrome as well as on ours. It has no effect on
    an artifact that ships one locale pak -- see docs/FINGERPRINTS.md section 8
    and scripts/package-artifact.sh, which now ships the full set.
    """
    if is_host_seed(plan.config.fingerprint):
        return {}
    resolved = plan.resolution.locale if plan.resolution is not None else None
    tag = str(resolved or plan.config.locale or COMPOSED_DEFAULT_LOCALE).split(",", 1)[0].strip()
    if not tag:
        tag = COMPOSED_DEFAULT_LOCALE
    posix = _posix_locale(tag)
    return {name: (tag if name == "LANGUAGE" else posix) for name in _LOCALE_ENV_VARS}


def _launch_environment(plan: LaunchPlan, supplied: Mapping[str, Any] | None) -> dict[str, str]:
    """The browser process environment: this one's, then the caller's word."""
    env = dict(os.environ)
    env.update(_locale_environment(plan))
    if plan.config.timezone:
        env["TZ"] = plan.config.timezone
    for key, value in dict(supplied or {}).items():
        env[str(key)] = str(value)
    return env


def _resolve_plan(config: LaunchConfig, *, resolver: Any = None, catalogue: Any = None,
                  geoip_provider: Any = None, geoip_timeout: float = 10.0) -> LaunchPlan:
    network_result: GeoIPResult | None = None
    geoip_warnings: list[str] = []
    proxy_value = _proxy_url(config.proxy)
    if config.geoip and (config.locale is None or config.timezone is None):
        if geoip_timeout <= 0:
            # A caller bug rather than a network failure, so this still raises.
            # Nothing below makes an impossible timeout a state to proceed from.
            raise GeoIPError("GeoIP timeout must be greater than zero")
        try:
            # ``require_*=False`` keeps a partial answer usable: a result that
            # carries a timezone and no locale contributes the timezone instead
            # of discarding both. ``resolve_geoip`` called directly still
            # enforces both fields by default.
            network_result = resolve_geoip(geoip_provider, proxy=proxy_value,
                                           timeout=geoip_timeout,
                                           require_locale=False, require_timezone=False)
        except GeoIPError as exc:
            # A lookup failure, a timeout, an unavailable provider and a
            # malformed result are all reported, and none of them invents a
            # locale or a timezone. This package used to raise here, which the
            # spec permits -- the prohibition is on inventing, not on
            # continuing -- but it is the less usable of the two conforming
            # answers, and the Node package's answer, substituting en-US/UTC,
            # was the non-conforming one. Both now proceed with no override at
            # all. That state only became distinguishable when GeoIP started
            # driving --fingerprint-locale/--fingerprint-timezone instead of an
            # --apostate-profile envelope: an envelope suppressed composition
            # whether or not it carried a locale, so "send nothing" and "send
            # en-US/UTC" had the same effect.
            #
            # Sending neither switch leaves the browser's own precedence to
            # settle the surface, and since patch 0111 that means the host's
            # own zone and language list rather than a pair drawn from a
            # four-entry catalogue pool. That is the whole reason this is a
            # warning and not a refusal: the host's zone matches a direct
            # egress and is at worst wrong the way a traveller's is, where a
            # drawn one could not match any egress by construction. The cost is
            # still reported rather than hidden, because behind a proxy the
            # host's zone is the host's and not the exit's.
            geoip_warnings.append(
                f"{exc}. No locale or timezone override is sent and none is invented, so the "
                "launch keeps the host's own locale and timezone. Behind a proxy that is the "
                "host's and not the exit's. Pass locale and timezone explicitly to guarantee "
                "a match."
            )
        else:
            # Two different facts, so two different sentences. A timezone is
            # something the provider either returned or did not. A locale is
            # never returned by anyone: it is inferred from the country, and
            # since that inference covers every ISO-3166 territory the only
            # way to have no locale is to have no country. Saying "resolved no
            # locale" over a lookup that answered MY was reporting the
            # launcher's own gap as the network's.
            if config.timezone is None and not network_result.timezone:
                geoip_warnings.append(
                    "the GeoIP lookup resolved no timezone. None is invented, so the "
                    "host's own is served for that field; pass it explicitly to "
                    "guarantee a match."
                )
            if (config.locale is None and not network_result.locale
                    and not network_result.languages):
                geoip_warnings.append(
                    "the GeoIP lookup returned no country, so no locale is derived; "
                    "the host's own is served for that field. Pass locale explicitly "
                    "to guarantee a match."
                )
        for warning in geoip_warnings:
            print(f"apostate: {warning}", file=sys.stderr)
    network_mapping = _geoip_dict(network_result)

    resolution: ProfileResolution | None = None
    if resolver is None:
        resolution = DeterministicResolver(catalogue).resolve(config, geoip=network_mapping)
        profile: dict[str, Any] = resolution.profile
        diagnostics = resolution.to_dict()
    elif isinstance(resolver, DeterministicResolver):
        resolution = resolver.resolve(config, geoip=network_mapping)
        profile = resolution.profile
        diagnostics = resolution.to_dict()
    elif callable(resolver):
        value = resolver(config)
        if isinstance(value, ProfileResolution):
            resolution = value
            profile = value.profile
            diagnostics = value.to_dict()
        else:
            if not isinstance(value, Mapping):
                raise ProfileError("profile resolver must return a mapping")
            profile = dict(value)
            diagnostics = {"profile_id": str(profile.get("id") or "custom"), "warnings": []}
    else:
        raise ConfigurationError("resolver must be callable or DeterministicResolver")
    # The lookup runs before a ProfileResolution exists, so a GeoIP warning
    # cannot travel in ``ProfileResolution.warnings``; it is merged into the
    # plan's diagnostics here instead. A caller inspecting the return value
    # reads this list, which is why a stderr line alone is not enough. Every
    # branch above provides one, including the custom-resolver dict.
    if geoip_warnings:
        existing = diagnostics.get("warnings")
        diagnostics = {
            **diagnostics,
            "warnings": (list(existing) if isinstance(existing, list) else []) + geoip_warnings,
        }
    # This second validation is intentional: custom resolvers are untrusted
    # integration points and no process may start before this check succeeds.
    profile = validate_profile(profile)
    return LaunchPlan(config=config, profile=profile, resolution=resolution,
                      geoip=network_result, diagnostics=diagnostics)


#: Profile ids the resolver uses for the two native-composition paths. Both
#: deliver their selection through ``--fingerprint``; everything else is an
#: explicit profile delivered through ``--apostate-profile``.
_NATIVE_SELECTION = ("host-inherited", "native-composed")


def _native_args(plan: LaunchPlan, *, persistent: bool = False) -> list[str]:
    config = plan.config
    check_fingerprint_switches(config.args)
    args: list[str] = []
    if config.headless:
        args.append("--headless=new")
    args.extend(("--no-first-run", "--no-default-browser-check"))

    native_selection = (plan.resolution is None
                        or plan.resolution.profile_id in _NATIVE_SELECTION)
    if native_selection and not _has_switch(config.args, "--fingerprint"):
        # The compositor is the browser process's. The package hands it the
        # selectors; it draws a fresh seed itself when none is given.
        if config.fingerprint is not None:
            args.append(f"--fingerprint={config.fingerprint}")
        if config.fingerprint_platform is not None:
            args.append(f"--fingerprint-platform={config.fingerprint_platform}")

    if plan.resolution is not None and plan.resolution.profile_id == "native-composed":
        # Locale and timezone ride 0085's per-field override switches, never a
        # profile envelope. An envelope describing a device -- even one
        # describing only a locale -- makes the browser's
        # InstallComposedProfile() return early, so nothing
        # is composed, the seed above is silently ignored, and every axis the
        # envelope omits falls back to the host. An override instead narrows the
        # draw inside the composed profile, which is what this path wants.
        #
        # Host mode does not outrank a per-field override, it REFUSES it: 0085
        # treats a persona, a pinned anchor and a per-field override alike, and
        # combining any of them with a host spelling writes to stderr and exits
        # non-zero rather than half-applying. So these are deliberately not
        # emitted for ``host-inherited`` -- doing so would kill the launch, not
        # merely be ignored.
        for switch, value in (("--fingerprint-locale", plan.resolution.locale),
                              ("--fingerprint-timezone", plan.resolution.timezone)):
            if value and not _has_switch(config.args, switch):
                args.append(f"{switch}={value}")

    device = _profile_dict(plan.profile)
    credentials = _proxy_credentials(config.proxy)
    if device is not None or credentials is not None:
        # A device envelope and a seed are alternatives, not layers. The browser
        # cannot report the conflict -- marking an envelope partial would be a
        # new page-visible surface, and the absent-means-absent rule is what
        # makes a single-surface envelope useful for testing -- so refuse here
        # rather than let the seed be dropped without a word.
        #
        # Credentials are not a device claim, so they do not trigger that
        # refusal: InstallComposedProfile() composes normally for a payload
        # that claims no device and attaches the credentials to what it
        # composed, which makes an authenticated proxy plus a pinned seed a
        # legal combination -- and the commonest one this package serves. The
        # empty ``device_profile`` key below is deliberate and must stay:
        # base/apostate/profile.cc's ParseOrNull reads ``proxy_credentials``
        # only inside ``if (FindDict("device_profile"))``, so a wrapper
        # without the key loses the credentials silently. npm/src/index.ts
        # builds the identical shape.
        if device is not None and _has_switch(config.args, "--fingerprint"):
            raise ProfileError(
                "an authored profile and a --fingerprint seed cannot be combined: "
                "an --apostate-profile payload describing a device suppresses the "
                "browser's composition entirely, so the seed would be silently "
                "ignored and every surface the profile does not describe would "
                "stay host-inherited"
            )
        payload: dict[str, Any] = ({"device_profile": device or {},
                                    "proxy_credentials": credentials}
                                   if credentials is not None else dict(device or {}))
        args.append("--apostate-profile=" + _encode_envelope(payload))
    if config.user_data_dir and not persistent:
        args.append("--user-data-dir=" + config.user_data_dir)
    proxy = _proxy_server_arg(config.proxy)
    if proxy:
        args.append("--proxy-server=" + proxy)
    args.extend(config.args)
    return args


def _has_switch(args: Any, name: str) -> bool:
    return any(item == name or item.startswith(name + "=") for item in args)


def _refuse_user_data_dir(config: Any, *, entry: str) -> None:
    """A persistent profile is a context, so ``launch()`` cannot carry one.

    Playwright's ``BrowserType.launch`` rejects ``--user-data-dir`` and points
    at ``launch_persistent_context``, but only after the browser has been
    resolved and the driver started. Refusing here costs nothing and names the
    entry point in this package's vocabulary.
    """
    named = bool(config.user_data_dir)
    in_args = _has_switch(config.args, "--user-data-dir")
    if not (named or in_args):
        return
    where = "user_data_dir" if named else "--user-data-dir in args"
    raise ConfigurationError(
        f"{entry}() returns a Browser and cannot take a persistent profile ({where}); "
        "use launch_persistent_context(user_data_dir, ...), which returns the "
        "context bound to that directory. The identity is stable there with no flag."
    )

#: Driver preference order, Patchright first. The reasoning, stated at the
#: strength it has been measured to: the browser owns what a page can observe
#: about the browser, and a driver's remaining job is to avoid CREATING
#: artifacts -- main-world ``addInitScript``/``exposeFunction`` bindings,
#: ``Runtime.addBinding``, evaluation-script names in stack traces, its
#: automation argv. Patchright is the hardened fork of that family.
#:
#: What is NOT claimed: that Patchright beats Playwright here. Nobody has
#: measured it on this project, and plain Playwright measured clean on both
#: artifacts expected to separate them -- the classic sentinels were absent
#: under every driver tried, and ``window`` key sets were byte-identical
#: between a bare launch and a driven page. Nor is patch 0087's neutralisation
#: of ``Runtime.enable`` settled: it has compiled and never run, so that is
#: design intent until the throw-cost ratio is re-measured on a built binary.
#: Choosing the default is still the package's job; asserting an unmeasured
#: advantage is not.
DRIVERS = ("patchright", "playwright")

#: What to do when no driver is importable. Patchright is a required
#: dependency, so reaching this is a partial install rather than a missing
#: step -- which is worth saying, because the old message told a user to run
#: an install they had already done.
_DRIVER_HINT = (
    "no Playwright-compatible driver is installed. Patchright is a required\n"
    "dependency of this package, so this is a partial install. Repair it:\n"
    "    pip install --force-reinstall patchright\n"
    "Or use the alternative driver: pip install playwright\n"
    "Neither needs `playwright install`: Apostate supplies its own browser."
)


class DriverSelection(NamedTuple):
    """Which driver was chosen, and the entry point to start it."""

    name: str
    factory: Any


def _load_backend(kind: str, requested: str | None = None) -> DriverSelection:
    """Import the first available driver, or the one the caller named."""
    if requested is not None:
        if requested not in DRIVERS:
            raise ConfigurationError(
                f"unknown driver {requested!r}; supported: {', '.join(DRIVERS)}"
            )
        candidates: tuple[str, ...] = (requested,)
    else:
        candidates = DRIVERS
    for name in candidates:
        try:
            module = importlib.import_module(f"{name}.{kind}_api")
        except ModuleNotFoundError:
            continue
        except ImportError as exc:
            raise LaunchError(f"unable to import the {name} {kind} API") from exc
        factory = getattr(module, f"{kind}_playwright")
        return DriverSelection(name=name, factory=factory)
    if requested is not None:
        raise LaunchError(f"driver {requested!r} is not installed. {_DRIVER_HINT}")
    raise LaunchError(_DRIVER_HINT)


def _load_sync_backend(requested: str | None = None) -> DriverSelection:
    return _load_backend("sync", requested)


def _load_async_backend(requested: str | None = None) -> DriverSelection:
    return _load_backend("async", requested)


def driver_info() -> dict[str, Any]:
    """Report which driver a launch would use, and what is installed.

    A silent driver choice makes a support conversation impossible, so the
    selection is inspectable without starting a browser.
    """
    installed = []
    for name in DRIVERS:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError:
            continue
        except ImportError:
            continue
        installed.append(name)
    return {
        "preference_order": list(DRIVERS),
        "installed": installed,
        "selected": installed[0] if installed else None,
        "recommended": DRIVERS[0],
    }


def _backend_error(exc: Exception) -> LaunchError:
    text = str(exc)
    # Playwright errors can echo the complete command line. Never expose a
    # credential-bearing proxy URL in a package exception.
    for token in ("http://", "https://", "socks5://", "socks5h://"):
        if token in text:
            text = text.split(token, 1)[0].rstrip() + " [proxy details redacted]"
            break
    return LaunchError(f"native Apostate browser launch failed: {text or 'unknown error'}")


#: Playwright passes this by default; Patchright and Puppeteer do not.
#: ``chrome/browser/chrome_browser_main.cc`` wraps the whole
#: ``RegisterComponentsForUpdate()`` call in a check for it, and that function is
#: the only caller of ``ComponentInstaller::Register``, which is the only path to
#: ``FindPreinstallation``. So it does not merely stop downloads -- it stops an
#: already-present component from ever being REGISTERED.
#:
#: Two reasons to drop it. A provisioned Widevine CDM is silently dead with it
#: set, which is the exact "told you DRM works, it does not" failure provisioning
#: exists to remove. And the artifact ships preinstalled components in
#: ``Libraries/``: MEIPreload and PrivacySandboxAttestationsPreloaded register on
#: every launch, IwaKeyDistribution is feature-gated and does not. The switch
#: gates the entire ``RegisterComponentsForUpdate()`` call, so it suppresses all
#: of them at once, and a real Chrome has them registered. Suppressing
#: registration is therefore itself a divergence from the browser being imitated,
#: independent of DRM.
#:
#: Measured on the provisioned install, offline, counting the browser's own
#: "Component ready" lines: without the switch, MEIPreload 1.0.7.1652906823,
#: PrivacySandboxAttestationsPreloaded 2025.7.18.0 and WidevineCdm 4.10.3050.0
#: register; with it, zero components register at all.
#:
#: Measured on macos-arm64 152.0.7977.83 with a provisioned CDM and no network:
#: through Patchright the empty robustness level resolved; through Playwright,
#: same install and same code, it rejected NotSupportedError.
_DISABLE_COMPONENT_UPDATE = "--disable-component-update"


def _ignore_default_args(requested: Any, args: Any) -> Any:
    """Drop the driver's component-update switch unless the caller asked for it.

    A switch the caller put in ``args`` themselves is honoured; only the
    driver's injected default is removed.
    """
    if _has_switch(args, _DISABLE_COMPONENT_UPDATE):
        return requested
    if requested is True:
        # Every default is already suppressed.
        return requested
    if requested is None or requested is False:
        return [_DISABLE_COMPONENT_UPDATE]
    if isinstance(requested, str):
        requested = [requested]
    merged = list(requested)
    if _DISABLE_COMPONENT_UPDATE not in merged:
        merged.append(_DISABLE_COMPONENT_UPDATE)
    return merged


def _coherent_viewport(target: Any) -> Any:
    """Stop the driver's default viewport overwriting the composed geometry.

    Playwright's default context is 1280x720 and reports ``screen == inner ==
    avail`` with ``devicePixelRatio`` flattened to 1. No real desktop has
    ``avail == screen``: there is always a menu bar or a taskbar. Puppeteer's
    default is worse -- outer 756x556 against inner 800x600, an inner viewport
    larger than the window containing it, which no machine reports.

    Measured on the shipped macos-arm64 build with ``--fingerprint=42``:

    =========================  ============================================
    Playwright default         screen/inner/avail all 1280x720, dpr 1
    Playwright ``no_viewport`` screen 1710x1112, avail 1710x1079, dpr 2
    Puppeteer default          outer 756x556, inner 800x600, avail==screen, dpr 1
    Puppeteer ``null``         outer 756x556, inner 756x469, avail<screen, dpr 2
    =========================  ============================================

    So the driver default costs three observables and the profile's own
    geometry; letting the real window size through restores all of them. A
    caller who asks for a viewport still gets it.
    """
    for name in ("new_page", "new_context"):
        original = getattr(target, name, None)
        if not callable(original):
            continue

        def wrapper(*args: Any, _original: Any = original, **kwargs: Any) -> Any:
            if "viewport" not in kwargs and "no_viewport" not in kwargs:
                kwargs["no_viewport"] = True
            return _original(*args, **kwargs)

        try:
            setattr(target, name, wrapper)
        except (AttributeError, TypeError):
            pass
    return target


async def _coherent_viewport_async(target: Any) -> Any:
    for name in ("new_page", "new_context"):
        original = getattr(target, name, None)
        if not callable(original):
            continue

        async def wrapper(*args: Any, _original: Any = original, **kwargs: Any) -> Any:
            if "viewport" not in kwargs and "no_viewport" not in kwargs:
                kwargs["no_viewport"] = True
            return await _original(*args, **kwargs)

        try:
            setattr(target, name, wrapper)
        except (AttributeError, TypeError):
            pass
    return target


def _own_driver(target: Any, driver: Any, name: str = "") -> Any:
    """Make *target* stop the Playwright driver it was created from.

    ``sync_playwright().start()`` installs an event loop in this thread and
    spawns a driver subprocess. Handing back only the browser leaks both: the
    driver outlives ``browser.close()``, and the installed loop makes the next
    sync launch in the same process fail with "Sync API inside the asyncio
    loop". A script that launches in a loop -- the common shape for this
    product -- then breaks on its second iteration. So whatever the caller is
    given closes the driver that produced it.
    """
    # A custom or fake backend may return anything, including a plain mapping.
    # Only a driver-backed object has a close() to chain onto.
    original = getattr(target, "close", None)
    if not callable(original):
        return target

    def close(*args: Any, **kwargs: Any) -> Any:
        try:
            return original(*args, **kwargs)
        finally:
            try:
                driver.stop()
            except Exception:
                pass

    try:
        target.close = close
        # Hold a reference so the driver is not collected while the browser lives.
        target.apostate_driver = driver
        # Which driver started this, so a support conversation is possible.
        target.apostate_driver_name = name
    except (AttributeError, TypeError):
        return target
    return target


async def _own_driver_async(target: Any, driver: Any, name: str = "") -> Any:
    original = getattr(target, "close", None)
    if not callable(original):
        return target

    async def close(*args: Any, **kwargs: Any) -> Any:
        try:
            return await original(*args, **kwargs)
        finally:
            try:
                await driver.stop()
            except Exception:
                pass

    try:
        target.close = close
        target.apostate_driver = driver
        target.apostate_driver_name = name
    except (AttributeError, TypeError):
        return target
    return target


def _resolve_executable(binary_path: Any, *, cache_dir: Any = None, manifest: Any = None,
                        downloader: Any = None, target: str | None = None) -> Path:
    """Return a runnable executable, acquiring the release artifact if needed.

    ``binary_path`` is the first of the four sources
    ``apostate.binary.DISCOVERY_ORDER`` names and is taken at its word: a
    caller who names a browser has said which one to run. Three spellings of
    "this one" are understood -- the executable, a macOS ``.app`` bundle, and
    the directory the release archive unpacks to -- because all three are
    things a user has a path to, and only the first used to be accepted. The
    rest of the search -- ``APOSTATE_BINARY``, this package's own install,
    then the documented well-known locations -- is ``ensure_binary``'s, which
    downloads only when none of them answers.
    """
    if binary_path is None:
        return ensure_binary(cache_dir=cache_dir, manifest=manifest,
                             downloader=downloader, target=target)
    binary = Path(binary_path).expanduser()
    executable, reason = resolve_named_binary(binary, "binary_path", target_platform(target))
    if executable is None:
        raise LaunchError(
            f"{reason}: {binary}. Omit binary_path to let the package find an "
            "existing install, or download and verify the release artifact."
        )
    if not os.access(executable, os.X_OK):
        raise LaunchError(f"Apostate browser binary is not executable: {executable}")
    return executable


def _assert_published(binary_path: Any, *, cache_dir: Any = None, manifest: Any = None,
                      target: str | None = None) -> None:
    """Ask the publication question before anything expensive happens.

    Publication is a local manifest lookup, so it comes before the driver
    check: on a release that ships no binary for this target, loading the
    driver first told the user to install Patchright or Playwright when the
    real blocker was that there is nothing to download yet. That is a
    first-run experience no developer machine can reproduce, because a driver
    is always already importable by the time anyone looks. The driver check
    still precedes acquisition, which is the ~150 MB download.

    A browser already on disk short-circuits it: see
    ``BinaryManager.assert_published``.
    """
    if binary_path is not None or os.environ.get("APOSTATE_BINARY"):
        return
    BinaryManager(cache_dir=cache_dir, manifest=manifest).assert_published(target=target)


def launch(*, fingerprint: int | str | None = None, fingerprint_platform: str | None = None,
           profile: Any = None, locale: str | None = None, timezone: str | None = None,
           geoip: bool = True, proxy: str | Mapping[str, Any] | None = None,
           headless: bool = True, user_data_dir: str | Path | None = None,
           args: list[str] | tuple[str, ...] | None = None,
           binary_path: str | Path | None = None, cache_dir: str | Path | None = None,
           manifest: Mapping[str, Any] | str | Path | None = None,
           downloader: Callable[[str], Any] | None = None, resolver: Any = None,
           catalogue: Any = None, geoip_provider: Any = None, geoip_timeout: float = 10.0,
           driver: str | None = None,
           **playwright_options: Any) -> Any:
    """Launch the native browser through a Patchright-compatible sync API."""
    config = translate_options(fingerprint=fingerprint, fingerprint_platform=fingerprint_platform,
                               profile=profile, locale=locale, timezone=timezone, geoip=geoip,
                               proxy=proxy, headless=headless, user_data_dir=user_data_dir, args=args)
    _refuse_user_data_dir(config, entry="launch")
    plan = _resolve_plan(config, resolver=resolver, catalogue=catalogue,
                         geoip_provider=geoip_provider, geoip_timeout=geoip_timeout)
    # Publication before the driver, driver before acquisition. See
    # _assert_published for why the order is load-bearing in both places.
    _assert_published(binary_path, cache_dir=cache_dir, manifest=manifest)
    selection = _load_sync_backend(driver)
    binary = _resolve_executable(binary_path, cache_dir=cache_dir, manifest=manifest,
                                 downloader=downloader)
    playwright = selection.factory().start()
    launch_options = dict(playwright_options)
    launch_options.update(executable_path=str(binary), headless=config.headless, args=_native_args(plan))
    launch_options["ignore_default_args"] = _ignore_default_args(
        launch_options.get("ignore_default_args"), config.args)
    # Set, not inherited: only host mode inherits the host's locale
    # environment. See _locale_environment.
    launch_options["env"] = _launch_environment(plan, launch_options.get("env"))
    if config.proxy is not None and "proxy" not in launch_options:
        launch_options["proxy"] = _playwright_proxy(config.proxy)
    try:
        return _own_driver(_coherent_viewport(playwright.chromium.launch(**launch_options)),
                           playwright, selection.name)
    except Exception as exc:
        try:
            playwright.stop()
        except Exception:
            pass
        raise _backend_error(exc) from exc


def _context_owns_browser(context: Any, browser: Any) -> Any:
    """Close the browser when the context the caller was handed is closed.

    ``launch_context`` returns a context, not the browser it came from, and
    closing a non-persistent context does not close its browser. Without this
    the browser and its driver outlive the context, and the driver's installed
    event loop makes the next sync launch in the same process fail with "Sync
    API inside the asyncio loop" -- the same leak as an unowned driver, reached
    through the one entry point that hands back something other than a browser.
    """
    original = getattr(context, "close", None)
    if not callable(original):
        return context

    def close(*args: Any, **kwargs: Any) -> Any:
        try:
            return original(*args, **kwargs)
        finally:
            try:
                browser.close()
            except Exception:
                pass

    try:
        context.close = close
        context.apostate_browser = browser
    except (AttributeError, TypeError):
        return context
    return context


def launch_context(*, context_options: Mapping[str, Any] | None = None, **options: Any) -> Any:
    """Launch a browser and create a non-persistent Playwright context."""
    context_options = dict(context_options or {})
    # Explicit context options are kept separate so canonical launch options
    # cannot accidentally become page-visible context configuration.
    browser = launch(**options)
    try:
        return _context_owns_browser(browser.new_context(**context_options), browser)
    except Exception as exc:
        try:
            browser.close()
        except Exception:
            pass
        raise _backend_error(exc) from exc


def launch_persistent_context(user_data_dir: str | Path, *, context_options: Mapping[str, Any] | None = None,
                              **options: Any) -> Any:
    """Launch a native persistent context backed by ``user_data_dir``."""
    path = Path(user_data_dir).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    config = translate_options(
        fingerprint=options.pop("fingerprint", None),
        fingerprint_platform=options.pop("fingerprint_platform", None),
        profile=options.pop("profile", None), locale=options.pop("locale", None),
        timezone=options.pop("timezone", None), geoip=options.pop("geoip", True),
        proxy=options.pop("proxy", None), headless=options.pop("headless", True),
        user_data_dir=path, args=options.pop("args", None),
    )
    plan = _resolve_plan(config, resolver=options.pop("resolver", None),
                         catalogue=options.pop("catalogue", None),
                         geoip_provider=options.pop("geoip_provider", None),
                         geoip_timeout=options.pop("geoip_timeout", 10.0))
    binary_path = options.pop("binary_path", None)
    cache_dir = options.pop("cache_dir", None)
    manifest = options.pop("manifest", None)
    downloader = options.pop("downloader", None)
    # Publication before the driver, driver before acquisition: see launch().
    _assert_published(binary_path, cache_dir=cache_dir, manifest=manifest)
    selection = _load_sync_backend(options.pop("driver", None))
    binary = _resolve_executable(binary_path, cache_dir=cache_dir, manifest=manifest,
                                 downloader=downloader)
    options.pop("_async", None)
    if options.get("user_data_dir") is not None:
        raise ConfigurationError("user_data_dir is the positional persistent-context path")
    launch_options = dict(context_options or {})
    launch_options.update(options)
    launch_options.update(executable_path=str(binary), headless=config.headless,
                          args=_native_args(plan, persistent=True), user_data_dir=str(path))
    launch_options["ignore_default_args"] = _ignore_default_args(
        launch_options.get("ignore_default_args"), config.args)
    # Set, not inherited: only host mode inherits the host's locale
    # environment. See _locale_environment.
    launch_options["env"] = _launch_environment(plan, launch_options.get("env"))
    if "viewport" not in launch_options and "no_viewport" not in launch_options:
        launch_options["no_viewport"] = True
    if config.proxy is not None and "proxy" not in launch_options:
        launch_options["proxy"] = _playwright_proxy(config.proxy)
    playwright = selection.factory().start()
    try:
        context = playwright.chromium.launch_persistent_context(**launch_options)
    except Exception as exc:
        try:
            playwright.stop()
        except Exception:
            pass
        raise _backend_error(exc) from exc
    return _own_driver(context, playwright, selection.name)


async def launch_async(**options: Any) -> Any:
    """Launch the native browser through a Patchright-compatible async API."""
    config = translate_options(fingerprint=options.pop("fingerprint", None),
                               fingerprint_platform=options.pop("fingerprint_platform", None),
                               profile=options.pop("profile", None), locale=options.pop("locale", None),
                               timezone=options.pop("timezone", None), geoip=options.pop("geoip", True),
                               proxy=options.pop("proxy", None), headless=options.pop("headless", True),
                               user_data_dir=options.pop("user_data_dir", None), args=options.pop("args", None))
    _refuse_user_data_dir(config, entry="launch_async")
    plan = _resolve_plan(config, resolver=options.pop("resolver", None), catalogue=options.pop("catalogue", None),
                         geoip_provider=options.pop("geoip_provider", None), geoip_timeout=options.pop("geoip_timeout", 10.0))
    binary_path = options.pop("binary_path", None)
    cache_dir = options.pop("cache_dir", None)
    manifest = options.pop("manifest", None)
    downloader = options.pop("downloader", None)
    # Publication before the driver, driver before acquisition: see launch().
    _assert_published(binary_path, cache_dir=cache_dir, manifest=manifest)
    selection = _load_async_backend(options.pop("driver", None))
    binary = _resolve_executable(binary_path, cache_dir=cache_dir, manifest=manifest,
                                 downloader=downloader)
    playwright = await selection.factory().start()
    launch_options = dict(options)
    launch_options.update(executable_path=str(binary), headless=config.headless, args=_native_args(plan))
    launch_options["ignore_default_args"] = _ignore_default_args(
        launch_options.get("ignore_default_args"), config.args)
    # Set, not inherited: only host mode inherits the host's locale
    # environment. See _locale_environment.
    launch_options["env"] = _launch_environment(plan, launch_options.get("env"))
    if config.proxy is not None and "proxy" not in launch_options:
        launch_options["proxy"] = _playwright_proxy(config.proxy)
    try:
        return await _own_driver_async(
            await _coherent_viewport_async(await playwright.chromium.launch(**launch_options)),
            playwright, selection.name)
    except Exception as exc:
        try:
            await playwright.stop()
        except Exception:
            pass
        raise _backend_error(exc) from exc


async def _context_owns_browser_async(context: Any, browser: Any) -> Any:
    original = getattr(context, "close", None)
    if not callable(original):
        return context

    async def close(*args: Any, **kwargs: Any) -> Any:
        try:
            return await original(*args, **kwargs)
        finally:
            try:
                await browser.close()
            except Exception:
                pass

    try:
        context.close = close
        context.apostate_browser = browser
    except (AttributeError, TypeError):
        return context
    return context


async def launch_context_async(*, context_options: Mapping[str, Any] | None = None, **options: Any) -> Any:
    browser = await launch_async(**options)
    try:
        return await _context_owns_browser_async(
            await browser.new_context(**dict(context_options or {})), browser)
    except Exception as exc:
        try:
            await browser.close()
        except Exception:
            pass
        raise _backend_error(exc) from exc


async def launch_persistent_context_async(user_data_dir: str | Path, *, context_options: Mapping[str, Any] | None = None,
                                          **options: Any) -> Any:
    path = Path(user_data_dir).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    options["user_data_dir"] = path
    # Keep delegation explicit and awaitable; this avoids a second sync API
    # implementation and makes async failures observable at the same boundary.
    config = translate_options(fingerprint=options.pop("fingerprint", None), fingerprint_platform=options.pop("fingerprint_platform", None),
                               profile=options.pop("profile", None), locale=options.pop("locale", None), timezone=options.pop("timezone", None),
                               geoip=options.pop("geoip", True), proxy=options.pop("proxy", None), headless=options.pop("headless", True),
                               user_data_dir=path, args=options.pop("args", None))
    plan = _resolve_plan(config, resolver=options.pop("resolver", None), catalogue=options.pop("catalogue", None),
                         geoip_provider=options.pop("geoip_provider", None), geoip_timeout=options.pop("geoip_timeout", 10.0))
    binary_path = options.pop("binary_path", None)
    cache_dir = options.pop("cache_dir", None)
    manifest = options.pop("manifest", None)
    downloader = options.pop("downloader", None)
    # Publication before the driver, driver before acquisition: see launch().
    _assert_published(binary_path, cache_dir=cache_dir, manifest=manifest)
    selection = _load_async_backend(options.pop("driver", None))
    binary = _resolve_executable(binary_path, cache_dir=cache_dir, manifest=manifest,
                                 downloader=downloader)
    playwright = await selection.factory().start()
    launch_options = dict(context_options or {})
    launch_options.update(options)
    launch_options.update(executable_path=str(binary), headless=config.headless,
                          args=_native_args(plan, persistent=True), user_data_dir=str(path))
    launch_options["ignore_default_args"] = _ignore_default_args(
        launch_options.get("ignore_default_args"), config.args)
    # Set, not inherited: only host mode inherits the host's locale
    # environment. See _locale_environment.
    launch_options["env"] = _launch_environment(plan, launch_options.get("env"))
    if "viewport" not in launch_options and "no_viewport" not in launch_options:
        launch_options["no_viewport"] = True
    if config.proxy is not None and "proxy" not in launch_options:
        launch_options["proxy"] = _playwright_proxy(config.proxy)
    try:
        return await _own_driver_async(
            await playwright.chromium.launch_persistent_context(**launch_options),
            playwright, selection.name)
    except Exception as exc:
        try:
            await playwright.stop()
        except Exception:
            pass
        raise _backend_error(exc) from exc


__all__ = [
    "DRIVERS", "DriverSelection", "LaunchPlan", "driver_info", "launch", "launch_async", "launch_context", "launch_context_async",
    "launch_persistent_context", "launch_persistent_context_async",
]
