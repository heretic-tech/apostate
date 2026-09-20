"""Proxy-aware GeoIP integration with an injectable provider boundary."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import quote, urlsplit, urlunsplit

from .config import country_locale
from .errors import GeoIPError, GeoIPUnavailableError


class GeoIPProvider(Protocol):
    def lookup(self, proxy: str | None = None, *, timeout: float = 10.0) -> Mapping[str, Any]:
        """Return a mapping containing locale and timezone for the exit IP."""


@dataclass(frozen=True)
class GeoIPResult:
    country_code: str | None = None
    region: str | None = None
    timezone: str | None = None
    locale: str | None = None
    languages: tuple[str, ...] = ()
    ip: str | None = None
    provider: str | None = None
    provider_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "country_code": self.country_code,
            "region": self.region,
            "timezone": self.timezone,
            "locale": self.locale,
            "languages": list(self.languages),
            "ip": self.ip,
            "provider": self.provider,
            "provider_version": self.provider_version,
        }


def _proxy_parts(proxy: str) -> tuple[str, str, int | None] | None:
    try:
        parsed = urlsplit(proxy)
        if not parsed.scheme or not parsed.hostname:
            return None
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return parsed.scheme, host, parsed.port
    except ValueError:
        return None


def redact_proxy(proxy: str | None) -> str | None:
    """Hide proxy usernames/passwords while retaining useful host context."""
    if proxy is None:
        return None
    parts = _proxy_parts(proxy)
    if parts is None:
        return "<proxy>"
    scheme, host, port = parts
    return urlunsplit((scheme, f"{host}{':' + str(port) if port is not None else ''}", "", "", ""))


def _provider_callable(provider: Any) -> Callable[..., Any]:
    if provider is None:
        # The default provider ships inside this package. It used to be
        # imported as ``scripts.geoip``, which is in the repository and not in
        # the wheel, so every installed package answered ``geoip=True`` with
        # "requires the repository GeoIP helper" -- naming something the user
        # could not obtain. Imported here rather than at module scope because
        # it pulls in ssl and http.client for the SOCKS5 transport, which a
        # launch that passes ``geoip=False`` should not pay for.
        from . import _prelaunch_geoip

        return _prelaunch_geoip.resolve_prelaunch_geoip
    if callable(provider):
        return provider
    for name in ("lookup", "resolve_prelaunch_geoip", "lookup_exit_ip", "resolve_geoip", "resolve"):
        method = getattr(provider, name, None)
        if callable(method):
            return method
    raise GeoIPUnavailableError("configured GeoIP provider has no lookup method")


def _call_provider(provider: Any, proxy: str | None, timeout: float) -> Any:
    method = _provider_callable(provider)
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        parameters = {}
    kwargs: dict[str, Any] = {}
    if "geoip" in parameters:
        kwargs["geoip"] = True
    if "proxy" in parameters:
        kwargs["proxy"] = proxy
    if "timeout" in parameters:
        kwargs["timeout"] = timeout
    if kwargs:
        return method(**kwargs)
    try:
        return method(proxy, timeout)
    except TypeError:
        return method(proxy)


def _first(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = mapping.get(name)
        if value is not None:
            return value
    return None


#: Country -> locale comes from ``config/country-locales.json``, shipped as
#: package data and shared byte-for-byte with the npm package, so one provider
#: payload resolves to one locale everywhere. It used to be a 45-country table
#: written out here and copied into two other files; a Malaysian exit matched
#: none of them.

_TIMEZONE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9._+-]*(?:/[A-Za-z0-9._+-]+)+$")


def _provider_timezone(value: Any) -> str | None:
    """Accept only what can drive ``--fingerprint-timezone``.

    Providers answer in three shapes: an IANA identifier, a nested object
    (``{"id": "Europe/Berlin"}``), and a bare UTC offset (``"+02:00"``, which
    freeipapi returns). Only an identifier is usable, so anything else is
    unresolved rather than patched up into something that looks like one. This
    mirrors ``_prelaunch_geoip``'s ``_valid_provider_timezone``.
    """

    if isinstance(value, Mapping):
        value = value.get("id") or value.get("name") or value.get("timezone")
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 128:
        return None
    if value.upper() in {"UTC", "GMT"} or _TIMEZONE_PATTERN.fullmatch(value):
        return value
    return None


def normalize_result(value: Any) -> GeoIPResult:
    if isinstance(value, GeoIPResult):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
    if not isinstance(value, Mapping):
        raise GeoIPError("GeoIP provider returned a non-object result")
    timezone = _provider_timezone(_first(value, "timezone", "time_zone", "tz"))
    locale = _first(value, "locale", "language", "default_locale")
    languages = _first(value, "languages", "accept_languages")
    if isinstance(languages, str):
        languages = tuple(part.strip() for part in re.split(r"[,;]", languages) if part.strip())
    elif isinstance(languages, Sequence) and not isinstance(languages, (bytes, bytearray)):
        languages = tuple(str(part) for part in languages if part)
    else:
        languages = ()
    # A two-letter code or nothing, as _prelaunch_geoip's _country_code does: a
    # provider that answers "Germany" under `country` has given a name rather
    # than a code, and a name maps to nothing.
    country = _first(value, "country_code", "countryCode", "country")
    country = country.strip().upper() if isinstance(country, str) else None
    if country is not None and not re.fullmatch(r"[A-Z]{2}", country):
        country = None
    if not isinstance(locale, str) and not languages and country:
        locale = country_locale(country)
    provider_name = value.get("provider") if isinstance(value.get("provider"), str) else None
    version = value.get("provider_version") if isinstance(value.get("provider_version"), str) else None
    return GeoIPResult(
        country_code=country,
        region=_first(value, "region", "region_code", "regionCode"),
        timezone=timezone,
        locale=locale if isinstance(locale, str) else None,
        languages=languages,
        ip=_first(value, "ip", "address", "exit_ip"),
        provider=provider_name,
        provider_version=version,
    )


def resolve_geoip(provider: Any = None, *, proxy: str | None = None,
                  timeout: float = 10.0, require_locale: bool = True,
                  require_timezone: bool = True) -> GeoIPResult:
    if timeout <= 0:
        raise GeoIPError("GeoIP timeout must be greater than zero")
    try:
        result = normalize_result(_call_provider(provider, proxy, timeout))
    except GeoIPError:
        raise
    except Exception as exc:
        detail = str(exc)
        if proxy:
            detail = detail.replace(proxy, redact_proxy(proxy) or "<proxy>")
        raise GeoIPError(
            f"GeoIP lookup failed for {redact_proxy(proxy) or 'direct network'}: {detail}"
        ) from exc
    missing = []
    if require_timezone and not result.timezone:
        missing.append("timezone")
    if require_locale and not result.locale and not result.languages:
        missing.append("locale")
    if missing:
        raise GeoIPError("GeoIP lookup did not provide " + " or ".join(missing))
    return result


__all__ = ["GeoIPProvider", "GeoIPResult", "redact_proxy", "normalize_result", "resolve_geoip"]
