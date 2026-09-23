import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { createServer, request as httpRequest } from "node:http";
import { createServer as createSocket } from "node:net";
import { chmod, lstat, mkdir, readFile, readdir, readlink, writeFile } from "node:fs/promises";
import { mkdtemp, rm } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { tmpdir } from "node:os";
import * as zlib from "node:zlib";
import test from "node:test";
import {
  BinaryExtractionError,
  CATALOGUE_VERSION,
  CHROMIUM_VERSION,
  PACKAGE_VERSION,
  ProfileResolutionError,
  UnpublishedArtifactError,
  binaryInfo,
  discoveryReport,
  ensureBinary,
  ensureWidevine,
  extractionFailureMessage,
  expectedArtifactName,
  launch,
  launchProcess,
  launchContext,
  launchPersistentContext,
  provisionWidevine,
  WidevineError,
  loadCatalogue,
  resolveLaunchConfig,
  resolveProfile,
  toCanonicalLaunchConfig,
} from "../dist/index.js";

const target = "linux-x64";
const artifactNameFor = (platform) => expectedArtifactName(platform);
function releaseManifest(archive, platform = target) {
  const artifact = artifactNameFor(platform);
  return {
    package_version: "0.1.0",
    chromium_version: CHROMIUM_VERSION,
    catalogue_version: CATALOGUE_VERSION,
    platform,
    artifact,
    sha256: createHash("sha256").update(archive).digest("hex"),
    url: `https://example.invalid/${artifact}`,
  };
}

function crc32(value) {
  let crc = 0xffffffff;
  for (const byte of value) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function storedZip(entries) {
  const localParts = [];
  const centralParts = [];
  let offset = 0;
  for (const entry of entries) {
    const name = Buffer.from(entry.name, "utf8");
    const data = Buffer.from(entry.data ?? "", "utf8");
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt32LE(crc32(data), 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(name.length, 26);
    localParts.push(Buffer.concat([local, name, data]));
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(entry.mode ? (3 << 8) | 20 : 20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt32LE(crc32(data), 16);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(name.length, 28);
    central.writeUInt32LE(offset, 42);
    if (entry.mode) central.writeUInt32LE((entry.mode << 16) >>> 0, 38);
    centralParts.push(Buffer.concat([central, name]));
    offset += local.length + name.length + data.length;
  }
  const centralDirectory = Buffer.concat(centralParts);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(centralDirectory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...localParts, centralDirectory, end]);
}

function tarArchive(entries) {
  const chunks = [];
  const writeField = (header, value, start, length) => {
    const encoded = Buffer.from(String(value), "ascii");
    encoded.copy(header, start, 0, Math.min(encoded.length, length - 1));
  };
  for (const entry of entries) {
    const header = Buffer.alloc(512);
    writeField(header, entry.name, 0, 100);
    writeField(header, "0000777", 100, 8);
    writeField(header, "0000000", 108, 8);
    writeField(header, "0000000", 116, 8);
    const data = entry.type === "2" ? Buffer.alloc(0) : Buffer.from(entry.data ?? "", "utf8");
    writeField(header, data.length.toString(8).padStart(11, "0"), 124, 12);
    writeField(header, "00000000000", 136, 12);
    header.fill(0x20, 148, 156);
    header.write(entry.type ?? "0", 156, 1, "ascii");
    if (entry.linkname) writeField(header, entry.linkname, 157, 100);
    header.write("ustar\0", 257, 6, "ascii");
    writeField(header, "00", 263, 8);
    const checksum = [...header].reduce((sum, byte) => sum + byte, 0);
    writeField(header, checksum.toString(8).padStart(6, "0"), 148, 8);
    chunks.push(header, data);
    if (data.length % 512) chunks.push(Buffer.alloc(512 - (data.length % 512)));
  }
  chunks.push(Buffer.alloc(1024));
  return Buffer.concat(chunks);
}


const shippedCataloguePath = new URL("../assets/catalogue.json", import.meta.url);

async function catalogueFixture(root, mutate) {
  const catalogue = JSON.parse(await readFile(shippedCataloguePath, "utf8"));
  mutate(catalogue);
  const cataloguePath = join(root, "catalogue.json");
  await writeFile(cataloguePath, JSON.stringify(catalogue));
  return cataloguePath;
}


test("translates canonical launch fields and accepts stable string seeds", () => {
  const config = toCanonicalLaunchConfig({
    fingerprint: "seed:stable-01",
    fingerprintPlatform: "macos",
    userDataDir: "/tmp/apostate-profile",
    headless: false,
    args: ["--disable-gpu"],
    proxy: { server: "http://proxy.example:8080", username: "user", password: "secret" },
  });
  assert.equal(config.fingerprint, "seed:stable-01");
  assert.equal(config.fingerprint_platform, "macos");
  assert.equal(config.user_data_dir, "/tmp/apostate-profile");
  assert.equal(config.headless, false);
  assert.deepEqual(config.args, ["--disable-gpu"]);
  assert.match(config.proxy, /^http:\/\/user:secret@proxy\.example:8080\/$/);
  assert.throws(() => toCanonicalLaunchConfig({ fingerprint: "bad seed" }), /fingerprint/);
});
test("rejects humanize until native behavior exists", () => {
  assert.throws(
    () => toCanonicalLaunchConfig({ humanize: true }),
    /humanize is not implemented/,
  );
});
test("refuses a persistent profile on launch(), before touching a binary", async () => {
  // Two spellings, one contract shared with the Python package: launch()
  // returns a Browser, so userDataDir belongs to launchPersistentContext. The
  // switch form used to be filtered out of argv and the launch went ahead
  // ephemeral, so a caller expecting a persistent identity got a new machine
  // every run with nothing said. Both are checked at config time: the
  // executable path here names nothing, and neither error mentions it, which
  // is what proves the refusal fires before ensureBinary.
  await assert.rejects(
    launch({ executablePath: "/nonexistent/chrome", geoip: false, userDataDir: "/tmp/profile" }),
    (error) => error.code === "APOSTATE_USER_DATA_DIR_ON_LAUNCH"
      && /launchPersistentContext/.test(error.message) && !/nonexistent/.test(error.message),
  );
  for (const args of [["--user-data-dir=/tmp/profile"], ["--user-data-dir"]]) {
    await assert.rejects(
      launch({ executablePath: "/nonexistent/chrome", geoip: false, args }),
      (error) => error.code === "APOSTATE_USER_DATA_DIR_SWITCH_IN_ARGS"
        && /launchPersistentContext/.test(error.message) && !/nonexistent/.test(error.message),
    );
  }
});
test("refuses an unknown or misspelled fingerprint switch at config time", () => {
  // Chromium ignores an unknown switch silently, which leaves the surface
  // host-inherited while the caller believed it was set. A one-letter typo
  // of a known switch is the same failure and is named; a switch that is
  // neither is Chromium's business, and every real switch passes.
  assert.throws(() => toCanonicalLaunchConfig({ args: ["--fingerprint-gpu-vendr=Apple"] }),
    (error) => error.code === "APOSTATE_UNKNOWN_FINGERPRINT_SWITCH");
  assert.throws(() => toCanonicalLaunchConfig({ args: ["--fingeprint-platform=windows"] }),
    (error) => error.code === "APOSTATE_MISSPELLED_FINGERPRINT_SWITCH" && /typo of --fingerprint-platform/.test(error.message));
  for (const accepted of ["--fingerprint-noise", "--fingerprint-platform=windows", "--disable-http2", "--lang=en-US"]) {
    assert.deepEqual(toCanonicalLaunchConfig({ args: [accepted] }).args, [accepted]);
  }
});
test("refuses an off-looking --fingerprint-noise value because it turns noise on", () => {
  // The binary tests whether the switch is present, never what it is set to,
  // so --fingerprint-noise=false enables readback noise. A competitor's
  // documentation recommends that exact string.
  for (const spelling of ["--fingerprint-noise=false", "--fingerprint-noise=0", "--fingerprint-noise=off"]) {
    assert.throws(() => toCanonicalLaunchConfig({ args: [spelling] }),
      (error) => error.code === "APOSTATE_NOISE_SWITCH_READS_BACKWARDS");
  }
  assert.deepEqual(toCanonicalLaunchConfig({ args: ["--fingerprint-noise"] }).args, ["--fingerprint-noise"]);
});
test("loads the version 2 catalogue and reports its anchors, axes and policy ids", () => {
  const catalogue = loadCatalogue();
  assert.deepEqual(Object.keys(catalogue).sort(), [
    "anchors",
    "axes",
    "browser_build",
    "catalogue_version",
    "model",
    "policies",
    "profile_schema_version",
  ]);
  assert.equal(catalogue.catalogue_version, 2);
  assert.equal(catalogue.profile_schema_version, 3);
  assert.equal(catalogue.browser_build, CHROMIUM_VERSION);
  assert.equal(catalogue.model, "anchors+dispersion");
  assert.ok(catalogue.anchors.length > 0);
  for (const anchor of catalogue.anchors) {
    assert.deepEqual(Object.keys(anchor).sort(), ["backend", "id", "members", "platform", "rotation_status"]);
    assert.ok(["linux", "macos", "windows"].includes(anchor.platform));
    assert.ok(anchor.members.length > 0);
    assert.ok(anchor.members.every((member) => typeof member === "string" && member.length > 0));
  }
  assert.ok(catalogue.axes.length > 0);
  for (const axis of catalogue.axes) {
    assert.deepEqual(Object.keys(axis).sort(), ["axis", "conditioned_on", "option_sets", "options", "selection", "servability"]);
  }
  assert.equal(new Set(catalogue.axes.map((axis) => axis.axis)).size, catalogue.axes.length);
  assert.ok(catalogue.policies.locale.length > 0);
  assert.ok(catalogue.policies.theme.length > 0);
});

test("rejects a catalogue that disagrees with the package or still carries the retired model", async () => {
  const root = await mkdtemp(join(tmpdir(), "apostate-node-catalogue-v2-"));
  try {
    assert.equal(loadCatalogue(await catalogueFixture(root, () => {})).model, "anchors+dispersion");
    for (const mutate of [
      (catalogue) => { catalogue.catalogue_version = 1; },
      (catalogue) => { catalogue.profile_schema_version = 2; },
      (catalogue) => { catalogue.browser_build = "1.2.3.4"; },
      (catalogue) => { catalogue.model = "fixed-catalogue"; },
      (catalogue) => { delete catalogue.catalogue_id; },
      (catalogue) => { delete catalogue.anchors; },
      (catalogue) => { delete catalogue.axes; },
      (catalogue) => { catalogue.anchors[0].members = []; },
      (catalogue) => { catalogue.anchors[0].rotation_status = ""; },
      (catalogue) => { catalogue.axes[0].conditioned_on = "platform"; },
      (catalogue) => { catalogue.axes[0].options = 0; },
      (catalogue) => { catalogue.policies.gpu = [{ id: "unexpected" }]; },
      (catalogue) => { catalogue.families = [{ id: "retired" }]; },
      (catalogue) => { catalogue.family_count = 14; },
    ]) {
      const cataloguePath = await catalogueFixture(root, mutate);
      assert.throws(() => loadCatalogue(cataloguePath), ProfileResolutionError);
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("hands a seed or persona to the browser process instead of refusing", () => {
  // The switches exist in the shipped binary. Measured on macos-arm64
  // 152.0.7977.83: --fingerprint=42 yields en-GB / Europe/London and repeats
  // across launches; a bare launch draws a fresh identity each time.
  for (const options of [{}, { fingerprint: 12345 }, { fingerprintPlatform: "windows" },
                         { fingerprint: "seed:stable-01", fingerprintPlatform: "macos" }]) {
    const resolution = resolveProfile(options);
    assert.equal(resolution.source, "native-composed");
    assert.equal(resolution.profileId, "native-composed");
    // The package composes nothing, so it sends no envelope: an envelope
    // outranks the seed and would suppress the browser's own composition.
    assert.equal(resolution.profile, null);
  }
  // Only a bare launch warns, because only a bare launch rotates.
  assert.match(resolveProfile({}).warnings.join(" "), /does not persist/);
  assert.deepEqual(resolveProfile({ fingerprint: 12345 }).warnings, []);

  // Host inheritance composes nothing, so a persona cannot be honoured.
  assert.throws(() => resolveProfile({ fingerprint: "host", fingerprintPlatform: "windows" }), (error) => {
    assert.ok(error instanceof ProfileResolutionError);
    assert.equal(error.code, "APOSTATE_HOST_INHERITANCE_PERSONA");
    return true;
  });
  // Every spelling the binary accepts for host inheritance is accepted here.
  for (const token of ["host", "off", "false", "0", "disable", "DISABLED"]) {
    const host = resolveProfile({ fingerprint: token });
    assert.equal(host.profile, null);
    assert.equal(host.source, "host-inherited");
  }
  // Retired catalogue ids stay refused: there is no such thing to resolve.
  assert.throws(() => resolveProfile({ profileId: "retired-id" }), (error) => {
    assert.ok(error instanceof ProfileResolutionError);
    assert.equal(error.code, "APOSTATE_CATALOGUE_PROFILE_IDS_RETIRED");
    assert.match(error.message, /composes a profile from anchors and dispersion/);
    return true;
  });
});

test("rejects explicit profile and profile-file platform mismatches", async () => {
  const root = await mkdtemp(join(tmpdir(), "apostate-node-profile-platform-"));
  const profile = { id: "explicit", platform: { name: "Windows" } };
  try {
    assert.throws(
      () => resolveProfile({ profile, fingerprintPlatform: "macos" }),
      /does not match fingerprint platform/,
    );
    const profilePath = join(root, "explicit.json");
    await writeFile(profilePath, JSON.stringify(profile));
    assert.throws(
      () => resolveProfile({ profilePath, fingerprintPlatform: "macos" }),
      /does not match fingerprint platform/,
    );
    const matching = resolveProfile({ profile, fingerprintPlatform: "win32" });
    assert.equal(matching.profileId, "explicit");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("strips source_capture and applies timezone and WebRTC proxy policy", async () => {
  const root = await mkdtemp(join(tmpdir(), "apostate-node-source-capture-"));
  const argvPath = join(root, "argv.json");
  const executable = join(root, "capture-argv.mjs");
  const profile = { id: "explicit", source_capture: "capture-2026-09-13", platform: { name: "macOS" } };
  try {
    await writeFile(executable, "#!/usr/bin/env node\nimport { writeFileSync } from \"node:fs\";\nwriteFileSync(process.env.APOSTATE_ARGV_PATH, JSON.stringify({ argv: process.argv.slice(2), timezone: process.env.TZ }));\nsetTimeout(() => {}, 10000);\n");
    await chmod(executable, 0o755);
    const browser = await launchProcess({
      executablePath: executable,
      profile,
      fingerprintPlatform: "macos",
      timezone: "Asia/Karachi",
      proxy: "http://proxy.example:8080",
      args: ["--fingerprint-webrtc-ip=198.51.100.7"],
      geoip: false,
      headless: false,
      env: { APOSTATE_ARGV_PATH: argvPath },
    });
    try {
      let launchData;
      for (let attempt = 0; attempt < 40; attempt += 1) {
        try {
          launchData = JSON.parse(await readFile(argvPath, "utf8"));
          break;
        } catch (error) {
          if (attempt === 39) throw error;
          await new Promise((resolve) => setTimeout(resolve, 25));
        }
      }
      const argv = launchData.argv;
      const encoded = argv.find((value) => value.startsWith("--apostate-profile="));
      assert.ok(encoded);
      const payload = JSON.parse(Buffer.from(encoded.slice("--apostate-profile=".length), "base64").toString("utf8"));
      assert.equal(payload.device_profile, undefined);
      assert.equal(payload.id, "explicit");
      assert.equal(payload.source_capture, undefined);
      assert.equal(payload.locale.timezone, "Asia/Karachi");
      assert.equal(launchData.timezone, "Asia/Karachi");
      assert.equal(argv.includes("--fingerprint-webrtc-ip=198.51.100.7"), true);
      assert.equal(argv.includes("--force-webrtc-ip-handling-policy=disable_non_proxied_udp"), true);
      assert.equal(browser.launchConfig.profile.source_capture, profile.source_capture);
    } finally {
      await browser.close();
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("geoip resolves through an authenticated HTTP proxy without leaking credentials to argv", async () => {
  const root = await mkdtemp(join(tmpdir(), "apostate-node-geoip-proxy-"));
  const argvPath = join(root, "argv.json");
  const executable = join(root, "capture-argv.mjs");
  const targetServer = createServer((request, response) => {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ ip: "202.63.209.57", languages: "en-US,en", timezone: "UTC" }));
  });
  let sawProxyAuth = false;
  const proxyServer = createServer((request, response) => {
    sawProxyAuth = request.headers["proxy-authorization"] === `Basic ${Buffer.from("fixture-user:fixture-password").toString("base64")}`;
    const upstream = httpRequest(request.url, { headers: { accept: request.headers.accept ?? "" } }, (upstreamResponse) => {
      response.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers);
      upstreamResponse.pipe(response);
    });
    upstream.on("error", () => response.writeHead(502).end());
    upstream.end();
  });
  try {
    await new Promise((resolve) => targetServer.listen(0, "127.0.0.1", resolve));
    await new Promise((resolve) => proxyServer.listen(0, "127.0.0.1", resolve));
    await writeFile(executable, "#!/usr/bin/env node\nimport { writeFileSync } from \"node:fs\";\nwriteFileSync(process.env.APOSTATE_ARGV_PATH, JSON.stringify(process.argv.slice(2)));\nsetTimeout(() => {}, 10000);\n");
    await chmod(executable, 0o755);
    const targetUrl = `http://127.0.0.1:${targetServer.address().port}/geoip`;
    const proxyUrl = `http://127.0.0.1:${proxyServer.address().port}`;
    const browser = await launchProcess({
      executablePath: executable,
      profile: { id: "geoip-fixture", platform: { name: "macOS" } },
      fingerprintPlatform: "macos",
      proxy: { server: proxyUrl, username: "fixture-user", password: "fixture-password" },
      geoipUrl: targetUrl,
      geoip: true,
      env: { APOSTATE_ARGV_PATH: argvPath },
    });
    let argv;
    for (let attempt = 0; attempt < 40; attempt += 1) {
      try {
        argv = JSON.parse(await readFile(argvPath, "utf8"));
        break;
      } catch (error) {
        if (attempt === 39) throw error;
        await new Promise((resolve) => setTimeout(resolve, 25));
      }
    }
    try {
      assert.equal(browser.launchConfig.locale, "en-US");
      assert.equal(browser.launchConfig.timezone, "UTC");
      assert.equal(argv.includes("--fingerprint-webrtc-ip=202.63.209.57"), true);
      assert.equal(sawProxyAuth, true);
      assert.equal(argv.some((value) => value.includes("fixture-user") || value.includes("fixture-password")), false);
      assert.equal(argv.includes(`--proxy-server=${proxyUrl}`), true);
      const encoded = argv.find((value) => value.startsWith("--apostate-profile="));
      assert.ok(encoded);
      const payload = JSON.parse(Buffer.from(encoded.slice("--apostate-profile=".length), "base64").toString("utf8"));
      assert.equal(payload.device_profile.id, "geoip-fixture");
      assert.deepEqual(payload.proxy_credentials, { username: "fixture-user", password: "fixture-password" });
      assert.equal(payload.id, undefined);
    } finally {
      await browser.close();
    }
  } finally {
    await new Promise((resolve) => proxyServer.close(resolve));
    await new Promise((resolve) => targetServer.close(resolve));
    await rm(root, { recursive: true, force: true });
  }
});

test("rejects unpublished manifests before downloading or extracting", async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-unpublished-"));
  let downloads = 0;
  let extracts = 0;
  try {
    await assert.rejects(
      ensureBinary({
        target,
        // ensureBinary now falls through to a browser already installed in a
        // well-known location, so every test that pins a manifest-level
        // refusal has to scan nothing: otherwise the suite passes or fails on
        // whether the machine running it happens to have Apostate in
        // /Applications or /opt/apostate.
        searchRoots: [],
        cacheDir,
        manifest: {
          package_version: "0.1.0",
          chromium_version: CHROMIUM_VERSION,
          catalogue_version: CATALOGUE_VERSION,
          artifacts: {},
          status: "unpublished",
        },
        download: async () => {
          downloads += 1;
          return Buffer.from("archive");
        },
        extract: async () => {
          extracts += 1;
          return null;
        },
      }),
      (error) => error instanceof UnpublishedArtifactError && /unpublished/i.test(error.message),
    );
    assert.equal(downloads, 0);
    assert.equal(extracts, 0);
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
  }
});

test("rejects an unpublished platform without downloading another platform's artifact", async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-subset-"));
  const records = ["linux-x64", "linux-arm64", "macos-arm64"].map((platform) => ({
    platform,
    artifact: artifactNameFor(platform),
    sha256: "0".repeat(64),
  }));
  let downloads = 0;
  try {
    for (const manifest of [
      { package_version: "0.1.0", chromium_version: CHROMIUM_VERSION, catalogue_version: CATALOGUE_VERSION, artifacts: Object.fromEntries(records.map((record) => [record.platform, record])) },
      { package_version: "0.1.0", chromium_version: CHROMIUM_VERSION, catalogue_version: CATALOGUE_VERSION, artifacts: records },
      { package_version: "0.1.0", chromium_version: CHROMIUM_VERSION, catalogue_version: CATALOGUE_VERSION, ...records[0] },
    ]) {
      await assert.rejects(
        ensureBinary({
          target: "windows-x64",
          searchRoots: [],
          cacheDir,
          manifest,
          download: async () => { downloads += 1; throw new Error("unexpected download"); },
        }),
        (error) => error instanceof UnpublishedArtifactError && /windows-x64.*not published for this release/.test(error.message),
      );
    }
    assert.equal(downloads, 0);
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
  }
});

test("an extraction failure names the tool that is missing, not the archive", async () => {
  // Both packages verify SHA-256 before opening an archive, so reaching tar at
  // all means the bytes were right and this system has no tool to read them.
  // tar's own words are "unrecognized archive format" whether zstd is absent,
  // tar is too old for --zstd, or tar is GNU and the archive is a zip: three
  // different fixes behind one sentence, which sends an operator to look at
  // their download instead of their toolchain.
  //
  // Called directly rather than through an arranged PATH. The first version of
  // this test spawned a stub tar and asserted which branch ran, which made the
  // host decide the result: it passed on a laptop without zstd and failed on a
  // runner with it.
  const zstdMissing = extractionFailureMessage("zst", false, "tar exited with code 1");
  assert.match(zstdMissing, /install the `zstd` command line tool/);
  assert.match(zstdMissing, /tar exited with code 1/);

  // A tool that is installed must not be blamed: on a machine with zstd the
  // failure is something else, and saying "install zstd" would be a confident
  // wrong answer rather than an unhelpful one.
  assert.match(extractionFailureMessage("zst", true, "tar exited with code 1"),
               /^Unable to read tar archive/);

  // The zip case is GNU tar ahead of bsdtar on PATH, which is a different fix.
  assert.match(extractionFailureMessage("zip", false, "tar exited with code 1"),
               /cannot read a zip archive/);
});

test("accepts scalar release manifest and rejects tampered cache state", async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-cache-"));
  try {
    const archive = Buffer.from("archive bytes");
    const manifest = releaseManifest(archive);
    let downloads = 0;
    let extracts = 0;
    const options = {
      target,
      searchRoots: [],
      cacheDir,
      manifest,
      download: async () => {
        downloads += 1;
        return archive;
      },
      // An extractor returns the DIRECTORY holding the distribution. Chromium
      // needs its resources beside the executable, so there is no single file
      // to hand back.
      extract: async (_bytes, destination) => {
        extracts += 1;
        const tree = join(destination, "tree");
        await mkdir(join(tree, "resources"), { recursive: true });
        await writeFile(join(tree, "chrome"), "binary bytes");
        await writeFile(join(tree, "resources", "en-US.pak"), "pak bytes");
        return tree;
      },
    };
    const binary = await ensureBinary(options);
    assert.equal(downloads, 1);
    assert.equal(extracts, 1);
    assert.equal(await readFile(binary, "utf8"), "binary bytes");
    // The whole tree is installed, not just the executable.
    assert.equal(await readFile(join(dirname(binary), "resources", "en-US.pak"), "utf8"), "pak bytes");

    await ensureBinary(options);
    assert.equal(downloads, 1, "verified cache should avoid a second download");
    assert.equal(extracts, 1);

    await writeFile(binary, "tampered binary");
    await ensureBinary(options);
    assert.equal(downloads, 2, "tampered executable must invalidate cache");
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
  }
});

test("an extractor must return a directory, not the executable", async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-extract-contract-"));
  try {
    const archive = Buffer.from("archive bytes");
    await assert.rejects(
      ensureBinary({
        target,
        searchRoots: [],
        cacheDir,
        manifest: releaseManifest(archive),
        download: async () => archive,
        extract: async (_bytes, destination) => {
          const path = join(destination, "chrome");
          await writeFile(path, "binary bytes");
          return path;
        },
      }),
      (error) => error instanceof BinaryExtractionError && /must return the directory/.test(error.message),
    );
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
  }
});

test("ZIP extraction refuses traversal and escaping links before writing", async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-zip-safety-"));
  try {
    for (const [label, malicious] of [
      ["traversal", storedZip([{ name: "../outside", data: "escape" }, { name: "chrome.exe", data: "binary" }])],
      ["escaping-link", storedZip([{ name: "link", data: "../../outside", mode: 0o120777 }, { name: "chrome.exe", data: "binary" }])],
      ["absolute-link", storedZip([{ name: "link", data: "/etc/passwd", mode: 0o120777 }, { name: "chrome.exe", data: "binary" }])],
    ]) {
      const manifest = releaseManifest(malicious, "windows-x64");
      await assert.rejects(
        ensureBinary({ target: "windows-x64", searchRoots: [], cacheDir, manifest, download: async () => malicious }),
        (error) => error instanceof BinaryExtractionError,
        label,
      );
    }
    assert.equal(await readFile(join(cacheDir, "outside"), "utf8").catch(() => null), null);
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
  }
});

test("tar extraction keeps a contained link and refuses an escaping one", async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-tar-safety-"));
  try {
    for (const [label, linkname] of [["escaping", "../outside"], ["absolute", "/etc/passwd"]]) {
      const malicious = tarArchive([
        { name: "chrome", data: "binary" },
        { name: "link", type: "2", linkname },
      ]);
      await assert.rejects(
        ensureBinary({ target, searchRoots: [], cacheDir, manifest: releaseManifest(malicious, target), download: async () => malicious }),
        (error) => error instanceof BinaryExtractionError,
        label,
      );
    }
    assert.equal(await readFile(join(cacheDir, "outside"), "utf8").catch(() => null), null);

    // The macOS bundle reaches its framework through five relative symlinks, so
    // prohibition is not an option. Containment is what is enforced, and a
    // contained link must survive extraction intact.
    const benign = tarArchive([
      { name: "chrome", data: "binary" },
      { name: "nested/", type: "5" },
      { name: "nested/current", type: "2", linkname: "../chrome" },
    ]);
    const binary = await ensureBinary({
      target, searchRoots: [], cacheDir, manifest: releaseManifest(benign, target), download: async () => benign,
    });
    const link = join(dirname(binary), "nested", "current");
    assert.equal((await lstat(link)).isSymbolicLink(), true);
    assert.equal(await readlink(link), "../chrome");
    assert.equal(await readFile(link, "utf8"), "binary");
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
  }
});

test("a zstd archive is unpacked in-process, and still scanned before it is", { skip: typeof zlib.zstdCompressSync !== "function" }, async () => {
  // With node:zlib's zstd the host's tar only ever reads a plain tar, so a
  // host without the zstd tool can still install the browser.
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-zstd-"));
  try {
    const benign = zlib.zstdCompressSync(tarArchive([
      { name: "chrome", data: "binary" },
      { name: "nested/", type: "5" },
      { name: "nested/current", type: "2", linkname: "../chrome" },
    ]));
    const binary = await ensureBinary({
      target, searchRoots: [], cacheDir, manifest: releaseManifest(benign, target), download: async () => benign,
    });
    assert.equal(await readFile(binary, "utf8"), "binary");
    assert.equal(await readlink(join(dirname(binary), "nested", "current")), "../chrome");

    const escaping = zlib.zstdCompressSync(tarArchive([
      { name: "chrome", data: "binary" },
      { name: "link", type: "2", linkname: "../outside" },
    ]));
    await assert.rejects(
      ensureBinary({ target, searchRoots: [], cacheDir: join(cacheDir, "escaping"), manifest: releaseManifest(escaping, target), download: async () => escaping }),
      (error) => error instanceof BinaryExtractionError,
    );
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
  }
});

test("rejects traversal paths returned by an extractor", async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-traversal-"));
  try {
    const archive = Buffer.from("archive bytes");
    const manifest = releaseManifest(archive);
    await assert.rejects(
      ensureBinary({
        target,
        searchRoots: [],
        cacheDir,
        manifest,
        download: async () => archive,
        extract: async () => "../outside",
      }),
      (error) => error instanceof BinaryExtractionError,
    );
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
  }
});

// An unpacked Apostate payload: the executable plus the two files
// scripts/package-artifact.sh copies into every release archive.
async function plantPayload(root, chromiumVersion) {
  const executable = join(root, "chrome");
  await mkdir(join(root, "build"), { recursive: true });
  await mkdir(join(root, "resources", "profiles"), { recursive: true });
  await writeFile(executable, "apostate chromium");
  await chmod(executable, 0o755);
  await writeFile(join(root, "build", "MANIFEST.lock"),
    `# Generated by scripts/build.sh\nchromium_version     = "${chromiumVersion}"\npatch_series_sha256  = "${"a1".repeat(32)}"\n`);
  await writeFile(join(root, "resources", "profiles", "catalogue.json"), "{}");
  return executable;
}

// Discovery consults APOSTATE_BINARY ahead of anything on disk, so a
// developer with one exported would otherwise watch these tests adopt it.
async function withoutConfiguredBinary(body) {
  const configured = process.env.APOSTATE_BINARY;
  delete process.env.APOSTATE_BINARY;
  try {
    return await body();
  } finally {
    if (configured !== undefined) process.env.APOSTATE_BINARY = configured;
  }
}

test("discovery adopts a planted Apostate payload and never a stock Chromium beside it", async () => {
  // The hazard the marker check exists for. Both trees hold an executable of
  // the same name in the same place; only one is ours, and launching the
  // other with Apostate's switches yields a session with none of the
  // protections those switches name -- silently, because stock Chromium
  // ignores switches it does not know.
  const root = await mkdtemp(join(tmpdir(), "apostate-node-discovery-"));
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-discovery-cache-"));
  try {
    await withoutConfiguredBinary(async () => {
      const planted = await plantPayload(join(root, "apostate"), CHROMIUM_VERSION);
      const stock = join(root, "stock", "chrome");
      await mkdir(dirname(stock), { recursive: true });
      await writeFile(stock, "stock chromium");
      await chmod(stock, 0o755);

      const report = await discoveryReport({ target, searchRoots: [root], cacheDir });
      assert.equal(report.found?.executable, planted);
      assert.equal(report.found.source, "well-known");
      assert.equal(report.found.chromium_version, CHROMIUM_VERSION);
      assert.deepEqual(report.rejected.find((entry) => entry.path === stock), {
        path: stock,
        reason: "no Apostate payload beside it (build/MANIFEST.lock or resources/profiles/catalogue.json)",
      });

      // And a release that publishes nothing for this target no longer ends
      // the search: there is nothing left to download, so there is nothing
      // for publication to decide.
      const binary = await ensureBinary({
        target,
        searchRoots: [root],
        cacheDir,
        manifest: {
          package_version: "0.1.0",
          chromium_version: CHROMIUM_VERSION,
          catalogue_version: CATALOGUE_VERSION,
          artifacts: {},
          status: "unpublished",
        },
        download: () => { throw new Error("must not download"); },
      });
      assert.equal(binary, planted);
    });
  } finally {
    await rm(root, { recursive: true, force: true });
    await rm(cacheDir, { recursive: true, force: true });
  }
});

test("a planted payload of the wrong Chromium version is rejected, not adopted", async () => {
  // An old install left behind by a previous release is the common case, and
  // adopting it would serve a fingerprint surface the catalogue no longer
  // describes.
  const root = await mkdtemp(join(tmpdir(), "apostate-node-discovery-stale-"));
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-discovery-stale-cache-"));
  try {
    await withoutConfiguredBinary(async () => {
      const planted = await plantPayload(join(root, "apostate"), "151.0.0.1");
      const report = await discoveryReport({ target, searchRoots: [root], cacheDir });
      assert.equal(report.found, null);
      assert.deepEqual(report.rejected, [
        { path: planted, reason: `reports Chromium 151.0.0.1, not ${CHROMIUM_VERSION}` },
      ]);
    });
  } finally {
    await rm(root, { recursive: true, force: true });
    await rm(cacheDir, { recursive: true, force: true });
  }
});

test("binaryInfo reports where the browser was found", async () => {
  const root = await mkdtemp(join(tmpdir(), "apostate-node-discovery-info-"));
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-discovery-info-cache-"));
  try {
    await withoutConfiguredBinary(async () => {
      const planted = await plantPayload(join(root, "apostate"), CHROMIUM_VERSION);
      const info = await binaryInfo({ target, searchRoots: [root], cacheDir });
      assert.equal(info.executable_source, "well-known");
      assert.equal(info.executable, planted);
      // Still the narrower question: this package's own install is absent.
      assert.equal(info.cache_hit, false);
      assert.deepEqual(info.discovery.order, ["argument", "environment", "cache", "well-known"]);
    });
  } finally {
    await rm(root, { recursive: true, force: true });
    await rm(cacheDir, { recursive: true, force: true });
  }
});

// The manifest baked into this package describes nothing -- a launcher
// release installs binaries published under an older tag, so it cannot carry
// their digest. These four tests cover the route that replaced the dead end.

const TAG_MANIFEST_URL = `https://github.com/heretic-tech/apostate/releases/download/v${PACKAGE_VERSION}/${artifactNameFor(target)}.manifest.json`;
const LATEST_MANIFEST_URL = `https://github.com/heretic-tech/apostate/releases/latest/download/${artifactNameFor(target)}.manifest.json`;

// The per-asset manifest the release publishes beside each archive.
function assetManifest(archive, platform = target, overrides = {}) {
  return {
    artifact: artifactNameFor(platform),
    build_manifest_sha256: "b1".repeat(32),
    catalogue_version: CATALOGUE_VERSION,
    chromium_version: CHROMIUM_VERSION,
    // Deliberately NOT this package's version: a 0.1.1 launcher installs the
    // binaries published as v0.1.0, and that must not be treated as a
    // mismatch.
    package_version: "0.1.0",
    patch_series_sha256: "a1".repeat(32),
    platform,
    sha256: createHash("sha256").update(archive).digest("hex"),
    source_revision: "0".repeat(40),
    ...overrides,
  };
}

test("falls back from the tagged release manifest to latest and installs against it", async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-release-manifest-"));
  try {
    const archive = Buffer.from("real archive bytes");
    const requested = [];
    const binary = await ensureBinary({
      target,
      searchRoots: [],
      cacheDir,
      download: async (url, context) => {
        requested.push(url);
        if (context.kind === "manifest") {
          // The tagged URL is the launcher's own version, which has no binary
          // release behind it. GitHub answers 404.
          if (url === TAG_MANIFEST_URL) throw new Error("HTTP 404");
          assert.equal(url, LATEST_MANIFEST_URL);
          return assetManifest(archive);
        }
        return archive;
      },
      extract: async (_bytes, destination) => {
        const tree = join(destination, "tree");
        await mkdir(tree, { recursive: true });
        await writeFile(join(tree, "chrome"), "binary bytes");
        return tree;
      },
    });
    assert.deepEqual(requested, [
      TAG_MANIFEST_URL,
      LATEST_MANIFEST_URL,
      // The archive comes from the directory the manifest came from.
      `https://github.com/heretic-tech/apostate/releases/latest/download/${artifactNameFor(target)}`,
    ]);
    assert.equal(await readFile(binary, "utf8"), "binary bytes");

    // And the install is reusable without the network: a launcher-only bump
    // must not throw away 600 MB, and no manifest is reachable to re-pin it.
    const again = await ensureBinary({
      target,
      searchRoots: [],
      cacheDir,
      download: async () => { throw new Error("must not touch the network"); },
    });
    assert.equal(again, binary);
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
  }
});

test("a fetched manifest that describes something else is refused, and latest is still tried", async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-release-manifest-wrong-"));
  try {
    const archive = Buffer.from("real archive bytes");
    const binary = await ensureBinary({
      target,
      searchRoots: [],
      cacheDir,
      download: async (url, context) => {
        if (context.kind !== "manifest") return archive;
        // Parses, binds to another build. Stopping here would strand a
        // launcher whose own tag published a different Chromium.
        if (url === TAG_MANIFEST_URL) return assetManifest(archive, target, { chromium_version: "151.0.0.1" });
        return assetManifest(archive);
      },
      extract: async (_bytes, destination) => {
        const tree = join(destination, "tree");
        await mkdir(tree, { recursive: true });
        await writeFile(join(tree, "chrome"), "binary bytes");
        return tree;
      },
    });
    assert.equal(await readFile(binary, "utf8"), "binary bytes");
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
  }
});

test("a digest that does not match the archive aborts before extraction", async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-release-manifest-digest-"));
  try {
    let extracts = 0;
    await assert.rejects(
      ensureBinary({
        target,
        searchRoots: [],
        cacheDir,
        download: async (_url, context) => (context.kind === "manifest"
          ? assetManifest(Buffer.from("what the manifest describes"))
          : Buffer.from("what the server sent")),
        extract: async () => { extracts += 1; return null; },
      }),
      (error) => error.code === "BINARY_HASH_MISMATCH",
    );
    assert.equal(extracts, 0);
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
  }
});

test("launch refuses only after both release manifest URLs failed, and names them", async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-launch-"));
  try {
    const tried = [];
    await assert.rejects(
      launch({
        target,
        searchRoots: [],
        cacheDir,
        geoip: false,
        fingerprint: "host",
        download: async (url) => { tried.push(url); throw new Error("HTTP 404"); },
      }),
      (error) => {
        assert.ok(error instanceof UnpublishedArtifactError, error?.message);
        assert.equal(
          error.message,
          "Release manifest is unpublished; Apostate binary artifacts are not available for acquisition."
          + ` No release manifest for ${artifactNameFor(target)} could be fetched.`
          + ` Tried: ${TAG_MANIFEST_URL}, ${LATEST_MANIFEST_URL}`,
        );
        assert.deepEqual(error.details.urls_tried, [TAG_MANIFEST_URL, LATEST_MANIFEST_URL]);
        return true;
      },
    );
    assert.deepEqual(tried, [TAG_MANIFEST_URL, LATEST_MANIFEST_URL]);
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
  }
});

test("a named bundle or payload root resolves to the executable inside it", async () => {
  const root = await mkdtemp(join(tmpdir(), "apostate-node-named-path-"));
  try {
    await withoutConfiguredBinary(async () => {
      // The macos-arm64 layout: the extracted payload root holds the bundle,
      // the build record and the profile resources.
      const payloadRoot = join(root, `apostate-${CHROMIUM_VERSION}-macos-arm64`);
      const bundle = join(payloadRoot, "Chromium.app");
      const executable = join(bundle, "Contents", "MacOS", "Chromium");
      await mkdir(dirname(executable), { recursive: true });
      await writeFile(executable, "binary bytes");
      await chmod(executable, 0o755);

      for (const named of [executable, bundle, payloadRoot]) {
        assert.equal(await ensureBinary({ target: "macos-arm64", binaryPath: named }), executable, named);
        const report = await discoveryReport({ target: "macos-arm64", binaryPath: named });
        assert.equal(report.found.executable, executable, named);
        assert.equal(report.found.source, "argument");
      }

      // The same three forms through the environment variable.
      process.env.APOSTATE_BINARY = bundle;
      try {
        assert.equal(await ensureBinary({ target: "macos-arm64" }), executable);
        assert.equal((await discoveryReport({ target: "macos-arm64" })).found.source, "environment");
      } finally {
        delete process.env.APOSTATE_BINARY;
      }
    });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("a named directory with no browser in it is refused by name, not by is-a-file", async () => {
  const root = await mkdtemp(join(tmpdir(), "apostate-node-named-empty-"));
  try {
    await withoutConfiguredBinary(async () => {
      const empty = join(root, "empty");
      const missing = join(root, "missing");
      await mkdir(empty, { recursive: true });

      await assert.rejects(
        ensureBinary({ target: "macos-arm64", binaryPath: empty }),
        (error) => error.message === `the configured path names a directory with no browser inside it (expected Chromium.app, chrome or chrome.exe): ${empty}`,
      );
      await assert.rejects(
        ensureBinary({ target: "macos-arm64", binaryPath: missing }),
        (error) => error.message === `the configured path does not name a file: ${missing}`,
      );

      const report = await discoveryReport({ target: "macos-arm64", binaryPath: empty, searchRoots: [] });
      assert.deepEqual(report.rejected, [{
        path: empty,
        reason: "the configured path names a directory with no browser inside it (expected Chromium.app, chrome or chrome.exe)",
      }]);

      process.env.APOSTATE_BINARY = missing;
      try {
        await assert.rejects(
          ensureBinary({ target: "macos-arm64" }),
          (error) => error.message === `APOSTATE_BINARY does not name a file: ${missing}`,
        );
      } finally {
        delete process.env.APOSTATE_BINARY;
      }
    });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("the cached CDM goes into an Apostate install and nowhere else", async () => {
  // A launch copies the cached CDM into the browser's preinstalled component
  // directory, which is also how a reinstall or an upgrade keeps DRM. It
  // writes only into an Apostate payload: a named binary can be anything.
  const root = await mkdtemp(join(tmpdir(), "apostate-node-widevine-"));
  try {
    const cacheDir = join(root, "cache");
    const store = join(cacheDir, "widevinecdm");
    await mkdir(join(store, "_platform_specific", "linux_x64"), { recursive: true });
    await writeFile(join(store, "_platform_specific", "linux_x64", "libwidevinecdm.so"), "cdm");
    await writeFile(join(store, "manifest.json"), JSON.stringify({ version: "4.10.3050.0" }));

    const chrome = await plantPayload(join(root, "payload"), CHROMIUM_VERSION);
    const component = await ensureWidevine(chrome, { target: "linux-x64", cacheDir });
    assert.equal(component, join(root, "payload", "WidevineCdm"));
    assert.equal(await readFile(join(component, "_platform_specific", "linux_x64", "libwidevinecdm.so"), "utf8"), "cdm");
    // No version directory: the browser reads it from manifest.json, and
    // Google Chrome's own bundled copy has none.
    assert.deepEqual((await readdir(component)).sort(), ["_platform_specific", "manifest.json"]);

    // Linux also reads the CDM from a hint inside a persistent profile.
    const profile = join(root, "profile");
    await ensureWidevine(chrome, { target: "linux-x64", cacheDir, userDataDir: profile });
    const hint = join(profile, "WidevineCdm", "latest-component-updated-widevine-cdm");
    assert.deepEqual(JSON.parse(await readFile(hint, "utf8")), { Path: component });

    const stock = join(root, "stock");
    await mkdir(stock, { recursive: true });
    await writeFile(join(stock, "chrome"), "stock chromium");
    assert.equal(await ensureWidevine(join(stock, "chrome"), { target: "linux-x64", cacheDir }), null);
    assert.equal(await lstat(join(stock, "WidevineCdm")).then(() => true, () => false), false);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("a downloaded CDM is kept only when its digest matches", async () => {
  const root = await mkdtemp(join(tmpdir(), "apostate-node-widevine-download-"));
  const saved = { fetch: globalThis.fetch, home: process.env.HOME, warn: console.warn };
  const warnings = [];
  try {
    // Nothing on this machine may answer first: no profile under HOME, and no
    // local Chrome ships linux-arm64.
    process.env.HOME = root;
    console.warn = (message) => warnings.push(String(message));
    const header = Buffer.alloc(12);
    header.write("Cr24", 0, "latin1");
    header.writeUInt32LE(3, 4);
    const crx = Buffer.concat([header, storedZip([
      { name: "manifest.json", data: JSON.stringify({ version: "4.10.3057.0" }) },
      { name: "_platform_specific/linux_arm64/libwidevinecdm.so", data: "cdm" },
      { name: "_metadata/verified_contents.json", data: "{}" },
    ])]);
    let digest = "0".repeat(64);
    const requested = [];
    globalThis.fetch = async (url) => {
      requested.push(String(url));
      if (String(url).endsWith(".crx3")) return new Response(crx);
      const check = {
        status: "ok",
        urls: { url: [{ codebase: "http://cdn.test/" }, { codebase: "https://cdn.test/" }] },
        manifest: { version: "4.10.3057.0", packages: { package: [{ name: "cdm.crx3", hash_sha256: digest }] } },
      };
      return new Response(`)]}'\n${JSON.stringify({ response: { app: [{ updatecheck: check }] } })}`);
    };
    const chrome = await plantPayload(join(root, "payload"), CHROMIUM_VERSION);
    const cacheDir = join(root, "cache");

    assert.equal(await ensureWidevine(chrome, { target: "linux-arm64", cacheDir }), null);
    assert.match(warnings.join("\n"), /failed SHA-256 verification/);
    assert.equal(await lstat(join(root, "payload", "WidevineCdm")).then(() => true, () => false), false);

    digest = createHash("sha256").update(crx).digest("hex");
    requested.length = 0;
    const component = await ensureWidevine(chrome, { target: "linux-arm64", cacheDir });
    assert.equal(component, join(root, "payload", "WidevineCdm"));
    assert.deepEqual(requested, ["https://update.googleapis.com/service/update2/json", "https://cdn.test/cdm.crx3"]);
    assert.equal(await readFile(join(component, "_platform_specific", "linux_arm64", "libwidevinecdm.so"), "utf8"), "cdm");
    assert.deepEqual((await readdir(component)).sort(), ["_platform_specific", "manifest.json"]);
  } finally {
    globalThis.fetch = saved.fetch;
    if (saved.home === undefined) delete process.env.HOME;
    else process.env.HOME = saved.home;
    console.warn = saved.warn;
    await rm(root, { recursive: true, force: true });
  }
});

test("Widevine provisioning refuses a directory with no library", async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-widevine-bad-"));
  try {
    const empty = join(cacheDir, "WidevineCdm");
    await mkdir(empty, { recursive: true });
    await assert.rejects(
      provisionWidevine({ target: "macos-arm64", cacheDir, source: empty }),
      (error) => error instanceof WidevineError && error.code === "WIDEVINE_NOT_FOUND",
    );
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
  }
});

test("drives the Puppeteer branch: launch, userDataDir and createBrowserContext", async () => {
  // The README promises an existing Puppeteer script works by changing only the
  // import. That claim had no coverage: no test reached driver.puppeteer.launch,
  // the userDataDir option branch, or createBrowserContext. Verified for real
  // against the local macos-arm64 binary with puppeteer-core 25.x before this
  // fake was written: launch()+newPage() read hardwareConcurrency 14 and
  // version Chrome/152.0.7977.83; launchPersistentContext wrote a 30-entry
  // profile containing Default; launchContext returned a CdpBrowserContext
  // whose newPage() worked. This pins the wiring so it cannot silently rot.
  const calls = [];
  const page = { async setContent() {}, async evaluate() { return 14; } };
  const context = { async newPage() { calls.push("context.newPage"); return page; }, async close() {} };
  const browser = {
    async newPage() { calls.push("browser.newPage"); return page; },
    async createBrowserContext() { calls.push("createBrowserContext"); return context; },
    async close() { calls.push("browser.close"); },
  };
  let seen = null;
  const fakePuppeteer = {
    default: {
      async launch(options) {
        seen = options;
        calls.push("puppeteer.launch");
        return browser;
      },
    },
  };

  const root = await mkdtemp(join(tmpdir(), "apostate-node-pptr-"));
  try {
    const executable = join(root, "browser");
    await writeFile(executable, "#!/bin/sh\nexit 0\n");
    await chmod(executable, 0o755);
    const base = {
      executablePath: executable,
      geoip: false,
      fingerprint: "host",
      driver: "puppeteer-core",
      _driverModule: fakePuppeteer,
    };

    const b = await launch(base);
    assert.equal(b.apostateDriverName, "puppeteer-core", "the selected driver must be visible");
    await b.newPage();

    // Puppeteer takes the profile directory as an option, not a switch.
    const userDataDir = join(root, "profile");
    await launchPersistentContext(userDataDir, base);
    assert.equal(resolve(seen.userDataDir), resolve(userDataDir));
    assert.ok(!seen.args.some((a) => a.startsWith("--user-data-dir=")),
      "the switch must not be duplicated as an argv entry for Puppeteer");

    // launchContext has no Playwright newContext here, so it must fall through.
    await launchContext(base);
    assert.ok(calls.includes("createBrowserContext"));

    // The component-update switch is stripped for Puppeteer too.
    assert.ok(seen.ignoreDefaultArgs.includes("--disable-component-update"));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

// The default claimed persona per host platform token, transcribed from
// DefaultPersonaForHost() in base/apostate/compose.cc (patch 0102) as given by
// its author. Declared here and deliberately NOT imported from
// DEFAULT_PERSONA_BY_HOST: a test that reads the table the implementation uses
// only proves the implementation agrees with itself.
//
// The mapping lives in no file both implementations can read. The dispersion
// tables under resources/profiles/ key option sets on a platform and so pin the
// token set, but not the host-to-persona edge, and 0102's author declined to add
// a data file that only tests would consume. This transcription is therefore the
// only pin, and the coupling is known and deliberate: if the C++ table moves,
// this literal must be moved with it, and until it is, the Node and Python
// mirrors fail here. python/tests/test_package.py carries the same table.
const COMPOSE_CC_DEFAULT_PERSONA = { macos: "macos", windows: "windows", linux: "windows" };
// os.platform() value -> host platform token. The other half of the chain: a
// correct table read from a misdetected host still claims the wrong OS.
const HOST_PLATFORM_TOKENS = { darwin: "macos", win32: "windows", linux: "linux" };

test("the resolver's default platform mirrors the browser's 0102 default", async () => {
  // The launch path sends no --fingerprint-platform when the caller named none,
  // so the browser applies its own host-conditional default. A local resolution
  // has no browser to ask and must reproduce that default, so this pins the two
  // implementations of one table against each other.
  //
  // Run in a child process per host case: os.platform() is read at module scope,
  // so the only honest way to exercise a Linux host from macOS is to mock node:os
  // before the package is imported. Assertions read the built argv and the
  // resolver's own output, never the source.
  const distHref = new URL("../dist/index.js", import.meta.url).href;
  const root = await mkdtemp(join(tmpdir(), "apostate-node-persona-"));
  try {
    const probePath = join(root, "probe.mjs");
    await writeFile(probePath, `
import { createRequire } from "node:module";
createRequire(import.meta.url)("node:os").platform = () => process.env.PROBE_HOST;
const m = await import(${JSON.stringify(distHref)});
const seen = [];
const fakeDriver = { default: { async launch(options) { seen.push(options); return { async close() {} }; } } };
const base = { executablePath: process.execPath, geoip: false, driver: "puppeteer-core", _driverModule: fakeDriver };
await m.launch({ ...base, fingerprint: 12345 });
await m.launch({ ...base, fingerprint: 12345, fingerprintPlatform: "linux" });
await m.launch({ ...base, fingerprint: 1, fingerprintPlatform: "win32" });
const personaSwitch = (i) => seen[i].args.filter((a) => a.startsWith("--fingerprint-platform"));
process.stdout.write(JSON.stringify({
  table: m.DEFAULT_PERSONA_BY_HOST,
  hostPersona: m.hostPersona(),
  mirror: m.defaultPersonaForHost(m.hostPersona()),
  passThrough: ["", "freebsd", "android", "WINDOWS"].map((t) => m.defaultPersonaForHost(t)),
  bare: m.resolveProfile({}).platform,
  seeded: m.resolveProfile({ fingerprint: 12345 }).platform,
  explicitProfile: m.resolveProfile({ profile: { id: "no-platform" } }).platform,
  explicitPersona: m.resolveProfile({ fingerprint: 1, fingerprintPlatform: "linux" }).platform,
  hostSeed: m.resolveProfile({ fingerprint: "host" }).platform,
  argvDefault: personaSwitch(0),
  argvExplicit: personaSwitch(1),
  argvAliased: personaSwitch(2),
}));
`);

    for (const [osPlatform, hostToken] of Object.entries(HOST_PLATFORM_TOKENS)) {
      const expected = COMPOSE_CC_DEFAULT_PERSONA[hostToken];
      const raw = execFileSync(process.execPath, [probePath], {
        encoding: "utf8",
        env: { ...process.env, PROBE_HOST: osPlatform },
      });
      const seen = JSON.parse(raw);

      assert.deepEqual(seen.table, COMPOSE_CC_DEFAULT_PERSONA,
        `DEFAULT_PERSONA_BY_HOST no longer matches compose.cc's table`);
      assert.equal(seen.hostPersona, hostToken,
        `hostPersona() must report the host, not the persona, on ${osPlatform}`);
      assert.equal(seen.mirror, expected);

      // Every shape that reaches the default: no selector, a seed, and an
      // explicit profile that declares no platform of its own.
      assert.equal(seen.bare, expected, `bare resolve on ${hostToken}`);
      assert.equal(seen.seeded, expected, `seeded resolve on ${hostToken}`);
      assert.equal(seen.explicitProfile, expected, `platformless profile on ${hostToken}`);
      // An explicit persona outranks the table outright, as request.platform
      // does in Compose().
      assert.equal(seen.explicitPersona, "linux");
      // Host inheritance composes nothing, so the page really does see the host
      // and reporting the host is correct. A deliberate exception, not a miss.
      assert.equal(seen.hostSeed, hostToken, `host seed must report the host on ${hostToken}`);
      // An unrecognised or empty token is returned unchanged rather than
      // defaulted to windows, so the browser's own IsKnownPlatform() check
      // fails and the launch inherits the host instead of composing for a
      // platform with no corpus behind it.
      assert.deepEqual(seen.passThrough, ["", "freebsd", "android", "WINDOWS"]);

      // Built argv, not source: a default launch must hand the browser no
      // persona switch at all, because that absence is what lets 0102's default
      // apply. --fingerprint-platform=linux on a Linux host is indistinguishable
      // inside the browser from the pre-0102 default.
      assert.deepEqual(seen.argvDefault, [], `no persona switch may be emitted on ${osPlatform}`);
      assert.deepEqual(seen.argvExplicit, ["--fingerprint-platform=linux"]);
      // An alias is normalised to a token IsKnownPlatform() accepts rather than
      // forwarded verbatim for the browser to reject and inherit the host.
      assert.deepEqual(seen.argvAliased, ["--fingerprint-platform=windows"]);
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("locale travels as an override, never as a partial profile envelope", async () => {
  // The regression test for a whole class of defect. The browser's
  // InstallComposedProfile() returns early whenever --apostate-profile carries
  // device content and base/apostate/profile.cc leaves absent fields absent, so a
  // locale-only envelope means composition never runs: eleven of twelve axes
  // fall back to the host and --fingerprint is silently ignored. geoip defaults
  // on, so that was the shape of almost every launch. The assertion is on the
  // emitted argv, because argv is what the browser actually reads. The Python
  // package pins the same nine cases in test_package.py.
  const root = await mkdtemp(join(tmpdir(), "apostate-node-envelope-"));
  try {
    const executable = join(root, "browser");
    await writeFile(executable, "#!/bin/sh\nexit 0\n");
    await chmod(executable, 0o755);
    let seen = null;
    const fakeDriver = { default: { async launch(options) { seen = options; return { async close() {} }; } } };
    const base = { executablePath: executable, driver: "puppeteer-core", _driverModule: fakeDriver };
    const geoipResolver = async () => ({ locale: "en-US,en", timezone: "Europe/London", ip: "1.2.3.4" });

    const cases = [
      ["nothing requested", { geoip: false }, [], false],
      ["seed only", { geoip: false, fingerprint: 12345 }, ["--fingerprint=12345"], false],
      ["explicit locale", { geoip: false, locale: "en-US" }, ["--fingerprint-locale=en-US"], false],
      ["explicit timezone", { geoip: false, timezone: "Europe/London" },
        ["--fingerprint-timezone=Europe/London"], false],
      ["geoip default, the common shape", { geoipResolver },
        ["--fingerprint-locale=en-US,en", "--fingerprint-timezone=Europe/London"], false],
      ["geoip default plus a seed", { fingerprint: 12345, geoipResolver },
        ["--fingerprint=12345", "--fingerprint-locale=en-US,en",
          "--fingerprint-timezone=Europe/London"], false],
      // Host mode composes nothing, so an envelope suppresses nothing there and
      // is the only carrier a locale has. Per-field overrides are refused by the
      // binary under host mode, so none are sent.
      ["host seed with a locale",
        { geoip: false, fingerprint: "host", locale: "en-GB,en" }, ["--fingerprint=host"], true],
      // A profile the user authored is the one legitimate envelope: they own its
      // coherence, and bypassing composition is documented rather than accidental.
      ["user-authored profile",
        { geoip: false, profile: { id: "mine", platform: { name: "macOS" } } }, [], true],
    ];

    for (const [label, options, expected, envelope] of cases) {
      seen = null;
      const browser = await launch({ ...base, ...options });
      await browser.close();
      assert.deepEqual(seen.args.filter((a) => a.startsWith("--fingerprint")), expected, label);
      assert.equal(seen.args.some((a) => a.startsWith("--apostate-profile=")), envelope, label);
    }

    // A payload DESCRIBING A DEVICE and a seed are alternatives, not layers, and
    // the browser cannot report the conflict: it drops the seed without a word.
    await assert.rejects(
      launch({ ...base, geoip: false, profile: { id: "mine" }, args: ["--fingerprint=99"] }),
      (error) => error.code === "APOSTATE_ENVELOPE_SEED_CONFLICT",
    );

    // But credentials are not a device claim, and this refusal used to fire on
    // them too. An authenticated proxy plus a pinned seed is legal and is the
    // commonest shape this package serves, so it must launch. Without this case
    // a revert to "any envelope refuses" leaves every other test green.
    seen = null;
    const authenticated = await launch({
      ...base,
      geoip: false,
      proxy: { server: "http://proxy.example:8080", username: "u", password: "p" },
      args: ["--fingerprint=99"],
    });
    await authenticated.close();
    assert.ok(seen.args.includes("--fingerprint=99"), "the user's seed must survive");
    const credentialArg = seen.args.find((a) => a.startsWith("--apostate-profile="));
    assert.ok(credentialArg, "credentials have no switch, so the envelope is still required");
    const decoded = JSON.parse(
      Buffer.from(credentialArg.slice("--apostate-profile=".length), "base64").toString("utf8"));
    assert.deepEqual(decoded.proxy_credentials, { username: "u", password: "p" });
    // The empty device_profile key is load-bearing, not incidental: 0072's
    // ParseOrNull reads proxy_credentials only inside FindDict("device_profile"),
    // so a wrapper without the key loses the credentials silently.
    assert.deepEqual(decoded.device_profile, {}, "the empty device_profile key must stay");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("an authenticated SOCKS5 proxy reaches the browser through the envelope, not the driver", async () => {
  // Playwright refuses to start at all when a socks5 server carries a
  // username -- "Browser does not support socks5 proxy authentication" --
  // because upstream Chromium has no way to supply one. This build does: the
  // credential rides the --apostate-profile envelope into the network stack
  // and never touches Chromium's proxy configuration. Handing the driver a
  // credential it will refuse to launch with, for a proxy the browser can
  // serve, made every residential SOCKS5 proxy unusable.
  const root = await mkdtemp(join(tmpdir(), "apostate-node-socks-"));
  try {
    const executable = join(root, "browser");
    await writeFile(executable, "#!/bin/sh\nexit 0\n");
    await chmod(executable, 0o755);
    let seen = null;
    const fakeDriver = { chromium: { async launch(options) { seen = options; return { async close() {} }; } } };
    const base = { executablePath: executable, _driverModule: fakeDriver, geoip: false, fingerprint: "host" };

    const socks = await launch({ ...base, proxy: "socks5://u:p@proxy.invalid:1080" });
    await socks.close();
    assert.deepEqual(seen.proxy, { server: "socks5://proxy.invalid:1080" });
    assert.ok(seen.args.includes("--proxy-server=socks5://proxy.invalid:1080"));
    const envelope = seen.args.find((arg) => arg.startsWith("--apostate-profile="));
    assert.deepEqual(
      JSON.parse(Buffer.from(envelope.slice("--apostate-profile=".length), "base64").toString("utf8")).proxy_credentials,
      { username: "u", password: "p" },
    );

    // An HTTP proxy keeps the driver-side credential: Playwright answers the
    // 407 itself there, and nothing refuses the launch.
    const http = await launch({ ...base, proxy: "http://u:p@proxy.invalid:8080" });
    await http.close();
    assert.deepEqual(seen.proxy, { server: "http://proxy.invalid:8080", username: "u", password: "p" });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("a GeoIP failure sends no override rather than inventing en-US and UTC", async () => {
  // Two independent invention sites used to live in prepareLaunch: the catch
  // handler substituted { locale: "en-US", timezone: "UTC" }, and a second
  // block below it forced the same pair whenever either field was still null,
  // so removing only the handler left the defect alive for a lookup that
  // succeeded without a timezone. Both are covered here, and every case also
  // asserts the seed survives: a failed lookup must not cost the composed
  // fingerprint. The assertion is on the emitted argv, because a resolved
  // locale now travels as --fingerprint-locale and an omitted one leaves the
  // composed profile's own drawn pair in place -- which is the state that makes
  // proceeding without an override correct rather than merely lenient.
  const root = await mkdtemp(join(tmpdir(), "apostate-node-geoip-failure-"));
  try {
    const executable = join(root, "browser");
    await writeFile(executable, "#!/bin/sh\nexit 0\n");
    await chmod(executable, 0o755);
    let seen = null;
    const fakeDriver = { default: { async launch(options) { seen = options; return { async close() {} }; } } };
    const base = {
      executablePath: executable, driver: "puppeteer-core", _driverModule: fakeDriver,
      fingerprint: 4242,
    };

    const cases = [
      ["lookup failure", {
        geoipResolver: async () => { throw new Error("connect ECONNREFUSED 203.0.113.9:443"); },
      }, [], "GeoIP lookup failed"],
      ["timeout", {
        geoipTimeoutMs: 5,
        geoipResolver: ({ signal }) => new Promise((_, reject) => {
          signal.addEventListener("abort", () => {
            reject(Object.assign(new Error("GeoIP request aborted."), { name: "AbortError" }));
          }, { once: true });
        }),
      }, [], "timed out"],
      // Nothing failed in the next two: the resolver answered, without one of
      // the two fields. This is the site the removed hard fallback re-invented
      // from, independently of the handler above.
      ["a result with no timezone", { geoipResolver: async () => ({ locale: "de-DE,de" }) },
        ["--fingerprint-locale=de-DE,de"], "resolved no timezone"],
      ["a result with no country at all", { geoipResolver: async () => ({ timezone: "Europe/Berlin" }) },
        ["--fingerprint-timezone=Europe/Berlin"], "returned no country"],
      // freeipapi answers `timeZone` with a UTC offset, which cannot drive
      // --fingerprint-timezone. Unresolved, not adjusted into a lookalike.
      ["an offset instead of an identifier",
        { geoipResolver: async () => ({ locale: "de-DE", timeZone: "+02:00" }) },
        ["--fingerprint-locale=de-DE"], "resolved no timezone"],
      // The locale is DERIVED from the country through assets/country-locales.json,
      // which covers all 257 territories CLDR knows. The old 45-country hand
      // table left most exits with no locale at all, so a Malaysian exit
      // served the host's own language from a Malaysian IP.
      ["a European country code and a timezone",
        { geoipResolver: async () => ({ country_code: "DE", timezone: "Europe/Berlin" }) },
        ["--fingerprint-locale=de-DE", "--fingerprint-timezone=Europe/Berlin"], null],
      ["the Malaysian exit the hand table missed",
        { geoipResolver: async () => ({ country_code: "MY", timezone: "Asia/Kuala_Lumpur" }) },
        ["--fingerprint-locale=ms", "--fingerprint-timezone=Asia/Kuala_Lumpur"], null],
      // es-419 is Latin American Spanish: a regional tag whose base language
      // still trails it in accept_languages.
      ["a Latin American exit",
        { geoipResolver: async () => ({ countryCode: "GT", timezone: "America/Guatemala" }) },
        ["--fingerprint-locale=es-419", "--fingerprint-timezone=America/Guatemala"], null],
    ];

    for (const [label, options, expected, warning] of cases) {
      seen = null;
      const browser = await launch({ ...base, ...options });
      await browser.close();
      const localization = seen.args.filter((arg) => arg.startsWith("--fingerprint-locale")
        || arg.startsWith("--fingerprint-timezone"));
      assert.deepEqual(localization, expected, label);
      assert.ok(seen.args.includes("--fingerprint=4242"), `${label}: the seed must survive`);
      assert.equal(seen.args.some((arg) => arg.startsWith("--lang=")),
        expected.some((arg) => arg.startsWith("--fingerprint-locale")), label);
      const expectedTimezone = expected.find((arg) => arg.startsWith("--fingerprint-timezone="));
      assert.equal(seen.env.TZ, expectedTimezone ? expectedTimezone.slice("--fingerprint-timezone=".length) : undefined, label);
      const warnings = browser.apostateDiagnostics.warnings;
      if (warning === null) assert.deepEqual(warnings, [], label);
      else assert.ok(warnings.some((entry) => entry.includes(warning)),
        `${label}: ${JSON.stringify(warnings)}`);
      assert.equal(warnings.some((entry) => entry.includes("Defaulting to fallback")), false, label);
      // A lookup that ran and failed is not "explicit": nothing was explicit.
      assert.equal(browser.apostateDiagnostics.geoip,
        expected.length === 0 ? "unresolved" : "resolved", label);
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("a country derives its locale and its accept-language list for every territory", async () => {
  // The country -> locale table is assets/country-locales.json, generated
  // from CLDR territoryInfo and Chromium's own kAcceptLanguageList and
  // shipped byte-identical to the Python package. What is asserted here is
  // the shape it is turned into: the tag drives --fingerprint-locale, and
  // accept_languages carries the tag with its base language behind it -- or
  // just the tag, when the tag has no region to strip.
  for (const [country, locale, acceptLanguages] of [
    ["MY", "ms", "ms"],
    ["GT", "es-419", "es-419,es"],
    ["IN", "en-IN", "en-IN,en"],
    ["CZ", "cs", "cs"],
    ["HK", "zh-HK", "zh-HK,zh"],
  ]) {
    const config = await resolveLaunchConfig({
      fingerprint: "host",
      geoipResolver: async () => ({ country_code: country, timezone: "Etc/UTC" }),
    });
    assert.equal(config.locale, locale, country);
    assert.equal(config.profile.locale.accept_languages, acceptLanguages, country);
  }

  // No country means no derivation, and the message says so: "resolved no
  // locale" sent people looking for a field the provider was never asked for.
  const warnings = [];
  const original = console.warn;
  console.warn = (line) => warnings.push(line);
  let config;
  try {
    config = await resolveLaunchConfig({
      fingerprint: "host",
      geoipResolver: async () => ({ timezone: "Etc/UTC" }),
    });
  } finally {
    console.warn = original;
  }
  assert.equal(config.locale, null);
  assert.deepEqual(warnings, [
    "\u001b[33m[Apostate] the GeoIP lookup returned no country, so no locale is derived;"
    + " the host's own is served for that field."
    + " Pass locale explicitly to guarantee a match.\u001b[0m",
  ]);
});


// One GeoIP site, serving a fixed reply and counting what reached it. Real
// sockets rather than a stubbed resolver: the cascade's whole job is to survive
// a site that is down, and "down" is an HTTP status or a closed connection, not
// an exception a double chose to throw. python/tests/test_package.py runs the
// same fixture shape through the Python cascade.
async function fakeGeoipEndpoint(payload, status = 200) {
  const state = { requests: 0 };
  const server = createServer((request, response) => {
    state.requests += 1;
    response.writeHead(status, { "content-type": "application/json" });
    response.end(JSON.stringify(payload ?? {}));
  });
  await new Promise((ready) => server.listen(0, "127.0.0.1", ready));
  state.url = `http://127.0.0.1:${server.address().port}/json`;
  state.close = () => new Promise((closed) => server.close(closed));
  return state;
}

test("a dead GeoIP endpoint is retried, then the walk moves on and still resolves", async () => {
  // The lookup used to make one attempt per site with no retry, so an outage,
  // a rate limit or a proxy that would not carry the request ended with no
  // locale and no timezone -- which means the browser serves the host's own
  // from a foreign exit, the leak the lookup exists to close. A failure now
  // costs an attempt rather than the answer.
  const down = await fakeGeoipEndpoint(null, 503);
  const up = await fakeGeoipEndpoint({ country_code: "MY", timezone: "Asia/Kuala_Lumpur", ip: "203.0.113.7" });
  try {
    const config = await resolveLaunchConfig({
      fingerprint: "host",
      _geoipEndpoints: [down.url, up.url],
    });
    assert.equal(config.locale, "ms");
    assert.equal(config.timezone, "Asia/Kuala_Lumpur");
    // The failing site was retried before the walk gave up on it, and the one
    // that answered was asked once.
    assert.equal(down.requests, 2);
    assert.equal(up.requests, 1);
  } finally {
    await down.close();
    await up.close();
  }
});

test("a GeoIP answer missing the timezone yields to a later site", async () => {
  // There is no country-to-timezone table in this repository, so deriving the
  // missing half would mean inventing a zone -- exactly the contradiction with
  // the exit IP that detectors score. The walk carries on instead, and falls
  // back to the half it has only once nothing better arrives.
  const partial = await fakeGeoipEndpoint({ country_code: "MY", ip: "203.0.113.7" });
  const full = await fakeGeoipEndpoint({ country_code: "DE", timezone: "Europe/Berlin" });
  const warnings = [];
  const original = console.warn;
  console.warn = (line) => warnings.push(line);
  try {
    const both = await resolveLaunchConfig({
      fingerprint: "host",
      _geoipEndpoints: [partial.url, full.url],
    });
    assert.equal(both.locale, "de-DE");
    assert.equal(both.timezone, "Europe/Berlin");
    // A site that answered is not retried: it would answer the same.
    assert.equal(partial.requests, 1);

    const alone = await resolveLaunchConfig({
      fingerprint: "host",
      _geoipEndpoints: [partial.url],
    });
    assert.equal(alone.locale, "ms");
    assert.equal(alone.timezone, null);
  } finally {
    console.warn = original;
    await partial.close();
    await full.close();
  }
});

test("no working GeoIP endpoint sends no override instead of a fabricated one", async () => {
  // A launch that cannot reach any site keeps sending no override at all: the
  // browser's own precedence settles locale and timezone, and nothing is
  // invented. What the message adds is the count, because "the lookup failed"
  // used to be indistinguishable from "the lookup was tried once".
  const first = await fakeGeoipEndpoint(null, 503);
  const second = await fakeGeoipEndpoint(null, 500);
  const warnings = [];
  const original = console.warn;
  console.warn = (line) => warnings.push(line);
  let config;
  try {
    config = await resolveLaunchConfig({
      fingerprint: "host",
      _geoipEndpoints: [first.url, second.url],
    });
  } finally {
    console.warn = original;
    await first.close();
    await second.close();
  }
  assert.equal(config.locale, null);
  assert.equal(config.timezone, null);
  const warning = warnings.join("\n");
  assert.match(warning, /every GeoIP endpoint failed: 4 attempts across 2 endpoints/);
  assert.match(warning, /No locale or timezone override is sent and none is invented/);
  assert.equal(/en-US|UTC/.test(warning.replace("GeoIP", "")), false);
});

// One connection, one RFC 1928 handshake, one HTTP reply. Small enough to read
// in full, which is the point: it is the thing that says whether the CONNECT
// carries a name or an address, so it must not be a second implementation of
// the same misunderstanding. python/tests/test_package.py drives the Python
// transport against the same shape.
async function fakeSocks5Server(payload) {
  const state = { addressType: null, host: null, port: null, credentials: null };
  const server = createSocket((connection) => {
    let stage = "greeting";
    let buffer = Buffer.alloc(0);
    connection.on("data", (chunk) => {
      buffer = Buffer.concat([buffer, chunk]);
      if (stage === "greeting" && buffer.length >= 2 + buffer[1]) {
        buffer = buffer.subarray(2 + buffer[1]);
        stage = "auth";
        connection.write(Buffer.from([0x05, 0x02]));
      }
      if (stage === "auth" && buffer.length >= 2) {
        const userLength = buffer[1];
        const passLength = buffer[2 + userLength];
        if (buffer.length < 3 + userLength + passLength) return;
        state.credentials = [
          buffer.subarray(2, 2 + userLength).toString("utf8"),
          buffer.subarray(3 + userLength, 3 + userLength + passLength).toString("utf8"),
        ];
        buffer = buffer.subarray(3 + userLength + passLength);
        stage = "connect";
        connection.write(Buffer.from([0x01, 0x00]));
      }
      if (stage === "connect" && buffer.length >= 5) {
        state.addressType = buffer[3];
        const length = state.addressType === 0x03 ? buffer[4] : (state.addressType === 0x01 ? 4 : 16);
        const start = state.addressType === 0x03 ? 5 : 4;
        if (buffer.length < start + length + 2) return;
        state.host = state.addressType === 0x03
          ? buffer.subarray(start, start + length).toString("utf8")
          : [...buffer.subarray(start, start + length)].join(".");
        state.port = buffer.readUInt16BE(start + length);
        buffer = buffer.subarray(start + length + 2);
        stage = "tunnel";
        connection.write(Buffer.from([0x05, 0, 0, 0x01, 127, 0, 0, 1, 0, 80]));
      }
      if (stage === "tunnel" && buffer.includes("\r\n\r\n")) {
        stage = "done";
        const body = JSON.stringify(payload);
        connection.end(`HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n`
          + `content-length: ${Buffer.byteLength(body)}\r\nconnection: close\r\n\r\n${body}`);
      }
    });
    connection.on("error", () => {});
  });
  await new Promise((ready) => server.listen(0, "127.0.0.1", ready));
  state.url = `socks5://geo%20user:p%40ss@127.0.0.1:${server.address().port}`;
  state.close = () => new Promise((closed) => server.close(closed));
  return state;
}

test("a socks5 GeoIP lookup hands the endpoint name to the proxy, never resolving it here", async () => {
  // The reported failure. socks5:// is the spelling a caller writes, because it
  // is what Chromium's --proxy-server takes, and socks-proxy-agent reads the
  // scheme literally: socks5: resolved the name on this host and connected to
  // the address it picked. dns.lookup put the endpoint's AAAA record first, a
  // residential exit with no IPv6 route answered "Socks5 proxy rejected
  // connection - Failure", and the launch fell back to the host's own locale
  // and timezone behind a proxy curl reached through socks5h:// at the same
  // moment. Worse than the outage: a DNS query for the GeoIP endpoint left
  // this network, and the lookup exists to learn where the exit is.
  const proxy = await fakeSocks5Server({ country_code: "MY", timezone: "Asia/Kuala_Lumpur", ip: "203.0.113.7" });
  try {
    const config = await resolveLaunchConfig({
      fingerprint: "host",
      proxy: proxy.url,
      _geoipEndpoints: ["http://exit.example.test/json"],
    });
    assert.equal(proxy.addressType, 3);
    assert.equal(proxy.host, "exit.example.test");
    assert.equal(proxy.port, 80);
    // RFC 1929 is sent decoded, not as the percent-encoded URL text.
    assert.deepEqual(proxy.credentials, ["geo user", "p@ss"]);
    assert.equal(config.locale, "ms");
    assert.equal(config.timezone, "Asia/Kuala_Lumpur");
    assert.equal(config.webrtc_ip, "203.0.113.7");
  } finally {
    await proxy.close();
  }
});