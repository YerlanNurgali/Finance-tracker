"""Loaded automatically by Python in test subprocesses (see db_safety.safe_subprocess_env,
which makes this folder the child's PYTHONPATH): the child refuses every database connection.

A subprocess does not get tests/conftest.py, so without this a child that reached a
database would be unguarded. The checks are made before libpq runs, so nothing is sent.
In the child:

* DATABASE_URL is the dummy, .env loading is off, PG* and FT_TEST_* variables are removed;
* psycopg2._connect and psycopg2._psycopg._connect refuse everything; a psycopg2 that
  is not installed now cannot be imported later either;
* the other PostgreSQL drivers cannot be imported at all.

Python's site.py catches any exception raised here and only prints it, then runs the
child anyway; so on any failure this module ends the process with os._exit before a
line of the child's own code runs, instead of raising.

A psycopg2 that is not loaded from an installed-packages directory (a psycopg2.py
placed in tests/ or on PYTHONPATH, for instance) stops the child too.

NOT covered (this is no absolute guarantee): a child started with -I, -E or -S, or with
another PYTHONPATH, does not load this file (with -E, PYTHONNOUSERSITE is ignored as
well); another interpreter (a system python, a different venv), a non-Python tool (psql,
a shell, which also read ~/.pgpass and service files), native code, creating
psycopg2.extensions.connection directly, or a child that removes the import blocker
from sys.meta_path are not intercepted. The static checks in tests/test_safety_net.py
only allow [sys.executable, "-c", <constant script>] with env=safe_subprocess_env(...),
and they are heuristics too.
"""
import os
import sys

_DUMMY_DATABASE_URL = "postgresql://nobody:nothing@127.0.0.1:1/none"  # = db_safety.DUMMY_DATABASE_URL
_BLOCKED_DRIVERS = ("psycopg", "psycopg_pool", "asyncpg", "pg8000", "aiopg", "pgdb", "psqlpy", "postgresql")
_TESTS_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def _installed(path):
    # = db_safety.installed_package: in site-packages or dist-packages, not under tests/.
    if not path:
        return False
    real = os.path.realpath(path)
    if real == _TESTS_DIR or real.startswith(_TESTS_DIR + os.sep):
        return False
    parts = real.split(os.sep)
    return "site-packages" in parts or "dist-packages" in parts


def _die(reason):
    try:
        os.write(2, ("test subprocess guard: %s; the child is stopped\n" % reason).encode("utf-8", "replace"))
    finally:
        os._exit(97)


class _RefuseDrivers:
    """A meta-path finder that makes the blocked drivers (and psycopg2, when it was not
    installed when the child started) impossible to import."""

    def __init__(self, names):
        self.names = tuple(names)

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in self.names:
            raise ImportError("%s cannot be imported in test subprocesses (database drivers are blocked)" % fullname)
        return None


def _blocked(*args, **kwargs):
    raise _ERROR("database connections are blocked in test subprocesses")


_ERROR = RuntimeError

try:
    os.environ["DATABASE_URL"] = _DUMMY_DATABASE_URL
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    for _name in [name for name in os.environ if name.upper().startswith(("PG", "FT_TEST_"))]:
        del os.environ[_name]
    blocked = list(_BLOCKED_DRIVERS)
    try:
        import psycopg2
        import psycopg2._psycopg
    except ModuleNotFoundError as exc:
        if exc.name not in ("psycopg2", "psycopg2._psycopg"):
            raise
        psycopg2 = None
        blocked.append("psycopg2")  # not there now: it must not appear later unguarded
    if psycopg2 is not None:
        if not _installed(getattr(psycopg2, "__file__", None)) or not _installed(
                getattr(psycopg2._psycopg, "__file__", None)):
            _die("psycopg2 is not loaded from an installed package (a module of that name shadows it?)")
        if not hasattr(psycopg2, "_connect") or not hasattr(psycopg2._psycopg, "_connect"):
            _die("psycopg2 no longer has _connect (the guard must be updated)")
        _ERROR = psycopg2.OperationalError
        psycopg2._connect = _blocked
        psycopg2._psycopg._connect = _blocked
        if psycopg2._connect is not _blocked or psycopg2._psycopg._connect is not _blocked:
            _die("the connection guard did not take effect")
    for _name in blocked:
        if _name in sys.modules and _name != "psycopg2":
            _die("%s was imported before the guard" % _name)
    sys.meta_path.insert(0, _RefuseDrivers(blocked))
except BaseException as exc:  # noqa: B902  (anything at all: the child must not run unguarded)
    _die("the connection guard could not be installed (%s)" % type(exc).__name__)
