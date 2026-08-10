import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from unittest import TestCase, mock

import run_with_mumu_chronicle as chronicle


def ui_root(*texts):
    root = ET.Element("hierarchy")
    for text in texts:
        ET.SubElement(root, "node", text=text)
    return root


class ChronicleStartupTests(TestCase):
    def test_run_chronicle_app_tasks_does_not_prelaunch_app_without_tasks(self):
        with mock.patch.object(chronicle, "launch_dnf_helper") as launch_mock:
            with mock.patch.object(chronicle, "query_screen_size", side_effect=RuntimeError("screen unavailable")):
                with self.assertRaisesRegex(RuntimeError, "screen unavailable"):
                    chronicle.run_chronicle_app_tasks("cli", "0")

        launch_mock.assert_not_called()

    def test_run_command_captures_output(self):
        output = chronicle.run_command(
            [sys.executable, "-c", "print('bounded command output')"],
            timeout=5,
        )

        self.assertEqual(output.strip(), "bounded command output")

    def test_run_command_enforces_hard_timeout(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            chronicle.run_command(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                timeout=0.1,
            )

    def test_open_and_locate_task_falls_back_to_screenshot_when_ui_dump_fails(self):
        with mock.patch.object(chronicle, "open_chronicle_task_list"):
            with mock.patch.object(
                chronicle,
                "locate_task_action",
                side_effect=RuntimeError("could not get idle state"),
            ):
                with mock.patch.object(
                    chronicle,
                    "locate_task_action_visually",
                    return_value=("已领取", None),
                ) as visual_mock:
                    result = chronicle.open_and_locate_task(
                        "cli",
                        "0",
                        1080,
                        1920,
                        "【周】分享助手周报",
                    )

        self.assertEqual(result, ("已领取", None))
        visual_mock.assert_called_once()

    def test_run_chronicle_task_safely_continues_after_task_error(self):
        task = mock.Mock(side_effect=RuntimeError("task failed"))

        with mock.patch.object(chronicle, "log") as log_mock:
            result = chronicle.run_chronicle_task_safely(
                "测试任务",
                task,
                "cli",
                "0",
                1080,
                1920,
            )

        self.assertFalse(result)
        self.assertIn("将继续后续任务", log_mock.call_args.args[0])

    def test_run_chronicle_app_tasks_closes_app_after_reward_verification(self):
        with mock.patch.object(chronicle, "query_screen_size", return_value=(1080, 1920)):
            with mock.patch.object(chronicle, "run_chronicle_task_safely", return_value=True):
                with mock.patch.object(chronicle, "open_chronicle_task_list"):
                    with mock.patch.object(chronicle, "claim_all_chronicle_rewards"):
                        with mock.patch.object(
                            chronicle,
                            "verify_chronicle_tasks_completed",
                            return_value=True,
                        ):
                            with mock.patch.object(
                                chronicle,
                                "verify_no_claimable_chronicle_rewards",
                                return_value=True,
                            ):
                                with mock.patch.object(chronicle, "stop_dnf_helper_app") as stop_mock:
                                    chronicle.run_chronicle_app_tasks("cli", "0")

        stop_mock.assert_called_once_with("cli", "0")

    def test_run_chronicle_app_tasks_keeps_app_open_when_rewards_remain(self):
        with mock.patch.object(chronicle, "query_screen_size", return_value=(1080, 1920)):
            with mock.patch.object(chronicle, "run_chronicle_task_safely", return_value=True):
                with mock.patch.object(chronicle, "open_chronicle_task_list"):
                    with mock.patch.object(chronicle, "claim_all_chronicle_rewards"):
                        with mock.patch.object(
                            chronicle,
                            "verify_chronicle_tasks_completed",
                            return_value=True,
                        ):
                            with mock.patch.object(
                                chronicle,
                                "verify_no_claimable_chronicle_rewards",
                                return_value=False,
                            ):
                                with mock.patch.object(chronicle, "stop_dnf_helper_app") as stop_mock:
                                    with self.assertRaisesRegex(RuntimeError, "仍有待领取奖励"):
                                        chronicle.run_chronicle_app_tasks("cli", "0")

        stop_mock.assert_not_called()

    def test_verify_chronicle_tasks_completed_requires_two_done_snapshots(self):
        done = {
            task_name: {"state": "done", "y_fraction": 0.25 + index * 0.1}
            for index, task_name in enumerate(chronicle.TASK_VISUAL_ORDER)
        }
        changed = {task_name: dict(state) for task_name, state in done.items()}
        changed[chronicle.TASK_VISUAL_ORDER[2]]["state"] = "todo"

        with mock.patch.object(
            chronicle,
            "capture_visual_task_states",
            side_effect=(done, changed),
        ):
            with mock.patch.object(chronicle.time, "sleep"):
                self.assertFalse(chronicle.verify_chronicle_tasks_completed("cli", "0"))

    def test_verify_chronicle_tasks_completed_accepts_two_done_snapshots(self):
        done = {
            task_name: {"state": "done", "y_fraction": 0.25 + index * 0.1}
            for index, task_name in enumerate(chronicle.TASK_VISUAL_ORDER)
        }

        with mock.patch.object(
            chronicle,
            "capture_visual_task_states",
            side_effect=(done, done),
        ):
            with mock.patch.object(chronicle.time, "sleep"):
                self.assertTrue(chronicle.verify_chronicle_tasks_completed("cli", "0"))

    def test_task_title_profiles_fail_closed_on_wrong_page(self):
        chronicle.validate_task_title_profiles(dict(chronicle.TASK_TITLE_PROFILES))
        mismatched = dict(chronicle.TASK_TITLE_PROFILES)
        mismatched[chronicle.TASK_VISUAL_ORDER[0]] = tuple(
            value + 3 for value in mismatched[chronicle.TASK_VISUAL_ORDER[0]]
        )

        with self.assertRaisesRegex(RuntimeError, "任务标题.*不一致"):
            chronicle.validate_task_title_profiles(mismatched)

    def test_single_instance_mutex_rejects_duplicate_workflow(self):
        if chronicle.os.name != "nt":
            self.skipTest("Windows named mutex test")

        name = f"Local\\djc_helper_test_{uuid.uuid4().hex}"
        mutex = chronicle.acquire_single_instance_mutex(name)
        try:
            with self.assertRaisesRegex(RuntimeError, "拒绝重复启动"):
                chronicle.acquire_single_instance_mutex(name)
        finally:
            chronicle.release_single_instance_mutex(mutex)

    def test_run_djc_helper_has_bounded_timeout(self):
        process = mock.Mock(pid=12345)
        process.wait.side_effect = subprocess.TimeoutExpired("main.py", 1)
        root_process = mock.Mock()
        root_process.children.return_value = []

        with mock.patch.object(chronicle.subprocess, "Popen", return_value=process):
            with mock.patch.object(chronicle.psutil, "Process", return_value=root_process):
                with mock.patch.object(chronicle.psutil, "wait_procs", return_value=([], [])):
                    with self.assertRaisesRegex(TimeoutError, "超过 1 秒"):
                        chronicle.run_djc_helper(timeout_seconds=1)

        root_process.terminate.assert_called_once()

    def test_run_chronicle_app_stage_retries_from_readiness_check(self):
        with mock.patch.object(chronicle, "ensure_mumu_started") as ensure_mock:
            with mock.patch.object(chronicle, "require_dnf_helper_installed"):
                with mock.patch.object(
                    chronicle,
                    "run_chronicle_app_tasks",
                    side_effect=(RuntimeError("transient"), None),
                ) as task_mock:
                    with mock.patch.object(chronicle.time, "sleep"):
                        chronicle.run_chronicle_app_stage("cli", "0", 30)

        self.assertEqual(ensure_mock.call_count, 2)
        self.assertEqual(task_mock.call_count, 2)

    def test_main_records_partial_failure_in_final_status(self):
        args = mock.Mock(
            skip_app_tasks=False,
            skip_djc_helper=False,
            mumu_cli=None,
            vmindex="0",
            startup_timeout=30,
            helper_timeout=30,
            include_weekly_topic=False,
        )
        statuses = []

        with mock.patch.object(chronicle, "find_mumu_cli", return_value="cli"):
            with mock.patch.object(
                chronicle,
                "run_chronicle_app_stage",
                side_effect=RuntimeError("app failed"),
            ):
                with mock.patch.object(chronicle, "run_djc_helper", return_value=0):
                    with mock.patch.object(chronicle, "log"):
                        with mock.patch.object(
                            chronicle,
                            "write_run_status",
                            side_effect=lambda status: statuses.append(dict(status)),
                        ):
                            exit_code = chronicle.run_workflow(args)

        self.assertEqual(exit_code, 1)
        self.assertEqual(statuses[-1]["app_tasks"], "failed")
        self.assertEqual(statuses[-1]["djc_helper"], "succeeded")
        self.assertFalse(statuses[-1]["success"])
        self.assertEqual(statuses[-1]["exit_code"], 1)
        self.assertIsNotNone(statuses[-1]["completed_at"])

    def test_run_workflow_finalizes_status_after_keyboard_interrupt(self):
        args = mock.Mock(
            skip_app_tasks=False,
            skip_djc_helper=False,
            mumu_cli=None,
            vmindex="0",
            startup_timeout=30,
            helper_timeout=30,
            include_weekly_topic=False,
        )
        statuses = []

        with mock.patch.object(chronicle, "find_mumu_cli", side_effect=KeyboardInterrupt):
            with mock.patch.object(chronicle, "log"):
                with mock.patch.object(
                    chronicle,
                    "write_run_status",
                    side_effect=lambda status: statuses.append(dict(status)),
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        chronicle.run_workflow(args)

        self.assertEqual(statuses[-1]["exit_code"], 130)
        self.assertFalse(statuses[-1]["success"])
        self.assertIsNotNone(statuses[-1]["completed_at"])
        self.assertIn("中断", statuses[-1]["error"])

    def test_run_workflow_does_not_defer_mumu_when_native_dnf_is_running(self):
        args = mock.Mock(
            skip_app_tasks=False,
            skip_djc_helper=True,
            mumu_cli=None,
            vmindex="0",
            startup_timeout=30,
            helper_timeout=30,
            include_weekly_topic=False,
        )
        call_order = []

        def find_cli_side_effect(*_args, **_kwargs):
            call_order.append("find_cli")
            return "cli"

        def stage_side_effect(*_args, **_kwargs):
            call_order.append("stage")

        native_dnf = mock.Mock(
            info={"pid": 1001, "name": "DNF.exe", "exe": r"C:\Games\DNF.exe"}
        )
        with mock.patch.object(
            chronicle.psutil,
            "process_iter",
            return_value=[native_dnf],
        ) as process_iter_mock:
            with mock.patch.object(chronicle, "find_mumu_cli", side_effect=find_cli_side_effect):
                with mock.patch.object(
                    chronicle,
                    "run_chronicle_app_stage",
                    side_effect=stage_side_effect,
                ):
                    with mock.patch.object(chronicle, "write_run_status"):
                        with mock.patch.object(chronicle, "log"):
                            exit_code = chronicle.run_workflow(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(call_order, ["find_cli", "stage"])
        process_iter_mock.assert_not_called()

    def test_stop_dnf_helper_app_only_force_stops_dnf_package(self):
        with mock.patch.object(chronicle, "force_stop_app") as force_stop_mock:
            with mock.patch.object(
                chronicle,
                "app_info",
                return_value='{"state": "stopped"}',
            ):
                chronicle.stop_dnf_helper_app("cli", "0")

        force_stop_mock.assert_called_once_with(
            "cli",
            "0",
            chronicle.DNF_HELPER_PACKAGE,
        )

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

    def test_dismiss_startup_update_dialog_uses_screenshot_when_ui_unavailable(self):
        with mock.patch.object(chronicle, "dump_ui", side_effect=RuntimeError("dump failed")):
            with mock.patch.object(chronicle, "screenshot_has_update_dialog", return_value=False):
                with mock.patch.object(chronicle, "log") as log_mock:
                    self.assertFalse(chronicle.dismiss_startup_update_dialog("cli", "0"))

        self.assertIn("改用截图检查版本更新提示", log_mock.call_args.args[0])

    def test_dismiss_startup_update_dialog_uses_screenshot_after_close(self):
        with mock.patch.object(
            chronicle,
            "dump_ui",
            side_effect=(ui_root("版本更新"), RuntimeError("dump failed")),
        ):
            with mock.patch.object(chronicle, "screenshot_has_update_dialog", return_value=False):
                with mock.patch.object(chronicle, "back") as back_mock:
                    with mock.patch.object(chronicle, "log") as log_mock:
                        self.assertTrue(chronicle.dismiss_startup_update_dialog("cli", "0"))

        back_mock.assert_called_once_with("cli", "0", count=1, delay=2)
        self.assertIn("改用截图复核", log_mock.call_args.args[0])

    def test_dismiss_startup_update_dialog_fails_closed_without_any_evidence(self):
        with mock.patch.object(chronicle, "dump_ui", side_effect=RuntimeError("dump failed")):
            with mock.patch.object(
                chronicle,
                "screenshot_has_update_dialog",
                side_effect=RuntimeError("screenshot failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "均无法确认"):
                    chronicle.dismiss_startup_update_dialog("cli", "0")

    def test_dismiss_startup_update_dialog_can_detect_and_close_from_screenshot(self):
        with mock.patch.object(chronicle, "dump_ui", side_effect=RuntimeError("dump failed")):
            with mock.patch.object(
                chronicle,
                "screenshot_has_update_dialog",
                side_effect=(True, False),
            ):
                with mock.patch.object(chronicle, "back") as back_mock:
                    self.assertTrue(chronicle.dismiss_startup_update_dialog("cli", "0"))

        back_mock.assert_called_once_with("cli", "0", count=1, delay=2)

    def test_update_dialog_pixel_detector_requires_blue_link_and_dimmed_background(self):
        width = height = 100
        rows = [bytearray([100, 100, 100] * width) for _ in range(height)]
        for y in range(39, 61):
            for x in range(27, 73):
                index = x * 3
                rows[y][index : index + 3] = bytes((245, 245, 245))
        for x in range(35, 45):
            index = x * 3
            rows[52][index : index + 3] = bytes((20, 160, 220))

        self.assertTrue(chronicle.looks_like_update_dialog_pixels(width, height, 3, rows))

        bright_rows = [bytearray([245, 245, 245] * width) for _ in range(height)]
        self.assertFalse(chronicle.looks_like_update_dialog_pixels(width, height, 3, bright_rows))

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

    def test_dump_ui_retries_command_timeouts_as_read_failures(self):
        timeout = subprocess.TimeoutExpired("uiautomator dump", 2)
        with mock.patch.object(chronicle, "adb_shell", side_effect=timeout) as adb_mock:
            with mock.patch.object(chronicle.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "无法读取 DNF助手页面结构"):
                    chronicle.dump_ui("cli", "0", attempts=2, command_timeout=2)

        self.assertEqual(adb_mock.call_count, 2)

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

    def test_open_dnf_home_fresh_recovers_app_after_launch_failure(self):
        with mock.patch.object(chronicle, "force_stop_app"):
            with mock.patch.object(chronicle, "adb_shell", return_value="Starting activity"):
                with mock.patch.object(
                    chronicle,
                    "wait_for_app_running",
                    side_effect=((False, '{"state": "stopped"}'), (False, '{"state": "stopped"}')),
                ):
                    with mock.patch.object(
                        chronicle,
                        "launch_dnf_helper",
                        side_effect=(RuntimeError("first launch failed"), None),
                    ) as launch_mock:
                        with mock.patch.object(chronicle.time, "sleep"):
                            with self.assertRaisesRegex(RuntimeError, "first launch failed"):
                                chronicle.open_dnf_home_fresh("cli", "0")

        self.assertEqual(launch_mock.call_count, 2)
