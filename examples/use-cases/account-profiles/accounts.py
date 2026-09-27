"""Keep one browser profile, and so one machine, per account you operate.

accounts.json maps each account to its user data directory, persona, locale,
timezone and the environment variable that holds its proxy. Commands:

    list            each account, its proxy variable and the seed its profile holds
    open NAME       launch the account's profile, open a page and compare the machine with the last launch
    check           refuse a registry where two accounts share a directory or a seed
    backup NAME     zip the account's profile into backups/
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from apostate import launch_persistent_context

HERE = Path(__file__).resolve().parent
REGISTRY = HERE / "accounts.json"
BACKUPS = HERE / "backups"

READ = """() => {
    const gl = document.createElement("canvas").getContext("webgl");
    const info = gl.getExtension("WEBGL_debug_renderer_info");
    return {
        userAgent: navigator.userAgent,
        platform: navigator.platform,
        cores: navigator.hardwareConcurrency,
        memory: navigator.deviceMemory,
        screen: `${screen.width}x${screen.height}`,
        gpu: gl.getParameter(info.UNMASKED_RENDERER_WEBGL),
        languages: navigator.languages.join(","),
        timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    };
}"""


def load_registry():
    accounts = json.loads(REGISTRY.read_text())
    for account in accounts.values():
        account["user_data_dir"] = (HERE / account["user_data_dir"]).resolve()
    return accounts


def stored_seed(user_data_dir):
    identity = user_data_dir / "apostate" / "identity"
    return identity.read_text().strip() if identity.exists() else None


def in_use(user_data_dir):
    # Chromium holds one of these while a browser runs on the directory.
    return any((user_data_dir / name).exists() or (user_data_dir / name).is_symlink()
               for name in ("SingletonLock", "lockfile"))


def list_accounts(accounts):
    for name, account in accounts.items():
        variable = account["proxy_env"]
        proxy = f"{variable} ({'set' if os.environ.get(variable) else 'not set'})" if variable else "direct"
        seed = stored_seed(account["user_data_dir"])
        print(f"{name:16} {account['persona']:8} {account['locale']:6} {account['timezone']:16} "
              f"{proxy:34} seed {seed[:12] + '...' if seed else 'none yet'}")


def open_account(name, account, url):
    directory = account["user_data_dir"]
    variable = account["proxy_env"]
    proxy = os.environ.get(variable) if variable else None
    if variable and not proxy:
        sys.exit(f"{variable} is not set. Set the proxy for {name}, or set proxy_env to null for a direct launch.")
    # Locale and timezone come from the registry, so the account presents the same region every launch.
    with launch_persistent_context(directory, fingerprint_platform=account["persona"],
                                   locale=account["locale"], timezone=account["timezone"],
                                   geoip=False, proxy=proxy) as context:
        page = context.new_page()
        page.goto(url)
        machine = page.evaluate(READ)

    record = directory / "machine.json"
    print(f"{name}  seed {stored_seed(directory)[:12]}...")
    for key, value in machine.items():
        print(f"    {key:10} {value}")
    if not record.exists():
        print("    first launch: machine recorded")
    else:
        previous = json.loads(record.read_text())
        changed = [key for key in machine if machine[key] != previous["machine"].get(key)]
        if changed:
            print(f"    changed since {previous['time']}: {', '.join(changed)}")
        else:
            print(f"    same machine as the launch at {previous['time']}")
    record.write_text(json.dumps({"time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "machine": machine}, indent=2))


def problems(accounts):
    """Two accounts on one directory, or two directories holding one seed."""
    found, dirs, seeds = [], {}, {}
    for name, account in accounts.items():
        directory = account["user_data_dir"]
        if directory in dirs:
            found.append(f"{name} and {dirs[directory]} share {os.path.relpath(directory, HERE)}")
        dirs[directory] = name
        seed = stored_seed(directory)
        if seed in seeds:
            found.append(f"{name} and {seeds[seed]} hold the same seed, so one profile is a copy")
        if seed:
            seeds[seed] = name
    return found


def backup(name, account):
    directory = account["user_data_dir"]
    if not directory.exists():
        sys.exit(f"{name} has no profile yet.")
    if in_use(directory):
        sys.exit(f"{name} is open in a browser. Close it before the backup.")
    BACKUPS.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = shutil.make_archive(str(BACKUPS / f"{name}-{stamp}"), "zip", directory)
    print(f"{name}  {Path(archive).relative_to(HERE)}  {Path(archive).stat().st_size} bytes")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    opener = commands.add_parser("open")
    opener.add_argument("name")
    opener.add_argument("--url", default="https://example.com")
    commands.add_parser("check")
    saver = commands.add_parser("backup")
    saver.add_argument("name")
    arguments = parser.parse_args()

    accounts = load_registry()
    if arguments.command == "list":
        list_accounts(accounts)
    elif arguments.command == "check":
        found = problems(accounts)
        for problem in found:
            print(f"problem: {problem}")
        print(f"accounts: {len(accounts)}, problems: {len(found)}")
        sys.exit(1 if found else 0)
    else:
        if arguments.name not in accounts:
            sys.exit(f"{arguments.name} is not in {REGISTRY.name}")
        if problems(accounts):
            sys.exit("Run the check command and fix accounts.json first.")
        account = accounts[arguments.name]
        if arguments.command == "open":
            open_account(arguments.name, account, arguments.url)
        else:
            backup(arguments.name, account)


if __name__ == "__main__":
    main()
