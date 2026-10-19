"""Manual helper for the PostgreSQL test sandbox. It is run by a person, never by the tests.

    python tests/pg_sandbox.py print-setup-sql   # prints the SQL an administrator runs once
    python tests/pg_sandbox.py init              # marks the sandbox (prints no token)

The sandbox is ONE local, disposable PostgreSQL database with a dedicated role and a
dedicated schema ``ft_test``; see tests/db_safety.py for every condition it must meet.
``init`` repeats the server and role checks (loopback address and the URL's port as
reported by the server, an unprivileged role that owns only ft_test and has no rights
elsewhere, no other databases it may connect to, no foreign servers or extra
extensions) before it creates the marker table, and refuses an existing marker that
names another database. It connects through the same connection guard as the tests,
only to the URL's own target, and closes the gate again whatever happens.

It cannot tell a local server from a tunnel (SSH, VPN, kubectl port-forward) that ends
on a remote host's loopback: making sure the URL points at YOUR local server is up to
you. It never prints the URL, the password or the token.

Steps (on YOUR local server only, never a shared, remote or production one):

1. As a PostgreSQL superuser, run the output of ``print-setup-sql`` and then give the
   role a password of your own with ``\\password ft_test_runner`` in psql.
2. export FT_TEST_POSTGRES_URL='postgresql://ft_test_runner:<your password>@127.0.0.1:<port>/ft_test_sandbox'
3. python tests/pg_sandbox.py init
4. Read the token in psql, connected to ft_test_sandbox:
   SELECT token FROM ft_test.ft_test_sandbox_marker;
   and export FT_TEST_POSTGRES_TOKEN='<that token>'
5. python -m pytest tests -q
"""
import secrets
import sys

import db_safety

# The functions this tool relies on, taken once at import; integrity_problems() (checked
# before connecting) reports any later replacement of db_safety's attributes.
_INTEGRITY = db_safety.integrity_problems
_INSTALL_GUARD = db_safety.install_guard
_SANDBOX_CONFIG = db_safety.sandbox_config
_OPEN_GATE = db_safety.open_gate
_GATE_FOR = db_safety.gate_for
_DEACTIVATE = db_safety.deactivate
_GATE_IS_OPEN = db_safety.gate_is_open
_CHECK_SERVER = db_safety.check_server
_PG_VARIABLES = db_safety.pg_variables
_SafetyError = db_safety.SafetyError
_BlockedConnection = db_safety.BlockedConnection

SETUP_SQL = """\
-- Run as a PostgreSQL superuser on YOUR LOCAL server. Never on a shared, remote or production one.
-- A dedicated local server (cluster) for the tests is recommended: the REVOKE CONNECT lines
-- below also apply to every other role of the server.
CREATE ROLE ft_test_runner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
-- Then, in psql: \\password ft_test_runner   (no password is written here on purpose)
CREATE DATABASE ft_test_sandbox;
REVOKE ALL ON DATABASE ft_test_sandbox FROM PUBLIC;
GRANT CONNECT ON DATABASE ft_test_sandbox TO ft_test_runner;
-- The sandbox role must not be able to connect to any other database of the server.
-- Repeat this line for every other database that allows connections:
REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
REVOKE CONNECT ON DATABASE template1 FROM PUBLIC;
\\connect ft_test_sandbox
REVOKE ALL ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA ft_test AUTHORIZATION ft_test_runner;
"""


def _mark(psycopg2, config):
    connection = psycopg2.connect(config.dsn_write)
    try:
        _CHECK_SERVER(connection, config, read_only=False)
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS %s.%s ("
                "token TEXT PRIMARY KEY, database_name TEXT NOT NULL, "
                "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                % (db_safety.SANDBOX_SCHEMA, db_safety.MARKER_TABLE))
            cursor.execute("SELECT database_name FROM %s.%s" % (db_safety.SANDBOX_SCHEMA, db_safety.MARKER_TABLE))
            rows = cursor.fetchall()
            if len(rows) > 1:
                raise _SafetyError("the marker table holds more than one token; drop it and run init again")
            if rows and rows[0][0] != config.dbname:
                raise _SafetyError("the marker names another database; this is not the sandbox it was made for")
            if not rows:
                cursor.execute(
                    "INSERT INTO %s.%s (token, database_name) VALUES (%%s, current_database())"
                    % (db_safety.SANDBOX_SCHEMA, db_safety.MARKER_TABLE), (secrets.token_urlsafe(32),))
        connection.commit()
        return bool(rows)
    finally:
        connection.close()


def init():
    try:
        import psycopg2
    except ModuleNotFoundError:
        print("psycopg2 is not installed.", file=sys.stderr)
        return 2
    try:
        config = _SANDBOX_CONFIG(require_token=False)
        if config is None:
            print("FT_TEST_POSTGRES_URL is not set.", file=sys.stderr)
            return 2
        pg_names = _PG_VARIABLES()
        if pg_names:  # names only, never values
            raise _SafetyError("unset these variables first, libpq would read them: %s" % ", ".join(pg_names))
        # Even this tool may connect only to the validated target, and only while marking.
        _INSTALL_GUARD()
        problems = _INTEGRITY(False)
        if problems:
            raise _SafetyError("the connection guard could not be set up: %s" % "; ".join(problems))
        _OPEN_GATE(_GATE_FOR(config))
        try:
            existed = _mark(psycopg2, config)
        finally:
            _DEACTIVATE()
    except _BlockedConnection as exc:  # before psycopg2.Error: it is an OperationalError
        print("Refused by the test safety net, nothing was sent to a server: %s" % exc, file=sys.stderr)
        return 1
    except _SafetyError as exc:
        print("Refused: %s" % exc, file=sys.stderr)
        return 1
    except psycopg2.Error as exc:
        print("PostgreSQL error (%s); nothing was marked." % type(exc).__name__, file=sys.stderr)
        return 1
    except RuntimeError as exc:  # install_guard: the driver is not what the safety net expects
        print("Refused: %s" % exc, file=sys.stderr)
        return 1
    if _GATE_IS_OPEN():
        print("Refused: the connection gate did not close.", file=sys.stderr)
        return 1
    print("The sandbox %s marked. The token is not printed: read it in psql with" %
          ("was already" if existed else "is now"))
    print("  SELECT token FROM %s.%s;" % (db_safety.SANDBOX_SCHEMA, db_safety.MARKER_TABLE))
    print("and export it as %s." % db_safety.SANDBOX_TOKEN_VARIABLE)
    return 0


def main(argv):
    if len(argv) == 2 and argv[1] == "print-setup-sql":
        print(SETUP_SQL)
        return 0
    if len(argv) == 2 and argv[1] == "init":
        return init()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
