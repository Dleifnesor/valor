"""valor-web: run the web API or the job worker, and break-glass account commands (root in the VALOR VM).

    valor-web serve [--uds /run/valor/web.sock | --port 8080]
    valor-web worker
    valor-web create-admin --username NAME --password-file FILE
    valor-web create-user --username NAME --role admin|operator|viewer --password-file FILE
    valor-web list-users | delete-user USER
    valor-web reset-mfa USER | unlock USER | reset-password USER --password-file FILE
    valor-web migrate
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import db
from .config import load_web_config
from .security import hash_password, password_problem


def _password(path: str) -> str:
    p = Path(path)
    pw = p.read_text().strip()
    return pw


def _user(conn, username: str):
    u = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if u is None:
        raise SystemExit(f"no user {username}")
    return u


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="valor-web", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--uds")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--static", help="serve the built UI from this directory (development)")
    sub.add_parser("worker")
    sub.add_parser("migrate")
    c = sub.add_parser("create-admin")
    c.add_argument("--username", required=True)
    c.add_argument("--password-file", required=True)
    c.add_argument("--display-name", default="Administrator")
    cu = sub.add_parser("create-user")
    cu.add_argument("--username", required=True)
    cu.add_argument("--role", choices=["admin", "operator", "viewer"], required=True)
    cu.add_argument("--password-file", required=True)
    cu.add_argument("--display-name", default="")
    sub.add_parser("list-users")
    for name in ("reset-mfa", "unlock", "delete-user"):
        x = sub.add_parser(name)
        x.add_argument("username")
    r = sub.add_parser("reset-password")
    r.add_argument("username")
    r.add_argument("--password-file", required=True)
    args = ap.parse_args(argv)
    wcfg = load_web_config()

    if args.cmd == "serve":
        import uvicorn
        from .app import create_app
        app = create_app(static_dir=args.static)
        kw = {"uds": args.uds} if args.uds else {"host": args.host, "port": args.port}
        uvicorn.run(app, proxy_headers=False, server_header=False, date_header=False, log_level="info",
                    access_log=False, **kw)
        return
    if args.cmd == "worker":
        from .worker import run_forever
        run_forever()
        return

    print(f"schema version {db.migrate(wcfg.db)}")
    if args.cmd == "migrate":
        return
    conn = db.connect(wcfg.db)
    now = time.time()
    if args.cmd in ("create-admin", "create-user"):
        role = "admin" if args.cmd == "create-admin" else args.role
        pw = _password(args.password_file)
        problem = password_problem(pw, args.username)
        if problem:
            raise SystemExit(problem)
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (args.username,)).fetchone():
            print(f"user {args.username} already exists - unchanged")
            return
        conn.execute("INSERT INTO users(username, display_name, role, source, password_hash, created_at, updated_at) "
                     "VALUES (?, ?, ?, 'local', ?, ?, ?)", (args.username, args.display_name, role, hash_password(pw), now, now))
        db.audit(conn, "user.create", "ok", username="root (console)", target=args.username, detail={"role": role})
        print(f"created {role} {args.username} (MFA enrollment at first sign-in)")
    elif args.cmd == "list-users":
        for u in conn.execute("SELECT username, role, source, disabled, totp_secret IS NOT NULL AS mfa, locked_until "
                              "FROM users ORDER BY username"):
            flags = [x for x, on in (("disabled", u["disabled"]), ("locked", u["locked_until"] > now),
                                     ("no-mfa", not u["mfa"])) if on]
            print(f"{u['username']:24} {u['role']:9} {u['source']:6} {' '.join(flags)}")
    elif args.cmd == "delete-user":
        u = _user(conn, args.username)
        if u["role"] == "admin" and not conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0 "
                                                     "AND id != ?", (u["id"],)).fetchone()[0]:
            raise SystemExit("refusing to delete the last admin")
        conn.execute("DELETE FROM users WHERE id = ?", (u["id"],))
        db.audit(conn, "user.delete", "ok", username="root (console)", target=args.username)
        print(f"deleted {args.username}")
    elif args.cmd == "reset-mfa":
        u = _user(conn, args.username)
        with db.transaction(conn):
            conn.execute("UPDATE users SET totp_secret = NULL, totp_last_step = 0 WHERE id = ?", (u["id"],))
            conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (u["id"],))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (u["id"],))
        db.audit(conn, "user.reset_mfa", "ok", username="root (console)", target=args.username)
        print(f"MFA reset for {args.username}: they enroll again at the next sign-in")
    elif args.cmd == "unlock":
        u = _user(conn, args.username)
        conn.execute("UPDATE users SET failed_logins = 0, locked_until = 0, disabled = 0 WHERE id = ?", (u["id"],))
        db.audit(conn, "user.unlock", "ok", username="root (console)", target=args.username)
        print(f"{args.username} unlocked and enabled")
    elif args.cmd == "reset-password":
        u = _user(conn, args.username)
        pw = _password(args.password_file)
        problem = password_problem(pw, args.username)
        if problem:
            raise SystemExit(problem)
        with db.transaction(conn):
            conn.execute("UPDATE users SET password_hash = ?, must_change_password = 1, failed_logins = 0, "
                         "locked_until = 0 WHERE id = ?", (hash_password(pw), u["id"]))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (u["id"],))
        db.audit(conn, "user.reset_password", "ok", username="root (console)", target=args.username)
        print(f"password reset for {args.username} (they must change it at the next sign-in)")
    conn.close()


if __name__ == "__main__":
    sys.exit(main())
