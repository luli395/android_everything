import shlex
import sys
import unittest
from unittest.mock import patch

from adb_wrapper import ADBError, ADBWrapper, STORAGE_PATH_MARKER


class StoragePathTests(unittest.TestCase):
    def setUp(self):
        self.adb = ADBWrapper(adb_path=sys.executable)

    def test_common_internal_and_sd_card_aliases_are_deduplicated(self):
        def shell(command, *, device_serial):
            self.assertEqual(device_serial, "device-1")
            if command == "ls -d /storage/*/ 2>/dev/null":
                return "/storage/emulated/\n/storage/ABCD-1234/\n"
            if command == "ls -d /mnt/media_rw/*/ 2>/dev/null":
                return "/mnt/media_rw/ABCD-1234/\n"
            if command == "ls -d /mnt/sdcard 2>/dev/null":
                return "/mnt/sdcard\n"
            if command == "ls -d /mnt/extSdCard 2>/dev/null":
                return ""
            if command == "ls -d /mnt/usb_storage/*/ 2>/dev/null":
                return ""
            if command == "ls -d /data/media/0 2>/dev/null":
                return "/data/media/0\n"
            if command == "echo $EXTERNAL_STORAGE":
                return "/sdcard\n"
            if command == "echo $SECONDARY_STORAGE":
                return "/storage/ABCD-1234:/mnt/media_rw/ABCD-1234\n"
            if STORAGE_PATH_MARKER in command:
                return "\n".join([
                    f"{STORAGE_PATH_MARKER}/data/media/0\t/data/media/0\t1:10",
                    f"{STORAGE_PATH_MARKER}/mnt/media_rw/ABCD-1234\t/mnt/media_rw/ABCD-1234\t2:20",
                    f"{STORAGE_PATH_MARKER}/mnt/sdcard\t/storage/emulated/0\t1:10",
                    f"{STORAGE_PATH_MARKER}/sdcard\t/storage/emulated/0\t1:10",
                    f"{STORAGE_PATH_MARKER}/storage/ABCD-1234\t/storage/ABCD-1234\t3:30",
                    f"{STORAGE_PATH_MARKER}/storage/emulated/0\t/storage/emulated/0\t4:40",
                ])
            self.fail(f"Unexpected shell command: {command}")

        with patch.object(self.adb, "shell", side_effect=shell):
            paths = self.adb.get_storage_paths(device_serial="device-1")

        self.assertEqual(
            paths,
            ["/storage/emulated/0", "/storage/ABCD-1234"],
        )

    def test_generic_symlink_alias_prefers_standard_storage_mount(self):
        probe_output = "\n".join([
            f"{STORAGE_PATH_MARKER}/legacy/card\t/storage/ABCD-1234\t8:80",
            f"{STORAGE_PATH_MARKER}/storage/ABCD-1234\t/storage/ABCD-1234\t8:80",
        ])

        with patch.object(self.adb, "shell", return_value=probe_output):
            paths = self.adb._deduplicate_storage_paths(
                ["/legacy/card", "/storage/ABCD-1234", "/missing"],
                device_serial="device-1",
            )

        self.assertEqual(paths, ["/storage/ABCD-1234"])

    def test_inaccessible_candidates_are_not_returned(self):
        probe_output = (
            f"{STORAGE_PATH_MARKER}/storage/emulated/0\t"
            "/storage/emulated/0\t1:1\n"
        )

        with patch.object(self.adb, "shell", return_value=probe_output):
            paths = self.adb._deduplicate_storage_paths(
                ["/storage/emulated/0", "/mnt/media_rw/DEAD-BEEF"],
                device_serial="device-1",
            )

        self.assertEqual(paths, ["/storage/emulated/0"])

    def test_filesystem_identity_connects_legacy_and_standard_mounts(self):
        probe_output = "\n".join([
            f"{STORAGE_PATH_MARKER}/mnt/extSdCard\t/mnt/runtime/write/card\t8:80",
            f"{STORAGE_PATH_MARKER}/storage/ABCD-1234\t/mnt/runtime/default/card\t8:80",
        ])

        with patch.object(self.adb, "shell", return_value=probe_output):
            paths = self.adb._deduplicate_storage_paths(
                ["/mnt/extSdCard", "/storage/ABCD-1234"],
                device_serial="device-1",
            )

        self.assertEqual(paths, ["/storage/ABCD-1234"])

    def test_probe_failure_still_collapses_well_known_aliases(self):
        with patch.object(
            self.adb,
            "shell",
            side_effect=ADBError("readlink unavailable"),
        ):
            paths = self.adb._deduplicate_storage_paths(
                [
                    "/sdcard",
                    "/storage/emulated/0",
                    "/mnt/media_rw/ABCD-1234",
                    "/storage/ABCD-1234",
                ],
                device_serial="device-1",
            )

        self.assertEqual(
            paths,
            ["/storage/emulated/0", "/storage/ABCD-1234"],
        )

    def test_probe_quotes_candidate_paths_and_returns_deterministic_order(self):
        commands = []
        unusual_path = "/storage/card's files"
        probe_output = "\n".join([
            f"{STORAGE_PATH_MARKER}{unusual_path}\t{unusual_path}\t5:50",
            f"{STORAGE_PATH_MARKER}/storage/emulated/0\t/storage/emulated/0\t1:10",
        ])

        def shell(command, *, device_serial):
            commands.append(command)
            return probe_output

        with patch.object(self.adb, "shell", side_effect=shell):
            paths = self.adb._deduplicate_storage_paths(
                [unusual_path, "/storage/emulated/0"],
                device_serial="device-1",
            )

        self.assertIn(shlex.quote(unusual_path), commands[0])
        self.assertEqual(
            paths,
            ["/storage/emulated/0", unusual_path],
        )


if __name__ == "__main__":
    unittest.main()
