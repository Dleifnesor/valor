# *types on a tiny terminal with paws* OwO valor/web/cli.py is the `valor-web` command! ^w^
# It starts the two services (the web API with `serve`, the job runner with `worker`), and gives root on the
# VALOR VM "break-glass" account commands: create an admin when nobody can sign in, reset someone's MFA, unlock an
# account, and so on. The usage is in the docstring below (argparse also prints it as --help) *wags tail*
# Passwords come from a FILE, never from the command line, because other users could see arguments in `ps` >w<
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

# Lazy type hints UwU
from __future__ import annotations

# argparse parses the command line; sys for the exit code; time stamps accounts; Path reads password files OwO
import argparse
import sys
import time
from pathlib import Path

# db = the database; load_web_config = the [web] settings; hash_password/password_problem for passwords ^w^
from . import db
from .config import load_web_config
from .security import hash_password, password_problem

# The name these commands write into the audit log UwU
CONSOLE = "root (console)"              # who the audit log names for these commands


# _password: read a password file, without a trailing newline >w<
def _password(path: str) -> str:
    return Path(path).read_text().strip()


# _checked_password: read it AND make sure it's strong enough; otherwise exit with the reason OwO
def _checked_password(path: str, username: str) -> str:
    password = _password(path)
    problem = password_problem(password, username)
    if problem:
        raise SystemExit(problem)
    return password


# _user: find a user by name, or exit with "no user X" ^w^
def _user(conn, username: str):
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if user is None:
        raise SystemExit(f"no user {username}")
    return user


# _parser: describe every subcommand and its options. RawDescriptionHelpFormatter keeps the docstring's line breaks
# in --help UwU
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="valor-web", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    # `dest="cmd"` stores which subcommand was given; required=True means one must be given >w<
    commands = parser.add_subparsers(dest="cmd", required=True)
    # serve: on a unix socket (--uds, what the VALOR VM uses behind nginx) or a TCP host/port; --static serves the
    # built UI too (development only) OwO
    serve = commands.add_parser("serve")
    serve.add_argument("--uds")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--static", help="serve the built UI from this directory (development)")
    # worker and migrate take no options ^w^
    commands.add_parser("worker")
    commands.add_parser("migrate")
    # create-admin / create-user: a username, a password file, a display name, and (for create-user) the role UwU
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
    # reset-mfa, unlock and delete-user each take just a username >w<
    for name in ("reset-mfa", "unlock", "delete-user"):
        command = commands.add_parser(name)
        command.add_argument("username")
    # reset-password: a username and a password file OwO
    reset_password = commands.add_parser("reset-password")
    reset_password.add_argument("username")
    reset_password.add_argument("--password-file", required=True)
    return parser


# main: parse the arguments and run the right command ^w^
def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    wcfg = load_web_config()

    # serve and worker run until stopped, so they return right after UwU
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
    # Open the database, find the command's function in a table, run it, close the database >w<
    conn = db.connect(wcfg.db)
    commands = {"create-admin": _create_user, "create-user": _create_user, "list-users": _list_users,
                "delete-user": _delete_user, "reset-mfa": _reset_mfa, "unlock": _unlock,
                "reset-password": _reset_password}
    commands[args.cmd](conn, args)
    conn.close()


# _serve: start the web API with uvicorn OwO
def _serve(args) -> None:
    # Imported here so the account commands don't need uvicorn/FastAPI loaded ^w^
    import uvicorn
    from .app import create_app
    app = create_app(static_dir=args.static)
    # Listen where asked: a unix socket, or host + port UwU
    where = {"uds": args.uds} if args.uds else {"host": args.host, "port": args.port}
    # proxy_headers=False (nginx's X-Real-IP is read by our own code instead); no Server/Date headers (less to
    # fingerprint); no access log (nginx keeps one) >w<
    uvicorn.run(app, proxy_headers=False, server_header=False, date_header=False, log_level="info",
                access_log=False, **where)


# ---- Account commands ----

# _create_user: create-admin always makes an admin; create-user uses --role OwO
def _create_user(conn, args) -> None:
    role = "admin" if args.cmd == "create-admin" else args.role
    password = _checked_password(args.password_file, args.username)
    # An existing user is left exactly as it is (safe to run twice) ^w^
    if conn.execute("SELECT 1 FROM users WHERE username = ?", (args.username,)).fetchone():
        print(f"user {args.username} already exists - unchanged")
        return
    # Insert a local account (MFA is set up at its first sign-in), audit as "root (console)", and say so UwU
    now = time.time()
    conn.execute("INSERT INTO users(username, display_name, role, source, password_hash, created_at, updated_at) "
                 "VALUES (?, ?, ?, 'local', ?, ?, ?)",
                 (args.username, args.display_name, role, hash_password(password), now, now))
    db.audit(conn, "user.create", "ok", username=CONSOLE, target=args.username, detail={"role": role})
    print(f"created {role} {args.username} (MFA enrollment at first sign-in)")


# _list_users: one line per account: name, role, source and flags (disabled / locked / no-mfa), in columns >w<
def _list_users(conn, args) -> None:
    now = time.time()
    for user in conn.execute("SELECT username, role, source, disabled, totp_secret IS NOT NULL AS mfa, locked_until "
                             "FROM users ORDER BY username"):
        flags = [flag for flag, on in (("disabled", user["disabled"]), ("locked", user["locked_until"] > now),
                                       ("no-mfa", not user["mfa"])) if on]
        print(f"{user['username']:24} {user['role']:9} {user['source']:6} {' '.join(flags)}")


# _delete_user: delete an account, but never the last active admin OwO
def _delete_user(conn, args) -> None:
    user = _user(conn, args.username)
    other_admins = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0 AND id != ?",
                                (user["id"],)).fetchone()[0]
    if user["role"] == "admin" and not other_admins:
        raise SystemExit("refusing to delete the last admin")
    conn.execute("DELETE FROM users WHERE id = ?", (user["id"],))
    db.audit(conn, "user.delete", "ok", username=CONSOLE, target=args.username)
    print(f"deleted {args.username}")


# _reset_mfa: remove the authenticator, recovery codes and sessions in one transaction ^w^
def _reset_mfa(conn, args) -> None:
    user = _user(conn, args.username)
    with db.transaction(conn):
        conn.execute("UPDATE users SET totp_secret = NULL, totp_last_step = 0 WHERE id = ?", (user["id"],))
        conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user["id"],))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
    db.audit(conn, "user.reset_mfa", "ok", username=CONSOLE, target=args.username)
    print(f"MFA reset for {args.username}: they enroll again at the next sign-in")


# _unlock: clear failed sign-ins and the lockout, and also re-enable the account (break-glass!) UwU
def _unlock(conn, args) -> None:
    user = _user(conn, args.username)
    conn.execute("UPDATE users SET failed_logins = 0, locked_until = 0, disabled = 0 WHERE id = ?", (user["id"],))
    db.audit(conn, "user.unlock", "ok", username=CONSOLE, target=args.username)
    print(f"{args.username} unlocked and enabled")


# _reset_password: set a new password (from a file), force a change at next sign-in, clear lockouts, end sessions >w<
def _reset_password(conn, args) -> None:
    user = _user(conn, args.username)
    password = _checked_password(args.password_file, args.username)
    with db.transaction(conn):
        conn.execute("UPDATE users SET password_hash = ?, must_change_password = 1, failed_logins = 0, "
                     "locked_until = 0 WHERE id = ?", (hash_password(password), user["id"]))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
    db.audit(conn, "user.reset_password", "ok", username=CONSOLE, target=args.username)
    print(f"password reset for {args.username} (they must change it at the next sign-in)")


# Run main() when started as a script; its return value (None = 0) becomes the exit code OwO *salutes*
if __name__ == "__main__":
    sys.exit(main())
