#!/usr/bin/env node
// `npx apostate` -- install and run the browser from a terminal.
//
// Get the browser, find out where it is, run it, throw it away, and install
// the system fonts a persona lists. Mirrors python/apostate/cli.py.
import { spawn, spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, readdirSync, rmSync, statSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { basename, extname, join, resolve } from "node:path";
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

const USAGE = `apostate ${PACKAGE_VERSION} (Chromium ${CHROMIUM_VERSION})

  apostate install [--force] [--keep-archive]   download, verify and extract the browser
  apostate path                                 print the executable path, installing if needed
  apostate info                                 print install and manifest state as JSON
  apostate clear                                delete the install cache
  apostate run [-- <browser args>]              run the browser, forwarding arguments
  apostate fonts install windows                install the Windows 11 font set
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

// Copy files into this user's font directory for `name` and refresh the cache.
function installFonts(files, name) {
  if (files.length === 0) throw new Error("no .ttf, .ttc or .otf files were found");
  const destination = process.platform === "darwin"
    ? join(homedir(), "Library", "Fonts", `apostate-${name}`)
    : join(homedir(), ".local", "share", "fonts", `apostate-${name}`);
  mkdirSync(destination, { recursive: true });
  for (const file of files) copyFileSync(file, join(destination, basename(file)));
  if (process.platform !== "darwin") {
    const cache = spawnSync("fc-cache", ["-f"], { stdio: "inherit" });
    if (cache.error?.code === "ENOENT") throw new Error("fc-cache is not installed; install fontconfig");
    if (cache.status !== 0) throw new Error(`fc-cache -f failed with exit code ${cache.status}`);
  }
  console.log(`installed ${files.length} font files into ${destination}`);
}

function fonts([action, value], from) {
  if (action === "export-macos") {
    if (process.platform !== "darwin") throw new Error("fonts export-macos runs on a Mac");
    if (!value) throw new Error("name the directory to copy the fonts into");
    const found = MAC_FONT_DIRS.flatMap(fontFiles);
    for (const asset of listing(MAC_FONT_ASSETS).filter((name) => name.startsWith("com_apple_MobileAsset_Font"))) {
      for (const entry of listing(join(MAC_FONT_ASSETS, asset))) {
        found.push(...fontFiles(join(MAC_FONT_ASSETS, asset, entry, "AssetData")));
      }
    }
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
    const scratch = mkdtempSync(join(tmpdir(), "apostate-fonts-"));
    try {
      const clone = join(scratch, "windows-11-fonts");
      const result = spawnSync("git", ["clone", "--depth", "1", WINDOWS_FONTS, clone], { stdio: "inherit" });
      if (result.error?.code === "ENOENT") throw new Error("git is not installed; install it and try again");
      if (result.status !== 0) throw new Error(`git clone ${WINDOWS_FONTS} failed with exit code ${result.status}`);
      installFonts(fontFiles(join(clone, "w11-fonts")), "windows");
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
