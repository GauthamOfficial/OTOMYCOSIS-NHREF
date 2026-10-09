"""Administration commands for the screening service.

    python scripts/manage.py setup            guided first time setup
    python scripts/manage.py init-db          create any missing tables
    python scripts/manage.py create-user      add a user account by email address
    python scripts/manage.py reset-password   set a new temporary password
    python scripts/manage.py unlock           clear a sign in lockout
    python scripts/manage.py check            test the database connection
    python scripts/manage.py shrink-images    bring stored images within the size limit

``setup`` connects to PostgreSQL once as the superuser to create a dedicated
database and a login role that owns only that database. The service itself
then runs as that limited role; the superuser password is not stored.
"""

import argparse
import getpass
import os
import re
import secrets
import sys

from common import BASE_DIR  # noqa: F401  (puts src/ on the path)

ENV_PATH = os.path.join(BASE_DIR, ".env")
IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


# ---------------- helpers ----------------

def ask(prompt, default=None, secret=False, validate=None):
    while True:
        label = "%s [%s]: " % (prompt, default) if default else "%s: " % prompt
        value = getpass.getpass("  " + label) if secret else input("  " + label).strip()
        if not value and default is not None:
            value = default
        problem = validate(value) if validate else (None if value else "A value is required.")
        if not problem:
            return value
        print("    " + problem)


def read_env():
    values = {}
    if os.path.isfile(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip()
    return values


def write_env(values):
    lines = [
        "# Ear Disease Detection System settings. Keep this file private:",
        "# it holds the database password and the token signing key.",
        "",
        "DATABASE_URL=%s" % values["DATABASE_URL"],
        "JWT_SECRET_KEY=%s" % values["JWT_SECRET_KEY"],
        "STORAGE_DIR=%s" % values.get("STORAGE_DIR", ""),
        "# Set to true once the service is reached over HTTPS.",
        "COOKIE_SECURE=%s" % values.get("COOKIE_SECURE", "false"),
        "# Set to true only behind a reverse proxy that sets X-Real-IP.",
        "TRUST_PROXY=%s" % values.get("TRUST_PROXY", "false"),
        "MIN_REVIEWS_FOR_RETRAIN=%s" % values.get("MIN_REVIEWS_FOR_RETRAIN", "50"),
        "# The OAuth client ID for Sign in with Google. Empty means passwords.",
        "GOOGLE_CLIENT_ID=%s" % values.get("GOOGLE_CLIENT_ID", ""),
    ]
    # Anything else already in the file, such as the backtest settings, is
    # kept, so re-running setup never quietly undoes a change made by hand.
    written = {line.split("=", 1)[0] for line in lines if "=" in line}
    extra = ["%s=%s" % (k, v) for k, v in sorted(values.items()) if k not in written]
    if extra:
        lines += ["", "# Kept from the previous settings file."] + extra
    with open(ENV_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines + [""]))


def provision_database(host, port, superuser, superpass, db_name, db_user, db_pass):
    """Create the login role and database if they do not exist."""
    import psycopg
    from psycopg import sql

    with psycopg.connect(host=host, port=port, user=superuser, password=superpass,
                         dbname="postgres", autocommit=True, connect_timeout=10) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (db_user,)).fetchone()
        statement = "ALTER ROLE {} WITH LOGIN PASSWORD {}" if exists else \
            "CREATE ROLE {} WITH LOGIN PASSWORD {}"
        conn.execute(sql.SQL(statement).format(sql.Identifier(db_user), sql.Literal(db_pass)))

        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8'").format(
                sql.Identifier(db_name), sql.Identifier(db_user)))

    # Only the owning role may use the new database.
    with psycopg.connect(host=host, port=port, user=superuser, password=superpass,
                         dbname=db_name, autocommit=True, connect_timeout=10) as conn:
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(db_name)))
        conn.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(db_user)))


def database_url(host, port, db_name, db_user, db_pass):
    from sqlalchemy.engine import URL
    return URL.create("postgresql+psycopg", username=db_user, password=db_pass,
                      host=host, port=int(port), database=db_name).render_as_string(hide_password=False)


def init_database(url):
    from ear_detection.db import db, make_db_app, upgrade_schema
    from ear_detection.registry import ensure_seed_version

    app = make_db_app(url)
    with app.app_context():
        db.create_all()
        for column in upgrade_schema():
            print("Upgraded database: added column %s" % column)
        ensure_seed_version()
    return app


def create_user(app, username, full_name, role, password, must_change):
    from ear_detection.auth import email_problem, hash_password, password_problem
    from ear_detection.db import User, db, record

    username = (username or "").strip().lower()
    problem = email_problem(username) or password_problem(password, username)
    if problem:
        raise SystemExit(problem)
    with app.app_context():
        if User.query.filter_by(username=username).first():
            raise SystemExit("An account already uses %s." % username)
        user = User(username=username, full_name=full_name, role=role,
                    password_hash=hash_password(password), must_change_password=must_change)
        db.session.add(user)
        db.session.flush()
        record("user_created", entity="user", entity_id=user.id,
               detail={"username": username, "role": role, "via": "manage.py"})
        db.session.commit()


def valid_email(value):
    """People sign in with their email address, so that is the account name."""
    from ear_detection.auth import email_problem
    return email_problem((value or "").strip().lower())


def valid_identifier(value):
    return None if IDENTIFIER.match(value or "") else \
        "Lower case letters, digits and underscores, starting with a letter."


def ask_new_password(username):
    from ear_detection.auth import password_problem
    while True:
        first = ask("Password", secret=True, validate=lambda v: password_problem(v, username))
        if ask("Repeat password", secret=True) == first:
            return first
        print("    The passwords did not match.")


# ---------------- commands ----------------

def cmd_setup(args):
    print("\nEar Disease Detection System, first time setup\n")
    existing = read_env()

    print("PostgreSQL connection (the superuser is used once and not stored)")
    host = args.pg_host or ask("Host", "localhost")
    port = args.pg_port or ask("Port", "5432", validate=lambda v: None if v.isdigit() else "A number.")
    superuser = args.pg_superuser or ask("Superuser", "postgres")
    superpass = args.pg_password or os.environ.get("PGPASSWORD") or \
        ask("Superuser password", secret=True)
    db_name = args.db_name or ask("New database name", "ear_screening", validate=valid_identifier)
    db_user = args.db_user or ask("New database user", "ear_app", validate=valid_identifier)
    db_pass = secrets.token_urlsafe(24)

    print("\nCreating database %s owned by %s ..." % (db_name, db_user))
    try:
        provision_database(host, port, superuser, superpass, db_name, db_user, db_pass)
    except Exception as exc:
        raise SystemExit("Could not set up PostgreSQL: %s" % exc)

    url = database_url(host, port, db_name, db_user, db_pass)
    write_env({
        "DATABASE_URL": url,
        "JWT_SECRET_KEY": existing.get("JWT_SECRET_KEY") or secrets.token_urlsafe(48),
        "STORAGE_DIR": existing.get("STORAGE_DIR", ""),
        "COOKIE_SECURE": existing.get("COOKIE_SECURE", "false"),
        "TRUST_PROXY": existing.get("TRUST_PROXY", "false"),
        "MIN_REVIEWS_FOR_RETRAIN": existing.get("MIN_REVIEWS_FOR_RETRAIN", "50"),
        "GOOGLE_CLIENT_ID": existing.get("GOOGLE_CLIENT_ID", ""),
    })
    print("Wrote settings to .env")

    print("Creating tables ...")
    app = init_database(url)

    from ear_detection.db import User
    with app.app_context():
        has_admin = User.query.filter_by(role="admin").first() is not None
    if has_admin:
        print("An administrator already exists; skipping account creation.")
    else:
        print("\nFirst administrator account")
        username = args.admin_username or ask("Email address", validate=valid_email)
        full_name = args.admin_name or ask("Full name")
        password = args.admin_password or ask_new_password(username)
        create_user(app, username, full_name, "admin", password, must_change=False)
        print("Created administrator %s." % username)

    print("\nSetup complete. Start the service with start.bat or 'python app.py'.\n")


def _app_or_exit():
    from ear_detection import config
    if not config.DATABASE_URL:
        raise SystemExit("DATABASE_URL is not set. Run 'python scripts/manage.py setup' first.")
    return init_database(config.DATABASE_URL)


def cmd_init_db(args):
    _app_or_exit()
    print("Tables are up to date.")


def cmd_create_user(args):
    app = _app_or_exit()
    username = args.email or args.username or ask("Email address", validate=valid_email)
    full_name = args.name or ask("Full name")
    role = args.role or ask("Role (admin or clinician)", "clinician",
                            validate=lambda v: None if v in ("admin", "clinician") else
                            "admin or clinician")
    password = args.password or ask_new_password(username)
    create_user(app, username, full_name, role, password, must_change=True)
    print("Created %s (%s). They will be asked to choose a new password at first sign in."
          % (username, role))


def _find_user(username):
    from ear_detection.db import User
    user = User.query.filter_by(username=(username or "").strip().lower()).first()
    if user is None:
        raise SystemExit("No account for %s." % username)
    return user


def cmd_reset_password(args):
    from ear_detection.auth import hash_password
    from ear_detection.db import db, record

    app = _app_or_exit()
    with app.app_context():
        user = _find_user(args.username)
        password = args.password or ask_new_password(user.username)
        user.password_hash = hash_password(password)
        user.must_change_password = True
        user.failed_logins = 0
        user.locked_until = None
        user.token_version += 1
        record("password_reset", entity="user", entity_id=user.id, detail={"via": "manage.py"})
        db.session.commit()
    print("Password reset for %s; they must change it at next sign in." % args.username)


def cmd_unlock(args):
    from ear_detection.db import db

    app = _app_or_exit()
    with app.app_context():
        user = _find_user(args.username)
        user.failed_logins = 0
        user.locked_until = None
        db.session.commit()
    print("Unlocked %s." % args.username)


def cmd_check(args):
    from ear_detection.db import Examination, ModelVersion, Review, User

    app = _app_or_exit()
    with app.app_context():
        active = ModelVersion.query.filter_by(status="active").first()
        print("Database connection OK")
        print("  users          %d" % User.query.count())
        print("  examinations   %d" % Examination.query.count())
        print("  reviews        %d" % Review.query.count())
        print("  active model   %s" % (active.version if active else "none"))


def cmd_shrink_images(args):
    """Bring patient images stored before the size limit within it.

    Each image over the limit is re-encoded exactly as a new upload would be.
    Results, reviews and the upload hash are left as they are; only the
    stored file, and its recorded width and height, change.
    """
    from ear_detection import config, imaging, storage
    from ear_detection.db import Examination, db

    limit = config.STORED_IMAGE_MAX_BYTES
    app = _app_or_exit()
    with app.app_context():
        oversized = []
        for exam in Examination.query.order_by(Examination.id).all():
            path = storage.absolute(exam.image_path)
            if os.path.isfile(path) and os.path.getsize(path) > limit:
                oversized.append((exam, path, os.path.getsize(path)))
        before = sum(size for _, _, size in oversized)
        print("%d stored images are over %d KB, %.1f MB in total."
              % (len(oversized), limit // 1024, before / 1048576.0))
        if not oversized or args.dry_run:
            return
        if not args.yes and ask("Re-encode them now? This cannot be undone (yes/no)", "no") != "yes":
            print("Nothing changed.")
            return

        after = 0
        for exam, path, _ in oversized:
            with open(path, "rb") as fh:
                raw = fh.read()
            data, extension, image = storage.fit_for_storage(
                raw, os.path.splitext(path)[1].lower(), imaging.open_image(raw))
            new_relative = os.path.splitext(exam.image_path)[0] + extension
            same_file = new_relative == exam.image_path
            target = storage.absolute(new_relative) + (".tmp" if same_file else "")
            with open(target, "wb") as fh:
                fh.write(data)
            exam.image_path = new_relative
            exam.image_width, exam.image_height = image.size
            db.session.commit()
            # The database points at the new file before the old one goes.
            if same_file:
                os.replace(target, path)
            else:
                os.remove(path)
            after += len(data)
        print("Done. %d images now take %.1f MB." % (len(oversized), after / 1048576.0))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="guided first time setup")
    for flag in ("pg-host", "pg-port", "pg-superuser", "pg-password", "db-name", "db-user",
                 "admin-username", "admin-name", "admin-password"):
        setup.add_argument("--" + flag)
    setup.set_defaults(func=cmd_setup)

    sub.add_parser("init-db", help="create any missing tables").set_defaults(func=cmd_init_db)

    create = sub.add_parser("create-user", help="add a user account")
    create.add_argument("--email", help="the address they sign in with")
    create.add_argument("--username", help=argparse.SUPPRESS)
    create.add_argument("--name")
    create.add_argument("--role", choices=("admin", "clinician"))
    create.add_argument("--password")
    create.set_defaults(func=cmd_create_user)

    reset = sub.add_parser("reset-password", help="set a new temporary password")
    reset.add_argument("username", metavar="EMAIL")
    reset.add_argument("--password")
    reset.set_defaults(func=cmd_reset_password)

    unlock = sub.add_parser("unlock", help="clear a sign in lockout")
    unlock.add_argument("username", metavar="EMAIL")
    unlock.set_defaults(func=cmd_unlock)

    sub.add_parser("check", help="test the database connection").set_defaults(func=cmd_check)

    shrink = sub.add_parser("shrink-images",
                            help="re-encode stored patient images that are over the size limit")
    shrink.add_argument("--dry-run", action="store_true", help="only report what would change")
    shrink.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    shrink.set_defaults(func=cmd_shrink_images)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
