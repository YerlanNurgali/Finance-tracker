"""Startup configuration and the client address the rate limits count. No database and no network.

The values come from fake environments and fake requests. Nothing here reads the real .env or the Render
settings, and no secret value is printed by these tests.
"""

import unittest
from unittest import mock

from starlette.requests import Request

import api as app_module
import auth

RENDER = {"RENDER": "true"}


def _request(headers=None, peer="10.0.0.5"):
    raw = [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in (headers or {}).items()]
    scope = {"type": "http", "method": "POST", "path": "/auth/login", "query_string": b"",
             "headers": raw, "client": (peer, 40000)}
    return Request(scope)


class SecretKeyTests(unittest.TestCase):
    def test_on_render_a_missing_key_stops_the_start(self):
        with self.assertRaises(auth.SecretKeyError):
            auth.load_secret_key(dict(RENDER))

    def test_on_render_an_empty_or_blank_key_counts_as_missing(self):
        for value in ["", "   "]:
            with self.subTest(value=value), self.assertRaises(auth.SecretKeyError):
                auth.load_secret_key({**RENDER, "SECRET_KEY": value})

    def test_on_render_a_short_key_stops_the_start(self):
        with self.assertRaises(auth.SecretKeyError):
            auth.load_secret_key({**RENDER, "SECRET_KEY": "x" * (auth.MIN_SECRET_KEY_LENGTH - 1)})

    def test_on_render_a_long_key_is_used_as_it_is(self):
        key = "k" * auth.MIN_SECRET_KEY_LENGTH
        self.assertEqual(auth.load_secret_key({**RENDER, "SECRET_KEY": key}), (key, True))

    def test_the_error_never_contains_the_value(self):
        short = "shortvalue-" + "z" * 5
        with self.assertRaises(auth.SecretKeyError) as raised:
            auth.load_secret_key({**RENDER, "SECRET_KEY": short})
        self.assertNotIn(short, str(raised.exception))

    def test_outside_render_a_missing_key_gets_a_random_one_for_this_process(self):
        key, configured = auth.load_secret_key({})
        self.assertFalse(configured)
        self.assertEqual(len(key), 64)
        other, _ = auth.load_secret_key({})
        self.assertNotEqual(key, other)

    def test_outside_render_a_given_key_is_used(self):
        self.assertEqual(auth.load_secret_key({"SECRET_KEY": "local-dev-key"}), ("local-dev-key", True))

    def test_render_false_is_not_production(self):
        key, configured = auth.load_secret_key({"RENDER": "false"})
        self.assertFalse(configured)


class ForwardedAllowIpsTests(unittest.TestCase):
    def test_on_render_the_wildcard_is_refused(self):
        with self.assertRaises(RuntimeError):
            app_module.check_forwarded_allow_ips({**RENDER, "FORWARDED_ALLOW_IPS": "*"})

    def test_on_render_a_wildcard_inside_a_list_is_refused(self):
        with self.assertRaises(RuntimeError):
            app_module.check_forwarded_allow_ips({**RENDER, "FORWARDED_ALLOW_IPS": "127.0.0.1, *"})

    def test_on_render_the_local_address_is_accepted(self):
        app_module.check_forwarded_allow_ips({**RENDER, "FORWARDED_ALLOW_IPS": "127.0.0.1"})
        app_module.check_forwarded_allow_ips({**RENDER})

    def test_outside_render_the_wildcard_is_not_checked(self):
        app_module.check_forwarded_allow_ips({"FORWARDED_ALLOW_IPS": "*"})


class TrustedProxyHopsTests(unittest.TestCase):
    def test_an_empty_value_means_no_trust(self):
        self.assertEqual(app_module.parse_trusted_proxy_hops(None), 0)
        self.assertEqual(app_module.parse_trusted_proxy_hops("  "), 0)

    def test_a_whole_number_in_range_is_accepted(self):
        self.assertEqual(app_module.parse_trusted_proxy_hops("1"), 1)
        self.assertEqual(app_module.parse_trusted_proxy_hops(" 2 "), 2)

    def test_other_values_are_refused(self):
        for raw in ["x", "1.5", "-1", "6", "*"]:
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                app_module.parse_trusted_proxy_hops(raw)


class ClientIpTests(unittest.TestCase):
    def test_by_default_the_forwarded_header_is_ignored(self):
        with mock.patch.object(app_module, "TRUSTED_PROXY_HOPS", 0):
            request = _request({"X-Forwarded-For": "1.2.3.4"}, peer="10.0.0.5")
            self.assertEqual(app_module.client_ip(request), "10.0.0.5")

    def test_with_one_trusted_proxy_the_address_appended_last_is_used(self):
        with mock.patch.object(app_module, "TRUSTED_PROXY_HOPS", 1):
            request = _request({"X-Forwarded-For": "6.6.6.6, 203.0.113.9"}, peer="10.0.0.5")
            self.assertEqual(app_module.client_ip(request), "203.0.113.9")

    def test_a_client_chosen_leftmost_value_does_not_change_the_address(self):
        with mock.patch.object(app_module, "TRUSTED_PROXY_HOPS", 1):
            first = _request({"X-Forwarded-For": "6.6.6.1, 203.0.113.9"})
            second = _request({"X-Forwarded-For": "6.6.6.2, 203.0.113.9"})
            self.assertEqual(app_module.client_ip(first), app_module.client_ip(second))

    def test_a_missing_or_malformed_header_falls_back_to_the_connection(self):
        with mock.patch.object(app_module, "TRUSTED_PROXY_HOPS", 1):
            self.assertEqual(app_module.client_ip(_request({}, peer="10.0.0.5")), "10.0.0.5")
            self.assertEqual(app_module.client_ip(_request({"X-Forwarded-For": "not-an-ip"}, peer="10.0.0.5")), "10.0.0.5")

    def test_an_ipv6_address_is_normalised_so_spellings_cannot_split_a_counter(self):
        with mock.patch.object(app_module, "TRUSTED_PROXY_HOPS", 1):
            a = app_module.client_ip(_request({"X-Forwarded-For": "2001:DB8:0:0::1"}))
            b = app_module.client_ip(_request({"X-Forwarded-For": "2001:db8::1"}))
            self.assertEqual(a, b)

    def test_an_ipv4_address_is_counted_as_it_is(self):
        with mock.patch.object(app_module, "TRUSTED_PROXY_HOPS", 1):
            self.assertEqual(app_module.client_ip(_request({"X-Forwarded-For": "203.0.113.9"})), "203.0.113.9")

    def test_an_ipv6_address_is_counted_in_its_compact_lower_case_form(self):
        with mock.patch.object(app_module, "TRUSTED_PROXY_HOPS", 1):
            self.assertEqual(app_module.client_ip(_request({"X-Forwarded-For": "2001:0DB8:0000::0001"})), "2001:db8::1")
            self.assertEqual(app_module.client_ip(_request({"X-Forwarded-For": "::1"})), "::1")

    def test_an_ipv4_mapped_ipv6_gets_the_same_key_as_the_ipv4_address(self):
        with mock.patch.object(app_module, "TRUSTED_PROXY_HOPS", 1):
            mapped = app_module.client_ip(_request({"X-Forwarded-For": "::ffff:203.0.113.9"}))
            plain = app_module.client_ip(_request({"X-Forwarded-For": "203.0.113.9"}))
            self.assertEqual(mapped, "203.0.113.9")
            self.assertEqual(mapped, plain)

    def test_an_ipv4_mapped_connection_address_gets_the_same_key_as_the_ipv4_address(self):
        with mock.patch.object(app_module, "TRUSTED_PROXY_HOPS", 0):
            self.assertEqual(app_module.client_ip(_request({}, peer="::ffff:10.0.0.5")), "10.0.0.5")
            self.assertEqual(app_module.client_ip(_request({}, peer="10.0.0.5")), "10.0.0.5")

    def test_a_connection_name_that_is_not_an_address_is_kept(self):
        with mock.patch.object(app_module, "TRUSTED_PROXY_HOPS", 0):
            self.assertEqual(app_module.client_ip(_request({}, peer="testclient")), "testclient")


if __name__ == "__main__":
    unittest.main(verbosity=2)
