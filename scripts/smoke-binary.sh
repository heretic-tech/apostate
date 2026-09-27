#!/usr/bin/env bash
# Prove that the binary a target just built exists and identifies itself.
#
# The per-target binary path is a property of the Chromium build, not of a
# workflow, so it lives here rather than being retyped in every CI job.
source "$(dirname "$0")/lib.sh"

TARGET="${1:-$(target_default)}"
OUT="$SRC/out/$TARGET"

case "$TARGET" in
  macos-arm64) binary="$OUT/Chromium.app/Contents/MacOS/Chromium" ;;
  linux-x64|linux-arm64) binary="$OUT/chrome" ;;
  windows-x64) binary="$OUT/chrome.exe" ;;
  *) die "unsupported target: $TARGET" ;;
esac

if [ "$TARGET" = windows-x64 ]; then
  # The executable bit is not meaningful on the Windows filesystem the runner
  # exposes through its POSIX layer.
  [ -f "$binary" ] || die "no built binary at $binary; run scripts/build.sh $TARGET"
else
  [ -x "$binary" ] || die "no built binary at $binary; run scripts/build.sh $TARGET"
fi

# Neither cross-built nor Windows binaries can be asked for their version, for
# different reasons, so both are checked by reading the file instead.
#
# linux-arm64 is an x86_64-hosted cross-build (see docs/contributing/build.mdx), so the
# binary cannot be executed on the machine that produced it. Check the machine
# type of the ELF instead of asking it for its version.
if [ "$TARGET" = linux-arm64 ]; then
  command -v file >/dev/null || die "file(1) is required to check a cross-built binary"
  format="$(file -b "$binary")"
  case "$format" in
    *aarch64*|*ARM64*|*"ARM aarch64"*) say "cross-built ARM64 binary: $format" ;;
    *) die "unexpected ARM64 binary format: $format" ;;
  esac
elif [ "$TARGET" = windows-x64 ]; then
  # `chrome.exe --version` must not be run, because on Windows it does not
  # print a version: chrome/app/chrome_main_delegate.cc calls
  # HandleVersionSwitches() inside `#if BUILDFLAG(IS_POSIX)` (line 1139 at this
  # pin), so --version is not handled at all off POSIX and falls straight
  # through into a full browser start. chrome.exe is also a GUI-subsystem
  # binary, so nothing arrives on stdout either way, and bash would then wait
  # on a browser that never exits -- spending the job's whole 600-minute
  # timeout on the most expensive runner of the four instead of failing.
  #
  # So ask the file, the way linux-arm64 does. chrome/app/
  # chrome_version.rc.version stamps FileVersion as MAJOR.MINOR.BUILD.PATCH out
  # of chrome/VERSION, and chrome/BUILD.gn links that resource into chrome.exe
  # through its :chrome_exe_version dependency, so this reads the version the
  # build itself stamped rather than one computed here.
  version="$(windows_file_version "$binary")" ||
    die "no FileVersion resource in $binary; chrome.exe did not link chrome_exe_version"
  [ "$version" = "$CHROMIUM_VERSION" ] ||
    die "$binary reports version $version, expected $CHROMIUM_VERSION"
  say "smoke $TARGET: $binary is $version"
else
  say "smoke $TARGET: $binary"
  "$binary" --version
fi
