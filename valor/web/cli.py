"""valor-web: run the web API or the job worker, and break-glass account commands (root in the VALOR VM).

    valor-web serve [--uds /run/valor/web.sock | --port 8080]
    valor-web worker
    valor-web create-admin --username NAME --password-file FILE
    valor-web create-user --username NAME --role admin|operator|viewer --password-file FILE
    valor-web list-users | delete-user USER
    valor-web reset-mfa USER | unlock USER | reset-password USER --password-file FILE
    valor-web migrate

Passwords are read from a file (never a command line argument, which other users could see in `ps`).
Every account command is audited as "root (console)".
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import db
from .config import load_web_config
from .security import hash_password, password_problem

CONSOLE = "root (console)"              # who the audit log names for these commands


def _password(path: str) -> str:
    return Path(path).read_text().strip()


def _checked_password(path: str, username: str) -> str:
    password = _password(path)
    problem = password_problem(password, username)
    if problem:
        raise SystemExit(problem)
    return password


def _user(conn, username: str):
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if user is None:
        raise SystemExit(f"no user {username}")
    return user


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="valor-web", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="cmd", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--uds")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--static", help="serve the built UI from this directory (development)")
    commands.add_parser("worker")
    commands.add_parser("migrate")
    create_admin = commands.add_parser("create-admin")
    create_admin.add_argument("--username", required=True)
    create_admin.add_argument("--password-file", required=True)
    create_admin.add_argument("--display-name", default="Administrator")
    create_user = commands.add_parser("create-user")
    create_user.add_argument("--username", required=True)
    create_user.add_argument("--role", choices=["admin", "operator", "viewer"], required=True)
    create_user.add_argument("--password-file", required=True)
    create_user.add_argument("--display-name", default="")
    commands.add_parser("list-users")
    for name in ("reset-mfa", "unlock", "delete-user"):
        command = commands.add_parser(name)
        command.add_argument("username")
    reset_password = commands.add_parser("reset-password")
    reset_password.add_argument("username")
    reset_password.add_argument("--password-file", required=True)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    wcfg = load_web_config()

    if args.cmd == "serve":
        _serve(args)
        return
    if args.cmd == "worker":
        from .worker import run_forever
        run_forever()
        return

    # Every account command brings the database schema up to date first.
    print(f"schema version {db.migrate(wcfg.db)}")
    if args.cmd == "migrate":
        return
    conn = db.connect(wcfg.db)
    commands = {"create-admin": _create_user, "create-user": _create_user, "list-users": _list_users,
                "delete-user": _delete_user, "reset-mfa": _reset_mfa, "unlock": _unlock,
                "reset-password": _reset_password}
    commands[args.cmd](conn, args)
    conn.close()


def _serve(args) -> None:
    import uvicorn
    from .app import create_app
    app = create_app(static_dir=args.static)
    where = {"uds": args.uds} if args.uds else {"host": args.host, "port": args.port}
    uvicorn.run(app, proxy_headers=False, server_header=False, date_header=False, log_level="info",
                access_log=False, **where)


# ---- Account commands ----

def _create_user(conn, args) -> None:
    role = "admin" if args.cmd == "create-admin" else args.role
    password = _checked_password(args.password_file, args.username)
    if conn.execute("SELECT 1 FROM users WHERE username = ?", (args.username,)).fetchone():
        print(f"user {args.username} already exists - unchanged")
        return
    now = time.time()
    conn.execute("INSERT INTO users(username, display_name, role, source, password_hash, created_at, updated_at) "
                 "VALUES (?, ?, ?, 'local', ?, ?, ?)",
                 (args.username, args.display_name, role, hash_password(password), now, now))
    db.audit(conn, "user.create", "ok", username=CONSOLE, target=args.username, detail={"role": role})
    print(f"created {role} {args.username} (MFA enrollment at first sign-in)")


def _list_users(conn, args) -> None:
    now = time.time()
    for user in conn.execute("SELECT username, role, source, disabled, totp_secret IS NOT NULL AS mfa, locked_until "
                             "FROM users ORDER BY username"):
        flags = [flag for flag, on in (("disabled", user["disabled"]), ("locked", user["locked_until"] > now),
                                       ("no-mfa", not user["mfa"])) if on]
        print(f"{user['username']:24} {user['role']:9} {user['source']:6} {' '.join(flags)}")


def _delete_user(conn, args) -> None:
    user = _user(conn, args.username)
    other_admins = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0 AND id != ?",
                                (user["id"],)).fetchone()[0]
    if user["role"] == "admin" and not other_admins:
        raise SystemExit("refusing to delete the last admin")
    conn.execute("DELETE FROM users WHERE id = ?", (user["id"],))
    db.audit(conn, "user.delete", "ok", username=CONSOLE, target=args.username)
    print(f"deleted {args.username}")


def _reset_mfa(conn, args) -> None:
    user = _user(conn, args.username)
    with db.transaction(conn):
        conn.execute("UPDATE users SET totp_secret = NULL, totp_last_step = 0 WHERE id = ?", (user["id"],))
        conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user["id"],))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
    db.audit(conn, "user.reset_mfa", "ok", username=CONSOLE, target=args.username)
    print(f"MFA reset for {args.username}: they enroll again at the next sign-in")


def _unlock(conn, args) -> None:
    user = _user(conn, args.username)
    conn.execute("UPDATE users SET failed_logins = 0, locked_until = 0, disabled = 0 WHERE id = ?", (user["id"],))
    db.audit(conn, "user.unlock", "ok", username=CONSOLE, target=args.username)
    print(f"{args.username} unlocked and enabled")


def _reset_password(conn, args) -> None:
    user = _user(conn, args.username)
    password = _checked_password(args.password_file, args.username)
    with db.transaction(conn):
        conn.execute("UPDATE users SET password_hash = ?, must_change_password = 1, failed_logins = 0, "
                     "locked_until = 0 WHERE id = ?", (hash_password(password), user["id"]))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
    db.audit(conn, "user.reset_password", "ok", username=CONSOLE, target=args.username)
    print(f"password reset for {args.username} (they must change it at the next sign-in)")


if __name__ == "__main__":
    sys.exit(main())
