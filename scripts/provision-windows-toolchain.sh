#!/usr/bin/env bash
# Install everything Chromium's Windows build needs that the runner image does
# not already carry, in one step, before any money is spent on a sync.
#
# Two installers, because the prerequisites come from two products:
#
#   1. Visual Studio components, through the VS installer's `modify --add`.
#      build/WINDOWS_VS_COMPONENTS lists them and the files that prove each one,
#      with the source for every entry. The one the image lacks is
#      Microsoft.VisualStudio.Component.VC.ATL: ATL ships only with Visual
#      Studio, is a separate component from the C++ toolset, and is what
#      "'atldef.h' file not found" means. A compiler and an SDK that are both
#      complete are not evidence that ATL is there, which is why the older
#      `vswhere -requires ...VC.Tools.x86.x64` check passed while the build
#      failed 33,797 edges in.
#
#   2. The Windows SDK's "Debugging Tools for Windows" feature, through the
#      pinned winsdksetup.exe. build/vs_toolchain.py treats
#      <SDK>/Debuggers/x64/dbghelp.dll as mandatory and raises without it.
#
# Both phases are idempotent: with everything present this does nothing, exits
# 0, and costs no download, so an image that gains a component later stops
# paying for it automatically.
#
# NEITHER PHASE TRUSTS AN EXIT CODE. The VS installer's documented codes
# include 3010, "Operation completed successfully, but install requires reboot
# before it can be used", and a hosted runner cannot take a reboot. Headers and
# import libraries need no registration -- clang-cl only reads them -- so a 3010
# is expected to be benign here, but "expected" is not a check. Each phase
# re-tests the same files it tested before installing, and those files are the
# only success signal. If ATL ever turns out to need the reboot it asked for,
# this fails naming the component instead of handing the gate a broken toolchain.
#
# PROVENANCE OF THAT CLAIM, because it matters which half is measured. The
# behaviour of this script on 0, 3010, 1641, each documented failure code and an
# undocumented one was demonstrated against a STUB installer that wrote a chosen
# exit code and optionally created the files, not against a real
# reboot-required install. So "a 3010 whose files did not land fails naming the
# component" is proven; "a real 3010 from this installer leaves usable headers"
# is reasoned from the fact that headers need no registration, and is exactly
# what the re-check exists to catch if the reasoning is wrong.
#
# It reports what it found either way -- the VS install path, the resolved MSVC
# toolset, the SDK version directories, and per component whether it was already
# there or installed by this run -- because every Windows failure on this project
# so far was diagnosed from one line of incidental log output, and this script
# runs on a 2 vCPU probe runner where logging is free.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) ;;
  *) die "provision-windows-toolchain.sh only runs on Windows" ;;
esac

pin() {
  local file="$REPO_ROOT/build/$1" value
  [ -f "$file" ] || die "missing build/$1"
  value="$(tr -d '[:space:]' < "$file")"
  [ -n "$value" ] || die "build/$1 is empty"
  printf '%s' "$value"
}

# One scratch directory and one trap for both phases. Two traps would mean the
# second silently replaced the first and phase 1's temporary files outlived the
# run.
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# ---------------------------------------------------------------------------
# Phase 1: Visual Studio components
# ---------------------------------------------------------------------------

say "visual studio install: ${vs2022_install:-<none resolved>}"
[ -n "${vs2022_install:-}" ] ||
  die "no Visual Studio 2022 install; scripts/lib.sh could not resolve one through vswhere.
Chromium needs Microsoft.VisualStudio.Component.VC.Tools.x86.x64 at minimum."

vs_unix="$(cygpath -u "$vs2022_install" 2>/dev/null || printf '%s' "$vs2022_install")"

# Every toolset present, not only the selected one: a second toolset appearing
# would change which directory the build resolves, and this listing is how that
# would be noticed.
say "MSVC toolsets under $vs_unix/VC/Tools/MSVC"
for _t in "$vs_unix"/VC/Tools/MSVC/*; do
  [ -d "$_t" ] || { printf '  (none)\n'; break; }
  printf '  %s\n' "$(basename "$_t")"
done

toolset="$(windows_msvc_toolset_root || true)"
[ -n "$toolset" ] ||
  die "no VC/Tools/MSVC/14.* toolset under $vs_unix.
Install Microsoft.VisualStudio.Component.VC.Tools.x86.x64."
say "selected toolset: $(basename "$toolset") (highest, matching build/vs_toolchain.py FindVCComponentRoot)"

# Whether the component payloads have to come off the network. Hosted images
# install Build Tools with --nocache, so this directory is normally absent or
# near-empty and every --add is a download from Microsoft. Reported rather than
# assumed, because it is the difference between a per-job network dependency and
# a local extract.
vs_cache="/c/ProgramData/Microsoft/VisualStudio/Packages"
if [ -d "$vs_cache" ]; then
  say "VS package cache present: $vs_cache ($(du -sm "$vs_cache" 2>/dev/null | cut -f1) MB)"
else
  say "VS package cache absent ($vs_cache); component payloads come from the network"
fi

# Which components are missing, by the probes the pin file names. A component is
# missing when ANY of its probes is absent: half an install is not an install.
missing=()
say "required Visual Studio components (build/WINDOWS_VS_COMPONENTS)"
while IFS=' ' read -r component probe; do
  [ -n "$component" ] || continue
  if [ -e "$toolset/$probe" ]; then
    printf '  present  %s  (%s)\n' "$probe" "$component"
  else
    printf '  MISSING  %s  (%s)\n' "$probe" "$component"
    case " ${missing[*]-} " in
      *" $component "*) ;;
      *) missing+=("$component") ;;
    esac
  fi
done < <(windows_vs_component_probes)

if [ "${#missing[@]}" -eq 0 ]; then
  say "all required Visual Studio components already installed"
else
  say "installing ${#missing[@]} missing component(s): ${missing[*]}"

  # Locate the installer rather than hardcoding it. Deriving it from the
  # resolved install is the reliable route: the installer always sits beside the
  # product roots, at <...>/Microsoft Visual Studio/Installer, so an image that
  # puts Visual Studio somewhere unexpected is still handled. The canonical
  # locations follow as fallbacks. setup.exe is the name Microsoft's own docs
  # use; vs_installer.exe is the same launcher under its other name.
  installer=""
  for _dir in \
    "$(dirname "$(dirname "$vs_unix")")/Installer" \
    "/c/Program Files (x86)/Microsoft Visual Studio/Installer" \
    "/c/Program Files/Microsoft Visual Studio/Installer"; do
    for _exe in setup.exe vs_installer.exe; do
      if [ -f "$_dir/$_exe" ]; then installer="$_dir/$_exe"; break 2; fi
    done
  done
  [ -n "$installer" ] ||
    die "no Visual Studio installer found; cannot add ${missing[*]}.
Searched for setup.exe and vs_installer.exe beside $vs_unix and in both
Program Files trees. Without it the components must be baked into the image."
  say "installer: $installer"

  # --channelId is documented as required for modify alongside --installPath.
  # vswhere reports the installed instance's own channel, so this cannot select
  # a different one by accident; omitted if vswhere has no answer rather than
  # guessed.
  channel="$(windows_vs_property channelId || true)"
  say "channel: ${channel:-<unset, omitting --channelId>}"

  # Driven from PowerShell rather than straight from bash for two reasons.
  # MSYS rewrites anything argument-shaped that contains a slash, and
  # --installPath is a Windows path with spaces. And the exit code has to
  # survive: bash sees only the low 8 bits of a process exit status, so 3010
  # would arrive as 194 and -1073720687 as noise. PowerShell writes the real
  # 32-bit value to a file and this reads it back.
  ps1="$work/add-components.ps1"
  code_file="$work/exitcode"
  cat > "$ps1" <<'PS1'
param(
  [Parameter(Mandatory = $true)][string]$Installer,
  [Parameter(Mandatory = $true)][string]$InstallPath,
  [Parameter(Mandatory = $true)][string]$CodeFile,
  [string]$ChannelId = '',
  [Parameter(Mandatory = $true)][string]$Components
)
$ErrorActionPreference = 'Stop'

# Start-Process joins -ArgumentList with spaces and quotes NOTHING, so an array
# containing "C:\Program Files (x86)\..." arrives at the installer as
# --installPath C:\Program, followed by Files and (x86)\Microsoft... as stray
# arguments. Measured: that is exactly how the first real run failed, exit 1
# after 7 seconds, with the unquoted path visible in this script's own echo.
# Quote here and pass one pre-joined string.
function Quote-Arg([string]$value) {
  if ($value -match '[\s"]') { return '"' + ($value -replace '"', '\"') + '"' }
  return $value
}

$argv = @('modify', '--installPath', (Quote-Arg $InstallPath))
if ($ChannelId -ne '') { $argv += @('--channelId', (Quote-Arg $ChannelId)) }
# Semicolon-separated rather than a PowerShell array parameter: powershell -File
# passes arguments as literal strings, so "A,B" would bind as one component id
# named "A,B" and the installer would reject it. Splitting here is unambiguous.
foreach ($c in ($Components -split ';' | Where-Object { $_ -ne '' })) {
  $argv += @('--add', (Quote-Arg $c))
}
# --quiet: no UI on a headless runner. --norestart: never reboot the runner out
# from under the job; a reboot-required result is reported as 3010 instead.
# --nocache: delete the payloads after installing, because Windows is the
# target with the least free disk and the packages are not needed again.
$argv += @('--quiet', '--norestart', '--nocache')
$commandLine = $argv -join ' '

# The installer's own logs are the only account of why it failed, and they are
# written to %TEMP% on a runner that stops existing when the job ends. Note the
# start time so only this run's logs get printed.
$started = Get-Date

Write-Host ('running: "{0}" {1}' -f $Installer, $commandLine)
$clock = [Diagnostics.Stopwatch]::StartNew()
$proc = Start-Process -FilePath $Installer -ArgumentList $commandLine -Wait -PassThru
$code = $proc.ExitCode

# The installer does not support --wait: "The --wait parameter can only be
# passed into the bootstrapper; the installer (setup.exe) doesn't support it."
# setup.exe hands the work to a separate engine, so -Wait can return while the
# install is still going. Wait for the installer processes to clear as well,
# bounded so a stuck installer fails the step instead of the job timeout.
$deadline = (Get-Date).AddMinutes(30)
$engines = @('setup', 'vs_installer', 'vs_installershell', 'vs_installerservice')
while ((Get-Date) -lt $deadline) {
  $live = @($engines | ForEach-Object { Get-Process -Name $_ -ErrorAction SilentlyContinue })
  if ($live.Count -eq 0) { break }
  Write-Host ('  waiting for {0} installer process(es) at {1:N0}s' -f $live.Count, $clock.Elapsed.TotalSeconds)
  Start-Sleep -Seconds 10
}
$clock.Stop()
Write-Host ('installer exit code {0} after {1:N0}s' -f $code, $clock.Elapsed.TotalSeconds)

if ($code -ne 0 -and $code -ne 3010 -and $code -ne 1641) {
  $logs = @(Get-ChildItem -Path $env:TEMP -Filter 'dd_*.log' -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -ge $started.AddSeconds(-5) } |
    Sort-Object LastWriteTime)
  if ($logs.Count -eq 0) {
    Write-Host 'no dd_*.log was written; the installer failed before it started logging'
  }
  foreach ($log in $logs) {
    Write-Host ('----- {0} (last 60 lines) -----' -f $log.FullName)
    Get-Content -Path $log.FullName -Tail 60 -ErrorAction SilentlyContinue |
      ForEach-Object { Write-Host $_ }
  }
}

Set-Content -Path $CodeFile -Value ([string]$code) -NoNewline
exit 0
PS1

  ps_args=(-NoProfile -ExecutionPolicy Bypass -File "$(cygpath -w "$ps1")"
    -Installer "$(cygpath -w "$installer")"
    -InstallPath "$vs2022_install"
    -CodeFile "$(cygpath -w "$code_file")")
  [ -z "$channel" ] || ps_args+=(-ChannelId "$channel")
  ps_args+=(-Components "$(IFS=';'; printf '%s' "${missing[*]}")")

  # The script itself always exits 0 and writes the installer's real code to a
  # file; a non-zero status here means PowerShell could not run it at all.
  MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' powershell "${ps_args[@]}" ||
    die "could not run the Visual Studio installer through PowerShell"

  rc="$(tr -d '[:space:]' < "$code_file" 2>/dev/null || true)"
  # Documented at
  # learn.microsoft.com/visualstudio/install/use-command-line-parameters-to-install-visual-studio
  # ("Error codes"). Only two of these are successes, and 3010 is one of them:
  # treating it as a failure would fail a run that installed everything asked
  # for. The file re-check below is what actually decides.
  case "$rc" in
    0) say "installer reported success" ;;
    3010) warn "installer reported 3010: installed, reboot required before use" ;;
    1641) warn "installer reported 1641: installed, and it initiated a reboot despite --norestart" ;;
    740) die "installer exit 740: elevation required" ;;
    1001) die "installer exit 1001: a Visual Studio installer process is already running" ;;
    1003) die "installer exit 1003: Visual Studio is in use" ;;
    1602|5004) die "installer exit $rc: operation was cancelled" ;;
    1618) die "installer exit 1618: another installation is running" ;;
    5003) die "installer exit 5003: bootstrapper failed to download the installer" ;;
    5005) die "installer exit 5005: command-line parse error; the arguments above are wrong" ;;
    5007) die "installer exit 5007: operation blocked, the machine does not meet the requirements" ;;
    8005) die "installer exit 8005: verifying source payloads failed" ;;
    8006) die "installer exit 8006: Visual Studio processes are running" ;;
    8010) die "installer exit 8010: operating system not supported" ;;
    -1073720687) die "installer exit -1073720687: connectivity failure reaching Microsoft's servers" ;;
    -1073741510) die "installer exit -1073741510: the installer was terminated" ;;
    "") die "the installer wrote no exit code; it did not run to completion" ;;
    *) die "installer exit $rc: undocumented. Its own dd_*.log tails are printed above." ;;
  esac

  # Re-resolve the toolset: an --add can create a newer VC/Tools/MSVC directory,
  # and checking the old one would then report a failure that is not real.
  toolset="$(windows_msvc_toolset_root || true)"
  [ -n "$toolset" ] || die "no VC/Tools/MSVC/14.* toolset after installing; the install did not land"
  say "toolset after install: $(basename "$toolset")"

  still=()
  while IFS=' ' read -r component probe; do
    [ -n "$component" ] || continue
    [ -e "$toolset/$probe" ] || still+=("$component: $toolset/$probe")
  done < <(windows_vs_component_probes)
  if [ "${#still[@]}" -gt 0 ]; then
    printf 'error: the installer exited %s but these are still absent:\n' "${rc:-?}" >&2
    for entry in "${still[@]}"; do printf '  - %s\n' "$entry" >&2; done
    printf 'A 3010 means a reboot is needed before the install can be USED. If that is\n' >&2
    printf 'what happened, provisioning at job start cannot work for these components and\n' >&2
    printf 'they have to be baked into the runner image instead.\n' >&2
    exit 1
  fi
  say "every required Visual Studio component is present after installing"
  if [ -d "$toolset/atlmfc" ]; then
    printf '  atlmfc payload: %s MB\n' "$(du -sm "$toolset/atlmfc" | cut -f1)"
  fi
fi

# ---------------------------------------------------------------------------
# Phase 2: the Windows SDK, at the pinned servicing revision
# ---------------------------------------------------------------------------
#
# This phase used to install only the Debugging Tools feature, and its
# idempotency test was "does dbghelp.dll exist". Both were too narrow, and the
# sixth Windows defect is what that cost: the image carries SDK 10.0.26100 at
# servicing revision 4654, Chromium's own
# docs/windows_build_instructions.md:54-56 requires 10.0.26100.7705, and
# ui/accessibility/platform/uia_client_info_source_win.cc uses
# IUIAutomationClientInfo unconditionally. The gate reached 26,642 of 33,797
# edges before failing on "unknown type name". A dbghelp-only test would have
# skipped this install entirely and never touched the headers.
#
# So the predicate is now build/WINDOWS_SDK_REQUIREMENTS -- the revision table
# -- and the payload comes from two places rather than one, because they are
# different jobs with different costs:
#
#   The headers and the x64 libraries are overlaid from Microsoft's own NuGet
#   payload packages, pinned by digest in build/WINDOWS_SDK_PACKAGES. Seconds,
#   and the bytes that land are the bytes we named.
#
#   The Debugging Tools come from the pinned winsdksetup.exe, which does that
#   one feature in about 40 seconds, and only when dbghelp.dll is missing.
#
# Asking winsdksetup.exe for the headers instead is what the first attempt did,
# and it does not finish: 25 minutes, package 004 of many, exit 124. The
# reasoning and the numbers are in build/WINDOWS_SDK_PACKAGES. The installer is
# still pinned by URL and by SHA-256, because a pinned URL only promises a name.
#
# Which Include/Lib DIRECTORY the build uses is not decided here and cannot
# drift: build/vs_toolchain.py hardcodes SDK_VERSION = '10.0.28000.0' and
# prints it verbatim as gn's sdk_version, with
# build/toolchain/win/setup_toolchain.py holding a second copy as a
# cross-check. That is also precisely why the revision needs its own table: an
# upgrade from 4654 to 7705 changes the headers inside that directory and does
# not change its name. This script logs the version directories before and
# after, because a component of the pinned version DISAPPEARING is a real
# failure and the listing is how it would be recognised.

sdk_root="$(windows_sdk_root)"
[ -d "$sdk_root" ] || die "no Windows SDK at $sdk_root"
dbghelp="$sdk_root/Debuggers/x64/dbghelp.dll"

list_versions() {
  for dir in bin Include Lib; do
    printf '  %s/%s: %s\n' "$(basename "$sdk_root")" "$dir" \
      "$(ls "$sdk_root/$dir" 2>/dev/null | tr '\n' ' ' || true)"
  done
}

# Every reason this phase might need to run, gathered rather than short-
# circuited, so one log says what the image is short of instead of one thing
# per run. Same reason verify-host-tooling.sh reports every gap.
sdk_gaps=()
[ -f "$dbghelp" ] || sdk_gaps+=("Debuggers/x64/dbghelp.dll is absent")
while IFS=' ' read -r kind path expected; do
  [ -n "$kind" ] || continue
  case "$kind" in
    symbol)
      windows_sdk_has_symbol "$sdk_root/$path" "$expected" ||
        sdk_gaps+=("$expected is not declared under $path")
      ;;
    libsymbol)
      windows_sdk_lib_has_symbol "$sdk_root/$path" "$expected" ||
        sdk_gaps+=("$expected is not defined by $path")
      ;;
    version)
      got="$(windows_file_version "$sdk_root/$path" || true)"
      if [ -z "$got" ]; then
        sdk_gaps+=("no FileVersion readable from $path")
      elif ! version_at_least "$got" "$expected"; then
        sdk_gaps+=("$path is $got, older than the required $expected")
      fi
      ;;
    *) die "build/WINDOWS_SDK_REQUIREMENTS has an unknown requirement kind '$kind'" ;;
  esac
done < <(windows_sdk_requirements)

say "SDK version directories present"
list_versions

if [ "${#sdk_gaps[@]}" -eq 0 ]; then
  say "the Windows SDK already satisfies every pinned requirement"
  exit 0
fi

say "${#sdk_gaps[@]} SDK requirement(s) unmet:"
for gap in "${sdk_gaps[@]}"; do printf '  %s\n' "$gap"; done

url="$(pin WINDOWS_SDK_INSTALLER_URL)"
version="$(pin WINDOWS_SDK_INSTALLER_VERSION)"
want_sha="$(pin WINDOWS_SDK_INSTALLER_SHA256)"

sdk_version="$(windows_sdk_version)"

# --- the headers and libraries, from the pinned NuGet payload packages -------
#
# Not from winsdksetup.exe. Measured on blacksmith-2vcpu-windows-2025: asking it
# for OptionId.DesktopCPPx64, OptionId.DesktopCPPx86 and the debuggers ran the
# full 25-minute bound and had reached only package 004 before being killed,
# exit 124. Slow rather than stuck, but a quarter of the gate's budget before
# the first compile, on every run, is not a price worth paying for a file copy.
# build/WINDOWS_SDK_PACKAGES has the numbers and the provenance argument.
overlaid=0
while IFS=' ' read -r pkg sha dest; do
  [ -n "$pkg" ] || continue
  nupkg="$work/$pkg.$version.nupkg"
  pkg_url="https://api.nuget.org/v3-flatcontainer/$pkg/$version/$pkg.$version.nupkg"
  say "downloading $pkg $version"
  curl --fail --location --silent --show-error --retry 3 --output "$nupkg" "$pkg_url" ||
    die "could not download $pkg_url
build/WINDOWS_SDK_PACKAGES names this package and
build/WINDOWS_SDK_INSTALLER_VERSION names the version. A 404 means that
combination does not exist on nuget.org; check the version before repinning."
  got_pkg_sha="$(sha256sum "$nupkg" | cut -d' ' -f1)"
  [ "$got_pkg_sha" = "$sha" ] ||
    die "$pkg digest does not match build/WINDOWS_SDK_PACKAGES
  expected $sha
  got      $got_pkg_sha"
  say "$pkg sha256 $got_pkg_sha ($(( $(wc -c < "$nupkg") / 1048576 )) MB)"

  # A .nupkg is a zip. 7z is already required on this target for
  # scripts/package-artifact.sh, so nothing new is needed to read one.
  extract="$work/x-$pkg"
  mkdir -p "$extract"
  # -o glues its path to the flag, and that is the one argument shape MSYS2's
  # conversion rules do not clearly cover. A bare POSIX path argument to a
  # native child IS converted -- measured on this runner, where `git apply` and
  # `python3` both received absolute /d/... paths and resolved them -- and so is
  # the part after an `=`, but `-o/tmp/x` is neither of those and we have no
  # Windows host to settle it on. If it passes through verbatim, 7z reads /tmp/x
  # as a Windows path, extracts to C:\tmp\x, and the cp below finds nothing at
  # $extract. Handing 7z the Windows form makes the answer stop mattering.
  # $nupkg is a bare argument and needs no help.
  extract_win="$(cygpath -m "$extract" 2>/dev/null || printf '%s' "$extract")"
  7z x -bso0 -bsp0 -y -o"$extract_win" "$nupkg" > /dev/null ||
    die "could not extract $nupkg"

  for part_dest in ${dest//,/ }; do
  case "$part_dest" in
    include)
      src="$extract/c/Include/$sdk_version"
      [ -d "$src" ] ||
        die "$pkg has no c/Include/$sdk_version; the package layout changed or
build/WINDOWS_SDK_VERSION no longer matches the packages' directory name."
      say "overlaying headers into $sdk_root/Include/$sdk_version"
      mkdir -p "$sdk_root/Include/$sdk_version"
      cp -rf "$src/." "$sdk_root/Include/$sdk_version/" ||
        die "could not overlay headers; the SDK tree may not be writable"
      ;;
    bin)
      # midl.exe and the other SDK tools the build runs from PATH. An image
      # that does not carry this SDK major has no bin/<version> at all.
      for arch in x64 x86; do
        src="$extract/c/bin/$sdk_version/$arch"
        [ -d "$src" ] || die "$pkg has no c/bin/$sdk_version/$arch"
        say "overlaying $arch tools into $sdk_root/bin/$sdk_version/$arch"
        mkdir -p "$sdk_root/bin/$sdk_version/$arch"
        cp -rf "$src/." "$sdk_root/bin/$sdk_version/$arch/" ||
          die "could not overlay $arch tools"
      done
      ;;
    lib-x64|lib-x86)
      arch="${part_dest#lib-}"
      for part in um ucrt; do
        src="$extract/c/$part/$arch"
        [ -d "$src" ] || die "$pkg has no c/$part/$arch"
        say "overlaying $part $arch libraries into $sdk_root/Lib/$sdk_version/$part/$arch"
        mkdir -p "$sdk_root/Lib/$sdk_version/$part/$arch"
        cp -rf "$src/." "$sdk_root/Lib/$sdk_version/$part/$arch/" ||
          die "could not overlay $part $arch libraries"
      done
      ;;
    *) die "build/WINDOWS_SDK_PACKAGES has an unknown destination '$part_dest'" ;;
  esac
  done
  overlaid=$((overlaid + 1))
done < <(windows_sdk_packages)
say "overlaid $overlaid pinned SDK package(s) at $version"

# --- the Debugging Tools, from the pinned installer --------------------------
#
# These are not in the NuGet payload packages, and winsdksetup.exe installs this
# one feature in about 40 seconds, so it stays. Only requested when it is
# actually missing, which keeps the common case free.
if [ ! -f "$dbghelp" ]; then
  installer_exe="$work/winsdksetup.exe"

  say "downloading pinned Windows SDK $version installer"
  curl --fail --location --silent --show-error --retry 3 --output "$installer_exe" "$url" ||
    die "could not download the pinned SDK installer from $url"
  [ -s "$installer_exe" ] || die "downloaded installer is empty"

  got_sha="$(sha256sum "$installer_exe" | cut -d' ' -f1)"
  say "installer sha256 $got_sha"
  [ "$got_sha" = "$want_sha" ] ||
    die "installer digest does not match build/WINDOWS_SDK_INSTALLER_SHA256
  expected $want_sha
  got      $got_sha
The pinned URL served different bytes. Verify the release before repinning."

  # A DEADLINE ENFORCED BY THE LAYER ABOVE THE THING BEING MEASURED IS THE WRONG
  # INSTRUMENT, BECAUSE IT DESTROYS EXACTLY THE EVIDENCE YOU NEEDED. That is the
  # general rule; here is the instance that taught it. An earlier version of this
  # phase ran winsdksetup.exe for 43m43s, the 45-minute job timeout killed the
  # step, the run was reported as "cancelled", and the installer's output went
  # with it -- leaving no way to tell a slow install from a stuck one, which was
  # the only question that mattered. The rule applies to every timeout in this
  # repository that is owned by a workflow rather than by the script it bounds.
  #
  # So: a deadline this script owns, the installer's own /log, and the tail
  # printed whether it succeeds, fails or runs out of time.
  APOSTATE_SDK_INSTALL_TIMEOUT="${APOSTATE_SDK_INSTALL_TIMEOUT:-10m}"
  sdk_log="$work/winsdk-install.log"
  say "installing OptionId.WindowsDesktopDebuggers, bounded at $APOSTATE_SDK_INSTALL_TIMEOUT"
  started="$(date -u +%s)"
  # MSYS rewrites arguments that look like paths, so /features would arrive as a
  # Windows path and the installer would reject it.
  set +e
  MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' \
    timeout --signal=TERM --kill-after=60 "$APOSTATE_SDK_INSTALL_TIMEOUT" \
    "$installer_exe" /features OptionId.WindowsDesktopDebuggers \
      /quiet /norestart /log "$(cygpath -w "$sdk_log" 2>/dev/null || printf '%s' "$sdk_log")"
  sdk_rc=$?
  set -e
  elapsed="$(( $(date -u +%s) - started ))"
  say "winsdksetup.exe exited $sdk_rc after ${elapsed}s"

  # Whatever happened, show what the installer said. winsdksetup writes several
  # files beside the path given to /log, so take them all.
  for log in "$sdk_log" "$sdk_log".*; do
    [ -f "$log" ] || continue
    printf -- '----- %s (last 40 lines) -----\n' "$(basename "$log")"
    tail -40 "$log" | tr -d '\r' || true
  done

  # 3010 is ERROR_SUCCESS_REBOOT_REQUIRED: installed, reboot pending. The files
  # are in place, and the requirement re-check below is the real success signal.
  case "$sdk_rc" in
    0) ;;
    3010) say "installer reported a pending reboot (3010); files are in place" ;;
    124|137) die "winsdksetup.exe did not finish within $APOSTATE_SDK_INSTALL_TIMEOUT.
Its log tail is above. The Debuggers feature alone has measured about 40
seconds, so a timeout here means something changed rather than that the bound
is too tight." ;;
    *) die "winsdksetup.exe exited $sdk_rc; its log tail is above" ;;
  esac
else
  say "debugging tools already present; not running the SDK installer"
fi

say "SDK version directories after provisioning"
list_versions

# Re-test the same requirements, not a proxy for them. An installer that
# reported success while leaving an older header in place is the exact failure
# this phase exists to stop, and its exit code cannot be asked about that.
remaining=()
[ -f "$dbghelp" ] || remaining+=("Debuggers/x64/dbghelp.dll is still absent")
while IFS=' ' read -r kind path expected; do
  [ -n "$kind" ] || continue
  case "$kind" in
    symbol)
      windows_sdk_has_symbol "$sdk_root/$path" "$expected" ||
        remaining+=("$expected is still not declared under $path")
      ;;
    libsymbol)
      windows_sdk_lib_has_symbol "$sdk_root/$path" "$expected" ||
        remaining+=("$expected is still not defined by $path")
      ;;
    version)
      got="$(windows_file_version "$sdk_root/$path" || true)"
      if [ -z "$got" ]; then
        remaining+=("still no FileVersion readable from $path")
      elif ! version_at_least "$got" "$expected"; then
        remaining+=("$path is still $got, older than $expected")
      else
        say "$path is now $got"
      fi
      ;;
  esac
done < <(windows_sdk_requirements)

if [ "${#remaining[@]}" -gt 0 ]; then
  printf 'error: provisioning finished but the SDK still does not satisfy:\n' >&2
  for gap in "${remaining[@]}"; do printf '  - %s\n' "$gap" >&2; done
  printf 'The pinned revision is %s. A missing symbol here means either that\n' "$version" >&2
  printf 'build/WINDOWS_SDK_PACKAGES overlaid the wrong tree, or that the symbol is\n' >&2
  printf 'not in this SDK revision at all and the row in\n' >&2
  printf 'build/WINDOWS_SDK_REQUIREMENTS should not be there. Confirm against the\n' >&2
  printf 'NuGet package before assuming the runner is at fault -- that check runs\n' >&2
  printf 'from any host and takes half a minute.\n' >&2
  exit 1
fi
say "the Windows SDK satisfies every pinned requirement at revision $version"
