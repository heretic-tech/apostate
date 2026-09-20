"""Dependency-free prelaunch GeoIP and network-localization helpers.

This module travels inside the installed package. It used to live only at
``scripts/geoip.py``, which meant ``geoip=True`` worked from a checkout and
failed from a wheel with "requires the repository GeoIP helper" -- a helper
the user had never heard of and could not install. ``scripts/geoip.py`` is now
a re-export of this module, so there is one implementation and it is the one
that ships.

The resolver is deliberately small so Python and Node package adapters can use
one deterministic launch contract without sharing a runtime dependency.  A
lookup is performed before browser startup, and the resulting values are
immutable for the lifetime of a resolver instance.

The default transport is standard library only and speaks three proxy
families: none, ``http(s)://`` through :mod:`urllib.request`'s ProxyHandler,
and ``socks5://``/``socks5h://`` through a minimal RFC 1928 CONNECT handshake
with RFC 1929 username/password authentication, wrapped in :mod:`ssl` for an
https endpoint. A SOCKS proxy is the common shape for residential exits, and
requiring PySocks for it would make an anti-detect launcher's headline feature
an optional extra.  Tests and package adapters may still inject a callable
accepting :class:`HTTPTransportRequest`, or a callable with the equivalent
``(url, timeout, proxy)`` arguments; an injected transport owns the actual
network operation and must honor ``timeout``.

This module does not enforce WebRTC/native network behavior.  It exposes that
requirement as ``pending-native-capability`` so callers cannot mistake launch
metadata for native enforcement.
"""

import http.client
import inspect
import ipaddress
import json
import math
import re
import socket
import ssl
import struct
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional, Tuple, Union

from .config import CATALOGUE_VERSION, CHROMIUM_VERSION, PACKAGE_VERSION, country_locale

_UNSET = object()

RESOLVER_VERSION = 1
PROVIDER_NAME = "ipwho.is"
PROVIDER_VERSION = "1"
MAPPING_VERSION = 1
DEFAULT_ENDPOINT = "https://ipwho.is/"
DEFAULT_TIMEOUT_SECONDS = 2.0
MAX_TIMEOUT_SECONDS = 30.0
MAX_RESPONSE_BYTES = 1024 * 1024

# The country -> locale policy lives in ``config/country-locales.json`` and
# ships as package data; see ``apostate.config.country_locale``. It used to be
# a 45-country table written out here, which meant a Malaysian exit resolved
# no locale at all.

_SUPPORTED_PROXY_SCHEMES = frozenset({"http", "https", "socks5", "socks5h"})
#: What ``urllib``'s ProxyHandler can carry on its own. The rest are tunnelled
#: by ``_socks5_transport``; nothing here is unsupported.
_URLOPENER_PROXY_SCHEMES = frozenset({"http", "https"})
_SOCKS_PROXY_SCHEMES = frozenset({"socks5", "socks5h"})
_LOCALE_PATTERN = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
_TIMEZONE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9._+-]*(?:/[A-Za-z0-9._+-]+)+$")
_COUNTRY_PATTERN = re.compile(r"^[A-Za-z]{2}$")


class GeoIPError(RuntimeError):
    """Base error for visible prelaunch GeoIP failures."""

    def __init__(self, message: str, *, diagnostics: Optional["GeoIPDiagnostics"] = None):
        super().__init__(message)
        self.diagnostics = diagnostics


class GeoIPConfigurationError(GeoIPError, ValueError):
    """The launch or proxy configuration is invalid."""


class GeoIPLookupError(GeoIPError):
    """The provider could not produce a usable response."""


class GeoIPTimeoutError(GeoIPLookupError, TimeoutError):
    """The provider request exceeded the configured bounded timeout."""


class GeoIPProviderError(GeoIPLookupError):
    """The provider returned an error or malformed payload."""


class GeoIPResolutionError(GeoIPError):
    """A resolver was asked to change after its launch result was fixed."""


@dataclass(frozen=True)
class ProxyConfig:
    """Parsed proxy endpoint with credentials hidden from repr/diagnostics."""

    scheme: str
    host: str
    port: int
    username: Optional[str] = field(default=None, repr=False)
    password: Optional[str] = field(default=None, repr=False)
    _raw_url: str = field(default="", repr=False, compare=False)

    @property
    def url(self) -> str:
        """The authenticated URL for the transport, when credentials exist.

        Callers should use :attr:`redacted_url` in logs and diagnostics.  The
        authenticated value is kept as a property rather than being included
        in any result or exception representation.
        """

        if self._raw_url:
            return self._raw_url
        userinfo = ""
        if self.username is not None:
            userinfo = urllib.parse.quote(self.username, safe="")
            if self.password is not None:
                userinfo += ":" + urllib.parse.quote(self.password, safe="")
            userinfo += "@"
        host = _format_proxy_host(self.host)
        return f"{self.scheme}://{userinfo}{host}:{self.port}"

    @property
    def redacted_url(self) -> str:
        """A credential-free proxy URL suitable for diagnostics."""

        return f"{self.scheme}://{_format_proxy_host(self.host)}:{self.port}"

    @property
    def supports_urllib(self) -> bool:
        return self.scheme in _URLOPENER_PROXY_SCHEMES

    def __repr__(self) -> str:
        return (
            "ProxyConfig("
            f"scheme={self.scheme!r}, host={self.host!r}, port={self.port!r}, "
            f"redacted_url={self.redacted_url!r})"
        )


def _format_proxy_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def _redact_proxy_text(value: str) -> str:
    """Redact URL userinfo even when a malformed URL cannot be parsed."""

    text = str(value)
    text = re.sub(
        r"(?i)((?:https?|socks5h?)://)([^/\s]+)@",
        r"\1<redacted>@",
        text,
    )
    return text


def redact_proxy_url(value: Union[str, ProxyConfig, None]) -> Optional[str]:
    """Return a credential-free representation for diagnostics."""

    if value is None:
        return None
    if isinstance(value, ProxyConfig):
        return value.redacted_url
    try:
        return parse_proxy_url(value).redacted_url
    except (GeoIPConfigurationError, TypeError):
        return "<redacted-proxy>"


def parse_proxy_url(value: Union[str, ProxyConfig]) -> ProxyConfig:
    """Parse and validate an HTTP(S)/SOCKS proxy URL.

    Credentials are retained only for the transport.  The returned object's
    repr and ``redacted_url`` never contain them.  All four schemes reach the
    network: http(s) through urllib's ProxyHandler, socks5 and socks5h through
    this module's own RFC 1928 CONNECT.
    """

    if isinstance(value, ProxyConfig):
        return value
    if not isinstance(value, str) or not value.strip():
        raise GeoIPConfigurationError("Proxy URL must be a non-empty string")
    raw = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise GeoIPConfigurationError("Proxy URL contains control characters")
    try:
        parts = urllib.parse.urlsplit(raw)
        scheme = parts.scheme.lower()
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise GeoIPConfigurationError(
            f"Invalid proxy URL {_redact_proxy_text(raw)!r}"
        ) from None
    if scheme not in _SUPPORTED_PROXY_SCHEMES:
        raise GeoIPConfigurationError(
            f"Unsupported proxy scheme {scheme!r}; expected http, https, socks5, or socks5h"
        )
    if not host:
        raise GeoIPConfigurationError(
            f"Invalid proxy URL {_redact_proxy_text(raw)!r}: host is required"
        )
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise GeoIPConfigurationError(
            f"Invalid proxy URL {_redact_proxy_text(raw)!r}: path, query, and fragment are not allowed"
        )
    if port is None:
        port = {"http": 80, "https": 443, "socks5": 1080, "socks5h": 1080}[scheme]
    if not 1 <= port <= 65535:
        raise GeoIPConfigurationError("Proxy port must be between 1 and 65535")
    username = urllib.parse.unquote(parts.username) if parts.username is not None else None
    password = urllib.parse.unquote(parts.password) if parts.password is not None else None
    if username is not None and any(ord(char) < 32 for char in username):
        raise GeoIPConfigurationError("Proxy username contains control characters")
    if password is not None and any(ord(char) < 32 for char in password):
        raise GeoIPConfigurationError("Proxy password contains control characters")
    return ProxyConfig(
        scheme=scheme,
        host=host.lower(),
        port=port,
        username=username,
        password=password,
        _raw_url=raw,
    )


# Short alias for adapters that use the noun rather than the URL-specific name.
parse_proxy = parse_proxy_url


@dataclass(frozen=True)
class HTTPTransportRequest:
    """Immutable request description passed to an injected HTTP transport."""

    url: str
    timeout: float
    mode: str
    proxy: Optional[ProxyConfig]
    headers: Tuple[Tuple[str, str], ...] = (("Accept", "application/json"),)

    @property
    def redacted_proxy(self) -> Optional[str]:
        return redact_proxy_url(self.proxy)

    @property
    def proxy_url(self) -> Optional[str]:
        """Authenticated proxy URL for transport implementations only."""

        return self.proxy.url if self.proxy is not None else None

    @property
    def proxy_redacted_url(self) -> Optional[str]:
        return self.redacted_proxy

    def __repr__(self) -> str:
        return (
            "HTTPTransportRequest("
            f"url={self.url!r}, timeout={self.timeout!r}, mode={self.mode!r}, "
            f"proxy={self.redacted_proxy!r}, headers={self.headers!r})"
        )


@dataclass(frozen=True)
class HTTPTransportResponse:
    """Small response shape accepted and returned by transport adapters."""

    status: int
    body: Union[bytes, str]
    headers: Tuple[Tuple[str, str], ...] = ()


# Friendly aliases make the transport contract easy to discover without
# duplicating classes.
GeoIPRequest = HTTPTransportRequest
GeoIPResponse = HTTPTransportResponse


@dataclass(frozen=True)
class GeoIPDiagnostics:
    """Machine-readable, credential-free lookup diagnostics."""

    lookup_mode: str
    request_url: str
    proxy: Optional[str]
    timeout_seconds: float
    provider: str
    provider_version: str
    mapping_version: int
    resolver_version: int
    status: str
    error: Optional[str] = None

    @property
    def mode(self) -> str:
        return self.lookup_mode

    def to_dict(self) -> dict[str, Any]:
        return {
            "lookup_mode": self.lookup_mode,
            "request_url": self.request_url,
            "proxy": self.proxy,
            "timeout_seconds": self.timeout_seconds,
            "provider": self.provider,
            "provider_version": self.provider_version,
            "mapping_version": self.mapping_version,
            "resolver_version": self.resolver_version,
            "status": self.status,
            "error": self.error,
        }


@dataclass(frozen=True)
class WebRTCNativeCapability:
    """Honest status of native network/WebRTC alignment at this layer."""

    status: str = "pending-native-capability"
    enforced: bool = False
    capability: str = "native-webrtc-network-alignment"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "enforced": self.enforced,
            "capability": self.capability,
        }


@dataclass(frozen=True)
class GeoIPResult:
    """Immutable effective network-localization result for one launch."""

    lookup_mode: str
    ip: Optional[str]
    country_code: Optional[str]
    region: Optional[str]
    derived_locale: Optional[str]
    derived_timezone: Optional[str]
    locale: Optional[str]
    timezone: Optional[str]
    accept_languages: Optional[str]
    locale_source: str
    timezone_source: str
    provider: str
    provider_version: str
    mapping_version: int
    resolver_version: int
    diagnostics: GeoIPDiagnostics
    webrtc: WebRTCNativeCapability = field(default_factory=WebRTCNativeCapability)

    @property
    def native_capability_pending(self) -> bool:
        return self.webrtc.status == "pending-native-capability"

    @property
    def proxy(self) -> Optional[str]:
        return self.diagnostics.proxy

    def launch_overrides(self) -> Mapping[str, Any]:
        """Return a fresh, credential-free mapping for browser launch code."""

        values: dict[str, Any] = {
            "locale": self.locale,
            "timezone": self.timezone,
            "accept_languages": self.accept_languages,
            "geoip_lookup_mode": self.lookup_mode,
            "geoip_provider": self.provider,
            "geoip_provider_version": self.provider_version,
            "geoip_mapping_version": self.mapping_version,
            "webrtc_capability": self.webrtc.status,
        }
        return MappingProxyType(values)

    # CamelCase is useful only at package boundaries; the shared result remains
    # snake_case.  Return a copy so callers cannot mutate the cached result.
    def to_dict(self) -> dict[str, Any]:
        return {
            "lookup_mode": self.lookup_mode,
            "ip": self.ip,
            "country_code": self.country_code,
            "region": self.region,
            "derived_locale": self.derived_locale,
            "derived_timezone": self.derived_timezone,
            "locale": self.locale,
            "timezone": self.timezone,
            "accept_languages": self.accept_languages,
            "locale_source": self.locale_source,
            "timezone_source": self.timezone_source,
            "provider": self.provider,
            "provider_version": self.provider_version,
            "mapping_version": self.mapping_version,
            "resolver_version": self.resolver_version,
            "diagnostics": self.diagnostics.to_dict(),
            "webrtc": self.webrtc.to_dict(),
        }


class GeoIPResolver:
    """Resolve network localization once and retain that result immutably.

    ``geoip=True`` performs one request through the supplied proxy (or direct
    when no proxy is configured).  Explicit locale/timezone values always win;
    profile values are considered only after a successful lookup.  No host
    locale/timezone defaults are fabricated by this class.
    """

    def __init__(
        self,
        *,
        geoip: bool = True,
        proxy: Union[str, ProxyConfig, None] = None,
        locale: Optional[str] = None,
        timezone: Optional[str] = None,
        profile_locale: Optional[str] = None,
        profile_timezone: Optional[str] = None,
        transport: Optional[Callable[..., Any]] = None,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        provider: str = PROVIDER_NAME,
        provider_version: str = PROVIDER_VERSION,
        mapping_version: int = MAPPING_VERSION,
        resolver_version: int = RESOLVER_VERSION,
    ) -> None:
        if not isinstance(geoip, bool):
            raise GeoIPConfigurationError("geoip must be a boolean")
        self._geoip = geoip
        self._proxy = parse_proxy_url(proxy) if proxy is not None else None
        self._locale = _validate_locale(locale, "locale") if locale is not None else None
        self._timezone = _validate_timezone(timezone, "timezone") if timezone is not None else None
        self._profile_locale = (
            _validate_locale(profile_locale, "profile_locale") if profile_locale is not None else None
        )
        self._profile_timezone = (
            _validate_timezone(profile_timezone, "profile_timezone")
            if profile_timezone is not None
            else None
        )
        self._endpoint = _validate_endpoint(endpoint)
        self._timeout = _validate_timeout(timeout)
        if not isinstance(provider, str) or not provider.strip():
            raise GeoIPConfigurationError("provider must be a non-empty string")
        if not isinstance(provider_version, str) or not provider_version.strip():
            raise GeoIPConfigurationError("provider_version must be a non-empty string")
        if not isinstance(mapping_version, int) or mapping_version < 1:
            raise GeoIPConfigurationError("mapping_version must be a positive integer")
        if not isinstance(resolver_version, int) or resolver_version < 1:
            raise GeoIPConfigurationError("resolver_version must be a positive integer")
        self._transport = transport or urllib_transport
        if not callable(self._transport):
            raise GeoIPConfigurationError("transport must be callable")
        self._provider = provider.strip()
        self._provider_version = provider_version.strip()
        self._mapping_version = mapping_version
        self._resolver_version = resolver_version
        self._lock = threading.RLock()
        self._result: Optional[GeoIPResult] = None
        self._last_request: Optional[HTTPTransportRequest] = None

    @property
    def resolved(self) -> bool:
        return self._result is not None

    @property
    def result(self) -> Optional[GeoIPResult]:
        return self._result

    @property
    def last_request(self) -> Optional[HTTPTransportRequest]:
        return self._last_request

    def resolve(
        self,
        *,
        geoip: Optional[bool] = None,
        proxy: Union[str, ProxyConfig, None, object] = _UNSET,
        locale: Union[str, None, object] = _UNSET,
        timezone: Union[str, None, object] = _UNSET,
        profile_locale: Union[str, None, object] = _UNSET,
        profile_timezone: Union[str, None, object] = _UNSET,
    ) -> GeoIPResult:
        """Resolve and cache one result; later configuration changes are rejected."""

        with self._lock:
            requested = {
                "geoip": self._geoip if geoip is None else geoip,
                "proxy": self._proxy if proxy is _UNSET else (parse_proxy_url(proxy) if proxy is not None else None),
                "locale": self._locale if locale is _UNSET else (_validate_locale(locale, "locale") if locale is not None else None),
                "timezone": self._timezone if timezone is _UNSET else (_validate_timezone(timezone, "timezone") if timezone is not None else None),
                "profile_locale": self._profile_locale if profile_locale is _UNSET else (_validate_locale(profile_locale, "profile_locale") if profile_locale is not None else None),
                "profile_timezone": self._profile_timezone if profile_timezone is _UNSET else (_validate_timezone(profile_timezone, "profile_timezone") if profile_timezone is not None else None),
            }
            if not isinstance(requested["geoip"], bool):
                raise GeoIPConfigurationError("geoip must be a boolean")
            if self._result is not None:
                if (
                    requested["geoip"] != self._geoip
                    or requested["proxy"] != self._proxy
                    or requested["locale"] != self._locale
                    or requested["timezone"] != self._timezone
                    or requested["profile_locale"] != self._profile_locale
                    or requested["profile_timezone"] != self._profile_timezone
                ):
                    raise GeoIPResolutionError(
                        "GeoIP resolution is already fixed for this launch"
                    )
                return self._result
            self._geoip = requested["geoip"]
            self._proxy = requested["proxy"]
            self._locale = requested["locale"]
            self._timezone = requested["timezone"]
            self._profile_locale = requested["profile_locale"]
            self._profile_timezone = requested["profile_timezone"]
            result = self._resolve_once()
            self._result = result
            return result

    def _resolve_once(self) -> GeoIPResult:
        mode = "proxy" if self._proxy is not None else "direct"
        if not self._geoip:
            diagnostics = GeoIPDiagnostics(
                lookup_mode="disabled",
                request_url=self._endpoint,
                proxy=redact_proxy_url(self._proxy),
                timeout_seconds=self._timeout,
                provider="disabled",
                provider_version="0",
                mapping_version=self._mapping_version,
                resolver_version=self._resolver_version,
                status="disabled",
            )
            effective_locale = self._locale or self._profile_locale
            effective_timezone = self._timezone or self._profile_timezone
            return GeoIPResult(
                lookup_mode="disabled",
                ip=None,
                country_code=None,
                region=None,
                derived_locale=None,
                derived_timezone=None,
                locale=effective_locale,
                timezone=effective_timezone,
                accept_languages=_accept_languages(effective_locale),
                locale_source="explicit" if self._locale else ("profile" if self._profile_locale else "unresolved"),
                timezone_source="explicit" if self._timezone else ("profile" if self._profile_timezone else "unresolved"),
                provider="disabled",
                provider_version="0",
                mapping_version=self._mapping_version,
                resolver_version=self._resolver_version,
                diagnostics=diagnostics,
            )

        try:
            payload = self._lookup(mode)
            ip, country_code, region, derived_locale, derived_timezone = _parse_provider_payload(payload)
        except GeoIPProviderError as exc:
            if exc.diagnostics is None:
                exc.diagnostics = self._error_diagnostics(mode, "provider-error", str(exc))
            raise
        effective_locale = self._locale or derived_locale or self._profile_locale
        effective_timezone = self._timezone or derived_timezone or self._profile_timezone
        diagnostics = GeoIPDiagnostics(
            lookup_mode=mode,
            request_url=self._endpoint,
            proxy=redact_proxy_url(self._proxy),
            timeout_seconds=self._timeout,
            provider=self._provider,
            provider_version=self._provider_version,
            mapping_version=self._mapping_version,
            resolver_version=self._resolver_version,
            status="resolved",
        )
        return GeoIPResult(
            lookup_mode=mode,
            ip=ip,
            country_code=country_code,
            region=region,
            derived_locale=derived_locale,
            derived_timezone=derived_timezone,
            locale=effective_locale,
            timezone=effective_timezone,
            accept_languages=_accept_languages(effective_locale),
            locale_source=("explicit" if self._locale else ("geoip" if derived_locale else ("profile" if self._profile_locale else "unresolved"))),
            timezone_source=("explicit" if self._timezone else ("geoip" if derived_timezone else ("profile" if self._profile_timezone else "unresolved"))),
            provider=self._provider,
            provider_version=self._provider_version,
            mapping_version=self._mapping_version,
            resolver_version=self._resolver_version,
            diagnostics=diagnostics,
        )

    def _lookup(self, mode: str) -> Mapping[str, Any]:
        request = HTTPTransportRequest(
            url=self._endpoint,
            timeout=self._timeout,
            mode=mode,
            proxy=self._proxy,
        )
        self._last_request = request
        try:
            raw_response = _call_transport(self._transport, request)
            response = _coerce_response(raw_response)
        except GeoIPError as exc:
            if exc.diagnostics is None:
                exc.diagnostics = self._error_diagnostics(mode, "transport-error", str(exc))
            raise
        except (TimeoutError, socket.timeout) as exc:
            diagnostics = self._error_diagnostics(mode, "timeout", str(exc) or "transport timeout")
            raise GeoIPTimeoutError(
                f"GeoIP lookup timed out after {self._timeout:g}s ({mode} mode; proxy={redact_proxy_url(self._proxy) or 'none'})",
                diagnostics=diagnostics,
            ) from None
        except urllib.error.URLError as exc:
            if _is_timeout_error(exc):
                diagnostics = self._error_diagnostics(mode, "timeout", str(exc) or "transport timeout")
                raise GeoIPTimeoutError(
                    f"GeoIP lookup timed out after {self._timeout:g}s ({mode} mode; proxy={redact_proxy_url(self._proxy) or 'none'})",
                    diagnostics=diagnostics,
                ) from None
            safe = _redact_proxy_text(str(exc) or exc.__class__.__name__)
            diagnostics = self._error_diagnostics(mode, "transport-error", safe)
            raise GeoIPLookupError(
                f"GeoIP lookup failed in {mode} mode: {safe}", diagnostics=diagnostics
            ) from None
        except Exception as exc:
            safe = _redact_proxy_text(str(exc) or exc.__class__.__name__)
            diagnostics = self._error_diagnostics(mode, "transport-error", safe)
            raise GeoIPLookupError(
                f"GeoIP lookup failed in {mode} mode: {safe}", diagnostics=diagnostics
            ) from None
        if response.status < 200 or response.status >= 300:
            message = f"provider returned HTTP {response.status}"
            diagnostics = self._error_diagnostics(mode, "http-error", message)
            raise GeoIPProviderError(message, diagnostics=diagnostics)
        if isinstance(response.body, bytes):
            if len(response.body) > MAX_RESPONSE_BYTES:
                message = "provider response exceeded the maximum size"
                diagnostics = self._error_diagnostics(mode, "response-too-large", message)
                raise GeoIPProviderError(message, diagnostics=diagnostics)
            try:
                body = response.body.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                message = "provider response was not valid UTF-8"
                diagnostics = self._error_diagnostics(mode, "invalid-response", message)
                raise GeoIPProviderError(message, diagnostics=diagnostics) from None
        elif isinstance(response.body, str):
            if len(response.body.encode("utf-8")) > MAX_RESPONSE_BYTES:
                message = "provider response exceeded the maximum size"
                diagnostics = self._error_diagnostics(mode, "response-too-large", message)
                raise GeoIPProviderError(message, diagnostics=diagnostics)
            body = response.body
        elif isinstance(response.body, Mapping):
            # A mapping is accepted only from injected transports; urllib always
            # returns bytes.  It avoids forcing tests through a JSON serializer.
            return response.body
        else:
            message = "provider response body must be JSON bytes, text, or a mapping"
            diagnostics = self._error_diagnostics(mode, "invalid-response", message)
            raise GeoIPProviderError(message, diagnostics=diagnostics)
        try:
            payload = json.loads(body)
        except (TypeError, ValueError, UnicodeError):
            message = "provider returned invalid JSON"
            diagnostics = self._error_diagnostics(mode, "invalid-json", message)
            raise GeoIPProviderError(message, diagnostics=diagnostics) from None
        if not isinstance(payload, Mapping):
            message = "provider JSON root must be an object"
            diagnostics = self._error_diagnostics(mode, "invalid-response", message)
            raise GeoIPProviderError(message, diagnostics=diagnostics)
        return payload

    def _error_diagnostics(self, mode: str, status: str, error: str) -> GeoIPDiagnostics:
        return GeoIPDiagnostics(
            lookup_mode=mode,
            request_url=self._endpoint,
            proxy=redact_proxy_url(self._proxy),
            timeout_seconds=self._timeout,
            provider=self._provider,
            provider_version=self._provider_version,
            mapping_version=self._mapping_version,
            resolver_version=self._resolver_version,
            status=status,
            error=_redact_proxy_text(error),
        )


def _is_timeout_error(error: BaseException) -> bool:
    if isinstance(error, (TimeoutError, socket.timeout)):
        return True
    reason = getattr(error, "reason", None)
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return True
    return "timed out" in str(error).lower()

def _validate_endpoint(endpoint: str) -> str:
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise GeoIPConfigurationError("GeoIP endpoint must be a non-empty URL")
    endpoint = endpoint.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in endpoint):
        raise GeoIPConfigurationError("GeoIP endpoint contains control characters")
    try:
        parts = urllib.parse.urlsplit(endpoint)
        host = parts.hostname
        username = parts.username
        password = parts.password
    except ValueError:
        raise GeoIPConfigurationError("GeoIP endpoint must be an http(s) URL without credentials") from None
    if parts.scheme not in ("http", "https") or not host or username or password:
        raise GeoIPConfigurationError("GeoIP endpoint must be an http(s) URL without credentials")
    return endpoint


def _validate_timeout(value: float) -> float:
    if isinstance(value, bool):
        raise GeoIPConfigurationError("timeout must be a finite positive number")
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        raise GeoIPConfigurationError("timeout must be a finite positive number") from None
    if not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_TIMEOUT_SECONDS:
        raise GeoIPConfigurationError(
            f"timeout must be greater than zero and no more than {MAX_TIMEOUT_SECONDS:g}s"
        )
    return timeout


def _validate_locale(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or not _LOCALE_PATTERN.fullmatch(value.strip()):
        raise GeoIPConfigurationError(f"{name} must be a single valid BCP-47 locale")
    return value.strip()


def _validate_timezone(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 128:
        raise GeoIPConfigurationError(f"{name} must be a non-empty timezone identifier")
    value = value.strip()
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise GeoIPConfigurationError(f"{name} must be a timezone identifier")
    if value.upper() in {"UTC", "GMT"} or _TIMEZONE_PATTERN.fullmatch(value):
        return value
    raise GeoIPConfigurationError(f"{name} must be a timezone identifier")


def _accept_languages(locale: Optional[str]) -> Optional[str]:
    if locale is None:
        return None
    base = locale.split("-", 1)[0]
    return locale if base.lower() == locale.lower() else f"{locale},{base}"


def _country_code(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip().upper()
    return value if _COUNTRY_PATTERN.fullmatch(value) else None


def _valid_ip(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def _valid_region(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 256 or any(ord(char) < 32 for char in value):
        return None
    return value


def _valid_provider_timezone(value: Any) -> Optional[str]:
    if isinstance(value, Mapping):
        value = value.get("id") or value.get("name") or value.get("timezone")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _validate_timezone(value.strip(), "provider timezone")
    except GeoIPConfigurationError:
        return None


def _parse_provider_payload(payload: Mapping[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
    if payload.get("success") is False:
        message = payload.get("message")
        safe = _redact_proxy_text(message if isinstance(message, str) else "provider reported failure")
        raise GeoIPProviderError(f"provider reported failure: {safe}")
    ip = _valid_ip(payload.get("ip") or payload.get("query"))
    country = _country_code(payload.get("country_code") or payload.get("countryCode"))
    region = _valid_region(payload.get("region") or payload.get("region_name") or payload.get("regionName"))
    timezone = _valid_provider_timezone(payload.get("timezone") or payload.get("time_zone"))
    locale = country_locale(country)
    # A successful provider may omit a field.  Leave that field unresolved so
    # the caller sees the gap; never fill it with a host or en-US/UTC default.
    if not any(value is not None for value in (ip, country, region, timezone)):
        raise GeoIPProviderError("provider response did not contain usable GeoIP fields")
    return ip, country, region, locale, timezone


def _call_transport(transport: Callable[..., Any], request: HTTPTransportRequest) -> Any:
    """Invoke supported injected transport shapes without duplicate requests."""

    # A request method is the least surprising shape for small test doubles.
    method = getattr(transport, "request", None)
    if callable(method):
        return method(request)
    try:
        signature = inspect.signature(transport)
    except (TypeError, ValueError):
        return transport(request)
    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
    ]
    names = {parameter.name for parameter in signature.parameters.values()}
    if any(name in names for name in ("request", "req", "http_request")):
        return transport(request)
    if any(name in names for name in ("url", "uri", "endpoint")):
        keyword_values = {
            "url": request.url,
            "uri": request.url,
            "endpoint": request.url,
            "timeout": request.timeout,
            "proxy": request.proxy,
            "headers": dict(request.headers),
            "mode": request.mode,
        }
        kwargs = {
            parameter.name: keyword_values[parameter.name]
            for parameter in signature.parameters.values()
            if parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)
            and parameter.name in keyword_values
        }
        if len(positional) >= 3:
            return transport(request.url, request.timeout, request.proxy)
        return transport(**kwargs)
    if len(positional) >= 3:
        return transport(request.url, request.timeout, request.proxy)
    if len(positional) == 2:
        return transport(request.url, request.timeout)
    return transport(request)


def _coerce_response(value: Any) -> HTTPTransportResponse:
    if isinstance(value, HTTPTransportResponse):
        return value
    if isinstance(value, Mapping):
        return HTTPTransportResponse(status=200, body=value)
    if isinstance(value, (bytes, str)):
        return HTTPTransportResponse(status=200, body=value)
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], int):
        return HTTPTransportResponse(status=value[0], body=value[1])
    status = getattr(value, "status", getattr(value, "code", None))
    if status is not None and hasattr(value, "read"):
        body = value.read()
        headers = getattr(value, "headers", ())
        return HTTPTransportResponse(status=int(status), body=body, headers=tuple(headers.items()) if hasattr(headers, "items") else ())
    raise GeoIPLookupError("transport returned an unsupported response shape")


#: RFC 1928 §6 reply codes, so a refusal says what the proxy said rather than
#: "the connection failed".
_SOCKS5_REPLIES = {
    0x01: "general SOCKS server failure",
    0x02: "connection not allowed by ruleset",
    0x03: "network unreachable",
    0x04: "host unreachable",
    0x05: "connection refused",
    0x06: "TTL expired",
    0x07: "command not supported",
    0x08: "address type not supported",
}


def _recv_exactly(sock: socket.socket, count: int) -> bytes:
    chunks = []
    remaining = count
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise GeoIPLookupError("SOCKS5 proxy closed the connection mid-handshake")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _socks5_target(scheme: str, host: str) -> Tuple[int, bytes]:
    """``(ATYP, encoded address)`` for the CONNECT request.

    ``socks5h`` hands the name to the proxy, which is the whole point of the
    ``h``: the DNS query leaves the proxy's network rather than this one.
    ``socks5`` resolves here, as the scheme says. An address literal needs
    neither and is sent as one.
    """

    literal = host.strip("[]")
    try:
        address = ipaddress.ip_address(literal)
    except ValueError:
        address = None
    if address is None and scheme == "socks5":
        try:
            resolved = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise GeoIPLookupError(
                f"cannot resolve {host} for the SOCKS5 proxy: {exc}") from None
        address = ipaddress.ip_address(resolved[0][4][0])
    if address is not None:
        return (0x01, address.packed) if address.version == 4 else (0x04, address.packed)
    try:
        encoded = host.encode("idna")
    except UnicodeError:
        encoded = host.encode("utf-8")
    if not 0 < len(encoded) <= 255:
        raise GeoIPConfigurationError("SOCKS5 host name must be 1 to 255 bytes")
    return 0x03, bytes([len(encoded)]) + encoded


def _socks5_connect(proxy: ProxyConfig, host: str, port: int,
                    timeout: float) -> socket.socket:
    """One RFC 1928 CONNECT, returning the tunnelled socket.

    Sixty lines of stdlib rather than a PySocks dependency. A residential
    SOCKS5 exit is the ordinary shape for this package's users, so the proxy
    family that matters most cannot be the one that needs a second install.
    """

    try:
        sock = socket.create_connection((proxy.host, proxy.port), timeout=timeout)
    except OSError as exc:
        raise GeoIPLookupError(
            f"cannot reach the SOCKS5 proxy {proxy.redacted_url}: {exc}") from None
    try:
        sock.settimeout(timeout)
        # Offer username/password only when there is one to send; a proxy that
        # needs none must not be told this client would authenticate.
        methods = b"\x02\x00" if proxy.username is not None else b"\x00"
        sock.sendall(b"\x05" + bytes([len(methods)]) + methods)
        version, method = _recv_exactly(sock, 2)
        if version != 0x05:
            raise GeoIPLookupError("SOCKS5 proxy answered with another protocol version")
        if method == 0xFF:
            raise GeoIPLookupError(
                "SOCKS5 proxy rejected every authentication method offered")
        if method == 0x02:
            if proxy.username is None:
                raise GeoIPLookupError(
                    "SOCKS5 proxy demanded credentials and none were configured")
            # RFC 1929: one round trip, and it is cleartext on the wire to the
            # proxy. That is the protocol, not a choice made here.
            user = proxy.username.encode("utf-8")
            password = (proxy.password or "").encode("utf-8")
            if len(user) > 255 or len(password) > 255:
                raise GeoIPConfigurationError(
                    "SOCKS5 username and password must each be at most 255 bytes")
            sock.sendall(b"\x01" + bytes([len(user)]) + user
                         + bytes([len(password)]) + password)
            auth_version, status = _recv_exactly(sock, 2)
            if auth_version != 0x01 or status != 0x00:
                raise GeoIPLookupError("SOCKS5 proxy rejected the supplied credentials")
        elif method != 0x00:
            raise GeoIPLookupError(
                f"SOCKS5 proxy chose unsupported authentication method {method}")
        address_type, address = _socks5_target(proxy.scheme, host)
        sock.sendall(b"\x05\x01\x00" + bytes([address_type]) + address
                     + struct.pack("!H", port))
        version, reply, _reserved, bound_type = _recv_exactly(sock, 4)
        if version != 0x05:
            raise GeoIPLookupError("SOCKS5 proxy answered with another protocol version")
        if reply != 0x00:
            raise GeoIPLookupError(
                "SOCKS5 proxy refused the connection: "
                + _SOCKS5_REPLIES.get(reply, f"reply code {reply}"))
        # The bound address is unused, but it has to leave the stream before
        # the tunnel carries anything else.
        if bound_type == 0x01:
            _recv_exactly(sock, 4 + 2)
        elif bound_type == 0x04:
            _recv_exactly(sock, 16 + 2)
        elif bound_type == 0x03:
            _recv_exactly(sock, _recv_exactly(sock, 1)[0] + 2)
        else:
            raise GeoIPLookupError("SOCKS5 proxy answered with an unknown address type")
        return sock
    except BaseException:
        sock.close()
        raise


def _socks5_transport(request: HTTPTransportRequest) -> HTTPTransportResponse:
    """One GET through a SOCKS5 tunnel, TLS-wrapped for an https endpoint."""

    parts = urllib.parse.urlsplit(request.url)
    secure = parts.scheme == "https"
    host = parts.hostname or ""
    port = parts.port or (443 if secure else 80)
    target = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
    sock = _socks5_connect(request.proxy, host, port, request.timeout)
    try:
        if secure:
            # Certificates are verified against the host the endpoint names, so
            # the proxy carries the bytes and cannot read or forge them.
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        connection = http.client.HTTPConnection(host, port, timeout=request.timeout)
        # The socket is already connected, and already wrapped when the
        # endpoint is https, so http.client never dials: it only speaks
        # HTTP/1.1 over what it is handed. That is chunked transfer, header
        # folding and status parsing for free, none of which is worth
        # reimplementing beside a SOCKS handshake.
        connection.sock = sock
        connection.request("GET", target or "/", headers=dict(request.headers))
        response = connection.getresponse()
        return HTTPTransportResponse(
            status=int(response.status),
            body=response.read(MAX_RESPONSE_BYTES + 1),
            headers=tuple(response.getheaders()),
        )
    except ssl.SSLError as exc:
        raise GeoIPLookupError(f"TLS failed through the SOCKS5 proxy: {exc}") from None
    except (OSError, http.client.HTTPException) as exc:
        raise GeoIPLookupError(f"request failed through the SOCKS5 proxy: {exc}") from None
    finally:
        try:
            sock.close()
        except OSError:
            pass


def urllib_transport(request: HTTPTransportRequest) -> HTTPTransportResponse:
    """Perform one GET with ambient proxies disabled unless explicitly supplied."""

    if request.proxy is not None and request.proxy.scheme in _SOCKS_PROXY_SCHEMES:
        return _socks5_transport(request)
    if request.proxy is not None and not request.proxy.supports_urllib:
        raise GeoIPConfigurationError(
            f"The standard-library transport cannot use {request.proxy.scheme!r} proxies"
        )
    proxy_map = {}
    if request.proxy is not None:
        proxy_map = {"http": request.proxy.url, "https": request.proxy.url}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxy_map))
    http_request = urllib.request.Request(
        request.url,
        headers=dict(request.headers),
        method="GET",
    )
    try:
        with opener.open(http_request, timeout=request.timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            response_status = getattr(response, "status", None)
            if response_status is None:
                response_status = response.getcode()
            response_headers = getattr(response, "headers", None)
            return HTTPTransportResponse(
                status=int(response_status),
                body=body,
                headers=tuple(response_headers.items()) if response_headers else (),
            )
    except urllib.error.HTTPError as exc:
        body = exc.read(MAX_RESPONSE_BYTES + 1)
        return HTTPTransportResponse(status=int(exc.code), body=body)


def resolve_prelaunch_geoip(
    *,
    geoip: bool = True,
    proxy: Union[str, ProxyConfig, None] = None,
    locale: Optional[str] = None,
    timezone: Optional[str] = None,
    profile_locale: Optional[str] = None,
    profile_timezone: Optional[str] = None,
    transport: Optional[Callable[..., Any]] = None,
    endpoint: str = DEFAULT_ENDPOINT,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    provider: str = PROVIDER_NAME,
    provider_version: str = PROVIDER_VERSION,
    mapping_version: int = MAPPING_VERSION,
    resolver_version: int = RESOLVER_VERSION,
) -> GeoIPResult:
    """Resolve one prelaunch result using the canonical snake_case fields."""

    return GeoIPResolver(
        geoip=geoip,
        proxy=proxy,
        locale=locale,
        timezone=timezone,
        profile_locale=profile_locale,
        profile_timezone=profile_timezone,
        transport=transport,
        endpoint=endpoint,
        timeout=timeout,
        provider=provider,
        provider_version=provider_version,
        mapping_version=mapping_version,
        resolver_version=resolver_version,
    ).resolve()


# Explicit aliases for package adapters and older integration naming.
resolve_geoip = resolve_prelaunch_geoip
resolve_launch_network = resolve_prelaunch_geoip
resolve_network_localization = resolve_prelaunch_geoip




__all__ = [
    "PACKAGE_VERSION",
    "CHROMIUM_VERSION",
    "CATALOGUE_VERSION",
    "RESOLVER_VERSION",
    "PROVIDER_NAME",
    "PROVIDER_VERSION",
    "MAPPING_VERSION",
    "DEFAULT_ENDPOINT",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_TIMEOUT_SECONDS",
    "GeoIPError",
    "GeoIPConfigurationError",
    "GeoIPLookupError",
    "GeoIPTimeoutError",
    "GeoIPProviderError",
    "GeoIPResolutionError",
    "ProxyConfig",
    "parse_proxy_url",
    "parse_proxy",
    "redact_proxy_url",
    "HTTPTransportRequest",
    "HTTPTransportResponse",
    "GeoIPRequest",
    "GeoIPResponse",
    "GeoIPDiagnostics",
    "WebRTCNativeCapability",
    "GeoIPResult",
    "GeoIPResolver",
    "urllib_transport",
    "resolve_prelaunch_geoip",
    "resolve_geoip",
    "resolve_launch_network",
    "resolve_network_localization",
]


if __name__ == "__main__":
    raise SystemExit("Import this module from a package adapter; it does not run a network lookup by itself.")
