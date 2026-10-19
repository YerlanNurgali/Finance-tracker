"""The test suite's safety net: no test may reach any database by accident.

``.env`` holds the production DATABASE_URL and ``database.py`` loads it when it is
imported. So, before anything of the application is imported, this file

* switches python-dotenv off for the process (PYTHON_DOTENV_DISABLED=1), removes every
  PG* variable libpq could take a host from and sets DATABASE_URL to a dummy (a closed
  local port);
* imports tests/db_safety.py (and checks it is that file) and puts its guard on
  ``psycopg2._connect`` for the whole process, including collection, import time,
  fixtures and session teardown (see tests/db_safety.py for what this does and does
  not guarantee);
* checks that psycopg2, python-dotenv, database.py and api.py are the expected files,
  not modules of the same name placed earlier on sys.path.

This file is only loaded when pytest collects from tests/ (see README): a file outside
tests/ passed to pytest by path runs without any of this.

Every refused attempt is logged and reported exactly once: during collection it stops
the session, during a test or its fixtures it fails that test (even when the
application swallowed the error), between tests it fails the next test, at the end of
the session it makes the run fail, and at interpreter exit a refusal or tampering ends
the process with exit code 3 only if the session still reports success (pytest's own
non-zero code is kept otherwise). Only the body of a
test marked ``expect_blocked`` in tests/test_safety_net.py may announce refusals of its
own thread, with ``db_safety.expected_refusals(count)``; those go to a separate log.

The interception and db_safety's own functions are checked when this file is loaded,
after the application is imported, after collection, before each test's setup, right
before each test body, after each test and at the end. The check used is the one taken
at import, so replacing ``db_safety.integrity_problems`` is itself detected.

Before and after every test the process-wide state is put back as it was when this file
was loaded, before any test module: the whole environment (PYTEST_* aside), every global
of ``database`` and ``api`` (``database._pool`` and the PDF font cache aside) with the
top-level contents of their plain dicts, lists and sets, the app's dependency overrides,
exception handlers, middleware list, routes, ``app.state`` (object and contents), cached
OpenAPI schema and startup/shutdown hooks; the pool is closed. Deeper nested objects,
the built middleware stack, the rate limiter's counters and other modules are NOT
restored. If the restore itself raises, the session stops. If it ran but something could not
be put back (a gate found open, a pool that would not close, DATABASE_URL or the PG*
variables), that test fails and the session goes on; the next test repeats the reset.
"""
import atexit
import os
import sys

os.environ["PYTHON_DOTENV_DISABLED"] = "1"  # before anything can call load_dotenv()

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import db_safety  # noqa: E402  (lives next to this file)

if os.path.realpath(getattr(db_safety, "__file__", "") or "") != os.path.realpath(os.path.join(_HERE, "db_safety.py")):
    raise RuntimeError("the imported db_safety is not tests/db_safety.py: the test safety net cannot be confirmed")

# The safety net's functions as they are now; later replacements of module attributes
# are reported by _INTEGRITY and never used here.
_INTEGRITY = db_safety.integrity_problems
_SETTLE = db_safety._settle
_ALLOW_EXPECTED = db_safety._allow_expected
_PENDING = db_safety.pending_refusals
_REPORTED = db_safety.reported_refusals
_GATE_IS_OPEN = db_safety.gate_is_open
_ACTIVATE = db_safety.activate
_DEACTIVATE = db_safety.deactivate
_VERIFY_SANDBOX = db_safety.verify_sandbox
_SANDBOX_CONFIG = db_safety.sandbox_config
_SANDBOX_CONFIGURED = db_safety.sandbox_configured
_PG_VARIABLES = db_safety.pg_variables
_INSTALLED_PACKAGE = db_safety.installed_package
_RESTORE_ENVIRON = db_safety.restore_environ
_RESTORE_MODULE = db_safety.restore_module
_SAFETY_ERROR = db_safety.SafetyError
_DUMMY_DATABASE_URL = db_safety.DUMMY_DATABASE_URL

for _name in _PG_VARIABLES():
    del os.environ[_name]
os.environ["DATABASE_URL"] = _DUMMY_DATABASE_URL
os.environ["TESTING"] = "true"
db_safety.install_guard()
if _INTEGRITY(False):
    raise RuntimeError("the test safety net could not be set up: %s" % "; ".join(_INTEGRITY(False)))

import pytest  # noqa: E402

_SAFETY_NET_TESTS = os.path.realpath(os.path.join(_HERE, "test_safety_net.py"))
# Third-party packages without which api.py cannot be imported; tests that need the app
# skip on their own then (pytest.importorskip). Any other import error stops everything.
_OPTIONAL_APP_PACKAGES = {"fastapi", "starlette", "slowapi", "pydantic", "multipart", "python_multipart"}

# The application, imported once, before any test module, and its state as it is now.
import database as _database  # noqa: E402  (an import error here must stop the run)

try:
    import api as _api  # noqa: E402
except ModuleNotFoundError as _exc:
    if (_exc.name or "").split(".")[0] not in _OPTIONAL_APP_PACKAGES:
        raise
    _api = None


def _same_file(module, expected):
    return os.path.realpath(getattr(module, "__file__", "") or "") == os.path.realpath(expected)


# A module of a trusted name placed earlier on sys.path (tests/database.py, a psycopg2.py
# next to the tests, ...) would be imported instead of the real one: refuse to run then.
if not _same_file(_database, os.path.join(_ROOT, "database.py")) or (
        _api is not None and not _same_file(_api, os.path.join(_ROOT, "api.py"))):
    raise RuntimeError("database or api was not imported from the project root: the safety net cannot be confirmed")
for _module_name in ("psycopg2", "dotenv"):
    if _module_name in sys.modules and not _INSTALLED_PACKAGE(getattr(sys.modules[_module_name], "__file__", None)):
        raise RuntimeError("%s was not imported from an installed package: the safety net cannot be confirmed"
                           % _module_name)
if _INTEGRITY(False):
    raise RuntimeError("importing the application changed the test safety net: %s" % "; ".join(_INTEGRITY(False)))

_DATABASE_RUNTIME = ("_pool",)  # rebound by database.py itself; closed after every test
_API_RUNTIME = ("_pdf_fonts",)  # a font cache filled by api.py itself
_ENVIRONMENT = db_safety.snapshot_environ()
_DATABASE_STATE = db_safety.snapshot_module(_database, _DATABASE_RUNTIME)
_API_STATE = db_safety.snapshot_module(_api, _API_RUNTIME) if _api is not None else None


def _app_containers(app):
    """The app's own mutable containers tests may change in place or rebind:
    (owner or None, attribute, original object, copy of its contents). app.state's dict is
    only refilled, never rebound (Starlette's State.__setattr__ writes into that dict)."""
    found = [(app, "dependency_overrides", getattr(app, "dependency_overrides", None)),
             (app, "exception_handlers", getattr(app, "exception_handlers", None)),
             (app, "user_middleware", getattr(app, "user_middleware", None)),
             (app.router, "routes", getattr(app.router, "routes", None)),
             (app.router, "on_startup", getattr(app.router, "on_startup", None)),
             (app.router, "on_shutdown", getattr(app.router, "on_shutdown", None)),
             (None, "state", getattr(getattr(app, "state", None), "_state", None))]
    return [(owner, name, value, value.copy()) for owner, name, value in found if isinstance(value, (dict, list))]


def _app_attributes(app):
    """Plain attributes of the app put back by identity: the State object itself and the
    cached OpenAPI schema (FastAPI rebuilds it when it is None). Not app.middleware_stack:
    whether resetting it is safe depends on Starlette's lifecycle (see README)."""
    return [(app, name, getattr(app, name)) for name in ("state", "openapi_schema") if hasattr(app, name)]


_APP_CONTAINERS = _app_containers(_api.app) if _api is not None else []
_APP_ATTRIBUTES = _app_attributes(_api.app) if _api is not None else []
_SESSION = {}


def _stop_if_tampered(when, during_test):
    problems = _INTEGRITY(during_test)
    if problems:
        pytest.exit("the test safety net was tampered with %s: %s" % (when, "; ".join(problems)),
                    returncode=pytest.ExitCode.INTERNAL_ERROR)


def _reset_state():
    """Puts the process-wide state back to the snapshots. Returns what had to be repaired
    that should never need repairing (an open gate, a pool that would not close)."""
    problems = []
    if _GATE_IS_OPEN():
        _DEACTIVATE()
        problems.append("the sandbox gate was left open")
    _RESTORE_ENVIRON(_ENVIRONMENT)
    for name in _PG_VARIABLES():
        del os.environ[name]
    _RESTORE_MODULE(_database, _DATABASE_STATE, _DATABASE_RUNTIME)
    _database.close_pool()  # closing never connects; the next test starts without a pool
    if _database._pool is not None:
        problems.append("the connection pool could not be closed")
    if _api is not None:
        _RESTORE_MODULE(_api, _API_STATE, _API_RUNTIME)
        for owner, name, original, saved in _APP_CONTAINERS:
            if type(original) is list:
                original[:] = saved
            else:
                original.clear()
                original.update(saved)
            if owner is not None:
                setattr(owner, name, original)
        for owner, name, original in _APP_ATTRIBUTES:
            if getattr(owner, name) is not original:
                setattr(owner, name, original)
    if _GATE_IS_OPEN() or os.environ.get("DATABASE_URL") != _DUMMY_DATABASE_URL or _PG_VARIABLES():
        problems.append("the gate, DATABASE_URL or the PG* variables could not be reset")
    return problems


def _reset_or_stop(when):
    try:
        return _reset_state()
    except BaseException as exc:  # the state is unknown: nothing may run on top of it
        pytest.exit("restoring the process state %s failed (%s): the session is stopped"
                    % (when, type(exc).__name__), returncode=pytest.ExitCode.INTERNAL_ERROR)


def _describe(refused):
    return "; ".join(sorted(set(refused)))


def _report(lines, session):
    reporter = session.config.pluginmanager.get_plugin("terminalreporter") if session is not None else None
    for line in lines:
        if reporter is not None:
            reporter.write_line("SAFETY NET: " + line, red=True, bold=True)
        else:
            sys.stderr.write("SAFETY NET: %s\n" % line)
            sys.stderr.flush()


def _fail_session(session, problems):
    if session is not None and session.exitstatus in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED):
        session.exitstatus = pytest.ExitCode.INTERNAL_ERROR if problems else pytest.ExitCode.TESTS_FAILED


def _at_exit():
    # A last check after every pytest hook. Normally pytest_sessionfinish and
    # pytest_unconfigure have already made the session fail when something was refused
    # or tampered with, and then this does nothing more than print new refusals: the
    # original non-zero exit code is kept and no other exit handler is skipped.
    # Only when the session still reports success (a hook or plugin set it back, or a
    # refusal came after the last hook) does it end the process with code 3; os._exit then
    # skips the exit handlers registered before this file (coverage data written at exit,
    # for instance). It cannot know a later os._exit or the parent of an in-process
    # pytest.main(); see README.
    session = _SESSION.get("session")
    try:
        problems = _INTEGRITY(False)
        late = _PENDING()
        refused = late + _REPORTED()
    except BaseException as exc:  # the check itself broke: that is a failure too
        problems, late, refused = ["the final check failed (%s)" % type(exc).__name__], [], []
    if not problems and not refused:
        return
    status = getattr(session, "exitstatus", None)
    succeeded = status is None or status in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED)
    if not succeeded and not late:
        return  # already reported, and the run already fails with pytest's own code
    try:
        sys.stdout.flush()
        sys.stderr.write("SAFETY NET: %d refused connection(s), %d after the session; %s\n"
                         % (len(refused), len(late), "; ".join(problems) or "no tampering found"))
        sys.stderr.flush()
    finally:
        if succeeded:
            os._exit(3)


atexit.register(_at_exit)


def pytest_sessionstart(session):
    _SESSION["session"] = session


def pytest_collection_finish(session):
    for name in _PG_VARIABLES():
        del os.environ[name]
    _stop_if_tampered("during collection", False)
    refused = _SETTLE()
    if refused:
        pytest.exit("a database connection was refused while the tests were collected (%s)" % _describe(refused),
                    returncode=pytest.ExitCode.TESTS_FAILED)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item):
    # After every fixture is set up, right before the test body. psycopg2.connect may be
    # monkeypatched by a fixture here; everything else must be intact.
    _stop_if_tampered("before the body of " + item.nodeid, True)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    # trylast: after pytest's own implementation has torn down the remaining fixtures.
    problems = _INTEGRITY(False)
    _SETTLE()
    refused = _REPORTED()
    if not problems and not refused:
        return
    lines = []
    if problems:
        lines.append("test safety net tampered with: %s" % "; ".join(problems))
    if refused:
        lines.append("%d database connection(s) were refused in this session (%s)" % (len(refused), _describe(refused)))
    _report(lines, session)
    _fail_session(session, problems)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    # pytest returns session.exitstatus after this hook (_pytest/main.py); _at_exit still follows.
    session = _SESSION.get("session")
    problems = _INTEGRITY(False)
    late = _SETTLE()
    if problems or late:
        lines = []
        if problems:
            lines.append("test safety net tampered with at the end: %s" % "; ".join(problems))
        if late:
            lines.append("%d database connection(s) were refused after the session summary (%s)"
                         % (len(late), _describe(late)))
        _report(lines, None)
        _fail_session(session, problems)


def pytest_report_header(config):
    if _SANDBOX_CONFIGURED():
        return "PostgreSQL sandbox: configured, verified when the first PostgreSQL test runs"
    return "PostgreSQL sandbox: not configured (PostgreSQL tests are skipped)"


@pytest.fixture(autouse=True)
def _isolated_state(request):
    """Per-test isolation, and the check that no database connection was refused."""
    _stop_if_tampered("before " + request.node.nodeid, False)
    earlier = _SETTLE()
    expects = request.node.get_closest_marker("expect_blocked") is not None
    in_safety_net = os.path.realpath(str(request.node.path)) == _SAFETY_NET_TESTS
    repaired = _reset_or_stop("before " + request.node.nodeid)
    if earlier:
        pytest.fail("a database connection was refused outside any test, before this one (%s)" % _describe(earlier),
                    pytrace=False)
    if repaired:
        pytest.fail("; ".join(repaired), pytrace=False)
    if expects and not in_safety_net:
        pytest.fail("expect_blocked is only allowed in tests/test_safety_net.py", pytrace=False)
    # Bound to this test's node id; db_safety also requires its call phase.
    _ALLOW_EXPECTED(request.node.nodeid if expects else None)
    try:
        yield
    finally:
        _ALLOW_EXPECTED(None)
    # The integrity check runs before the reset too: the reset closes an open gate and puts
    # the application back, and would otherwise hide a hand-made gate from the check after it.
    # By now the function-scoped fixtures of the test (monkeypatch among them) are undone,
    # so psycopg2.connect is expected to be the original, exactly as for the check below.
    _stop_if_tampered("by " + request.node.nodeid + " (before the reset)", False)
    repaired = _reset_or_stop("after " + request.node.nodeid)
    _stop_if_tampered("by " + request.node.nodeid, False)
    refused = _SETTLE()
    if refused:
        pytest.fail("a database connection was refused during this test (%s)" % _describe(refused), pytrace=False)
    if repaired:
        pytest.fail("; ".join(repaired), pytrace=False)


@pytest.fixture(scope="session")
def pg_sandbox():
    """The verified PostgreSQL sandbox configuration, or a skip when none is configured.
    A configured but unsafe sandbox is an error, never a skip, and nothing is changed."""
    if not _SANDBOX_CONFIGURED():
        pytest.skip("FT_TEST_POSTGRES_URL is not set (see README: PostgreSQL sandbox)")
    pytest.importorskip("psycopg2")
    _stop_if_tampered("before the sandbox was verified", False)
    try:
        config = _SANDBOX_CONFIG()
        _VERIFY_SANDBOX(config)
    except _SAFETY_ERROR as exc:
        # A refusal by the guard during the verification is logged too. Report it here, with the
        # reason, so it does not come up later as "refused outside any test" in an unrelated
        # test: it is moved to the reported log (still counted, and still fails the session).
        # This takes every pending refusal, not only this verification's, and does not depend
        # on the order in which pytest sets fixtures up.
        logged = _SETTLE()
        pytest.fail("the PostgreSQL sandbox was refused: %s%s" % (
            exc, " [refusals logged so far: %s]" % _describe(logged) if logged else ""), pytrace=False)
    return config


@pytest.fixture
def pg_gate(pg_sandbox):
    """Opens the connection gate for this test only; afterwards closes the application's
    pool (whose connections point at the sandbox) and then the gate, whatever happens."""
    _stop_if_tampered("before the gate was opened", False)
    _ACTIVATE(pg_sandbox)
    try:
        yield pg_sandbox
    finally:
        try:
            _database.close_pool()
        finally:
            _DEACTIVATE()
    if _GATE_IS_OPEN() or _database._pool is not None:
        pytest.exit("the sandbox gate or pool could not be closed", returncode=pytest.ExitCode.INTERNAL_ERROR)


@pytest.fixture
def pg_database_url(pg_gate, monkeypatch):
    """Points the application's own DATABASE_URL (and so its connection pool) at the sandbox."""
    monkeypatch.setenv("DATABASE_URL", pg_gate.dsn_write)
    return pg_gate.dsn_write
