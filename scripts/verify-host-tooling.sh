#!/usr/bin/env bash
# Check that this host carries everything the build for <target> will reach for.
#
# Every tool here is used by a script that runs deep into a build measured in
# hours -- package-artifact.sh is the very last step of all -- so a missing one
# otherwise surfaces after the expensive work is already paid for. This runs in
# seconds instead.
#
# It reports EVERY gap rather than exiting at the first, and enumerates what it
# did find when something is missing. One cheap run on the smallest runner of a
# family therefore answers the whole question for that image; exiting early
# meant learning about one missing file per run.
#
# Every Windows failure message names the Visual Studio component id or Windows
# SDK feature that supplies the missing file, because the cost of not naming it
# is measured: four Windows runs at ~$15 and ~25 minutes each, learning one
# prerequisite per run. The last of them compiled 33,797 edges before failing on
# "'atldef.h' file not found", which is a Visual Studio component -- ATL -- and
# not anything the compiler or the SDK could have told us about.
#
# Usage: scripts/verify-host-tooling.sh <target>
set -uo pipefail

# lib.sh, not a local copy of its logic. It resolves vs2022_install through
# vswhere with the version range pinned, and that variable is what
# build/vs_toolchain.py reads and therefore what gn will use. A second
# resolution here could approve one install while the build used another, and
# then every path this script checks would be checked in the wrong tree -- a
# green report about a toolchain nothing builds with.
source "$(dirname "$0")/lib.sh"
# lib.sh sets -e. This script's whole point is to report every gap in one run,
# so drop it and keep -u and pipefail.
set +e

target="${1:-${APOSTATE_TARGET:-}}"
[ -n "$target" ] || { echo "usage: scripts/verify-host-tooling.sh <target>" >&2; exit 2; }

problems=()
note() { printf '  %s\n' "$*"; }
fail() { problems+=("$1"); }

have() { command -v "$1" >/dev/null 2>&1; }
need() {
  if have "$1"; then
    note "$1 present"
  else
    fail "$1 is missing; $2 needs it"
  fi
}

# A path that must exist, with the component that supplies it named in the
# failure. Reports what it found either way: presence lines on a cheap probe
# runner are how the next failure gets diagnosed without paying for a gate run.
needs_path() {
  local path="$1" owner="$2" why="$3"
  if [ -e "$path" ]; then
    note "present  $path"
  else
    fail "missing $path
      install: $owner
      needed by: $why"
  fi
}

printf 'host tooling for %s (%s %s)\n' "$target" "$(uname -s)" "$(uname -m)"

need python3 scripts/build.sh
need git scripts/fetch-sources.sh

# The bash on PATH is the one every `#!/usr/bin/env bash` script here runs
# under. scripts/checkfile.sh and scripts/verify-release-inputs.sh use mapfile
# and declare -A, which Bash 3.2 -- macOS's /bin/bash -- does not have.
if path_bash="$(command -v bash)" &&
   path_bash_version="$("$path_bash" -c 'printf "%s %s" "${BASH_VERSINFO[0]}" "$BASH_VERSION"' 2>/dev/null)"; then
  if [ "${path_bash_version%% *}" -ge 4 ] 2>/dev/null; then
    note "bash ${path_bash_version#* } at $path_bash"
  else
    note "bash at $path_bash is ${path_bash_version#* }; the build runs, but scripts/checkfile.sh and scripts/verify-release-inputs.sh need Bash 4 or later
      install: a newer bash, first on PATH (on macOS, Homebrew's bash: brew install bash)
      needed by: mapfile and declare -A in those scripts"
  fi
else
  fail "no runnable bash on PATH; every script here starts with #!/usr/bin/env bash"
fi

case "$target" in
  windows-*)
    # windows-x64 packages a .zip through 7z; the others write .tar.zst.
    # Probing the wrong format would fail a runner that is actually fine.
    need 7z scripts/package-artifact.sh

    vc_toolset='Microsoft.VisualStudio.Component.VC.Tools.x86.x64'

    # ------------------------------------------------------------------
    # Visual Studio
    # ------------------------------------------------------------------
    if [ -z "${vs2022_install:-}" ]; then
      fail "no Visual Studio 2022 install could be resolved through vswhere
      install: $vc_toolset (in any 2022 edition, including BuildTools)
      needed by: build/vs_toolchain.py DetectVisualStudioPath; gn cannot generate without it"
      # Deliberately -all and unfiltered: the point is to show what IS there
      # when the pinned-range query found nothing, so a 2019-only or 2026-only
      # image reads as a version mismatch rather than as no toolchain at all.
      if vswhere="$(windows_vswhere)"; then
        note "vswhere reports these installs, unfiltered:"
        "$vswhere" -all -products '*' -property installationPath 2>/dev/null |
          tr -d '\r' | while IFS= read -r line; do note "    ${line:-<none>}"; done
      else
        note "vswhere.exe is not installed either"
      fi
    else
      note "visual studio: $vs2022_install"
      vs_unix="$(cygpath -u "$vs2022_install" 2>/dev/null || printf '%s' "$vs2022_install")"

      # Every toolset, not only the selected one. A second one appearing changes
      # which directory the build resolves, and this listing is how that is seen.
      note "MSVC toolsets:"
      for entry in "$vs_unix"/VC/Tools/MSVC/*; do
        [ -d "$entry" ] && note "    $(basename "$entry")"
      done

      toolset="$(windows_msvc_toolset_root)"
      if [ -z "$toolset" ]; then
        fail "no VC/Tools/MSVC/14.* toolset under $vs_unix
      install: $vc_toolset
      needed by: every Windows compile; build/vs_toolchain.py FindVCComponentRoot"
      else
        note "selected toolset: $(basename "$toolset")"

        # The pinned component list. One row per proof file, each naming the
        # component that ships it. This is the check the ATL failure needed.
        while IFS=' ' read -r component probe; do
          [ -n "$component" ] || continue
          needs_path "$toolset/$probe" "$component" \
            "build/WINDOWS_VS_COMPONENTS names this file as proof of the component"
        done < <(windows_vs_component_probes)

        # Diagnose a wrong expectation rather than only reporting it: if an
        # atlmfc probe failed, the tree that should hold it is the evidence.
        if [ ! -d "$toolset/atlmfc" ]; then
          note "no atlmfc directory at all under $(basename "$toolset"); ATL is not installed"
        else
          note "atlmfc subdirectories:"
          for entry in "$toolset"/atlmfc/*; do
            [ -e "$entry" ] && note "    $(basename "$entry")"
          done
        fi
      fi

      # build/toolchain/win/setup_toolchain.py runs vcvarsall.bat to produce
      # INCLUDE, LIB and PATH, and raises "<path> is missing - make sure VC++
      # tools are installed" when neither location has it. This is also the
      # mechanism behind the ATL failure: vcvarsall adds atlmfc\include to
      # INCLUDE only when that directory exists, so a missing ATL component
      # does not error, it silently produces an INCLUDE without ATL in it.
      if [ -f "$vs_unix/VC/vcvarsall.bat" ] ||
         [ -f "$vs_unix/VC/Auxiliary/Build/vcvarsall.bat" ]; then
        note "present  vcvarsall.bat"
      else
        fail "missing $vs_unix/VC/Auxiliary/Build/vcvarsall.bat
      install: $vc_toolset
      needed by: build/toolchain/win/setup_toolchain.py, which runs it for x86 AND x64"
      fi

      # msdia140.dll comes from the VS install's DIA SDK. build/vs_toolchain.py
      # _CopyDebugger copies it with no existence check, so an absence arrives
      # as a bare Python FileNotFoundError during gn gen.
      needs_path "$vs_unix/DIA SDK/bin/amd64/msdia140.dll" \
        "the DIA SDK, installed with any Visual Studio C++ workload" \
        "build/vs_toolchain.py _CopyDebugger, unconditionally, at gn gen time"

      # Whether Chromium can actually FIND this install is asserted in
      # configure.sh, not here. This script runs before the checkout exists, so
      # build/vs_toolchain.py is not present to ask, and a guarded check that
      # finds no file would skip silently -- a check rendering as green because
      # it never ran.
    fi

    # ------------------------------------------------------------------
    # Windows SDK
    # ------------------------------------------------------------------
    sdk_root="$(windows_sdk_root)"
    # build/vs_toolchain.py hardcodes SDK_VERSION and prints it verbatim as gn's
    # sdk_version, so the directory name is not ours to choose. This file
    # mirrors it because the checkout does not exist yet; scripts/configure.sh
    # compares the two once it does, so the mirror cannot drift unnoticed.
    #
    # And the directory name is ALL it gives us: it is the major build, so it
    # is identical on every 26100 servicing revision. The revision assertions
    # below are a separate mechanism for that reason.
    sdk_version="$(windows_sdk_version 2>/dev/null || true)"
    if [ -z "$sdk_version" ]; then
      fail "build/WINDOWS_SDK_VERSION is missing or empty; cannot check the SDK trees"
    elif [ ! -d "$sdk_root" ]; then
      fail "no Windows SDK at $sdk_root
      install: Windows 11 SDK $sdk_version
      needed by: every Windows compile and link"
    else
      note "windows sdk: $sdk_root (expecting $sdk_version)"
      note "SDK version directories:"
      for dir in bin Include Lib; do
        note "    $dir: $(ls "$sdk_root/$dir" 2>/dev/null | tr '\n' ' ')"
      done

      # The include and library trees the failing compile command lines named.
      # build/toolchain/win/setup_toolchain.py _ExtractImportantEnvironment
      # checks every INCLUDE and LIB entry and raises 'Path "%s" ... does not
      # exist. Make sure the necessary SDK is installed.' for any that is gone.
      for part in ucrt um shared winrt cppwinrt; do
        case "$part" in
          winrt|cppwinrt)
            # Present in a complete SDK but not referenced by the x64 compile
            # command lines; reported, never fatal.
            if [ -d "$sdk_root/Include/$sdk_version/$part" ]; then
              note "present  Include/$sdk_version/$part (not required)"
            else
              note "absent   Include/$sdk_version/$part (not required)"
            fi
            ;;
          *)
            needs_path "$sdk_root/Include/$sdk_version/$part" \
              "Windows 11 SDK $sdk_version" \
              "clang-cl include path; setup_toolchain.py asserts every INCLUDE entry exists"
            ;;
        esac
      done
      for part in ucrt um; do
        needs_path "$sdk_root/Lib/$sdk_version/$part/x64" \
          "Windows 11 SDK $sdk_version" \
          "lld-link library path; setup_toolchain.py asserts every LIB entry exists"
      done

      # build/vs_toolchain.py marks dbghelp.dll NOT optional and raises
      # 'You must install Windows 10 SDK version <v> including the "Debugging
      # Tools for Windows" feature.'
      needs_path "$sdk_root/Debuggers/x64/dbghelp.dll" \
        "the Windows SDK feature \"Debugging Tools for Windows\" (OptionId.WindowsDesktopDebuggers)" \
        "build/vs_toolchain.py _CopyDebugger, which marks it non-optional, at gn gen time"

      # Optional by name in vs_toolchain.py's own table, and the CDB bundle only
      # matters if //build/win:copy_cdb_to_output enters the graph. Reported so
      # a partial Debuggers install is visible, never fatal, because failing on
      # these would fail a runner the build works on.
      for opt in dbgcore.dll symsrv.dll cdb.exe dbgeng.dll dbgmodel.dll \
                 winext/ext.dll winext/uext.dll winxp/exts.dll winxp/ntsdexts.dll; do
        if [ -e "$sdk_root/Debuggers/x64/$opt" ]; then
          note "present  Debuggers/x64/$opt (optional)"
        else
          note "absent   Debuggers/x64/$opt (optional)"
        fi
      done

      # ----------------------------------------------------------------
      # Revision, not presence
      # ----------------------------------------------------------------
      # Everything above asks "does this exist", and none of it can tell SDK
      # revision 4654 from 7705: both install into Include/10.0.26100.0, and
      # HKLM\...\Windows Kits\Installed Roots keys the same way, so the
      # revision is nowhere in a name. Measured cost of not checking it: the
      # windows-x64 gate reached 26,642 of 33,797 edges and failed with
      # "error: unknown type name 'IUIAutomationClientInfo'", with every
      # presence check above green.
      want_revision="$(tr -d '[:space:]' < "$REPO_ROOT/build/WINDOWS_SDK_INSTALLER_VERSION" 2>/dev/null)"
      if [ ! -f "$REPO_ROOT/build/WINDOWS_SDK_REQUIREMENTS" ]; then
        # Explicitly, rather than letting the read loop find nothing. A table
        # that silently reads as empty is a check that renders as green.
        fail "build/WINDOWS_SDK_REQUIREMENTS is missing; no SDK revision is being checked at all"
      else
        note "SDK revision requirements (pinned installer ${want_revision:-<unset>}):"
        while IFS=' ' read -r kind path expected; do
          [ -n "$kind" ] || continue
          case "$kind" in
            symbol)
              windows_sdk_has_symbol "$sdk_root/$path" "$expected"
              case "$?" in
                0) note "present  $expected, declared under $path" ;;
                2) fail "no directory $sdk_root/$path, so $expected cannot be checked
      install: Windows 11 SDK ${want_revision:-10.0.26100.7705}
      needed by: build/WINDOWS_SDK_REQUIREMENTS" ;;
                *) fail "$expected is not declared by any header under $sdk_root/$path
      install: Windows 11 SDK ${want_revision:-10.0.26100.7705}; this SDK is an OLDER servicing revision
      needed by: build/WINDOWS_SDK_REQUIREMENTS, which cites the consumer. The
      directories are all present -- the revision is what is wrong, and the
      directory names cannot show it" ;;
              esac
              ;;
            libsymbol)
              # The half a compile cannot see. A header declaring
              # EXTERN_C const CLSID X compiles against any revision; only the
              # link needs the library that defines X, and the series gate
              # never links.
              windows_sdk_lib_has_symbol "$sdk_root/$path" "$expected"
              case "$?" in
                0) note "present  $expected, defined in $path" ;;
                2) fail "no library at $sdk_root/$path, so $expected cannot be checked
      install: Windows 11 SDK ${want_revision:-10.0.26100.7705}
      needed by: build/WINDOWS_SDK_REQUIREMENTS" ;;
                *) fail "$expected is not defined by $sdk_root/$path
      install: Windows 11 SDK ${want_revision:-10.0.26100.7705}; this library is an OLDER servicing revision
      needed by: build/WINDOWS_SDK_REQUIREMENTS. The header that declares this
      symbol may well be current -- a compile would succeed and the LINK would
      fail, which the compile-only gate cannot catch" ;;
              esac
              ;;
            version)
              got_version="$(windows_file_version "$sdk_root/$path" || true)"
              if [ -z "$got_version" ]; then
                fail "cannot read a FileVersion from $sdk_root/$path
      install: Windows 11 SDK ${want_revision:-10.0.26100.7705}
      needed by: build/WINDOWS_SDK_REQUIREMENTS requires it to be >= $expected"
              elif version_at_least "$got_version" "$expected"; then
                note "present  $path is $got_version (>= $expected)"
              else
                fail "$sdk_root/$path is $got_version, older than the required $expected
      install: Windows 11 SDK ${want_revision:-10.0.26100.7705} including \"Debugging Tools for Windows\"
      needed by: docs/windows_build_instructions.md, for reading the large-page PDBs Chrome uses above 4 GiB"
              fi
              ;;
            *)
              fail "build/WINDOWS_SDK_REQUIREMENTS has an unknown requirement kind '$kind'; nothing checked that row"
              ;;
          esac
        done < <(windows_sdk_requirements)
      fi
    fi

    # ------------------------------------------------------------------
    # Machine-wide Visual C++ runtime
    # ------------------------------------------------------------------
    # With DEPOT_TOOLS_WIN_TOOLCHAIN=0, build/vs_toolchain.py takes the runtime
    # DLL source directories from %windir%\System32 and %windir%\SysWOW64, not
    # from the VS Redist tree, and build/toolchain/win/BUILD.gn runs copy_dlls
    # during gn gen. _CopyRuntimeImpl does not check its source, so a missing
    # one of these arrives as a bare FileNotFoundError out of gn. Release only:
    # is_official_build makes configuration_name "Release", so no debug runtime
    # and no ucrtbased.dll are reached.
    system32="${WINDIR:-/c/Windows}/System32"
    system32="$(cygpath -u "$system32" 2>/dev/null || printf '%s' "$system32")"
    for dll in msvcp140.dll msvcp140_atomic_wait.dll vccorlib140.dll \
               vcruntime140.dll vcruntime140_1.dll; do
      needs_path "$system32/$dll" \
        "the machine-wide Microsoft Visual C++ Redistributable (x64)" \
        "build/vs_toolchain.py copy_dlls, run by build/toolchain/win/BUILD.gn at gn gen time"
    done
    ;;
  *)
    need tar scripts/package-artifact.sh
    probe="$(mktemp -d)"
    trap 'rm -rf "$probe"' EXIT
    : > "$probe/member"
    if tar --zstd -cf "$probe/probe.tar.zst" -C "$probe" member 2>/dev/null; then
      note "tar can write .tar.zst"
    else
      msg="tar here cannot write a .tar.zst archive, which scripts/package-artifact.sh requires"
      have zstd && msg="$msg (a standalone zstd binary IS present, but tar cannot use it)"
      fail "$msg"
    fi
    ;;
esac

case "$target" in
  linux-*)
    need docker scripts/in-linux-build-container.sh
    if have docker && docker info >/dev/null 2>&1; then
      note "docker daemon reachable"
    elif have docker; then
      fail "the docker daemon is unreachable; scripts/in-linux-build-container.sh cannot run"
    fi
    ;;
  macos-*)
    for tool in xcodebuild xcrun plutil; do
      need "$tool" scripts/prepare-mac-sdk.sh
    done
    ;;
esac

if [ "${#problems[@]}" -gt 0 ]; then
  printf '\n%s problem(s) on this host:\n' "${#problems[@]}" >&2
  for problem in "${problems[@]}"; do printf '  - %s\n' "$problem" >&2; done
  exit 1
fi
printf 'host tooling complete for %s\n' "$target"
