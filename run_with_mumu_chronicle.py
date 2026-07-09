import argparse
import struct
import json
import os
import re
import subprocess
import sys
import time
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DNF_HELPER_PACKAGE = "com.tencent.gamehelper.dnf"
DNF_HELPER_MAIN_ACTIVITY = "com.tencent.gamehelper.ui.main.MainActivity"
DNF_HELPER_TOPIC_ACTIVITY = "com.tencent.gamehelper.ui.moment.TopicMomentActivity"
SCREENSHOT_REMOTE_PATH = "/sdcard/djc_helper_chronicle_state.png"
SCREENSHOT_LOCAL_PATH = ROOT / ".cached" / "mumu_chronicle_state.png"


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


def adb_pull(cli, vmindex, remote_path, local_path, timeout=60, check=True):
    return adb(cli, vmindex, f"pull {remote_path} {local_path}", timeout=timeout, check=check)


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
    time.sleep(5)


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


def open_dnf_home_fresh(cli, vmindex):
    force_stop_app(cli, vmindex, DNF_HELPER_PACKAGE)
    launch_dnf_helper(cli, vmindex)


def tap_dnf_home_tab(cli, vmindex, width, height):
    tap_fraction(cli, vmindex, width, height, 0.127, 0.971)
    time.sleep(1.5)


def capture_screen(cli, vmindex):
    SCREENSHOT_LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    adb_shell(cli, vmindex, f"screencap -p {SCREENSHOT_REMOTE_PATH}", timeout=20, check=False)
    adb_pull(cli, vmindex, SCREENSHOT_REMOTE_PATH, SCREENSHOT_LOCAL_PATH, timeout=20, check=False)
    return SCREENSHOT_LOCAL_PATH


def read_png_rgb(path):
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} 不是 PNG 文件")

    pos = 8
    width = height = bit_depth = color_type = None
    compressed = bytearray()

    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        chunk_type = data[pos + 4 : pos + 8]
        chunk_data = data[pos + 8 : pos + 8 + length]
        pos += 12 + length

        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", chunk_data)
            if bit_depth != 8 or interlace != 0 or color_type not in (2, 6):
                raise ValueError(f"不支持的 PNG 格式：bit_depth={bit_depth}, color_type={color_type}, interlace={interlace}")
        elif chunk_type == b"IDAT":
            compressed.extend(chunk_data)
        elif chunk_type == b"IEND":
            break

    if width is None or height is None:
        raise ValueError(f"{path} 缺少 IHDR")

    channels = 4 if color_type == 6 else 3
    row_size = width * channels
    raw = zlib.decompress(bytes(compressed))
    rows = []
    previous = bytearray(row_size)
    offset = 0

    for _ in range(height):
        filter_type = raw[offset]
        offset += 1
        row = bytearray(raw[offset : offset + row_size])
        offset += row_size

        for i in range(row_size):
            left = row[i - channels] if i >= channels else 0
            up = previous[i]
            up_left = previous[i - channels] if i >= channels else 0

            if filter_type == 1:
                row[i] = (row[i] + left) & 0xFF
            elif filter_type == 2:
                row[i] = (row[i] + up) & 0xFF
            elif filter_type == 3:
                row[i] = (row[i] + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                predictor = paeth_predictor(left, up, up_left)
                row[i] = (row[i] + predictor) & 0xFF
            elif filter_type != 0:
                raise ValueError(f"不支持的 PNG filter：{filter_type}")

        rows.append(row)
        previous = row

    return width, height, channels, rows


def paeth_predictor(left, up, up_left):
    estimate = left + up - up_left
    left_distance = abs(estimate - left)
    up_distance = abs(estimate - up)
    up_left_distance = abs(estimate - up_left)

    if left_distance <= up_distance and left_distance <= up_left_distance:
        return left
    if up_distance <= up_left_distance:
        return up
    return up_left


def classify_task_button(cli, vmindex, width, height, y_fraction):
    try:
        image_path = capture_screen(cli, vmindex)
        image_width, image_height, channels, rows = read_png_rgb(image_path)
    except Exception as exc:
        log(f"截图识别任务按钮失败，将按未知状态处理：{exc}")
        return "unknown"

    x1 = int(image_width * 0.57)
    x2 = int(image_width * 0.72)
    y1 = int(image_height * (y_fraction - 0.032))
    y2 = int(image_height * (y_fraction + 0.032))
    total = black = red = gray = 0

    for y in range(max(0, y1), min(image_height, y2)):
        row = rows[y]
        for x in range(max(0, x1), min(image_width, x2)):
            idx = x * channels
            r, g, b = row[idx], row[idx + 1], row[idx + 2]
            total += 1

            if r < 55 and g < 55 and b < 55:
                black += 1
            elif r > 190 and g < 105 and b < 125:
                red += 1
            elif 105 <= r <= 185 and 105 <= g <= 185 and 105 <= b <= 185 and max(r, g, b) - min(r, g, b) < 24:
                gray += 1

    if total == 0:
        return "unknown"

    black_ratio = black / total
    red_ratio = red / total
    gray_ratio = gray / total

    if black_ratio > 0.09:
        return "todo"
    if red_ratio > 0.015:
        return "claim"
    if gray_ratio > 0.015:
        return "done"
    return "unknown"


def open_chronicle_task_list(cli, vmindex, width, height, launch_first=True):
    log("打开 DNF助手编年史任务页")
    if launch_first:
        launch_dnf_helper(cli, vmindex)
    tap_dnf_home_tab(cli, vmindex, width, height)

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


def ensure_task_ready(cli, vmindex, width, height, task_name, y_fraction):
    state = classify_task_button(cli, vmindex, width, height, y_fraction)

    if state == "done":
        log(f"跳过：{task_name} 已领取")
        return False

    if state == "claim":
        claim_visible_chronicle_task(cli, vmindex, width, height, task_name, y_fraction)
        return False

    if state == "todo":
        return True

    log(f"未能识别 {task_name} 的按钮状态，为避免误点，跳过该任务")
    return False


def return_to_chronicle_from_content_feed(cli, vmindex, width, height):
    back(cli, vmindex, count=1, delay=1.5)
    tap_dnf_home_tab(cli, vmindex, width, height)
    tap_fraction(cli, vmindex, width, height, 0.133, 0.128)
    time.sleep(6)
    swipe_fraction(cli, vmindex, width, height, 0.5, 0.906, 0.5, 0.375, 700)
    time.sleep(2)


def return_to_chronicle_from_circle_page(cli, vmindex, width, height):
    back(cli, vmindex, count=1, delay=1.5)
    tap_dnf_home_tab(cli, vmindex, width, height)
    tap_fraction(cli, vmindex, width, height, 0.133, 0.128)
    time.sleep(6)
    swipe_fraction(cli, vmindex, width, height, 0.5, 0.906, 0.5, 0.375, 700)
    time.sleep(2)


def browse_one_content(cli, vmindex, width, height):
    if not ensure_task_ready(cli, vmindex, width, height, "浏览1篇内容", 0.666):
        return

    log("执行：浏览1篇内容")

    # 必须从编年史任务卡片的“去完成”进入，再点开一篇内容详情，才会计入任务。
    tap_fraction(cli, vmindex, width, height, 0.648, 0.666)
    time.sleep(6)
    tap_fraction(cli, vmindex, width, height, 0.278, 0.375)
    time.sleep(12)

    swipe_fraction(cli, vmindex, width, height, 0.5, 0.875, 0.5, 0.52, 600)
    time.sleep(5)

    return_to_chronicle_from_content_feed(cli, vmindex, width, height)
    if classify_task_button(cli, vmindex, width, height, 0.666) == "claim":
        claim_visible_chronicle_task(cli, vmindex, width, height, "浏览1篇内容", 0.666)
    else:
        log("浏览1篇内容未出现可领取按钮，可能已领取或助手未及时刷新")


def enter_circle_detail(cli, vmindex, width, height):
    if not ensure_task_ready(cli, vmindex, width, height, "进入圈子详细页", 0.779):
        return

    log("执行：进入圈子详细页")
    # “去完成”先进入发现/圈子聚合页，再点进一个话题详情才会计入任务。
    tap_fraction(cli, vmindex, width, height, 0.648, 0.779)
    time.sleep(6)
    tap_fraction(cli, vmindex, width, height, 0.278, 0.603)
    time.sleep(10)

    return_to_chronicle_from_circle_page(cli, vmindex, width, height)
    if classify_task_button(cli, vmindex, width, height, 0.779) == "claim":
        claim_visible_chronicle_task(cli, vmindex, width, height, "进入圈子详细页", 0.779)
    else:
        log("进入圈子详细页未出现可领取按钮，可能已领取或助手未及时刷新")


def enter_weekly_topic(cli, vmindex, width, height):
    log("执行：每周浏览话题详细页")
    reset_to_home(cli, vmindex)
    run_am_start(cli, vmindex, DNF_HELPER_TOPIC_ACTIVITY, {"id": 403443, "name": "编年7月组队"})

    tap_fraction(cli, vmindex, width, height, 0.5, 0.394)
    time.sleep(10)
    back(cli, vmindex, count=2, delay=1.5)


def run_chronicle_app_tasks(cli, vmindex, include_weekly_topic=False):
    width, height = query_screen_size(cli, vmindex)

    open_dnf_home_fresh(cli, vmindex)
    open_chronicle_task_list(cli, vmindex, width, height, launch_first=False)
    browse_one_content(cli, vmindex, width, height)
    enter_circle_detail(cli, vmindex, width, height)

    if include_weekly_topic:
        enter_weekly_topic(cli, vmindex, width, height)
    else:
        log("跳过：每周浏览话题详细页。如需执行，可添加 --include-weekly-topic")

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
    parser.add_argument("--include-weekly-topic", action="store_true", help="Also run the weekly topic detail task")
    return parser.parse_args()


def main():
    args = parse_args()

    if not args.skip_app_tasks:
        cli = find_mumu_cli(args.mumu_cli)
        log(f"使用 MuMu CLI：{cli}")
        ensure_mumu_started(cli, args.vmindex, args.startup_timeout)
        require_dnf_helper_installed(cli, args.vmindex)
        run_chronicle_app_tasks(cli, args.vmindex, args.include_weekly_topic)
    else:
        log("跳过 MuMu/DNF助手 App 行为任务")

    if not args.skip_djc_helper:
        return run_djc_helper()

    log("跳过 djc_helper")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
