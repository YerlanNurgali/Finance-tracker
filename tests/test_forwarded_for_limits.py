"""Can a client change the address that the login and registration limits count?

The application is wrapped the way uvicorn wraps it when it starts:
``Config(...).load().loaded_app`` adds ``ProxyHeadersMiddleware`` (uvicorn's own code)
when ``proxy_headers`` is on. ``TestClient`` then calls the wrapped app in-process:
no server is started and no socket is opened.

Isolation:
- ``database.get_connection`` is replaced by a function that fails, so any database
  access that is not replaced fails the test;
- the two database functions the login and registration routes use are replaced by an
  in-memory store with fictitious credentials;
- the limiter's counters are reset before and after every test, and the logging state
  that ``Config`` changes is restored after every test.

Expected results:
- the controls and the secure configuration (default trusted address ``127.0.0.1``)
  pass;
- with ``forwarded_allow_ips="*"`` and ``TRUSTED_PROXY_HOPS=1``, the counted address is
  the one the proxy appends, so a client cannot change its counter with its own
  X-Forwarded-For entries.
- Not covered here: ``forwarded_allow_ips="*"`` without ``TRUSTED_PROXY_HOPS`` still lets
  the header choose the counted address. Production refuses that combination at start
  (``check_forwarded_allow_ips``, tested in test_security_config.py).
"""

import logging
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from limits.storage import MemoryStorage
from uvicorn.config import Config

import api as app_module
import database

# Documentation address ranges (RFC 5737): the addresses a client may claim, and the
# address a proxy would append as the last hop.
SPOOFED_PREFIX = "198.51.100."
PROXY_HOP = "203.0.113.9"

LOGIN_ATTEMPTS = 11        # the login limit is 10 per minute: the 11th attempt must be refused
REGISTRATIONS = 6          # the registration limit is 5 per minute: the 6th must be refused

def _login_body(i):
    # A different account on every attempt: only the address limit can refuse the request.
    return {"email": f"nobody{i}@example.test", "password": "Wrong-Password-1"}


def _client(forwarded_allow_ips):
    """The application as uvicorn serves it with this trusted-proxy setting."""
    config = Config(app_module.app, forwarded_allow_ips=forwarded_allow_ips)
    config.load()  # returns None; the wrapped application is stored on the config
    # No ``with``: the startup hook (which touches the database) is not run.
    return TestClient(config.loaded_app)


def _codes(client, path, body_for, forwarded_for, count):
    codes = []
    for i in range(count):
        response = client.post(path, json=body_for(i), headers={"X-Forwarded-For": forwarded_for(i)})
        codes.append(response.status_code)
    return codes


def _counter_keys(fragment):
    """Limiter keys that contain ``fragment`` (the address the limiter counted)."""
    storage = app_module.limiter._storage
    names = list(getattr(storage, "storage", {})) + list(getattr(storage, "events", {}))
    return sorted({str(name) for name in names if fragment in str(name)})


def _snapshot_logging():
    loggers = [logging.root] + [logger for logger in logging.root.manager.loggerDict.values()
                                if isinstance(logger, logging.Logger)]
    return {logger.name: (logger.level, list(logger.handlers), logger.propagate, logger.disabled)
            for logger in loggers}


def _restore_logging(saved):
    loggers = [logging.root] + [logger for logger in logging.root.manager.loggerDict.values()
                                if isinstance(logger, logging.Logger)]
    for logger in loggers:
        if logger.name in saved:
            level, handlers, propagate, disabled = saved[logger.name]
        else:  # created while the test ran
            level, handlers, propagate, disabled = logging.NOTSET, [], True, False
        logger.setLevel(level)
        logger.handlers[:] = handlers
        logger.propagate = propagate
        logger.disabled = disabled


class _InMemoryUsers:
    """Stands in for the users table: the two functions the authentication routes call."""

    def __init__(self):
        self.rows = {}
        self.next_id = 1

    def get_user_by_email(self, email):
        return self.rows.get(email.strip().lower())

    def create_user(self, email, password_hash):
        key = email.strip().lower()
        if key in self.rows:
            raise database.UserAlreadyExists("Пользователь с таким email уже существует")
        user_id = self.next_id
        self.rows[key] = (user_id, key, password_hash, "2026-01-01 00:00:00", 0)
        self.next_id += 1
        return user_id


def _no_database(*_args, **_kwargs):
    raise AssertionError("a database connection was requested: only the in-memory store may be used")


class ForwardedForLimitTests(unittest.TestCase):
    def setUp(self):
        saved = _snapshot_logging()
        self.addCleanup(_restore_logging, saved)

        self.assertIsInstance(app_module.limiter._storage, MemoryStorage,
                              "the limiter counters must be in memory for this test")
        app_module.limiter.reset()
        self.addCleanup(app_module.limiter.reset)

        users = _InMemoryUsers()
        for name, replacement in (("get_connection", _no_database),
                                  ("get_user_by_email", users.get_user_by_email),
                                  ("create_user", users.create_user)):
            patcher = mock.patch.object(database, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    # ── Controls: the limiter works ────────────────────────────────────────────

    def test_control_one_address_is_limited_with_wildcard_trust(self):
        codes = _codes(_client("*"), "/auth/login", _login_body,
                       lambda i: f"{SPOOFED_PREFIX}7, {PROXY_HOP}", LOGIN_ATTEMPTS)
        self.assertEqual(codes[:-1], [401] * (LOGIN_ATTEMPTS - 1), f"login statuses: {codes}")
        self.assertEqual(codes[-1], 429,
                         f"control failed: the limiter did not count one repeated address: {codes}")

    def test_control_one_address_is_limited_with_default_trust(self):
        codes = _codes(_client("127.0.0.1"), "/auth/login", _login_body,
                       lambda i: f"{SPOOFED_PREFIX}7, {PROXY_HOP}", LOGIN_ATTEMPTS)
        self.assertEqual(codes[:-1], [401] * (LOGIN_ATTEMPTS - 1), f"login statuses: {codes}")
        self.assertEqual(codes[-1], 429,
                         f"control failed: the limiter did not count repeated attempts: {codes}")

    # ── Secure configuration: the header is ignored ────────────────────────────

    def test_default_trust_ignores_spoofed_forwarded_for(self):
        codes = _codes(_client("127.0.0.1"), "/auth/login", _login_body,
                       lambda i: f"{SPOOFED_PREFIX}{i}, {PROXY_HOP}", LOGIN_ATTEMPTS)
        self.assertEqual(_counter_keys(SPOOFED_PREFIX), [],
                         "with the default trusted address the header must not be counted")
        self.assertEqual(codes[-1], 429,
                         f"changing X-Forwarded-For changed the login counter: {codes}")

    # ── Wildcard trust with TRUSTED_PROXY_HOPS=1: the proxy's address is counted ─

    def test_wildcard_trust_login_limit_resists_spoofed_forwarded_for(self):
        with mock.patch.object(app_module, "TRUSTED_PROXY_HOPS", 1):
            codes = _codes(_client("*"), "/auth/login", _login_body,
                           lambda i: f"{SPOOFED_PREFIX}{i}, {PROXY_HOP}", LOGIN_ATTEMPTS)
        self.assertEqual(_counter_keys(SPOOFED_PREFIX), [], "a client-chosen address was counted")
        self.assertTrue(_counter_keys(PROXY_HOP), "the proxy-appended address was not counted")
        self.assertEqual(codes[-1], 429,
                         f"changing X-Forwarded-For let attempt {LOGIN_ATTEMPTS} through: {codes}")

    def test_wildcard_trust_registration_limit_resists_spoofed_forwarded_for(self):
        with mock.patch.object(app_module, "TRUSTED_PROXY_HOPS", 1):
            codes = _codes(_client("*"),
                           "/auth/register",
                           lambda i: {"email": f"user{i}@example.test", "password": "Secret123!"},
                           lambda i: f"{SPOOFED_PREFIX}{i}, {PROXY_HOP}", REGISTRATIONS)
        self.assertEqual(_counter_keys(SPOOFED_PREFIX), [], "a client-chosen address was counted")
        self.assertTrue(_counter_keys(PROXY_HOP), "the proxy-appended address was not counted")
        self.assertEqual(codes[-1], 429,
                         f"changing X-Forwarded-For let registration {REGISTRATIONS} through: {codes}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
