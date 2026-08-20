import unittest
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from ui.main_window import MainWindow


class FakeControl:
    def __init__(self):
        self.configurations = []

    def configure(self, **kwargs):
        self.configurations.append(kwargs)

    @property
    def states(self):
        return [
            item["state"]
            for item in self.configurations
            if "state" in item
        ]


class FakeProgress:
    def __init__(self):
        self.value = None
        self.visible = False

    def grid(self):
        self.visible = True

    def grid_remove(self):
        self.visible = False

    def __setitem__(self, key, value):
        if key == "value":
            self.value = value


class FakeVar:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


class ImmediateRoot:
    def after(self, delay, callback):
        callback()


class DeferredThread:
    instances = []

    def __init__(self, target, daemon=None):
        self.target = target
        self.started = False
        self.__class__.instances.append(self)

    def start(self):
        self.started = True

    def run(self):
        self.target()


def make_window():
    window = MainWindow.__new__(MainWindow)
    window.adb = Mock()
    window.root = ImmediateRoot()
    window.device_combo = FakeControl()
    window.refresh_btn = FakeControl()
    window.status_var = FakeVar()
    window._current_device = "device-A"
    window._device_operation_count = 0
    return window


class DeviceBindingTests(unittest.TestCase):
    def setUp(self):
        DeferredThread.instances.clear()

    def test_single_download_keeps_the_starting_device_serial(self):
        window = make_window()
        window.adb.pull_file.return_value = True

        with TemporaryDirectory() as temp_dir, patch(
            "ui.main_window.filedialog.asksaveasfilename",
            return_value=f"{temp_dir}/report.txt",
        ), patch("ui.main_window.threading.Thread", DeferredThread):
            window._pull_file({
                "name": "report.txt",
                "path": "/sdcard/report.txt",
            })

            self.assertEqual(window.device_combo.states, ["disabled"])
            self.assertEqual(window.refresh_btn.states, ["disabled"])
            self.assertEqual(window._device_operation_count, 1)

            # Simulate an external or already-queued UI state change before the
            # background operation actually reaches ADB.
            window._current_device = "device-B"
            DeferredThread.instances[0].run()

        window.adb.pull_file.assert_called_once_with(
            "/sdcard/report.txt",
            f"{temp_dir}/report.txt",
            device_serial="device-A",
        )
        self.assertEqual(window.device_combo.states, ["disabled", "readonly"])
        self.assertEqual(window.refresh_btn.states, ["disabled", "normal"])
        self.assertEqual(window._device_operation_count, 0)

    def test_delete_updates_the_index_for_the_starting_device(self):
        window = make_window()
        window.adb.delete_file.return_value = True
        window.file_list = Mock()
        window.file_list.get_selected_files.return_value = [{
            "name": "old.txt",
            "path": "/sdcard/old.txt",
        }]
        window.search_engine = Mock()
        window._do_search = Mock()
        database = Mock()

        with patch(
            "ui.main_window.messagebox.askyesno",
            return_value=True,
        ), patch(
            "ui.main_window.threading.Thread",
            DeferredThread,
        ), patch("database.get_database", return_value=database):
            window._delete_selected()
            window._current_device = "device-B"
            DeferredThread.instances[0].run()

        window.adb.delete_file.assert_called_once_with(
            "/sdcard/old.txt",
            device_serial="device-A",
        )
        database.delete_files.assert_called_once_with(
            "device-A",
            ["/sdcard/old.txt"],
        )
        window._do_search.assert_not_called()
        self.assertEqual(window.device_combo.states, ["disabled", "readonly"])

    def test_indexing_callback_keeps_the_starting_device_serial(self):
        window = make_window()
        window.indexer = Mock()
        window.indexer.is_indexing = False
        window.indexer.index_device.return_value = True
        window.index_btn = FakeControl()
        window.progress = FakeProgress()
        window.count_var = FakeVar()
        window.search_engine = Mock()

        window._start_indexing()

        self.assertEqual(window.device_combo.states, ["disabled"])
        call = window.indexer.index_device.call_args
        self.assertEqual(call.args[0], "device-A")

        window._current_device = "device-B"
        call.kwargs["complete_callback"](25)

        window.search_engine.clear_cache.assert_called_once_with()
        self.assertEqual(
            window.status_var.value,
            "Indexed 25 files on device-A",
        )
        self.assertEqual(window.device_combo.states, ["disabled", "readonly"])
        self.assertFalse(window.progress.visible)

    def test_overlapping_operations_hold_the_selector_until_all_finish(self):
        window = make_window()

        window._begin_device_operation()
        window._begin_device_operation()
        window._end_device_operation()

        self.assertEqual(window._device_operation_count, 1)
        self.assertEqual(window.device_combo.states, ["disabled"])

        window._end_device_operation()

        self.assertEqual(window._device_operation_count, 0)
        self.assertEqual(window.device_combo.states, ["disabled", "readonly"])
        self.assertEqual(window.refresh_btn.states, ["disabled", "normal"])


if __name__ == "__main__":
    unittest.main()
