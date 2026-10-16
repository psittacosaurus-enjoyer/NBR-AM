"""Isolated BlueStacks fixtures; no installed emulator or ADB is contacted."""
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bluestacks


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.conf = self.root / "custom data" / "bluestacks.conf"
        self.conf.parent.mkdir()
        self.adb = self.root / "custom install" / "HD-Adb.exe"
        self.adb.parent.mkdir()
        self.adb.touch()
        self.sources = patch.object(bluestacks, "_configuration_sources", return_value=[(str(self.conf), str(self.adb))])
        self.sources.start()
        self.addCleanup(self.sources.stop)
        self.addCleanup(patch.stopall)
        patch.object(bluestacks, "_registry_locations", return_value=[]).start()
        self.states = patch.object(bluestacks, "_device_states", return_value={}).start()
        self.identity = patch.object(bluestacks, "_android_identity", side_effect=lambda endpoint: {
            "127.0.0.1:6101": "id-one", "127.0.0.1:6237": "id-two"}[endpoint]).start()
        patch.object(bluestacks, "_running_instances", return_value=set()).start()
        self.process = patch.object(bluestacks.subprocess, "run", side_effect=AssertionError("Monitoring must not run ADB")).start()
        self.socket = patch.object(bluestacks.socket, "create_connection", side_effect=AssertionError("Unexpected socket access")).start()

    def config(self, instances=(("PieCustom", "A name chosen by the user", 6101, "id-one"),)):
        self.conf.write_text("\n".join(f'bst.instance.{internal}.{key}="{value}"'
                                     for internal, name, port, identity in instances
                                     for key, value in (("display_name", name), ("adb_port", port), ("android_id", identity))),
                             encoding="utf-8")

    def instance(self):
        return bluestacks.discover()["instances"][0]

    def test_one_instance_preserves_distinct_name_id_endpoint(self):
        self.config()
        self.states.return_value = {"127.0.0.1:6101": "device"}
        instance = self.instance()
        self.assertEqual(instance.display_name, "A name chosen by the user")
        self.assertEqual(instance.instance_id, "PieCustom")
        self.assertEqual(instance.endpoint, "127.0.0.1:6101")
        self.assertEqual(instance.adb_path, str(self.adb))
        self.assertEqual(instance.status, "running")
        self.assertEqual(instance.connection_status, "connected")
        self.process.assert_not_called()
        self.socket.assert_not_called()

    def test_multiple_instances_and_custom_ports(self):
        self.config((("Nougat64_3", "My second device", 6237, "id-two"),
                     ("PieCustom", "My first device", 6101, "id-one")))
        self.states.return_value = {"127.0.0.1:6101": "device"}
        instances = bluestacks.discover()["instances"]
        self.assertEqual(len(instances), 2)
        self.assertEqual({item.instance_id for item in instances}, {"Nougat64_3", "PieCustom"})
        self.assertEqual({item.endpoint for item in instances}, {"127.0.0.1:6101", "127.0.0.1:6237"})
        self.assertEqual({item.status for item in instances}, {"running", "stopped"})

    def test_unavailable_server_is_passive_and_reports_stopped(self):
        self.config()
        self.states.side_effect = RuntimeError("ADB server is unavailable")
        instance = self.instance()
        self.assertEqual(instance.status, "stopped")
        self.assertEqual(instance.connection_status, "disconnected")
        self.assertIn("unavailable", instance.detail)
        self.process.assert_not_called()

    def test_running_but_disconnected_uses_exact_player_process(self):
        self.config()
        with patch.object(bluestacks, "_running_instances", return_value={
                (os.path.normcase(str(self.adb.parent.resolve())), "PieCustom")}):
            instance = self.instance()
        self.assertEqual(instance.status, "running")
        self.assertEqual(instance.connection_status, "disconnected")
        self.identity.assert_not_called()

    def test_unknown_process_state_reports_unavailable(self):
        self.config()
        with patch.object(bluestacks, "_running_instances", return_value=None):
            self.assertEqual(self.instance().status, "unavailable")

    def test_missing_configuration_and_installation_are_graceful(self):
        self.assertEqual(bluestacks.discover()["instances"], [])
        with patch.object(bluestacks, "_configuration_sources", return_value=[]):
            self.assertIn("not found", bluestacks.discover()["detail"])

    def test_missing_android_id_is_not_trusted_on_first_use(self):
        self.config((("PieCustom", "No identity", 6101, ""),))
        instance = self.instance()
        self.assertEqual(instance.status, "unavailable")
        with self.assertRaisesRegex(RuntimeError, "verifiable identity"):
            bluestacks.verify_identity(instance)
        self.identity.assert_not_called()

    def test_missing_adb_executable_is_unavailable(self):
        self.config()
        self.adb.unlink()
        self.assertEqual(self.instance().status, "unavailable")

    def test_identity_mismatch_fails_closed(self):
        self.config()
        self.states.return_value = {"127.0.0.1:6101": "device"}
        self.identity.side_effect = None
        self.identity.return_value = "another-device"
        instance = self.instance()
        self.assertEqual(instance.connection_status, "unverified")
        with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
            bluestacks.verify_identity(instance)

    def test_stable_saved_selection_allows_friendly_rename(self):
        self.config()
        instance = self.instance()
        self.config((("PieCustom", "Renamed device", 6101, "id-one"),))
        renamed = self.instance()
        self.assertEqual(instance.key, renamed.key)
        self.assertEqual(bluestacks.Instance.from_dict(instance.to_dict()), instance)
        self.assertEqual(instance.to_dict()["key"], instance.key)
        bluestacks.validate_mapping(instance)

    def test_changed_port_or_identity_invalidates_saved_selection(self):
        self.config()
        instance = self.instance()
        for port, identity in ((6237, "id-one"), (6101, "id-two")):
            self.config((("PieCustom", "Same name", port, identity),))
            with self.assertRaisesRegex(RuntimeError, "mapping or Android identity changed"):
                bluestacks.validate_mapping(instance)

    def test_duplicate_port_mapping_is_unavailable_and_rejected(self):
        self.config((("One", "First", 6101, "id-one"), ("Two", "Second", 6101, "id-two")))
        instances = bluestacks.discover()["instances"]
        self.assertTrue(all(item.status == "unavailable" for item in instances))
        with self.assertRaisesRegex(RuntimeError, "multiple instances"):
            bluestacks.validate_mapping(instances[0])

    def test_duplicate_endpoint_across_configuration_files_is_rejected(self):
        self.config()
        other = self.root / "another.conf"
        other.write_text(self.conf.read_text().replace("PieCustom", "AnotherId"))
        with patch.object(bluestacks, "_configuration_sources", return_value=[(str(self.conf), str(self.adb)), (str(other), str(self.adb))]):
            instances = bluestacks.discover()["instances"]
            self.assertTrue(all(item.status == "unavailable" for item in instances))
            with self.assertRaisesRegex(RuntimeError, "Multiple BlueStacks configurations"):
                bluestacks.verify_identity(instances[0])

    def test_manual_resolves_internal_id_and_checks_endpoint(self):
        self.config()
        self.states.return_value = {"127.0.0.1:6101": "device"}
        instance = bluestacks.resolve_manual("PieCustom", "localhost:6101")
        self.assertEqual(instance.instance_id, "PieCustom")
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            bluestacks.resolve_manual("PieCustom", "127.0.0.1:6237")

    def test_manual_ambiguous_names_and_unknown_names_are_rejected(self):
        self.config((("One", "Duplicate", 6101, "id-one"), ("Two", "Duplicate", 6237, "id-two")))
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            bluestacks.resolve_manual("Duplicate", "127.0.0.1:6101")
        with self.assertRaisesRegex(RuntimeError, "No configured"):
            bluestacks.resolve_manual("Not this device", "127.0.0.1:6101")

    def test_manual_rejects_disconnected_and_invalid_endpoints(self):
        self.config()
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            bluestacks.resolve_manual("PieCustom")
        for endpoint in ("192.168.1.4:6101", "127.0.0.1:0", "localhost:99999", "127.0.0.999:6101", "serial", "127.0.0.1:6101;bad"):
            with self.subTest(endpoint=endpoint), self.assertRaises(RuntimeError):
                bluestacks.resolve_manual("PieCustom", endpoint)

    def test_advanced_manual_requires_bluestacks_evidence(self):
        with patch.object(bluestacks, "_configuration_sources", return_value=[]), \
                patch.object(bluestacks, "_is_bluestacks", return_value=False):
            self.states.return_value = {"127.0.0.1:6101": "device"}
            with self.assertRaisesRegex(RuntimeError, "cannot be verified as BlueStacks"):
                bluestacks.resolve_manual("My emulator", "localhost:6101")
            self.identity.assert_not_called()

    def test_advanced_manual_pins_identity_and_rechecks_vendor(self):
        with patch.object(bluestacks, "_configuration_sources", return_value=[]), \
                patch.object(bluestacks, "_registry_locations", return_value=[{"InstallDir": str(self.adb.parent)}]), \
                patch.object(bluestacks, "_is_bluestacks", return_value=True) as vendor, \
                patch.dict(os.environ, {"ProgramFiles": "", "ProgramFiles(x86)": ""}):
            self.states.return_value = {"127.0.0.1:6101": "device"}
            instance = bluestacks.resolve_manual("My emulator", "localhost:6101")
            self.assertEqual(instance.instance_id, "manual@127.0.0.1:6101")
            self.assertEqual(instance.android_id, "id-one")
            self.assertEqual(instance.config_path, "")
            self.assertGreaterEqual(vendor.call_count, 2)
            self.identity.side_effect = None
            self.identity.return_value = "changed"
            with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
                bluestacks.verify_identity(instance)

    def test_custom_registry_directories_resolve_configuration_and_adb(self):
        self.config()
        self.sources.stop()
        with patch.object(bluestacks, "_registry_locations", return_value=[{
                "UserDefinedDir": str(self.conf.parent), "DataDir": str(self.conf.parent / "Engine"),
                "InstallDir": str(self.adb.parent)}]), \
                patch.dict(os.environ, {"ProgramData": "", "ProgramFiles": "", "ProgramFiles(x86)": "", "NBR_BLUESTACKS_CONFIG": ""}):
            sources = bluestacks._configuration_sources()
        self.assertEqual(sources, [(str(self.conf.resolve()), str(self.adb.resolve()))])

    def test_config_duplicate_values_are_rejected(self):
        self.config()
        with self.conf.open("a") as config:
            config.write('\nbst.instance.PieCustom.adb_port="6237"')
        result = bluestacks.discover()
        self.assertEqual(result["instances"], [])
        self.assertIn("conflicting values", result["detail"])

    def test_inaccessible_standard_directories_are_graceful(self):
        with patch.object(Path, "glob", side_effect=PermissionError("denied")):
            self.assertEqual(bluestacks._safe_glob(self.root, "BlueStacks*"), [])

    def test_other_installation_process_is_not_this_instance(self):
        self.config()
        with patch.object(bluestacks, "_running_instances", return_value={
                (os.path.normcase(str((self.root / "other install").resolve())), "PieCustom")}):
            self.assertEqual(self.instance().status, "stopped")


class ProtocolTests(unittest.TestCase):
    class Socket:
        def __init__(self, payload):
            self.payload = payload
            self.sent = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def sendall(self, data):
            self.sent.append(data)

        def recv(self, length):
            data, self.payload = self.payload[:length], self.payload[length:]
            return data

    def test_device_listing_uses_existing_server_protocol_only(self):
        payload = b"127.0.0.1:6101\tdevice\nUSB123\tdevice\n192.168.1.2:6101\tdevice\n"
        sock = self.Socket(b"OKAY" + f"{len(payload):04x}".encode() + payload)
        with patch.object(bluestacks.socket, "create_connection", return_value=sock) as connect, \
                patch.object(bluestacks.subprocess, "run", side_effect=AssertionError("Must not launch ADB")), \
                patch.dict(os.environ, {"ANDROID_ADB_SERVER_PORT": "5040"}):
            states = bluestacks._device_states()
        self.assertEqual(states, {"127.0.0.1:6101": "device"})
        connect.assert_called_once_with(("127.0.0.1", 5040), timeout=1.5)
        self.assertEqual(sock.sent, [b"000chost:devices"])

    def test_identity_shell_is_read_only_and_targets_exact_endpoint(self):
        sock = self.Socket(b"OKAYOKAYid-one\r\n")
        with patch.object(bluestacks.socket, "create_connection", return_value=sock):
            self.assertEqual(bluestacks._android_identity("127.0.0.1:6237"), "id-one")
        services = [part[4:] for part in sock.sent]
        self.assertEqual(services, [b"host:transport:127.0.0.1:6237", b"shell:settings get secure android_id"])

    def test_generic_emulator_properties_do_not_identify_bluestacks(self):
        for props, expected in (("[ro.hardware]: [ranchu]\n[ro.product.manufacturer]: [samsung]", False),
                                ("[ro.hardware]: [BlueStacks]", True),
                                ("[persist.sys.bluestacks.version]: [5.0]", True)):
            with self.subTest(props=props), patch.object(bluestacks, "_adb_request", return_value=props):
                self.assertEqual(bluestacks._is_bluestacks("127.0.0.1:6101"), expected)

    def test_player_process_handles_quoted_windows_arguments(self):
        for command in ('"C:\\custom install\\HD-Player.exe" "--instance" "PieCustom"',
                        'HD-Player.exe --instance PieCustom', 'HD-Player.exe --instance="PieCustom"'):
            self.assertEqual(bluestacks._player_instance(command), "PieCustom")
        for command in ('HD-Player.exe', 'HD-Player.exe --instance One --instance Two'):
            self.assertEqual(bluestacks._player_instance(command), "")


if __name__ == "__main__":
    unittest.main()
