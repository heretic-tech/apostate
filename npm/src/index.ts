// @ts-nocheck
import { createHash } from "node:crypto";
import { execFile, spawn } from "node:child_process";
import {
  request as httpRequest,
} from "node:http";
import {
  request as httpsRequest,
} from "node:https";
import { isIP } from "node:net";
import {
  access,
  chmod,
  copyFile,
  cp,
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  realpath,
  rename,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { constants as fsConstants, readFileSync as readFileSyncNative } from "node:fs";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { homedir, platform as hostPlatform, arch as hostArch } from "node:os";
import { fileURLToPath } from "node:url";
import { SocksProxyAgent } from "socks-proxy-agent";
import { HttpProxyAgent } from "http-proxy-agent";
import { HttpsProxyAgent } from "https-proxy-agent";
export const PACKAGE_VERSION = "0.2.1";
export const CHROMIUM_VERSION = "152.0.7977.83";
export const CATALOGUE_VERSION = 2;
const PROFILE_SCHEMA_VERSION = 3;
const SUPPORTED_EVIDENCE = {
  "physical-ground-truth": true,
  "compatibility-capture": true,
  "catalogue-value": true,
  "native-derived": true,
  "proxy-derived": true,
  "host-inherited": true,
};

const PACKAGE_ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const ASSET_ROOT = join(PACKAGE_ROOT, "assets");
const DEFAULT_MANIFEST_PATH = join(ASSET_ROOT, "release-manifest.json");
const DEFAULT_CATALOGUE_PATH = join(ASSET_ROOT, "catalogue.json");
const EXPECTED_TARGETS = new Set([
  "linux-x64",
  "linux-arm64",
  "macos-arm64",
  "windows-x64",
]);
const DEFAULT_PROFILE_SCHEMA_PATH = join(ASSET_ROOT, "profile.schema.json");
const DEFAULT_COUNTRY_LOCALES_PATH = join(ASSET_ROOT, "country-locales.json");
const CATALOGUE_MODEL = "anchors+dispersion";
// Catalogue version 1 shipped the fourteen-family model; version 2 retired it.
const RETIRED_CATALOGUE_KEYS = ["families", "family_count", "distributions"];
const CATALOGUE_POLICY_FAMILIES = { locale: true, theme: true };
const HOST_INHERITANCE_SEED = "host";
// All six are exactly equivalent and case-insensitive in the binary. `off` is
// what users arriving from other anti-detect wrappers type.
const HOST_INHERITANCE_SEEDS = {
  host: true, off: true, false: true, "0": true, disable: true, disabled: true,
};
// docs/FINGERPRINTS.md section 7: the compositor is the browser process's and is
// the only implementation. The package does not compose; it hands the browser
// the selectors and lets the browser process compose. Measured on the shipped
// macos-arm64 artifact at 152.0.7977.83: --fingerprint=42 yields en-GB /
// Europe/London, a bare launch draws a fresh identity, --fingerprint=host
// inherits the host.
const HOST_PERSONA_MESSAGE = "host inheritance disables every layer below it, so a platform persona cannot be applied at the same time";
const EXPLICIT_PROFILE_WARNING = "explicit profile bypasses composition; its coherence and servability are the author's responsibility, not the catalogue's";
const ROTATING_SEED_WARNING = "no fingerprint seed given: the browser draws a fresh seed on every launch, so this identity does not persist. Pass fingerprint: <seed> for a stable identity";
// Every --fingerprint* switch the binary reads. Chromium silently ignores an
// unknown switch, which would leave a surface host-inherited while the operator
const FINGERPRINT_SWITCHES = {
  "--apostate-profile": true,
  "--fingerprint": true,
  "--fingerprint-anchor": true,
  "--fingerprint-device-memory": true,
  "--fingerprint-explain": true,
  "--fingerprint-gpu-renderer": true,
  "--fingerprint-gpu-vendor": true,
  "--fingerprint-hardware-concurrency": true,
  "--fingerprint-locale": true,
  "--fingerprint-platform": true,
  "--fingerprint-screen-height": true,
  "--fingerprint-screen-width": true,
  "--fingerprint-timezone": true,
  "--fingerprint-webrtc-ip": true,
  "--fingerprint-webrtc-udp": true,
};
// Longest --fingerprint value the binary accepts before exiting non-zero.
const MAX_SEED_LENGTH = 512;
// Where a published release lives, used only to build a download URL. The
// digest always comes from the manifest in this package, so a wrong base URL
// fails verification rather than installing something else.
const RELEASE_REPOSITORY = "heretic-tech/apostate";

const PERSONA_ALIASES = new Map([
  ["darwin", "macos"],
  ["mac", "macos"],
  ["macos", "macos"],
  ["mac os", "macos"],
  ["mac os x", "macos"],
  ["os x", "macos"],
  ["osx", "macos"],
  ["win", "windows"],
  ["windows", "windows"],
  ["win32", "windows"],
  ["linux", "linux"],
]);

const TARGET_ARTIFACTS = {
  "linux-x64": `apostate-${CHROMIUM_VERSION}-linux-x64.tar.zst`,
  "linux-arm64": `apostate-${CHROMIUM_VERSION}-linux-arm64.tar.zst`,
  "macos-arm64": `apostate-${CHROMIUM_VERSION}-macos-arm64.zip`,
  "windows-x64": `apostate-${CHROMIUM_VERSION}-windows-x64.zip`,
};

export class ApostateError extends Error {
  constructor(message, code = "APOSTATE_ERROR", details = {}) {
    super(message);
    this.name = "ApostateError";
    this.code = code;
    this.details = details;
  }
}

export class UnsupportedPlatformError extends ApostateError {
  constructor(platform, architecture) {
    super(
      `Apostate does not ship a binary for host ${platform}/${architecture}; supported targets are ${[...EXPECTED_TARGETS].join(", ")}.`,
      "UNSUPPORTED_PLATFORM",
      { platform, architecture },
    );
    this.name = "UnsupportedPlatformError";
  }
}

export class ProfileResolutionError extends ApostateError {
  constructor(message, details = {}, code = "PROFILE_RESOLUTION_FAILED") {
    super(message, code, details);
    this.name = "ProfileResolutionError";
  }
}

export class ManifestError extends ApostateError {
  constructor(message, details = {}) {
    super(message, "INVALID_RELEASE_MANIFEST", details);
    this.name = "ManifestError";
  }
}

export class UnpublishedArtifactError extends ManifestError {
  constructor(message = "Release manifest is unpublished; Apostate binary artifacts are not available for acquisition.", details = {}) {
    super(message, details);
    this.name = "UnpublishedArtifactError";
    this.code = "UNPUBLISHED_ARTIFACT";
  }
}

export class MissingBinaryError extends ApostateError {
  constructor(message, details = {}) {
    super(message, "BINARY_NOT_FOUND", details);
    this.name = "MissingBinaryError";
  }
}

export class BinaryIntegrityError extends ApostateError {
  constructor(message, details = {}) {
    super(message, "BINARY_HASH_MISMATCH", details);
    this.name = "BinaryIntegrityError";
  }
}

export class BinaryDownloadError extends ApostateError {
  constructor(message, details = {}) {
    super(message, "BINARY_DOWNLOAD_FAILED", details);
    this.name = "BinaryDownloadError";
  }
}

export class BinaryExtractionError extends ApostateError {
  constructor(message, details = {}) {
    super(message, "BINARY_EXTRACTION_FAILED", details);
    this.name = "BinaryExtractionError";
  }
}

export class GeoIPError extends ApostateError {
  constructor(message, details = {}) {
    super(message, "GEOIP_LOOKUP_FAILED", details);
    this.name = "GeoIPError";
  }
}

export class BrowserLaunchError extends ApostateError {
  constructor(message, details = {}) {
    super(message, "BROWSER_LAUNCH_FAILED", details);
    this.name = "BrowserLaunchError";
  }
}

export class WidevineError extends ApostateError {
  constructor(message, details = {}) {
    super(message, "WIDEVINE_NOT_FOUND", details);
    this.name = "WidevineError";
  }
}

export class UnsupportedFeatureError extends ApostateError {
  constructor(message, details = {}) {
    super(message, "UNSUPPORTED_FEATURE", details);
    this.name = "UnsupportedFeatureError";
  }
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function cloneJson(value) {
  try {
    return JSON.parse(JSON.stringify(value));
  } catch (error) {
    throw new ProfileResolutionError("Profile must contain JSON-serializable values.", {
      cause: error instanceof Error ? error.message : String(error),
    });
  }
}

function jsonAscii(value) {
  const encoded = JSON.stringify(value);
  if (encoded === undefined) return undefined;
  return encoded.replace(/[^\x00-\x7F]/g, (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`);
}

function stableValue(value) {
  if (value === null || typeof value !== "object") {
    return jsonAscii(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableValue(item)).join(",")}]`;
  }
  const keys = Object.keys(value).sort();
  return `{${keys.map((key) => `${jsonAscii(key)}:${stableValue(value[key])}`).join(",")}}`;
}

export function stableStringify(value) {
  const output = stableValue(value);
  if (output === undefined) {
    throw new TypeError("Cannot canonicalize undefined JSON");
  }
  return output;
}

function assertJsonTree(value, path = "profile") {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new ProfileResolutionError(`${path} contains a non-finite number.`);
    }
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((entry, index) => assertJsonTree(entry, `${path}[${index}]`));
    return;
  }
  if (!isObject(value)) {
    throw new ProfileResolutionError(`${path} contains an unsupported value.`);
  }
  for (const [key, entry] of Object.entries(value)) {
    if (key === "__proto__" || key === "constructor" || key === "prototype") {
      throw new ProfileResolutionError(`${path} contains a forbidden key.`);
    }
    assertJsonTree(entry, `${path}.${key}`);
  }
}

let profileSchema;

function loadProfileSchema() {
  if (profileSchema) return profileSchema;
  try {
    profileSchema = JSON.parse(readFileSyncNative(DEFAULT_PROFILE_SCHEMA_PATH, "utf8"));
  } catch (error) {
    throw new ProfileResolutionError("Package profile schema is missing or invalid.", {
      cause: error instanceof Error ? error.message : String(error),
    });
  }
  return profileSchema;
}

function jsonTypeMatches(value, type) {
  if (Array.isArray(type)) return type.some((entry) => jsonTypeMatches(value, entry));
  if (type === "object") return isObject(value);
  if (type === "array") return Array.isArray(value);
  if (type === "string") return typeof value === "string";
  if (type === "boolean") return typeof value === "boolean";
  if (type === "integer") return typeof value === "number" && Number.isInteger(value);
  if (type === "number") return typeof value === "number" && Number.isFinite(value);
  if (type === "null") return value === null;
  return true;
}

function schemaReference(root, reference) {
  if (typeof reference !== "string" || !reference.startsWith("#/")) return null;
  let current = root;
  for (const encoded of reference.slice(2).split("/")) {
    const key = encoded.replaceAll("~1", "/").replaceAll("~0", "~");
    if (!isObject(current) || !Object.prototype.hasOwnProperty.call(current, key)) return null;
    current = current[key];
  }
  return isObject(current) ? current : null;
}

function schemaMatches(value, schema, root) {
  try {
    schemaValidate(value, schema, "$schema-match", root);
    return true;
  } catch {
    return false;
  }
}

function schemaValidate(value, schema, path, root = schema) {
  if (!schema || typeof schema !== "object") return;
  if (schema.$ref !== undefined) {
    const referenced = schemaReference(root, schema.$ref);
    if (!referenced) throw new ProfileResolutionError(`${path} has an invalid schema reference.`);
    schemaValidate(value, referenced, path, root);
  }
  if (schema.allOf) {
    for (const branch of schema.allOf) schemaValidate(value, branch, path, root);
  }
  for (const keyword of ["anyOf", "oneOf"]) {
    if (!Array.isArray(schema[keyword]) || schema[keyword].length === 0) continue;
    const matches = schema[keyword].filter((branch) => schemaMatches(value, branch, root)).length;
    const valid = keyword === "anyOf" ? matches > 0 : matches === 1;
    if (!valid) throw new ProfileResolutionError(`${path} must match ${keyword === "anyOf" ? "at least one" : "exactly one"} schema branch.`);
  }
  if (schema.not && schemaMatches(value, schema.not, root)) {
    throw new ProfileResolutionError(`${path} must not match the schema branch.`);
  }
  if (schema.type && !jsonTypeMatches(value, schema.type)) {
    throw new ProfileResolutionError(`${path} must be ${Array.isArray(schema.type) ? schema.type.join(" or ") : schema.type}.`);
  }
  if (schema.const !== undefined && stableStringify(value) !== stableStringify(schema.const)) {
    throw new ProfileResolutionError(`${path} must equal the schema constant.`);
  }
  if (schema.enum && !schema.enum.some((entry) => stableStringify(entry) === stableStringify(value))) {
    throw new ProfileResolutionError(`${path} is not an allowed value.`);
  }
  if (typeof value === "string") {
    if (schema.minLength !== undefined && value.length < schema.minLength) throw new ProfileResolutionError(`${path} is shorter than the schema minimum.`);
    if (schema.maxLength !== undefined && value.length > schema.maxLength) throw new ProfileResolutionError(`${path} is longer than the schema maximum.`);
    if (schema.pattern && !(new RegExp(schema.pattern).test(value))) throw new ProfileResolutionError(`${path} does not match the schema pattern.`);
  }
  if (typeof value === "number") {
    if (schema.minimum !== undefined && value < schema.minimum) throw new ProfileResolutionError(`${path} is below the schema minimum.`);
    if (schema.maximum !== undefined && value > schema.maximum) throw new ProfileResolutionError(`${path} is above the schema maximum.`);
    if (schema.exclusiveMinimum !== undefined && value <= schema.exclusiveMinimum) throw new ProfileResolutionError(`${path} is not above the schema minimum.`);
    if (schema.exclusiveMaximum !== undefined && value >= schema.exclusiveMaximum) throw new ProfileResolutionError(`${path} is not below the schema maximum.`);
  }
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) throw new ProfileResolutionError(`${path} has too few items.`);
    if (schema.maxItems !== undefined && value.length > schema.maxItems) throw new ProfileResolutionError(`${path} has too many items.`);
    if (schema.uniqueItems) {
      const unique = new Set(value.map((entry) => stableStringify(entry)));
      if (unique.size !== value.length) throw new ProfileResolutionError(`${path} contains duplicate items.`);
    }
    if (schema.items) value.forEach((entry, index) => schemaValidate(entry, schema.items, `${path}[${index}]`, root));
    if (schema.contains) {
      const count = value.filter((entry) => schemaMatches(entry, schema.contains, root)).length;
      if (count < (schema.minContains ?? 1) || (schema.maxContains !== undefined && count > schema.maxContains)) {
        throw new ProfileResolutionError(`${path} does not satisfy its contains constraint.`);
      }
    }
  }
  if (isObject(value)) {
    if (schema.required) {
      for (const required of schema.required) {
        if (!Object.prototype.hasOwnProperty.call(value, required)) throw new ProfileResolutionError(`${path}.${required} is required.`);
      }
    }
    if (schema.propertyNames) {
      for (const key of Object.keys(value)) schemaValidate(key, schema.propertyNames, `${path} property name`, root);
    }
    const properties = schema.properties ?? {};
    for (const [key, entry] of Object.entries(value)) {
      if (Object.prototype.hasOwnProperty.call(properties, key)) {
        schemaValidate(entry, properties[key], `${path}.${key}`, root);
      } else if (schema.additionalProperties === false) {
        throw new ProfileResolutionError(`${path}.${key} is not part of the schema.`);
      } else if (schema.additionalProperties && typeof schema.additionalProperties === "object") {
        schemaValidate(entry, schema.additionalProperties, `${path}.${key}`, root);
      }
    }
  }
  if (schema.if) {
    const branch = schemaMatches(value, schema.if, root) ? schema.then : schema.else;
    if (branch) schemaValidate(value, branch, path, root);
  }
}


export function validateProfile(profile) {
  if (!isObject(profile)) {
    throw new ProfileResolutionError("Profile must be a JSON object.");
  }
  // config/profile.schema.json is the single source of truth for accepted
  // fields: every object in it is closed, so schemaValidate rejects unknown
  // keys at every level and a second hand-kept allow-list would only drift.
  schemaValidate(profile, loadProfileSchema(), "profile");
  assertJsonTree(profile);
  if (profile.id !== undefined && (typeof profile.id !== "string" || profile.id.length === 0)) {
    throw new ProfileResolutionError("profile.id must be a non-empty string.");
  }
  if (profile.locale) {
    for (const key of ["timezone", "accept_languages"]) {
      if (profile.locale[key] !== undefined && typeof profile.locale[key] !== "string") {
        throw new ProfileResolutionError(`profile.locale.${key} must be a string.`);
      }
    }
  }
  if (profile.platform) {
    for (const key of ["name", "version", "architecture", "bitness", "model", "navigator_platform"]) {
      if (profile.platform[key] !== undefined && typeof profile.platform[key] !== "string" && typeof profile.platform[key] !== "boolean") {
        throw new ProfileResolutionError(`profile.platform.${key} must be a scalar.`);
      }
    }
  }
  return cloneJson(profile);
}

// The fingerprint-platform token for the HOST OS -- never the platform a launch
// claims. The browser's default claimed persona is host-conditional and the two
// differ on a Linux host, so a caller that wants to know what a default launch
// will present must pass this through defaultPersonaForHost(). Mirrors
// HostPlatformToken() in base/apostate/host_capability.cc.
export function hostPersona() {
  if (hostPlatform() === "darwin") return "macos";
  if (hostPlatform() === "win32") return "windows";
  if (hostPlatform() === "linux") return "linux";
  return null;
}

// The browser's default claimed persona for each host platform token, mirroring
// DefaultPersonaForHost() in base/apostate/compose.cc (patch 0102).
//
// This is a deliberate second implementation of a C++ default, scoped to the one
// path that cannot avoid one. The launch path does not use it: it sends no
// --fingerprint-platform when the user named no platform, so the browser applies
// its own default and the table has a single owner. resolveProfile() composes and
// reports a resolution locally, with no browser process to ask, so it has to know
// the same table.
//
// The mapping exists nowhere but the C++; the dispersion tables under
// resources/profiles/ key option sets on a platform and so pin the token set, but
// not the host-to-persona edge. That makes this a known coupling: test/index.test.mjs
// and python/tests/test_package.py transcribe the table independently and fail if
// either side moves alone.
export const DEFAULT_PERSONA_BY_HOST = Object.freeze({
  macos: "macos",
  windows: "windows",
  // The one default that is not the host's own OS. A Linux host is the deployment
  // target and a Windows persona is what most launches there want; the cost is the
  // Windows font set, which the browser reports as a limitation rather than hiding.
  linux: "windows",
});

// An unrecognised or empty token is returned unchanged rather than defaulted to
// "windows". That is what the C++ does: the caller then fails its own
// IsKnownPlatform() check, composes nothing and inherits the host. Mapping an
// unknown host onto a persona here would instead compose profiles for platforms
// with no corpus behind them.
export function defaultPersonaForHost(hostToken) {
  return Object.prototype.hasOwnProperty.call(DEFAULT_PERSONA_BY_HOST, hostToken)
    ? DEFAULT_PERSONA_BY_HOST[hostToken]
    : hostToken;
}

export function normalizePersona(value) {
  // An absent platform stays absent. It used to become hostPersona() here, which
  // made this normalizer a second home for a default and, after 0102, the wrong
  // one: the host's platform is not what a default launch claims.
  if (value === undefined || value === null) {
    return null;
  }
  const normalized = PERSONA_ALIASES.get(String(value).trim().toLowerCase());
  if (!normalized) {
    throw new ProfileResolutionError(`Unsupported fingerprint platform ${String(value)}; use windows, macos, or linux.`);
  }
  return normalized;
}

export function targetForHost(platform = hostPlatform(), architecture = hostArch()) {
  if (platform === "darwin" && architecture === "arm64") return "macos-arm64";
  if (platform === "linux" && architecture === "x64") return "linux-x64";
  if (platform === "linux" && architecture === "arm64") return "linux-arm64";
  if (platform === "win32" && architecture === "x64") return "windows-x64";
  throw new UnsupportedPlatformError(platform, architecture);
}

export function normalizeTarget(target) {
  if (target === undefined || target === null || target === "") {
    return targetForHost();
  }
  const value = String(target).toLowerCase();
  if (!EXPECTED_TARGETS.has(value)) {
    throw new UnsupportedPlatformError(value, "unknown");
  }
  return value;
}

function normalizedProfilePlatform(profile, source = "profile") {
  const platform = profile?.platform;
  if (platform === undefined || platform === null) return null;
  if (!isObject(platform)) throw new ProfileResolutionError(`${source}.platform must be an object.`);
  const name = platform.name;
  if (name === undefined || name === null || name === "") return null;
  if (typeof name !== "string") throw new ProfileResolutionError(`${source}.platform.name must be a string.`);
  return normalizePersona(name);
}

// `requested` arrives already normalized by normalizePersona: a token or null.
function requestedProfilePlatform(profile, requested, source) {
  const declared = normalizedProfilePlatform(profile, source);
  if (requested === null) {
    // No browser process is involved in a local resolution, so unlike the launch
    // path -- which sends no --fingerprint-platform and lets the binary apply its
    // own default -- this has to reproduce that default itself.
    return declared ?? defaultPersonaForHost(hostPersona());
  }
  if (declared !== null && declared !== requested) {
    throw new ProfileResolutionError(`${source} platform ${declared} does not match fingerprint platform ${requested}.`, {
      requested_platform: requested,
      declared_platform: declared,
    });
  }
  return requested;
}

function requireCatalogueString(value, label) {
  if (typeof value !== "string" || value.trim() === "") {
    throw new ProfileResolutionError(`${label} must be a non-empty string.`);
  }
  return value;
}

function catalogueAnchorView(anchor, position) {
  if (!isObject(anchor)) {
    throw new ProfileResolutionError(`profile catalogue anchors[${position}] must be an object.`);
  }
  const id = requireCatalogueString(anchor.id, `profile catalogue anchors[${position}].id`);
  if (!Object.prototype.hasOwnProperty.call(SUPPORTED_EVIDENCE, anchor.evidence_class)) {
    throw new ProfileResolutionError(`profile catalogue anchor ${id} evidence_class is invalid.`);
  }
  if (!Array.isArray(anchor.members) || anchor.members.length === 0) {
    throw new ProfileResolutionError(`profile catalogue anchor ${id} must list its measured members.`);
  }
  if (anchor.member_count !== undefined && anchor.member_count !== anchor.members.length) {
    throw new ProfileResolutionError(`profile catalogue anchor ${id} member_count does not match its members.`);
  }
  return {
    id,
    platform: normalizePersona(requireCatalogueString(anchor.platform, `profile catalogue anchor ${id} platform`)),
    backend: requireCatalogueString(anchor.backend, `profile catalogue anchor ${id} backend`),
    members: anchor.members.map((member, index) => requireCatalogueString(member, `profile catalogue anchor ${id} members[${index}]`)),
    rotation_status: requireCatalogueString(anchor.rotation_status, `profile catalogue anchor ${id} rotation_status`),
  };
}

function catalogueAxisView(axis, position) {
  if (!isObject(axis)) {
    throw new ProfileResolutionError(`profile catalogue axes[${position}] must be an object.`);
  }
  const label = requireCatalogueString(axis.axis, `profile catalogue axes[${position}].axis`);
  if (!Array.isArray(axis.conditioned_on)) {
    throw new ProfileResolutionError(`profile catalogue axis ${label} conditioned_on must be a list.`);
  }
  for (const count of ["option_sets", "options"]) {
    if (!Number.isInteger(axis[count]) || axis[count] < 1) {
      throw new ProfileResolutionError(`profile catalogue axis ${label} ${count} must be a positive integer.`);
    }
  }
  return {
    axis: label,
    selection: requireCatalogueString(axis.selection, `profile catalogue axis ${label} selection`),
    servability: requireCatalogueString(axis.servability, `profile catalogue axis ${label} servability`),
    conditioned_on: axis.conditioned_on.map((entry, index) => requireCatalogueString(entry, `profile catalogue axis ${label} conditioned_on[${index}]`)),
    option_sets: axis.option_sets,
    options: axis.options,
  };
}

function cataloguePolicyIds(policies, family) {
  const entries = policies[family];
  if (!Array.isArray(entries) || entries.length === 0) {
    throw new ProfileResolutionError(`profile catalogue policies.${family} must be a non-empty list.`);
  }
  return entries.map((entry, index) => {
    if (!isObject(entry)) {
      throw new ProfileResolutionError(`profile catalogue policies.${family}[${index}] must be an object.`);
    }
    return requireCatalogueString(entry.id, `profile catalogue policies.${family}[${index}].id`);
  });
}

// The catalogue describes the browser process's composition model. The package
// reads it to report the anchors, axes and policy ids it will compose from, and
// never to compose: see docs/FINGERPRINTS.md section 7.
export function loadCatalogue(path = DEFAULT_CATALOGUE_PATH) {
  const cataloguePath = resolve(path);
  let parsed;
  try {
    parsed = JSON.parse(readFileSyncNative(cataloguePath, "utf8"));
  } catch (error) {
    throw new ProfileResolutionError(`Unable to read profile catalogue ${cataloguePath}.`, {
      cause: error instanceof Error ? error.message : String(error),
    });
  }
  if (!isObject(parsed)) {
    throw new ProfileResolutionError(`Profile catalogue ${cataloguePath} must contain a JSON object.`);
  }
  for (const key of RETIRED_CATALOGUE_KEYS) {
    if (parsed[key] !== undefined) {
      throw new ProfileResolutionError(
        `profile catalogue still carries the retired ${key} key; catalogue version ${CATALOGUE_VERSION} composes from anchors and dispersion.`,
        { retired_key: key },
      );
    }
  }
  requireCatalogueString(parsed.catalogue_id, "profile catalogue catalogue_id");
  for (const [key, expected] of [
    ["catalogue_version", CATALOGUE_VERSION],
    ["profile_schema_version", PROFILE_SCHEMA_VERSION],
    ["browser_build", CHROMIUM_VERSION],
    ["model", CATALOGUE_MODEL],
  ]) {
    if (parsed[key] !== expected) {
      throw new ProfileResolutionError(`profile catalogue ${key} does not match this package.`, {
        expected,
        actual: parsed[key] ?? null,
      });
    }
  }
  if (parsed.version !== undefined && parsed.version !== CATALOGUE_VERSION) {
    throw new ProfileResolutionError("profile catalogue.version does not match catalogue_version.", {
      expected: CATALOGUE_VERSION,
      actual: parsed.version,
    });
  }
  if (!Array.isArray(parsed.anchors) || parsed.anchors.length === 0) {
    throw new ProfileResolutionError("profile catalogue anchors must be a non-empty list.");
  }
  if (!Array.isArray(parsed.axes) || parsed.axes.length === 0) {
    throw new ProfileResolutionError("profile catalogue axes must be a non-empty list.");
  }
  if (!isObject(parsed.policies)) {
    throw new ProfileResolutionError("profile catalogue policies must be an object.");
  }
  for (const family of Object.keys(parsed.policies)) {
    if (!Object.prototype.hasOwnProperty.call(CATALOGUE_POLICY_FAMILIES, family)) {
      throw new ProfileResolutionError(`profile catalogue policies.${family} is not a catalogue version ${CATALOGUE_VERSION} policy family.`);
    }
  }
  return {
    catalogue_version: parsed.catalogue_version,
    profile_schema_version: parsed.profile_schema_version,
    browser_build: parsed.browser_build,
    model: parsed.model,
    anchors: parsed.anchors.map(catalogueAnchorView),
    axes: parsed.axes.map(catalogueAxisView),
    policies: {
      locale: cataloguePolicyIds(parsed.policies, "locale"),
      theme: cataloguePolicyIds(parsed.policies, "theme"),
    },
  };
}

function readProfileFile(path) {
  const absolute = resolve(path);
  let parsed;
  try {
    parsed = JSON.parse(readFileSyncNative(absolute, "utf8"));
  } catch (error) {
    throw new ProfileResolutionError(`Unable to read profile file ${absolute}.`, {
      cause: error instanceof Error ? error.message : String(error),
    });
  }
  return validateProfile(parsed);
}

function looksLikePath(value) {
  return value.endsWith(".json") || value.includes("/") || value.includes("\\") || value.startsWith(".") || isAbsolute(value);
}

function normalizeSeed(seed) {
  if (typeof seed === "number") {
    if (!Number.isSafeInteger(seed) || seed < 0) {
      throw new ProfileResolutionError("fingerprint must be a non-negative safe integer or a stable non-empty string.");
    }
    return seed;
  }
  if (typeof seed === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(seed)) return seed;
  throw new ProfileResolutionError("fingerprint must be a non-negative safe integer or a stable non-empty string.");
}

function profileIdentity(profileId, fingerprint, persona) {
  const identity = {
    profile_id: profileId,
    fingerprint: fingerprint ?? null,
    platform: persona,
    catalogue_version: CATALOGUE_VERSION,
    browser_build: CHROMIUM_VERSION,
  };
  return createHash("sha256").update(stableStringify(identity), "utf8").digest("hex");
}

function explicitProfileResult(validated, source, fingerprint, persona) {
  const profileId = validated.id ?? null;
  return {
    profile: validated,
    source,
    profileId,
    platform: persona,
    identity: profileIdentity(profileId, fingerprint, persona),
    catalogueVersion: CATALOGUE_VERSION,
    chromiumVersion: CHROMIUM_VERSION,
    warnings: [EXPLICIT_PROFILE_WARNING],
  };
}

export function resolveProfile(options = {}) {
  if (!isObject(options)) throw new ProfileResolutionError("Launch options must be an object.");
  // Normalized once here, so every path below sees a canonical token or null.
  // This used to be re-derived per branch, and toCanonicalLaunchConfig kept the
  // raw string, which is how `win32` reached argv unnormalized.
  const requestedPlatform = normalizePersona(options.fingerprintPlatform ?? options.fingerprint_platform ?? null);
  const explicitPath = options.profilePath ?? options.profile_file ?? options.profileFile;
  if (explicitPath !== undefined && explicitPath !== null) {
    const profile = readProfileFile(String(explicitPath));
    const persona = requestedProfilePlatform(profile, requestedPlatform, "Explicit profile");
    return explicitProfileResult(profile, "explicit-file", options.fingerprint, persona);
  }

  const explicitProfile = options.profile;
  if (isObject(explicitProfile)) {
    const profile = validateProfile(explicitProfile);
    const persona = requestedProfilePlatform(profile, requestedPlatform, "Explicit profile");
    return explicitProfileResult(profile, "explicit-profile", options.fingerprint, persona);
  }
  if (typeof explicitProfile === "string" && looksLikePath(explicitProfile)) {
    const profile = readProfileFile(explicitProfile);
    const persona = requestedProfilePlatform(profile, requestedPlatform, "Explicit profile");
    return explicitProfileResult(profile, "explicit-file", options.fingerprint, persona);
  }

  const requestedId = options.profileId ?? options.profile_id ?? (typeof explicitProfile === "string" ? explicitProfile : undefined);
  if (requestedId !== undefined && requestedId !== null && requestedId !== "") {
    throw new ProfileResolutionError(
      `catalogue profile ids were retired with catalogue version 1; catalogue version ${CATALOGUE_VERSION} composes a profile from anchors and dispersion instead of offering families, so there is no catalogue profile named ${String(requestedId)} to resolve`,
      { requested_profile_id: String(requestedId), model: CATALOGUE_MODEL },
      "APOSTATE_CATALOGUE_PROFILE_IDS_RETIRED",
    );
  }

  const fingerprint = options.fingerprint;
  const hasFingerprint = fingerprint !== undefined && fingerprint !== null && fingerprint !== "";
  if (hasFingerprint) normalizeSeed(fingerprint);
  if (hasFingerprint && HOST_INHERITANCE_SEEDS[String(fingerprint).trim().toLowerCase()] === true) {
    if (requestedPlatform !== null) {
      // The binary refuses this combination too. Under host inheritance nothing
      // is composed, so a persona cannot be honoured, and quietly presenting
      // the operator's real machine when they asked for a Windows desktop is
      // the worst outcome available.
      throw new ProfileResolutionError(HOST_PERSONA_MESSAGE, { fingerprint_platform: requestedPlatform }, "APOSTATE_HOST_INHERITANCE_PERSONA");
    }
    return {
      profile: null,
      source: "host-inherited",
      profileId: "host-inherited",
      // The host's own platform, deliberately not the 0102 default table:
      // nothing is composed on this path, so the platform the page sees is the
      // host's by definition. Reporting a persona here would describe a
      // composition that does not happen.
      platform: hostPersona(),
      identity: null,
      catalogueVersion: CATALOGUE_VERSION,
      chromiumVersion: CHROMIUM_VERSION,
      warnings: [],
    };
  }
  // A seed, a persona, or no selector at all: the browser process composes.
  // The package sends only the selectors and no profile envelope, because an
  // envelope outranks the seed and would silently suppress composition.
  return {
    profile: null,
    source: "native-composed",
    profileId: "native-composed",
    // Reported, not sent. The launch path emits no --fingerprint-platform when
    // the user named none, so the browser applies its own default; this has to
    // reproduce that default to report it truthfully. See defaultPersonaForHost.
    platform: requestedPlatform ?? defaultPersonaForHost(hostPersona()),
    identity: null,
    catalogueVersion: CATALOGUE_VERSION,
    chromiumVersion: CHROMIUM_VERSION,
    warnings: hasFingerprint ? [] : [ROTATING_SEED_WARNING],
  };
}

function checkFingerprintSwitches(args) {
  for (const item of args) {
    if (typeof item !== "string") continue;
    // --proxy-server is the package's to emit, because a proxy URL has to be
    // split: the endpoint goes on the command line and the credential travels
    // in the profile envelope. One passed here used to be filtered out and
    // dropped without a word, so a caller who configured their proxy this way
    // launched with no proxy at all and nothing said so. The browser accepts a
    // credential in that switch now, which makes this the syntax people will
    // reach for, so it is named rather than swallowed.
    if (item === "--proxy-server" || item.startsWith("--proxy-server=")) {
      throw new ProfileResolutionError(
        "--proxy-server cannot be passed in args; this package emits it itself. Pass proxy: \"socks5://user:pass@host:port\" instead, which sends the endpoint on the command line and the credential in the launch envelope. Passing it here used to be silently ignored, so a proxy configured this way was never applied.",
        { switch: "--proxy-server" },
        "APOSTATE_PROXY_SWITCH_IN_ARGS",
      );
    }
    if (!item.startsWith("--fingerprint")) continue;
    const name = item.split("=", 1)[0];
    if (FINGERPRINT_SWITCHES[name] !== true) {
      throw new ProfileResolutionError(
        `${name} is not a switch this browser reads; Chromium would ignore it and leave that surface host-inherited. Supported: ${Object.keys(FINGERPRINT_SWITCHES).sort().join(", ")}`,
        { switch: name },
        "APOSTATE_UNKNOWN_FINGERPRINT_SWITCH",
      );
    }
  }
}


function normalizeProxy(proxy) {
  if (proxy === undefined || proxy === null || proxy === "") return null;
  if (typeof proxy === "object") {
    const server = proxy.server ?? proxy.proxyServer ?? proxy.proxy_server;
    if (typeof server !== "string" || server.length === 0) {
      throw new TypeError("proxy.server must be a non-empty URL.");
    }
    let parsed;
    try {
      parsed = new URL(server);
    } catch {
      throw new TypeError("proxy.server must be a valid URL.");
    }
    if (proxy.username !== undefined) parsed.username = String(proxy.username);
    if (proxy.password !== undefined) parsed.password = String(proxy.password);
    if (!["http:", "https:", "socks4:", "socks5:"].includes(parsed.protocol)) {
      throw new TypeError("proxy.server must use http, https, socks4, or socks5.");
    }
    return parsed.toString();
  }
  if (typeof proxy !== "string") throw new TypeError("proxy must be a URL string or an object with server.");
  try {
    const parsed = new URL(proxy);
    if (!parsed.protocol || !parsed.hostname) throw new Error("missing host");
    if (!["http:", "https:", "socks4:", "socks5:"].includes(parsed.protocol)) {
      throw new Error("unsupported scheme");
    }
    return parsed.toString();
  } catch {
    throw new TypeError("proxy must be a valid HTTP(S), SOCKS4, or SOCKS5 URL.");
  }
}

export function redactProxy(proxy) {
  if (proxy === undefined || proxy === null || proxy === "") return null;
  try {
    const parsed = new URL(typeof proxy === "string" ? proxy : normalizeProxy(proxy));
    parsed.username = "";
    parsed.password = "";
    return `${parsed.protocol}//${parsed.host}${parsed.pathname !== "/" ? parsed.pathname : ""}${parsed.search}${parsed.hash}`;
  } catch {
    return "<redacted-proxy>";
  }
}

function proxyEndpoint(proxy) {
  if (!proxy) return null;
  const parsed = new URL(proxy);
  parsed.username = "";
  parsed.password = "";
  return `${parsed.protocol}//${parsed.host}${parsed.pathname !== "/" ? parsed.pathname : ""}${parsed.search}${parsed.hash}`;
}

function launchProxyCredentials(proxy) {
  if (!proxy) return null;
  const parsed = new URL(proxy);
  if (!parsed.username && !parsed.password) return null;
  let username;
  let password;
  try {
    username = decodeURIComponent(parsed.username);
    password = decodeURIComponent(parsed.password);
  } catch {
    throw new TypeError("proxy credentials must be valid URL-encoded text.");
  }
  if (username.length > 4096 || password.length > 4096) {
    throw new TypeError("proxy credentials must be at most 4096 characters.");
  }
  return { username, password };
}

function sanitizeErrorMessage(message, proxy) {
  let text = String(message);
  if (proxy) {
    text = text.split(proxy).join(redactProxy(proxy));
    try {
      const parsed = new URL(proxy);
      if (parsed.username || parsed.password) {
        text = text.split(decodeURIComponent(parsed.username)).join("<redacted>");
        text = text.split(decodeURIComponent(parsed.password)).join("<redacted>");
      }
    } catch {
      // Keep the generic redaction below for malformed values.
    }
  }
  return text.replace(/(https?:\/\/)[^\s/@:]+:[^\s/@]+@/gi, "$1<redacted>@");
}

function localeLanguages(locale) {
  const value = String(locale).trim();
  if (value.includes(",")) return value;
  const base = value.split("-")[0];
  return base && base.toLowerCase() !== value.toLowerCase() ? `${value},${base}` : value;
}

function profileLocale(profile) {
  return {
    locale: profile?.locale?.accept_languages?.split(",")[0]?.trim() || null,
    timezone: profile?.locale?.timezone || null,
  };
}

function withLocale(profile, locale, timezone) {
  if (!profile && locale === null && timezone === null) return null;
  const output = profile ? cloneJson(profile) : {};
  const section = isObject(output.locale) ? output.locale : {};
  if (locale !== null && locale !== undefined) section.accept_languages = localeLanguages(locale);
  if (timezone !== null && timezone !== undefined) section.timezone = String(timezone);
  output.locale = section;
  return validateProfile(output);
}

export function toCanonicalLaunchConfig(options = {}) {
  if (!isObject(options)) throw new TypeError("Launch options must be an object.");
  if (options.humanize === true) {
    throw new UnsupportedFeatureError("humanize is not implemented; refusing to accept a no-op launch option.");
  }
  const rawFingerprint = options.fingerprint;
  if (rawFingerprint !== undefined && rawFingerprint !== null) normalizeSeed(rawFingerprint);
  const args = options.args ?? [];
  if (!Array.isArray(args) || args.some((arg) => typeof arg !== "string")) {
    throw new TypeError("args must be an array of strings.");
  }
  const userDataDir = options.userDataDir ?? options.user_data_dir ?? null;
  if (userDataDir !== null && typeof userDataDir !== "string") {
    throw new TypeError("userDataDir must be a path string.");
  }
  const locale = options.locale ?? options.fingerprintLocale ?? options.fingerprint_locale ?? null;
  const timezone = options.timezone ?? options.fingerprintTimezone ?? options.fingerprint_timezone ?? null;
  if (locale !== null && typeof locale !== "string") throw new TypeError("locale must be a string.");
  if (timezone !== null && typeof timezone !== "string") throw new TypeError("timezone must be a string.");
  const proxy = normalizeProxy(options.proxy);
  // Store the canonical token, not the raw option. Python's normalize_platform
  // has always done this; keeping the raw string here meant an alias such as
  // `win32` reached argv verbatim, and compose.cc's IsKnownPlatform() rejects
  // it and inherits the host on every axis -- a silent full deanonymisation
  // from a spelling the Python package accepts. Null stays null, so the switch
  // stays absent and the browser applies its own host-conditional default.
  const fingerprintPlatform = normalizePersona(options.fingerprintPlatform ?? options.fingerprint_platform ?? null);
  return {
    fingerprint: rawFingerprint ?? null,
    fingerprint_platform: fingerprintPlatform,
    profile: null,
    locale,
    timezone,
    geoip: options.geoip !== false,
    proxy,
    headless: options.headless !== false,
    user_data_dir: userDataDir,
    args: [...args],
  };
}

function proxyAgentForGeoip(target, proxy) {
  if (!proxy) return undefined;
  const proxyUrl = new URL(proxy);
  if (proxyUrl.protocol === "socks4:" || proxyUrl.protocol === "socks5:") {
    return new SocksProxyAgent(proxy);
  }
  if (proxyUrl.protocol !== "http:" && proxyUrl.protocol !== "https:") {
    throw new GeoIPError(`Unsupported GeoIP proxy scheme ${proxyUrl.protocol}.`, {
      proxy: redactProxy(proxy),
    });
  }
  if (proxyUrl.protocol === "https:" || target.protocol === "https:") {
    return new HttpsProxyAgent(proxy);
  }
  return new HttpProxyAgent(proxy);
}

function requestGeoipJson(url, proxy, signal) {
  const target = new URL(url);
  if (target.protocol !== "http:" && target.protocol !== "https:") {
    throw new GeoIPError(`GeoIP URL must use HTTP or HTTPS, got ${target.protocol}.`);
  }
  const requester = target.protocol === "https:" ? httpsRequest : httpRequest;
  const agent = proxyAgentForGeoip(target, proxy);
  return new Promise((resolveResponse, rejectResponse) => {
    let settled = false;
    let request;
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      signal?.removeEventListener("abort", abort);
      callback(value);
    };
    const abort = () => {
      request?.destroy(new Error("GeoIP request aborted."));
    };
    if (signal?.aborted) {
      finish(rejectResponse, Object.assign(new Error("GeoIP request aborted."), { name: "AbortError" }));
      return;
    }
    try {
      request = requester(target, {
        agent,
        headers: {
          accept: "application/json",
          "user-agent": `apostate-node/${PACKAGE_VERSION}`,
        },
        signal,
      }, (response) => {
        let size = 0;
        const chunks = [];
        response.setEncoding("utf8");
        response.on("data", (chunk) => {
          size += Buffer.byteLength(chunk);
          if (size > 1024 * 1024) {
            request.destroy(new Error("GeoIP response exceeded 1 MiB."));
            return;
          }
          chunks.push(chunk);
        });
        response.on("end", () => {
          const body = chunks.join("");
          if (response.statusCode < 200 || response.statusCode >= 300) {
            finish(rejectResponse, new Error(`GeoIP endpoint returned HTTP ${response.statusCode}.`));
            return;
          }
          try {
            finish(resolveResponse, JSON.parse(body));
          } catch {
            finish(rejectResponse, new Error("GeoIP endpoint returned invalid JSON."));
          }
        });
        response.on("error", (error) => finish(rejectResponse, error));
      });
      request.once("error", (error) => {
        if (error?.name === "AbortError" || signal?.aborted) {
          finish(rejectResponse, Object.assign(new Error("GeoIP request aborted."), { name: "AbortError" }));
        } else {
          finish(rejectResponse, error);
        }
      });
      signal?.addEventListener("abort", abort, { once: true });
      request.end();
    } catch (error) {
      finish(rejectResponse, error);
    }
  });
}

async function defaultGeoipLookup(url, proxy, signal) {
  if (url) {
    return requestGeoipJson(url, proxy, signal);
  }

  // Cascading fallbacks for more robust lookups, preferring free, reliable endpoints
  const endpoints = [
    "http://ip-api.com/json/",
    "https://ipapi.co/json/",
    "https://freeipapi.com/api/json"
  ];

  let lastError;
  for (const endpoint of endpoints) {
    try {
      const result = await requestGeoipJson(endpoint, proxy, signal);
      // Ensure API didn't return a custom error state inside 200 OK
      if (result && result.status === 'fail') {
        throw new Error(`API returned fail: ${result.message}`);
      }
      if (result && typeof result === 'object') {
        return result;
      }
    } catch (error) {
      lastError = error;
      // Continue to try the next endpoint in the cascade
    }
  }

  throw lastError || new Error("All default GeoIP endpoints failed.");
}

// Country -> locale, read from the asset scripts/generate-country-locales.py
// derives from CLDR territoryInfo and Chromium's own kAcceptLanguageList, and
// shipped byte-identical to the Python package and resources/. It replaces a
// hand-written table of 45 countries that left most of the world unresolved:
// a Malaysian exit got no locale at all, and "no locale" means the host's own
// is served from a foreign IP, which is the leak the lookup exists to close.
//
// The table covers all 257 territories CLDR knows, so a payload carrying a
// country always derives a locale. Read at import: a package whose asset is
// missing cannot answer the question at all, and finding that out at the
// moment of a launch is worse than finding it out at the import.
const GEOIP_COUNTRY_LOCALES = (() => {
  try {
    const parsed = JSON.parse(readFileSyncNative(DEFAULT_COUNTRY_LOCALES_PATH, "utf8"));
    if (!isObject(parsed) || !isObject(parsed.locales)) throw new Error("no locales table");
    return Object.freeze(parsed.locales);
  } catch (error) {
    throw new ApostateError(`Package country-locale table ${DEFAULT_COUNTRY_LOCALES_PATH} is missing or invalid.`, "INVALID_PACKAGE_ASSET", {
      cause: error instanceof Error ? error.message : String(error),
    });
  }
})();

const GEOIP_TIMEZONE_PATTERN = /^[A-Za-z][A-Za-z0-9._+-]*(?:\/[A-Za-z0-9._+-]+)+$/;

// scripts/geoip.py::_valid_provider_timezone. Providers answer in three shapes:
// an IANA identifier, a nested object (`{"id": "Europe/Berlin"}`), and a bare
// UTC offset (`"+02:00"`, which freeipapi returns). Only an identifier can
// drive --fingerprint-timezone, so anything else is unresolved rather than
// patched up into something that looks like one.
function geoipTimezone(value) {
  const candidate = isObject(value) ? (value.id ?? value.name ?? value.timezone) : value;
  if (typeof candidate !== "string") return null;
  const trimmed = candidate.trim();
  if (!trimmed || trimmed.length > 128) return null;
  if (trimmed.toUpperCase() === "UTC" || trimmed.toUpperCase() === "GMT") return trimmed;
  return GEOIP_TIMEZONE_PATTERN.test(trimmed) ? trimmed : null;
}

function geoipLocale(result) {
  const direct = result.locale ?? result.language;
  if (typeof direct === "string" && direct.trim()) return direct.trim();
  const languages = Array.isArray(result.languages) ? result.languages[0] : result.languages;
  if (typeof languages === "string" && languages.trim()) {
    const first = languages.split(",")[0].trim();
    if (first) return first;
  }
  // `country` last and behind the two-letter test: ip-api answers it with
  // "Malaysia" while ipapi.co answers it with "MY", and only one of those is
  // a key into the table.
  const country = result.country_code ?? result.countryCode ?? result.country;
  if (typeof country === "string" && /^[A-Za-z]{2}$/.test(country.trim())) {
    return GEOIP_COUNTRY_LOCALES[country.trim().toUpperCase()] ?? null;
  }
  return null;
}

// A successful lookup may omit a field. That field comes back null and stays
// unresolved; it is never filled with en-US or UTC. A resolver that answers
// with something structurally wrong throws, and prepareLaunch's handler turns
// that into the same warning a network failure produces.
function validateGeoipResult(result) {
  if (!isObject(result)) throw new GeoIPError("GeoIP resolver must return an object.");
  const ip = result.ip ?? result.query ?? result.ipAddress ?? result.ip_address ?? null;
  if (ip !== null && (typeof ip !== "string" || isIP(ip) === 0)) throw new GeoIPError("GeoIP resolver returned an invalid IP address.");
  return { locale: geoipLocale(result), timezone: geoipTimezone(result.timezone ?? result.time_zone ?? result.timeZone), ip };
}

// The environment variables that decide Chromium's application locale, and
// through it ICU's default locale and every Intl constructor's. All four,
// because each moves it on its own: leaving one at the operator's value lets
// the host decide through a variable nobody wrote.
const LOCALE_ENV_VARS = ["LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"];

// What a composed launch that resolved no locale gets. Not the host's, and not
// nothing: absence of a resolved locale has to mean a defined default, or the
// served locale becomes a property of the operator's shell. Mirrors
// COMPOSED_DEFAULT_LOCALE in the Python package.
const COMPOSED_DEFAULT_LOCALE = "en-US";

// `de-DE` -> `de_DE.UTF-8`, for the three LC_* variables that expect it. A tag
// with no region stays region-free rather than acquiring an invented one. If
// the host has not generated the named locale, setlocale falls back to C and
// LANGUAGE -- which takes the tag as written and needs nothing generated --
// still decides the application locale. Either way the host's own value is
// gone, which is the point of writing these at all.
function posixLocale(tag) {
  const [language, region] = tag.split("-");
  return `${region ? `${language}_${region}` : language}.UTF-8`;
}

// The locale environment a launch is given, or nothing for host mode.
//
// Only host inheritance inherits the host's locale environment, because only
// there is the host the thing being presented. A composed persona gets these
// written explicitly, and the reason is a leak rather than tidiness: an
// operator in Bangkok with LANG=th_TH.UTF-8, composing a Windows persona
// through a Mexican exit, would otherwise serve Thai language preferences and
// Thai date and number formatting from a Mexican IP under a synthetic Windows
// identity. That is the host showing through a composed profile.
//
// Chromium resolves its application locale from these, sets ICU's default
// locale from that, and every Intl constructor resolves its own default
// against ICU's (v8/src/execution/isolate.cc:8123). So this is the only lever
// that moves Intl.DateTimeFormat, Intl.NumberFormat, Intl.Collator and
// toLocaleString together with navigator.languages; --lang moves none of them,
// measured on stock Chrome as well as on ours. It has no effect on an artifact
// that ships one locale pak -- see docs/FINGERPRINTS.md section 8 and
// scripts/package-artifact.sh, which now ships the full set.
function localeEnvironment(config, resolution) {
  const seed = config.fingerprint;
  if (seed !== null && seed !== undefined
      && HOST_INHERITANCE_SEEDS[String(seed).trim().toLowerCase()] === true) {
    return {};
  }
  const resolved = resolution?.locale ?? config.locale ?? COMPOSED_DEFAULT_LOCALE;
  const tag = String(resolved).split(",")[0].trim() || COMPOSED_DEFAULT_LOCALE;
  const posix = posixLocale(tag);
  const out = {};
  for (const name of LOCALE_ENV_VARS) out[name] = name === "LANGUAGE" ? tag : posix;
  return out;
}

async function prepareLaunch(options = {}) {
  const canonical = toCanonicalLaunchConfig(options);
  const resolution = resolveProfile(options);
  let profile = resolution.profile;
  const inheritedLocale = profileLocale(profile);
  // Captured before the lookup: a field the caller set explicitly is not one
  // GeoIP was asked for, so an unresolved one is not worth reporting.
  const askedForLocale = canonical.locale === null;
  const askedForTimezone = canonical.timezone === null;
  const geoipWarnings = [];
  let geoipResult = null;

  if (canonical.geoip && (canonical.locale === null || canonical.timezone === null || (canonical.proxy !== null && !hasSwitch(canonical.args, "--fingerprint-webrtc-ip")))) {
    const controller = new AbortController();
    const timeoutMs = Number.isFinite(options.geoipTimeoutMs) ? Math.max(1, options.geoipTimeoutMs) : 10000;
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const resolver = options.geoipResolver ?? ((context) => defaultGeoipLookup(options.geoipUrl, context.proxy, context.signal));
      geoipResult = validateGeoipResult(await resolver({
        proxy: canonical.proxy,
        proxy_redacted: redactProxy(canonical.proxy),
        signal: controller.signal,
      }));
    } catch (error) {
      // What stood here was "PRACTICAL FIX: Revert to fallback instead of
      // crashing the entire browser launch", with a second block below reading
      // "Hard fallback just in case to ensure we never crash". That trade was
      // deliberate and its concern was real -- raising would fail a launch over
      // an unreachable lookup -- so it is recorded rather than deleted. It is
      // no longer a trade, because the third option did not exist when it was
      // made: the resolved locale used to travel inside an --apostate-profile
      // envelope, where "send nothing" and "send en-US/UTC" were the same
      // thing, since an envelope suppresses composition either way. It now
      // travels as --fingerprint-locale/--fingerprint-timezone, so sending no
      // override is a real state: the browser's own precedence settles the
      // surface, and since patch 0111 that means the host's own zone and
      // language list. It used to mean a pair drawn from a four-entry
      // catalogue pool, which was described here as "coherent with the rest of
      // that identity by construction" -- true of the identity and false of
      // the network, because a drawn zone cannot correlate with an exit IP the
      // draw never saw. Inventing en-US/UTC was worse again for the same
      // reason. The host's zone matches a direct egress and is at worst wrong
      // the way a traveller's is. The launch still does not crash. Do not
      // restore the fallback.
      const reason = error?.name === "AbortError"
        ? `timed out after ${timeoutMs} ms`
        : sanitizeErrorMessage(error?.message ?? error, canonical.proxy);
      geoipWarnings.push(
        `GeoIP lookup failed for ${redactProxy(canonical.proxy) ?? "the direct network"}: ${reason}. `
        + "No locale or timezone override is sent and none is invented, so the launch keeps the "
        + "host's own locale and timezone. Behind a proxy that is the host's and not the exit's. "
        + "Pass locale and timezone explicitly to guarantee a match.",
      );
      console.warn(`\x1b[33m[Apostate] ${geoipWarnings[geoipWarnings.length - 1]}\x1b[0m`);
    } finally {
      clearTimeout(timeout);
    }
  }

  canonical.locale = canonical.locale ?? geoipResult?.locale ?? inheritedLocale.locale ?? null;
  canonical.timezone = canonical.timezone ?? geoipResult?.timezone ?? inheritedLocale.timezone ?? null;
  canonical.webrtc_ip = canonical.proxy !== null && !hasSwitch(canonical.args, "--fingerprint-webrtc-ip")
    ? geoipResult?.ip ?? null
    : null;

  // A lookup that answered without one of the two fields is reported for the
  // same reason a failed one is, and treated the same way: the field stays
  // unresolved. This is where the removed hard fallback used to re-invent
  // en-US/UTC unconditionally, independently of the handler above, so a
  // resolver returning a locale and no timezone got an invented UTC even
  // though nothing had failed.
  if (geoipResult !== null) {
    // Locale and timezone are reported apart because they now fail for
    // different reasons. A locale is DERIVED from the country through a table
    // that covers every territory CLDR knows, so it can only come back
    // unresolved when the provider named no country at all -- saying "the
    // lookup resolved no locale" of a Malaysian exit sent people looking for
    // a locale the provider was never asked for.
    if (askedForLocale && geoipResult.locale === null) {
      geoipWarnings.push(
        "the GeoIP lookup returned no country, so no locale is derived; the host's own is "
        + "served for that field. Pass locale explicitly to guarantee a match.",
      );
      console.warn(`\x1b[33m[Apostate] ${geoipWarnings[geoipWarnings.length - 1]}\x1b[0m`);
    }
    if (askedForTimezone && geoipResult.timezone === null) {
      geoipWarnings.push(
        "the GeoIP lookup resolved no timezone. None is invented, so the "
        + "host's own is served for that field; pass it explicitly to guarantee a match.",
      );
      console.warn(`\x1b[33m[Apostate] ${geoipWarnings[geoipWarnings.length - 1]}\x1b[0m`);
    }
  }

  // A locale-only envelope is not a cheap way to force a locale. The browser's
  // InstallComposedProfile() returns early whenever --apostate-profile carries
  // device content, and base/apostate/profile.cc leaves absent fields absent by
  // design, so an envelope carrying nothing but a locale means composition
  // never runs and every other axis -- GPU identity, capability cluster, cores,
  // memory, panel, timezone, fonts, media topology, voices -- silently falls
  // back to the host while the seed is ignored. On the composing path the
  // locale therefore leaves as 0085's per-field override switches, emitted by
  // buildLaunchArguments. The other paths compose nothing either way, so there
  // an envelope suppresses nothing and is the only carrier available.
  const composesNatively = resolution.profileId === "native-composed";
  profile = composesNatively ? profile : withLocale(profile, canonical.locale, canonical.timezone);
  canonical.profile = profile;
  // A warning for the credentials-only envelope stood here, telling the caller
  // that authenticating to a proxy cost them the composed fingerprint on every
  // axis. It was sanctioned as an interim while that was true. It no longer is:
  // the loader stops treating a payload that claims no device as a composed
  // profile, composes normally and attaches the credentials to the result. A
  // warning nobody should act on teaches callers to ignore the ones that
  // matter, so it is retired rather than re-worded. Do not re-add it; if the
  // credentials path regresses, the regression is in the loader.
  const warnings = [...(resolution.warnings ?? []), ...geoipWarnings];
  return {
    config: canonical,
    resolution,
    diagnostics: {
      proxy: redactProxy(canonical.proxy),
      // "explicit" means no lookup was needed, so a lookup that ran and failed
      // cannot borrow that word: it reports "unresolved" instead. Diagnostics
      // only -- nothing here is page-visible.
      geoip: !canonical.geoip ? "disabled"
        : geoipResult ? "resolved" : geoipWarnings.length > 0 ? "unresolved" : "explicit",
      profile_source: resolution.source,
      profile_id: resolution.profileId,
      profile_identity: resolution.identity,
      warnings,
      catalogue_version: CATALOGUE_VERSION,
      chromium_version: CHROMIUM_VERSION,
    },
  };
}

export async function resolveLaunchConfig(options = {}) {
  return (await prepareLaunch(options)).config;
}

function hasSwitch(args, name) {
  return args.some((arg) => arg === name || arg.startsWith(`${name}=`));
}

function stripSourceCapture(value) {
  if (Array.isArray(value)) return value.map((entry) => stripSourceCapture(entry));
  if (!isObject(value)) return value;
  const output = {};
  for (const [key, entry] of Object.entries(value)) {
    if (key !== "source_capture") output[key] = stripSourceCapture(entry);
  }
  return output;
}

function nativeProfilePayload(profile) {
  return validateProfile(stripSourceCapture(profile ?? {}));
}
// Profile ids the resolver uses for the two native-composition paths. Both
// deliver their selection through --fingerprint; anything else is an explicit
// profile delivered through --apostate-profile.
const NATIVE_SELECTION = { "host-inherited": true, "native-composed": true };

// `driverOwnsProfile` omits --user-data-dir because both Playwright and
// Puppeteer take the profile directory as an option and emit the switch
// themselves; passing it in argv too hands the browser the same switch twice.
// launchProcess() spawns the binary directly, so there it must be emitted.
// The Python package has always done this via `_native_args(persistent=True)`;
// this branch was the inconsistency between the two implementations.
function buildLaunchArguments(config, resolution, { driverOwnsProfile = false } = {}) {
  checkFingerprintSwitches(config.args);
  // --proxy-server is not filtered here any more: checkFingerprintSwitches
  // above refuses one rather than dropping it, so nothing reaches this point
  // carrying it.
  const args = config.args.filter((arg) => !arg.startsWith("--apostate-profile=") && !arg.startsWith("--user-data-dir="));
  // Captured before anything is pushed: from here on `args` also holds the
  // package's own selectors, so re-asking would see those instead of the user's.
  const userSuppliedSeed = hasSwitch(args, "--fingerprint");
  if (NATIVE_SELECTION[resolution?.profileId] === true && !userSuppliedSeed) {
    // The compositor is the browser process's. The package hands it the
    // selectors; it draws a fresh seed itself when none is given.
    if (config.fingerprint !== null && config.fingerprint !== undefined) args.push(`--fingerprint=${config.fingerprint}`);
    if (config.fingerprint_platform !== null && config.fingerprint_platform !== undefined) args.push(`--fingerprint-platform=${config.fingerprint_platform}`);
  }

  if (resolution?.profileId === "native-composed") {
    // Locale and timezone ride 0085's per-field override switches, never a
    // profile envelope. An envelope describing a device -- even one describing
    // only a locale -- makes the browser's InstallComposedProfile() return
    // early, so nothing is composed, the seed above is silently ignored, and
    // every axis the envelope omits falls back to the host. An override instead
    // narrows the draw inside the composed profile, which is what this path
    // wants.
    //
    // Host mode does not outrank a per-field override, it REFUSES it: 0085
    // treats a persona, a pinned anchor and a per-field override alike, and
    // combining any of them with a host spelling writes to stderr and exits
    // non-zero rather than half-applying. So these are deliberately not emitted
    // for `host-inherited` -- doing so would kill the launch, not merely be
    // ignored.
    if (config.locale !== null && !hasSwitch(args, "--fingerprint-locale")) {
      args.push(`--fingerprint-locale=${config.locale}`);
    }
    if (config.timezone !== null && !hasSwitch(args, "--fingerprint-timezone")) {
      args.push(`--fingerprint-timezone=${config.timezone}`);
    }
  }

  const credentials = launchProxyCredentials(config.proxy);
  const devicePayload = nativeProfilePayload(config.profile);
  const describesDevice = Object.keys(devicePayload).length > 0;
  if (describesDevice || credentials !== null) {
    // A device envelope and a seed are alternatives, not layers. The browser
    // cannot report the conflict -- marking an envelope partial would be a new
    // page-visible surface, and the absent-means-absent rule is what makes a
    // single-surface envelope useful for testing -- so refuse here rather than
    // let the seed be dropped without a word.
    //
    // Credentials are not a device claim, and this refusal used to fire on them
    // too. InstallComposedProfile() composes normally for a payload that claims
    // no device and attaches the credentials to what it composed, so an
    // authenticated proxy plus a pinned seed is a legal combination -- and the
    // commonest one this package serves. The empty `device_profile` key below
    // is deliberate and must stay: base/apostate/profile.cc's ParseOrNull reads
    // `proxy_credentials` only inside `if (FindDict("device_profile"))`, so a
    // wrapper without the key loses the credentials silently.
    if (userSuppliedSeed && describesDevice) {
      throw new ProfileResolutionError(
        "an authored profile and a --fingerprint seed cannot be combined: an "
        + "--apostate-profile payload describing a device suppresses the browser's "
        + "composition entirely, so the seed would be silently ignored and every "
        + "surface the profile does not describe would stay host-inherited",
        { profile_id: resolution?.profileId ?? null },
        "APOSTATE_ENVELOPE_SEED_CONFLICT",
      );
    }
    const payload = credentials
      ? { device_profile: devicePayload, proxy_credentials: credentials }
      : devicePayload;
    const encoded = Buffer.from(stableStringify(payload), "utf8").toString("base64");
    args.push(`--apostate-profile=${encoded}`);
  }
  if (config.proxy !== null && config.webrtc_ip && !hasSwitch(args, "--fingerprint-webrtc-ip")) {
    args.push(`--fingerprint-webrtc-ip=${config.webrtc_ip}`);
  }
  if (config.proxy !== null && !hasSwitch(args, "--force-webrtc-ip-handling-policy")) {
    args.push("--force-webrtc-ip-handling-policy=disable_non_proxied_udp");
  }
  if (config.headless && !hasSwitch(args, "--headless")) args.push("--headless=new");
  if (config.user_data_dir !== null && !driverOwnsProfile) args.push(`--user-data-dir=${config.user_data_dir}`);
  if (config.proxy !== null) args.push(`--proxy-server=${proxyEndpoint(config.proxy)}`);
  if (config.locale !== null && !hasSwitch(args, "--lang")) args.push(`--lang=${config.locale.split(",")[0]}`);
  return args;
}

async function isRegularFile(path) {
  try {
    const info = await stat(path);
    return info.isFile();
  } catch {
    return false;
  }
}

// Must agree byte-for-byte with python/apostate/binary.py::_default_cache_dir.
// A user with both packages installed should share one 600 MB install, not
// download the browser twice into two different conventions.
function defaultCacheDir() {
  if (process.env.APOSTATE_CACHE_DIR) return process.env.APOSTATE_CACHE_DIR;
  if (process.platform === "win32") {
    return join(process.env.LOCALAPPDATA || process.env.TEMP || homedir(), "apostate", "cache");
  }
  if (process.platform === "darwin") return join(homedir(), "Library", "Caches", "apostate");
  return join(process.env.XDG_CACHE_HOME || join(homedir(), ".cache"), "apostate");
}

export function expectedArtifactName(target) {
  return TARGET_ARTIFACTS[target];
}

function manifestDeclaresUnpublished(manifest) {
  if (manifest.status === "unpublished") return true;
  if (isObject(manifest.artifacts) && Object.keys(manifest.artifacts).length === 0 && manifest.artifact === undefined) return true;
  if (Array.isArray(manifest.artifacts) && manifest.artifacts.length === 0 && manifest.artifact === undefined) return true;
  return false;
}

// A manifest's `package_version` is NOT required to equal this package's.
//
// It names the binary release the archive came from, and the launcher moves
// independently of it: this package is 0.1.1 and installs the binaries
// published as v0.1.0. That is not a mismatch to be caught, it is the normal
// relationship between a launcher release and a browser release. What binds a
// manifest to this package is the Chromium version and the catalogue version,
// and those are still exact.
function normalizeManifest(manifest, source = "manifest") {
  if (!isObject(manifest)) throw new ManifestError(`${source} must contain a JSON object.`);
  for (const [key, expected] of [["chromium_version", CHROMIUM_VERSION], ["catalogue_version", CATALOGUE_VERSION]]) {
    if (manifest[key] !== undefined && manifest[key] !== expected) {
      throw new ManifestError(`${source}.${key} does not match this package.`, { expected, actual: manifest[key] });
    }
  }
  if (manifest.artifacts !== undefined && !Array.isArray(manifest.artifacts) && !isObject(manifest.artifacts)) {
    throw new ManifestError(`${source}.artifacts must be an array or object.`);
  }
  return {
    ...cloneJson(manifest),
    package_version: manifest.package_version ?? PACKAGE_VERSION,
    chromium_version: manifest.chromium_version ?? CHROMIUM_VERSION,
    catalogue_version: manifest.catalogue_version ?? CATALOGUE_VERSION,
  };
}

// Returns the manifest and where it came from. `baked` is true only for the
// copy shipped inside this package, and it is the one thing that decides
// whether the runtime release-manifest route below may be taken: a caller who
// names a manifest is pinning a digest, and going to the network behind that
// instruction would quietly unpin it.
async function readOrDownloadManifest(options = {}) {
  if (options.manifest !== undefined && options.manifest !== null) {
    if (typeof options.manifest === "string") {
      const source = options.manifest;
      if (/^[a-z][a-z\d+.-]*:\/\//i.test(source)) {
        return { manifest: normalizeManifest(await downloadUrl(source, options, "manifest"), source), source, baked: false };
      }
      try {
        return { manifest: normalizeManifest(JSON.parse(await readFile(resolve(source), "utf8")), source), source, baked: false };
      } catch (error) {
        if (error instanceof ManifestError) throw error;
        throw new ManifestError(`Unable to read release manifest ${source}.`, { cause: error?.message ?? String(error) });
      }
    }
    return { manifest: normalizeManifest(options.manifest, "manifest option"), source: "manifest option", baked: false };
  }
  if (options.manifestPath) {
    try {
      return { manifest: normalizeManifest(JSON.parse(await readFile(resolve(options.manifestPath), "utf8")), options.manifestPath), source: String(options.manifestPath), baked: false };
    } catch (error) {
      if (error instanceof ManifestError) throw error;
      throw new ManifestError(`Unable to read release manifest ${options.manifestPath}.`, { cause: error?.message ?? String(error) });
    }
  }
  if (options.manifestUrl) {
    return { manifest: normalizeManifest(await downloadUrl(options.manifestUrl, options, "manifest"), options.manifestUrl), source: String(options.manifestUrl), baked: false };
  }
  try {
    return { manifest: normalizeManifest(JSON.parse(await readFile(DEFAULT_MANIFEST_PATH, "utf8")), DEFAULT_MANIFEST_PATH), source: "package", baked: true };
  } catch (error) {
    if (error instanceof ManifestError) throw error;
    throw new ManifestError(`Unable to read package release manifest ${DEFAULT_MANIFEST_PATH}.`, { cause: error?.message ?? String(error) });
  }
}

function artifactFromManifest(manifest, target) {
  const expected = expectedArtifactName(target);
  const artifacts = manifest.artifacts;
  let candidate = null;
  if (Array.isArray(artifacts)) {
    candidate = artifacts.find((entry) => isObject(entry) && (entry.target === target || entry.platform === target || entry.name === expected || entry.artifact === expected));
  } else if (isObject(artifacts)) {
    candidate = artifacts[target] ?? null;
  }
  if (!candidate && typeof manifest.artifact === "string" && manifest.platform === target) {
    candidate = {
      platform: manifest.platform,
      artifact: manifest.artifact,
      sha256: manifest.sha256,
      url: manifest.url ?? manifest.download_url,
      binary_path: manifest.binary_path ?? manifest.binaryPath,
    };
  }
  if (!candidate && isObject(manifest.artifact) && (manifest.artifact.target === undefined || manifest.artifact.platform === undefined || manifest.artifact.platform === target || manifest.artifact.target === target)) candidate = manifest.artifact;
  if (!candidate) return null;
  if (!isObject(candidate)) throw new ManifestError(`Manifest artifact for ${target} must be an object.`);
  const name = candidate.artifact ?? candidate.name ?? expected;
  if (name !== expected) {
    throw new ManifestError(`Manifest artifact name ${name} does not match expected ${expected}.`, { target, expected, actual: name });
  }
  if (candidate.platform !== undefined && candidate.platform !== target && candidate.target !== target) {
    throw new ManifestError(`Manifest artifact platform ${candidate.platform} does not match ${target}.`, { target, actual: candidate.platform });
  }
  return { ...cloneJson(candidate), target, platform: target, artifact: expected, name: expected };
}

// The per-asset manifest published beside every archive in a GitHub release.
const RELEASE_MANIFEST_SUFFIX = ".manifest.json";

// Where an artifact's own manifest is looked for when the copy baked into
// this package does not describe it.
//
// Two URLs, tried in order. The tagged one first, so a launcher published in
// step with a browser release reads exactly that release; `latest` second,
// because a launcher-only release -- this package is 0.1.1 and installs the
// binaries published as v0.1.0 -- has no tag of its own to read.
//
// APOSTATE_DOWNLOAD_BASE_URL deliberately does NOT redirect these. It points
// the ARCHIVE at a mirror, and leaving the digest on the release keeps the
// property that makes a mirror safe: a wrong base URL cannot install the
// wrong thing, because the digest and the bytes still come from two
// different places.
function releaseManifestUrls(artifactName) {
  return [
    `https://github.com/${RELEASE_REPOSITORY}/releases/download/v${PACKAGE_VERSION}/${artifactName}${RELEASE_MANIFEST_SUFFIX}`,
    `https://github.com/${RELEASE_REPOSITORY}/releases/latest/download/${artifactName}${RELEASE_MANIFEST_SUFFIX}`,
  ];
}

// How each URL is labelled in `apostate info`, positionally: the two routes
// are the two URLs and nothing else produces one.
const RELEASE_MANIFEST_SOURCES = ["release-tag", "release-latest"];

// Every field that binds a fetched manifest to this package, checked before
// its digest is trusted with a 150 MB download.
//
// `package_version` is deliberately absent. It names the binary release the
// archive belongs to, and a launcher installing an older binary release is
// exactly the case this route exists for.
function releaseManifestRecord(payload, target, url) {
  const expected = expectedArtifactName(target);
  const refuse = (detail) => new ManifestError(`release manifest at ${url} does not describe this package: ${detail}`, { url, target, detail });
  if (!isObject(payload)) throw refuse("it is not a JSON object");
  for (const [key, want] of [["chromium_version", CHROMIUM_VERSION], ["catalogue_version", CATALOGUE_VERSION], ["platform", target], ["artifact", expected]]) {
    if (payload[key] !== want) throw refuse(`${key} is ${payload[key] === undefined ? "absent" : String(payload[key])}, not ${want}`);
  }
  if (typeof payload.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(payload.sha256)) {
    throw refuse("sha256 is not a 64-character lowercase digest");
  }
  // The archive is taken from the directory the manifest came from, so the
  // two can never be paired across releases.
  return { ...cloneJson(payload), target, platform: target, artifact: expected, name: expected, url: new URL(expected, url).toString() };
}

// The first candidate URL that answers with a manifest this package accepts,
// plus every URL tried -- which is what an unpublished error has to name.
async function fetchReleaseArtifact(options, target) {
  const expected = expectedArtifactName(target);
  const tried = [];
  const failures = [];
  for (const [index, url] of releaseManifestUrls(expected).entries()) {
    tried.push(url);
    let payload;
    try {
      payload = await downloadUrl(url, options, "manifest");
    } catch (error) {
      failures.push({ url, reason: error?.message ?? String(error) });
      continue;
    }
    try {
      const artifact = releaseManifestRecord(payload, target, url);
      return { manifest: cloneJson(payload), artifact, url, source: RELEASE_MANIFEST_SOURCES[index], tried, failures };
    } catch (error) {
      // A manifest that parsed but describes something else is not a reason
      // to stop: the tagged URL can legitimately answer for a different build
      // while `latest` answers for this one.
      failures.push({ url, reason: error?.message ?? String(error) });
    }
  }
  return { manifest: null, artifact: null, url: null, source: null, tried, failures };
}

function unpublishedArtifactError(target, tried = []) {
  const artifact = expectedArtifactName(target);
  // No trailing full stop after the last URL: a terminal that wraps the line
  // makes a period look like part of the address, and these get pasted.
  const suffix = tried.length
    ? ` No release manifest for ${artifact} could be fetched. Tried: ${tried.join(", ")}`
    : "";
  return new UnpublishedArtifactError(
    `Release manifest is unpublished; Apostate binary artifacts are not available for acquisition.${suffix}`,
    { target, artifact, urls_tried: [...tried] },
  );
}

// Which refusal applies when nothing local describes an artifact and the
// runtime route is closed. Two messages, because they answer different
// questions: a manifest that declares itself unpublished carries no binaries
// at all, while one that simply omits this target published a subset.
function localRefusal(manifest, target) {
  if (manifestDeclaresUnpublished(manifest)) return unpublishedArtifactError(target);
  return new UnpublishedArtifactError(`Apostate binary for ${target} is not published for this release.`, { target });
}

// What `apostate info` says about how far a manifest can be trusted. Mirrored
// verbatim in the Python package.
const MANIFEST_NOTE_PINNED = "This manifest ships inside the package, so the digest was pinned at publish time rather than fetched from the same place as the bytes it describes.";
const MANIFEST_NOTE_FETCHED = "A manifest fetched at run time is a transport-integrity check: it detects a corrupted or truncated download, not a substituted release. For provenance run: gh attestation verify <archive> --repo heretic-tech/apostate";

// Everything needed to acquire the archive for `target`, and where the digest
// that will be checked against it came from.
//
// Two sources, in this order:
//
//  1. The manifest baked into this package, or one the caller named, when it
//     declares itself published and carries a record for this target. The
//     pinned path and the strongest one: the digest shipped inside the
//     package, so it and the bytes it describes came from two different
//     places.
//
//  2. The artifact's own manifest, published beside it in the release and
//     fetched at run time. A launcher release and a browser release move
//     independently, so a launcher cannot always carry the digest of the
//     archive it installs. This is a transport-integrity check -- it catches
//     a corrupted or truncated download -- and it is NOT provenance, because
//     the digest then travels with the bytes. Provenance is
//     `gh attestation verify`.
//
// `remote: false` keeps the whole thing local. Discovery and launch need
// that: "where would this launch get its browser" must not turn into a
// network round trip on every call.
async function resolveArtifact(options, target, { remote = true } = {}) {
  const { manifest, baked } = await readOrDownloadManifest(options);
  const local = manifest.status === "unpublished" ? null : artifactFromManifest(manifest, target);
  if (local) return { manifest, artifact: local, source: baked ? "baked" : "configured", url: null, tried: [], trust: "pinned" };
  // A caller who named a manifest is pinning a digest. Fetching a different
  // one behind that instruction would quietly unpin it, so the runtime route
  // is reserved for the copy this package ships.
  if (!remote || !baked) {
    return { manifest, artifact: null, source: baked ? "baked" : "configured", url: null, tried: [], trust: null };
  }
  const fetched = await fetchReleaseArtifact(options, target);
  if (!fetched.artifact) {
    return { manifest, artifact: null, source: null, url: null, tried: fetched.tried, failures: fetched.failures, trust: null };
  }
  return {
    manifest: fetched.manifest,
    artifact: fetched.artifact,
    source: fetched.source,
    url: fetched.url,
    tried: fetched.tried,
    failures: fetched.failures,
    trust: "transport-integrity",
  };
}
async function downloadUrl(url, options, kind) {
  const proxy = normalizeProxy(options.proxy);
  if (proxy && !options.download) {
    throw new BinaryDownloadError(`Cannot download ${kind} through proxy ${redactProxy(proxy)} without a proxy-aware downloader.`, {
      proxy: redactProxy(proxy),
    });
  }
  if (options.download) {
    try {
      const value = await options.download(String(url), {
        kind,
        proxy,
        proxy_redacted: redactProxy(proxy),
      });
      if (kind === "manifest") {
        if (isObject(value)) return value;
        return JSON.parse(Buffer.from(await toBuffer(value)).toString("utf8"));
      }
      return await toBuffer(value);
    } catch (error) {
      if (error instanceof ApostateError) throw error;
      throw new BinaryDownloadError(`Unable to download ${kind}: ${sanitizeErrorMessage(error?.message ?? error, proxy)}.`, {
        proxy: redactProxy(proxy),
      });
    }
  }
  if (typeof fetch !== "function") throw new BinaryDownloadError("This Node runtime has no fetch; provide a download callback.");
  try {
    const timeoutMs = Number.isFinite(options.downloadTimeoutMs) ? Math.max(1, options.downloadTimeoutMs) : 30000;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(String(url), { signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      if (kind === "manifest") return await response.json();
      return Buffer.from(await response.arrayBuffer());
    } finally {
      clearTimeout(timer);
    }
  } catch (error) {
    const reason = error?.name === "AbortError" ? "timed out" : sanitizeErrorMessage(error?.message ?? error, proxy);
    throw new BinaryDownloadError(`Unable to download ${kind}: ${reason}.`, { proxy: redactProxy(proxy) });
  }
}

async function toBuffer(value) {
  if (Buffer.isBuffer(value)) return value;
  if (value instanceof Uint8Array) return Buffer.from(value);
  if (value instanceof ArrayBuffer) return Buffer.from(value);
  if (value && typeof value.arrayBuffer === "function") return Buffer.from(await value.arrayBuffer());
  if (value && value[Symbol.asyncIterator]) {
    const chunks = [];
    for await (const chunk of value) chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    return Buffer.concat(chunks);
  }
  throw new TypeError("Downloader must return bytes, an ArrayBuffer, or an async iterable of bytes.");
}

export async function verifyArtifact(archive, artifact) {
  const bytes = await toBuffer(archive);
  if (!artifact || typeof artifact.sha256 !== "string" || !/^[0-9a-f]{64}$/i.test(artifact.sha256)) {
    throw new BinaryIntegrityError("Release manifest is missing a valid SHA-256 for the artifact.");
  }
  const actualHash = createHash("sha256").update(bytes).digest("hex");
  if (actualHash.toLowerCase() !== artifact.sha256.toLowerCase()) {
    throw new BinaryIntegrityError(`SHA-256 mismatch for ${artifact.artifact ?? artifact.name ?? "binary artifact"}.`, {
      expected: artifact.sha256.toLowerCase(),
      actual: actualHash,
    });
  }
  return { sha256: actualHash };
}

async function runCommand(command, args, cwd, capture = false) {
  return new Promise((resolveCommand, rejectCommand) => {
    const child = spawn(command, args, { cwd, stdio: ["ignore", "pipe", "pipe"] });
    const stdout = [];
    const stderr = [];
    child.stdout?.on("data", (chunk) => stdout.push(Buffer.from(chunk)));
    child.stderr?.on("data", (chunk) => stderr.push(Buffer.from(chunk)));
    child.once("error", (error) => rejectCommand(error));
    child.once("close", (code) => {
      if (code === 0) return resolveCommand(capture ? Buffer.concat(stdout).toString("utf8") : undefined);
      const detail = Buffer.concat(stderr).toString("utf8").trim();
      rejectCommand(new Error(`${command} exited with code ${code}${detail ? `: ${detail}` : ""}`));
    });
  });
}

function safeArchiveMemberName(value) {
  const name = String(value).replaceAll("\\", "/");
  const parts = name.split("/");
  if (!name || name.includes("\0") || name.startsWith("/") || /^[A-Za-z]:\//.test(name) || parts.includes("..")) {
    throw new BinaryExtractionError(`Archive contains an unsafe member path: ${JSON.stringify(name)}.`);
  }
  return name;
}

// `.github/release/artifact-policy.json` says reject every symbolic link. That
// rule cannot be satisfied and also ship macOS: the macos-arm64 artifact reaches
// its framework payload through five relative symlinks
// (Chromium Framework.framework/Versions/Current -> 152.0.7977.83 and four
// siblings), and a bundle without them does not launch. The property the rule
// protects is containment, so containment is what is enforced -- a relative
// target that stays inside the extraction root is accepted, an absolute target
// or one that climbs out is refused.
function checkContainedLink(name, link) {
  const target = String(link ?? "").replaceAll("\\", "/");
  if (!target || target.startsWith("/") || target.includes("\0") || /^[A-Za-z]:\//.test(target)) {
    throw new BinaryExtractionError(`Archive member ${JSON.stringify(name)} links outside the archive: ${JSON.stringify(target)}.`);
  }
  const parts = name.split("/").slice(0, -1);
  for (const part of target.split("/")) {
    if (part === "" || part === ".") continue;
    if (part === "..") {
      if (parts.length === 0) {
        throw new BinaryExtractionError(`Archive member ${JSON.stringify(name)} links outside the archive: ${JSON.stringify(target)}.`);
      }
      parts.pop();
      continue;
    }
    parts.push(part);
  }
}



function scanZipArchive(bytes) {
  const minimumEnd = 22;
  const lowerBound = Math.max(0, bytes.length - 0xffff - minimumEnd);
  let endOffset = -1;
  for (let offset = bytes.length - minimumEnd; offset >= lowerBound; offset -= 1) {
    if (bytes.readUInt32LE(offset) === 0x06054b50) {
      endOffset = offset;
      break;
    }
  }
  if (endOffset < 0 || endOffset + minimumEnd > bytes.length) {
    throw new BinaryExtractionError("Unable to inspect ZIP central directory before extraction.");
  }
  const entryCount = bytes.readUInt16LE(endOffset + 10);
  const directorySize = bytes.readUInt32LE(endOffset + 12);
  const directoryOffset = bytes.readUInt32LE(endOffset + 16);
  if (entryCount === 0xffff || directorySize === 0xffffffff || directoryOffset === 0xffffffff || directoryOffset + directorySize > endOffset) {
    throw new BinaryExtractionError("ZIP64 archives are not supported for safe extraction.");
  }
  let offset = directoryOffset;
  for (let index = 0; index < entryCount; index += 1) {
    if (offset + 46 > bytes.length || bytes.readUInt32LE(offset) !== 0x02014b50) {
      throw new BinaryExtractionError("ZIP central directory is malformed.");
    }
    const versionMadeBy = bytes.readUInt16LE(offset + 4);
    const flags = bytes.readUInt16LE(offset + 8);
    const nameLength = bytes.readUInt16LE(offset + 28);
    const extraLength = bytes.readUInt16LE(offset + 30);
    const commentLength = bytes.readUInt16LE(offset + 32);
    const nameStart = offset + 46;
    const nextOffset = nameStart + nameLength + extraLength + commentLength;
    if (nextOffset > bytes.length || nextOffset > directoryOffset + directorySize) {
      throw new BinaryExtractionError("ZIP central directory entry is truncated.");
    }
    const name = safeArchiveMemberName(bytes.subarray(nameStart, nameStart + nameLength).toString(flags & 0x0800 ? "utf8" : "latin1"));
    if ((versionMadeBy >>> 8) === 3) {
      // Unix-made zip: only regular files, directories and symlinks may land.
      const kind = ((bytes.readUInt32LE(offset + 38) >>> 16) & 0xffff) & 0o170000;
      if (kind !== 0 && kind !== 0o100000 && kind !== 0o040000 && kind !== 0o120000) {
        throw new BinaryExtractionError(`Archive member ${JSON.stringify(name)} is a special file.`);
      }
    }
    offset = nextOffset;
  }
  if (offset > directoryOffset + directorySize) throw new BinaryExtractionError("ZIP central directory extends beyond its declared size.");
}

// bsdtar (macOS, and tar.exe on Windows 10+) autodetects zstd; GNU tar needs to
// be told. Plain first, because `--use-compress-program=zstd` fails on bsdtar.
const TAR_COMPRESSION_ATTEMPTS = [[], ["--zstd"], ["--use-compress-program=zstd"]];

async function runTar(operands, cwd, capture = false) {
  let firstError;
  for (const prefix of TAR_COMPRESSION_ATTEMPTS) {
    try {
      return await runCommand("tar", [...prefix, ...operands], cwd, capture);
    } catch (error) {
      firstError ??= error;
    }
  }
  throw new BinaryExtractionError(`Unable to read tar archive: ${firstError?.message ?? "tar failed"}.`);
}

async function scanTarArchive(archivePath, destination) {
  // `-tf` gives exact names, one per line. `-tvf` gives the mode column and,
  // for a symlink, `<name> -> <target>`. The target is read by anchoring on the
  // exact name from `-tf` rather than by parsing the verbose columns.
  const names = (await runTar(["-tf", archivePath], destination, true)).split(/\r?\n/).filter((line) => line.length > 0);
  const details = (await runTar(["-tvf", archivePath], destination, true)).split(/\r?\n/).filter((line) => line.length > 0);
  if (names.length !== details.length) throw new BinaryExtractionError("Tar archive listing is ambiguous; refusing extraction.");
  names.forEach((raw, index) => {
    const name = safeArchiveMemberName(raw);
    const detail = details[index];
    if (!/^[\-dlhbcps][\-rwxSsTt]{9}/.test(detail)) {
      throw new BinaryExtractionError("Tar archive member metadata is malformed.");
    }
    const kind = detail[0];
    if (kind === "l") {
      const marker = `${raw} -> `;
      const at = detail.indexOf(marker);
      if (at < 0) throw new BinaryExtractionError(`Unable to read the link target of ${JSON.stringify(name)}.`);
      checkContainedLink(name, detail.slice(at + marker.length));
      return;
    }
    if (kind === "h") {
      const marker = `${raw} link to `;
      const at = detail.indexOf(marker);
      if (at < 0) throw new BinaryExtractionError(`Unable to read the link target of ${JSON.stringify(name)}.`);
      safeArchiveMemberName(detail.slice(at + marker.length));
      return;
    }
    if (kind !== "-" && kind !== "d") {
      throw new BinaryExtractionError(`Archive member ${JSON.stringify(name)} is a special file.`);
    }
  });
}

async function defaultExtract(bytes, destination, context) {
  const archivePath = join(destination, context.artifact);
  const tree = join(destination, "tree");
  await writeFile(archivePath, bytes, { mode: 0o600 });
  await mkdir(tree, { recursive: true });
  try {
    if (context.artifact.endsWith(".zip")) {
      scanZipArchive(bytes);
      // tar.exe reads zip on Windows 10+; unzip is not installed by default.
      await runTar(["-xf", archivePath, "-C", tree], destination);
    } else if (context.artifact.endsWith(".tar.zst")) {
      await scanTarArchive(archivePath, destination);
      await runTar(["-xf", archivePath, "-C", tree], destination);
    } else {
      throw new BinaryExtractionError(`unsupported archive format ${context.artifact}`);
    }
  } finally {
    await rm(archivePath, { force: true });
  }
  return tree;
}

function withinDirectory(root, candidate) {
  const relativePath = relative(resolve(root), resolve(candidate));
  return relativePath === "" || (!isAbsolute(relativePath) && relativePath !== ".." && !relativePath.startsWith(".." + sep));
}

// Suffixes an artifact name may carry, longest first so `.tar.zst` wins.
const ARCHIVE_SUFFIXES = [".tar.zst", ".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tar", ".zip", ".zst"];

// Every release archive wraps its tree in a directory named exactly after the
// archive, `apostate-<version>-<target>/`. Dropping that level keeps the
// installed path short and the executable's relative path independent of the
// archive's name. The name must match: hoisting any lone directory would also
// unwrap an archive whose single top-level entry is meaningful -- `Chromium.app/`
// is the obvious one -- and silently move the executable.
async function hoistSingleRoot(root, archiveName) {
  let expected = archiveName;
  for (const suffix of ARCHIVE_SUFFIXES) {
    if (expected.toLowerCase().endsWith(suffix)) {
      expected = expected.slice(0, -suffix.length);
      break;
    }
  }
  const entries = await readdir(root, { withFileTypes: true });
  if (entries.length !== 1 || !entries[0].isDirectory() || entries[0].name !== expected) return root;
  return join(root, entries[0].name);
}

// Where the browser executable sits inside each platform's archive.
const EXECUTABLE_LAYOUT = {
  "macos-arm64": "Chromium.app/Contents/MacOS/Chromium",
  "linux-x64": "chrome",
  "linux-arm64": "chrome",
  "windows-x64": "chrome.exe",
};
const EXECUTABLE_NAMES = {
  chrome: true, "chrome.exe": true, Chromium: true, chromium: true,
  "chromium-browser": true, "chromium.exe": true,
};

// Returns the executable's path RELATIVE to the install root. Chromium cannot
// run as a lone file -- it needs its framework, ICU data and .pak resources --
// so the tree stays intact and only the executable's location is recorded.
async function findExtractedBinary(root, requestedPath, target) {
  if (requestedPath) {
    const candidate = resolve(root, requestedPath);
    if (!withinDirectory(root, candidate) || !(await isRegularFile(candidate))) {
      throw new BinaryExtractionError("Manifest binary_path does not identify a file inside the extracted archive.");
    }
    return relative(root, candidate);
  }
  const expected = EXECUTABLE_LAYOUT[target];
  if (expected && await isRegularFile(join(root, expected))) return expected;
  const results = [];
  async function walk(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) await walk(path);
      else if (entry.isFile() && EXECUTABLE_NAMES[entry.name] === true) results.push(path);
    }
  }
  await walk(root);
  if (results.length !== 1) {
    throw new BinaryExtractionError(results.length === 0
      ? `Extracted archive does not contain a recognized browser executable for ${target}.`
      : `Extracted archive contains ${results.length} candidate executables for ${target}.`);
  }
  return relative(root, results[0]);
}

// Marker format. Bumped when the on-disk layout changes so an install written
// by an older package is replaced rather than misread. Format 1 cached a lone
// copied executable, which could not start.
const INSTALL_FORMAT = 2;

function cachePaths(cacheDir, target) {
  const root = join(resolve(cacheDir), CHROMIUM_VERSION, target);
  return {
    root,
    install: join(root, "install"),
    archive: join(root, expectedArtifactName(target)),
    metadata: join(root, "install.json"),
  };
}

// The digest never comes from here, so a wrong base URL cannot install the
// wrong thing: it fails verification instead. APOSTATE_DOWNLOAD_BASE_URL is
// consulted first because it is an explicit operator instruction -- "take the
// bytes from this mirror" -- and a url carried by the manifest would
// otherwise silently ignore it.
function artifactUrlFor(manifest, artifact) {
  const name = artifact.artifact;
  const base = process.env.APOSTATE_DOWNLOAD_BASE_URL || manifest.base_url;
  if (typeof base === "string" && base) return new URL(name, base.endsWith("/") ? base : `${base}/`).toString();
  if (typeof artifact.url === "string" && artifact.url) return artifact.url;
  if (typeof artifact.download_url === "string" && artifact.download_url) return artifact.download_url;
  const repository = manifest.repository ?? RELEASE_REPOSITORY;
  // docs/RELEASE.md step 2: releases are tagged vMAJOR.MINOR.PATCH. The tag
  // comes from the MANIFEST's package_version, which is the binary release's
  // identity -- v0.1.0 -- and not this launcher's.
  const tag = manifest.tag ?? `v${manifest.package_version ?? PACKAGE_VERSION}`;
  return `https://github.com/${repository}/releases/download/${tag}/${name}`;
}

// `artifact` may be null, and that is the interesting case. It means no
// manifest record could be had -- the copy baked into this package describes
// nothing and the network was not consulted -- and the question then is not
// "is this install the one the manifest names" but "is this install intact".
// The marker answers that on its own: it records the layout format, the
// Chromium version, the platform, the archive it came from and the digest of
// the executable, and the executable is re-hashed against it here. Refusing
// a good install because a manifest was unreachable is how the 0.1.0 packages
// made a perfectly valid cache invisible.
//
// package_version is not compared against this package's. The install is of a
// browser, identified by its Chromium version and archive; a launcher-only
// release would otherwise throw away 600 MB and re-download it unchanged.
async function validCachedInstall(paths, target, artifact) {
  if (!(await isRegularFile(paths.metadata))) return null;
  try {
    const metadata = JSON.parse(await readFile(paths.metadata, "utf8"));
    if (!isObject(metadata) || metadata.format !== INSTALL_FORMAT) return null;
    if (metadata.chromium_version !== CHROMIUM_VERSION) return null;
    if (metadata.platform !== target || metadata.artifact !== expectedArtifactName(target)) return null;
    if (typeof metadata.artifact_sha256 !== "string" || !/^[0-9a-f]{64}$/i.test(metadata.artifact_sha256)) return null;
    if (artifact) {
      if (typeof artifact.sha256 !== "string" || !/^[0-9a-f]{64}$/i.test(artifact.sha256)) return null;
      if (metadata.artifact_sha256.toLowerCase() !== artifact.sha256.toLowerCase()) return null;
    }
    if (typeof metadata.executable !== "string" || !metadata.executable) return null;
    const executable = resolve(paths.install, metadata.executable);
    if (!withinDirectory(paths.install, executable) || !(await isRegularFile(executable))) return null;
    // Re-hash only the file about to be exec'd. The archive is not retained and
    // the install was swapped in atomically, so re-reading the whole tree on
    // every launch would be a cost with no matching threat.
    const actual = createHash("sha256").update(await readFile(executable)).digest("hex");
    if (actual.toLowerCase() !== String(metadata.executable_sha256).toLowerCase()) return null;
    return executable;
  } catch {
    return null;
  }
}

async function replaceTree(staged, destination) {
  let retired = null;
  try {
    await lstat(destination);
    retired = `${destination}.stale-${process.pid}-${Date.now()}`;
    await rename(destination, retired);
  } catch {
    retired = null;
  }
  try {
    await rename(staged, destination);
  } catch (error) {
    if (retired) await rename(retired, destination);
    throw error;
  }
  if (retired) await rm(retired, { recursive: true, force: true });
}


// The publication question as far as it can be answered without the network:
// does a manifest already on this machine carry a record for this target.
//
// A `null` artifact here no longer means "nothing is published". It means
// nothing local says so, and the answer has moved to the release, where
// resolveArtifact() will go when acquisition is actually about to happen.
// `remote_available` is what tells a caller which of the two it got.
async function resolveLocalArtifact(options, target) {
  const resolved = await resolveArtifact(options, target, { remote: false });
  return { ...resolved, remote_available: resolved.artifact === null && resolved.source === "baked" };
}

// ---------------------------------------------------------------------------
// Finding a browser that is already on disk.
//
// The package downloads its own copy, but it is not the only way an Apostate
// build arrives on a machine: a release archive unpacked by hand, a packaged
// .app dragged into /Applications, an image that bakes one into /opt. Asking
// the network for 150 MB that is already sitting there is the wrong first
// move, so the search below runs before the download and after the cache.
//
// Everything here is subordinate to one hazard. A stock Chrome and an
// Apostate build are the same executable name in the same layout, and
// launching stock Chrome with Apostate's switches produces a session with
// none of the protections those switches name -- silently, because the
// unknown switches are simply ignored. So identification is never by name or
// location: a candidate is adopted only once the tree around it has been
// shown to be an Apostate payload.

// The order is part of the published contract and is reported verbatim by
// discoveryReport(), so both packages can be diffed against it.
const DISCOVERY_ORDER = Object.freeze(["argument", "environment", "cache", "well-known"]);

// scripts/package-artifact.sh copies build/MANIFEST.lock and the whole of
// resources/profiles/ into every release archive. Stock Chrome and stock
// Chromium ship neither, which is what makes their presence beside an
// executable evidence rather than a guess.
const PAYLOAD_MARKER_REASON = "no Apostate payload beside it (build/MANIFEST.lock or resources/profiles/catalogue.json)";

// Where a hand-installed build plausibly lives, per target. Deliberately
// short: every extra root is another tree that gets stat'd on every launch,
// and a location nobody uses only adds latency.
function wellKnownRoots(target) {
  const home = homedir();
  if (target === "macos-arm64") return ["/Applications", join(home, "Applications")];
  if (target === "linux-x64" || target === "linux-arm64") return [join(home, ".cache", "apostate"), "/opt/apostate"];
  if (target === "windows-x64") {
    // No LOCALAPPDATA means no per-user application data directory to search;
    // guessing C:\Users\... from the username would be a worse answer.
    return process.env.LOCALAPPDATA ? [join(process.env.LOCALAPPDATA, "apostate")] : [];
  }
  return [];
}

// Where the executable sits inside a payload root. macOS gets three because
// the archive ships Chromium.app while a rebranded local build may ship
// Apostate.app, and either bundle may name its executable either way.
const DISCOVERY_EXECUTABLES = {
  "macos-arm64": [
    "Chromium.app/Contents/MacOS/Chromium",
    "Apostate.app/Contents/MacOS/Chromium",
    "Apostate.app/Contents/MacOS/Apostate",
  ],
  "linux-x64": ["chrome"],
  "linux-arm64": ["chrome"],
  "windows-x64": ["chrome.exe"],
};

// Where the executable sits inside a macOS bundle. Separate from the table
// above because a caller may name the bundle itself -- dragging Chromium.app
// out of the archive is the obvious thing to do with it -- and the path
// inside it is then one level shorter.
const BUNDLE_EXECUTABLES = {
  "macos-arm64": ["Contents/MacOS/Chromium", "Contents/MacOS/Apostate"],
};

// Reported verbatim by `apostate info` and mirrored in
// python/apostate/binary.py, so these two strings are part of the contract.
const NO_FILE_REASON = "does not name a file";
const NO_BROWSER_REASON = "names a directory with no browser inside it (expected Chromium.app, chrome or chrome.exe)";

// A path the caller named, resolved to something runnable.
//
// Three forms, because all three are what people actually have on disk. The
// first is the executable itself. The other two are directories, and
// refusing them was the most common way this package told someone who had
// the browser that they did not: `Chromium.app` is a directory, so the
// obvious answer to "where is the browser" failed an is-a-file test, and so
// did the extracted `apostate-<version>-<target>/` tree.
//
// A directory is resolved through the same layout the well-known search
// uses, so there is one description of where a browser sits inside a tree.
// Nothing beyond existence is verified: a caller who names a path has named
// it, and the marker and version checks belong to the searches, which adopt
// a browser nobody pointed at.
async function resolveNamedBinary(value, target = null) {
  const path = resolve(String(value));
  if (await isRegularFile(path)) return { executable: path, payload_root: null };
  if (!(await isDirectory(path))) return { path, reason: NO_FILE_REASON };
  // Only a directory consults the layout table, and only the table needs a
  // host this package ships a binary for. Someone who names an executable on
  // an unsupported host is still naming an executable, and resolving the
  // target eagerly refused it.
  const layout = target ?? normalizeTarget();
  for (const relativeExecutable of [...(BUNDLE_EXECUTABLES[layout] ?? []), ...(DISCOVERY_EXECUTABLES[layout] ?? [])]) {
    const executable = join(path, relativeExecutable);
    if (await isRegularFile(executable)) return { executable, payload_root: path };
  }
  return { path, reason: NO_BROWSER_REASON };
}

async function isDirectory(path) {
  try {
    return (await stat(path)).isDirectory();
  } catch {
    return false;
  }
}

// A root and its immediate subdirectories, and no deeper. One level is what
// makes both /opt/apostate/chrome and the unpacked archive directory beside
// it, /opt/apostate/apostate-<version>-<target>/chrome, reachable; recursing
// further would walk the whole of /Applications for no further layout.
// Sorted, so the report does not depend on the order a filesystem hands back.
async function payloadRootsUnder(root) {
  const roots = [root];
  let entries;
  try {
    entries = await readdir(root, { withFileTypes: true });
  } catch {
    return roots;
  }
  for (const name of entries.map((entry) => entry.name).sort()) {
    if (await isDirectory(join(root, name))) roots.push(join(root, name));
  }
  return roots;
}

// The scalar head of build/MANIFEST.lock, or null if this is not Apostate's
// own build record. The format is scripts/build.sh's: `key = "value"` lines,
// `#` comments and blanks skipped, an `[outputs]` hash table below. Lines
// that are not scalar assignments are ignored rather than fatal -- only two
// questions are being asked of the file, and refusing to read a valid payload
// because a table row did not parse would be a worse answer than reading it.
const BUILD_RECORD_ASSIGNMENT = /^([A-Za-z_][A-Za-z\d_]*)\s*=\s*"([^"]*)"$/;

async function readBuildRecord(path) {
  let text;
  try {
    text = await readFile(path, "utf8");
  } catch {
    return null;
  }
  const values = {};
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const match = BUILD_RECORD_ASSIGNMENT.exec(line);
    if (match) values[match[1]] = match[2];
  }
  // Some other project's MANIFEST.lock parses just as well, so the file only
  // vouches for the tree when it carries the fields Apostate's build writes.
  if (typeof values.chromium_version !== "string" || !values.chromium_version) return null;
  if (typeof values.patch_series_sha256 !== "string" && typeof values.patch_contents_sha256 !== "string") return null;
  return values;
}

// CFBundleShortVersionString out of a plain-text plist. A binary plist simply
// does not match, which is the right outcome: the next step can still ask the
// executable, and inventing a parser for a format Apostate does not ship
// would be code with no reader.
const PLIST_SHORT_VERSION = /<key>\s*CFBundleShortVersionString\s*<\/key>\s*<string>([^<]*)<\/string>/;

async function bundleShortVersion(executable) {
  // <root>/Foo.app/Contents/MacOS/<exe> -> <root>/Foo.app/Contents/Info.plist
  const plist = join(dirname(dirname(executable)), "Info.plist");
  try {
    const match = PLIST_SHORT_VERSION.exec(await readFile(plist, "utf8"));
    return match ? match[1].trim() || null : null;
  } catch {
    return null;
  }
}

// The last resort: ask the browser. Never on Windows, where chrome.exe does
// not answer --version at all -- chrome_main_delegate.cc calls
// HandleVersionSwitches() inside `#if BUILDFLAG(IS_POSIX)`, so the switch
// falls straight through into a full browser start, and a GUI-subsystem
// binary writes nothing to stdout either way. See scripts/smoke-binary.sh,
// which refuses the same call for the same reason.
function probeVersion(executable) {
  const { promise, resolve: settle } = Promise.withResolvers();
  try {
    execFile(executable, ["--version"], { timeout: 10000, windowsHide: true, encoding: "utf8" }, (_error, stdout) => {
      // The exit status is not consulted. A browser that printed its version
      // and then failed to shut down cleanly has still answered the question,
      // and a browser that printed nothing is rejected by the match either way.
      const match = /\d+\.\d+\.\d+\.\d+/.exec(String(stdout ?? ""));
      settle(match ? match[0] : null);
    });
  } catch {
    settle(null);
  }
  return promise;
}

async function isExecutableFile(path) {
  try {
    await access(path, fsConstants.X_OK);
    return true;
  } catch {
    return false;
  }
}

// Marker first, then version, and that order is the safety property rather
// than an optimisation: step 2c executes the candidate, and nothing may be
// executed until step 1 has already established that the tree is an Apostate
// payload. Reversing the two would run an unknown binary found in a
// well-known directory, which is exactly the thing discovery must not do.
async function verifyPayload(payloadRoot, executable, target) {
  const record = await readBuildRecord(join(payloadRoot, "build", "MANIFEST.lock"));
  const catalogue = await isRegularFile(join(payloadRoot, "resources", "profiles", "catalogue.json"));
  if (!record && !catalogue) return { reason: PAYLOAD_MARKER_REASON };
  let version = record?.chromium_version ?? null;
  if (!version && target === "macos-arm64") version = await bundleShortVersion(executable);
  if (!version && target !== "windows-x64") version = await probeVersion(executable);
  if (!version) return { reason: "version could not be established" };
  if (version !== CHROMIUM_VERSION) return { reason: `reports Chromium ${version}, not ${CHROMIUM_VERSION}` };
  // Extraction by hand loses the mode bit often enough to be worth naming:
  // EACCES out of the driver is a much harder error to read than this one.
  if (target !== "windows-x64" && !(await isExecutableFile(executable))) return { reason: "not executable" };
  return { executable, source: "well-known", chromium_version: version, payload_root: payloadRoot };
}

// Scans every root to the end even after a hit. The alternative -- return on
// the first acceptance -- makes the rejection list depend on which sibling
// the filesystem happened to enumerate first, and the rejections are the
// whole value of the report: "I have Apostate installed, why is it
// downloading" is answered by the reason beside the path, not by its absence.
// The cost is a handful of stat() calls; the version probe runs only for a
// tree that already passed the marker check.
async function scanWellKnown(target, searchRoots) {
  const roots = searchRoots === undefined || searchRoots === null
    ? wellKnownRoots(target)
    : [...searchRoots].map((value) => resolve(String(value)));
  const candidates = DISCOVERY_EXECUTABLES[target] ?? [];
  const searched = [];
  const rejected = [];
  let found = null;
  for (const root of roots) {
    // A root that does not exist is not a rejection; it is the normal state
    // of three of the four platforms' locations on any given machine.
    if (!(await isDirectory(root))) continue;
    searched.push(root);
    for (const payloadRoot of await payloadRootsUnder(root)) {
      for (const relativeExecutable of candidates) {
        const executable = join(payloadRoot, relativeExecutable);
        if (!(await isRegularFile(executable))) continue;
        const verdict = await verifyPayload(payloadRoot, executable, target);
        if (verdict.reason !== undefined) rejected.push({ path: executable, reason: verdict.reason });
        else if (!found) found = verdict;
      }
    }
  }
  return { searched, rejected, found };
}

// Where a launch would get its browser from right now, without downloading
// anything, and what was refused on the way. This is the diagnostic behind
// `apostate info`; discoverBinary() is the same search with only the answer.
export async function discoveryReport(options = {}) {
  if (typeof options === "string") options = { binaryPath: options };
  if (!isObject(options)) throw new TypeError("discoveryReport options must be an object.");
  const order = [...DISCOVERY_ORDER];
  const target = normalizeTarget(options.target);
  const rejected = [];
  const answer = (executable, source, chromiumVersion, payloadRoot, searched = []) => ({
    order,
    searched,
    found: { executable, source, chromium_version: chromiumVersion, payload_root: payloadRoot },
    rejected,
  });

  // The two configured routes are instructions rather than candidates: each
  // is honoured verbatim, with no marker or version check, because a caller
  // who names a path is entitled to point this package at a build it made
  // itself. Existence is still established, because a path that names
  // nothing is not an instruction anybody can carry out -- and the report
  // exists to say so rather than to stop.
  for (const [value, source, label] of [
    [options.binaryPath ?? options.executablePath, "argument", "the configured path"],
    [process.env.APOSTATE_BINARY, "environment", "APOSTATE_BINARY"],
  ]) {
    if (value === undefined || value === null || value === "") continue;
    const named = await resolveNamedBinary(value, target);
    if (named.executable) return answer(named.executable, source, null, named.payload_root);
    // ensureBinary() still treats both misses as fatal -- a caller who named
    // a path wants that path and not a substitute. The report describes the
    // machine instead of acting on it, so it records the miss and looks on.
    rejected.push({ path: named.path, reason: `${label} ${named.reason}` });
  }

  const cacheDir = options.cacheDir ?? options.cache_dir ?? defaultCacheDir();
  const paths = cachePaths(cacheDir, target);
  let artifact = null;
  try {
    ({ artifact } = await resolveLocalArtifact(options, target));
  } catch {
    // An unusable manifest says nothing about what is on disk, and this
    // function's whole job is to look at what is on disk. Nor is the release
    // asked: "where would a launch get its browser right now, without
    // downloading anything" must not itself become a network round trip.
    artifact = null;
  }
  const cached = await validCachedInstall(paths, target, artifact);
  if (cached) return answer(cached, "cache", CHROMIUM_VERSION, paths.install);

  const scan = await scanWellKnown(target, options.searchRoots);
  rejected.push(...scan.rejected);
  return { order, searched: scan.searched, found: scan.found, rejected };
}

// The browser a launch would use without downloading, or null.
export async function discoverBinary(options = {}) {
  return (await discoveryReport(options)).found;
}


export async function ensureBinary(options = {}) {
  if (typeof options === "string") options = { binaryPath: options };
  if (!isObject(options)) throw new TypeError("ensureBinary options must be an object.");
  const configured = options.binaryPath ?? options.executablePath;
  const explicitBinary = configured ?? process.env.APOSTATE_BINARY;
  if (explicitBinary !== undefined && explicitBinary !== null && explicitBinary !== "") {
    // The target is left unresolved here on purpose. Only a named DIRECTORY
    // needs it, and resolveNamedBinary asks for it then.
    const named = await resolveNamedBinary(explicitBinary, options.target ? normalizeTarget(options.target) : null);
    if (named.executable) return named.executable;
    const label = configured ? "the configured path" : "APOSTATE_BINARY";
    throw new MissingBinaryError(`${label} ${named.reason}: ${named.path}`, { path: named.path });
  }
  const target = normalizeTarget(options.target);
  const cacheDir = options.cacheDir ?? options.cache_dir ?? defaultCacheDir();
  const paths = cachePaths(cacheDir, target);
  // Resolved locally first, and not acted on yet. A manifest that describes
  // no artifact for this target is a reason not to download; it is not a
  // reason to ignore a browser already sitting on the disk, and raising here
  // used to make an unpublished manifest look like "no browser anywhere" to
  // a user who had one installed.
  //
  // The release itself is asked only below, once downloading is the sole
  // remaining route. A machine with a valid cache never touches the network.
  let local = null;
  let unavailable = null;
  try {
    local = await resolveLocalArtifact(options, target);
  } catch (error) {
    // Only a publication answer is deferred. Anything else is a fault in the
    // lookup itself and must not be turned into a silent fallback.
    if (!(error instanceof ApostateError)) throw error;
    unavailable = error;
  }
  if (!options.force) {
    const cached = await validCachedInstall(paths, target, local?.artifact ?? null);
    if (cached) return cached;
    // Skipped under force for the same reason the cache is: force means
    // reinstall, and a browser found elsewhere is not this install.
    const discovered = await scanWellKnown(target, options.searchRoots);
    if (discovered.found) return discovered.found.executable;
  }
  if (unavailable) throw unavailable;
  let { manifest, artifact } = local;
  if (!artifact) {
    if (!local.remote_available) throw localRefusal(manifest, target);
    const fetched = await fetchReleaseArtifact(options, target);
    if (!fetched.artifact) throw unpublishedArtifactError(target, fetched.tried);
    ({ manifest, artifact } = fetched);
    // Said on the acquisition that actually used the fallback: silence would
    // leave an operator believing the digest was pinned when it was not.
    console.warn(`\x1b[33m[Apostate] release manifest fetched from ${fetched.url} (no published manifest is baked into this package). A fetched manifest verifies transport integrity only; for provenance run: gh attestation verify ${artifact.artifact} --repo ${RELEASE_REPOSITORY}\x1b[0m`);
  }
  let archive;
  try {
    const local = artifact.path ?? artifact.local_path ?? artifact.file;
    if (local) {
      archive = await readFile(resolve(String(local)));
    } else {
      archive = await downloadUrl(artifactUrlFor(manifest, artifact), options, "binary artifact");
    }
  } catch (error) {
    if (error instanceof ApostateError) throw error;
    throw new BinaryDownloadError(`Unable to obtain ${artifact.artifact}: ${sanitizeErrorMessage(error?.message ?? error, normalizeProxy(options.proxy))}.`, { proxy: redactProxy(options.proxy) });
  }
  // Nothing below opens the archive until this returns.
  await verifyArtifact(archive, artifact);
  await mkdir(paths.root, { recursive: true, mode: 0o700 });
  const temporary = await mkdtemp(join(paths.root, ".extract-"));
  try {
    const extract = options.extract ?? defaultExtract;
    const result = await extract(archive, temporary, {
      artifact: artifact.artifact,
      target,
      manifest,
      proxy: normalizeProxy(options.proxy),
      proxy_redacted: redactProxy(options.proxy),
    });
    // The extractor's contract is a DIRECTORY holding the whole distribution,
    // not a path to the executable: Chromium cannot run without its framework
    // and resources, so there is no single file to return.
    const extracted = typeof result === "string" ? resolve(temporary, result) : join(temporary, "tree");
    if (!withinDirectory(temporary, extracted)) {
      throw new BinaryExtractionError("Extractor returned a path outside the extraction directory.");
    }
    if (!(await lstat(extracted).then((info) => info.isDirectory(), () => false))) {
      throw new BinaryExtractionError("Extractor must return the directory holding the extracted distribution.");
    }
    const tree = await hoistSingleRoot(extracted, artifact.artifact);
    const relativeExecutable = await findExtractedBinary(tree, artifact.binary_path ?? artifact.binaryPath, target);
    const executable = join(tree, relativeExecutable);
    if (target !== "windows-x64") await chmod(executable, 0o755);
    const executableSha256 = createHash("sha256").update(await readFile(executable)).digest("hex");
    await replaceTree(tree, paths.install);
    if (["1", "true", "yes", "on"].includes(String(process.env.APOSTATE_KEEP_ARCHIVE ?? "").trim().toLowerCase())) {
      // Retained only on request: `gh attestation verify` needs the archive,
      // but keeping 150 MB beside a 600 MB install by default is not a cost a
      // daily user should pay.
      await writeFile(paths.archive, archive, { mode: 0o600 });
    }
    await writeFile(paths.metadata, `${stableStringify({
      artifact: artifact.artifact,
      artifact_sha256: artifact.sha256.toLowerCase(),
      catalogue_version: CATALOGUE_VERSION,
      chromium_version: CHROMIUM_VERSION,
      executable: relativeExecutable,
      executable_sha256: executableSha256,
      format: INSTALL_FORMAT,
      package_version: PACKAGE_VERSION,
      platform: target,
    })}\n`, { mode: 0o600 });
    // Provisioning is an optional extra; a browser that launches without DRM
    // is far better than no browser at all, so a failure here is not fatal.
    await applyWidevine(paths.install, target, cacheDir, CHROMIUM_VERSION).catch(() => null);
    return join(paths.install, relativeExecutable);
  } catch (error) {
    if (error instanceof ApostateError) throw error;
    throw new BinaryExtractionError(`Unable to extract ${artifact.artifact}: ${sanitizeErrorMessage(error?.message ?? error, options.proxy)}.`, { target });
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
}

// The one caller allowed on the network.
//
// `info` exists to explain where a browser would come from, and after this
// release that includes which of the two release URLs answered -- a question
// no local file can settle. It never raises over it: a lookup that fails is
// reported as a failure, because an unreachable release is a fact about the
// machine and this function's job is to state facts about the machine.
export async function binaryInfo(options = {}) {
  if (!isObject(options)) throw new TypeError("binaryInfo options must be an object.");
  const target = normalizeTarget(options.target);
  const cacheDir = options.cacheDir ?? options.cache_dir ?? defaultCacheDir();
  const paths = cachePaths(cacheDir, target);
  let resolved;
  try {
    // Shorter than the acquisition timeout on purpose: `info` is something a
    // person waits on, and two unreachable URLs at 30s each is a minute of
    // silence before a diagnostic.
    resolved = await resolveArtifact({ downloadTimeoutMs: 15000, ...options }, target);
  } catch (error) {
    resolved = { manifest: {}, artifact: null, source: null, url: null, tried: [], failures: [{ url: null, reason: error?.message ?? String(error) }], trust: null };
  }
  const { manifest, artifact } = resolved;
  const discovery = await discoveryReport(options);
  // `cache_hit` still means this package's own install is valid, which is a
  // narrower question than where a launch would get its browser: a configured
  // path wins over a perfectly good cache, and discovery stops before looking
  // at it. Reuse the answer when discovery already computed it -- validating
  // an install re-hashes the executable, and doing that twice per `info` is a
  // few hundred milliseconds of nothing on a 200 MB binary.
  const cached = discovery.found?.source === "cache"
    ? discovery.found.executable
    : await validCachedInstall(paths, target, artifact);
  const failures = resolved.failures ?? [];
  return {
    package_version: PACKAGE_VERSION,
    chromium_version: CHROMIUM_VERSION,
    catalogue_version: CATALOGUE_VERSION,
    target,
    platform: target,
    artifact: artifact?.artifact ?? expectedArtifactName(target),
    artifact_url: artifact ? artifactUrlFor(manifest, artifact) : null,
    sha256: artifact?.sha256 ?? null,
    // Where the digest came from, and how far that lets it be trusted.
    manifest_source: resolved.source,
    manifest_url: resolved.url ?? null,
    manifest_urls_tried: resolved.tried ?? [],
    manifest_trust: resolved.trust,
    manifest_note: resolved.trust === "pinned" ? MANIFEST_NOTE_PINNED : (resolved.trust === "transport-integrity" ? MANIFEST_NOTE_FETCHED : null),
    reason: failures.length ? failures.map((failure) => (failure.url ? `${failure.url}: ${failure.reason}` : failure.reason)).join("; ") : null,
    provenance: `gh attestation verify ${expectedArtifactName(target)} --repo ${RELEASE_REPOSITORY}`,
    cache_dir: resolve(cacheDir),
    install_dir: paths.install,
    // Where a launch would get its browser right now, with no download. On a
    // machine whose only copy is this package's own install, unchanged.
    executable: discovery.found?.executable ?? null,
    executable_source: discovery.found?.source ?? null,
    cache_hit: cached !== null,
    archive_retained: await isRegularFile(paths.archive),
    available: Boolean(artifact),
    discovery,
  };
}

export async function clearCache(options = {}) {
  if (typeof options === "string") options = { cacheDir: options };
  if (!isObject(options)) throw new TypeError("clearCache options must be an object.");
  const cacheDir = options.cacheDir ?? options.cache_dir ?? defaultCacheDir();
  let path = resolve(cacheDir);
  if (options.target !== undefined && options.target !== null) path = join(path, CHROMIUM_VERSION, normalizeTarget(options.target));
  else if (options.version !== undefined && options.version !== null) path = join(path, String(options.version));
  await rm(path, { recursive: true, force: true });
}

function waitForExit(child, timeoutMs = 5000) {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve();
  const { promise, resolve: settle } = Promise.withResolvers();
  let finished = false;
  const finish = () => {
    if (finished) return;
    finished = true;
    clearTimeout(timer);
    settle();
  };
  const timer = setTimeout(finish, timeoutMs);
  child.once("exit", finish);
  return promise;
}

function spawnBrowser(binary, args, options) {
  const { promise, resolve: settle, reject: fail } = Promise.withResolvers();
  const launchError = (error) => new BrowserLaunchError(
    `Unable to launch Apostate Chromium: ${sanitizeErrorMessage(error?.message ?? error, options.proxy)}.`,
    { proxy: redactProxy(options.proxy) },
  );
  let child;
  try {
    child = spawn(binary, args, {
      cwd: options.cwd,
      env: options.env ? { ...process.env, ...options.env } : process.env,
      stdio: options.stdio ?? "ignore",
    });
  } catch (error) {
    fail(launchError(error));
    return promise;
  }
  let settled = false;
  child.once("error", (error) => {
    if (settled) return;
    settled = true;
    fail(launchError(error));
  });
  child.once("spawn", () => {
    if (settled) return;
    settled = true;
    settle(child);
  });
  return promise;
}

// A bare child process with no protocol client, for callers that drive the
// browser some other way or only want it on screen. `launch()` returns a real
// Playwright or Puppeteer browser instead.
export class ApostateProcess {
  constructor(child, executablePath, launchConfig, diagnostics) {
    this.process = child;
    this.executablePath = executablePath;
    this.launchConfig = launchConfig;
    this.diagnostics = diagnostics;
    this._closed = false;
  }

  isConnected() {
    return !this._closed && this.process.exitCode === null && this.process.signalCode === null && !this.process.killed;
  }

  async close() {
    if (this._closed) return;
    this._closed = true;
    if (this.process.exitCode === null && this.process.signalCode === null) {
      this.process.kill("SIGTERM");
      await waitForExit(this.process);
      if (this.process.exitCode === null && this.process.signalCode === null) this.process.kill("SIGKILL");
    }
  }
}

// Driver preference order, Patchright first. Stated at the strength it has been
// measured to: the browser owns what a page can observe about the browser, and a
// driver's remaining job is to avoid CREATING artifacts -- main-world
// addInitScript/exposeFunction bindings, Runtime.addBinding, evaluation-script
// names in stack traces, its automation argv. Patchright is the hardened fork of
// that family.
//
// What is NOT claimed: that Patchright beats Playwright here. Nobody has
// measured it on this project, and plain Playwright measured clean on both
// artifacts expected to separate them. Nor is patch 0087's neutralisation of
// Runtime.enable settled: it has compiled and never run.
//
// Puppeteer is last on a measured basis: its stack traces from driver-evaluated
// code carry the operator's absolute filesystem path, and exposeFunction installs
// a puppeteer___-prefixed global alongside the requested name.
export const DRIVERS = ["patchright", "playwright", "playwright-core", "puppeteer", "puppeteer-core"];
const DRIVER_MODULES = DRIVERS;
// Patchright is a real dependency, so reaching this hint means a partial or
// pruned install rather than a step the user skipped -- worth saying, because
// the old message told them to run an install they had already run.
const DRIVER_HINT = "no Playwright-compatible or Puppeteer driver is installed. Patchright is a\n"
  + "dependency of this package, so this is a partial install. Repair it:\n"
  + "    npm install patchright\n"
  + "Or use an alternative: npm install playwright-core, or puppeteer-core\n"
  + "None of them needs to download a browser: Apostate supplies its own.";

async function loadDriver(requested, injected) {
  if (injected) {
    // Test seam: lets the Puppeteer and Playwright branches be exercised without
    // installing five optional peers in CI.
    const api = injected?.default ?? injected;
    const chromium = api?.chromium ?? injected?.chromium;
    if (chromium && typeof chromium.launch === "function") return { name: requested ?? "injected", kind: "playwright", chromium };
    if (typeof api?.launch === "function") return { name: requested ?? "injected", kind: "puppeteer", puppeteer: api };
    throw new BrowserLaunchError("Injected driver exposes neither chromium.launch nor launch.");
  }
  if (requested !== undefined && requested !== null && !DRIVERS.includes(requested)) {
    throw new BrowserLaunchError(`Unknown driver ${JSON.stringify(requested)}; supported: ${DRIVERS.join(", ")}.`, { driver: requested });
  }
  const names = requested ? [requested] : DRIVER_MODULES;
  const failures = [];
  for (const name of names) {
    try {
      // Dynamic by necessity: every driver is an OPTIONAL peer that the user
      // chooses and that is normally absent. A static import would make this
      // package hard-depend on all five, fail to resolve at load time when any
      // is missing, and force a browser download that Apostate supplies
      // itself. The specifier comes from a fixed registry, not user input.
      const loaded = await import(name);
      const api = loaded?.default ?? loaded;
      // Playwright exposes `chromium.launch`; Puppeteer exposes `launch`.
      const chromium = api?.chromium ?? loaded?.chromium;
      if (chromium && typeof chromium.launch === "function") return { name, kind: "playwright", chromium };
      if (typeof api?.launch === "function") return { name, kind: "puppeteer", puppeteer: api };
      failures.push(`${name} exposes neither chromium.launch nor launch`);
    } catch (error) {
      if (error?.code !== "ERR_MODULE_NOT_FOUND" && error?.code !== "MODULE_NOT_FOUND") {
        failures.push(`${name}: ${error?.message ?? error}`);
      }
    }
  }
  throw new BrowserLaunchError(failures.length > 0 ? `${DRIVER_HINT}\n${failures.join("\n")}` : DRIVER_HINT);
}

// A silent driver choice makes a support conversation impossible, so the
// selection is inspectable without starting a browser.
export async function driverInfo() {
  const installed = [];
  for (const name of DRIVERS) {
    try {
      // Same runtime-registry reason as loadDriver: every driver is an optional
      // peer and normally absent.
      await import(name);
      installed.push(name);
    } catch { /* not installed */ }
  }
  return {
    preference_order: [...DRIVERS],
    installed,
    selected: installed[0] ?? null,
    recommended: DRIVERS[0],
  };
}

function playwrightProxy(proxy) {
  if (!proxy) return undefined;
  const parsed = new URL(proxy);
  const result = { server: proxyEndpoint(proxy) };
  // SOCKS credentials are withheld from the driver on purpose.
  //
  // The browser already has them: buildLaunchArguments puts them in the
  // --apostate-profile envelope, which is the route that keeps a credential
  // out of NetLog, socket-pool group keys and error strings (docs/FLAGS.md,
  // "The proxy"). Playwright, meanwhile, refuses to start at all when a
  // socks5 server carries a username -- "Browser does not support socks5
  // proxy authentication" -- because upstream Chromium has no way to supply
  // one. Handing the driver a credential it will not use, and failing a
  // launch the browser can serve, is the worst of both.
  if (parsed.protocol.startsWith("socks")) return result;
  if (parsed.username) result.username = decodeURIComponent(parsed.username);
  if (parsed.password) result.password = decodeURIComponent(parsed.password);
  return result;
}

// Stop the driver's default viewport overwriting the composed geometry.
// Playwright's default context is 1280x720 and reports screen == inner == avail
// with devicePixelRatio flattened to 1. No real desktop has avail == screen:
// there is always a menu bar or a taskbar. Puppeteer's default is worse --
// outer 756x556 against inner 800x600, an inner viewport larger than the window
// containing it, which no machine reports.
//
// Measured on the shipped macos-arm64 build with --fingerprint=42:
//   Playwright default          screen/inner/avail all 1280x720, dpr 1
//   Playwright viewport null    screen 1710x1112, avail 1710x1079, dpr 2
//   Puppeteer default           outer 756x556, inner 800x600, avail==screen, dpr 1
//   Puppeteer defaultViewport null  outer 756x556, inner 756x469, avail<screen, dpr 2
//
// Three observables per driver, plus the profile's own geometry. A caller who
// asks for a viewport still gets it.
function coherentViewport(target) {
  for (const name of ["newPage", "newContext"]) {
    const original = target?.[name];
    if (typeof original !== "function") continue;
    target[name] = async (options = {}, ...rest) => {
      const merged = isObject(options) && !("viewport" in options)
        ? { ...options, viewport: null }
        : options;
      return original.call(target, merged, ...rest);
    };
  }
  return target;
}

// launchContext returns a context, not the browser it came from, and closing a
// non-persistent context does not close its browser. Without this the browser
// and its driver outlive the context.
function contextOwnsBrowser(context, browser) {
  const original = context?.close;
  if (typeof original !== "function") return context;
  context.close = async (...args) => {
    try {
      return await original.call(context, ...args);
    } finally {
      await browser.close().catch(() => {});
    }
  };
  context.apostateBrowser = browser;
  return context;
}

// The publication question, asked only while its answer can still change the
// outcome. Publication decides whether there is anything to download; a
// browser already on disk means there is nothing to download, so the answer
// stops mattering and refusing the launch over it would be refusing a launch
// that would have worked.
//
// It is also asked only while it is still free. The point of running this
// before the driver check is that a local manifest lookup costs nothing; the
// moment the answer moves to the release, ensureBinary() is the one that
// goes and gets it, and asking here too would buy a second round trip to
// reorder two error messages.
async function assertPublishedOrDiscoverable(options, target) {
  let local;
  try {
    local = await resolveLocalArtifact(options, target);
  } catch (error) {
    if (!(error instanceof ApostateError)) throw error;
    if (await discoverBinary(options) === null) throw error;
    return;
  }
  if (local.artifact || local.remote_available) return;
  if (await discoverBinary(options) !== null) return;
  throw localRefusal(local.manifest, target);
}

// Returns a real browser object from whichever driver is installed: a
// Playwright `Browser` (newPage, newContext, close) or a Puppeteer `Browser`.
// An existing Playwright or Puppeteer script works by changing only the import.
export async function launch(options = {}) {
  if (!isObject(options)) throw new TypeError("Launch options must be an object.");
  const prepared = await prepareLaunch(options);
  // Three checks, cheapest first, and the order is the whole point.
  //
  // Publication is a local manifest lookup, so it comes first: on a release
  // that ships no binary for this target, loading the driver first told the
  // user to `npm install puppeteer-core` when the real blocker was that
  // there is nothing to install yet. That is a first-run experience no
  // developer machine can reproduce, because a driver is always lying around
  // in node_modules by the time anyone looks.
  //
  // The driver check still precedes acquisition, which is the ~150 MB
  // download: failing after that with "no driver installed" spends the
  // user's bandwidth to tell them something knowable up front.
  const explicitBinary = options.executablePath ?? options.binaryPath;
  // APOSTATE_BINARY is resolved inside ensureBinary, so a configured binary
  // by any route means there is no publication question to ask. Neither is
  // there one when a browser is already installed somewhere this package
  // knows to look: an unpublished release cannot stop a launch that needs to
  // download nothing.
  if (!(explicitBinary ?? process.env.APOSTATE_BINARY)) {
    await assertPublishedOrDiscoverable(options, normalizeTarget(options.target));
  }
  const driver = await loadDriver(options.driver, options._driverModule);
  // Always through ensureBinary, even for a named path: that is where a
  // bundle or a payload root is turned into the executable inside it, and
  // handing the driver a directory only moves the failure somewhere it
  // cannot be explained.
  const binary = await ensureBinary(options);
  const args = buildLaunchArguments(prepared.config, prepared.resolution, { driverOwnsProfile: true });
  const env = {
    // Set, not inherited: only host mode inherits the host's locale
    // environment. See localeEnvironment.
    ...localeEnvironment(prepared.config, prepared.resolution),
    ...(prepared.config.timezone ? { TZ: prepared.config.timezone } : {}),
    ...(options.env ?? {}),
  };
  // The seed and persona switches already carry the identity, so `headless` is
  // the only launch flag the driver owns; everything else is in `args`.
  const common = {
    executablePath: binary,
    headless: prepared.config.headless,
    args,
    env: { ...process.env, ...env },
    ignoreDefaultArgs: ignoreDefaultArgsFor(options.ignoreDefaultArgs, prepared.config.args),
  };
  try {
    if (driver.kind === "playwright") {
      const proxy = playwrightProxy(prepared.config.proxy);
      const browser = prepared.config.user_data_dir
        ? await driver.chromium.launchPersistentContext(prepared.config.user_data_dir, { ...common, viewport: null, ...(proxy ? { proxy } : {}) })
        : coherentViewport(await driver.chromium.launch({ ...common, ...(proxy ? { proxy } : {}) }));
      browser.apostateDiagnostics = prepared.diagnostics;
      browser.apostateExecutablePath = binary;
      browser.apostateDriverName = driver.name;
      return browser;
    }
    const browser = await driver.puppeteer.launch({
      ...common,
      // null rather than Puppeteer's 800x600 default, which reports an inner
      // viewport larger than its own window. See coherentViewport above.
      defaultViewport: options.defaultViewport ?? null,
      // Puppeteer takes the user data dir as an option, not a switch.
      ...(prepared.config.user_data_dir ? { userDataDir: prepared.config.user_data_dir } : {}),
    });
    browser.apostateDiagnostics = prepared.diagnostics;
    browser.apostateExecutablePath = binary;
    browser.apostateDriverName = driver.name;
    return browser;
  } catch (error) {
    if (error instanceof ApostateError) throw error;
    throw new BrowserLaunchError(`Unable to launch Apostate Chromium through ${driver.name}: ${sanitizeErrorMessage(error?.message ?? error, prepared.config.proxy)}.`, { driver: driver.name, proxy: redactProxy(prepared.config.proxy) });
  }
}

// The raw child process, with no protocol client. Use this when no driver is
// installed and you only need the browser running.
export async function launchProcess(options = {}) {
  if (!isObject(options)) throw new TypeError("Launch options must be an object.");
  const prepared = await prepareLaunch(options);
  const binary = await ensureBinary(options);
  const args = buildLaunchArguments(prepared.config, prepared.resolution);
  const env = {
    // Set, not inherited: only host mode inherits the host's locale
    // environment. See localeEnvironment.
    ...localeEnvironment(prepared.config, prepared.resolution),
    ...(prepared.config.timezone ? { TZ: prepared.config.timezone } : {}),
    ...(options.env ?? {}),
  };
  const child = await spawnBrowser(binary, args, { ...options, env });
  return new ApostateProcess(child, binary, prepared.config, prepared.diagnostics);
}

export async function launchContext(options = {}) {
  const browser = await launch(options);
  try {
    // A Playwright persistent context is already a context; Puppeteer has none.
    if (typeof browser.newContext === "function") {
      return contextOwnsBrowser(await browser.newContext(), browser);
    }
    if (typeof browser.createBrowserContext === "function") {
      return contextOwnsBrowser(await browser.createBrowserContext(), browser);
    }
    return browser;
  } catch (error) {
    await browser.close();
    throw error;
  }
}

export async function launchPersistentContext(userDataDir, options = {}) {
  if (typeof userDataDir !== "string" || userDataDir.length === 0) throw new TypeError("userDataDir must be a non-empty path string.");
  if (!isObject(options)) throw new TypeError("Launch options must be an object.");
  if (options.userDataDir !== undefined && resolve(options.userDataDir) !== resolve(userDataDir)) {
    throw new TypeError("launchPersistentContext received conflicting userDataDir values.");
  }
  await mkdir(resolve(userDataDir), { recursive: true });
  return launch({ ...options, userDataDir });
}

// Playwright passes this by default; Patchright and Puppeteer do not.
// chrome/browser/chrome_browser_main.cc wraps the whole
// RegisterComponentsForUpdate() call in a check for it, and that function is the
// only caller of ComponentInstaller::Register, which is the only path to
// FindPreinstallation. So it does not merely stop downloads -- it stops an
// already-present component from ever being REGISTERED.
//
// Two reasons to drop it. A provisioned Widevine CDM is silently dead with it
// set, which is the exact "told you DRM works, it does not" failure that
// provisioning exists to remove. And the artifact ships preinstalled components
// in Libraries/: MEIPreload and PrivacySandboxAttestationsPreloaded register on
// every launch, IwaKeyDistribution is feature-gated and does not. The switch
// gates the entire RegisterComponentsForUpdate() call, so it suppresses all of
// them at once, and a real Chrome has them registered. Suppressing registration
// is therefore itself a divergence from the browser being imitated,
// independent of DRM.
//
// Measured on the provisioned install, offline, counting the browser's own
// "Component ready" lines: without the switch, MEIPreload 1.0.7.1652906823,
// PrivacySandboxAttestationsPreloaded 2025.7.18.0 and WidevineCdm 4.10.3050.0
// register; with it, zero components register at all.
//
// Measured on macos-arm64 152.0.7977.83 with a provisioned CDM and no network:
// through Patchright the empty robustness level resolved; through Playwright,
// same install and same code, it rejected NotSupportedError.
const DISABLE_COMPONENT_UPDATE = "--disable-component-update";

// A switch the caller put in args themselves is honoured; only the driver's
// injected default is removed.
function ignoreDefaultArgsFor(requested, args) {
  if (hasSwitch(args, DISABLE_COMPONENT_UPDATE)) return requested;
  if (requested === true) return requested;
  if (requested === undefined || requested === null || requested === false) return [DISABLE_COMPONENT_UPDATE];
  const merged = Array.isArray(requested) ? [...requested] : [String(requested)];
  if (!merged.includes(DISABLE_COMPONENT_UPDATE)) merged.push(DISABLE_COMPONENT_UPDATE);
  return merged;
}

// --- Widevine DRM provisioning -------------------------------------------
//
// Chromium fetches the Widevine CDM from Google at runtime into the profile
// directory, and Apostate cannot ship it -- third_party/widevine/LICENSE
// forbids redistribution. An ephemeral profile, which is what launch() uses by
// default, therefore has no CDM, and requestMediaKeySystemAccess rejects with
// NotSupportedError where a real user's Chrome resolves. A site reads that in
// one call.
//
// A CDM placed in the browser's preinstalled-component directory registers at
// startup for every profile, including a fresh ephemeral one, with no network
// and without writing into the profile. That is the directory the artifact
// already loads MEIPreload from, and where Google Chrome keeps its own copy,
// in the same layout with no version subdirectory.
//
// Nothing is redistributed: the bytes travel from Google to the operator's
// machine exactly as they do for Chrome, and this only copies a file already
// on that machine. It is deliberately NOT part of launch(): on a machine with
// no CDM there is nothing to copy, and a silent no-op would leave a caller
// believing DRM works. Measured working on macos-arm64 only; the Linux and
// Windows layouts come from the documented component paths and are unverified.
const WIDEVINE_COMPONENT = "WidevineCdm";
const WIDEVINE_LAYOUT = {
  "macos-arm64": { subdir: "mac_arm64", library: "libwidevinecdm.dylib", relative: "Chromium.app/Contents/Frameworks/Chromium Framework.framework/Versions/{version}/Libraries" },
  "linux-x64": { subdir: "linux_x64", library: "libwidevinecdm.so", relative: "" },
  "linux-arm64": { subdir: "linux_arm64", library: "libwidevinecdm.so", relative: "" },
  "windows-x64": { subdir: "win_x64", library: "widevinecdm.dll", relative: "" },
};
const WIDEVINE_VERIFIED_TARGETS = { "macos-arm64": true };

// Shared with the pip package on purpose: same cache root, same store, so
// provisioning once serves both.
function widevineStore(cacheDir) {
  return join(resolve(cacheDir), WIDEVINE_COMPONENT.toLowerCase());
}

async function copyWidevine(source, destination, subdir, library) {
  if (!(await isRegularFile(join(source, "_platform_specific", subdir, library)))) {
    throw new WidevineError(`${source} does not contain _platform_specific/${subdir}/${library}.`);
  }
  const staged = `${destination}.part`;
  await rm(staged, { recursive: true, force: true });
  await mkdir(join(staged, "_platform_specific"), { recursive: true });
  for (const name of ["manifest.json", "LICENSE"]) {
    if (await isRegularFile(join(source, name))) await copyFile(join(source, name), join(staged, name));
  }
  if (!(await isRegularFile(join(staged, "manifest.json")))) {
    await rm(staged, { recursive: true, force: true });
    throw new WidevineError(`${source} has no manifest.json; it is not a CDM directory.`);
  }
  await cp(join(source, "_platform_specific", subdir), join(staged, "_platform_specific", subdir), { recursive: true });
  await rm(destination, { recursive: true, force: true });
  await rename(staged, destination);
}

// Re-applied after every extraction: `--force` and a Chromium upgrade both
// replace the install tree, and DRM must not silently vanish when they do.
async function applyWidevine(install, target, cacheDir, version) {
  const layout = WIDEVINE_LAYOUT[target];
  if (!layout) return null;
  const store = widevineStore(cacheDir);
  if (!(await isRegularFile(join(store, "_platform_specific", layout.subdir, layout.library)))) return null;
  // Do not conjure an install tree. Writing into a path that holds no browser
  // would leave an orphan directory that looks installed and is not.
  if (!(await lstat(install).then((i) => i.isDirectory(), () => false))) return null;
  const destination = join(install, layout.relative.replace("{version}", version), WIDEVINE_COMPONENT);
  await copyWidevine(store, destination, layout.subdir, layout.library);
  return destination;
}

export async function provisionWidevine(options = {}) {
  if (!isObject(options)) throw new TypeError("provisionWidevine options must be an object.");
  const target = normalizeTarget(options.target);
  const layout = WIDEVINE_LAYOUT[target];
  if (!layout) throw new WidevineError(`No Widevine layout is known for ${target}.`, { target });
  const source = options.source;
  if (!source) {
    throw new WidevineError(
      "provisionWidevine needs source: a WidevineCdm directory already on this machine. "
      + "The pip package can find one for you (`python -m apostate provision-drm --list`); "
      + "Chromium writes it into a persistent --user-data-dir after playing DRM video once.",
      { target });
  }
  const cacheDir = options.cacheDir ?? options.cache_dir ?? defaultCacheDir();
  let chosen = resolve(String(source));
  if (!(await isRegularFile(join(chosen, "_platform_specific", layout.subdir, layout.library)))) {
    // The component updater writes a version directory; a bundle has none.
    const entries = await readdir(chosen, { withFileTypes: true }).catch(() => []);
    const versioned = entries.filter((e) => e.isDirectory()).map((e) => e.name).sort().reverse();
    let found = null;
    for (const name of versioned) {
      if (await isRegularFile(join(chosen, name, "_platform_specific", layout.subdir, layout.library))) {
        found = join(chosen, name);
        break;
      }
    }
    if (!found) throw new WidevineError(`${chosen} does not contain _platform_specific/${layout.subdir}/${layout.library}.`, { target });
    chosen = found;
  }
  const store = widevineStore(cacheDir);
  await mkdir(dirname(store), { recursive: true });
  await copyWidevine(chosen, store, layout.subdir, layout.library);
  const manifestVersion = JSON.parse(await readFile(join(store, "manifest.json"), "utf8")).version ?? null;
  const paths = cachePaths(cacheDir, target);
  const installed = await applyWidevine(paths.install, target, cacheDir, CHROMIUM_VERSION);
  return {
    platform: target,
    platform_verified: WIDEVINE_VERIFIED_TARGETS[target] === true,
    source: chosen,
    version: manifestVersion,
    store,
    installed,
    // A CDM being present is not a CDM being registered. launch() strips the
    // switch, but anyone driving the binary directly must too.
    requires: `the browser must not run with ${DISABLE_COMPONENT_UPDATE}; it blocks component registration and a provisioned CDM is silently inert. launch() removes it from the driver's defaults automatically.`,
  };
}
export const provision_widevine = provisionWidevine;

// Python-style names are useful when sharing launch code across wrappers.
export const launch_context = launchContext;
export const launch_persistent_context = launchPersistentContext;
export const launch_process = launchProcess;
export const ensure_binary = ensureBinary;
export const binary_info = binaryInfo;
export const discover_binary = discoverBinary;
export const discovery_report = discoveryReport;
export const clear_cache = clearCache;
export const translateOptions = toCanonicalLaunchConfig;
export const load_catalogue = loadCatalogue;
