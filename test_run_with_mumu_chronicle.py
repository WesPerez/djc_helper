import xml.etree.ElementTree as ET
from unittest import TestCase, mock

import run_with_mumu_chronicle as chronicle


def ui_root(*texts):
    root = ET.Element("hierarchy")
    for text in texts:
        ET.SubElement(root, "node", text=text)
    return root


class ChronicleStartupTests(TestCase):
    def test_dismiss_startup_update_dialog_uses_android_back(self):
        roots = iter((ui_root("版本更新", "取消"), ui_root("DNF端游")))

        with mock.patch.object(chronicle, "dump_ui", side_effect=lambda *args, **kwargs: next(roots)):
            with mock.patch.object(chronicle, "back") as back_mock:
                self.assertTrue(chronicle.dismiss_startup_update_dialog("cli", "0"))

        back_mock.assert_called_once_with("cli", "0", count=1, delay=2)

    def test_dismiss_startup_update_dialog_is_noop_without_prompt(self):
        with mock.patch.object(chronicle, "dump_ui", return_value=ui_root("DNF端游")):
            with mock.patch.object(chronicle, "back") as back_mock:
                self.assertFalse(chronicle.dismiss_startup_update_dialog("cli", "0"))

        back_mock.assert_not_called()

    def test_dismiss_startup_update_dialog_fails_closed_when_ui_unavailable(self):
        with mock.patch.object(chronicle, "dump_ui", side_effect=RuntimeError("dump failed")):
            with self.assertRaisesRegex(RuntimeError, "避免误触升级"):
                chronicle.dismiss_startup_update_dialog("cli", "0")

    def test_dismiss_startup_update_dialog_requires_post_close_verification(self):
        with mock.patch.object(
            chronicle,
            "dump_ui",
            side_effect=(ui_root("版本更新"), RuntimeError("dump failed")),
        ):
            with mock.patch.object(chronicle, "back"):
                with self.assertRaisesRegex(RuntimeError, "无法确认页面状态"):
                    chronicle.dismiss_startup_update_dialog("cli", "0")

    def test_finish_claim_dialogs_continues_after_dismissing_update(self):
        with mock.patch.object(chronicle.time, "sleep"):
            with mock.patch.object(
                chronicle,
                "dismiss_startup_update_dialog",
                return_value=True,
            ):
                with mock.patch.object(chronicle, "tap_fraction") as tap_mock:
                    chronicle.finish_claim_dialogs("cli", "0", 1080, 1920)

        self.assertEqual(
            tap_mock.call_args_list,
            [
                mock.call("cli", "0", 1080, 1920, 0.668, 0.584),
                mock.call("cli", "0", 1080, 1920, 0.5, 0.595),
            ],
        )

    def test_parse_app_state_accepts_clean_and_prefixed_json(self):
        self.assertEqual(chronicle.parse_app_state('{"state": "running"}'), "running")
        self.assertEqual(
            chronicle.parse_app_state('warning before json {"state": "stopped"}'),
            "stopped",
        )
        self.assertIsNone(chronicle.parse_app_state("not json"))

    def test_open_dnf_home_fresh_falls_back_to_mumu_launch(self):
        with mock.patch.object(chronicle, "force_stop_app"):
            with mock.patch.object(chronicle, "adb_shell", return_value="Starting activity"):
                with mock.patch.object(
                    chronicle,
                    "wait_for_app_running",
                    return_value=(False, '{"state": "stopped"}'),
                ):
                    with mock.patch.object(chronicle, "launch_dnf_helper") as launch_mock:
                        with mock.patch.object(
                            chronicle,
                            "dismiss_startup_update_dialog",
                        ) as dismiss_mock:
                            with mock.patch.object(chronicle.time, "sleep"):
                                chronicle.open_dnf_home_fresh("cli", "0")

        launch_mock.assert_called_once_with("cli", "0")
        dismiss_mock.assert_called_once_with("cli", "0")
