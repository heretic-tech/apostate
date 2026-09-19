import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { createServer, request as httpRequest } from "node:http";
import { chmod, lstat, mkdir, readFile, readdir, readlink, writeFile } from "node:fs/promises";
import { mkdtemp, rm } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { tmpdir } from "node:os";
import test from "node:test";
import {
  BinaryExtractionError,
  CATALOGUE_VERSION,
  CHROMIUM_VERSION,
  ProfileResolutionError,
  UnpublishedArtifactError,
  binaryInfo,
  discoveryReport,
  ensureBinary,
  expectedArtifactName,
  launch,
  launchProcess,
  launchContext,
  launchPersistentContext,
  provisionWidevine,
  WidevineError,
  loadCatalogue,
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

test("launch reports an unpublished package before fabricating a browser", async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-launch-"));
  try {
    await assert.rejects(
      launch({ target, searchRoots: [], cacheDir, geoip: false, fingerprint: "host" }),
      (error) => error instanceof UnpublishedArtifactError,
    );
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
  }
});

test("provisioned Widevine survives a forced reinstall", async () => {
  // The CDM is stored outside the install tree precisely so that --force and a
  // Chromium upgrade, which both replace that tree, do not silently remove DRM
  // and turn a working launch into a NotSupportedError a site reads in one call.
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-widevine-"));
  try {
    const archive = Buffer.from("archive bytes");
    const options = {
      target: "macos-arm64",
      searchRoots: [],
      cacheDir,
      manifest: releaseManifest(archive, "macos-arm64"),
      download: async () => archive,
      extract: async (_bytes, destination) => {
        const tree = join(destination, "tree");
        await mkdir(join(tree, "Chromium.app/Contents/MacOS"), { recursive: true });
        await writeFile(join(tree, "Chromium.app/Contents/MacOS/Chromium"), "binary bytes");
        return tree;
      },
    };
    await ensureBinary(options);

    // A CDM in the component-updater layout, i.e. with a version directory.
    const source = join(cacheDir, "fetched", "WidevineCdm", "4.10.3050.0");
    await mkdir(join(source, "_platform_specific", "mac_arm64"), { recursive: true });
    await writeFile(join(source, "_platform_specific", "mac_arm64", "libwidevinecdm.dylib"), "cdm");
    await writeFile(join(source, "manifest.json"), JSON.stringify({ version: "4.10.3050.0" }));

    const result = await provisionWidevine({
      target: "macos-arm64", cacheDir, source: join(cacheDir, "fetched", "WidevineCdm"),
    });
    assert.equal(result.version, "4.10.3050.0");
    assert.ok(result.installed, "a present install must receive the CDM");
    const library = join(result.installed, "_platform_specific", "mac_arm64", "libwidevinecdm.dylib");
    assert.equal(await readFile(library, "utf8"), "cdm");
    // No version directory: the browser reads it from manifest.json, and
    // Google Chrome's own bundled copy has none.
    assert.deepEqual((await readdir(result.installed)).sort(), ["_platform_specific", "manifest.json"]);

    await ensureBinary({ ...options, force: true });
    assert.equal(await readFile(library, "utf8"), "cdm", "reinstall must re-apply the CDM");
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
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
      ["a result with no locale", { geoipResolver: async () => ({ timezone: "Europe/Berlin" }) },
        ["--fingerprint-timezone=Europe/Berlin"], "resolved no locale"],
      // freeipapi answers `timeZone` with a UTC offset, which cannot drive
      // --fingerprint-timezone. Unresolved, not adjusted into a lookalike.
      ["an offset instead of an identifier",
        { geoipResolver: async () => ({ locale: "de-DE", timeZone: "+02:00" }) },
        ["--fingerprint-locale=de-DE"], "resolved no timezone"],
      // A country code derives its locale from the Apostate-owned table that
      // scripts/geoip.py and the Python package share, so a German exit is
      // de-DE. The old code built `en-${countryCode}` and served en-DE.
      ["a country code and a timezone",
        { geoipResolver: async () => ({ country_code: "DE", timezone: "Europe/Berlin" }) },
        ["--fingerprint-locale=de-DE", "--fingerprint-timezone=Europe/Berlin"], null],
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
      assert.equal(seen.env.TZ, expected.some((arg) => arg.startsWith("--fingerprint-timezone"))
        ? "Europe/Berlin" : undefined, label);
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
