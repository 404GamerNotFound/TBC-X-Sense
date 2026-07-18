import base64
import unittest

from xsense_api import APP_CODE, XSenseClient, _ipc_node_type, _mac_scalar


class MacScalarTests(unittest.TestCase):
    def test_none_becomes_literal_null(self):
        self.assertEqual(_mac_scalar(None), "null")

    def test_booleans_become_lowercase_literals(self):
        self.assertEqual(_mac_scalar(True), "true")
        self.assertEqual(_mac_scalar(False), "false")

    def test_strings_and_numbers_pass_through_str(self):
        self.assertEqual(_mac_scalar("abc"), "abc")
        self.assertEqual(_mac_scalar(42), "42")


class IpcNodeTypeTests(unittest.TestCase):
    def test_known_regions_are_used_directly(self):
        self.assertEqual(_ipc_node_type("eu-central-1"), "EU")
        self.assertEqual(_ipc_node_type("cn-north-1"), "CN")
        self.assertEqual(_ipc_node_type("us-east-1"), "US")

    def test_unknown_or_missing_region_falls_back_to_us(self):
        self.assertEqual(_ipc_node_type(None), "US")
        self.assertEqual(_ipc_node_type(""), "US")
        self.assertEqual(_ipc_node_type("xx-somewhere-1"), "US")


class DecodeSecretTests(unittest.TestCase):
    def test_strips_app_code_length_prefix_and_trailing_byte(self):
        client = XSenseClient("user@example.com", "secret")
        real_secret = b"actual-client-secret"
        # Mirrors the encoder: a prefix exactly len(APP_CODE) bytes long,
        # then the real secret, then one trailing byte that gets dropped.
        encoded = base64.b64encode(b"X" * len(APP_CODE) + real_secret + b"Z").decode()

        decoded = client._decode_secret(encoded)

        self.assertEqual(decoded, real_secret)


class CalculateMacTests(unittest.TestCase):
    def test_mac_is_deterministic_and_order_dependent(self):
        client = XSenseClient("user@example.com", "secret")
        client._client_secret = b"shared-secret"

        first = client._calculate_mac({"a": "1", "b": "2"})
        second = client._calculate_mac({"a": "1", "b": "2"})
        different_order = client._calculate_mac({"b": "2", "a": "1"})

        self.assertEqual(first, second)
        self.assertEqual(len(first), 32)  # md5 hex digest length
        self.assertNotEqual(first, different_order)


if __name__ == "__main__":
    unittest.main()
