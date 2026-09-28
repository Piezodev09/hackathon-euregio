"""Administration commands for the operator.

    python -m app.cli create-platform-admin --email ops@example.org --name "Operations"
    python -m app.cli demo --reset                             # demo organisation, 2 stations, 7 days of history
    python -m app.cli enrollment-code --station st_...         # pairing code for scripts
    python -m app.cli purge                                    # apply retention periods

Passwords are prompted interactively (or taken from BIKE_CLI_PASSWORD in scripts).
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import time

from .config import load_settings
from .core import Core
from .security import hash_password, hash_token, new_id, password_problems
from .service import Monitoring


def _password(core: Core, email: str, name: str) -> str:
    pw = os.environ.get("BIKE_CLI_PASSWORD") or getpass.getpass("Password: ")
    problems = password_problems(pw, core.s.password_min_length, email, name)
    if problems:
        sys.exit(f"Password rejected: {', '.join(problems)} (at least {core.s.password_min_length} characters)")
    return hash_password(pw, core.s.scrypt_n)


def create_platform_admin(core: Core, email: str, name: str) -> None:
    email = email.strip().lower()
    if core.db.one("SELECT 1 FROM user WHERE email = ?", (email,)):
        sys.exit("E-mail address already in use")
    now = time.time()
    core.db.execute(
        "INSERT INTO user (id, tenant_id, email, name, role, password_hash, email_verified_at, is_platform_admin, created_at, password_changed_at) "
        "VALUES (?, NULL, ?, ?, 'platform', ?, ?, 1, ?, ?)",
        (new_id("usr"), email, name, _password(core, email, name), now, now, now))
    core.audit("platform_admin_created", actor="cli", target=email)
    core.finish_setup()  # the browser setup link is no longer needed
    print(f"Platform admin {email} created. Two-factor sign-in must be set up at the first login.")


def demo(core: Core, email: str, reset: bool, days: int, live: bool) -> None:
    from .demo import create_demo

    pw = os.environ.get("BIKE_CLI_PASSWORD") or None
    if pw:
        problems = password_problems(pw, core.s.password_min_length, email, "Demo Owner")
        if problems:
            sys.exit(f"Password rejected: {', '.join(problems)}")
    try:
        r = create_demo(core, email=email, password=pw, days=days, reset=reset, live=live)
    except ValueError as exc:
        sys.exit(str(exc))
    # KEY=value lines so scripts can "source" the output; comments explain the rest.
    print(f"# Demo organisation with 2 stations, {r.measurements} simulated measurements, {r.events} events")
    print(f"TENANT_ID={r.tenant_id}\nSTATION_ID={r.station_id}\nSTATION2_ID={r.second_station_id}")
    print(f"DEMO_EMAIL={r.email}\nDISPLAY_URL={r.display_url}")
    if r.password:
        print(f"DEMO_PASSWORD={r.password}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-platform-admin")
    a.add_argument("--email", required=True)
    a.add_argument("--name", default="Platform admin")
    d = sub.add_parser("demo", help="demo organisation with 2 stations and 7 days of simulated history")
    d.add_argument("--email", default="demo@example.org")
    d.add_argument("--reset", action="store_true", help="delete an existing demo organisation first")
    d.add_argument("--days", type=int, default=7, choices=range(1, 29), metavar="1..28")
    d.add_argument("--no-live", action="store_true",
                   help="do not let the platform simulate live readings (e.g. when a simulated agent is paired)")
    sub.add_parser("demo-remove", help="delete the demo organisation")
    en = sub.add_parser("enrollment-code", help="create a pairing code for a station (e.g. for scripts)")
    en.add_argument("--station", required=True)
    sub.add_parser("purge")
    args = ap.parse_args(argv)

    core = Core(load_settings())
    if args.cmd == "create-platform-admin":
        create_platform_admin(core, args.email, args.name)
    elif args.cmd == "demo":
        demo(core, args.email, args.reset, args.days, not args.no_live)
    elif args.cmd == "demo-remove":
        from .demo import remove_demo

        print("Demo removed." if remove_demo(core) else "No demo organisation found.")
    elif args.cmd == "enrollment-code":
        from .routes.agent import CODE_LIFETIME_S, new_code

        st = core.db.one("SELECT * FROM station WHERE id = ?", (args.station,))
        if st is None:
            sys.exit("Station not found")
        code = new_code()
        now = time.time()
        core.db.execute("INSERT INTO enrollment (id, tenant_id, station_id, code_hash, name, created_by, created_at, expires_at) "
                        "VALUES (?,?,?,?,?,?,?,?)", (new_id("enr"), st["tenant_id"], st["id"], hash_token(code), "Pi-Gateway", "cli",
                                                    now, now + CODE_LIFETIME_S))
        print(code)
    elif args.cmd == "purge":
        print(Monitoring(core).purge())


if __name__ == "__main__":
    main()
