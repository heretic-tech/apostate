"""Catalogue loading and profile resolution for Python launches.

Catalogue version 2 composes a profile from an anchor and the dispersion axes,
and that compositor lives in the browser process in C++ as the only
implementation (docs/HOW_IT_WORKS.md). This module therefore does
not compose. What it does is decide which of the two native selection paths a
launch uses and hand the browser the switches for it:

* an explicit profile file or inline object is validated here and delivered as
  ``--apostate-profile=<base64>``;
* a seed, a persona, or no selector at all is delivered as ``--fingerprint`` /
  ``--fingerprint-platform`` and composed by the browser process.

The second path used to raise. It does not any more: the switches are
implemented in the shipped binary, measured on the macos-arm64 artifact at
152.0.7977.83 (``--fingerprint=42`` yields ``en-GB``/``Europe/London``; a bare
launch composes a fresh identity; ``--fingerprint=host`` inherits the host).
Refusing them here made the package unable to launch the browser at all.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.resources
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .config import (
    CATALOGUE_VERSION,
    CHROMIUM_VERSION,
    HOST_INHERITANCE_SEEDS,
    PROFILE_SCHEMA_VERSION,
    LaunchConfig,
    default_persona_for_host,
    host_persona,
    is_host_seed,
    normalize_platform,
    translate_options,
)
from .errors import ConfigurationError, ProfileError
from .profile_validation import validate_profile

#: The canonical seed that requests host inheritance instead of a composition.
#: ``off``, ``false``, ``0``, ``disable`` and ``disabled`` are equivalent; see
#: ``HOST_INHERITANCE_SEEDS``.
HOST_INHERITANCE_SEED = "host"

_CATALOGUE_MODEL = "anchors+dispersion"
_CATALOGUE_ID = "apostate"
#: Keys that only the retired fourteen-family catalogue carried. A catalogue
#: still carrying one is a version-1 file wearing a version-2 number.
_RETIRED_CATALOGUE_KEYS = ("families", "family_count", "distributions", "compatibility_acceptance")
_DISPERSION_AXES = (
    "os_release", "gpu_identity", "machine_class", "cpu", "memory", "panel",
    "furniture", "font_packs", "media_topology", "audio", "network", "battery",
    "voices", "extensions",
)
_POLICY_KINDS = ("locale", "theme")
_ROTATION_STATUSES = frozenset({"measured-safe", "single-member"})


def _load_json(path: Path, description: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"{description} is unavailable or invalid: {path}") from exc
    if not isinstance(value, Mapping):
        raise ProfileError(f"{description} must be a JSON object: {path}")
    return value


def _package_json(package_root: Any, filename: str, description: str) -> Mapping[str, Any]:
    resource = package_root.joinpath("assets", filename)
    is_symlink = getattr(resource, "is_symlink", None)
    try:
        if callable(is_symlink) and is_symlink():
            raise OSError("symbolic link")
        value = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileError(f"package {description} is unavailable or invalid") from exc
    if not isinstance(value, Mapping):
        raise ProfileError(f"package {description} must be a JSON object")
    return value


def _read_catalogue() -> dict[str, Any]:
    """Read the catalogue index from the checkout, else from package assets."""
    source = Path(__file__).resolve().parents[2] / "resources" / "profiles" / "catalogue.json"
    if source.is_file():
        return dict(_load_json(source, "profile catalogue"))
    try:
        package_root = importlib.resources.files("apostate")
    except (ModuleNotFoundError, TypeError) as exc:
        raise ProfileError("package profile catalogue is unavailable") from exc
    return dict(_package_json(package_root, "catalogue.json", "profile catalogue"))


def _catalogue_platform(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise ProfileError(f"{label} platform is missing")
    try:
        platform = normalize_platform(value)
    except ConfigurationError as exc:
        raise ProfileError(f"{label} platform is unsupported: {value!r}") from exc
    assert platform is not None
    return platform


def _anchor_view(anchors: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(anchors, list) or not anchors:
        raise ProfileError("profile catalogue anchors must be a non-empty list")
    view: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, anchor in enumerate(anchors):
        label = f"profile catalogue anchors[{index}]"
        if not isinstance(anchor, Mapping):
            raise ProfileError(f"{label} must be an object")
        anchor_id = anchor.get("id")
        if not isinstance(anchor_id, str) or not anchor_id.strip():
            raise ProfileError(f"{label} has no id")
        if anchor_id in seen:
            raise ProfileError(f"profile catalogue duplicates anchor {anchor_id}")
        seen.add(anchor_id)
        platform = _catalogue_platform(anchor.get("platform"), label=f"anchor {anchor_id}")
        backend = anchor.get("backend")
        if not isinstance(backend, str) or not backend.strip():
            raise ProfileError(f"anchor {anchor_id} has no backend")
        members = anchor.get("members")
        if not isinstance(members, list) or not members or not all(
            isinstance(member, str) and member.strip() for member in members
        ):
            raise ProfileError(f"anchor {anchor_id} members must be a non-empty list of names")
        declared_count = anchor.get("member_count")
        if declared_count is not None and declared_count != len(members):
            raise ProfileError(f"anchor {anchor_id} member_count does not match its members")
        rotation_status = anchor.get("rotation_status")
        if rotation_status not in _ROTATION_STATUSES:
            raise ProfileError(f"anchor {anchor_id} rotation_status is invalid")
        if rotation_status == "single-member" and len(members) != 1:
            raise ProfileError(f"anchor {anchor_id} claims single-member with {len(members)} members")
        # The anchor's evidence class and digests are validated where they are
        # consumed, by scripts/profile_resolver.py and the compositor.
        view.append({
            "id": anchor_id, "platform": platform, "backend": backend,
            "members": list(members), "rotation_status": rotation_status,
        })
    return tuple(view)


def _axis_view(axes: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(axes, list) or [
        axis.get("axis") if isinstance(axis, Mapping) else None for axis in axes
    ] != list(_DISPERSION_AXES):
        raise ProfileError("profile catalogue axes must list every dispersion axis in order")
    view: list[dict[str, Any]] = []
    for axis in axes:
        name = axis["axis"]
        selection = axis.get("selection")
        servability = axis.get("servability")
        conditioned_on = axis.get("conditioned_on")
        if not isinstance(selection, str) or not selection:
            raise ProfileError(f"dispersion axis {name} has no selection mode")
        if not isinstance(servability, str) or not servability:
            raise ProfileError(f"dispersion axis {name} has no servability rule")
        if not isinstance(conditioned_on, list) or not all(
            isinstance(parent, str) and parent for parent in conditioned_on
        ):
            raise ProfileError(f"dispersion axis {name} conditioned_on must be a list of axis names")
        view.append({
            "axis": name, "selection": selection, "servability": servability,
            "conditioned_on": list(conditioned_on),
            "option_sets": axis.get("option_sets"), "options": axis.get("options"),
        })
    return tuple(view)


def _policy_view(policies: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(policies, Mapping) or set(policies) != set(_POLICY_KINDS):
        raise ProfileError("profile catalogue policies must be exactly locale and theme")
    view: dict[str, tuple[str, ...]] = {}
    for kind in _POLICY_KINDS:
        entries = policies[kind]
        if not isinstance(entries, list) or not entries:
            raise ProfileError(f"profile catalogue policies.{kind} must be a non-empty list")
        ids: list[str] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise ProfileError(f"profile catalogue policies.{kind} contains a non-object entry")
            entry_id = entry.get("id")
            if not isinstance(entry_id, str) or not entry_id.strip():
                raise ProfileError(f"profile catalogue policies.{kind} contains an entry without an id")
            if entry_id in ids:
                raise ProfileError(f"profile catalogue duplicates {kind} policy {entry_id}")
            ids.append(entry_id)
        view[kind] = tuple(ids)
    return view


def _profile_file(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Path):
        return _load_json(value.expanduser(), "profile file")
    if not isinstance(value, str):
        return None
    path = Path(value).expanduser()
    looks_like_path = value.endswith(".json") or "/" in value or "\\" in value or value.startswith(".") or path.is_absolute()
    if not looks_like_path:
        return None
    return _load_json(path, "profile file")


def _profile_platform(profile: Mapping[str, Any]) -> str | None:
    value = profile.get("platform")
    if isinstance(value, Mapping):
        value = value.get("name")
    if not isinstance(value, str) or not value.strip():
        return None
    aliases = {"macos": "macos", "mac": "macos", "mac os": "macos", "mac os x": "macos",
               "os x": "macos", "osx": "macos", "darwin": "macos", "windows": "windows",
               "win": "windows", "win32": "windows", "linux": "linux"}
    lowered = value.strip().lower()
    return aliases.get(lowered)


def _nested_locale(profile: Mapping[str, Any]) -> tuple[str | None, str | None]:
    value = profile.get("locale")
    if not isinstance(value, Mapping):
        return None, None
    languages = value.get("accept_languages")
    timezone = value.get("timezone")
    return (
        languages if isinstance(languages, str) and languages else None,
        timezone if isinstance(timezone, str) and timezone else None,
    )


@dataclass(frozen=True)
class ProfileResolution:
    """Schema-valid profile plus the provenance diagnostics may report.

    Anchor and per-axis provenance is deliberately absent: those belong to a
    composition, and the package does not compose.
    """

    profile: dict[str, Any]
    profile_id: str
    catalogue_version: int
    browser_version: str
    platform: str
    locale_source: str
    timezone_source: str
    identity: str
    #: The effective locale and timezone this launch will present. On the
    #: composing path they travel to the browser as 0085 per-field override
    #: switches rather than inside ``profile``; see ``_native_args``.
    locale: str | None = None
    timezone: str | None = None
    warnings: tuple[str, ...] = ()
    profile_schema_version: int = PROFILE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Return a detached resolution/diagnostics envelope."""
        return {
            "profile": copy.deepcopy(self.profile),
            "profile_id": self.profile_id,
            "catalogue_version": self.catalogue_version,
            "profile_schema_version": self.profile_schema_version,
            "browser_build": self.browser_version,
            "platform": self.platform,
            "locale_source": self.locale_source,
            "timezone_source": self.timezone_source,
            "locale": self.locale,
            "timezone": self.timezone,
            "identity": self.identity,
            "warnings": list(self.warnings),
        }


class DeterministicResolver:
    """Load the catalogue index and resolve the paths that do not compose."""

    def __init__(self, catalogue: Mapping[str, Any] | str | Path | None = None,
                 *, browser_version: str = CHROMIUM_VERSION,
                 catalogue_version: int = CATALOGUE_VERSION) -> None:
        if catalogue is None:
            loaded = _read_catalogue()
        elif isinstance(catalogue, (str, Path)):
            loaded = dict(_load_json(Path(catalogue).expanduser(), "profile catalogue"))
        else:
            loaded = dict(catalogue)
        self.catalogue = loaded

        for retired in _RETIRED_CATALOGUE_KEYS:
            if retired in loaded:
                raise ProfileError(
                    f"profile catalogue still carries the retired key {retired!r}: the "
                    "fourteen-family catalogue was retired with catalogue version 1 and "
                    "replaced by anchors plus dispersion"
                )
        declared_id = loaded.get("catalogue_id")
        if declared_id is not None and declared_id != _CATALOGUE_ID:
            raise ProfileError("profile catalogue catalogue_id is not the Apostate catalogue")
        actual_version = loaded.get("catalogue_version")
        if actual_version != CATALOGUE_VERSION or catalogue_version != CATALOGUE_VERSION:
            raise ProfileError("profile catalogue catalogue_version does not match this package")
        self.catalogue_version = actual_version
        declared_version = loaded.get("version")
        if declared_version is not None and declared_version != actual_version:
            raise ProfileError("profile catalogue version does not match catalogue_version")
        if loaded.get("profile_schema_version") != PROFILE_SCHEMA_VERSION:
            raise ProfileError("profile catalogue profile_schema_version does not match this package")
        self.profile_schema_version = PROFILE_SCHEMA_VERSION
        if loaded.get("model") != _CATALOGUE_MODEL:
            raise ProfileError(f"profile catalogue model must be {_CATALOGUE_MODEL!r}")
        self.model = _CATALOGUE_MODEL
        declared_build = loaded.get("browser_build")
        if declared_build is not None and declared_build != browser_version:
            raise ProfileError("profile catalogue browser_build does not match this package")
        supported = loaded.get("supported_browser_builds")
        if isinstance(supported, list) and browser_version not in supported:
            raise ProfileError("profile catalogue does not support this browser build")
        self.browser_version = browser_version
        self.anchors = _anchor_view(loaded.get("anchors"))
        self.axes = _axis_view(loaded.get("axes"))
        self.policies = _policy_view(loaded.get("policies"))

    def catalogue_summary(self) -> dict[str, Any]:
        """Return the version-2 catalogue shape this package can vouch for."""
        return {
            "catalogue_version": self.catalogue_version,
            "profile_schema_version": self.profile_schema_version,
            "browser_build": self.browser_version,
            "model": self.model,
            "anchors": [dict(anchor) for anchor in self.anchors],
            "axes": [dict(axis) for axis in self.axes],
            "policies": {kind: list(ids) for kind, ids in self.policies.items()},
        }

    def resolve(self, config: LaunchConfig, *, geoip: Mapping[str, Any] | None = None) -> ProfileResolution:
        profile_value = config.profile
        warnings: list[str] = []

        if profile_value is None:
            selected: dict[str, Any] = {}
            if is_host_seed(config.fingerprint):
                if config.fingerprint_platform is not None:
                    # DefaultsCore's 0085 makes the browser refuse this combination
                    # too. Under host inheritance nothing is composed, so a persona
                    # cannot be honoured, and quietly presenting the operator's real
                    # machine when they asked for another platform is the worst
                    # outcome available.
                    raise ProfileError(
                        "host inheritance disables every layer below it, so a platform "
                        "persona cannot be applied at the same time"
                    )
                profile_id = "host-inherited"
                # The host's own platform, deliberately not the 0102 default
                # table: nothing is composed on this path, so the platform the
                # page sees is the host's by definition. Reporting a persona
                # here would describe a composition that does not happen.
                selected_platform = host_persona()
                warnings.append(
                    "host inheritance requested: no profile layer is composed and every "
                    "observable stays host-inherited"
                )
            else:
                # The browser process composes from the seed. The package sends the
                # selectors and no profile envelope: an envelope outranks the seed,
                # so emitting one here would silently suppress composition.
                profile_id = "native-composed"
                selected_platform = config.fingerprint_platform
                if config.fingerprint is None:
                    warnings.append(
                        "no fingerprint seed given: the browser draws a fresh seed on every "
                        "launch, so this identity does not persist. Pass fingerprint=<seed> "
                        "for a stable identity"
                    )
        else:
            explicit = _profile_file(profile_value)
            if explicit is None and isinstance(profile_value, Mapping):
                explicit = profile_value
            if explicit is None:
                if isinstance(profile_value, str):
                    raise ProfileError(
                        "catalogue profile ids were retired with catalogue version 1; catalogue "
                        "version 2 composes a profile from anchors and dispersion instead of "
                        f"offering families, so there is no catalogue profile named {profile_value!r} "
                        "to resolve"
                    )
                raise ProfileError("profile must be a profile file path or an inline profile object")
            selected = dict(explicit)
            profile_id = str(selected.get("id") or "explicit")
            selected_platform = _profile_platform(selected)
            warnings.append(
                "explicit profile bypasses composition; its coherence and servability are the "
                "author's responsibility, not the catalogue's"
            )
            if config.fingerprint is not None:
                warnings.append(
                    "fingerprint seed was ignored: an explicit profile outranks the seed in the "
                    "documented selection precedence"
                )
            if config.fingerprint_platform is not None and selected_platform is not None:
                if config.fingerprint_platform != selected_platform:
                    raise ProfileError("explicit profile platform does not match fingerprint_platform")
            if not selected.get("id"):
                selected["id"] = profile_id

        # No browser process is involved in a local resolution, so unlike the
        # launch path -- which sends no --fingerprint-platform and lets the
        # binary apply its own default -- this has to reproduce that default
        # itself. `default_persona_for_host` is the mirror of 0102's
        # `DefaultPersonaForHost()`; see its note in config.py for why the
        # duplication is deliberate and how it is pinned.
        platform = (selected_platform or config.fingerprint_platform
                    or default_persona_for_host(host_persona()))

        profile_locale, profile_timezone = _nested_locale(selected)
        geo_locale = geoip.get("locale") if isinstance(geoip, Mapping) else None
        if not isinstance(geo_locale, str) and isinstance(geoip, Mapping):
            languages = geoip.get("languages")
            if isinstance(languages, (list, tuple)) and languages:
                geo_locale = ",".join(str(item) for item in languages)
        geo_timezone = geoip.get("timezone") if isinstance(geoip, Mapping) else None
        if config.locale is not None:
            locale = config.locale
            locale_source = "explicit"
        elif isinstance(geo_locale, str) and geo_locale:
            locale = geo_locale
            locale_source = "geoip-derived"
        else:
            locale = profile_locale
            locale_source = "profile-selected" if locale else "host"
        if config.timezone is not None:
            timezone = config.timezone
            timezone_source = "explicit"
        elif isinstance(geo_timezone, str) and geo_timezone:
            timezone = geo_timezone
            timezone_source = "geoip-derived"
        else:
            timezone = profile_timezone
            timezone_source = "profile-selected" if timezone else "host"
        # A locale-only envelope is not a cheap way to force a locale. The
        # browser's InstallComposedProfile() returns early whenever
        # --apostate-profile carries device content, and base/apostate/profile.cc
        # leaves absent fields absent by design, so an envelope carrying nothing
        # but a locale means composition never runs and every other axis -- GPU
        # identity, capability cluster, cores, memory, panel, timezone, fonts,
        # media topology, voices -- silently falls back to the host while the
        # seed is ignored. On the composing path the locale therefore leaves as
        # 0085's per-field override switches, which narrow the draw inside the
        # composed profile; ``_native_args`` emits them from the two fields
        # below. The host-inherited and explicit-profile paths compose nothing
        # either way, so there an envelope suppresses nothing and is the only
        # carrier available.
        if (locale or timezone) and profile_id != "native-composed":
            value = selected.get("locale")
            locale_block = dict(value) if isinstance(value, Mapping) else {}
            if locale:
                locale_block["accept_languages"] = locale
            if timezone:
                locale_block["timezone"] = timezone
            selected["locale"] = locale_block

        identity_payload = {
            "profile_id": profile_id, "fingerprint": config.fingerprint, "platform": platform,
            "catalogue_version": self.catalogue_version, "browser_build": self.browser_version,
        }
        identity = hashlib.sha256(
            json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        selected = validate_profile(selected)
        return ProfileResolution(
            profile=selected, profile_id=profile_id, catalogue_version=self.catalogue_version,
            browser_version=self.browser_version, platform=platform, locale_source=locale_source,
            timezone_source=timezone_source, identity=identity, locale=locale or None,
            timezone=timezone or None, warnings=tuple(warnings),
            profile_schema_version=self.profile_schema_version,
        )

    def __call__(self, config: LaunchConfig) -> dict[str, Any]:
        return self.resolve(config).profile


def load_catalogue(catalogue: Mapping[str, Any] | str | Path | None = None,
                   *, browser_version: str = CHROMIUM_VERSION) -> dict[str, Any]:
    """Load and check the catalogue index, returning its version-2 shape."""
    return DeterministicResolver(catalogue, browser_version=browser_version).catalogue_summary()


def resolve(config: LaunchConfig, *, catalogue: Mapping[str, Any] | str | Path | None = None,
            browser_version: str = CHROMIUM_VERSION,
            geoip: Mapping[str, Any] | None = None) -> ProfileResolution:
    return DeterministicResolver(catalogue, browser_version=browser_version).resolve(config, geoip=geoip)


def resolve_profile(*, fingerprint: int | str | None = None, fingerprint_platform: str | None = None,
                    profile: Any = None, locale: str | None = None, timezone: str | None = None,
                    catalogue: Mapping[str, Any] | str | Path | None = None,
                    browser_version: str = CHROMIUM_VERSION,
                    geoip: Mapping[str, Any] | None = None) -> ProfileResolution:
    """Resolve a profile from the public launch selectors, or fail closed."""
    config = translate_options(
        fingerprint=fingerprint, fingerprint_platform=fingerprint_platform,
        profile=profile, locale=locale, timezone=timezone, geoip=False,
    )
    return resolve(config, catalogue=catalogue, browser_version=browser_version, geoip=geoip)


__all__ = [
    "HOST_INHERITANCE_SEED", "DeterministicResolver", "ProfileResolution",
    "load_catalogue", "resolve", "resolve_profile",
]
