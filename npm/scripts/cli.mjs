#!/usr/bin/env node
// `npx apostate` -- install and run the browser from a terminal.
//
// Get the browser, find out where it is, run it, throw it away, and install
// the system fonts a persona lists. Mirrors python/apostate/cli.py.
import { spawn, spawnSync } from "node:child_process";
import {
  closeSync, copyFileSync, fstatSync, lstatSync, mkdirSync, mkdtempSync, openSync, readFileSync, readSync, readdirSync,
  rmSync, statSync, unlinkSync,
} from "node:fs";
import { homedir, tmpdir } from "node:os";
import { basename, dirname, extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { binaryInfo, clearCache, ensureBinary, ensureWidevine, CHROMIUM_VERSION, PACKAGE_VERSION } from "../dist/index.js";

// The Windows 11 font set. Cloned by the user's own machine, never shipped.
const WINDOWS_FONTS = "https://github.com/MauCariApa-com/windows-11-fonts";
const FONT_SUFFIXES = [".ttf", ".ttc", ".otf"];
// Where a Mac keeps the fonts its persona lists. PingFang lives in the private
// FontServices directory; downloaded fonts in the font asset folders.
const MAC_FONT_DIRS = [
  "/System/Library/Fonts",
  "/System/Library/Fonts/Supplemental",
  "/Library/Fonts",
  "/System/Library/PrivateFrameworks/FontServices.framework/Versions/A/Resources/Reserved",
];
const MAC_FONT_ASSETS = "/System/Library/AssetsV2";
// The font packs this package ships; the Windows core pack is what gets installed.
const FONT_PACKS = join(dirname(dirname(fileURLToPath(import.meta.url))), "assets", "font_packs.json");

const USAGE = `apostate ${PACKAGE_VERSION} (Chromium ${CHROMIUM_VERSION})

  apostate install [--force] [--keep-archive]   download, verify and extract the browser
  apostate path                                 print the executable path, installing if needed
  apostate info                                 print install and manifest state as JSON
  apostate clear                                delete the install cache
  apostate run [-- <browser args>]              run the browser, forwarding arguments
  apostate fonts install windows [--from <dir>] install the core Windows font set, from a clone or a Windows Fonts folder
  apostate fonts install macos --from <dir>     install the fonts export-macos wrote on a Mac
  apostate fonts export-macos <dir>             on a Mac, copy its system fonts into <dir>

  --cache-dir <dir>   override the install cache directory
  --manifest <src>    release manifest path, URL, or JSON file
  --target <target>   platform target; defaults to this host
`;

function listing(directory) {
  try {
    return readdirSync(directory).sort();
  } catch {
    return [];
  }
}

function fontFiles(directory) {
  return listing(directory).map((name) => join(directory, name)).filter((path) => {
    if (!FONT_SUFFIXES.includes(extname(path).toLowerCase())) return false;
    try {
      return statSync(path).isFile();
    } catch {
      return false;
    }
  });
}

// Font files in `directory` and every directory below it.
function fontFilesUnder(directory) {
  const found = fontFiles(directory);
  for (const name of listing(directory)) {
    try {
      if (lstatSync(join(directory, name)).isDirectory()) found.push(...fontFilesUnder(join(directory, name)));
    } catch {
      // An entry that cannot be read holds no fonts to report.
    }
  }
  return found;
}

// The font files a Mac keeps its own fonts in.
function macFontFiles() {
  const found = MAC_FONT_DIRS.flatMap(fontFiles);
  for (const asset of listing(MAC_FONT_ASSETS).filter((name) => name.startsWith("com_apple_MobileAsset_Font"))) {
    for (const entry of listing(join(MAC_FONT_ASSETS, asset))) {
      found.push(...fontFiles(join(MAC_FONT_ASSETS, asset, entry, "AssetData")));
    }
  }
  return found;
}

// This user's font directory for the `name` font set.
function fontDirectory(name) {
  return process.platform === "darwin"
    ? join(homedir(), "Library", "Fonts", `apostate-${name}`)
    : join(homedir(), ".local", "share", "fonts", `apostate-${name}`);
}

// Copy files into this user's font directory for `name` and refresh the cache.
function installFonts(files, name) {
  if (files.length === 0) throw new Error("no .ttf, .ttc or .otf files were found");
  const destination = fontDirectory(name);
  mkdirSync(destination, { recursive: true });
  for (const file of files) copyFileSync(file, join(destination, basename(file)));
  if (process.platform !== "darwin") {
    const cache = spawnSync("fc-cache", ["-f"], { stdio: "inherit" });
    if (cache.error?.code === "ENOENT") throw new Error("fc-cache is not installed; install fontconfig");
    if (cache.status !== 0) throw new Error(`fc-cache -f failed with exit code ${cache.status}`);
  }
  console.log(`installed ${files.length} font files into ${destination}`);
}

// The English family names of every face in a .ttf, .otf or .ttc file. Name ID
// 1 is the family Windows lists a face under: ARIALN.TTF is Arial Narrow there,
// although its typographic family (ID 16) is Arial. Names come from a face's
// Windows records, as Windows reads them, and from its Macintosh records only
// when it has none: Candaral.ttf is Candara Light on Windows and Candara in its
// Macintosh record. A file that cannot be read has no families.
function fontFamilies(path, nameIds = [1]) {
  const names = new Set();
  let fd;
  try {
    fd = openSync(path, "r");
    const end = fstatSync(fd).size;
    const read = (offset, size) => {
      if (offset + size > end) throw new Error("truncated font file");
      const buffer = Buffer.alloc(size);
      readSync(fd, buffer, 0, size, offset);
      return buffer;
    };
    let faces = [0];
    if (read(0, 4).toString("latin1") === "ttcf") {
      const count = read(8, 4).readUInt32BE(0);
      const offsets = read(12, 4 * count);
      faces = Array.from({ length: count }, (_, index) => offsets.readUInt32BE(4 * index));
    }
    for (const face of faces) {
      const tables = read(face + 4, 2).readUInt16BE(0);
      let table = -1;
      for (let index = 0; index < tables && table < 0; index += 1) {
        const record = read(face + 12 + 16 * index, 16);
        if (record.toString("latin1", 0, 4) === "name") table = record.readUInt32BE(8);
      }
      if (table < 0) continue;
      const header = read(table, 6);
      const records = header.readUInt16BE(2);
      const storage = table + header.readUInt16BE(4);
      const windows = new Set();
      const mac = new Set();
      for (let index = 0; index < records; index += 1) {
        const entry = read(table + 6 + 12 * index, 12);
        const [platform, , language, nameId, length, start] = [0, 2, 4, 6, 8, 10].map((at) => entry.readUInt16BE(at));
        if (!nameIds.includes(nameId)) continue;
        const raw = read(storage + start, length);
        // Windows English in any region is UTF-16BE; Macintosh English is Mac
        // Roman, which agrees with Latin-1 on the ASCII family names are made of.
        if (platform === 3 && (language & 0xff) === 0x09) windows.add(raw.subarray(0, length & ~1).swap16().toString("utf16le"));
        else if (platform === 1 && language === 0) mac.add(raw.toString("latin1"));
      }
      for (const name of windows.size > 0 ? windows : mac) names.add(name);
    }
  } catch {
    return new Set();
  } finally {
    if (fd !== undefined) closeSync(fd);
  }
  return names;
}

// The families of the Windows core font pack, as this package ships it.
function windowsCoreFamilies() {
  let families;
  try {
    const packs = JSON.parse(readFileSync(FONT_PACKS, "utf8"));
    families = new Set(packs.option_sets
      .filter((optionSet) => optionSet.key.platform === "windows")
      .flatMap((optionSet) => optionSet.options.filter((option) => option.pack_kind === "core"))
      .flatMap((option) => option.value.fonts.enumeration_allowlist));
  } catch {
    throw new Error("the font packs this package ships are unreadable");
  }
  if (families.size === 0) throw new Error("the font packs this package ships list no Windows core families");
  return [...families];
}

// Every family name this host's fonts answer to, lowercased; null if unknown.
function hostFamilies() {
  if (process.platform === "darwin") {
    const files = [...macFontFiles(), ...fontFilesUnder(join(homedir(), "Library", "Fonts"))];
    return new Set(files.flatMap((file) => [...fontFamilies(file, [1, 16])]).map((name) => name.toLowerCase()));
  }
  const listed = spawnSync("fc-list", ["--format", "%{family}\n"], { encoding: "utf8" });
  if (listed.error || listed.status !== 0) return null;
  return new Set(listed.stdout.split("\n").flatMap((line) => line.split(",")).map((name) => name.trim().toLowerCase()));
}

// Install the files of the Windows core families, and only those.
function installWindowsFonts(files) {
  if (files.length === 0) throw new Error("no .ttf, .ttc or .otf files were found");
  const core = windowsCoreFamilies();
  const wanted = new Set(core.map((family) => family.toLowerCase()));
  const isCore = (file) => [...fontFamilies(file)].some((family) => wanted.has(family.toLowerCase()));
  const selected = files.filter(isCore);
  if (selected.length === 0) throw new Error("none of the font files is in a core Windows family");
  // Earlier versions installed the whole repository. Files outside the core
  // set come out again, so the host holds the families a persona lists.
  const destination = fontDirectory("windows");
  const stale = fontFiles(destination).filter((file) => !isCore(file));
  for (const file of stale) unlinkSync(file);
  if (stale.length > 0) console.log(`removed ${stale.length} font files outside the core Windows set from ${destination}`);
  installFonts(selected, "windows");

  const present = hostFamilies();
  if (!present) return;
  const missing = core.filter((family) => !present.has(family.toLowerCase())).sort();
  if (missing.length === 0) {
    console.log("every core Windows family is installed");
    return;
  }
  console.log(`still missing ${missing.length} core Windows families: ${missing.join(", ")}`);
  console.log("add them from a Windows Fonts folder with `fonts install windows --from DIR`");
}

function fonts([action, value], from) {
  if (action === "export-macos") {
    if (process.platform !== "darwin") throw new Error("fonts export-macos runs on a Mac");
    if (!value) throw new Error("name the directory to copy the fonts into");
    const found = macFontFiles();
    // One file per name; the directory listed first wins.
    const files = new Map();
    for (const file of found) if (!files.has(basename(file))) files.set(basename(file), file);
    const destination = resolve(value);
    mkdirSync(destination, { recursive: true });
    for (const [name, file] of files) copyFileSync(file, join(destination, name));
    console.log(`copied ${files.size} font files into ${destination}`);
    return;
  }
  if (action !== "install" || (value !== "windows" && value !== "macos")) {
    throw new Error("usage: apostate fonts install windows | fonts install macos --from <dir> | fonts export-macos <dir>");
  }
  if (value === "windows") {
    if (process.platform === "win32") {
      console.log("this is a Windows machine; its fonts are already installed");
      return;
    }
    if (from) {
      installWindowsFonts(fontFiles(resolve(from)));
      return;
    }
    const scratch = mkdtempSync(join(tmpdir(), "apostate-fonts-"));
    try {
      const clone = join(scratch, "windows-11-fonts");
      const result = spawnSync("git", ["clone", "--depth", "1", WINDOWS_FONTS, clone], { stdio: "inherit" });
      if (result.error?.code === "ENOENT") throw new Error("git is not installed; install it and try again");
      if (result.status !== 0) throw new Error(`git clone ${WINDOWS_FONTS} failed with exit code ${result.status}`);
      installWindowsFonts(fontFiles(join(clone, "w11-fonts")));
    } finally {
      rmSync(scratch, { recursive: true, force: true });
    }
    return;
  }
  if (process.platform === "darwin") {
    console.log("this is a Mac; its fonts are already installed");
    return;
  }
  if (process.platform === "win32") throw new Error("installing macOS fonts on Windows is not supported");
  if (!from) throw new Error("pass --from <dir>, a directory `apostate fonts export-macos` wrote on a Mac");
  installFonts(fontFiles(resolve(from)), "macos");
}

const argv = process.argv.slice(2);
const options = {};
const rest = [];
let from;
for (let index = 0; index < argv.length; index += 1) {
  const item = argv[index];
  if (item === "--") { rest.push(...argv.slice(index + 1)); break; }
  if (item === "--cache-dir") { options.cacheDir = argv[++index]; continue; }
  if (item === "--manifest") { options.manifest = argv[++index]; continue; }
  if (item === "--target") { options.target = argv[++index]; continue; }
  if (item === "--from") { from = argv[++index]; continue; }
  if (item === "--force") { options.force = true; continue; }
  if (item === "--keep-archive") { process.env.APOSTATE_KEEP_ARCHIVE = "1"; continue; }
  if (item === "--version" || item === "-v") { console.log(`apostate ${PACKAGE_VERSION} (Chromium ${CHROMIUM_VERSION})`); process.exit(0); }
  if (item === "--help" || item === "-h") { console.log(USAGE); process.exit(0); }
  rest.push(item);
}

const command = rest.shift();
try {
  if (command === "clear") {
    await clearCache(options);
  } else if (command === "info") {
    console.log(JSON.stringify(await binaryInfo(options), null, 2));
  } else if (command === "install" || command === "path") {
    const executable = await ensureBinary(options);
    if (command === "install") await ensureWidevine(executable, options);
    console.log(executable);
  } else if (command === "fonts") {
    fonts(rest, from);
  } else if (command === "run") {
    const executable = await ensureBinary(options);
    const profile = rest.find((item) => item.startsWith("--user-data-dir="))?.slice("--user-data-dir=".length);
    await ensureWidevine(executable, { ...options, userDataDir: profile });
    // The browser owns the terminal and the exit code from here on.
    const child = spawn(executable, rest, { stdio: "inherit" });
    child.on("exit", (code, signal) => process.exit(signal ? 1 : code ?? 0));
  } else {
    console.log(USAGE);
    process.exit(command === undefined ? 1 : 2);
  }
} catch (error) {
  console.error(`apostate: ${error?.message ?? error}`);
  process.exit(1);
}
