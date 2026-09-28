"""Verwaltungsbefehle für Betreiber.

    python -m app.cli create-platform-admin --email ops@example.org --name "Betrieb"
    python -m app.cli create-demo --email demo@example.org     # Demo-Mandant mit Station + Geräte-Token
    python -m app.cli purge                                    # Aufbewahrungsfristen anwenden

Passwörter werden interaktiv abgefragt (oder über BIKE_CLI_PASSWORD für Skripte).
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import time

from .config import load_settings
from .core import Core
from .security import hash_password, hash_token, new_id, new_token, password_problems
from .service import Monitoring


def _password(core: Core, email: str, name: str) -> str:
    pw = os.environ.get("BIKE_CLI_PASSWORD") or getpass.getpass("Passwort: ")
    problems = password_problems(pw, core.s.password_min_length, email, name)
    if problems:
        sys.exit(f"Passwort abgelehnt: {', '.join(problems)} (mindestens {core.s.password_min_length} Zeichen)")
    return hash_password(pw, core.s.scrypt_n)


def create_platform_admin(core: Core, email: str, name: str) -> None:
    email = email.strip().lower()
    if core.db.one("SELECT 1 FROM user WHERE email = ?", (email,)):
        sys.exit("E-Mail bereits vergeben")
    now = time.time()
    core.db.execute(
        "INSERT INTO user (id, tenant_id, email, name, role, password_hash, email_verified_at, is_platform_admin, created_at, password_changed_at) "
        "VALUES (?, NULL, ?, ?, 'platform', ?, ?, 1, ?, ?)",
        (new_id("usr"), email, name, _password(core, email, name), now, now, now))
    core.audit("platform_admin_created", actor="cli", target=email)
    print(f"Plattform-Admin {email} angelegt. Beim ersten Login ist 2FA einzurichten.")


def create_demo(core: Core, email: str, org: str, plan: str) -> None:
    email = email.strip().lower()
    if core.db.one("SELECT 1 FROM user WHERE email = ?", (email,)):
        sys.exit("E-Mail bereits vergeben")
    now = time.time()
    tid, sid = new_id("org"), new_id("st")
    token = new_token("bsd_")
    display = new_token("bsp_")
    with core.db.tx() as c:
        c.execute("INSERT INTO tenant (id, name, plan, status, created_at) VALUES (?,?,?,?,?)", (tid, org, plan, "active", now))
        c.execute(
            "INSERT INTO user (id, tenant_id, email, name, role, password_hash, email_verified_at, created_at, password_changed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (new_id("usr"), tid, email, "Demo Owner", "owner", _password(core, email, "Demo Owner"), now, now, now))
        c.execute("INSERT INTO station (id, tenant_id, name, location, display_token_hash, display_enabled, created_at) VALUES (?,?,?,?,?,1,?)",
                  (sid, tid, "Radstation Schulhof", "Haupteingang", hash_token(display), now))
        for i, k in enumerate("ABC", start=1):
            c.execute("INSERT INTO slot (id, station_id, key, label, position) VALUES (?,?,?,?,?)", (new_id("sl"), sid, k, f"Platz {k}", i))
        c.execute("INSERT INTO device (id, tenant_id, station_id, name, token_prefix, token_hash, created_at, created_by) VALUES (?,?,?,?,?,?,?,?)",
                  (new_id("dev"), tid, sid, "Pi-Gateway", token[:10], hash_token(token), now, "cli"))
    core.audit("demo_created", tenant_id=tid, actor="cli", target=email)
    print(f"STATION_ID={sid}\nDEVICE_TOKEN={token}\nDISPLAY_URL={core.s.base_url}/display#{display}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-platform-admin")
    a.add_argument("--email", required=True)
    a.add_argument("--name", default="Plattform-Admin")
    d = sub.add_parser("create-demo")
    d.add_argument("--email", required=True)
    d.add_argument("--org", default="Demo-Schule")
    d.add_argument("--plan", default="school")
    sub.add_parser("purge")
    args = ap.parse_args(argv)

    core = Core(load_settings())
    if args.cmd == "create-platform-admin":
        create_platform_admin(core, args.email, args.name)
    elif args.cmd == "create-demo":
        create_demo(core, args.email, args.org, args.plan)
    elif args.cmd == "purge":
        print(Monitoring(core).purge())


if __name__ == "__main__":
    main()
