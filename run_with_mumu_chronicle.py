import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DNF_HELPER_PACKAGE = "com.tencent.gamehelper.dnf"
DNF_HELPER_MAIN_ACTIVITY = "com.tencent.gamehelper.ui.main.MainActivity"
DNF_HELPER_TOPIC_ACTIVITY = "com.tencent.gamehelper.ui.moment.TopicMomentActivity"


def log(message):
    print(f"[mumu-chronicle] {message}", flush=True)


def run_command(args, timeout=60, check=True, cwd=None):
    completed = subprocess.run(
        args,
        cwd=str(cwd or ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    if check and completed.returncode != 0:
        joined = " ".join(str(arg) for arg in args)
        raise RuntimeError(f"Command failed ({completed.returncode}): {joined}\n{completed.stdout}")
    return completed.stdout


def find_mumu_cli(explicit_path=None):
    candidates = []
    if explicit_path:
        candidates.append(Path(explicit_path))

    candidates.extend(
        [
            Path(r"D:\Program Files\Netease\MuMu\nx_main\mumu-cli.exe"),
            Path(r"C:\Program Files\Netease\MuMu\nx_main\mumu-cli.exe"),
            Path(os.environ.get("LOCALAPPDATA", "")) / "MuMu模拟器" / "nx_main" / "mumu-cli.exe",
        ]
    )

    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate

    raise FileNotFoundError("未找到 mumu-cli.exe，请使用 --mumu-cli 指定路径")


def mumu(cli, *args, timeout=60, check=True):
    return run_command([str(cli), *map(str, args)], timeout=timeout, check=check)


def get_mumu_info(cli, vmindex):
    raw = mumu(cli, "info", "--vmindex", vmindex, timeout=30)
    data = json.loads(raw)

    if isinstance(data, dict) and data.get("index") == str(vmindex):
        return data

    if str(vmindex) not in data:
        raise RuntimeError(f"MuMu 实例 {vmindex} 不存在，当前信息：{raw}")
    return data[str(vmindex)]


def wait_for_android(cli, vmindex, timeout_seconds):
    deadline = time.time() + timeout_seconds
    last_info = None

    while time.time() < deadline:
        try:
            last_info = get_mumu_info(cli, vmindex)
            if last_info.get("is_android_started"):
                return last_info
        except Exception as exc:
            log(f"等待 MuMu 信息时出错，稍后重试：{exc}")

        time.sleep(3)

    raise TimeoutError(f"等待 MuMu Android 启动超时，最后状态：{last_info}")


def ensure_mumu_started(cli, vmindex, timeout_seconds):
    info = get_mumu_info(cli, vmindex)
    if info.get("is_android_started"):
        log(f"MuMu 实例 {vmindex} 已启动：{info.get('name')}")
        return info

    log(f"启动 MuMu 实例 {vmindex}：{info.get('name')}")
    mumu(cli, "control", "--vmindex", vmindex, "launch", timeout=60)
    return wait_for_android(cli, vmindex, timeout_seconds)


def adb(cli, vmindex, command, timeout=60, check=True):
    return mumu(cli, "adb", "--vmindex", vmindex, "--cmd", command, timeout=timeout, check=check)


def adb_shell(cli, vmindex, command, timeout=60, check=True):
    return adb(cli, vmindex, f"shell {command}", timeout=timeout, check=check)


def app_info(cli, vmindex, package_name):
    return mumu(
        cli,
        "control",
        "--vmindex",
        vmindex,
        "app",
        "info",
        "--package",
        package_name,
        timeout=30,
        check=False,
    )


def require_dnf_helper_installed(cli, vmindex):
    raw = app_info(cli, vmindex, DNF_HELPER_PACKAGE)
    installed_raw = mumu(
        cli,
        "control",
        "--vmindex",
        vmindex,
        "app",
        "info",
        "--installed",
        timeout=60,
        check=False,
    )

    if DNF_HELPER_PACKAGE not in raw and DNF_HELPER_PACKAGE not in installed_raw:
        raise RuntimeError(
            "MuMu 中未检测到 DNF助手。请先在 MuMu 实例里安装并登录 DNF助手一次，"
            "之后这个脚本就能从电脑端自动跑任务。"
        )

    log("已检测到 MuMu 中安装了 DNF助手")


def tap(cli, vmindex, x, y):
    adb_shell(cli, vmindex, f"input tap {int(x)} {int(y)}", timeout=20, check=False)


def tap_fraction(cli, vmindex, width, height, x_fraction, y_fraction):
    tap(cli, vmindex, width * x_fraction, height * y_fraction)


def swipe(cli, vmindex, x1, y1, x2, y2, duration_ms):
    adb_shell(
        cli,
        vmindex,
        f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration_ms)}",
        timeout=20,
        check=False,
    )


def swipe_fraction(cli, vmindex, width, height, x1, y1, x2, y2, duration_ms):
    swipe(
        cli,
        vmindex,
        width * x1,
        height * y1,
        width * x2,
        height * y2,
        duration_ms,
    )


def back(cli, vmindex, count=1, delay=1.0):
    for _ in range(count):
        adb_shell(cli, vmindex, "input keyevent 4", timeout=20, check=False)
        time.sleep(delay)


def force_stop_app(cli, vmindex, package_name):
    adb_shell(cli, vmindex, f"am force-stop {package_name}", timeout=20, check=False)


def launch_dnf_helper(cli, vmindex):
    mumu(
        cli,
        "control",
        "--vmindex",
        vmindex,
        "app",
        "launch",
        "--package",
        DNF_HELPER_PACKAGE,
        timeout=30,
        check=False,
    )
    time.sleep(8)


def run_am_start(cli, vmindex, component, extras):
    parts = ["am", "start", "-n", f"{DNF_HELPER_PACKAGE}/{component}"]
    for key, value in extras.items():
        if isinstance(value, int):
            parts.extend(["--ei", key, str(value)])
        else:
            parts.extend(["--es", key, str(value)])
    adb_shell(cli, vmindex, " ".join(parts), timeout=30, check=False)
    time.sleep(8)


def query_screen_size(cli, vmindex):
    raw = adb_shell(cli, vmindex, "wm size", timeout=20, check=False)
    match = re.search(r"Physical size:\s*(\d+)x(\d+)", raw)
    if not match:
        log(f"未能识别分辨率，使用默认 1080x2160。原始输出：{raw.strip()}")
        return 1080, 2160

    width, height = int(match.group(1)), int(match.group(2))
    log(f"MuMu 分辨率：{width}x{height}")
    return width, height


def scaler(width, height):
    sx = width / 1080.0
    sy = height / 2160.0

    def scale(x, y):
        return int(x * sx), int(y * sy)

    return scale


def reset_to_home(cli, vmindex):
    launch_dnf_helper(cli, vmindex)
    back(cli, vmindex, count=3, delay=1.2)
    launch_dnf_helper(cli, vmindex)


def open_dnf_home(cli, vmindex):
    force_stop_app(cli, vmindex, DNF_HELPER_PACKAGE)
    launch_dnf_helper(cli, vmindex)


def open_chronicle_task_list(cli, vmindex, width, height):
    log("打开 DNF助手编年史任务页")
    open_dnf_home(cli, vmindex)

    # 首页顶部快捷入口“编年有礼”
    tap_fraction(cli, vmindex, width, height, 0.133, 0.128)
    time.sleep(6)

    # 编年史页顶部是签到和已完成任务，向下滑到 App 行为任务卡片。
    swipe_fraction(cli, vmindex, width, height, 0.5, 0.906, 0.5, 0.375, 700)
    time.sleep(2)


def claim_visible_chronicle_task(cli, vmindex, width, height, task_name, y_fraction):
    log(f"领取/确认任务奖励：{task_name}")
    tap_fraction(cli, vmindex, width, height, 0.648, y_fraction)
    time.sleep(2)

    # 如果出现“获得奖励”弹窗，这里点“知道了”；没有弹窗时这个点位通常为空白区域。
    tap_fraction(cli, vmindex, width, height, 0.5, 0.595)
    time.sleep(1.5)


def browse_one_content(cli, vmindex, width, height):
    log("执行：浏览1篇内容")
    open_chronicle_task_list(cli, vmindex, width, height)

    # 必须从编年史任务卡片的“去完成”进入，再点开一篇内容详情，才会计入任务。
    tap_fraction(cli, vmindex, width, height, 0.648, 0.666)
    time.sleep(6)
    tap_fraction(cli, vmindex, width, height, 0.278, 0.375)
    time.sleep(12)

    swipe_fraction(cli, vmindex, width, height, 0.5, 0.875, 0.5, 0.52, 600)
    time.sleep(5)

    open_chronicle_task_list(cli, vmindex, width, height)
    claim_visible_chronicle_task(cli, vmindex, width, height, "浏览1篇内容", 0.666)


def enter_circle_detail(cli, vmindex, width, height):
    log("执行：进入圈子详细页")
    open_chronicle_task_list(cli, vmindex, width, height)

    # “去完成”先进入发现/圈子聚合页，再点进一个话题详情才会计入任务。
    tap_fraction(cli, vmindex, width, height, 0.648, 0.779)
    time.sleep(6)
    tap_fraction(cli, vmindex, width, height, 0.278, 0.603)
    time.sleep(10)

    open_chronicle_task_list(cli, vmindex, width, height)
    claim_visible_chronicle_task(cli, vmindex, width, height, "进入圈子详细页", 0.779)


def enter_weekly_topic(cli, vmindex, width, height):
    log("执行：每周浏览话题详细页")
    reset_to_home(cli, vmindex)
    run_am_start(cli, vmindex, DNF_HELPER_TOPIC_ACTIVITY, {"id": 403443, "name": "编年7月组队"})

    tap_fraction(cli, vmindex, width, height, 0.5, 0.394)
    time.sleep(10)
    back(cli, vmindex, count=2, delay=1.5)


def run_chronicle_app_tasks(cli, vmindex):
    width, height = query_screen_size(cli, vmindex)

    browse_one_content(cli, vmindex, width, height)
    enter_circle_detail(cli, vmindex, width, height)
    enter_weekly_topic(cli, vmindex, width, height)

    log("DNF助手 App 行为任务已执行完毕")


def run_djc_helper():
    python_exe = ROOT / ".venv" / "Scripts" / "python.exe"
    if not python_exe.exists():
        python_exe = Path(sys.executable)

    log("开始运行 djc_helper 领取奖励/经验")
    pause_flag = ROOT / ".disable_pause_after_run"
    created_pause_flag = False

    if not pause_flag.exists():
        pause_flag.write_text("created by run_with_mumu_chronicle.py\n", encoding="utf-8")
        created_pause_flag = True

    try:
        return subprocess.call([str(python_exe), "main.py"], cwd=str(ROOT))
    finally:
        if created_pause_flag and pause_flag.exists():
            pause_flag.unlink()


def parse_args():
    parser = argparse.ArgumentParser(description="Run DNF helper chronicle app tasks in MuMu, then run djc_helper.")
    parser.add_argument("--vmindex", default="0", help="MuMu instance index, default: 0")
    parser.add_argument("--mumu-cli", default=None, help="Path to mumu-cli.exe")
    parser.add_argument("--startup-timeout", type=int, default=180, help="Seconds to wait for Android startup")
    parser.add_argument("--skip-app-tasks", action="store_true", help="Only run djc_helper")
    parser.add_argument("--skip-djc-helper", action="store_true", help="Only run MuMu/DNF Assistant app tasks")
    return parser.parse_args()


def main():
    args = parse_args()

    if not args.skip_app_tasks:
        cli = find_mumu_cli(args.mumu_cli)
        log(f"使用 MuMu CLI：{cli}")
        ensure_mumu_started(cli, args.vmindex, args.startup_timeout)
        require_dnf_helper_installed(cli, args.vmindex)
        run_chronicle_app_tasks(cli, args.vmindex)
    else:
        log("跳过 MuMu/DNF助手 App 行为任务")

    if not args.skip_djc_helper:
        return run_djc_helper()

    log("跳过 djc_helper")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
