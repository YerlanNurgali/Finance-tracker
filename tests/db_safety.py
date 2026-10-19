"""Rules that keep the test suite away from every database except one local,
disposable, explicitly marked sandbox.

What the code does (by reading it; none of this has been run yet)
------------------------------------------------------------------
For code that goes through ``psycopg2.connect`` or a psycopg2 pool, every connection is
refused in Python, before libpq runs, unless a PostgreSQL test opened the gate for the
verified sandbox and the final DSN names exactly that sandbox (a loopback address, port,
database, role, the pinned ``-c`` options and only known harmless parameters), the call
has exactly the arguments psycopg2.connect passes, and integrity_problems() finds
nothing wrong. The DSN handed to libpq then also carries ``hostaddr=<that address>``;
that libpq prefers it to an address from the environment is libpq's documented
behaviour, not something proven here. Any error inside the check refuses the connection
too. Every refusal is logged with the thread that made it and counted.

The mutable state (gate, verified sandbox, logs, counter) lives in closures of the
functions below, not in a module dictionary. An open gate must match one of the ways a
gate is opened (verify_sandbox's temporary gate, the latest verified sandbox, open_gate
outside pytest). integrity_problems() compares the whole module namespace and every
function's code, defaults and closure cells with a snapshot taken at import, reports
names added to the module since (a new ``len``, ``set``, ``type``... in this namespace
would shadow the builtin inside every function here; a new dunder name only counts when a
builtin has that name, such as ``__import__``), whether any builtin the module's own code
text refers to (``len``, ``isinstance``, ``sorted``...: found from the code objects when the
check is built) is still the same object in the builtins dict, and checks the interception on
psycopg2 and that gate state. The guard, and the first step of the integrity check (the
one that looks for new names and replaced builtins), use the builtins they need through
references bound at set-up; the later steps and the other functions of the module rely on
that first step having found nothing. Only code that calls them in between could still be
misled (the check and the use are not one atomic step), and a builtin reached some other way
than by name in the code (``getattr`` with a computed name) is not watched. This makes
tampering visible; it does NOT make it impossible: inside one Python process, code that
walks closures, frames or memory can still change anything, the check itself included
(it is compared with a snapshot it also takes part in).

The guard sits on BOTH names of the connect function, ``psycopg2._connect`` (what
psycopg2.connect calls) and ``psycopg2._psycopg._connect`` (the extension module's own
attribute), so a replaced psycopg2.connect that calls the second one is refused too.
That nothing else in psycopg2 needs the second name unguarded is an assumption about the
library; a call that does is refused, never let through.

Not guaranteed (residual risks, documented in the README):

* code that creates a connection without either of those two functions (instantiating
  ``psycopg2.extensions.connection`` or a psycopg2.extras connection class directly,
  another driver, a C extension, a ``psql`` process): only the static checks in
  tests/test_safety_net.py look for these;
* environment-only libpq settings other than the address (sslmode, a password file...)
  set with ``os.putenv`` or native code are invisible here;
* that the sandbox server is physically on this machine: an SSH, VPN or
  ``kubectl port-forward`` tunnel that ends on the database host's own loopback looks
  local from inside PostgreSQL. ``inet_server_addr()`` and ``inet_server_port()`` are
  only used to REJECT what is clearly not local; they prove nothing;
* ``search_path=ft_test`` is not a security boundary: the protection is the dedicated
  role's lack of rights, which ``check_server`` verifies as far as the catalog of the
  sandbox database shows it (not other databases, not server files, not the OS).

No message here contains a DSN, password, token or environment value.
"""
import os
import re
import secrets
import sys
import threading
from collections import namedtuple
from contextlib import contextmanager
from types import CodeType, FunctionType
from urllib.parse import quote, urlsplit

try:
    import psycopg2
    from psycopg2.extensions import make_dsn, parse_dsn
except ModuleNotFoundError as _missing:  # no driver at all: nothing can connect through it
    if _missing.name not in ("psycopg2", "psycopg2.extensions"):
        raise
    psycopg2 = None
    make_dsn = None
    parse_dsn = None

SANDBOX_SCHEMA = "ft_test"
MARKER_TABLE = "ft_test_sandbox_marker"
SANDBOX_URL_VARIABLE = "FT_TEST_POSTGRES_URL"
SANDBOX_TOKEN_VARIABLE = "FT_TEST_POSTGRES_TOKEN"
DUMMY_DATABASE_URL = "postgresql://nobody:nothing@127.0.0.1:1/none"
# python-dotenv (1.2.1 is installed) loads nothing when this is "1"; an older version would
# ignore it, and only override=False plus the dummy DATABASE_URL would remain.
DOTENV_SWITCH = "PYTHON_DOTENV_DISABLED"
TESTS_DIR = os.path.dirname(os.path.realpath(__file__))

# libpq fills missing connection parameters from PG* variables. The names below are the
# ones known to redirect; EVERY variable starting with PG (even an empty one) is removed
# at start-up and refused at connect time (pg_variables()).
PG_ENVIRONMENT = (
    "PGHOST", "PGHOSTADDR", "PGPORT", "PGSERVICE", "PGSERVICEFILE", "PGSYSCONFDIR",
    "PGDATABASE", "PGUSER", "PGOPTIONS", "PGTARGETSESSIONATTRS", "PGPASSFILE", "PGPASSWORD",
)
# The only variables a test subprocess inherits (plus LC_*); everything else, the
# application's settings, secrets and every PYTHON*, LD_* and DYLD_* variable included,
# is dropped.
SUBPROCESS_ALLOWED = ("PATH", "LANG", "LANGUAGE", "TMPDIR", "TEMP", "TMP", "TZ",
                      "SYSTEMROOT", "COMSPEC", "PATHEXT", "__CF_USER_TEXT_ENCODING")
# What safe_subprocess_env(extra) refuses: anything that selects a database, the
# interpreter's or the loader's behaviour, or the inherited infrastructure above.
_CHILD_FORBIDDEN_PREFIXES = ("PG", "FT_TEST_", "PYTHON", "LD_", "DYLD_", "DATABASE", "LC_")
_CHILD_FIXED = ("DATABASE_URL", DOTENV_SWITCH, "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE", "PYTHONPATH")

# Only literal loopback addresses: "localhost" depends on the resolver and /etc/hosts.
LOOPBACK_ADDRESSES = frozenset(("127.0.0.1", "::1"))
WRITE_SETTINGS = frozenset(("search_path=" + SANDBOX_SCHEMA,))
READ_SETTINGS = frozenset(("default_transaction_read_only=on", "search_path=" + SANDBOX_SCHEMA))
# Every libpq parameter a sandbox connection may carry; anything else (service, hostaddr,
# passfile, sslcert, target_session_attrs, replication, ...) is refused.
ALLOWED_DSN_PARAMETERS = frozenset((
    "host", "port", "dbname", "user", "password", "options", "connect_timeout",
    "keepalives", "keepalives_idle", "keepalives_interval", "keepalives_count", "application_name",
))
_NUMERIC_DSN_PARAMETERS = ("connect_timeout", "keepalives", "keepalives_idle", "keepalives_interval", "keepalives_count")

_NAME = re.compile(r"^ft_test(_[a-z0-9]+)*$")
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,}$")
_PORT = re.compile(r"^[1-9][0-9]{0,4}$")
_DIGITS = re.compile(r"^[0-9]{1,6}$")
_OPTION = re.compile(r"\s*-c\s*([^\s=]+=[^\s]*)\s*")
_CHILD_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
_CONNECTION_TEXT = re.compile(
    r"postgres(ql)?://|\b(host|hostaddr|port|dbname|user|password|passfile|service|servicefile|sslmode)\s*=", re.I)
# Schemas that belong to PostgreSQL itself; everything else counts as "elsewhere".
# (Non-superusers cannot create schemas named pg_*; a superuser could, see the README.)
_OUTSIDE = "n.nspname <> %s AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'"
_ROLE = "(SELECT oid FROM pg_roles WHERE rolname = current_user)"
# Built-in functions that read server files, change the server or its logs. They are not
# executable by PUBLIC in a default installation; the sandbox role must not have them.
DANGEROUS_FUNCTIONS = (
    "lo_import", "lo_export", "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "pg_ls_logdir", "pg_ls_waldir", "pg_ls_tmpdir", "pg_ls_archive_statusdir", "pg_reload_conf",
    "pg_rotate_logfile", "pg_promote", "pg_switch_wal", "pg_create_restore_point",
    "pg_file_write", "pg_file_rename", "pg_file_unlink",
)
ALLOWED_EXTENSIONS = ("plpgsql",)

# What the connection guard lets through: one loopback address, port, database and role,
# and for the options one of the exact sets of ``-c name=value`` settings.
Gate = namedtuple("Gate", "host port dbname user settings")
SandboxConfig = namedtuple("SandboxConfig", "dsn_write dsn_read host port dbname user token")
# What install_guard() registers once: the objects the interception consists of.
_Trusted = namedtuple("_Trusted", "driver c_module real_connect guard guard_parts public_connect public_parts "
                                  "connection_class")


class SafetyError(Exception):
    """A rule of the safety net is broken. Messages never contain secrets."""


_BlockedBase = psycopg2.OperationalError if psycopg2 is not None else Exception


class BlockedConnection(_BlockedBase):
    """Raised instead of connecting. It is an OperationalError, so code that handles
    an unreachable database behaves as it would for one; the attempt is also logged."""


def pg_variables(environ=None):
    """The names (never the values) of the PG* variables in ``environ``, empty ones included."""
    environ = os.environ if environ is None else environ
    return sorted(name for name in environ if name.upper().startswith("PG"))


def installed_package(path):
    """Whether a module file lies in an installed-packages directory (site-packages or
    dist-packages) and not under tests/: a file named psycopg2.py next to the tests, or
    anywhere earlier on sys.path outside the installed packages, is not the driver."""
    if not isinstance(path, str) or not path:
        return False
    real = os.path.realpath(path)
    if real == TESTS_DIR or real.startswith(TESTS_DIR + os.sep):
        return False
    parts = real.split(os.sep)
    return "site-packages" in parts or "dist-packages" in parts


# --- The connection checks ---------------------------------------------------------------

def _hosts(value):
    # Only lower-cased: a trailing dot, a port suffix or an alias must not compare equal.
    return tuple(part.strip().lower() for part in str(value).split(",")) if value else ()


def option_settings(options):
    """The set of ``name=value`` of a libpq ``options`` string made of ``-c`` settings only."""
    text = options or ""
    position, found = 0, []
    while position < len(text):
        match = _OPTION.match(text, position)
        if not match or match.end() == position:
            raise SafetyError("the connection options are not plain -c settings")
        found.append(match.group(1))
        position = match.end()
    return frozenset(found)


def check_gate(gate):
    """Raises SafetyError unless ``gate`` is a Gate for a local ft_test sandbox: a literal
    loopback address, a numeric port, ft_test names and only the known option sets.
    This does NOT prove the server is local (see the module docstring)."""
    if type(gate) is not Gate:
        raise SafetyError("the gate is not a db_safety.Gate")
    host, port, dbname, user, settings = gate
    if host not in LOOPBACK_ADDRESSES:
        raise SafetyError("the gate is only for 127.0.0.1 or ::1")
    if not isinstance(port, str) or not _PORT.match(port) or int(port) > 65535:
        raise SafetyError("the gate needs a numeric port")
    if not isinstance(dbname, str) or not isinstance(user, str) or not _NAME.match(dbname) or not _NAME.match(user):
        raise SafetyError("the gate is only for an ft_test database and role")
    if (not isinstance(settings, tuple) or not settings
            or any(setting not in (WRITE_SETTINGS, READ_SETTINGS) for setting in settings)):
        raise SafetyError("the gate allows options that are not the sandbox's")


def check_connect(dsn, gate, environ=None):
    """Raises SafetyError unless a connection with this final DSN is allowed by ``gate``."""
    if pg_variables(environ):
        raise SafetyError("a PG* environment variable could redirect the connection")
    if gate is None:
        raise SafetyError("database connections are blocked in tests")
    check_gate(gate)
    if parse_dsn is None:
        raise SafetyError("the driver is not available to check the connection")
    if not isinstance(dsn, str) or not dsn.strip():
        raise SafetyError("a connection needs an explicit DSN")
    try:
        params = parse_dsn(dsn)
    except Exception:
        raise SafetyError("the DSN cannot be parsed") from None
    if params.get("service") or params.get("hostaddr") or params.get("replication"):
        raise SafetyError("service, hostaddr and replication are not allowed")
    unknown = sorted(set(params) - ALLOWED_DSN_PARAMETERS)
    if unknown:
        raise SafetyError("the DSN has parameters the sandbox does not allow (%s)" % ", ".join(unknown))
    if any(name in params and not _DIGITS.match(str(params[name])) for name in _NUMERIC_DSN_PARAMETERS):
        raise SafetyError("a numeric connection parameter is not a plain number")
    if _hosts(params.get("host")) != (gate.host,) or _hosts(params.get("port")) != (gate.port,):
        raise SafetyError("the host or port is not the sandbox's")
    if params.get("dbname") != gate.dbname or params.get("user") != gate.user:
        raise SafetyError("the database or role is not the sandbox's")
    if option_settings(params.get("options")) not in gate.settings:
        raise SafetyError("the connection options are not the sandbox's")


def _quoted(value):
    """A value of libpq's keyword/value format, always in single quotes, with ``\\`` and ``'``
    escaped by a backslash (the rule in libpq's documentation, "Connection Strings"). psycopg2's
    make_dsn quotes only a value with a Unicode space, so a password such as a Cyrillic one, whose
    UTF-8 bytes can include one that libpq's C isspace() takes for a space, was cut in the
    middle: ``invalid dsn: missing "=" after ...``."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _harden_dsn(dsn, gate):
    """The DSN actually handed to libpq: the checked one plus ``hostaddr=<the gate's
    address>``, written out with every value quoted (see _quoted). The result must parse
    (by libpq's own parser, through parse_dsn) to exactly the checked parameters plus that
    hostaddr, nothing else changed. A DSN that already names a hostaddr is refused: it never
    reaches this point through check_connect, and here it would otherwise be overwritten.

    That libpq then prefers this explicit hostaddr over PGHOSTADDR or a service file set
    behind os.environ's back is libpq's documented behaviour, NOT something this code
    proves; other environment-only settings are not neutralised at all."""
    before = parse_dsn(dsn)
    if "hostaddr" in before:
        raise SafetyError("the DSN already names a hostaddr")
    final = " ".join("%s=%s" % (key, _quoted(value)) for key, value in {**before, "hostaddr": gate.host}.items())
    after = parse_dsn(final)
    if after.pop("hostaddr", None) != gate.host or after != before:
        raise SafetyError("normalising the DSN changed its parameters unexpectedly")
    return final


def _plain_connection_factory(factory, base):
    """None, or a connection class (with the ordinary metaclass) whose construction is
    psycopg2's own: a factory that builds the connection itself could ignore the DSN."""
    if factory is None:
        return True
    if type(factory) is not type(base) or not issubclass(factory, base):
        return False
    return not any("__init__" in vars(klass) or "__new__" in vars(klass)
                   for klass in factory.__mro__ if klass is not base and klass is not object)


def _function_snapshot(functions):
    """For every function given, and every function found in their closure cells: the
    function with its code, defaults, keyword defaults and the objects in its cells."""
    seen, todo, snapshot = set(), list(functions), []
    while todo:
        function = todo.pop()
        if id(function) in seen:
            continue
        seen.add(id(function))
        cells = tuple(cell.cell_contents for cell in (function.__closure__ or ()))
        snapshot.append((function, function.__code__, function.__defaults__,
                         _keyword_defaults(function), cells))
        todo.extend(content for content in cells if isinstance(content, FunctionType))
    return tuple(snapshot)


def _keyword_defaults(function):
    """The keyword-only defaults as a tuple (a copy that cannot be changed in place)."""
    return tuple(sorted((function.__kwdefaults__ or {}).items(), key=lambda item: item[0]))


def _code_names(code):
    """Every global or attribute name that ``code`` and the code nested in it (inner
    functions, comprehensions) refer to. Used once, by _seal(), to find the builtins this
    module's own code relies on; attribute names are included too, so the result can only
    be larger than needed (an unused builtin that is watched costs nothing)."""
    names = set(code.co_names)
    for constant in code.co_consts:
        if isinstance(constant, CodeType):
            names |= _code_names(constant)
    return names


def _function_problems(snapshot):
    """Names of the functions in ``snapshot`` whose code, defaults or cells changed."""
    changed = []
    for function, code, defaults, kwdefaults, cells in snapshot:
        try:
            now = tuple(cell.cell_contents for cell in (function.__closure__ or ()))
        except ValueError:  # an emptied cell
            now = None
        if (function.__code__ is not code or function.__defaults__ is not defaults
                or _keyword_defaults(function) != kwdefaults
                or now is None or len(now) != len(cells) or any(a is not b for a, b in zip(now, cells))):
            changed.append(getattr(function, "__qualname__", "?"))
    return changed


# --- The state: gate, verified sandbox, logs ---------------------------------------------------

def _make_core():
    """The mutable state of the safety net, held only by the closures returned here.
    The module exposes the functions, never the containers."""
    # gate: the open gate or None; config/verified: the active and the last verified sandbox;
    # verifying: the temporary gate of a running verify_sandbox(); opened: a gate opened by
    # open_gate() outside pytest. verifications: every configuration verify_sandbox()
    # accepted, appended in order and never removed.
    box = {"gate": None, "config": None, "verified": None, "verifying": None, "opened": None, "expect_node": None}
    pending, reported, expected, recorded = [], [], [], [0]
    verifications = []
    lock = threading.Lock()

    def record_refusal(reason):
        with lock:
            pending.append((reason, threading.get_ident()))
            recorded[0] += 1

    def pending_refusals():
        """Refusals not yet reported, as reasons (no DSNs)."""
        return [reason for reason, _ in pending]

    def reported_refusals():
        return [reason for reason, _ in reported]

    def expected_refusal_log():
        """Refusals that safety-net tests announced with expected_refusals(); kept apart."""
        return [reason for reason, _ in expected]

    def log_counts():
        with lock:
            return recorded[0], len(pending) + len(reported) + len(expected)

    def settle():
        """For tests/conftest.py only: moves the pending refusals to the reported ones and
        returns them, so each refusal is reported exactly once and never just dropped."""
        with lock:
            taken = list(pending)
            del pending[:len(taken)]
            reported.extend(taken)
        return [reason for reason, _ in taken]

    def allow_expected(nodeid):
        """For tests/conftest.py only: the test (node id) that may announce refusals, or None."""
        box["expect_node"] = nodeid or None

    def expected_refusals(count):
        """Inside the body of a safety-net test (marked expect_blocked in
        tests/test_safety_net.py): exactly ``count`` refusals of THIS thread must happen in
        the block. Those, and only those, move to the separate log of expected refusals.
        Refusals of other threads stay pending; more, fewer, or an error in the block
        leave every entry where it is, so the test fails at teardown."""
        node = box["expect_node"]
        if not node or os.environ.get("PYTEST_CURRENT_TEST") != node + " (call)":
            raise AssertionError("expected refusals are only allowed in the body of tests marked expect_blocked "
                                 "in tests/test_safety_net.py")
        me = threading.get_ident()
        with lock:
            start = len(pending)
        yield
        with lock:
            mine = [entry for entry in pending[start:] if entry[1] == me]
            if len(mine) != count:
                raise AssertionError("expected %d refused connection(s), got %d" % (count, len(mine)))
            others = [entry for entry in pending[start:] if entry[1] != me]
            del pending[start:]
            pending.extend(others)
            expected.extend(mine)

    def gate_is_open():
        return box["gate"] is not None

    def is_latest_verification(config):
        return config is not None and config is box["verified"] and bool(verifications) and verifications[-1] is config

    def gate_state():
        """The open gate, or None when it is closed. Raises SafetyError when the open gate
        matches none of the ways a gate is opened (the state was changed by hand): the
        temporary gate of verify_sandbox(), the gate of the latest verified sandbox
        (activate()), or a gate open_gate() opened outside pytest."""
        gate, config = box["gate"], box["config"]
        if gate is None:
            return None
        if gate is box["verifying"]:
            return gate
        if config is None and gate is box["opened"]:
            return gate
        if is_latest_verification(config) and gate == gate_for(config):
            return gate
        raise SafetyError("the open gate does not belong to a verified sandbox (state changed by hand?)")

    def state_problems():
        """For integrity_problems(): the gate and verification state are consistent."""
        try:
            gate_state()
        except SafetyError as exc:
            return [str(exc)]
        if box["verified"] is not None and not is_latest_verification(box["verified"]):
            return ["the verified sandbox is not the one verify_sandbox() accepted last"]
        return []

    def open_gate(gate):
        """For tests/pg_sandbox.py, run by a person outside pytest, which checks the server
        before it changes anything. Refused under pytest: tests use the pg_gate fixture."""
        if sys.modules.get("_pytest") is not None:
            raise SafetyError("open_gate is only for tests/pg_sandbox.py; tests use the pg_gate fixture")
        check_gate(gate)
        box["opened"] = gate
        box["gate"] = gate

    def activate(config):
        """Opens the gate for the sandbox; only the configuration verify_sandbox() accepted last."""
        if not is_latest_verification(config):
            raise SafetyError("the gate opens only for the sandbox verify_sandbox() accepted")
        gate = gate_for(config)
        check_gate(gate)
        box["config"] = config
        box["gate"] = gate

    def deactivate():
        box["config"] = None
        box["opened"] = None
        box["gate"] = None

    def verify_sandbox(config):
        """The one step that touches the database before any destructive test: a read-only
        session checks the server, the role, the database name and the marker token, in
        that order. Only a configuration that passes may open the gate (activate())."""
        box["verified"] = None
        gate = Gate(config.host, config.port, config.dbname, config.user, (READ_SETTINGS,))
        check_gate(gate)
        previous = box["gate"]
        box["verifying"] = gate
        box["gate"] = gate
        try:
            try:
                connection = psycopg2.connect(config.dsn_read)
            except BlockedConnection as exc:  # logged by the guard as well; the reason holds no DSN
                raise SafetyError("the safety net refused the sandbox connection (%s)" % exc) from None
            except psycopg2.Error as exc:
                raise SafetyError("the sandbox is not reachable (%s)" % type(exc).__name__) from None
            try:
                check_server(connection, config, read_only=True)
                try:
                    rows = _fetch(connection, "SELECT token, database_name FROM %s.%s" % (SANDBOX_SCHEMA, MARKER_TABLE))
                except psycopg2.Error:
                    raise SafetyError("the sandbox marker is missing (run tests/pg_sandbox.py init)") from None
                if len(rows) != 1 or rows[0][1] != config.dbname or not secrets.compare_digest(
                        str(rows[0][0]).encode("utf-8"), config.token.encode("utf-8")):
                    raise SafetyError("the sandbox marker does not match FT_TEST_POSTGRES_TOKEN")
            finally:
                connection.close()
        finally:
            box["gate"] = previous
            box["verifying"] = None
        verifications.append(config)
        box["verified"] = config

    def sandbox_sql(statement, params=()):
        """Runs one statement in the verified sandbox, in its own transaction. The first
        thing every connection does is to confirm database, schema and role."""
        config = box["config"]
        if box["gate"] is None or not is_latest_verification(config):
            raise SafetyError("the sandbox gate is closed (use the pg_gate fixture)")
        gate_state()  # raises when the open gate is not the verified sandbox's
        connection = psycopg2.connect(config.dsn_write)
        try:
            with connection, connection.cursor() as cursor:
                cursor.execute("SELECT current_database(), current_schema(), current_user")
                if tuple(cursor.fetchone()) != (config.dbname, SANDBOX_SCHEMA, config.user):
                    raise SafetyError("the connection is not in the sandbox's ft_test schema as the sandbox role")
                cursor.execute(statement, params)
                return cursor.fetchall() if cursor.description else None
        finally:
            connection.close()

    trusted = []

    def register_guard(snapshot):
        if trusted:
            raise RuntimeError("the guard is already registered")
        trusted.append(snapshot)

    def trusted_guard():
        return trusted[0] if trusted else None

    return dict(record_refusal=record_refusal, pending_refusals=pending_refusals, reported_refusals=reported_refusals,
                expected_refusal_log=expected_refusal_log, log_counts=log_counts, settle=settle,
                allow_expected=allow_expected, expected_refusals=expected_refusals, gate_is_open=gate_is_open,
                gate_state=gate_state, state_problems=state_problems, open_gate=open_gate, activate=activate,
                deactivate=deactivate, verify_sandbox=verify_sandbox, sandbox_sql=sandbox_sql,
                register_guard=register_guard, trusted_guard=trusted_guard)


_core = _make_core()
_record_refusal = _core["record_refusal"]
pending_refusals = _core["pending_refusals"]
reported_refusals = _core["reported_refusals"]
expected_refusal_log = _core["expected_refusal_log"]
_log_counts = _core["log_counts"]
_settle = _core["settle"]
_allow_expected = _core["allow_expected"]
expected_refusals = contextmanager(_core["expected_refusals"])
gate_is_open = _core["gate_is_open"]
_gate_state = _core["gate_state"]
_state_problems = _core["state_problems"]
open_gate = _core["open_gate"]
activate = _core["activate"]
deactivate = _core["deactivate"]
verify_sandbox = _core["verify_sandbox"]
sandbox_sql = _core["sandbox_sql"]
_register_guard = _core["register_guard"]
_trusted_guard = _core["trusted_guard"]
del _core


def gate_for(config):
    return Gate(config.host, config.port, config.dbname, config.user, (WRITE_SETTINGS, READ_SETTINGS))


# --- The interception ---------------------------------------------------------------------

def install_guard():
    """Puts the guard on ``psycopg2._connect`` and ``psycopg2._psycopg._connect`` for the
    rest of the process. Idempotent.

    The guard holds its own references (the real ``_connect``, the checks, the gate, the
    log and the builtins it uses), and what it consists of is registered once for
    integrity_problems()."""
    if psycopg2 is None or _trusted_guard() is not None:
        return
    if not hasattr(psycopg2, "_connect") or not callable(getattr(psycopg2, "connect", None)):
        raise RuntimeError("psycopg2 no longer has connect/_connect: the test safety net must be updated")
    base = getattr(getattr(psycopg2, "extensions", None), "connection", None)
    c_module = sys.modules.get("psycopg2._psycopg")
    if not isinstance(base, type) or c_module is None or make_dsn is None:
        raise RuntimeError("psycopg2 is not the expected package: the test safety net must be updated")
    if not installed_package(getattr(psycopg2, "__file__", None)) or not installed_package(
            getattr(c_module, "__file__", None)):
        raise RuntimeError("psycopg2 is not loaded from an installed package (a module of that name "
                           "shadows it?): the test safety net cannot be confirmed")

    real_connect, check, harden, plain = psycopg2._connect, check_connect, _harden_dsn, _plain_connection_factory
    gate_now, record, integrity = _gate_state, _record_refusal, integrity_problems
    allowed_keywords = frozenset(("dsn", "connection_factory", "async", "async_"))
    if getattr(c_module, "_connect", None) is not real_connect:
        raise RuntimeError("psycopg2._connect and psycopg2._psycopg._connect are not the same function: "
                           "the test safety net must be updated")
    # The builtins and exception classes the guard uses, bound now. Names added to this module's
    # namespace later cannot replace them inside the guard (and integrity_problems() reports
    # such names anyway).
    count, make_set, text, kind, failure = len, set, str, type, Exception
    refusal, blocked = SafetyError, BlockedConnection

    def guarded_connect(*args, **kwargs):
        try:
            problems = integrity(True)  # True: psycopg2.connect itself may be monkeypatched in a test
            if problems:
                raise refusal("the test safety net was tampered with (%s)" % "; ".join(problems))
            # Only the calls psycopg2.connect makes: _connect(dsn, connection_factory=..., [async_=...]).
            # Any other positional argument (a positional factory, for instance) or keyword
            # is refused before the driver could see it.
            if count(args) + ("dsn" in kwargs) != 1 or not make_set(kwargs) <= allowed_keywords:
                raise refusal("unexpected arguments to the connection function")
            dsn = args[0] if args else kwargs["dsn"]
            if not plain(kwargs.get("connection_factory"), base):
                raise refusal("a custom connection_factory could connect somewhere else")
            gate = gate_now()
            check(dsn, gate)
            final = harden(dsn, gate)
        except refusal as exc:
            record(text(exc))
            raise blocked("blocked by the test safety net: %s" % exc) from None
        except failure as exc:  # an unexpected error in the check refuses too, and is logged
            reason = "the safety check itself failed (%s)" % kind(exc).__name__
            record(reason)
            raise blocked("blocked by the test safety net: %s" % reason) from None
        return real_connect(final, **{key: value for key, value in kwargs.items() if key != "dsn"})

    # Partial states, as far as the text shows. A failed write to the extension module (an
    # Exception) is rolled back below and nothing is registered. Registration comes last, so
    # "registered but not installed" cannot happen. An exception that is not an Exception
    # (KeyboardInterrupt, SystemExit) between the two writes is NOT rolled back on purpose:
    # system exceptions are not swallowed or replaced here. It would leave psycopg2._connect
    # guarded, psycopg2._psycopg._connect not, and nothing registered; integrity_problems()
    # then reports "the guard is not installed" (so every psycopg2.connect is refused and
    # tests/conftest.py stops at import), but a direct call to the extension's own _connect
    # would not be refused. The interrupt normally ends the process.
    public = psycopg2.connect
    psycopg2._connect = guarded_connect
    try:
        c_module._connect = guarded_connect  # the extension module's own name for the same function
    except Exception as exc:
        psycopg2._connect = real_connect
        raise RuntimeError("psycopg2._psycopg._connect could not be guarded (%s): the test safety net "
                           "must be updated" % type(exc).__name__) from None
    _register_guard(_Trusted(psycopg2, c_module, real_connect, guarded_connect,
                             _function_snapshot([guarded_connect]), public, _function_snapshot([public]), base))


def guard_is_installed():
    return psycopg2 is not None and not integrity_problems(False)


# --- The sandbox ---------------------------------------------------------------------------

def sandbox_configured(environ=None):
    environ = os.environ if environ is None else environ
    return bool((environ.get(SANDBOX_URL_VARIABLE) or "").strip())


def sandbox_config(environ=None, require_token=True):
    """Validates FT_TEST_POSTGRES_URL by its text alone (nothing is connected) and
    returns a SandboxConfig, or None when the variable is not set."""
    environ = os.environ if environ is None else environ
    url = (environ.get(SANDBOX_URL_VARIABLE) or "").strip()
    if not url:
        return None
    if "?" in url or "#" in url or any(ch.isspace() for ch in url):
        raise SafetyError("the sandbox URL must have no query, fragment or spaces (options are added by the tests)")
    try:
        parts = urlsplit(url)
        host, port, user = parts.hostname, parts.port, parts.username
    except ValueError:
        raise SafetyError("the sandbox URL cannot be parsed") from None
    if parts.scheme not in ("postgresql", "postgres") or "," in parts.netloc:
        raise SafetyError("the sandbox URL must be a plain postgresql:// URL with one host")
    if host not in LOOPBACK_ADDRESSES:
        raise SafetyError("the sandbox must be addressed as 127.0.0.1 or [::1] (not localhost, not a remote host)")
    if port is None:
        raise SafetyError("the sandbox URL needs an explicit port")
    if parts.path.count("/") != 1 or not _NAME.match(parts.path[1:]):
        raise SafetyError("the sandbox database name must look like ft_test or ft_test_<name>")
    if not user or not _NAME.match(user):
        raise SafetyError("the sandbox role name must look like ft_test or ft_test_<name> (a dedicated role)")
    token = (environ.get(SANDBOX_TOKEN_VARIABLE) or "").strip()
    if require_token and not _TOKEN.match(token):
        raise SafetyError("FT_TEST_POSTGRES_TOKEN is missing or malformed (see tests/pg_sandbox.py)")
    write = url + "?options=" + quote("-c search_path=" + SANDBOX_SCHEMA, safe="")
    read = url + "?options=" + quote("-c default_transaction_read_only=on -c search_path=" + SANDBOX_SCHEMA, safe="")
    return SandboxConfig(write, read, host, str(port), parts.path[1:], user, token)


def _fetch(connection, statement, params=()):
    with connection.cursor() as cursor:
        cursor.execute(statement, params)
        return cursor.fetchall()


def _count(connection, statement, params=()):
    return _fetch(connection, statement, params)[0][0]


# Each of these must count zero objects; the message says what was found (never names
# of data, only of the kind of right). They cover the catalog of the sandbox database
# only: rights in other databases, on server files and on the OS are out of sight.
_ZERO_CHECKS = (
    # Any membership is refused: pg_read_all_data, pg_write_all_data, pg_execute_server_program,
    # pg_read_server_files, pg_monitor, pg_signal_backend and every other granted role.
    ("SELECT count(*) FROM pg_auth_members WHERE member = " + _ROLE, False,
     "the sandbox role must not be a member of any role"),
    ("SELECT count(*) FROM pg_database WHERE datdba = " + _ROLE, False,
     "the sandbox role must not own a database"),
    ("SELECT count(*) FROM pg_database WHERE datname = current_database() "
     "AND has_database_privilege(oid, 'CREATE, TEMPORARY')", False,
     "the sandbox role must not have CREATE or TEMP on the database"),
    ("SELECT count(*) FROM pg_database WHERE datname <> current_database() AND datallowconn "
     "AND has_database_privilege(oid, 'CONNECT')", False,
     "the sandbox role can connect to other databases of this server (revoke CONNECT from PUBLIC; "
     "a dedicated local server is recommended)"),
    ("SELECT count(*) FROM pg_namespace n WHERE " + _OUTSIDE + " AND has_schema_privilege(n.oid, 'USAGE, CREATE')",
     True, "the sandbox role may use or create in schemas outside ft_test"),
    ("SELECT count(*) FROM pg_namespace WHERE nspowner = " + _ROLE + " AND nspname <> %s", True,
     "the sandbox role owns schemas other than ft_test"),
    ("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
     "WHERE c.relowner = " + _ROLE + " AND " + _OUTSIDE, True,
     "the sandbox role owns tables, views or sequences outside ft_test"),
    ("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
     "WHERE p.proowner = " + _ROLE + " AND " + _OUTSIDE, True,
     "the sandbox role owns functions outside ft_test"),
    ("SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
     "WHERE t.typowner = " + _ROLE + " AND " + _OUTSIDE, True,
     "the sandbox role owns types outside ft_test"),
    ("SELECT count(*) FROM pg_largeobject_metadata WHERE lomowner = " + _ROLE, False,
     "the sandbox role owns large objects"),
    # Rights held directly or through PUBLIC.
    ("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
     "WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND " + _OUTSIDE +
     " AND (has_table_privilege(c.oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')"
     " OR has_any_column_privilege(c.oid, 'SELECT, INSERT, UPDATE, REFERENCES'))", True,
     "the sandbox role has rights on tables or columns outside ft_test"),
    ("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
     "WHERE c.relkind = 'S' AND " + _OUTSIDE + " AND CASE WHEN c.relkind = 'S' "
     "THEN has_sequence_privilege(c.oid, 'USAGE, SELECT, UPDATE') ELSE false END",
     True, "the sandbox role has rights on sequences outside ft_test"),
    ("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
     "WHERE " + _OUTSIDE + " AND has_function_privilege(p.oid, 'EXECUTE')", True,
     "the sandbox role may execute functions outside ft_test and the system schemas"),
    ("SELECT count(*) FROM pg_foreign_data_wrapper", False,
     "a foreign-data wrapper is installed: the server could connect elsewhere"),
    ("SELECT count(*) FROM pg_foreign_server", False,
     "a foreign server is defined: the server could connect elsewhere"),
    ("SELECT count(*) FROM pg_user_mappings", False,
     "a user mapping is defined: the server could connect elsewhere"),
)


def check_server(connection, config, read_only):
    """Checks, through an open connection, what the catalog of the sandbox database can
    tell about the server and the role. Raises SafetyError on the first problem; changes
    nothing. Passing these checks does NOT prove isolation: see the module docstring.

    The address and port checks only reject what is clearly not a local server; they
    cannot detect a tunnel that ends on the database host's own loopback."""
    try:
        name, user, schema, transaction_read_only, in_recovery, address, port = _fetch(
            connection,
            "SELECT current_database(), current_user, current_schema(), "
            "current_setting('transaction_read_only'), pg_is_in_recovery(), "
            "inet_server_addr(), inet_server_port()")[0]
        if name != config.dbname or user != config.user:
            raise SafetyError("the server reports another database or role than the URL")
        if schema != SANDBOX_SCHEMA:
            raise SafetyError("the sandbox schema ft_test does not exist or is not first in the search path")
        if transaction_read_only != ("on" if read_only else "off"):
            raise SafetyError("the session is not in the expected read-only mode")
        if in_recovery:
            raise SafetyError("the server is a replica")
        if address is None or str(address).split("/")[0] not in LOOPBACK_ADDRESSES:
            raise SafetyError("the server does not report a loopback address (not a local TCP connection)")
        if str(port) != config.port:
            raise SafetyError("the server listens on another port than the URL's (a forwarded connection?)")
        role = _fetch(connection, "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
                                  "FROM pg_roles WHERE rolname = current_user")
        if len(role) != 1 or any(role[0]):
            raise SafetyError("the sandbox role must have no special attributes")
        if _fetch(connection, "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = %s",
                  (SANDBOX_SCHEMA,)) != [(config.user,)]:
            raise SafetyError("the sandbox role must own the ft_test schema")
        for statement, needs_schema, message in _ZERO_CHECKS:
            if _count(connection, statement, (SANDBOX_SCHEMA,) if needs_schema else ()):
                raise SafetyError(message)
        if _count(connection, "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                              "WHERE n.nspname = 'pg_catalog' AND p.proname::text = ANY(%s) "
                              "AND has_function_privilege(p.oid, 'EXECUTE')", (list(DANGEROUS_FUNCTIONS),)):
            raise SafetyError("the sandbox role may execute server-file or server-control functions")
        extensions = sorted(row[0] for row in _fetch(connection, "SELECT extname FROM pg_extension")
                            if row[0] not in ALLOWED_EXTENSIONS)
        if extensions:
            raise SafetyError("extensions other than plpgsql are installed (%s)" % ", ".join(extensions))
    except psycopg2.Error as exc:
        raise SafetyError("the sandbox could not be checked (%s)" % type(exc).__name__) from None


# --- Subprocesses --------------------------------------------------------------------------

def safe_subprocess_env(extra=None):
    """A minimal environment for a test subprocess, built from an allow-list: PATH,
    locale and temporary-directory variables only. On top: the dummy DATABASE_URL,
    .env loading switched off, no user site, and a PYTHONPATH that loads
    tests/subprocess_guard/sitecustomize.py, which refuses every psycopg2 connection.

    ``extra`` adds test settings: upper-case names that are not PG*, FT_TEST_*, PYTHON*,
    LD_*, DYLD_*, DATABASE*, LC_* or an inherited variable, with text values that do not
    look like connection settings. Errors name the variable, never its value."""
    env = {key: value for key, value in os.environ.items()
           if key.upper() in SUBPROCESS_ALLOWED or key.upper().startswith("LC_")}
    env.update({
        "DATABASE_URL": DUMMY_DATABASE_URL,
        DOTENV_SWITCH: "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.path.join(TESTS_DIR, "subprocess_guard"),
    })
    for key, value in (extra or {}).items():
        name = key.upper() if isinstance(key, str) else ""
        if (not isinstance(key, str) or not _CHILD_KEY.match(key) or name.startswith(_CHILD_FORBIDDEN_PREFIXES)
                or name in SUBPROCESS_ALLOWED or name in _CHILD_FIXED):
            raise ValueError("a test subprocess may not receive %s" % (key if isinstance(key, str) else "<non-text name>"))
        if not isinstance(value, str) or "\x00" in value or _CONNECTION_TEXT.search(value):
            raise ValueError("the value for %s is not a plain setting (connection settings are not allowed)" % key)
        env[key] = value
    return env


# --- Restoring process-wide state ------------------------------------------------------------

_CONTAINER_TYPES = (dict, list, set)


def snapshot_environ(environ=None):
    """A copy of the environment, without pytest's own PYTEST_* variables (pytest sets
    and removes those itself and fails if they disappear)."""
    environ = os.environ if environ is None else environ
    return {key: value for key, value in environ.items() if not key.startswith("PYTEST_")}


def restore_environ(snapshot, environ=None):
    """Makes the environment equal to ``snapshot`` again (PYTEST_* left alone) and returns
    the names, never the values, that had to change."""
    environ = os.environ if environ is None else environ
    changed = []
    for key in [key for key in environ if not key.startswith("PYTEST_") and key not in snapshot]:
        del environ[key]
        changed.append(key)
    for key, value in snapshot.items():
        if environ.get(key) != value:
            environ[key] = value
            changed.append(key)
    return sorted(changed)


def snapshot_module(module, runtime=()):
    """The module's global names and values, except ``runtime`` names that the module
    itself rebinds while it works (e.g. database._pool), plus a shallow copy of every
    plain dict, list and set among them (dunder names aside)."""
    names = {key: value for key, value in vars(module).items() if key not in runtime}
    contents = {key: value.copy() for key, value in names.items()
                if type(value) in _CONTAINER_TYPES and not key.startswith("__")}
    return names, contents


def restore_module(module, snapshot, runtime=()):
    """Puts every global of ``module`` back to ``snapshot`` (names added since are removed)
    and the top-level contents of its plain dicts, lists and sets; returns the names that
    had to change. Objects nested deeper, and other objects changed in place, are not
    restored."""
    names, contents = snapshot
    namespace = vars(module)
    missing = object()
    changed = []
    for key in [key for key in namespace if key not in runtime and key not in names]:
        del namespace[key]
        changed.append(key)
    for key, value in names.items():
        if namespace.get(key, missing) is not value:
            namespace[key] = value
            changed.append(key)
    for key, saved in contents.items():
        current = names[key]
        if current != saved:
            if type(current) is list:
                current[:] = saved
            else:
                current.clear()
                current.update(saved)
            changed.append(key)
    return sorted(set(changed))


# --- Integrity of the interception and of this module ---------------------------------------

def _seal():
    """Builds integrity_problems() around references and snapshots taken now, at import:
    every global of this module, every function's code, defaults and closure cells, and
    (once the guard is installed) the interception on psycopg2. tests/conftest.py keeps
    its own reference to the function it got at import."""
    namespace = globals()
    module = sys.modules.get(__name__)
    # Reference data only, in tuples (nothing in them can be changed in place).
    values = tuple((key, value) for key, value in namespace.items() if not key.startswith("__"))
    functions = _function_snapshot([value for _, value in values if isinstance(value, FunctionType)])
    function_problems, trusted_guard, log_counts, state_problems = (
        _function_problems, _trusted_guard, _log_counts, _state_problems)
    driver, packaged, missing = namespace["psycopg2"], installed_package, object()
    # Every name the module has now, and the dict of builtins its functions fall back on. A name
    # added later (len, set, type, isinstance, Exception...) would shadow a builtin in every
    # function of this module. The builtins the check itself needs are bound here, so the
    # first step of the check does not depend on the names it looks for.
    known, builtins_ref = frozenset(namespace), namespace.get("__builtins__")
    make_set, ordered = set, sorted
    # The builtins dict itself (a module's __builtins__ is normally that dict; if it is the
    # builtins module, its namespace). Replacing an entry of it is a second way to change what
    # `len`, `isinstance`... mean inside this module; `used` (filled in below) records the
    # identity of every builtin this module's code refers to, so such a replacement is reported.
    builtins_dict = builtins_ref if isinstance(builtins_ref, dict) else getattr(builtins_ref, "__dict__", {})

    def integrity_problems(during_test):
        """What has happened to the interception and to this module, as reasons (empty when
        intact). ``during_test`` has no default on purpose: every caller says which check
        it wants. True (right before a test body, and inside the guard) tolerates a replaced
        psycopg2.connect, which a fixture may monkeypatch; False requires the original.

        This function is compared with a snapshot it takes part in: replacing its own code
        or the reference data in its closure is not detected (only the static checks forbid
        reaching them)."""
        added = make_set(namespace) - known
        added.discard("integrity_problems")  # put there by _seal() itself
        # A new dunder name (e.g. __warningregistry__) shadows nothing unless a builtin has that
        # name (__import__): only those are reported.
        added = [name for name in added if not name.startswith("__") or name in builtins_dict]
        replaced = namespace.get("__builtins__") is not builtins_ref
        swapped = [name for name, original in used if builtins_dict.get(name, missing) is not original]
        if added or replaced or swapped:
            early = []
            if added or replaced:
                early.append("db_safety has new or replaced module-level names (%s): they could shadow builtins"
                             % ", ".join(ordered(added) + (["__builtins__"] if replaced else [])))
            if swapped:
                early.append("builtins that db_safety relies on were replaced (%s)" % ", ".join(ordered(swapped)))
            return early
        problems = []
        if sys.modules.get(__name__) is not module or namespace.get("integrity_problems") is not integrity_problems:
            problems.append("tests/db_safety.py was replaced, reloaded or its integrity check swapped")
        for key, value in values:
            if namespace.get(key, missing) is not value:
                problems.append("db_safety.%s was replaced" % key)
        problems += ["db_safety function %s was changed (code, defaults or closure)" % name
                     for name in function_problems(functions)]
        recorded, kept = log_counts()
        if recorded != kept:
            problems.append("refusals were removed from the log")
        problems += state_problems()
        if driver is None:
            if sys.modules.get("psycopg2") is not None:
                problems.append("psycopg2 appeared after the guard was set up")
            return problems
        trusted = trusted_guard()
        if trusted is None:
            problems.append("the guard is not installed")
            return problems
        if sys.modules.get("psycopg2") is not trusted.driver or trusted.driver is not driver:
            problems.append("psycopg2 was removed from, reloaded in or replaced in sys.modules")
        if sys.modules.get("psycopg2._psycopg") is not trusted.c_module:
            problems.append("psycopg2._psycopg was removed or replaced in sys.modules")
        if getattr(trusted.c_module, "_connect", None) is not trusted.guard:
            problems.append("psycopg2._psycopg._connect is no longer the guard (replaced)")
        if getattr(driver, "_connect", None) is not trusted.guard or function_problems(trusted.guard_parts):
            problems.append("psycopg2._connect is no longer the intact guard (reloaded or patched)")
        if not during_test and (getattr(driver, "connect", None) is not trusted.public_connect
                                or function_problems(trusted.public_parts)):
            problems.append("psycopg2.connect was replaced (and not put back)")
        if getattr(getattr(driver, "extensions", None), "connection", None) is not trusted.connection_class:
            problems.append("psycopg2.extensions.connection was replaced")
        pool = sys.modules.get("psycopg2.pool")
        if pool is not None and getattr(pool, "psycopg2", driver) is not driver:
            problems.append("psycopg2.pool no longer uses the guarded psycopg2")
        if not packaged(getattr(driver, "__file__", None)):
            problems.append("psycopg2 is not loaded from an installed package")
        return problems

    # The builtins this module's own code refers to (functions, the functions nested in them,
    # the methods of its classes, and the check above), with their identity now. `used` is read
    # by integrity_problems() through its closure; it is only assigned here because the check
    # is part of what is scanned. It lists what the code text refers to: a builtin reached in
    # some other way (getattr with a computed name, for instance) is not in it.
    # Only code defined in this module (not helpers imported from psycopg2 or the standard library).
    scanned = [value for _, value in values if isinstance(value, FunctionType) and value.__globals__ is namespace]
    for _, value in values:
        if isinstance(value, type) and getattr(value, "__module__", None) == __name__:
            scanned += [member for member in vars(value).values()
                        if isinstance(member, FunctionType) and member.__globals__ is namespace]
    scanned.append(integrity_problems)
    referred = set()
    for function in scanned:
        referred |= _code_names(function.__code__)
    used = tuple(sorted(((name, builtins_dict[name]) for name in referred if name in builtins_dict and name not in known),
                        key=lambda item: item[0]))

    namespace["integrity_problems"] = integrity_problems


_seal()
