"""Tests of the test safety net itself (tests/db_safety.py and tests/conftest.py).

No database is used, and none is needed: every refusal happens in Python before libpq
runs. Tests that need the driver skip on their own; the rest of the suite does not.

This is the only file where ``expect_blocked`` and ``db_safety.expected_refusals`` may be
used, and it is not itself scanned by the static checks below (it quotes every pattern
they look for); it, conftest.py, db_safety.py, pg_sandbox.py and the subprocess
sitecustomize.py are reviewed by hand.
"""
import ast
import builtins
import os
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

import db_safety

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
psycopg2 = db_safety.psycopg2
needs_driver = pytest.mark.skipif(psycopg2 is None, reason="psycopg2 is not installed")

GATE = db_safety.Gate("127.0.0.1", "55432", "ft_test_sandbox", "ft_test_runner",
                      (db_safety.WRITE_SETTINGS, db_safety.READ_SETTINGS))
TOKEN = "t" * 40
SANDBOX_URL = "postgresql://ft_test_runner:pw@127.0.0.1:55432/ft_test_sandbox"
# Where a refused attempt "would" go: a socket directory that does not exist, so even a
# broken guard could not reach any server, local or remote.
NOWHERE = "host=/nonexistent/ft_test_safety_net port=1 dbname=x user=y"


def dsn(host="127.0.0.1", port="55432", dbname="ft_test_sandbox", user="ft_test_runner",
        options="-c search_path=ft_test", extra=""):
    parts = ["host=%s" % host, "port=%s" % port, "dbname=%s" % dbname, "user=%s" % user]
    if options is not None:
        parts.append("options='%s'" % options)
    return " ".join(parts + ([extra] if extra else []))


def sandbox_environment(**changes):
    values = {db_safety.SANDBOX_URL_VARIABLE: SANDBOX_URL, db_safety.SANDBOX_TOKEN_VARIABLE: TOKEN}
    values.update(changes)
    return {key: value for key, value in values.items() if value is not None}


# --- The connection gate ------------------------------------------------------------------

@needs_driver
@pytest.mark.parametrize("candidate", [
    dsn(),
    dsn(extra="connect_timeout=10 keepalives=1 keepalives_idle=30"),
    dsn(options="-csearch_path=ft_test"),
    dsn(options="-c default_transaction_read_only=on -c search_path=ft_test"),
    "postgresql://ft_test_runner:pw@127.0.0.1:55432/ft_test_sandbox?options=-c%20search_path%3Dft_test",
])
def test_the_exact_sandbox_is_allowed(candidate):
    db_safety.check_connect(candidate, GATE, environ={})


@needs_driver
@pytest.mark.parametrize("candidate", [
    dsn(host="localhost"),
    dsn(host="LOCALHOST"),
    dsn(host="127.0.0.1."),
    dsn(host="127.0.0.1,example.invalid"),
    dsn(host="example.invalid"),
    dsn(host="EP-COOL-123.EU-CENTRAL-1.AWS.NEON.TECH"),
    dsn(host="::1"),
    dsn(host="/var/run/postgresql"),
    dsn(port="5432"),
    dsn(dbname="neondb"),
    dsn(dbname="ft_test_other"),
    dsn(dbname="postgresql://evil@example.invalid/db"),
    dsn(user="postgres"),
    dsn(options=None),
    dsn(options="-c search_path=public"),
    dsn(options="-c search_path=ft_test -c statement_timeout=0"),
    dsn(options="--search_path=ft_test"),
    dsn(extra="hostaddr=203.0.113.1"),
    dsn(extra="hostaddr=127.0.0.1"),
    dsn(extra="service=production"),
    dsn(extra="replication=database"),
    dsn(extra="sslmode=require"),
    dsn(extra="passfile=/tmp/elsewhere"),
    dsn(extra="target_session_attrs=any"),
    dsn(extra="sslrootcert=/tmp/ca.pem"),
    dsn(extra="keepalives=yes"),
    "this is not a dsn",
    "postgresql://nobody:nothing@127.0.0.1:1/none",
    "",
    "   ",
])
def test_anything_else_is_refused(candidate):
    with pytest.raises(db_safety.SafetyError):
        db_safety.check_connect(candidate, GATE, environ={})


@pytest.mark.parametrize("candidate", [None, 5, b"host=127.0.0.1", ["host=127.0.0.1"]])
def test_a_connection_without_a_text_dsn_is_refused(candidate):
    with pytest.raises(db_safety.SafetyError):
        db_safety.check_connect(candidate, GATE, environ={})


def test_with_the_gate_closed_nothing_is_allowed():
    with pytest.raises(db_safety.SafetyError):
        db_safety.check_connect(dsn(), None, environ={})
    with pytest.raises(db_safety.SafetyError):
        db_safety.check_connect("postgresql://nobody:nothing@127.0.0.1:1/none", None, environ={})


@pytest.mark.parametrize("name", db_safety.PG_ENVIRONMENT + ("PGSSLMODE", "PGAPPNAME", "pgHost"))
def test_an_ambient_pg_variable_refuses_even_the_sandbox(name):
    with pytest.raises(db_safety.SafetyError):
        db_safety.check_connect(dsn(), GATE, environ={name: "anything"})


@pytest.mark.parametrize("gate", [
    db_safety.Gate("db.example.com", "5432", "ft_test_sandbox", "ft_test_runner", (db_safety.WRITE_SETTINGS,)),
    db_safety.Gate("ep-cool-123.eu-central-1.aws.neon.tech", "5432", "neondb", "owner", (db_safety.WRITE_SETTINGS,)),
    db_safety.Gate("localhost", "55432", "ft_test_sandbox", "ft_test_runner", (db_safety.WRITE_SETTINGS,)),
    db_safety.Gate("127.0.0.1", "port", "ft_test_sandbox", "ft_test_runner", (db_safety.WRITE_SETTINGS,)),
    db_safety.Gate("127.0.0.1", "70000", "ft_test_sandbox", "ft_test_runner", (db_safety.WRITE_SETTINGS,)),
    db_safety.Gate("127.0.0.1", "55432", "neondb", "ft_test_runner", (db_safety.WRITE_SETTINGS,)),
    db_safety.Gate("127.0.0.1", "55432", "ft_test_sandbox", "postgres", (db_safety.WRITE_SETTINGS,)),
    db_safety.Gate("127.0.0.1", "55432", "ft_test_sandbox", "ft_test_runner", (frozenset(["search_path=public"]),)),
    db_safety.Gate("127.0.0.1", "55432", "ft_test_sandbox", "ft_test_runner", ()),
    ("127.0.0.1", "55432", "ft_test_sandbox", "ft_test_runner", (db_safety.WRITE_SETTINGS,)),
    types.SimpleNamespace(host="127.0.0.1", port="55432", dbname="ft_test_sandbox", user="ft_test_runner",
                          settings=(db_safety.WRITE_SETTINGS,)),
])
def test_a_gate_for_anything_but_a_local_sandbox_is_refused(gate):
    with pytest.raises(db_safety.SafetyError):
        db_safety.check_gate(gate)
    if isinstance(gate, db_safety.Gate):
        with pytest.raises(db_safety.SafetyError):
            db_safety.check_connect(dsn(host=gate.host, port=gate.port, dbname=gate.dbname, user=gate.user),
                                    gate, environ={})
    assert not db_safety.gate_is_open()


def test_open_gate_is_refused_under_pytest_even_for_a_valid_gate():
    # open_gate is for tests/pg_sandbox.py only; under pytest it refuses before looking at the gate.
    with pytest.raises(db_safety.SafetyError, match="pg_sandbox"):
        db_safety.open_gate(GATE)
    assert not db_safety.gate_is_open()


def test_a_subclass_of_gate_is_refused():
    class LooseGate(db_safety.Gate):
        pass

    with pytest.raises(db_safety.SafetyError):
        db_safety.check_gate(LooseGate(*GATE))


def test_option_settings():
    assert db_safety.option_settings(None) == frozenset()
    assert db_safety.option_settings("-c search_path=ft_test") == frozenset(["search_path=ft_test"])
    assert db_safety.option_settings("-csearch_path=ft_test -c a=b") == frozenset(["search_path=ft_test", "a=b"])
    for garbage in ("search_path=ft_test", "-c", "-c x", "--x=y", "-c a=b; DROP TABLE users"):
        with pytest.raises(db_safety.SafetyError):
            db_safety.option_settings(garbage)


def test_the_gate_opens_only_for_a_verified_sandbox():
    config = db_safety.sandbox_config(sandbox_environment())
    with pytest.raises(db_safety.SafetyError):
        db_safety.activate(config)
    assert not db_safety.gate_is_open()


@needs_driver
def test_only_psycopg2s_own_connection_classes_may_be_a_connection_factory():
    import psycopg2.extras

    base = psycopg2.extensions.connection

    def plain(factory):
        return db_safety._plain_connection_factory(factory, base)

    assert plain(None) and plain(base) and plain(psycopg2.extras.DictConnection)

    class Redirecting(base):
        def __init__(self, dsn, *args, **kwargs):
            super().__init__("host=elsewhere.example.com", *args, **kwargs)

    class Building(type):  # a metaclass whose __call__ could build any connection it likes
        def __call__(cls, *args, **kwargs):
            raise AssertionError("never called here")

    assert not plain(Redirecting)
    assert not plain(Building("Built", (base,), {}))
    assert not plain(lambda dsn, **kwargs: None)
    assert not plain(psycopg2.extras.LogicalReplicationConnection)


@needs_driver
def test_the_dsn_handed_to_libpq_pins_the_address():
    final = db_safety._harden_dsn(dsn(), GATE)
    params = db_safety.parse_dsn(final)
    assert params["hostaddr"] == "127.0.0.1" and params["host"] == "127.0.0.1"
    assert {key: value for key, value in params.items() if key != "hostaddr"} == db_safety.parse_dsn(dsn())


# Made-up passwords. libpq's keyword/value format ends an unquoted value at a space, and a
# C isspace() on a single byte can take a byte of a non-ASCII letter for one (the UTF-8 form
# of "Р" ends in 0xA0); the DSN handed to libpq quotes every value so that cannot happen.
FICTIONAL_PASSWORDS = [
    "ПАРОЛЬ", "пароль", "Рр", "ÀÐÑÞß-é-ü", "日本語パスワード", "back\\slash", "it's", "both\\'", "'", "\\", "end\\",
    "a=b", 'dq"x', "p+q", "p@q:r/s", "100%", "a b",
]


def _percent(text):
    return "".join("%%%02X" % byte for byte in text.encode("utf-8"))


def _url(password="pw", host="127.0.0.1", port="55432", dbname="ft_test_sandbox", user="ft_test_runner"):
    return ("postgresql://%s:%s@%s:%s/%s?options=-c%%20search_path%%3Dft_test"
            % (user, _percent(password), host, port, dbname))


def test_a_value_is_always_quoted_and_escaped_for_libpq():
    assert db_safety._quoted("") == "''"
    assert db_safety._quoted("plain") == "'plain'"
    assert db_safety._quoted("a b") == "'a b'"
    assert db_safety._quoted("a'b\\c") == "'a\\'b\\\\c'"
    assert db_safety._quoted("ПАРОЛЬ") == "'ПАРОЛЬ'"


@needs_driver
@pytest.mark.parametrize("password", FICTIONAL_PASSWORDS)
def test_the_hardened_dsn_keeps_any_password_and_every_other_parameter(password):
    candidate = _url(password)
    before = db_safety.parse_dsn(candidate)
    assert before["password"] == password, "the fixture itself must survive libpq's URL parser"
    db_safety.check_connect(candidate, GATE, environ={})
    final = db_safety._harden_dsn(candidate, GATE)
    assert re.fullmatch(r"(?:\w+='(?:[^'\\]|\\.)*'(?: |$))+", final), "every value is quoted"
    after = db_safety.parse_dsn(final)
    assert after.pop("hostaddr") == GATE.host
    assert after == before, "nothing but hostaddr was added, changed or lost"
    assert after["password"] == password


@needs_driver
def test_the_hardened_dsn_also_handles_a_keyword_dsn_with_a_quoted_value():
    candidate = dsn(extra="password='a b\\'c\\\\d' connect_timeout=10")
    before = db_safety.parse_dsn(candidate)
    final = db_safety._harden_dsn(candidate, GATE)
    after = db_safety.parse_dsn(final)
    assert after.pop("hostaddr") == GATE.host and after == before and after["password"] == "a b'c\\d"


@needs_driver
@pytest.mark.parametrize("hostaddr", ["203.0.113.1", "127.0.0.1", "::1"])
def test_a_dsn_that_names_a_hostaddr_is_refused_and_never_rewritten(hostaddr):
    candidate = dsn(extra="hostaddr=%s" % hostaddr)
    with pytest.raises(db_safety.SafetyError):
        db_safety.check_connect(candidate, GATE, environ={})
    with pytest.raises(db_safety.SafetyError):
        db_safety._harden_dsn(candidate, GATE)


@needs_driver
@pytest.mark.parametrize("changes", [
    {"host": "localhost"}, {"host": "example.invalid"}, {"host": "10.0.0.5"}, {"port": "5432"},
    {"dbname": "neondb"}, {"dbname": "ft_test_other"}, {"user": "postgres"}, {"user": "neondb_owner"},
])
def test_a_non_ascii_password_does_not_relax_the_checks_on_host_port_database_and_role(changes):
    with pytest.raises(db_safety.SafetyError):
        db_safety.check_connect(_url("ПАРОЛЬ", **changes), GATE, environ={})
    db_safety.check_connect(_url("ПАРОЛЬ"), GATE, environ={})


@pytest.mark.parametrize("path,installed", [
    ("/usr/lib/python3.9/site-packages/psycopg2/__init__.py", True),
    ("/usr/lib/python3/dist-packages/psycopg2/__init__.py", True),
    (str(TESTS / "psycopg2.py"), False),
    (str(TESTS / "site-packages" / "psycopg2" / "__init__.py"), False),
    ("/somewhere/psycopg2/__init__.py", False),
    ("", False),
    (None, False),
])
def test_only_an_installed_package_counts_as_the_driver(path, installed):
    assert db_safety.installed_package(path) is installed


# --- The guard on psycopg2 and the log of refusals --------------------------------------------

@needs_driver
@pytest.mark.expect_blocked
def test_every_way_of_connecting_is_refused_outside_a_sandbox_test():
    import psycopg2.pool

    assert db_safety.guard_is_installed(), "the guard must be in place before anything tries to connect"
    assert not db_safety.gate_is_open()
    refused = db_safety.BlockedConnection
    before = len(db_safety.expected_refusal_log())
    with db_safety.expected_refusals(6):  # every refusal is logged, so a swallowed one still fails its test
        with pytest.raises(refused):
            psycopg2.connect(NOWHERE)
        with pytest.raises(refused):
            psycopg2.connect(host="/nonexistent/ft_test_safety_net", dbname="x")  # host as a keyword
        with pytest.raises(refused):
            psycopg2.connect(dbname="x", user="y", hostaddr="127.0.0.1", port="1")
        with pytest.raises(refused):
            psycopg2.connect()
        with pytest.raises(refused):
            psycopg2.pool.ThreadedConnectionPool(1, 1, NOWHERE)  # a pool
        with pytest.raises(refused):
            psycopg2.connect(NOWHERE, connection_factory=lambda dsn, **kwargs: None)
    assert db_safety.pending_refusals() == []
    assert len(db_safety.expected_refusal_log()) == before + 6, "expected refusals are kept, apart"


@needs_driver
@pytest.mark.expect_blocked
def test_a_refusal_never_contains_the_dsn():
    with db_safety.expected_refusals(1):
        with pytest.raises(db_safety.BlockedConnection) as raised:
            psycopg2.connect("host=/nonexistent/db.example.com port=5432 dbname=prod user=owner password=s3cr3t-password")
        text = str(raised.value) + " ".join(db_safety.pending_refusals())
    assert "s3cr3t" not in text and "db.example.com" not in text and "owner" not in text


@needs_driver
@pytest.mark.expect_blocked
def test_an_expectation_takes_only_its_own_refusals_off_the_pending_log():
    with db_safety.expected_refusals(1):  # the outer block takes the refusal the inner one had to leave
        with pytest.raises(AssertionError, match="expected 2"):
            with db_safety.expected_refusals(2):
                with pytest.raises(db_safety.BlockedConnection):
                    psycopg2.connect(NOWHERE)
        assert len(db_safety.pending_refusals()) == 1, "a wrong count leaves the log as it is"
    with db_safety.expected_refusals(1):
        with pytest.raises(RuntimeError):
            with db_safety.expected_refusals(1):
                with pytest.raises(db_safety.BlockedConnection):
                    psycopg2.connect(NOWHERE)
                raise RuntimeError("the block failed")
        assert len(db_safety.pending_refusals()) == 1, "a failed block leaves the log as it is"
    assert db_safety.pending_refusals() == []
    assert db_safety.integrity_problems(False) == [], "every refusal is accounted for"


def test_without_the_marker_refusals_cannot_be_expected():
    with pytest.raises(AssertionError, match="expect_blocked"):
        with db_safety.expected_refusals(0):
            pass


@needs_driver
def test_the_guard_wraps_the_function_connect_calls():
    assert hasattr(psycopg2, "_connect")
    assert db_safety.guard_is_installed()
    assert db_safety.integrity_problems(False) == []


def test_the_integrity_check_has_no_default_mode():
    # A default could be flipped to skip a check; every caller has to say which one it wants.
    assert db_safety.integrity_problems.__defaults__ is None and db_safety.integrity_problems.__kwdefaults__ is None
    with pytest.raises(TypeError):
        db_safety.integrity_problems()


@needs_driver
@pytest.mark.expect_blocked
def test_the_guard_refuses_arguments_psycopg2_connect_never_passes():
    with db_safety.expected_refusals(4):
        with pytest.raises(db_safety.BlockedConnection):
            psycopg2._connect(NOWHERE, None)  # a positional second argument (a factory, for instance)
        with pytest.raises(db_safety.BlockedConnection):
            psycopg2._connect(NOWHERE, unexpected=1)
        with pytest.raises(db_safety.BlockedConnection):
            psycopg2._connect()
        with pytest.raises(db_safety.BlockedConnection):
            psycopg2._connect(NOWHERE, dsn=NOWHERE)
    assert all("unexpected arguments" in reason for reason in db_safety.expected_refusal_log()[-4:])


@needs_driver
@pytest.mark.expect_blocked
def test_a_gate_set_by_hand_is_not_trusted():
    # The gate below has the right form but was not opened by verify_sandbox/activate:
    # the guard refuses and integrity_problems reports it. Port 1 is closed, so even a
    # broken guard would not reach a server.
    box = next(cell.cell_contents for cell in db_safety.gate_is_open.__closure__ if isinstance(cell.cell_contents, dict))
    forged = db_safety.Gate("127.0.0.1", "1", "ft_test_forged", "ft_test_forged", (db_safety.WRITE_SETTINGS,))
    try:
        box["gate"] = forged
        assert any("does not belong to a verified sandbox" in problem for problem in db_safety.integrity_problems(False))
        with db_safety.expected_refusals(1):
            with pytest.raises(db_safety.BlockedConnection):
                psycopg2.connect(dsn(port="1", dbname="ft_test_forged", user="ft_test_forged"))
    finally:
        box["gate"] = None
    assert db_safety.integrity_problems(False) == []


@needs_driver
@pytest.mark.expect_blocked
def test_tampering_with_the_interception_is_detected():
    # Every change is undone in a finally block before the test ends; conftest checks
    # the interception again right after this test. `check` is the function conftest
    # also holds: replacing the module attribute does not replace it.
    check = db_safety.integrity_problems

    guard = psycopg2._connect
    try:
        psycopg2._connect = lambda *args, **kwargs: None
        assert any("_connect" in problem for problem in check(False))
    finally:
        psycopg2._connect = guard

    connect = psycopg2.connect
    try:
        psycopg2.connect = lambda *args, **kwargs: None
        assert any("psycopg2.connect" in problem for problem in check(False))
        assert not any("psycopg2.connect" in problem for problem in check(True))
    finally:
        psycopg2.connect = connect

    original_check = db_safety.check_connect
    try:
        db_safety.check_connect = lambda dsn, gate, environ=None: None  # would allow anything
        assert any("check_connect" in problem for problem in check(False))
        with db_safety.expected_refusals(1):  # the guard checks integrity first, and uses its own check
            with pytest.raises(db_safety.BlockedConnection):
                psycopg2.connect(NOWHERE)
    finally:
        db_safety.check_connect = original_check

    try:
        db_safety.integrity_problems = lambda during_test: []
        assert any("integrity check" in problem for problem in check(False))
    finally:
        db_safety.integrity_problems = check

    code = db_safety.option_settings.__code__
    try:
        db_safety.option_settings.__code__ = (lambda options: frozenset()).__code__
        assert any("option_settings" in problem for problem in check(False))
    finally:
        db_safety.option_settings.__code__ = code

    defaults = original_check.__defaults__
    try:
        original_check.__defaults__ = ({},)  # would check an empty environment instead of os.environ
        assert any("check_connect" in problem for problem in check(False))
    finally:
        original_check.__defaults__ = defaults

    cell = next(cell for cell in guard.__closure__ if cell.cell_contents is original_check)
    try:
        cell.cell_contents = lambda dsn, gate, environ=None: None  # inside the guard itself
        assert any("intact guard" in problem for problem in check(False))
    finally:
        cell.cell_contents = original_check

    cell = next(cell for cell in db_safety.pending_refusals.__closure__ if isinstance(cell.cell_contents, list))
    pending = cell.cell_contents
    with db_safety.expected_refusals(1):
        with pytest.raises(db_safety.BlockedConnection):
            psycopg2.connect(NOWHERE)
        saved = list(pending)
        try:
            del pending[:]
            assert any("removed from the log" in problem for problem in check(False))
        finally:
            pending.extend(saved)
    try:
        cell.cell_contents = []
        assert any("pending_refusals" in problem for problem in check(False))
    finally:
        cell.cell_contents = pending

    module = sys.modules["psycopg2"]
    try:
        sys.modules["psycopg2"] = types.ModuleType("psycopg2")
        assert any("sys.modules" in problem for problem in check(False))
    finally:
        sys.modules["psycopg2"] = module
    assert check(False) == []


@needs_driver
@pytest.mark.expect_blocked
def test_a_new_module_level_name_is_detected_and_does_not_shadow_the_guard():
    check = db_safety.integrity_problems
    # The expectation is entered before, and left after, the shadowing name exists: expected_refusals
    # itself uses len().
    with db_safety.expected_refusals(1):
        try:
            db_safety.len = lambda *args: 1  # would shadow the builtin inside every function of the module
            assert any("new or replaced module-level names" in problem and "len" in problem
                       for problem in check(False))
            with pytest.raises(db_safety.BlockedConnection):
                psycopg2.connect(NOWHERE)  # the guard refuses: its integrity check comes first
        finally:
            del db_safety.len
    assert check(False) == []


@needs_driver
@pytest.mark.expect_blocked
def test_a_replaced_builtin_that_db_safety_relies_on_is_detected():
    # `sorted` is used by several db_safety functions (check_server, pg_variables...), so it is among
    # the builtins whose identity the integrity check keeps. The replacement behaves exactly like
    # the original, and is put back in `finally`; expected_refusals is entered and left outside
    # the window in which it exists.
    check = db_safety.integrity_problems
    original = builtins.sorted
    with db_safety.expected_refusals(1):
        try:
            builtins.sorted = lambda *args, **kwargs: original(*args, **kwargs)
            assert any("builtins that db_safety relies on were replaced" in problem and "sorted" in problem
                       for problem in check(False))
            with pytest.raises(db_safety.BlockedConnection):
                psycopg2.connect(NOWHERE)  # refused: the integrity check comes first
        finally:
            builtins.sorted = original
    assert check(False) == []


def test_a_builtin_that_db_safety_does_not_use_may_be_patched():
    # `input` is not referred to by db_safety: patching it (mock.patch('builtins.input') is used in
    # tests) is not reported.
    original = builtins.input
    try:
        builtins.input = lambda prompt="": "1"
        assert db_safety.integrity_problems(False) == []
    finally:
        builtins.input = original


def test_a_new_dunder_name_counts_only_when_it_shadows_a_builtin():
    check = db_safety.integrity_problems
    try:
        db_safety.__ft_probe__ = 1  # not a builtin's name: nothing to shadow
        assert check(False) == []
        db_safety.__import__ = lambda *args, **kwargs: None  # would shadow the builtin __import__
        assert any("new or replaced module-level names" in problem and "__import__" in problem
                   for problem in check(False))
    finally:
        for name in ("__import__", "__ft_probe__"):
            vars(db_safety).pop(name, None)
    assert check(False) == []


@needs_driver
@pytest.mark.expect_blocked
def test_the_extension_modules_own_connect_function_is_guarded_too():
    c_module = sys.modules["psycopg2._psycopg"]
    assert c_module._connect is psycopg2._connect, "one guard on both names"
    with db_safety.expected_refusals(1):
        with pytest.raises(db_safety.BlockedConnection):
            c_module._connect(NOWHERE)  # what a replaced psycopg2.connect could call
    original = c_module._connect
    try:
        c_module._connect = lambda *args, **kwargs: None
        assert any("_psycopg._connect" in problem for problem in db_safety.integrity_problems(False))
    finally:
        c_module._connect = original
    assert db_safety.integrity_problems(False) == []


def test_restoring_a_snapshot_of_the_real_application_modules_undoes_changes_in_one_test():
    # The primitives conftest uses, on the real modules, all inside one test. conftest's own
    # wiring (what it snapshots and restores) is checked by the test further down, also in one test.
    pytest.importorskip("fastapi")
    import api
    import database

    modules = ((database, ("_pool",)), (api, ("_pdf_fonts",)))
    snapshots = [db_safety.snapshot_module(module, runtime) for module, runtime in modules]
    pool_max, headers = database.POOL_MAX, dict(api.SECURITY_HEADERS)
    database.POOL_MAX = -1
    database.safety_net_leak = True
    api.SECURITY_HEADERS["X-Safety-Net-Leak"] = "1"
    api.safety_net_leak = True
    for (module, runtime), snapshot in zip(modules, snapshots):
        db_safety.restore_module(module, snapshot, runtime)
    assert database.POOL_MAX == pool_max and not hasattr(database, "safety_net_leak")
    assert api.SECURITY_HEADERS == headers and not hasattr(api, "safety_net_leak")


def test_outside_a_sandbox_test_the_sandbox_gate_is_closed():
    assert not db_safety.gate_is_open()
    with pytest.raises(db_safety.SafetyError):
        db_safety.sandbox_sql("SELECT 1")


# --- The sandbox settings -------------------------------------------------------------------

def test_a_valid_sandbox_configuration():
    config = db_safety.sandbox_config(sandbox_environment())
    assert (config.host, config.port, config.dbname, config.user, config.token) == (
        "127.0.0.1", "55432", "ft_test_sandbox", "ft_test_runner", TOKEN)
    assert config.dsn_write == SANDBOX_URL + "?options=-c%20search_path%3Dft_test"
    assert config.dsn_read == SANDBOX_URL + "?options=-c%20default_transaction_read_only%3Don%20-c%20search_path%3Dft_test"
    assert db_safety.gate_for(config) == GATE
    db_safety.check_gate(db_safety.gate_for(config))


@pytest.mark.parametrize("url,host", [
    ("postgresql://ft_test_runner@[::1]:55432/ft_test_sandbox", "::1"),
    ("postgres://ft_test_x:pw@127.0.0.1:5432/ft_test_y", "127.0.0.1"),
])
def test_other_loopback_forms_are_valid(url, host):
    assert db_safety.sandbox_config(sandbox_environment(**{db_safety.SANDBOX_URL_VARIABLE: url})).host == host


@pytest.mark.parametrize("value", [None, "", "   "])
def test_no_sandbox_is_configured_without_a_url(value):
    environment = sandbox_environment(**{db_safety.SANDBOX_URL_VARIABLE: value})
    assert not db_safety.sandbox_configured(environment)
    assert db_safety.sandbox_config(environment) is None


@pytest.mark.parametrize("url", [
    "postgresql://ft_test_runner@localhost:55432/ft_test",
    "postgres://ft_test_x:pw@LOCALHOST:5432/ft_test_y",
    "postgresql://ft_test_runner:pw@db.example.com:5432/ft_test_sandbox",
    "postgresql://ft_test_runner:pw@ep-cool-123.eu-central-1.aws.neon.tech:5432/ft_test_sandbox",
    "postgresql://ft_test_runner:pw@EP-COOL-123.EU-CENTRAL-1.AWS.NEON.TECH:5432/ft_test_sandbox",
    "postgresql://ft_test_runner:pw@10.0.0.5:5432/ft_test_sandbox",
    "postgresql://ft_test_runner:pw@127.0.0.2:5432/ft_test_sandbox",
    "postgresql://ft_test_runner:pw@127.0.0.1.:5432/ft_test_sandbox",
    "postgresql://ft_test_runner:pw@127.0.0.1:55432/ft_test_sandbox?sslmode=require",
    "postgresql://ft_test_runner:pw@127.0.0.1:55432/ft_test_sandbox?host=db.example.com",
    "postgresql://ft_test_runner:pw@127.0.0.1:55432/ft_test_sandbox?hostaddr=203.0.113.1",
    "postgresql://ft_test_runner:pw@127.0.0.1:55432/ft_test_sandbox?",
    "postgresql://ft_test_runner:pw@127.0.0.1:55432/ft_test_sandbox#fragment",
    "postgresql://ft_test_runner:pw@127.0.0.1:5432,db.example.com:5432/ft_test_sandbox",
    "postgresql://ft_test_runner:pw@127.0.0.1,db.example.com:5432/ft_test_sandbox",
    "postgresql://ft_test_runner:pw@127.0.0.1/ft_test_sandbox",
    "postgresql://ft_test_runner:pw@127.0.0.1:55432/neondb",
    "postgresql://ft_test_runner:pw@127.0.0.1:55432/FT_TEST_SANDBOX",
    "postgresql://ft_test_runner:pw@127.0.0.1:55432/ft_test_sandbox/extra",
    "postgresql://ft_test_runner:pw@127.0.0.1:55432/",
    "postgresql://postgres:pw@127.0.0.1:55432/ft_test_sandbox",
    "postgresql://127.0.0.1:55432/ft_test_sandbox",
    "mysql://ft_test_runner:pw@127.0.0.1:55432/ft_test_sandbox",
    "host=127.0.0.1 port=55432 dbname=ft_test_sandbox user=ft_test_runner",
    "postgresql://ft_test_runner:pw@127.0.0.1:55432/ft_test_sandbox x",
])
def test_an_unsafe_sandbox_url_is_refused(url):
    with pytest.raises(db_safety.SafetyError):
        db_safety.sandbox_config(sandbox_environment(**{db_safety.SANDBOX_URL_VARIABLE: url}))


@pytest.mark.parametrize("token", [None, "", "short", "t" * 31, "t" * 20 + " " + "t" * 20, "t" * 40 + ";"])
def test_a_missing_or_malformed_token_is_refused_unless_init_is_running(token):
    environment = sandbox_environment(**{db_safety.SANDBOX_TOKEN_VARIABLE: token})
    with pytest.raises(db_safety.SafetyError):
        db_safety.sandbox_config(environment)
    assert db_safety.sandbox_config(environment, require_token=False) is not None


# --- Isolation of process-wide state --------------------------------------------------------

def test_the_environment_is_clean():
    assert db_safety.pg_variables() == []
    assert os.environ["DATABASE_URL"] == db_safety.DUMMY_DATABASE_URL
    assert os.environ["TESTING"] == "true"
    assert os.environ[db_safety.DOTENV_SWITCH] == "1", ".env must not be loaded into the test process"


class _SafetyNetLeak(Exception):
    """Only used as a key that must not survive the test that adds it."""


def _assert_the_original_application_state(api, database):
    """What conftest's snapshot holds: the same assertions before the changes and after the reset."""
    assert database.get_connection.__module__ == "database" and database.get_connection.__name__ == "get_connection"
    assert not hasattr(database, "safety_net_leak")
    assert api.startup in api.app.router.on_startup and api.shutdown in api.app.router.on_shutdown
    assert database._pool is None, "no test inherits a connection pool"
    assert not db_safety.gate_is_open()
    assert _SafetyNetLeak not in api.app.dependency_overrides
    assert "X-Safety-Net-Leak" not in getattr(api, "SECURITY_HEADERS", {})
    assert _SafetyNetLeak not in api.app.exception_handlers
    assert getattr(api.app, "openapi_schema", None) is None, "the cached schema is dropped again"
    assert not hasattr(api.app.state, "safety_net_leak")
    assert os.environ["TESTING"] == "true" and os.environ["DATABASE_URL"] == db_safety.DUMMY_DATABASE_URL
    assert db_safety.pg_variables() == []


def test_every_test_starts_from_the_original_application_state():
    # Holds whatever ran before (conftest restores the snapshots before every test). On its own
    # it proves little in a given order: the test below changes the state and checks the reset.
    pytest.importorskip("fastapi")
    import api
    import database

    _assert_the_original_application_state(api, database)


def test_conftests_own_reset_undoes_changes_to_the_application_state_within_one_test(request):
    # The change, conftest's own reset and the check all happen in this one test, so the result does
    # not depend on which tests run before or after it. conftest is found among the registered
    # plugins by its file (not imported by name), and its _reset_state is what runs before and
    # after every test. The reset also runs in `finally`, so the state is put back even when an
    # assertion fails; the attributes touched are the ones conftest restores (none added on a guess).
    pytest.importorskip("fastapi")
    import api
    import database

    wanted = os.path.realpath(str(TESTS / "conftest.py"))
    found = [plugin for plugin in request.config.pluginmanager.get_plugins()
             if os.path.realpath(getattr(plugin, "__file__", None) or "") == wanted]
    assert len(found) == 1, "tests/conftest.py is not registered as exactly one plugin"
    reset = found[0]._reset_state
    app = api.app
    try:
        _assert_the_original_application_state(api, database)
        database.get_connection = lambda: None
        database.POOL_MAX = -1
        database.safety_net_leak = True
        app.router.on_startup.clear()
        app.router.on_shutdown.clear()
        app.dependency_overrides[_SafetyNetLeak] = lambda: None
        if isinstance(getattr(api, "SECURITY_HEADERS", None), dict):
            api.SECURITY_HEADERS["X-Safety-Net-Leak"] = "1"
        app.exception_handlers[_SafetyNetLeak] = lambda leaked_request, leaked_exc: None
        app.openapi_schema = {"leaked": True}
        app.state.safety_net_leak = True
        os.environ["TESTING"] = "leaked"
        os.environ["DATABASE_URL"] = "postgresql://leaked@127.0.0.1:1/leaked"
        os.environ["PGHOST"] = "leaked.invalid"
        # The changes are really in place, so the checks after the reset mean something.
        assert database.POOL_MAX == -1 and not app.router.on_startup and _SafetyNetLeak in app.dependency_overrides
        assert app.state.safety_net_leak and os.environ["PGHOST"] == "leaked.invalid"
        repaired = reset()
    finally:
        reset()
    assert repaired == [], "nothing that should never need repairing was found"
    _assert_the_original_application_state(api, database)


def test_the_environment_is_restored_to_its_snapshot():
    environ = {"A": "1", "B": "2", "PYTEST_CURRENT_TEST": "x"}
    snapshot = db_safety.snapshot_environ(environ)
    assert "PYTEST_CURRENT_TEST" not in snapshot, "pytest's own variable is left to pytest"
    environ.update({"A": "changed", "C": "new", "PYTEST_CURRENT_TEST": "y"})
    del environ["B"]
    assert db_safety.restore_environ(snapshot, environ) == ["A", "B", "C"]
    assert environ == {"A": "1", "B": "2", "PYTEST_CURRENT_TEST": "y"}


def test_a_module_is_restored_to_its_snapshot():
    module = types.ModuleType("fake_database")

    def original():
        return "real"

    items, headers = [1, 2], {"a": 1}
    module.get_connection, module.LIMIT, module._pool, module.ITEMS, module.HEADERS = original, 5, None, items, headers
    snapshot = db_safety.snapshot_module(module, ("_pool",))
    module.get_connection, module.LIMIT, module.extra, module._pool = (lambda: "fake"), 6, 1, "pool"
    items.append(3)
    headers["b"] = 2
    assert db_safety.restore_module(module, snapshot, ("_pool",)) == ["HEADERS", "ITEMS", "LIMIT", "extra",
                                                                      "get_connection"]
    assert module.get_connection is original and module.LIMIT == 5 and not hasattr(module, "extra")
    assert module.ITEMS is items and items == [1, 2] and module.HEADERS is headers and headers == {"a": 1}
    assert module._pool == "pool", "runtime names are left to the module (the pool is closed separately)"


# --- Subprocesses ---------------------------------------------------------------------------

def test_the_subprocess_environment_is_an_allow_list(monkeypatch):
    for name, value in (("PGHOST", "db.example.com"), ("PGSERVICE", "production"), ("PGSSLMODE", "require"),
                        (db_safety.SANDBOX_URL_VARIABLE, SANDBOX_URL), (db_safety.SANDBOX_TOKEN_VARIABLE, TOKEN),
                        ("PYTHONPATH", "/somewhere"), ("PYTHONHOME", "/somewhere"), ("PYTHONSTARTUP", "/x.py"),
                        ("LD_PRELOAD", "/x.so"), ("DYLD_INSERT_LIBRARIES", "/x.dylib"), ("SECRET_KEY", "s3cr3t"),
                        ("RENDER", "true"), ("API_DOCS", "1"), ("LC_ALL", "C")):
        monkeypatch.setenv(name, value)
    env = db_safety.safe_subprocess_env({"EXTRA": "1"})
    for name in ("PGHOST", "PGSERVICE", "PGSSLMODE", db_safety.SANDBOX_URL_VARIABLE, db_safety.SANDBOX_TOKEN_VARIABLE,
                 "PYTHONHOME", "PYTHONSTARTUP", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "SECRET_KEY", "RENDER",
                 "API_DOCS", "HOME"):
        assert name not in env, name
    assert env["DATABASE_URL"] == db_safety.DUMMY_DATABASE_URL and env["EXTRA"] == "1" and env["LC_ALL"] == "C"
    assert env[db_safety.DOTENV_SWITCH] == "1" and env["PYTHONNOUSERSITE"] == "1"
    guard = Path(env["PYTHONPATH"])
    assert guard.name == "subprocess_guard" and os.pathsep not in env["PYTHONPATH"]
    assert (guard / "sitecustomize.py").is_file()
    fixed = {"DATABASE_URL", db_safety.DOTENV_SWITCH, "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE", "PYTHONPATH", "EXTRA"}
    assert all(key.upper() in db_safety.SUBPROCESS_ALLOWED or key.upper().startswith("LC_") or key in fixed
               for key in env)


@pytest.mark.parametrize("name", ["PGHOST", "pghost", "PGSSLMODE", "FT_TEST_POSTGRES_URL", "DATABASE_URL",
                                  "DATABASE_HOST", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT",
                                  "PYTHON_DOTENV_DISABLED", "PYTHONNOUSERSITE", "LD_PRELOAD", "LD_LIBRARY_PATH",
                                  "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "PATH", "TMPDIR", "LC_ALL",
                                  "lower_case", "WITH-DASH", ""])
def test_a_subprocess_cannot_be_given_dangerous_settings(name):
    with pytest.raises(ValueError):
        db_safety.safe_subprocess_env({name: "x"})


@pytest.mark.parametrize("value", ["postgresql://owner:pw@db.example.com/neondb", "postgres://x@127.0.0.1/y",
                                   "host=db.example.com dbname=prod", "hostaddr=203.0.113.1", "service=prod",
                                   "dbname=neondb user=owner", "a\x00b", 5, None])
def test_a_subprocess_setting_cannot_be_a_connection_string(value):
    with pytest.raises(ValueError) as raised:
        db_safety.safe_subprocess_env({"SETTING": value})
    assert "db.example.com" not in str(raised.value) and "pw" not in str(raised.value), "values are never echoed"


def test_the_subprocess_guard_stops_the_child_instead_of_raising():
    # site.py catches whatever sitecustomize raises and runs the child anyway.
    tree = ast.parse((TESTS / "subprocess_guard" / "sitecustomize.py").read_text(encoding="utf-8"))
    module_level, stack = [], list(tree.body)
    while stack:  # everything outside function bodies, i.e. what runs while site.py imports the file
        node = stack.pop()
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            module_level.append(node)
            stack.extend(ast.iter_child_nodes(node))
    assert not any(isinstance(node, ast.Raise) and node.exc is not None for node in module_level), \
        "the guard must not raise its own exceptions while it is installed"
    handlers = [node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)
                and isinstance(node.type, ast.Name) and node.type.id == "BaseException"]
    assert handlers, "every failure while installing the guard must end in os._exit"
    assert [node for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr == "_exit"]


@needs_driver
def test_a_subprocess_refuses_every_connection():
    script = (
        "import psycopg2\n"
        "try:\n"
        "    psycopg2.connect('host=/nonexistent/ft_test_safety_net port=1 dbname=x user=y connect_timeout=1')\n"
        "except psycopg2.OperationalError as exc:\n"
        "    print(exc)\n"
        "    raise SystemExit(7)\n"
    )
    result = subprocess.run([sys.executable, "-c", script], cwd=str(ROOT), env=db_safety.safe_subprocess_env(),
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 7, result.stderr
    assert "blocked in test subprocesses" in result.stdout  # not a plain "connection refused"


# --- Static checks of the Python sources in tests/ ----------------------------------------------
#
# Heuristics over the syntax tree plus a few regular expressions; NOT a proof and NOT a
# complete analysis. They follow import aliases and plain ``name = module.attr``
# assignments inside one file, positional and keyword arguments of the calls they know,
# but not values passed through functions, containers, objects or other files, and not
# code or SQL built at run time. They catch accidents and the obvious bypasses; a
# determined author can still get around them, which is why the guard itself also
# checks at run time.

# The safety net's own files use what the checks forbid; they are reviewed by hand.
GUARD_FILES = {"conftest.py", "db_safety.py", "pg_sandbox.py", "test_safety_net.py", "subprocess_guard/sitecustomize.py"}
GUARDED_MODULES = ("psycopg2", "db_safety", "sitecustomize", "conftest")
SANDBOX_FIXTURES = {"pg_database_url", "pg_gate", "pg_sandbox"}
DB_SAFETY_RESTRICTED = {"open_gate", "activate", "deactivate", "install_guard", "gate_for", "verify_sandbox",
                        "check_server", "check_connect", "check_gate", "expected_refusals", "psycopg2", "parse_dsn",
                        "restore_environ", "restore_module", "snapshot_environ", "snapshot_module"}
# Attribute names that only matter for tampering, on whatever object they are reached.
TAMPERING_ATTRIBUTES = {"_connect", "_psycopg", "_settle", "_allow_expected", "__closure__", "__code__",
                        "__globals__", "__builtins__", "__defaults__", "__kwdefaults__", "cell_contents",
                        "exitstatus"}
# Module names a file in tests/ must not have: tests/ comes first on sys.path, so such a
# file would be imported instead of the real module (checked by file and folder name).
TRUSTED_MODULE_NAMES = {"psycopg2", "psycopg", "dotenv", "database", "api", "auth", "db_safety", "conftest",
                        "pytest", "_pytest", "pluggy", "site", "sitecustomize", "usercustomize", "fastapi",
                        "starlette", "importlib", "os", "sys", "subprocess", "threading", "types"}
_DRIVER = "uses a database driver the guard does not cover"
_NETWORK = "opens network connections the guard does not cover"
FORBIDDEN_MODULES = tuple((name, _DRIVER) for name in (
    "psycopg", "psycopg_pool", "asyncpg", "sqlalchemy", "pg8000", "aiopg", "postgresql", "pgdb", "psqlpy",
    "peewee", "pony", "databases")) + tuple((name, _NETWORK) for name in (
    "socket", "requests", "urllib.request", "http.client", "aiohttp", "websocket", "websockets", "paramiko",
    "sshtunnel", "smtplib", "ftplib", "telnetlib")) + (
    ("ctypes", "calls native code around the guard"),
    ("cffi", "calls native code around the guard"),
    ("runpy", "runs code dynamically"),
    ("builtins", "reaches the builtins the checks rely on"),
    ("pickle", "deserialises code"), ("marshal", "deserialises code"), ("shelve", "deserialises code"),
    ("dill", "deserialises code"), ("cloudpickle", "deserialises code"),
    ("gc", "reaches objects by reference walking"), ("inspect", "reaches closures and frames"),
    ("_ctypes", "calls native code around the guard"), ("_socket", "opens network connections the guard does not cover"),
    ("importlib.util", "imports dynamically"),
    ("importlib.machinery", "imports dynamically"),
    ("imp", "imports or reloads dynamically"), ("pkgutil", "imports dynamically"),
    ("zipimport", "imports dynamically"),
    ("multiprocessing", "starts a process without the subprocess guard"),
    ("pty", "starts a process without the subprocess guard"),
    ("dotenv", "loads .env, which holds the production settings"),
    ("migrate", "imports a migration script, which connects to the real database"),
    ("urllib3", _NETWORK),
    ("xmlrpc", _NETWORK),
    ("httpx", "makes HTTP requests directly (use fastapi.testclient.TestClient)"),
)
# Network packages reached through a short import (``import urllib`` then ``urllib.request.x``,
# ``import http`` then ``http.client.x``). Only names that come from an import are checked: a
# fixture or variable that happens to be called ``http`` is not the standard library module.
NETWORK_MODULE_PREFIXES = ("urllib.request", "http.client", "xmlrpc")
# The packages those submodules hang on. ``getattr(urllib, "request")``, ``getattr(urllib, name)``,
# ``vars(urllib)`` and ``urllib.__dict__`` reach them without writing ``urllib.request``; they are
# refused when ``urllib``/``http``/``xmlrpc`` came from an import. ``getattr(urllib, "parse")`` and
# ``getattr(http, "HTTPStatus")`` (a literal, harmless name) stay allowed.
NETWORK_ROOTS = ("urllib", "http", "xmlrpc")
# The builtins tests/db_safety.py refers to by name (read from its source, so the list cannot go
# stale): replacing one of them changes what the checks mean. Patching any other builtin
# (``patch("builtins.input")``) stays allowed. Names that are only attributes or that db_safety
# merely assigns are not included; the integrity check in db_safety watches the same builtins at
# run time, from the code objects.
GUARD_BUILTINS = frozenset(
    node.id for node in ast.walk(ast.parse((TESTS / "db_safety.py").read_text(encoding="utf-8")))
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in vars(builtins)
    and not node.id.startswith("_"))
# Modules a test may probe with pytest.importorskip without using them directly
# (fastapi.testclient needs httpx); any use through the returned module is still flagged.
PROBE_ONLY_MODULES = {"httpx"}
# HTTP client packages: any attribute reached through them is a direct network call.
HTTP_CLIENT_ROOTS = ("httpx", "urllib3", "requests", "aiohttp")
# Low-level network calls, flagged on whatever object they are reached (an event loop, a socket).
NETWORK_ATTRIBUTES = {"create_connection", "create_unix_connection", "open_connection", "open_unix_connection",
                      "sock_connect", "create_datagram_endpoint", "getaddrinfo"}
# Modules where even a computed getattr(module, name) is refused.
SENSITIVE_MODULES = {"os", "sys", "subprocess", "importlib", "builtins", "socket", "asyncio", "ctypes", "psycopg2",
                     "db_safety"}
FORBIDDEN_NAMES = {
    "__import__", "builtins.__import__", "exec", "builtins.exec", "eval", "builtins.eval",
    "compile", "builtins.compile", "globals", "locals", "__builtins__", "os.system", "os.popen", "os.fork",
    "os.forkpty", "os.putenv", "concurrent.futures.ProcessPoolExecutor", "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell", "asyncio.open_connection", "asyncio.open_unix_connection",
    "operator.attrgetter", "operator.methodcaller", "sys.meta_path", "sys.path_hooks", "sys.settrace",
    "sys.setprofile", "sys.addaudithook", "threading.settrace", "threading.setprofile",
}
FORBIDDEN_PREFIXES = ("os.spawn", "os.exec", "os.posix_spawn", "asyncio.subprocess")
SUBPROCESS_CALLS = {"run", "Popen", "call", "check_call", "check_output"}
SUBPROCESS_CONSTANTS = {"PIPE", "DEVNULL", "STDOUT", "TimeoutExpired", "CalledProcessError",
                        "CompletedProcess", "SubprocessError"}
SUBPROCESS_KEYWORDS = {"cwd", "env", "capture_output", "text", "timeout", "check", "stdout", "stderr",
                       "input", "encoding", "errors", "universal_newlines"}
RISKY_VARIABLES = ("PG", "FT_TEST_", "DATABASE_URL")
TEXT_RULES = (
    (r"migrate_to_v2|migrate_sqlite_to_neon|\bmigrate_\w+", "mentions a migration script, which connects to the real database"),
    (r"\bexpect_blocked\b|\bexpected_refusals\b", "expects refused connections (only tests/test_safety_net.py may)"),
    (r"\bpytest_plugins\b", "loads pytest plugins"),
)
# Checked in every string constant (including the literal parts of f-strings) after SQL
# comments are removed and white space is collapsed.
SQL_RULES = (
    r"\bDROP\s+(SCHEMA|DATABASE|ROLE|USER|GROUP|OWNED|EXTENSION|SERVER|FUNCTION|PROCEDURE|TABLESPACE|"
    r"FOREIGN|EVENT|LANGUAGE|TRIGGER|RULE)\b",
    r"\bTRUNCATE\b",
    r"\bALTER\s+(ROLE|USER|GROUP|DATABASE|SYSTEM|SCHEMA|DEFAULT\s+PRIVILEGES|EXTENSION|SERVER|FOREIGN|"
    r"LARGE\s+OBJECT|FUNCTION|PROCEDURE)\b",
    r"\b(GRANT|REVOKE)\b",
    r"\bCREATE\s+(ROLE|USER|GROUP|DATABASE|EXTENSION|SERVER|SCHEMA|TABLESPACE|FOREIGN|SUBSCRIPTION|PUBLICATION|"
    r"(OR\s+REPLACE\s+)?(FUNCTION|PROCEDURE|LANGUAGE|TRIGGER|RULE|TRANSFORM)|EVENT\s+TRIGGER|CAST|OPERATOR)\b",
    r"\bSET\s+(LOCAL\s+|SESSION\s+)?(ROLE|AUTHORIZATION|SESSION\s+AUTHORIZATION|search_path)\b|\bRESET\s+(ALL|ROLE|search_path)\b",
    r"\bset_config\s*\(",
    r"\bCOPY\b[^;]*\b(PROGRAM|(FROM|TO)\s+E?')",
    r"\bLOAD\s+'",
    r"\b(dblink\w*|postgres_fdw|pg_terminate_backend|pg_cancel_backend|lo_import|lo_export|pg_read_file|"
    r"pg_read_binary_file|pg_ls_dir|pg_reload_conf)\b",
)
# Only in statements of files that use the sandbox: dynamic SQL that would hide what runs.
SANDBOX_SQL_RULES = (r"\bDO\s+\$", r"\bEXECUTE\b")
# A string piece is treated as an SQL statement when it starts with one of these words.
SQL_STATEMENT = re.compile(r"^\s*\(?\s*(SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|WITH|DO|EXECUTE|CALL|MERGE)\b", re.I)
# In sandbox files these statements must name an ft_test.* table (matched at the start).
QUALIFIED_STATEMENTS = re.compile(
    r"\s*(?:DROP\s+(?:TABLE|VIEW|MATERIALIZED\s+VIEW|SEQUENCE|INDEX|TYPE)\s+(?:IF\s+EXISTS\s+)?"
    r"|DELETE\s+FROM\s+(?:ONLY\s+)?|UPDATE\s+(?:ONLY\s+)?|ALTER\s+(?:TABLE|SEQUENCE|INDEX|VIEW)\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?)"
    r"([^;]+?)(?:\s+(?:CASCADE|RESTRICT|SET\b.*|WHERE\b.*|RENAME\b.*|ADD\b.*|DROP\b.*|ALTER\b.*|OWNER\b.*))?\s*(?:;|$)",
    re.I | re.S)
_URL_HOST = re.compile(r"postgres(?:ql)?://(?:[^@/\s]*@)?(\[[^\]]*\]|[^:/\s?,]+)", re.I)
_KEYWORD_HOST = re.compile(r"\b(?:host|hostaddr)\s*=\s*'?([^\s',]+)", re.I)
# Strings that only look like SQL: hostile input handed to code under test, never executed.
NOT_SQL = {"'); DROP TABLE operations; --"}


def _checked_files():
    return [path for path in sorted(TESTS.rglob("*.py"))
            if "__pycache__" not in path.parts and path.relative_to(TESTS).as_posix() not in GUARD_FILES]


def _dotted(node, names):
    """``a.b.c`` with the import alias of ``a`` resolved, or None for anything else."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return ".".join([names.get(node.id, node.id)] + parts[::-1])


def _guarded(dotted):
    return isinstance(dotted, str) and any(dotted == name or dotted.startswith(name + ".") for name in GUARDED_MODULES)


def _text(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _leading_text(node):
    """The text a string expression is certain to start with: the whole constant, the first
    literal part of an f-string, the left side of ``"a" + x``. "" when nothing is known."""
    if isinstance(node, ast.JoinedStr) and node.values:
        return _text(node.values[0]) or ""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _leading_text(node.left)
    return _text(node) or ""


def _network_reference(dotted):
    """Whether a dotted name (aliases resolved) is urllib.request, http.client or xmlrpc, or
    something under them, or the namespace dict of urllib/http/xmlrpc."""
    return (any(dotted == prefix or dotted.startswith(prefix + ".") for prefix in NETWORK_MODULE_PREFIXES)
            or dotted in [root + ".__dict__" for root in NETWORK_ROOTS])


def _argument(call, position, *keywords):
    if len(call.args) > position and not any(isinstance(arg, ast.Starred) for arg in call.args[:position + 1]):
        return call.args[position]
    return next((keyword.value for keyword in call.keywords if keyword.arg in keywords), None)


def _import_names(tree):
    names = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                names[alias.asname or alias.name] = node.module + "." + alias.name
    for _ in range(3):  # plain re-bindings: x = module.attr, x = pytest.importorskip("module")
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
                continue
            value = node.value
            if isinstance(value, ast.Call) and (_dotted(value.func, names) or "").endswith("importorskip"):
                module = _text(_argument(value, 0, "modname"))
                if module:
                    names[node.targets[0].id] = module
            elif isinstance(value, (ast.Name, ast.Attribute)):
                dotted = _dotted(value, names)
                root = value
                while isinstance(root, ast.Attribute):
                    root = root.value
                if dotted and isinstance(root, ast.Name) and root.id in names:
                    names[node.targets[0].id] = dotted
    return names


def _module_problems(module):
    return ["%s (%s)" % (reason, module) for name, reason in FORBIDDEN_MODULES
            if module == name or module.startswith(name + ".") or (name == "migrate" and module.startswith("migrate"))]


def _reference_problems(dotted):
    parts = dotted.split(".")
    if parts[0] == "psycopg2":
        for part in parts[1:]:
            if part.startswith("_"):
                return ["reaches into psycopg2 internals (%s)" % dotted]
            if part == "connection" or part.endswith("Connection"):
                return ["creates a connection around the guard (%s)" % dotted]
    if any(part in TAMPERING_ATTRIBUTES for part in parts[1:]):
        return ["reaches an attribute only used for tampering (%s)" % dotted]
    if parts[-1] in ("hookimpl", "hookwrapper", "HookimplMarker"):
        return ["defines pytest hooks, which can change the session's outcome (%s)" % dotted]
    if parts[0] == "builtins" and len(parts) > 1 and (parts[1] in GUARD_BUILTINS or parts[1] == "__dict__"):
        return ["replaces or reaches a builtin the safety net's own checks rely on (%s)" % dotted]
    if parts[0] in HTTP_CLIENT_ROOTS and len(parts) > 1:
        return ["makes a network call around the test client (%s)" % dotted]
    if parts[0] == "db_safety" and len(parts) > 1 and (parts[1].startswith("_") or parts[1] in DB_SAFETY_RESTRICTED):
        return ["operates the safety net by hand (%s)" % dotted]
    if parts[0] in ("sitecustomize", "conftest"):
        return ["touches the safety net (%s)" % dotted]
    if dotted == "sys.modules" or dotted.startswith("sys.modules."):
        return ["changes sys.modules, where the guarded psycopg2 lives"]
    if parts[0] == "importlib" and dotted not in ("importlib", "importlib.reload"):
        return ["imports dynamically (%s)" % dotted]
    if dotted in FORBIDDEN_NAMES or dotted.startswith(FORBIDDEN_PREFIXES):
        return ["runs code or a process, or reaches objects, around the guard (%s)" % dotted]
    if parts[0] == "subprocess" and len(parts) > 1 and parts[1] not in SUBPROCESS_CONSTANTS:
        return ["uses subprocess other than in a checked call (%s)" % dotted]
    return []


def _scope(node, parents):
    while node in parents and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        node = parents[node]
    return node


def _single_assignment(name, scope):
    """The value assigned to ``name`` in ``scope`` if it is assigned exactly once and never
    changed in place (no item assignment, no attribute or method use), else None."""
    stores = [node for node in ast.walk(scope)
              if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, (ast.Store, ast.Del))]
    assigned = [node for node in ast.walk(scope) if isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name]
    if len(stores) != 1 or len(assigned) != 1:
        return None
    for node in ast.walk(scope):
        if isinstance(node, (ast.Subscript, ast.Attribute)) and isinstance(node.value, ast.Name) and node.value.id == name:
            return None
    return assigned[0].value


def _is_safe_env_call(node, names):
    return isinstance(node, ast.Call) and _dotted(node.func, names) == "db_safety.safe_subprocess_env"


def _subprocess_problems(call, scope, names):
    found = []
    argv = _argument(call, 0, "args")
    if len(call.args) > 1 or not (
            isinstance(argv, (ast.List, ast.Tuple)) and len(argv.elts) == 3
            and _dotted(argv.elts[0], names) == "sys.executable" and _text(argv.elts[1]) == "-c"):
        found.append("the command must be exactly [sys.executable, '-c', script]")
    else:
        script = argv.elts[2]
        if isinstance(script, ast.Name):
            script = _single_assignment(script.id, scope)
        if _text(script) is None:
            found.append("its -c script is not a constant that can be checked")
        else:
            try:
                found += ["its -c script " + problem for problem in _bypasses(_text(script))]
            except SyntaxError:
                found.append("its -c script cannot be parsed")
    keywords = {keyword.arg for keyword in call.keywords}
    if None in keywords or not keywords <= SUBPROCESS_KEYWORDS | {"args"}:
        found.append("passes keyword arguments that are not allowed (%s)"
                     % sorted(map(str, keywords - SUBPROCESS_KEYWORDS - {"args"})))
    env = next((keyword.value for keyword in call.keywords if keyword.arg == "env"), None)
    if isinstance(env, ast.Name):
        env = _single_assignment(env.id, scope)
    if not _is_safe_env_call(env, names):
        found.append("its env is not an unchanged safe_subprocess_env(...)")
    return found


def _risky_variable(name):
    return isinstance(name, str) and name.upper().startswith(RISKY_VARIABLES)


def _call_problems(call, names, parents, approved):
    found = []
    function = _dotted(call.func, names) or ""
    last = function.rsplit(".", 1)[-1]
    root = function.split(".")[0]
    dynamic = any(isinstance(arg, ast.Starred) for arg in call.args) or any(k.arg is None for k in call.keywords)
    if dynamic and last in ("setattr", "delattr", "object", "patch", "setitem", "delitem", "getattr", "reload",
                            "importorskip", "setenv", "connect"):
        found.append("passes arguments to %s with * or ** (cannot be checked)" % last)
    restoring = (root == "monkeypatch" and last == "setattr") or function.endswith("patch.object")
    if last in ("setattr", "delattr", "object"):
        target = _dotted(_argument(call, 0, "target", "obj"), names)
        attribute = _text(_argument(call, 1, "name", "attribute"))
        if target is None and _text(_argument(call, 0, "target")) is not None:
            target = None  # the string form is handled below
        elif _guarded(target):
            if not (restoring and target == "psycopg2" and attribute == "connect"):
                found.append("changes %s.%s (only monkeypatch.setattr(psycopg2, 'connect', ...) is allowed)"
                             % (target, attribute or "<computed>"))
    if last in ("setattr", "delattr", "patch") or function.endswith("patch.dict"):
        text = _text(_argument(call, 0, "target", "in_dict"))
        if text is not None:
            if _guarded(text):
                if not ((restoring or last == "patch") and text == "psycopg2.connect"):
                    found.append("changes %s (only psycopg2.connect may be replaced, by monkeypatch or mock)" % text)
            else:
                found += _reference_problems(text)
        elif _leading_text(_argument(call, 0, "target", "in_dict")).startswith("builtins"):
            # patch(f"builtins.{name}"), setattr("builtins." + name, ...): which builtin is not known.
            found.append("patches a name under builtins that is computed at run time (cannot be checked)")
    first = call.args[0] if call.args else None
    target = _dotted(first, names) if first is not None else None
    literal = _text(call.args[1]) if len(call.args) > 1 else None
    # Only urllib/http/xmlrpc that came from an import count: a fixture called ``http`` is not the module.
    network_import = first is not None and _root_name(first) in names and target in NETWORK_ROOTS
    if function in ("getattr", "builtins.getattr") and target is not None:
        # getattr(os, "system") is os.system; a computed name on a sensitive module is refused.
        if literal is not None:
            found += _reference_problems(target + "." + literal)
            if _root_name(first) in names and _network_reference(target + "." + literal):
                found.append("reaches %s.%s through getattr, a network module" % (target, literal))
        elif _guarded(target) or target.split(".")[0] in SENSITIVE_MODULES:
            found.append("looks %s up dynamically" % target)
        elif network_import:
            found.append("looks %s up dynamically (its submodules open network connections)" % target)
    if function in ("vars", "builtins.vars") and (_guarded(target) or network_import):
        found.append("looks %s up dynamically" % target)
    if function in ("vars", "builtins.vars") and not call.args:
        found.append("reads the local namespace dynamically (vars())")
    if last == "importorskip":
        module = _text(_argument(call, 0, "modname"))
        if module is not None:
            found += [] if module in PROBE_ONLY_MODULES else _module_problems(module) + _reference_problems(module)
        else:
            found.append("imports a module named at run time")
    if function == "importlib.reload":
        module = _dotted(_argument(call, 0, "module"), names)
        if module is None or _guarded(module) or module.split(".")[0] in ("sys", "importlib", "builtins", "site", "os"):
            found.append("reloads %s, which can undo the guard" % (module or "<computed>"))
    if last in ("setitem", "delitem"):
        dictionary = _argument(call, 0, "dic")
        if _dotted(dictionary, names) == "sys.modules":
            key = _text(_argument(call, 1, "name"))
            if key is not None and not _guarded(key) and key.split(".")[0] not in ("sys", "importlib", "builtins", "site"):
                approved.add(id(dictionary))
            else:
                found.append("replaces %s in sys.modules" % (key or "<computed>"))
        elif _dotted(dictionary, names) == "os.environ" and (_risky_variable(_text(_argument(call, 1, "name")))
                                                            or _text(_argument(call, 1, "name")) is None):
            found.append("sets a database variable in os.environ")
    if last == "setenv" and _risky_variable(_text(_argument(call, 0, "name"))):
        found.append("sets %s (only conftest's pg_database_url may point the app at the sandbox)"
                     % _text(_argument(call, 0, "name")))
    if function in ("os.environ.update", "os.environ.setdefault"):
        keys = []
        for arg in call.args[:1]:  # update(mapping) or setdefault(name, value)
            keys += [_text(key) for key in arg.keys] if isinstance(arg, ast.Dict) else [_text(arg)]
        keys += [keyword.arg for keyword in call.keywords]
        if any(key is None or _risky_variable(key) for key in keys):
            found.append("sets a database variable (or an unknown one) in os.environ")
    if function == "psycopg2.connect" or (function.startswith("psycopg2.") and last.endswith("Pool")):
        found.append("connects directly (%s); go through the application or sandbox_sql" % function)
    if any(keyword.arg == "connection_factory" for keyword in call.keywords):
        found.append("passes a connection_factory, which could connect somewhere else")
    if function.startswith("subprocess.") and last in SUBPROCESS_CALLS:
        approved.add(id(call.func))
        found += ["subprocess.%s: %s" % (last, problem)
                  for problem in _subprocess_problems(call, _scope(call, parents), names)]
    return found


def _sql_text(value):
    """SQL comments removed, white space collapsed: DROP/**/SCHEMA is DROP SCHEMA."""
    value = re.sub(r"/\*.*?\*/", " ", value, flags=re.S)
    value = re.sub(r"--[^\n]*", " ", value)
    return re.sub(r"\s+", " ", value)


def _constant_problems(value):
    found = []
    if value in NOT_SQL:
        return found
    sql = _sql_text(value)
    found += ["contains administrative or dangerous SQL (%s)" % pattern for pattern in SQL_RULES if re.search(pattern, sql, re.I)]
    if re.search(r"\.sql\b", value, re.I):
        found.append("names an SQL file (SQL read from files cannot be checked)")
    stripped = value.strip()
    if stripped == ".env" or stripped.endswith(("/.env", "\\.env")):
        found.append("names the .env file, which holds the production settings")
    hosts = [match.group(1) for match in _URL_HOST.finditer(value)] + [match.group(1) for match in _KEYWORD_HOST.finditer(value)]
    for host in hosts:
        bare = host.strip("[]").lower()
        if bare not in ("127.0.0.1", "::1", "localhost") and ("." in bare or ":" in bare):
            found.append("names a remote database host in a connection string")
            break
    return found


def _docstrings(tree):
    """Ids of string constants that are bare expression statements (docstrings and the
    like): they are never executed, so the SQL and host rules do not apply to them."""
    return {id(node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)}


def _bypasses(source):
    """Why this source may go around the safety net (heuristic, see above); [] when nothing is found."""
    tree = ast.parse(source)
    names = _import_names(tree)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = [reason for pattern, reason in TEXT_RULES if re.search(pattern, source)]
    approved = set()
    called = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    docstrings = _docstrings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found += _module_problems(alias.name) + _reference_problems(alias.name)
                if alias.name == "subprocess" and alias.asname:
                    found.append("imports subprocess under another name")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                continue
            found += _module_problems(module) + _reference_problems(module)
            for alias in node.names:
                if module == "subprocess" or alias.name == "*":
                    found.append("imports names from %s (use `import %s`)" % (module, module))
                else:
                    found += _module_problems(module + "." + alias.name) + _reference_problems(module + "." + alias.name)
        elif isinstance(node, ast.Call):
            found += _call_problems(node, names, parents, approved)
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete)):
            targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
            for target in targets:
                for element in (target.elts if isinstance(target, (ast.Tuple, ast.List)) else [target]):
                    base = _dotted(element.value, names) if isinstance(element, (ast.Attribute, ast.Subscript)) else None
                    if _guarded(base):
                        found.append("changes %s without monkeypatch" % base)
                    if isinstance(element, ast.Attribute) and element.attr in TAMPERING_ATTRIBUTES:
                        found.append("assigns %s, an attribute only used for tampering" % element.attr)
                    if isinstance(element, ast.Subscript) and base == "os.environ":
                        key = element.slice.value if isinstance(element.slice, ast.Index) else element.slice  # 3.8
                        if _text(key) is None or _risky_variable(_text(key)):
                            found.append("sets a database variable (or an unknown one) in os.environ")
        elif isinstance(node, ast.Attribute) and node.attr in TAMPERING_ATTRIBUTES:
            found.append("reaches %s, an attribute only used for tampering" % node.attr)
        elif isinstance(node, ast.Attribute) and node.attr in NETWORK_ATTRIBUTES:
            found.append("opens a low-level network connection (.%s)" % node.attr)
        elif isinstance(node, ast.Attribute) and (node.attr == "connection" and _guarded(_dotted(node.value, names))
                                                  or node.attr.endswith("Connection")):
            found.append("creates a connection around the guard (.%s)" % node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            found += _constant_problems(node.value)
            if node.value in TAMPERING_ATTRIBUTES:
                found.append("names %s, an attribute only used for tampering" % node.value)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Name, ast.Attribute)) and id(node) not in approved:
            dotted = _dotted(node, names)
            if dotted:
                found += _reference_problems(dotted)
                if dotted in ("setattr", "delattr", "getattr", "builtins.setattr", "builtins.getattr") \
                        and id(node) not in called:
                    found.append("passes %s around instead of calling it" % dotted)
                if _root_name(node) in names and _network_reference(dotted):
                    found.append("reaches a network module through an imported name (%s)" % dotted)
    return sorted(set(found))


def _root_name(node):
    """The name an attribute chain starts from (``a`` in ``a.b.c``), or None."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _is_sandbox_file(path, tree, names):
    """Whether a file uses the PostgreSQL sandbox: by its name or by what it refers to."""
    if path.name.endswith("_postgres.py"):
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.arg in SANDBOX_FIXTURES:
            return True
        if _text(node) in SANDBOX_FIXTURES:
            return True
        if isinstance(node, (ast.Name, ast.Attribute)) and _dotted(node, names) == "db_safety.sandbox_sql":
            return True
    return False


def _parameters(function):
    return [arg.arg for arg in function.args.args + function.args.kwonlyargs]


def _is_fixture(function, names):
    return any((_dotted(d.func if isinstance(d, ast.Call) else d, names) or "").endswith("fixture")
               for d in function.decorator_list)


def _tests_without_the_sandbox(tree, names):
    """Test functions of a sandbox file that do not depend, directly or through fixtures
    defined in the same file, on pg_database_url or pg_gate."""
    fixtures = {node.name: node for node in tree.body
                if isinstance(node, ast.FunctionDef) and _is_fixture(node, names)}
    autouse = [name for name, node in fixtures.items() if any(
        isinstance(d, ast.Call) and any(k.arg == "autouse" and getattr(k.value, "value", None) is True for k in d.keywords)
        for d in node.decorator_list)]

    def reaches(name, seen):
        if name in ("pg_database_url", "pg_gate"):
            return True
        if name not in fixtures or name in seen:
            return False
        seen.add(name)
        return any(reaches(parameter, seen) for parameter in _parameters(fixtures[name]))

    def used(nodes):
        """Fixture names requested with pytest.mark.usefixtures("...") anywhere in ``nodes``."""
        return [_text(arg) for node in nodes for call in ast.walk(node) if isinstance(call, ast.Call)
                and _dotted(call.func, names) == "pytest.mark.usefixtures" for arg in call.args if _text(arg)]

    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    module_wide = autouse + used([node for node in tree.body if isinstance(node, ast.Assign)
                                  and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets)])
    tests = [(node, []) for node in tree.body if isinstance(node, functions) and node.name.startswith("test")
             and node.name not in fixtures]
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            tests += [(item, used(node.decorator_list)) for item in node.body
                      if isinstance(item, functions) and item.name.startswith("test")]
    return [test.name for test, inherited in tests
            if not any(reaches(parameter, set())
                       for parameter in _parameters(test) + used(test.decorator_list) + inherited + module_wide)]


def _has_postgres_mark(tree, names):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            if any(_dotted(item, names) == "pytest.mark.postgres" for item in ast.walk(node.value)):
                return True
    return False


MUST_BE_FLAGGED = [
    "import psycopg2\npsycopg2._connect = None",
    "import psycopg2 as pg\npg._psycopg._connect('dsn')",
    "from psycopg2 import _psycopg",
    "from psycopg2.extras import RealDictConnection",
    "import psycopg2.extras\npsycopg2.extras.LoggingConnection('dsn')",
    "from psycopg2 import extensions as e\ne.connection('dsn')",
    "def f():\n    import psycopg2\n    return psycopg2\nf()._connect",
    "def f(x):\n    return x.RealDictConnection('dsn')",
    "p = __import__('psycopg2')",
    "import importlib\nimportlib.import_module('psycopg2')",
    "import importlib, psycopg2\nimportlib.reload(psycopg2)",
    "import importlib, psycopg2\nimportlib.reload(module=psycopg2)",
    "from importlib import reload\nimport db_safety\nreload(db_safety)",
    "import sys\ndel sys.modules['psycopg2']",
    "import sys\nsys.modules['psycopg2'] = object()",
    "from sys import modules\nmodules.pop('psycopg2')",
    "import sys\ndef t(monkeypatch):\n    monkeypatch.setitem(sys.modules, 'psycopg2', None)",
    "import sys\ndef t(monkeypatch):\n    monkeypatch.setitem(dic=sys.modules, name='psycopg2', value=None)",
    "def t(monkeypatch):\n    import psycopg2\n    monkeypatch.setattr(psycopg2, '_connect', None)",
    "def t(monkeypatch):\n    import psycopg2\n    monkeypatch.setattr(target=psycopg2, name='_connect', value=None)",
    "def t(monkeypatch):\n    import psycopg2\n    monkeypatch.setattr(psycopg2, name='_connect', value=None)",
    "from unittest import mock\nimport psycopg2\nmock.patch.object(target=psycopg2, attribute='_connect')",
    "def t(monkeypatch):\n    monkeypatch.setattr('psycopg2._connect', None)",
    "def t(monkeypatch):\n    monkeypatch.setattr(target='psycopg2._connect', name=None)",
    "def t(monkeypatch):\n    import db_safety\n    monkeypatch.setattr(db_safety, 'check_connect', None)",
    "def t(monkeypatch):\n    import db_safety\n    monkeypatch.setattr(db_safety, 'integrity_problems', None)",
    "import psycopg2\nsetattr(psycopg2, 'connect', None)",
    "import psycopg2\nsetattr(psycopg2, '_con' + 'nect', None)",
    "import psycopg2\ngetattr(psycopg2, name)",
    "import psycopg2\npsycopg2.connect = None",
    "import psycopg2\nargs = (psycopg2, '_connect', None)\nsetattr(*args)",
    "import functools, psycopg2\nfunctools.partial(setattr, psycopg2)",
    "import operator, psycopg2\noperator.attrgetter('_connect')(psycopg2)",
    "globals()['psycopg2']",
    "__builtins__['__import__']('psycopg2')",
    "def f(g):\n    g.__code__ = None",
    "import db_safety\ndb_safety.open_gate(None)",
    "from db_safety import activate",
    "from db_safety import check_gate",
    "from db_safety import _settle\n_settle()",
    "import db_safety\ndb_safety._state['gate'] = 1",
    "import psycopg2\npsycopg2.connect('host=127.0.0.1 dbname=ft_test')",
    "import psycopg2\npsycopg2.connect(dsn='host=127.0.0.1', connection_factory=object)",
    "import psycopg2.pool\npsycopg2.pool.ThreadedConnectionPool(1, 1, 'dsn')",
    "import asyncpg",
    "from sqlalchemy import create_engine",
    "import psycopg",
    "import socket",
    "import requests",
    "from urllib.request import urlopen",
    "import paramiko",
    "import pickle",
    "import inspect",
    "from dotenv import load_dotenv",
    "x = pytest.importorskip('asyncpg')",
    "x = pytest.importorskip(modname='asyncpg')",
    "exec('print(1)')",
    "eval('1')",
    "import runpy",
    "import os\nos.system('psql')",
    "import os\nos.execv('/bin/sh', [])",
    "import os\nos.putenv('PGHOST', 'x')",
    "import multiprocessing",
    "import subprocess\nsubprocess.run(['psql'])",
    "import subprocess, sys\nsubprocess.run([sys.executable, '-c', 'pass'])",
    "import subprocess, sys\nsubprocess.run(args=['psql'])",
    "import subprocess, sys\nfrom db_safety import safe_subprocess_env\n"
    "subprocess.run([sys.executable, '-I', '-c', 'pass'], env=safe_subprocess_env())",
    "import subprocess, sys\nfrom db_safety import safe_subprocess_env\ndef f():\n    env = safe_subprocess_env()\n"
    "    env['PGHOST'] = 'x'\n    subprocess.run([sys.executable, '-c', 'pass'], env=env)",
    "import subprocess, sys\nfrom db_safety import safe_subprocess_env\n"
    "subprocess.run([sys.executable, '-c', 'import psycopg2; psycopg2._connect(1)'], env=safe_subprocess_env())",
    "import subprocess\nrun = subprocess.run",
    "from subprocess import run",
    "import migrate_to_v2",
    "import migrate_anything",
    "x = pytest.importorskip('migrate_to_v2')",
    "@pytest.mark.expect_blocked\ndef test_x(): pass",
    "pytest_plugins = ['elsewhere']",
    "import os\nos.environ['DATABASE_URL'] = 'postgresql://x@127.0.0.1/y'",
    "import os\nos.environ['PGHOST'] = 'db.example.com'",
    "import os\nos.environ.update({'PGHOST': 'x'})",
    "import os\nos.environ.update(values)",
    "def t(monkeypatch):\n    monkeypatch.setenv('DATABASE_URL', 'postgresql://x@127.0.0.1/y')",
    "def t(monkeypatch):\n    monkeypatch.setenv(name='PGHOST', value='x')",
    "URL = 'postgresql://owner:pw@ep-cool-123.eu-central-1.aws.neon.tech/neondb'",
    "DSN = 'host=db.example.com dbname=prod user=owner'",
    "PATH = '.env'",
    "PATH = 'migrations/0001.sql'",
    "SQL = 'GRANT ALL ON SCHEMA public TO ft_test_runner'",
    "SQL = f'TRUNCATE {table}'",
    "SQL = 'DROP SCHEMA public CASCADE'",
    "SQL = 'DROP/**/SCHEMA public CASCADE'",
    "SQL = 'DROP -- a comment\\n SCHEMA public'",
    "SQL = 'ALTER ROLE ft_test_runner SUPERUSER'",
    "SQL = 'SET search_path = public'",
    "SQL = \"SELECT set_config('search_path', 'public', false)\"",
    "SQL = \"COPY users TO '/tmp/users.csv'\"",
    "SQL = \"COPY users FROM PROGRAM 'curl example.com'\"",
    "SQL = 'CREATE OR REPLACE FUNCTION f() RETURNS int AS $$ SELECT 1 $$ LANGUAGE sql'",
    "SQL = \"LOAD 'auto_explain'\"",
    "SQL = \"SELECT dblink_connect('host=db.example.com')\"",
    "SQL = 'SELECT pg_terminate_backend(1)'",
    "def f(g):\n    g.__defaults__ = ()",
    "def f(g):\n    g.__kwdefaults__['environ'] = {}",
    "def f(cell):\n    cell.cell_contents = None",
    "import imp\nimp.reload(module)",
    "import pkgutil\npkgutil.resolve_name('psycopg2:connect')",
    "import sys\nsys.meta_path.pop(0)",
    "import sys\nsys.settrace(None)",
    "def f(session):\n    session.exitstatus = 0",
    "import pytest\n@pytest.hookimpl(wrapper=True)\ndef pytest_sessionfinish(session):\n    yield",
    "import os\ngetattr(os, 'system')('psql')",
    "import subprocess\ngetattr(subprocess, 'run')(['psql'])",
    "import sys\ngetattr(sys, 'modules')",
    "import os\nname = 'system'\ngetattr(os, name)",
    "import httpx",
    "from httpx import Client",
    "client = pytest.importorskip('httpx')\nclient.get('https://example.com')",
    "import urllib3",
    "import asyncio\nasyncio.get_event_loop().create_connection(None, 'db.example.com', 5432)",
    "def f(loop, sock):\n    return loop.sock_connect(sock, ('db.example.com', 5432))",
    "import urllib\nurllib.request.urlopen('https://example.com')",
    "import urllib.request",
    "from urllib import request",
    "from urllib import request as r\nr.urlopen('https://example.com')",
    "import http\nhttp.client.HTTPSConnection('example.com')",
    "import http.client as hc\nhc.HTTPSConnection('example.com')",
    "from http import client",
    "import xmlrpc.client",
    "import xmlrpc\nxmlrpc.client.ServerProxy('https://example.com')",
    "import urllib\ngetattr(urllib, 'request')",
    "import urllib\ngetattr(urllib, 'request').urlopen('https://example.com')",
    "import urllib as u\ngetattr(u, 'request')",
    "import http\ngetattr(http, 'client')",
    "import urllib\ngetattr(urllib, name)",
    "import http\ngetattr(http, 'cli' + 'ent')",
    "import urllib\nvars(urllib)",
    "import urllib\nurllib.__dict__",
    "import http\nhttp.__dict__['client']",
    "import xmlrpc\ngetattr(xmlrpc, name)",
    "def t(monkeypatch):\n    monkeypatch.setattr('builtins.len', None)",
    "from unittest.mock import patch\npatch('builtins.isinstance')",
    "from unittest.mock import patch\npatch.dict('builtins.__dict__', {})",
    "from unittest.mock import patch\nname = 'len'\npatch(f'builtins.{name}')",
    "def t(monkeypatch, n):\n    monkeypatch.setattr('builtins.' + n, None)",
]

MAY_PASS = [
    "import psycopg2\ndef t(monkeypatch):\n    monkeypatch.setattr(psycopg2, 'connect', None)",
    "import psycopg2\ndef t(monkeypatch):\n    monkeypatch.setattr(target=psycopg2, name='connect', value=None)",
    "psycopg2 = pytest.importorskip('psycopg2')\nfrom psycopg2 import extensions\n"
    "x = extensions.TRANSACTION_STATUS_IDLE\ny = psycopg2.OperationalError\nz = psycopg2.connect",
    "import importlib, auth\nimportlib.reload(auth)",
    "import sys, types\ndef t(monkeypatch):\n    monkeypatch.setitem(sys.modules, 'openpyxl', types.SimpleNamespace())",
    "from db_safety import sandbox_sql as sql, sandbox_configured\nsql('DELETE FROM ft_test.users')",
    "import subprocess, sys\nfrom db_safety import safe_subprocess_env\ndef f(env):\n    script = ('import api;'\n"
    "              'print(1)')\n    base = safe_subprocess_env(dict(env, SECRET_KEY='tests'))\n"
    "    return subprocess.run([sys.executable, '-c', script], cwd='.', env=base, capture_output=True, text=True, timeout=60)",
    "import subprocess, sys\nfrom db_safety import safe_subprocess_env\n"
    "subprocess.run([sys.executable, '-c', 'import database'], env=safe_subprocess_env({'A': '1'}))",
    "def t(monkeypatch):\n    monkeypatch.setattr('storage.get_operations', None)",
    "from unittest.mock import patch\nwith patch('builtins.input', return_value='1'):\n    pass",
    "import re\nPATTERN = re.compile('x')",
    "HEADER = \"script-src 'self'; no unsafe-eval\"",
    "def t(monkeypatch):\n    monkeypatch.setenv('DB_POOL_MAX', '0')\n    monkeypatch.delenv('DATABASE_URL', raising=False)",
    "def t(monkeypatch, name, value):\n    monkeypatch.setenv(name, value)",
    "def connect():\n    return database._checkout(database._get_pool('postgresql://test'))",
    "import psycopg2\ndef t(monkeypatch):\n    real_connect = psycopg2.connect\n    monkeypatch.setattr(psycopg2, 'connect', real_connect)",
    "SECRET = 'connection to server at \"ep-secret-host.neon.tech\" (10.0.0.7), port 5432 failed'",
    "FAKE = ['CREATE TABLE', 'CREATE INDEX', 'DO $$', 'ALTER TABLE', 'ON CONFLICT']",
    "def test_migration_keeps_rows(legacy):\n    pass",
    "def f(self):\n    return self.connection",
    "pytest.importorskip('httpx')\nfrom fastapi.testclient import TestClient\nTestClient(app).get('/health')",
    "import api as app_module\nstorage = getattr(app_module.limiter, '_storage', None)",
    "from reportlab.pdfgen import canvas\noriginal = {name: getattr(canvas.Canvas, name) for name in ('drawString',)}",
    "def test_x(http):\n    return http.get('/health'), http.post('/budgets', json={})",  # a fixture, not the module
    "import urllib.parse\nurllib.parse.quote('a b')",
    "from http import HTTPStatus\nHTTPStatus.OK",
    "import urllib\ngetattr(urllib, 'parse')",
    "import http\ngetattr(http, 'HTTPStatus')",
    "def t(http, name):\n    return getattr(http, name), vars(http)",  # a fixture, not the module
    "def t(monkeypatch):\n    monkeypatch.setattr('builtins.input', lambda prompt='': '1')",
]


@pytest.mark.parametrize("source", MUST_BE_FLAGGED)
def test_the_static_checks_flag_known_bypasses(source):
    assert _bypasses(source), source


@pytest.mark.parametrize("source", MAY_PASS)
def test_the_static_checks_allow_the_supported_forms(source):
    assert _bypasses(source) == []


def test_the_builtins_refused_as_patch_targets_are_those_db_safety_uses():
    # Read from db_safety's own source: a few it certainly uses, and the ones tests may patch.
    assert {"len", "isinstance", "sorted", "str", "set", "getattr", "Exception"} <= GUARD_BUILTINS
    assert not {"input", "open", "print"} & GUARD_BUILTINS


@pytest.mark.parametrize("source,missing", [
    ("@pytest.fixture\ndef pg(pg_database_url):\n    pass\ndef test_a(pg):\n    pass\ndef test_b():\n    pass", ["test_b"]),
    ("@pytest.fixture(autouse=True)\ndef pg(pg_gate):\n    pass\ndef test_a():\n    pass", []),
    ("@pytest.fixture\ndef a(b):\n    pass\n@pytest.fixture\ndef b(a):\n    pass\ndef test_loop(a):\n    pass", ["test_loop"]),
    ("class TestX:\n    def test_m(self, pg_database_url):\n        pass\n    def test_n(self):\n        pass", ["test_n"]),
    ("@pytest.mark.usefixtures('pg_gate')\ndef test_u():\n    pass\nasync def test_async():\n    pass", ["test_async"]),
    ("pytestmark = [pytest.mark.usefixtures('pg_database_url')]\ndef test_v():\n    pass", []),
    ("@pytest.mark.usefixtures('pg_gate')\nclass TestY:\n    def test_w(self):\n        pass", []),
])
def test_the_sandbox_reachability_check_itself(source, missing):
    tree = ast.parse(source)
    assert _tests_without_the_sandbox(tree, _import_names(tree)) == missing


def test_the_guard_files_exist_and_everything_else_is_checked():
    for name in GUARD_FILES:
        assert (TESTS / name).is_file(), "%s is excluded from the static checks but does not exist" % name
    checked = {path.name for path in _checked_files()}
    assert {path.name for path in TESTS.glob("test_*.py")} - checked == {"test_safety_net.py"}


def test_no_python_file_in_tests_goes_around_the_guard():
    found = []
    for path in _checked_files():
        found += ["%s: %s" % (path.relative_to(TESTS).as_posix(), problem)
                  for problem in _bypasses(path.read_text(encoding="utf-8"))]
    assert not found, "\n".join(found)


def test_destructive_sql_stays_in_the_sandbox_schema():
    found = []
    for path in _checked_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        sandbox = _is_sandbox_file(path, tree, _import_names(tree))
        docstrings = _docstrings(tree)
        for node in ast.walk(tree):
            value = _text(node)
            if value is None or value in NOT_SQL or id(node) in docstrings:
                continue
            sql = _sql_text(value)
            if re.search(r"\bDROP\s+(TABLE|VIEW|MATERIALIZED\s+VIEW|SEQUENCE|INDEX|TYPE)\b", sql, re.I) and not sandbox:
                found.append("%s drops objects but does not use the PostgreSQL sandbox" % path.name)
            if not sandbox:
                continue
            for statement in re.split(r";", sql):
                if not SQL_STATEMENT.match(statement):
                    continue
                found += ["%s runs dynamic SQL (%s)" % (path.name, rule)
                          for rule in SANDBOX_SQL_RULES if re.search(rule, statement, re.I)]
                match = QUALIFIED_STATEMENTS.match(statement.strip())
                if match:
                    targets = [target.strip() for target in match.group(1).split(",")]
                    if not all(target.lower().startswith("ft_test.") for target in targets):
                        found.append("%s changes a table that is not schema-qualified with ft_test (%s)"
                                     % (path.name, statement.strip()[:60]))
    assert not found, "\n".join(found)


def test_postgres_tests_go_through_the_gate():
    found = []
    for path in _checked_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = _import_names(tree)
        if not _is_sandbox_file(path, tree, names):
            continue
        if not _has_postgres_mark(tree, names):
            found.append("%s uses the sandbox but its pytestmark lacks pytest.mark.postgres" % path.name)
        found += ["%s::%s does not depend on pg_database_url or pg_gate" % (path.name, test)
                  for test in _tests_without_the_sandbox(tree, names)]
        for node in ast.walk(tree):
            dotted = _dotted(node, names) if isinstance(node, (ast.Name, ast.Attribute)) else None
            if dotted in ("os.environ", "os.getenv", "os.environb") or (dotted or "").startswith("os.environ."):
                found.append("%s reads the environment itself (%s)" % (path.name, dotted))
                break
    assert not found, "\n".join(found)


def test_no_file_in_tests_shadows_a_trusted_module():
    # tests/ is first on sys.path: tests/psycopg2.py or tests/dotenv/ would be imported instead
    # of the real package. The guard files themselves are the expected exceptions.
    expected = {TESTS / "conftest.py", TESTS / "db_safety.py", TESTS / "subprocess_guard" / "sitecustomize.py"}
    found = [path.relative_to(TESTS).as_posix() for path in TESTS.rglob("*")
             if "__pycache__" not in path.parts and path not in expected
             and (path.is_dir() or path.suffix in (".py", ".pyc", ".so", ".pth"))
             and path.name.split(".")[0] in TRUSTED_MODULE_NAMES]
    assert not found, "these files shadow trusted modules: %s" % ", ".join(found)
    assert not list(TESTS.rglob("*.pth")), "a .pth file in tests/ could run code at start-up"


def test_there_is_only_one_conftest_in_tests():
    # A nested conftest.py could define hooks that reset the session status or the guard.
    assert [path for path in TESTS.rglob("conftest.py") if "__pycache__" not in path.parts] == [TESTS / "conftest.py"]


def test_only_the_tests_folder_is_collected_by_default():
    # This does NOT cover a root script passed to pytest by path (`pytest migrate_to_v2.py`):
    # pytest imports explicitly named files whatever python_files says, and tests/conftest.py
    # is not even loaded then (see README).
    text = (ROOT / "pytest.ini").read_text(encoding="utf-8")
    assert re.search(r"^testpaths\s*=\s*tests\s*$", text, re.M), "root scripts such as migrate_to_v2.py must not be collected"
