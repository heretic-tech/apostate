#!/usr/bin/env bash
# Fetch the pinned depot_tools and assert the host can build. Idempotent.
source "$(dirname "$0")/lib.sh"

case "$(uname -s)" in
  Darwin)
    command -v xcodebuild >/dev/null || die "Xcode command line tools not installed"
    command -v plutil >/dev/null || die "plutil is required to verify an SDK build version"
    # Resolve the exact SDK version/build before creating the workspace or
    # fetching depot_tools. --resolve-only reports installed SDKs on failure
    # and never writes the checkout symlink.
    mac_sdk_version="$(tr -d '[:space:]' < "$REPO_ROOT/build/MAC_SDK_VERSION")"
    mac_sdk_build="$(tr -d '[:space:]' < "$REPO_ROOT/build/MAC_SDK_BUILD")"
    mac_sdk="$(bash "$REPO_ROOT/scripts/prepare-mac-sdk.sh" --resolve-only)"
    active="$(xcrun --sdk macosx --show-sdk-version 2>/dev/null || true)"
    say "macOS SDK $mac_sdk_version ($mac_sdk_build) at $mac_sdk"
    if [ "$active" != "$mac_sdk_version" ]; then
      # Not a failure. The active Xcode's own SDK is irrelevant once
      # mac_sdk_path is pinned, and on a host upgraded past the pin the
      # pinned SDK legitimately lives only in the Command Line Tools copy.
      say "active Xcode ships SDK $active; the build uses the pinned $mac_sdk_version regardless"
    fi
    ;;
  Linux)
    command -v python3 >/dev/null || die "python3 missing"
    ;;
esac

mkdir -p "$WORKSPACE"

if [ ! -d "$DEPOT_TOOLS/.git" ]; then
  say "cloning depot_tools"
  git clone -q https://chromium.googlesource.com/chromium/tools/depot_tools.git "$DEPOT_TOOLS"
fi

say "pinning depot_tools to $DEPOT_TOOLS_REVISION"
git -C "$DEPOT_TOOLS" fetch -q origin
git -C "$DEPOT_TOOLS" checkout -q --detach "$DEPOT_TOOLS_REVISION"

actual="$(git -C "$DEPOT_TOOLS" rev-parse HEAD)"
[ "$actual" = "$DEPOT_TOOLS_REVISION" ] || die "depot_tools is at $actual, expected $DEPOT_TOOLS_REVISION"

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    # A fresh depot_tools clone has no git.bat. depot_tools ships its Windows
    # tool bundle as CIPD packages and generates the .bat entry points from
    # templates in bootstrap/, and gclient_scm hardcodes git_exe = "git.bat" on
    # win32 -- so every git operation inside a sync needs a file that only
    # bootstrap/win_tools.bat creates. Without it the sync dies inside
    # git_cache.GetCachePath with WinError 2, because that function catches
    # CalledProcessError but not a missing executable.
    #
    # Invoking gclient through the POSIX entry point is what exposes this. The
    # .bat entry points call win_tools.bat themselves; the shell scripts only
    # bootstrap .cipd_bin, which is why vpython3 works and git.bat does not.
    # DEPOT_TOOLS_UPDATE=0 is not implicated: it suppresses the self-update of
    # the depot_tools checkout, and the tool bundle still has to exist.
    #
    # This does not compromise the pin. win_tools.bat installs exactly the
    # versions in bootstrap/manifest.txt, which is a tracked file at
    # $DEPOT_TOOLS_REVISION, and it leaves the checkout's own revision alone.
    # It will not touch global git config either: those writes are gated on
    # depot-tools.allowGlobalGitConfig, which we never set.
    #
    # cmd is given //c rather than /c: MSYS rewrites a lone /c into a Windows
    # path, and doubling the slash is the documented way to opt out for one
    # argument. The script path keeps backslashes so it is passed through.
    say "materialising the depot_tools windows tool bundle"
    ( cd "$DEPOT_TOOLS" && cmd //c "bootstrap\\win_tools.bat" ) ||
      die "bootstrap/win_tools.bat failed; depot_tools cannot run git on Windows without it"
    # Only the two files depot_tools generates. vpython3.bat and cipd.bat are
    # tracked, so a fresh clone already has them and checking them would assert
    # nothing; git.bat and python3.bat are in depot_tools' own .gitignore
    # because bootstrap.py writes them.
    for stub in git.bat python3.bat; do
      [ -f "$DEPOT_TOOLS/$stub" ] ||
        die "bootstrap/win_tools.bat reported success but did not create $stub. depot_tools is half-installed; delete $DEPOT_TOOLS and re-run."
    done
    ;;
esac

# Assert that PATH resolves gclient, not merely that the file exists. A broken
# PATH entry is invisible to an existence check: the Windows measurement had a
# perfectly good $DEPOT_TOOLS/gclient on disk and still died 25 minutes into
# the sync with "gclient: command not found", because PATH is colon-separated
# and the drive letter had split the entry in two. Bootstrap owns "depot_tools
# is usable", so the check belongs here, where it costs milliseconds.
#
# Both sides are resolved through `cd && pwd -P` so the comparison is between
# physical directories rather than spellings -- on Windows `command -v` reports
# /c/... while $DEPOT_TOOLS is C:/..., and both forms print identically after
# cd, which also collapses symlinks such as macOS's /tmp.
gclient_bin="$(command -v gclient || true)"
[ -n "$gclient_bin" ] ||
  die "gclient is not on PATH. depot_tools is pinned at $DEPOT_TOOLS but PATH does not resolve it: $PATH"
gclient_dir="$(cd "$(dirname "$gclient_bin")" && pwd -P)"
depot_dir="$(cd "$DEPOT_TOOLS" && pwd -P)"
[ "$gclient_dir" = "$depot_dir" ] ||
  die "PATH resolves gclient to $gclient_dir, but this build is pinned to the depot_tools at $depot_dir. Remove the other depot_tools from PATH."

# Two tiers, because one number cannot do both jobs.
#
# The floor is arithmetic: a build that starts with less space than it is going
# to consume will spend hours on an expensive runner before dying of ENOSPC
# inside ninja. Failing here costs seconds and says why.
#
# The floors are measured totals -- checkout plus output plus depot_tools --
# and they differ between targets by more than a factor of two, so they are
# per-target. The Windows figure used to be extrapolated from macOS, as "the
# checkout alone measures 46GB"; a completed Windows sync measured 28.1GB. The
# extrapolation was wrong because macOS additionally fetches
# third_party/swift-toolchain at 3.9GB and carries a much larger third_party
# overall. Sizing one target from another's measurement is the error, so each
# number below names the run it came from.
case "${APOSTATE_TARGET:-}" in
  # 66.6GB measured on a complete build: 50 checkout, 16 output, 0.7 depot_tools.
  macos-arm64) need_gb=68 ;;
  # 44.8GB measured: 28.1 checkout (run 35262879677), 16 output, 0.7 depot_tools.
  windows-x64) need_gb=46 ;;
  # Linux is unmeasured -- both Linux targets have only ever run on runners with
  # over 1TB free, so no run has produced a figure. Use the largest known.
  *) need_gb=68 ;;
esac

avail_kb="$(df -Pk "$WORKSPACE" | awk 'NR==2{print $4}')"
say "workspace has $((avail_kb / 1048576))GB free at $WORKSPACE"
[ "$avail_kb" -gt $((need_gb * 1048576)) ] ||
  die "only $((avail_kb / 1048576))GB free at $WORKSPACE; a complete ${APOSTATE_TARGET:-build} measures ${need_gb}GB of checkout and output. Free space or use a larger runner."
# Comfort, not arithmetic: the old 200GB warning was roughly three times actual
# consumption, so it fired on the one runner with the least headroom and nowhere
# else, which is how a warning gets ignored.
[ "$avail_kb" -gt 104857600 ] || warn "under 100GB free; a complete ${APOSTATE_TARGET:-build} needs ${need_gb}GB"

# Resolution, not existence. fetch-sources.sh checks that $DEPOT_TOOLS/gclient
# is executable and then runs `gclient` bare, and on Windows the file passed
# that check while PATH held a drive-letter entry split in two, so the sync
# died 25 minutes later with "command not found". This is the step that owns
# "depot_tools is usable", so it is the step that has to prove it.
resolved="$(command -v gclient || true)"
[ -n "$resolved" ] || die "gclient is not on PATH; lib.sh put $DEPOT_TOOLS there and the shell cannot see it"
resolved_dir="$(cd "$(dirname "$resolved")" && pwd -P)"
expected_dir="$(cd "$DEPOT_TOOLS" && pwd -P)"
[ "$resolved_dir" = "$expected_dir" ] ||
  die "gclient resolves to $resolved, not to the pinned depot_tools at $DEPOT_TOOLS"

say "bootstrap ok  chromium=$CHROMIUM_VERSION  workspace=$WORKSPACE"
