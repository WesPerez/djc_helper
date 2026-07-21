import argparse
import struct
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DNF_HELPER_PACKAGE = "com.tencent.gamehelper.dnf"
DNF_HELPER_MAIN_ACTIVITY = "com.tencent.gamehelper.ui.main.MainActivity"
DNF_HELPER_WELCOME_ACTIVITY = "com.tencent.gamehelper.ui.main.WelcomeActivity"
DNF_HELPER_TOPIC_ACTIVITY = "com.tencent.gamehelper.ui.moment.TopicMomentActivity"
SCREENSHOT_REMOTE_PATH = "/sdcard/djc_helper_chronicle_state.png"
SCREENSHOT_LOCAL_PATH = ROOT / ".cached" / "mumu_chronicle_state.png"
UI_DUMP_REMOTE_PATH = "/sdcard/djc_helper_window.xml"
UI_DUMP_LOCAL_PATH = ROOT / ".cached" / "djc_helper_window.xml"
TASK_ACTION_TEXTS = ("去完成", "领取", "已领取", "已全部领取")
DAILY_SIGNIN_Y_FRACTIONS = (0.074, 0.188)
TASK_VISUAL_ORDER = (
    "【周】查看地区排行榜",
    "【周】分享助手周报",
    "浏览1篇内容",
    "进入圈子详细页",
)
TASK_ACTION_X_FRACTION = 0.675


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
            Path(r"D:\Program Files\Netease\MuMuPlayer\nx_main\mumu-cli.exe"),
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


def parse_app_state(raw):
    try:
        parsed = json.loads(raw.strip())
        return parsed.get("state") if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, AttributeError, TypeError):
        state_match = re.search(r'"state"\s*:\s*"([^"]+)"', raw)
        return state_match.group(1) if state_match else None


def wait_for_app_running(cli, vmindex, package_name, timeout_seconds=15):
    deadline = time.time() + timeout_seconds
    last_raw = ""

    while time.time() < deadline:
        last_raw = app_info(cli, vmindex, package_name)
        if parse_app_state(last_raw) == "running":
            return True, last_raw

        time.sleep(1)

    return False, last_raw


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
    output = mumu(
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
    running, state_raw = wait_for_app_running(cli, vmindex, DNF_HELPER_PACKAGE)
    if not running:
        raise RuntimeError(
            "MuMu CLI 未能启动 DNF助手。"
            f"启动输出：{output.strip() or '<empty>'}；"
            f"应用状态：{state_raw.strip() or '<empty>'}"
        )


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
    open_dnf_home_fresh(cli, vmindex)


def open_dnf_home_fresh(cli, vmindex):
    force_stop_app(cli, vmindex, DNF_HELPER_PACKAGE)
    start_output = adb_shell(
        cli,
        vmindex,
        f"am start -S -n {DNF_HELPER_PACKAGE}/{DNF_HELPER_WELCOME_ACTIVITY}",
        timeout=30,
        check=False,
    )
    running, _ = wait_for_app_running(cli, vmindex, DNF_HELPER_PACKAGE, timeout_seconds=8)
    if not running:
        log(
            "直接启动 DNF助手 WelcomeActivity 后应用未运行，"
            f"改用 MuMu app launch。原始输出：{start_output.strip() or '<empty>'}"
        )
        launch_dnf_helper(cli, vmindex)

    time.sleep(5)
    dismiss_startup_update_dialog(cli, vmindex)


def tap_dnf_home_tab(cli, vmindex, width, height):
    tap_fraction(cli, vmindex, width, height, 0.127, 0.971)
    time.sleep(1.5)


def capture_screen(cli, vmindex):
    SCREENSHOT_LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    adb_shell(cli, vmindex, f"screencap -p {SCREENSHOT_REMOTE_PATH}", timeout=20, check=False)
    adb_pull(cli, vmindex, SCREENSHOT_REMOTE_PATH, SCREENSHOT_LOCAL_PATH, timeout=20, check=False)
    return SCREENSHOT_LOCAL_PATH


def dump_ui(cli, vmindex, attempts=3):
    UI_DUMP_LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, attempts + 1):
        UI_DUMP_LOCAL_PATH.unlink(missing_ok=True)
        output = adb_shell(
            cli,
            vmindex,
            f"uiautomator dump --compressed {UI_DUMP_REMOTE_PATH}",
            timeout=30,
            check=False,
        )
        if "dumped to" in output:
            adb_pull(cli, vmindex, UI_DUMP_REMOTE_PATH, UI_DUMP_LOCAL_PATH, timeout=20, check=False)

        if UI_DUMP_LOCAL_PATH.exists():
            try:
                return ET.parse(UI_DUMP_LOCAL_PATH).getroot()
            except ET.ParseError as exc:
                log(f"第 {attempt} 次解析 DNF助手页面结构失败：{exc}")
        else:
            log(f"第 {attempt} 次读取 DNF助手页面结构失败：{output.strip()}")

        time.sleep(1.5)

    raise RuntimeError("无法读取 DNF助手页面结构")


def has_exact_ui_text(root, text):
    return any(node.get("text") == text for node in root.iter())


def dismiss_startup_update_dialog(cli, vmindex):
    try:
        root = dump_ui(cli, vmindex)
    except RuntimeError as exc:
        raise RuntimeError(
            "无法确认 DNF助手是否显示版本更新提示，为避免误触升级已停止 App 自动操作"
        ) from exc

    if not has_exact_ui_text(root, "版本更新"):
        return False

    log("检测到 DNF助手版本更新提示，使用 Android 返回键取消，避免误触应用升级")
    back(cli, vmindex, count=1, delay=2)

    try:
        root = dump_ui(cli, vmindex)
    except RuntimeError as exc:
        raise RuntimeError("关闭版本更新提示后无法确认页面状态，已停止 App 自动操作") from exc

    if has_exact_ui_text(root, "版本更新"):
        raise RuntimeError("DNF助手版本更新提示无法关闭，为避免误触升级已停止 App 自动操作")

    return True


def parse_bounds(value):
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", value or "")
    if not match:
        return None
    left, top, right, bottom = map(int, match.groups())
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def node_center(node):
    bounds = parse_bounds(node.get("bounds"))
    if not bounds:
        return None
    left, top, right, bottom = bounds
    return (left + right) // 2, (top + bottom) // 2


def find_visible_text_node(root, text, resource_id=None):
    candidates = []
    for node in root.iter():
        if node.get("text") != text or not node_center(node):
            continue
        if resource_id and node.get("resource-id") != resource_id:
            continue
        candidates.append(node)

    if not candidates:
        return None

    return min(candidates, key=lambda node: node_center(node)[1])


def find_task_action(root, task_name):
    parent_map = {child: parent for parent in root.iter() for child in parent}
    task_nodes = [node for node in root.iter() if node.get("text") == task_name]

    for task_node in task_nodes:
        card = parent_map.get(task_node)
        while card is not None:
            action_nodes = [
                node
                for node in card.iter()
                if node.get("text") in TASK_ACTION_TEXTS
            ]
            if action_nodes:
                for action_text in TASK_ACTION_TEXTS:
                    for action_node in action_nodes:
                        if action_node.get("text") == action_text:
                            return action_text, action_node
            card = parent_map.get(card)

    return None, None


def locate_task_action(cli, vmindex, width, height, task_name, max_scrolls=5):
    for scroll_index in range(max_scrolls + 1):
        root = dump_ui(cli, vmindex)
        action_text, action_node = find_task_action(root, task_name)

        if action_text in ("已领取", "已全部领取"):
            return action_text, None

        center = node_center(action_node) if action_node is not None else None
        if action_text and center:
            return action_text, center

        if scroll_index == max_scrolls:
            break

        swipe_fraction(cli, vmindex, width, height, 0.5, 0.84, 0.5, 0.48, 500)
        time.sleep(1.5)

    return None, None


def open_and_locate_task(cli, vmindex, width, height, task_name):
    open_chronicle_task_list(cli, vmindex, width, height)

    # MuMu occasionally exposes only the top 1080 px of a 1080x1920 WebView to
    # uiautomator. Try the current viewport first, then use screenshot-based
    # recognition for the stable task rows before attempting any blind scrolls.
    action_text, center = locate_task_action(cli, vmindex, width, height, task_name, max_scrolls=0)
    if action_text:
        return action_text, center

    action_text, center = locate_task_action_visually(cli, vmindex, width, height, task_name)
    if action_text:
        log(f"通过截图识别 {task_name} 按钮状态：{action_text}")
        return action_text, center

    return locate_task_action(cli, vmindex, width, height, task_name)


def locate_task_action_visually(cli, vmindex, width, height, task_name):
    if task_name not in TASK_VISUAL_ORDER:
        return None, None

    try:
        image_path = capture_screen(cli, vmindex)
        image_width, image_height, channels, rows = read_png_rgb(image_path)
    except Exception as exc:
        log(f"截图识别任务行失败，将按未知状态处理：{exc}")
        return None, None

    task_rows = find_task_row_y_fractions(image_width, image_height, channels, rows)
    task_index = TASK_VISUAL_ORDER.index(task_name)
    if len(task_rows) <= task_index:
        log(f"截图仅识别到 {len(task_rows)} 个连续任务卡片，无法定位 {task_name}")
        return None, None

    y_fraction = task_rows[task_index]
    state = classify_task_button_pixels(image_width, image_height, channels, rows, y_fraction)
    action_text = {
        "todo": "去完成",
        "claim": "领取",
        "done": "已领取",
    }.get(state)
    if action_text is None:
        return None, None

    if state == "done":
        return action_text, None

    log(f"动态定位 {task_name}：y={y_fraction:.3f}，状态={action_text}")
    return action_text, (
        int(width * TASK_ACTION_X_FRACTION),
        int(height * y_fraction),
    )


def claim_task_if_ready(cli, vmindex, width, height, task_name):
    action_text, center = open_and_locate_task(cli, vmindex, width, height, task_name)

    if action_text in ("已领取", "已全部领取"):
        log(f"已完成：{task_name}")
        return True

    if action_text == "领取" and center:
        log(f"领取任务奖励：{task_name}")
        tap(cli, vmindex, *center)
        finish_claim_dialogs(cli, vmindex, width, height)
        action_text, _ = open_and_locate_task(cli, vmindex, width, height, task_name)
        if action_text in ("已领取", "已全部领取"):
            log(f"已确认领取：{task_name}")
            return True

        log(f"领取后未确认完成：{task_name}，当前状态={action_text or '未识别'}")
        return False

    log(f"任务尚未进入可领取状态：{task_name}，当前状态={action_text or '未识别'}")
    return False


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


def classify_task_button_pixels(image_width, image_height, channels, rows, y_fraction):
    x1 = int(image_width * 0.57)
    # Include the gray completed-state label to the right of the black/red
    # action button. The task title itself remains outside this x range.
    x2 = int(image_width * 0.90)
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


def classify_task_button(cli, vmindex, width, height, y_fraction):
    try:
        image_path = capture_screen(cli, vmindex)
        image_width, image_height, channels, rows = read_png_rgb(image_path)
    except Exception as exc:
        log(f"截图识别任务按钮失败，将按未知状态处理：{exc}")
        return "unknown"

    return classify_task_button_pixels(image_width, image_height, channels, rows, y_fraction)


def is_task_card_background(r, g, b):
    return (
        232 <= r <= 249
        and 235 <= g <= 252
        and 239 <= b <= 255
        and r <= g <= b
        and b - r <= 14
    )


def find_task_row_y_fractions(image_width, image_height, channels, rows):
    # The WebView does not reliably expose its task text through uiautomator.
    # Detect the repeated light-gray task cards instead of binding task names to
    # absolute Y coordinates, which drift when the page layout changes.
    sample_xs = [int(image_width * fraction) for fraction in (0.45, 0.50, 0.55)]
    card_rows = []
    for y, row in enumerate(rows):
        matches = 0
        for x in sample_xs:
            idx = x * channels
            if is_task_card_background(row[idx], row[idx + 1], row[idx + 2]):
                matches += 1
        if matches >= 2:
            card_rows.append(y)

    segments = []
    start = last = None
    for y in card_rows:
        if start is None:
            start = last = y
        elif y - last <= 3:
            last = y
        else:
            segments.append((start, last))
            start = last = y
    if start is not None:
        segments.append((start, last))

    min_height = image_height * 0.07
    max_height = image_height * 0.15
    centers = [
        (start + end) / 2 / image_height
        for start, end in segments
        if min_height <= end - start + 1 <= max_height
    ]

    # The actual task cards form the longest evenly spaced run. This excludes
    # similarly colored fragments inside the larger daily-signin card.
    best_run = []
    for start_index in range(len(centers)):
        run = [centers[start_index]]
        for center in centers[start_index + 1 :]:
            gap = center - run[-1]
            if 0.07 <= gap <= 0.16:
                run.append(center)
            else:
                break
        if len(run) > len(best_run):
            best_run = run

    return best_run


def find_visible_claim_buttons(cli, vmindex):
    try:
        image_path = capture_screen(cli, vmindex)
        image_width, image_height, channels, rows = read_png_rgb(image_path)
    except Exception as exc:
        log(f"截图扫描领取按钮失败：{exc}")
        return []

    x1 = int(image_width * 0.48)
    x2 = int(image_width * 0.72)
    y1 = int(image_height * 0.34)
    y2 = int(image_height * 0.91)
    red_rows = []

    for y in range(y1, y2):
        red_pixels = 0
        for x in range(x1, x2):
            idx = x * channels
            r, g, b = rows[y][idx], rows[y][idx + 1], rows[y][idx + 2]
            if r > 190 and g < 115 and b < 135:
                red_pixels += 1

        if red_pixels >= max(6, int((x2 - x1) * 0.035)):
            red_rows.append(y)

    segments = []
    start = last = None
    for y in red_rows:
        if start is None:
            start = last = y
        elif y - last <= 3:
            last = y
        else:
            segments.append((start, last))
            start = last = y

    if start is not None:
        segments.append((start, last))

    button_y_values = []
    for start, end in segments:
        height = end - start + 1
        center_y = (start + end) / 2

        # A claim button has red text and/or red rounded border in this right-side region.
        # Ignore tiny decorative red marks and near-bottom floating controls.
        if height < 8 or center_y > image_height * 0.90:
            continue

        y_fraction = center_y / image_height
        if all(abs(y_fraction - existing) > 0.035 for existing in button_y_values):
            button_y_values.append(y_fraction)

    return sorted(button_y_values)


def claim_all_visible_chronicle_rewards(cli, vmindex, width, height, max_claims=20):
    claimed = 0
    attempted_positions = set()

    for _ in range(max_claims):
        claim_buttons = find_visible_claim_buttons(cli, vmindex)
        if not claim_buttons:
            break

        y_fraction = claim_buttons[0]
        position_key = round(y_fraction, 3)
        if position_key in attempted_positions:
            log(f"当前页同一位置 y={y_fraction:.3f} 仍被识别为可领取，停止当前页扫描以避免重复误点")
            break

        attempted_positions.add(position_key)
        log(f"领取当前页可见奖励：y={y_fraction:.3f}")
        tap_fraction(cli, vmindex, width, height, 0.648, y_fraction)
        finish_claim_dialogs(cli, vmindex, width, height)
        claimed += 1

    return claimed


def claim_all_chronicle_rewards(cli, vmindex, width, height, max_pages=6):
    log("开始扫描并领取编年史任务页全部可见奖励")
    total_claimed = 0

    for y_fraction in DAILY_SIGNIN_Y_FRACTIONS:
        if classify_task_button(cli, vmindex, width, height, y_fraction) != "claim":
            continue

        claim_visible_chronicle_task(
            cli,
            vmindex,
            width,
            height,
            "DNF助手签到",
            y_fraction,
        )
        total_claimed += 1
        break

    for page in range(max_pages):
        total_claimed += claim_all_visible_chronicle_rewards(cli, vmindex, width, height)

        if page == max_pages - 1:
            break

        swipe_fraction(cli, vmindex, width, height, 0.5, 0.86, 0.5, 0.50, 500)
        time.sleep(1.5)

    log(f"编年史任务页奖励扫描完成，本次领取 {total_claimed} 个")
    return total_claimed


def open_chronicle_task_list(cli, vmindex, width, height, launch_first=True):
    log("打开 DNF助手编年史任务页")
    if launch_first:
        open_dnf_home_fresh(cli, vmindex)
    tap_dnf_home_tab(cli, vmindex, width, height)

    # 首页顶部快捷入口“编年有礼”
    tap_fraction(cli, vmindex, width, height, 0.133, 0.128)
    time.sleep(6)

    # WebView 会保留上次离开时的滚动位置。先反向滑动到页面顶部，再执行
    # 一次固定距离的正向滑动，确保后续截图识别的任务行位置稳定。
    for _ in range(4):
        swipe_fraction(cli, vmindex, width, height, 0.5, 0.30, 0.5, 0.85, 500)
        time.sleep(0.8)

    # 编年史页顶部是签到区域，向下滑到 App 行为任务卡片。
    swipe_fraction(cli, vmindex, width, height, 0.5, 0.906, 0.5, 0.375, 700)
    time.sleep(2)


def claim_visible_chronicle_task(cli, vmindex, width, height, task_name, y_fraction):
    log(f"领取/确认任务奖励：{task_name}")
    tap_fraction(cli, vmindex, width, height, 0.648, y_fraction)
    finish_claim_dialogs(cli, vmindex, width, height)


def finish_claim_dialogs(cli, vmindex, width, height):
    time.sleep(1.5)

    dismiss_startup_update_dialog(cli, vmindex)

    # DNF助手签到会先弹“奖励确认领取”，右侧红色按钮是“确认”。
    # 普通任务可能直接弹“获得奖励”，这里先尝试确认，再尝试关闭成功弹窗。
    tap_fraction(cli, vmindex, width, height, 0.668, 0.584)
    time.sleep(1.5)

    # 如果出现“获得奖励”弹窗，这里点“知道了”；没有弹窗时通常不会产生副作用。
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
    task_name = "浏览1篇内容"
    action_text, center = open_and_locate_task(cli, vmindex, width, height, task_name)

    if action_text in ("已领取", "已全部领取"):
        log(f"跳过：{task_name} 已领取")
        return True
    if action_text == "领取":
        return claim_task_if_ready(cli, vmindex, width, height, task_name)
    if action_text != "去完成" or not center:
        log(f"未找到 {task_name} 的去完成按钮，当前状态={action_text or '未识别'}")
        return False

    log(f"执行：{task_name}")

    # 必须从编年史任务卡片的“去完成”进入，再点开一篇内容详情，才会计入任务。
    tap(cli, vmindex, *center)
    time.sleep(6)
    tap_fraction(cli, vmindex, width, height, 0.278, 0.375)
    time.sleep(12)

    swipe_fraction(cli, vmindex, width, height, 0.5, 0.875, 0.5, 0.52, 600)
    time.sleep(5)

    return claim_task_if_ready(cli, vmindex, width, height, task_name)


def enter_circle_detail(cli, vmindex, width, height):
    task_name = "进入圈子详细页"
    action_text, center = open_and_locate_task(cli, vmindex, width, height, task_name)

    if action_text in ("已领取", "已全部领取"):
        log(f"跳过：{task_name} 已领取")
        return True
    if action_text == "领取":
        return claim_task_if_ready(cli, vmindex, width, height, task_name)
    if action_text != "去完成" or not center:
        log(f"未找到 {task_name} 的去完成按钮，当前状态={action_text or '未识别'}")
        return False

    log(f"执行：{task_name}")
    # “去完成”先进入发现/圈子聚合页，再点进一个话题详情才会计入任务。
    tap(cli, vmindex, *center)
    time.sleep(6)
    tap_fraction(cli, vmindex, width, height, 0.37, 0.63)
    time.sleep(10)

    return claim_task_if_ready(cli, vmindex, width, height, task_name)


def view_region_ranking(cli, vmindex, width, height):
    task_name = "【周】查看地区排行榜"
    action_text, center = open_and_locate_task(cli, vmindex, width, height, task_name)

    if action_text in ("已领取", "已全部领取"):
        log(f"跳过：{task_name} 已领取")
        return True
    if action_text == "领取":
        return claim_task_if_ready(cli, vmindex, width, height, task_name)
    if action_text != "去完成" or not center:
        log(f"未找到 {task_name} 的去完成按钮，当前状态={action_text or '未识别'}")
        return False

    log(f"执行：{task_name}")
    tap(cli, vmindex, *center)
    time.sleep(7)
    back(cli, vmindex, count=1, delay=2)
    return claim_task_if_ready(cli, vmindex, width, height, task_name)


def share_weekly_report(cli, vmindex, width, height):
    task_name = "【周】分享助手周报"
    action_text, center = open_and_locate_task(cli, vmindex, width, height, task_name)

    if action_text in ("已领取", "已全部领取"):
        log(f"跳过：{task_name} 已领取")
        return True
    if action_text == "领取":
        return claim_task_if_ready(cli, vmindex, width, height, task_name)
    if action_text != "去完成" or not center:
        log(f"未找到 {task_name} 的去完成按钮，当前状态={action_text or '未识别'}")
        return False

    log(f"执行：{task_name}")
    tap(cli, vmindex, *center)
    time.sleep(8)

    root = dump_ui(cli, vmindex)
    share_node = find_visible_text_node(
        root,
        "分享",
        resource_id=f"{DNF_HELPER_PACKAGE}:id/funcation",
    )
    share_center = node_center(share_node) if share_node is not None else None
    if not share_center:
        log("周报页未找到顶部分享按钮")
        back(cli, vmindex, count=1, delay=1.5)
        return False

    tap(cli, vmindex, *share_center)
    time.sleep(3)

    # 打开分享面板即满足任务条件；立即退出，不选择联系人或平台。
    back(cli, vmindex, count=1, delay=1.5)
    back(cli, vmindex, count=1, delay=2)
    return claim_task_if_ready(cli, vmindex, width, height, task_name)


def enter_weekly_topic(cli, vmindex, width, height):
    log("执行：每周浏览话题详细页")
    reset_to_home(cli, vmindex)
    run_am_start(cli, vmindex, DNF_HELPER_TOPIC_ACTIVITY, {"id": 403443, "name": "编年7月组队"})

    tap_fraction(cli, vmindex, width, height, 0.5, 0.394)
    time.sleep(10)
    back(cli, vmindex, count=2, delay=1.5)


def run_chronicle_app_tasks(cli, vmindex, include_weekly_topic=False):
    width, height = query_screen_size(cli, vmindex)

    tasks = (
        ("【周】查看地区排行榜", view_region_ranking),
        ("【周】分享助手周报", share_weekly_report),
        ("浏览1篇内容", browse_one_content),
        ("进入圈子详细页", enter_circle_detail),
    )
    failed_tasks = []

    for task_name, task in tasks:
        if not task(cli, vmindex, width, height):
            failed_tasks.append(task_name)

    if failed_tasks:
        log(f"首次执行后未确认完成，将重试：{', '.join(failed_tasks)}")
        retry_failed_tasks = []
        for task_name, task in tasks:
            if task_name in failed_tasks and not task(cli, vmindex, width, height):
                retry_failed_tasks.append(task_name)
        failed_tasks = retry_failed_tasks

    if include_weekly_topic:
        log("兼容执行旧版每周话题详情任务")
        enter_weekly_topic(cli, vmindex, width, height)
        open_chronicle_task_list(cli, vmindex, width, height)
    else:
        log("跳过已下线的旧版每周话题详情入口")

    open_chronicle_task_list(cli, vmindex, width, height)
    claim_all_chronicle_rewards(cli, vmindex, width, height)

    if failed_tasks:
        raise RuntimeError(f"DNF助手 App 任务未完成：{', '.join(failed_tasks)}")

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
        return subprocess.call([str(python_exe), "main.py", "--no_max_console"], cwd=str(ROOT))
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
    app_task_error = None

    if not args.skip_app_tasks:
        try:
            cli = find_mumu_cli(args.mumu_cli)
            log(f"使用 MuMu CLI：{cli}")
            ensure_mumu_started(cli, args.vmindex, args.startup_timeout)
            require_dnf_helper_installed(cli, args.vmindex)
            run_chronicle_app_tasks(cli, args.vmindex, args.include_weekly_topic)
        except Exception as exc:
            app_task_error = exc
            log(f"MuMu/DNF助手 App 阶段失败：{exc}")
    else:
        log("跳过 MuMu/DNF助手 App 行为任务")

    if not args.skip_djc_helper:
        helper_exit_code = run_djc_helper()
        if helper_exit_code != 0:
            return helper_exit_code
    else:
        log("跳过 djc_helper")

    return 1 if app_task_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
