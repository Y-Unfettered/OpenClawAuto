from __future__ import annotations

import ctypes
import http.client
import json
import locale
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse
import tkinter as tk

try:
    import winreg
except ImportError:  # pragma: no cover - Windows only
    winreg = None


APP_NAME = "OpenClaw 中文助手 V1.0"
APP_VERSION = "V1.0"
APP_AUTHOR = "青墨荀"
CONFIG_FILENAME = "installer-config.json"
TEMPLATE_FILENAME = "installer-config.template.json"
MIN_NODE_VERSION = (22, 14, 0)
DEFAULT_PROVIDER_ID = "custom-provider-openai"
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
SYS_ENCODING = locale.getpreferredencoding(False) or "utf-8"
USER_ENV_KEY = r"Environment"
MACHINE_ENV_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
DASHBOARD_URL_PATTERN = re.compile(r"Dashboard URL:\s*(\S+)")
DEFAULT_GATEWAY_HOST = "127.0.0.1"
DEFAULT_GATEWAY_PORT = 18789
GATEWAY_START_TIMEOUT = 45.0
GATEWAY_READY_STABLE_HITS = 2
GATEWAY_PORT_READY_GRACE_SECONDS = 2.0
FEISHU_PLUGIN_COMMAND = ["npx", "-y", "@larksuite/openclaw-lark", "install"]
WECHAT_PLUGIN_COMMAND = ["npx", "-y", "@tencent-weixin/openclaw-weixin-cli@latest", "install"]
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
URL_RE = re.compile(r"https?://[^\s]+")
QR_SMALL_CELL_MAP: dict[str, tuple[bool, bool]] = {
    "█": (False, False),
    "▀": (False, True),
    "▄": (True, False),
    " ": (True, True),
}
QUICK_COMMANDS: tuple[tuple[str, str], ...] = (
    ("⌘ 帮助", "/help"),
    ("☰ 命令列表", "/commands"),
    ("◌ 当前状态", "/status"),
    ("✦ 新会话", "/new"),
    ("⚒ 工具列表", "/tools compact"),
    ("⬢ 高思考", "/think high"),
)
PAGE_LABELS: tuple[tuple[str, str], ...] = (
    ("install", "◈ 安装配置"),
    ("channels", "⌁ 聊天接入"),
    ("usage", "◎ 日常使用"),
)
PAIRING_CHANNELS: tuple[tuple[str, str], ...] = (
    ("feishu", "飞书"),
    ("openclaw-weixin", "微信"),
)
THEME_LABELS = {
    "light": "浅色模式",
    "dark": "深色模式",
}
THEME_BY_LABEL = {label: key for key, label in THEME_LABELS.items()}
THEME_PALETTES = {
    "light": {
        "bg": "#F2F7FC",
        "surface": "#FFFFFF",
        "surface_alt": "#EAF2FB",
        "text": "#10273D",
        "muted": "#5C7893",
        "accent": "#1F8FFF",
        "accent_active": "#0E7AE4",
        "border": "#C9D9EA",
        "input_bg": "#F7FBFF",
        "button_bg": "#E9F3FF",
        "button_active": "#D8EAFF",
        "log_bg": "#F6FAFE",
        "log_fg": "#15324A",
    },
    "dark": {
        "bg": "#08111C",
        "surface": "#0E1A2B",
        "surface_alt": "#142438",
        "text": "#E7F1FB",
        "muted": "#8DA7C2",
        "accent": "#4DB4FF",
        "accent_active": "#2E9BEA",
        "border": "#22384F",
        "input_bg": "#132237",
        "button_bg": "#17304A",
        "button_active": "#214160",
        "log_bg": "#07111C",
        "log_fg": "#D8E8F8",
    },
}


@dataclass
class InstallerPreset:
    node_installer: str = ""
    python_installer: str = ""
    git_installer: str = ""
    base_url: str = ""
    model_id: str = ""
    api_key: str = ""
    provider_id: str = DEFAULT_PROVIDER_ID
    compatibility: str = "openai"
    install_python: bool = True
    install_git_if_missing: bool = False
    install_daemon: bool = True
    skip_skills: bool = True


def bundle_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def format_version(version: tuple[int, int, int] | None) -> str:
    if not version:
        return "未知"
    return ".".join(str(part) for part in version)


def version_is_usable(version: tuple[int, int, int] | None) -> bool:
    return bool(version and version >= MIN_NODE_VERSION)


def normalize_path_token(value: str) -> str:
    return value.strip().rstrip("\\/").lower()


def merge_path_strings(*values: str) -> str:
    items: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        for raw_part in value.split(os.pathsep):
            part = raw_part.strip()
            if not part:
                continue
            key = normalize_path_token(part)
            if key in seen:
                continue
            seen.add(key)
            items.append(part)
    return os.pathsep.join(items)


def read_registry_value(root: int, key_path: str, name: str) -> str:
    if winreg is None:
        return ""
    try:
        with winreg.OpenKey(root, key_path) as key:
            value, _ = winreg.QueryValueEx(key, name)
    except FileNotFoundError:
        return ""
    except OSError:
        return ""
    if not isinstance(value, str):
        return ""
    return os.path.expandvars(value)


def refresh_process_path_from_registry() -> None:
    machine_path = read_registry_value(winreg.HKEY_LOCAL_MACHINE, MACHINE_ENV_KEY, "Path") if winreg else ""
    user_path = read_registry_value(winreg.HKEY_CURRENT_USER, USER_ENV_KEY, "Path") if winreg else ""
    os.environ["Path"] = merge_path_strings(machine_path, user_path, os.environ.get("Path", ""))


def broadcast_environment_change() -> None:
    if os.name != "nt":
        return
    hwnd_broadcast = 0xFFFF
    wm_settingchange = 0x001A
    result = ctypes.c_ulong()
    ctypes.windll.user32.SendMessageTimeoutW(
        hwnd_broadcast,
        wm_settingchange,
        0,
        "Environment",
        0,
        5000,
        ctypes.byref(result),
    )


def detect_preferred_theme() -> str:
    if winreg is None:
        return "light"
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
    except OSError:
        return "light"
    return "dark" if int(value) == 0 else "light"


def read_user_env(name: str) -> str:
    if winreg is None:
        return os.environ.get(name, "")
    return read_registry_value(winreg.HKEY_CURRENT_USER, USER_ENV_KEY, name)


def set_user_env(name: str, value: str) -> None:
    os.environ[name] = value
    if winreg is None:
        return
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        USER_ENV_KEY,
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_EXPAND_SZ, value)
    broadcast_environment_change()


def ensure_user_path(path: str) -> None:
    if not path:
        return
    current = read_registry_value(winreg.HKEY_CURRENT_USER, USER_ENV_KEY, "Path") if winreg else os.environ.get("Path", "")
    merged = merge_path_strings(current, path)
    os.environ["Path"] = merge_path_strings(os.environ.get("Path", ""), path)
    if winreg is None or merged == current:
        return
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        USER_ENV_KEY,
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, merged)
    broadcast_environment_change()


def to_shell_args(command: list[str]) -> list[str]:
    if not command:
        raise ValueError("命令为空")
    program = str(command[0])
    if Path(program).suffix.lower() in {".cmd", ".bat"}:
        return ["cmd.exe", "/c", program, *[str(arg) for arg in command[1:]]]
    return [str(arg) for arg in command]


def run_capture(command: list[str], env: dict[str, str] | None = None) -> tuple[int, str]:
    prepared = to_shell_args(command)
    completed = subprocess.run(
        prepared,
        capture_output=True,
        text=True,
        encoding=SYS_ENCODING,
        errors="replace",
        env=env,
        creationflags=CREATE_NO_WINDOW,
        shell=False,
    )
    output = (completed.stdout or completed.stderr or "").strip()
    return completed.returncode, output


def run_stream(
    command: list[str],
    logger,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    line_handler=None,
    stdin_devnull: bool = False,
) -> None:
    prepared = to_shell_args(command)
    logger(f"$ {' '.join(prepared)}")
    process = subprocess.Popen(
        prepared,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL if stdin_devnull else None,
        text=True,
        encoding=SYS_ENCODING,
        errors="replace",
        env=env,
        cwd=cwd,
        creationflags=CREATE_NO_WINDOW,
        shell=False,
    )
    assert process.stdout is not None
    for line in process.stdout:
        message = line.rstrip()
        if message:
            logger(message)
            if line_handler is not None:
                line_handler(message)
    process.wait()
    if process.returncode != 0:
        raise RuntimeError(f"命令失败，退出码 {process.returncode}")


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def resolve_qrcode_terminal_module() -> str:
    candidates: list[Path] = []

    prefix = npm_global_prefix()
    if prefix:
        candidates.append(Path(prefix) / "node_modules" / "openclaw" / "node_modules" / "qrcode-terminal" / "lib" / "main.js")

    openclaw_cmd = resolve_openclaw_command()
    if openclaw_cmd:
        candidates.append(Path(openclaw_cmd).parent / "node_modules" / "openclaw" / "node_modules" / "qrcode-terminal" / "lib" / "main.js")

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        npx_root = Path(local_app_data) / "npm-cache" / "_npx"
        if npx_root.exists():
            candidates.extend(npx_root.glob("*/node_modules/qrcode-terminal/lib/main.js"))

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return ""


def parse_small_qr_text(qr_text: str) -> list[list[bool]]:
    matrix: list[list[bool]] = []
    for raw_line in qr_text.splitlines():
        line = strip_ansi(raw_line).rstrip("\r")
        if not line:
            continue
        if any(char not in QR_SMALL_CELL_MAP for char in line):
            continue
        top_row: list[bool] = []
        bottom_row: list[bool] = []
        for char in line:
            top, bottom = QR_SMALL_CELL_MAP[char]
            top_row.append(top)
            bottom_row.append(bottom)
        matrix.append(top_row)
        matrix.append(bottom_row)
    return matrix


def detect_installer(base: Path, patterns: list[str]) -> str:
    search_roots = [base / "payload", Path.cwd() / "payload", base, Path.cwd()]
    for root in search_roots:
        if not root.exists():
            continue
        matches: list[Path] = []
        for pattern in patterns:
            matches.extend(root.glob(pattern))
        matches = sorted(
            (item for item in matches if item.is_file()),
            key=lambda item: item.name.lower(),
        )
        if matches:
            return str(matches[-1])
    return ""


def resolve_command(*names: str) -> str:
    refresh_process_path_from_registry()
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return ""


def command_version(command: str, version_args: list[str]) -> tuple[str, tuple[int, int, int] | None]:
    if not command:
        return "", None
    code, output = run_capture([command, *version_args], env=os.environ.copy())
    if code != 0:
        return output, None
    return output, parse_version(output)


def npm_global_prefix() -> str:
    npm_path = resolve_command("npm", "npm.cmd")
    if not npm_path:
        return ""
    code, output = run_capture([npm_path, "prefix", "-g"], env=os.environ.copy())
    if code != 0:
        return ""
    return output.strip()


def resolve_openclaw_command() -> str:
    direct = resolve_command("openclaw", "openclaw.cmd")
    if direct:
        return direct

    appdata = os.environ.get("APPDATA", "")
    if appdata:
        candidate = Path(appdata) / "npm" / "openclaw.cmd"
        if candidate.exists():
            return str(candidate)

    prefix = npm_global_prefix()
    if prefix:
        candidate = Path(prefix) / "openclaw.cmd"
        if candidate.exists():
            return str(candidate)
    return ""


def redact_api_key(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def extract_dashboard_url(output: str) -> str:
    match = DASHBOARD_URL_PATTERN.search(output)
    if not match:
        return ""
    return match.group(1).strip()


def can_connect(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_http_service_ready(host: str, port: int, timeout: float = 1.5) -> bool:
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        response.read(128)
        return 200 <= response.status < 500
    except OSError:
        return False
    finally:
        try:
            connection.close()
        except OSError:
            pass


def npm_global_root() -> str:
    npm_path = resolve_command("npm", "npm.cmd")
    if not npm_path:
        return ""
    code, output = run_capture([npm_path, "root", "-g"], env=os.environ.copy())
    if code != 0:
        return ""
    return output.strip()


def sanitize_provider_segment(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def build_provider_id(base_url: str, compatibility: str) -> str:
    raw_url = base_url.strip()
    compatibility_tag = "anthropic" if compatibility == "anthropic" else "openai"
    if not raw_url:
        return f"custom-provider-{compatibility_tag}"

    parsed = urlparse(raw_url if "://" in raw_url else f"https://{raw_url}")
    host = sanitize_provider_segment(parsed.netloc or parsed.path)

    path_hint = ""
    path_parts = [segment for segment in parsed.path.split("/") if segment]
    if path_parts:
        first_part = sanitize_provider_segment(path_parts[0])
        if first_part and first_part != "v1":
            path_hint = first_part

    parts = ["custom"]
    if host:
        parts.append(host)
    if path_hint:
        parts.append(path_hint)
    parts.append(compatibility_tag)

    provider_id = "-".join(part for part in parts if part)
    provider_id = re.sub(r"-{2,}", "-", provider_id).strip("-")
    return provider_id[:64] or DEFAULT_PROVIDER_ID


class InstallerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.base_dir = bundle_dir()
        self.config_path = self.base_dir / CONFIG_FILENAME
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.is_running = False
        self.dashboard_url = ""
        self.gateway_console_process: subprocess.Popen[str] | None = None
        self.action_buttons: list[ttk.Button] = []
        self.channel_qr_window: tk.Toplevel | None = None
        self.channel_qr_canvas: tk.Canvas | None = None
        self.channel_qr_link_var = tk.StringVar(value="")
        self.channel_qr_status_var = tk.StringVar(value="")

        self.root.title(APP_NAME)
        self.root.geometry("1000x980")
        self.root.minsize(1000, 960)
        self.style = ttk.Style(self.root)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

        self.node_installer_var = tk.StringVar()
        self.python_installer_var = tk.StringVar()
        self.git_installer_var = tk.StringVar()
        self.base_url_var = tk.StringVar()
        self.model_id_var = tk.StringVar()
        self.api_key_var = tk.StringVar()
        self.compatibility_var = tk.StringVar(value="openai")
        self.pairing_channel_var = tk.StringVar(value="openclaw-weixin")
        self.pairing_code_var = tk.StringVar()
        self.theme_mode_var = tk.StringVar(value=THEME_LABELS[detect_preferred_theme()])
        self.install_python_var = tk.BooleanVar(value=True)
        self.install_git_var = tk.BooleanVar(value=False)
        self.install_daemon_var = tk.BooleanVar(value=True)
        self.skip_skills_var = tk.BooleanVar(value=True)
        self.page_frames: dict[str, ttk.Frame] = {}
        self.page_buttons: dict[str, ttk.Button] = {}
        self.current_page_key = PAGE_LABELS[0][0]
        self.theme_menus: list[tk.Menu] = []

        self.status_vars = {
            "node": tk.StringVar(value="未检测"),
            "python": tk.StringVar(value="未检测"),
            "git": tk.StringVar(value="未检测"),
            "openclaw": tk.StringVar(value="未检测"),
            "gateway": tk.StringVar(value="未检测"),
            "dashboard": tk.StringVar(value="未检测"),
        }

        self.build_ui()
        self.load_template_defaults()
        self.auto_detect_installers()
        self.load_saved_config()
        self.refresh_statuses()
        self.apply_theme(THEME_BY_LABEL.get(self.theme_mode_var.get(), "light"))

        self.root.after(120, self.flush_logs)

    def build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=14, style="App.TFrame")
        main.pack(fill=tk.BOTH, expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(3, weight=5)
        main.rowconfigure(4, weight=1)
        self.main_frame = main

        hero = ttk.Frame(main, style="Hero.TFrame", padding=(18, 14))
        hero.grid(row=0, column=0, sticky="we")
        hero.columnconfigure(0, weight=1)
        hero.columnconfigure(1, weight=0)
        self.hero_frame = hero

        brand_frame = ttk.Frame(hero, style="Hero.TFrame")
        brand_frame.grid(row=0, column=0, sticky="w")

        header = ttk.Label(
            brand_frame,
            text=f"◈ {APP_NAME} {APP_VERSION}",
            style="HeroTitle.TLabel",
        )
        header.grid(row=0, column=0, sticky="w")

        desc = ttk.Label(
            brand_frame,
            text=(
                f"全网同名作者：{APP_AUTHOR} | 有问题欢迎在抖音、微信公众号交流 | 这个工具主要送给朋友使用，也欢迎你拿去体验。\n"
                "面向小白的一键安装、聊天接入和网关控制工具。Node / Python / Git 安装包放到同目录 payload 文件夹即可自动识别。"
            ),
            style="HeroSub.TLabel",
            wraplength=760,
        )
        desc.grid(row=1, column=0, sticky="w", pady=(6, 0))

        mode_card = ttk.LabelFrame(hero, text="界面模式", padding=10, style="Toolbar.TLabelframe")
        mode_card.grid(row=0, column=1, sticky="e", padx=(16, 0))
        ttk.Label(mode_card, text="◐ 主题", style="Toolbar.TLabel").grid(row=0, column=0, sticky="w")
        theme_combo = ttk.Combobox(
            mode_card,
            textvariable=self.theme_mode_var,
            values=list(THEME_LABELS.values()),
            state="readonly",
            width=10,
            style="Theme.TCombobox",
        )
        theme_combo.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        theme_combo.bind("<<ComboboxSelected>>", self.on_theme_selected)
        self.theme_combo = theme_combo

        hero_line = tk.Frame(main, height=3, bd=0, highlightthickness=0)
        hero_line.grid(row=1, column=0, sticky="we", pady=(10, 12))
        self.hero_line = hero_line

        nav_frame = ttk.Frame(main, style="App.TFrame")
        nav_frame.grid(row=2, column=0, sticky="we", pady=(0, 10))
        for index, (key, label) in enumerate(PAGE_LABELS):
            nav_frame.columnconfigure(index, weight=1, uniform="page-nav")
            button = ttk.Button(
                nav_frame,
                text=label,
                style="Nav.TButton",
                command=lambda page_key=key: self.show_page(page_key),
                width=18,
            )
            button.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 8, 0))
            self.page_buttons[key] = button

        page_host = ttk.Frame(main, style="PageHost.TFrame", padding=0)
        page_host.grid(row=3, column=0, sticky="nsew")
        page_host.columnconfigure(0, weight=1)
        page_host.rowconfigure(0, weight=1)
        self.page_host = page_host

        install_page = self.create_page("install", PAGE_LABELS[0][1])
        install_page.columnconfigure(0, weight=1)

        install_action_frame = ttk.LabelFrame(install_page, text="◉ 安装操作", padding=10)
        install_action_frame.grid(row=0, column=0, sticky="we", pady=(0, 10))
        install_action_frame.columnconfigure(0, weight=0)
        install_action_frame.columnconfigure(1, weight=0)
        install_action_frame.columnconfigure(2, weight=0)
        install_action_frame.columnconfigure(3, weight=1)

        self.save_button = ttk.Button(install_action_frame, text="⟡ 保存预设", command=self.save_preset)
        self.save_button.grid(row=0, column=0, sticky="w")
        self.detect_button = ttk.Button(install_action_frame, text="⟳ 刷新检测", command=self.refresh_statuses)
        self.detect_button.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.start_button = ttk.Button(
            install_action_frame,
            text="◉ 开始一键安装",
            command=self.start_install,
            style="Accent.TButton",
        )
        self.start_button.grid(row=0, column=2, sticky="w", padx=(8, 0))
        ttk.Label(
            install_action_frame,
            text="填好模型和安装包后，直接从这里开始执行。",
            style="Hint.TLabel",
        ).grid(row=0, column=3, sticky="e", padx=(12, 0))

        form = ttk.Frame(install_page, style="Page.TFrame")
        form.grid(row=1, column=0, sticky="nsew")
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)

        pkg_frame = ttk.LabelFrame(form, text="◌ 安装包路径", padding=12)
        pkg_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        pkg_frame.columnconfigure(1, weight=1)

        self.add_path_row(pkg_frame, 0, "Node 安装包", self.node_installer_var)
        self.add_path_row(pkg_frame, 1, "Python 安装包", self.python_installer_var)
        self.add_path_row(pkg_frame, 2, "Git 安装包", self.git_installer_var)

        status_frame = ttk.LabelFrame(form, text="◎ 当前环境检测", padding=12)
        status_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        status_frame.columnconfigure(1, weight=1)

        self.add_status_row(status_frame, 0, "Node")
        self.add_status_row(status_frame, 1, "Python")
        self.add_status_row(status_frame, 2, "Git")
        self.add_status_row(status_frame, 3, "OpenClaw")
        self.add_status_row(status_frame, 4, "Gateway")
        self.add_status_row(status_frame, 5, "Dashboard")

        model_frame = ttk.LabelFrame(install_page, text="⬢ OpenClaw 模型预配置", padding=12)
        model_frame.grid(row=2, column=0, sticky="we")
        model_frame.columnconfigure(1, weight=1)
        model_frame.columnconfigure(3, weight=1)

        ttk.Label(model_frame, text="Base URL").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(model_frame, textvariable=self.base_url_var, style="App.TEntry").grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(model_frame, text="模型 ID").grid(row=0, column=2, sticky="w", padx=(12, 0), pady=4)
        ttk.Entry(model_frame, textvariable=self.model_id_var, style="App.TEntry").grid(row=0, column=3, sticky="ew", pady=4)

        ttk.Label(model_frame, text="API Key").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(model_frame, textvariable=self.api_key_var, show="*", style="App.TEntry").grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(model_frame, text="协议类型").grid(row=1, column=2, sticky="w", padx=(12, 0), pady=4)
        protocol_frame = ttk.Frame(model_frame, style="Card.TFrame")
        protocol_frame.grid(row=1, column=3, sticky="w", pady=4)
        ttk.Radiobutton(
            protocol_frame,
            text="OpenAI 兼容接口",
            value="openai",
            variable=self.compatibility_var,
        ).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            protocol_frame,
            text="Anthropic 兼容接口",
            value="anthropic",
            variable=self.compatibility_var,
        ).grid(row=0, column=1, sticky="w", padx=(12, 0))

        ttk.Label(
            model_frame,
            text="如果接口文档是 /v1/chat/completions 或 /v1/responses，选 OpenAI；如果是 /v1/messages，选 Anthropic。",
            style="Hint.TLabel",
            wraplength=860,
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 2))
        option_frame = ttk.Frame(model_frame, style="Card.TFrame")
        option_frame.grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Checkbutton(option_frame, text="同时补齐 Python 环境", variable=self.install_python_var).grid(
            row=0, column=0, sticky="w", padx=(0, 12)
        )
        ttk.Checkbutton(option_frame, text="Git 缺失时也尝试安装", variable=self.install_git_var).grid(
            row=0, column=1, sticky="w", padx=(0, 12)
        )
        ttk.Checkbutton(option_frame, text="配置 OpenClaw 自启动", variable=self.install_daemon_var).grid(
            row=0, column=2, sticky="w", padx=(0, 12)
        )
        ttk.Checkbutton(option_frame, text="跳过推荐 Skills 安装", variable=self.skip_skills_var).grid(
            row=0, column=3, sticky="w"
        )

        channels_page = self.create_page("channels", PAGE_LABELS[1][1])
        channels_page.columnconfigure(0, weight=1)

        plugin_frame = ttk.LabelFrame(channels_page, text="⌁ 扫码接入", padding=12)
        plugin_frame.grid(row=0, column=0, sticky="we")
        plugin_frame.columnconfigure(0, weight=1)
        plugin_frame.columnconfigure(1, weight=1)
        ttk.Label(
            plugin_frame,
            text="点击后会直接执行官方接入流程：安装插件、进入扫码登录、完成后自动重启 Gateway。",
            style="Hint.TLabel",
            wraplength=900,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        feishu_button = ttk.Button(plugin_frame, text="◈ 接入飞书", command=self.connect_feishu_clicked)
        feishu_button.grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=4)
        self.register_action_button(feishu_button)

        wechat_button = ttk.Button(plugin_frame, text="◈ 接入微信", command=self.connect_wechat_clicked)
        wechat_button.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=4)
        self.register_action_button(wechat_button)

        pair_frame = ttk.LabelFrame(channels_page, text="◇ 配对码批准", padding=12)
        pair_frame.grid(row=1, column=0, sticky="we", pady=(10, 0))
        pair_frame.columnconfigure(1, weight=1)
        pair_frame.columnconfigure(3, weight=1)

        ttk.Label(
            pair_frame,
            text="扫码成功后，先在飞书或微信里给机器人发一条私信，拿到 8 位配对码，再回这里一键批准。",
            style="Hint.TLabel",
            wraplength=900,
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))

        ttk.Label(pair_frame, text="渠道").grid(row=1, column=0, sticky="w", pady=4)
        pair_channel_frame = ttk.Frame(pair_frame, style="Card.TFrame")
        pair_channel_frame.grid(row=1, column=1, sticky="w", pady=4)
        for index, (channel_id, channel_label) in enumerate(PAIRING_CHANNELS):
            ttk.Radiobutton(
                pair_channel_frame,
                text=channel_label,
                value=channel_id,
                variable=self.pairing_channel_var,
            ).grid(row=0, column=index, sticky="w", padx=(0 if index == 0 else 12, 0))

        ttk.Label(pair_frame, text="配对码").grid(row=1, column=2, sticky="w", padx=(12, 0), pady=4)
        ttk.Entry(pair_frame, textvariable=self.pairing_code_var, style="App.TEntry").grid(row=1, column=3, sticky="ew", pady=4)

        approve_pair_button = ttk.Button(pair_frame, text="◆ 批准配对码", command=self.approve_pairing_clicked)
        approve_pair_button.grid(row=2, column=0, columnspan=2, sticky="ew", padx=(0, 6), pady=6)
        self.register_action_button(approve_pair_button)

        list_pair_button = ttk.Button(pair_frame, text="≡ 查看待配对请求", command=self.list_pairing_clicked)
        list_pair_button.grid(row=2, column=2, columnspan=2, sticky="ew", pady=6)
        self.register_action_button(list_pair_button)

        usage_page = self.create_page("usage", PAGE_LABELS[2][1])
        usage_page.columnconfigure(0, weight=1)
        usage_page.columnconfigure(1, weight=1)

        gateway_frame = ttk.LabelFrame(usage_page, text="◎ 网关控制", padding=12)
        gateway_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        gateway_frame.columnconfigure(0, weight=1)
        gateway_frame.columnconfigure(1, weight=1)
        ttk.Label(
            gateway_frame,
            text="安装成功后会直接弹出 OpenClaw Gateway 控制台窗口。等网关完全就绪后，再自动打开带 token 的控制台地址。",
            style="Hint.TLabel",
            wraplength=420,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        self.gateway_start_button = ttk.Button(gateway_frame, text="▶ 启动网关", command=self.start_gateway_clicked)
        self.gateway_start_button.grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=4)
        self.register_action_button(self.gateway_start_button)

        self.gateway_restart_button = ttk.Button(gateway_frame, text="⟳ 重启网关", command=self.restart_gateway_clicked)
        self.gateway_restart_button.grid(row=1, column=1, sticky="ew", pady=4)
        self.register_action_button(self.gateway_restart_button)

        self.dashboard_open_button = ttk.Button(gateway_frame, text="⌘ 打开控制台", command=self.open_dashboard_clicked)
        self.dashboard_open_button.grid(row=2, column=0, sticky="ew", padx=(0, 6), pady=4)
        self.register_action_button(self.dashboard_open_button)

        self.dashboard_copy_button = ttk.Button(gateway_frame, text="⎘ 复制控制台地址", command=self.copy_dashboard_clicked)
        self.dashboard_copy_button.grid(row=2, column=1, sticky="ew", pady=4)
        self.register_action_button(self.dashboard_copy_button)

        command_frame = ttk.LabelFrame(usage_page, text="⚒ 常用斜杠命令", padding=12)
        command_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        command_frame.columnconfigure(0, weight=1)
        command_frame.columnconfigure(1, weight=1)
        command_frame.columnconfigure(2, weight=1)
        ttk.Label(
            command_frame,
            text="点按钮会复制命令到剪贴板。\n进入 OpenClaw 聊天框后直接粘贴并回车即可。",
            style="Hint.TLabel",
            wraplength=420,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        for index, (label, slash_command) in enumerate(QUICK_COMMANDS):
            row = index // 3 + 1
            column = index % 3
            button = ttk.Button(
                command_frame,
                text=label,
                command=lambda cmd=slash_command: self.copy_quick_command(cmd),
            )
            button.grid(row=row, column=column, sticky="ew", padx=(0 if column == 0 else 6, 0), pady=4)
            self.register_action_button(button)

        log_frame = ttk.LabelFrame(main, text="▣ 实时日志", padding=8)
        log_frame.grid(row=4, column=0, sticky="nsew", pady=(12, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_frame = log_frame

        self.log_text = tk.Text(
            log_frame,
            wrap=tk.WORD,
            font=("Consolas", 10),
            relief=tk.FLAT,
            padx=10,
            pady=10,
            height=8,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.configure(state=tk.DISABLED)
        scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

        self.build_menu_bar()
        self.show_page(self.current_page_key)

    def create_page(self, key: str, label: str) -> ttk.Frame:
        frame = ttk.Frame(self.page_host, padding=8, style="Page.TFrame")
        frame.grid(row=0, column=0, sticky="nsew")
        self.page_frames[key] = frame
        return frame

    def build_menu_bar(self) -> None:
        menubar = tk.Menu(self.root, tearoff=False)

        page_menu = tk.Menu(menubar, tearoff=False)
        for key, label in PAGE_LABELS:
            page_menu.add_command(label=label, command=lambda page_key=key: self.show_page(page_key))
        menubar.add_cascade(label="页面", menu=page_menu)

        action_menu = tk.Menu(menubar, tearoff=False)
        action_menu.add_command(label="刷新环境检测", command=self.refresh_statuses)
        action_menu.add_command(label="打开控制台", command=self.open_dashboard_clicked)
        action_menu.add_command(label="滚动到最新日志", command=self.scroll_logs_to_end)
        menubar.add_cascade(label="操作", menu=action_menu)

        theme_menu = tk.Menu(menubar, tearoff=False)
        theme_menu.add_command(label="浅色模式", command=lambda: self.set_theme_mode("light"))
        theme_menu.add_command(label="深色模式", command=lambda: self.set_theme_mode("dark"))
        menubar.add_cascade(label="主题", menu=theme_menu)

        self.theme_menus = [menubar, page_menu, action_menu, theme_menu]
        self.root.configure(menu=menubar)

    def show_page(self, key: str) -> None:
        frame = self.page_frames.get(key)
        if frame is None:
            return
        self.current_page_key = key
        frame.tkraise()
        for page_key, button in self.page_buttons.items():
            button.configure(style="NavSelected.TButton" if page_key == key else "Nav.TButton")

    def scroll_logs_to_end(self) -> None:
        self.log_text.see(tk.END)

    def on_theme_selected(self, _event: object = None) -> None:
        self.apply_theme(THEME_BY_LABEL.get(self.theme_mode_var.get(), "light"))

    def set_theme_mode(self, mode: str) -> None:
        self.theme_mode_var.set(THEME_LABELS.get(mode, THEME_LABELS["light"]))
        self.apply_theme(mode)

    def apply_theme(self, mode: str) -> None:
        palette = THEME_PALETTES["dark" if mode == "dark" else "light"]
        self.root.configure(bg=palette["bg"])

        self.style.configure("TFrame", background=palette["surface"])
        self.style.configure("App.TFrame", background=palette["bg"])
        self.style.configure("Hero.TFrame", background=palette["surface_alt"])
        self.style.configure("Page.TFrame", background=palette["bg"])
        self.style.configure("Card.TFrame", background=palette["surface"])
        self.style.configure("TLabel", background=palette["surface"], foreground=palette["text"])
        self.style.configure("HeroTitle.TLabel", background=palette["surface_alt"], foreground=palette["text"], font=("Microsoft YaHei UI", 18, "bold"))
        self.style.configure("HeroSub.TLabel", background=palette["surface_alt"], foreground=palette["muted"], font=("Microsoft YaHei UI", 10))
        self.style.configure("Toolbar.TLabel", background=palette["surface_alt"], foreground=palette["muted"])
        self.style.configure("Hint.TLabel", background=palette["surface"], foreground=palette["muted"])
        self.style.configure("TLabelframe", background=palette["surface"], bordercolor=palette["border"], relief="solid", borderwidth=1)
        self.style.configure("TLabelframe.Label", background=palette["surface"], foreground=palette["text"], font=("Microsoft YaHei UI", 10, "bold"))
        self.style.configure("Toolbar.TLabelframe", background=palette["surface_alt"], bordercolor=palette["border"], relief="solid", borderwidth=1)
        self.style.configure("Toolbar.TLabelframe.Label", background=palette["surface_alt"], foreground=palette["text"], font=("Microsoft YaHei UI", 9, "bold"))
        self.style.configure("TButton", background=palette["button_bg"], foreground=palette["text"], bordercolor=palette["border"], focusthickness=0, padding=(10, 7))
        self.style.map("TButton", background=[("active", palette["button_active"]), ("pressed", palette["button_active"])])
        self.style.configure("Accent.TButton", background=palette["accent"], foreground="#FFFFFF", bordercolor=palette["accent"], padding=(12, 8))
        self.style.map("Accent.TButton", background=[("active", palette["accent_active"]), ("pressed", palette["accent_active"])], foreground=[("active", "#FFFFFF"), ("pressed", "#FFFFFF")])
        self.style.configure("TEntry", fieldbackground=palette["input_bg"], foreground=palette["text"], bordercolor=palette["border"], insertcolor=palette["text"], padding=6)
        self.style.configure("App.TEntry", fieldbackground=palette["input_bg"], foreground=palette["text"], bordercolor=palette["border"], insertcolor=palette["text"], padding=6)
        self.style.configure("TCheckbutton", background=palette["surface"], foreground=palette["text"])
        self.style.configure("TRadiobutton", background=palette["surface"], foreground=palette["text"])
        self.style.configure("PageHost.TFrame", background=palette["bg"])
        self.style.configure("App.TNotebook", background=palette["bg"], borderwidth=0, tabmargins=(0, 0, 0, 0))
        self.style.configure(
            "App.TNotebook.Tab",
            background=palette["surface_alt"],
            foreground=palette["muted"],
            padding=(18, 10),
            borderwidth=1,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.style.map(
            "App.TNotebook.Tab",
            background=[("selected", palette["surface"]), ("active", palette["button_bg"])],
            foreground=[("selected", palette["text"]), ("active", palette["text"])],
            expand=[("selected", (0, 0, 0, 0)), ("active", (0, 0, 0, 0))],
        )
        self.style.configure(
            "Nav.TButton",
            background=palette["surface_alt"],
            foreground=palette["muted"],
            bordercolor=palette["border"],
            padding=(18, 10),
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.style.map(
            "Nav.TButton",
            background=[("active", palette["button_bg"]), ("pressed", palette["button_bg"])],
            foreground=[("active", palette["text"]), ("pressed", palette["text"])],
        )
        self.style.configure(
            "NavSelected.TButton",
            background=palette["surface"],
            foreground=palette["text"],
            bordercolor=palette["accent"],
            padding=(18, 10),
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.style.map(
            "NavSelected.TButton",
            background=[("active", palette["surface"]), ("pressed", palette["surface"])],
            foreground=[("active", palette["text"]), ("pressed", palette["text"])],
        )
        self.style.configure("TCombobox", fieldbackground=palette["input_bg"], background=palette["input_bg"], foreground=palette["text"], arrowcolor=palette["accent"], bordercolor=palette["border"], insertcolor=palette["text"])
        self.style.map("TCombobox", fieldbackground=[("readonly", palette["input_bg"])], selectbackground=[("readonly", palette["input_bg"])], selectforeground=[("readonly", palette["text"])])
        self.style.configure("Theme.TCombobox", fieldbackground=palette["input_bg"], background=palette["input_bg"], foreground=palette["text"], arrowcolor=palette["accent"], bordercolor=palette["border"])

        self.hero_line.configure(bg=palette["accent"])
        self.log_text.configure(bg=palette["log_bg"], fg=palette["log_fg"], insertbackground=palette["accent"], selectbackground=palette["accent_active"], selectforeground="#FFFFFF")

        for menu in self.theme_menus:
            menu.configure(bg=palette["surface"], fg=palette["text"], activebackground=palette["button_active"], activeforeground=palette["text"], borderwidth=0)

        if self.page_buttons:
            self.show_page(self.current_page_key)

    def add_path_row(self, parent: ttk.LabelFrame, row: int, label: str, var: tk.StringVar) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(parent, text="浏览", command=lambda v=var: self.choose_file(v)).grid(row=row, column=2, pady=4)

    def register_action_button(self, button: ttk.Button) -> None:
        self.action_buttons.append(button)

    def add_status_row(self, parent: ttk.LabelFrame, row: int, label: str) -> None:
        key = label.lower()
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Label(parent, textvariable=self.status_vars[key], wraplength=330).grid(row=row, column=1, sticky="w", pady=4)

    def choose_file(self, var: tk.StringVar) -> None:
        path = filedialog.askopenfilename(
            title="选择安装包",
            filetypes=[("Windows 安装包", "*.exe *.msi"), ("所有文件", "*.*")],
        )
        if path:
            var.set(path)

    def log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_queue.put(f"[{timestamp}] {message}")

    def flush_logs(self) -> None:
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.log_text.configure(state=tk.NORMAL)
                self.log_text.insert(tk.END, line + "\n")
                self.log_text.see(tk.END)
                self.log_text.configure(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.root.after(120, self.flush_logs)

    def run_background_action(
        self,
        action,
        error_title: str,
        success_callback=None,
    ) -> None:
        if self.is_running:
            self.log("当前有任务正在执行，请稍后再试。")
            return

        def worker() -> None:
            self.is_running = True
            self.root.after(0, lambda: self.set_busy(True))
            try:
                result = action()
                if success_callback is not None:
                    self.root.after(0, lambda value=result: success_callback(value))
            except Exception as exc:
                self.log(f"{error_title}失败：{exc}")
                self.root.after(0, lambda: messagebox.showerror(APP_NAME, f"{error_title}失败：\n{exc}"))
            finally:
                self.is_running = False
                self.root.after(0, self.refresh_statuses)
                self.root.after(0, lambda: self.set_busy(False))

        threading.Thread(target=worker, daemon=True).start()

    def load_template_defaults(self) -> None:
        template_path = self.base_dir / TEMPLATE_FILENAME
        if not template_path.exists() or self.config_path.exists():
            return
        try:
            data = json.loads(template_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self.apply_preset(data, overwrite_api_key=False)

    def auto_detect_installers(self) -> None:
        self.node_installer_var.set(
            detect_installer(self.base_dir, ["node*.msi", "node*.exe"])
        )
        self.python_installer_var.set(
            detect_installer(self.base_dir, ["python*.exe", "python*.msi"])
        )
        self.git_installer_var.set(
            detect_installer(self.base_dir, ["git*.exe", "Git-*.exe", "git*.msi"])
        )

    def apply_preset(self, data: dict[str, object], overwrite_api_key: bool) -> None:
        mapping = {
            "node_installer": self.node_installer_var,
            "python_installer": self.python_installer_var,
            "git_installer": self.git_installer_var,
            "base_url": self.base_url_var,
            "model_id": self.model_id_var,
        }
        for key, variable in mapping.items():
            value = data.get(key, "")
            if isinstance(value, str) and value:
                variable.set(value)

        compatibility = data.get("compatibility")
        if compatibility in {"openai", "anthropic"}:
            self.compatibility_var.set(str(compatibility))

        for key, variable in {
            "install_python": self.install_python_var,
            "install_git_if_missing": self.install_git_var,
            "install_daemon": self.install_daemon_var,
            "skip_skills": self.skip_skills_var,
        }.items():
            value = data.get(key)
            if isinstance(value, bool):
                variable.set(value)

        api_key = data.get("api_key", "")
        if overwrite_api_key and isinstance(api_key, str) and api_key:
            self.api_key_var.set(api_key)

    def load_saved_config(self) -> None:
        if not self.config_path.exists():
            return
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            self.log(f"预设文件读取失败：{exc}")
            return
        self.apply_preset(data, overwrite_api_key=True)
        self.log(f"已加载预设文件：{self.config_path}")

    def collect_preset(self) -> InstallerPreset:
        compatibility = self.compatibility_var.get().strip() or "openai"
        return InstallerPreset(
            node_installer=self.node_installer_var.get().strip(),
            python_installer=self.python_installer_var.get().strip(),
            git_installer=self.git_installer_var.get().strip(),
            base_url=self.base_url_var.get().strip(),
            model_id=self.model_id_var.get().strip(),
            api_key=self.api_key_var.get().strip(),
            provider_id=build_provider_id(self.base_url_var.get().strip(), compatibility),
            compatibility=compatibility,
            install_python=self.install_python_var.get(),
            install_git_if_missing=self.install_git_var.get(),
            install_daemon=self.install_daemon_var.get(),
            skip_skills=self.skip_skills_var.get(),
        )

    def save_preset(self) -> None:
        preset = self.collect_preset()
        try:
            self.config_path.write_text(
                json.dumps(asdict(preset), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"保存失败：{exc}")
            return
        messagebox.showinfo(
            APP_NAME,
            f"已保存到：\n{self.config_path}\n\n注意：该文件会包含 API Key 明文，请妥善保管。",
        )

    def refresh_statuses(self) -> None:
        refresh_process_path_from_registry()

        node_path = resolve_command("node", "node.exe")
        node_output, node_version = command_version(node_path, ["-v"])
        node_text = "未安装"
        if node_path:
            tag = "满足要求" if version_is_usable(node_version) else "版本偏低"
            node_text = f"{tag} | {format_version(node_version)} | {node_path}"
        elif node_output:
            node_text = node_output
        self.status_vars["node"].set(node_text)

        python_path = resolve_command("python", "python.exe")
        python_output, python_version = command_version(python_path, ["--version"])
        python_text = "未安装"
        if python_path:
            python_text = f"{format_version(python_version)} | {python_path}"
        elif python_output:
            python_text = python_output
        self.status_vars["python"].set(python_text)

        git_path = resolve_command("git", "git.exe")
        git_output, git_version = command_version(git_path, ["--version"])
        git_text = "未安装"
        if git_path:
            git_text = f"{format_version(git_version)} | {git_path}"
        elif git_output:
            git_text = git_output
        self.status_vars["git"].set(git_text)

        openclaw_path = resolve_openclaw_command()
        openclaw_output, openclaw_version = command_version(openclaw_path, ["--version"])
        openclaw_text = "未安装"
        if openclaw_path:
            openclaw_text = f"{format_version(openclaw_version)} | {openclaw_path}"
        elif openclaw_output:
            openclaw_text = openclaw_output
        self.status_vars["openclaw"].set(openclaw_text)

        config_file = Path.home() / ".openclaw" / "openclaw.json"
        if can_connect(DEFAULT_GATEWAY_HOST, DEFAULT_GATEWAY_PORT):
            self.status_vars["gateway"].set(f"运行中 | http://{DEFAULT_GATEWAY_HOST}:{DEFAULT_GATEWAY_PORT}/")
        elif config_file.exists():
            self.status_vars["gateway"].set("已配置 | 未启动")
        else:
            self.status_vars["gateway"].set("未配置")

        if self.dashboard_url:
            self.status_vars["dashboard"].set("已就绪 | 可一键打开")
        elif openclaw_path and config_file.exists():
            self.status_vars["dashboard"].set("可生成登录地址")
        else:
            self.status_vars["dashboard"].set("未就绪")

    def set_busy(self, busy: bool) -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        self.start_button.configure(state=state)
        self.detect_button.configure(state=state)
        self.save_button.configure(state=state)
        for button in self.action_buttons:
            button.configure(state=state)

    def start_gateway_clicked(self) -> None:
        self.run_background_action(
            self.start_gateway_for_ui,
            "启动网关",
            success_callback=lambda url: self.handle_dashboard_ready(url, open_browser=True, copy=True),
        )

    def restart_gateway_clicked(self) -> None:
        self.run_background_action(
            self.restart_gateway_for_ui,
            "重启网关",
            success_callback=lambda url: self.handle_dashboard_ready(url, open_browser=True, copy=True),
        )

    def open_dashboard_clicked(self) -> None:
        self.run_background_action(
            self.open_dashboard_for_ui,
            "打开控制台",
            success_callback=lambda url: self.handle_dashboard_ready(url, open_browser=True, copy=False),
        )

    def copy_dashboard_clicked(self) -> None:
        self.run_background_action(
            self.copy_dashboard_for_ui,
            "复制控制台地址",
            success_callback=lambda url: self.handle_dashboard_ready(url, open_browser=False, copy=True),
        )

    def connect_feishu_clicked(self) -> None:
        self.run_background_action(
            self.connect_feishu_for_ui,
            "接入飞书官方插件",
        )

    def connect_wechat_clicked(self) -> None:
        self.run_background_action(
            lambda: self.launch_official_plugin_setup(
                "微信",
                [WECHAT_PLUGIN_COMMAND, self.build_openclaw_command("channels", "login", "--channel", "openclaw-weixin"), self.build_openclaw_command("gateway", "restart")],
            ),
            "接入微信官方插件",
        )

    def approve_pairing_clicked(self) -> None:
        self.run_background_action(
            self.approve_pairing_for_ui,
            "批准配对码",
            success_callback=lambda _code: messagebox.showinfo(APP_NAME, "配对码已批准，可以回聊天工具里继续使用了。"),
        )

    def list_pairing_clicked(self) -> None:
        self.run_background_action(
            self.list_pairing_for_ui,
            "查看待配对请求",
            success_callback=lambda _result: self.scroll_logs_to_end(),
        )

    def copy_quick_command(self, slash_command: str) -> None:
        self.copy_text_to_clipboard(slash_command, f"快捷命令已复制：{slash_command}")

    def copy_channel_qr_link(self) -> None:
        url = self.channel_qr_link_var.get().strip()
        if url:
            self.copy_text_to_clipboard(url, "扫码链接已复制到剪贴板。")

    def close_channel_qr_dialog(self) -> None:
        if self.channel_qr_window is not None:
            try:
                self.channel_qr_window.destroy()
            except tk.TclError:
                pass
        self.channel_qr_window = None
        self.channel_qr_canvas = None
        self.channel_qr_link_var.set("")
        self.channel_qr_status_var.set("")

    def draw_qr_matrix(self, canvas: tk.Canvas, matrix: list[list[bool]]) -> None:
        canvas.delete("all")
        if not matrix:
            canvas.configure(width=1, height=1)
            return

        module_count = len(matrix)
        scale = max(5, min(10, 420 // max(module_count, 1)))
        padding = scale * 2
        size = module_count * scale + padding * 2
        canvas.configure(width=size, height=size, bg="#FFFFFF", highlightthickness=0)
        canvas.create_rectangle(0, 0, size, size, fill="#FFFFFF", outline="")

        for row_index, row in enumerate(matrix):
            for col_index, is_black in enumerate(row):
                if not is_black:
                    continue
                x0 = padding + col_index * scale
                y0 = padding + row_index * scale
                x1 = x0 + scale
                y1 = y0 + scale
                canvas.create_rectangle(x0, y0, x1, y1, fill="#000000", outline="")

    def show_channel_qr_dialog(self, channel_name: str, url: str, matrix: list[list[bool]]) -> None:
        if self.channel_qr_window is None or not self.channel_qr_window.winfo_exists():
            window = tk.Toplevel(self.root)
            window.title(f"{channel_name} 扫码接入")
            window.resizable(False, False)
            window.transient(self.root)
            self.channel_qr_window = window

            frame = ttk.Frame(window, padding=16, style="App.TFrame")
            frame.pack(fill=tk.BOTH, expand=True)

            ttk.Label(frame, text=f"请使用{channel_name}扫一扫", style="HeroTitle.TLabel").pack(anchor="w")
            ttk.Label(
                frame,
                text="不要再扫终端字符二维码，直接扫这里的黑白码。",
                style="Hint.TLabel",
            ).pack(anchor="w", pady=(6, 10))

            canvas = tk.Canvas(frame, bg="#FFFFFF", highlightthickness=0, bd=0)
            canvas.pack(anchor="center", pady=(0, 10))
            self.channel_qr_canvas = canvas

            ttk.Label(
                frame,
                textvariable=self.channel_qr_status_var,
                style="Hint.TLabel",
                wraplength=460,
            ).pack(anchor="w", pady=(0, 6))
            ttk.Label(
                frame,
                textvariable=self.channel_qr_link_var,
                style="Hint.TLabel",
                wraplength=460,
            ).pack(anchor="w", pady=(0, 10))

            button_row = ttk.Frame(frame, style="App.TFrame")
            button_row.pack(fill=tk.X)
            ttk.Button(button_row, text="复制扫码链接", command=self.copy_channel_qr_link).pack(side=tk.LEFT)
            ttk.Button(button_row, text="关闭窗口", command=self.close_channel_qr_dialog).pack(side=tk.RIGHT)

            window.protocol("WM_DELETE_WINDOW", self.close_channel_qr_dialog)
        else:
            self.channel_qr_window.deiconify()
            self.channel_qr_window.lift()

        self.channel_qr_link_var.set(url)
        self.channel_qr_status_var.set(f"{channel_name} 官方安装已进入扫码阶段。扫码完成后会自动继续。")
        if self.channel_qr_canvas is not None:
            self.draw_qr_matrix(self.channel_qr_canvas, matrix)

    def has_existing_feishu_credentials(self) -> bool:
        config_file = Path.home() / ".openclaw" / "openclaw.json"
        if not config_file.exists():
            return False
        try:
            data = json.loads(config_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False

        channels = data.get("channels", {})
        if not isinstance(channels, dict):
            return False
        feishu_cfg = channels.get("feishu", {})
        if not isinstance(feishu_cfg, dict):
            return False
        return bool(str(feishu_cfg.get("appId", "")).strip() and feishu_cfg.get("appSecret"))

    def generate_qr_matrix_from_url(self, url: str) -> list[list[bool]]:
        node_path = resolve_command("node", "node.exe")
        if not node_path:
            raise RuntimeError("未检测到 Node，无法生成二维码。")

        qrcode_module = resolve_qrcode_terminal_module()
        if not qrcode_module:
            raise RuntimeError("未找到 qrcode-terminal 模块，无法生成二维码。")

        script = (
            "const path=require('path');"
            "const libPath=process.argv[1];"
            "const url=process.argv[2];"
            "const QRCode=require(path.join(path.dirname(libPath),'..','vendor','QRCode'));"
            "const QRErrorCorrectLevel=require(path.join(path.dirname(libPath),'..','vendor','QRCode','QRErrorCorrectLevel'));"
            "const qr=new QRCode(-1,QRErrorCorrectLevel.L);"
            "qr.addData(url);"
            "qr.make();"
            "const quiet=4;"
            "const size=qr.getModuleCount();"
            "const rows=[];"
            "for(let y=-quiet;y<size+quiet;y++){"
            "const row=[];"
            "for(let x=-quiet;x<size+quiet;x++){"
            "row.push(y>=0&&y<size&&x>=0&&x<size ? !!qr.modules[y][x] : false);"
            "}"
            "rows.push(row);"
            "}"
            "process.stdout.write(JSON.stringify(rows));"
        )
        completed = subprocess.run(
            [node_path, "-e", script, qrcode_module, url],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=os.environ.copy(),
            creationflags=CREATE_NO_WINDOW,
            shell=False,
        )
        output = (completed.stdout or completed.stderr or "").rstrip()
        if completed.returncode != 0 or not output:
            raise RuntimeError("二维码生成失败。")
        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError("二维码矩阵解析失败。") from exc
        if not isinstance(data, list) or not data:
            raise RuntimeError("二维码矩阵为空。")
        return [[bool(cell) for cell in row] for row in data if isinstance(row, list)]

    def connect_feishu_for_ui(self) -> str:
        refresh_process_path_from_registry()
        npx_path = resolve_command("npx", "npx.cmd")
        if not npx_path:
            raise RuntimeError("未检测到 npx，请先完成一键安装。")

        self.root.after(0, self.close_channel_qr_dialog)
        install_command = [npx_path, "-y", "@larksuite/openclaw-lark", "install", "--verbose"]
        if self.has_existing_feishu_credentials():
            install_command.append("--use-existing")

        shown_urls: set[str] = set()

        def log_clean(line: str) -> None:
            clean = strip_ansi(line).strip()
            if clean:
                self.log(clean)

        def handle_line(line: str) -> None:
            clean = strip_ansi(line)
            match = URL_RE.search(clean)
            if not match:
                return
            url = match.group(0)
            if url in shown_urls:
                return
            shown_urls.add(url)
            try:
                qr_matrix = self.generate_qr_matrix_from_url(url)
            except Exception as exc:
                self.log(f"生成飞书二维码失败：{exc}")
                qr_matrix = []
            self.root.after(0, lambda u=url, q=qr_matrix: self.show_channel_qr_dialog("飞书", u, q))

        self.log("开始执行飞书官方插件安装。")
        run_stream(
            install_command,
            log_clean,
            env=os.environ.copy(),
            line_handler=handle_line,
            stdin_devnull=True,
        )
        self.root.after(0, lambda: self.channel_qr_status_var.set("扫码完成，正在应用飞书配置..."))

        run_stream(
            self.build_openclaw_command("channels", "login", "--channel", "feishu"),
            self.log,
            env=os.environ.copy(),
        )
        run_stream(
            self.build_openclaw_command("gateway", "restart"),
            self.log,
            env=os.environ.copy(),
        )

        self.root.after(0, lambda: self.channel_qr_status_var.set("飞书接入完成，可关闭此窗口。"))
        self.log("飞书接入流程完成。")
        return "飞书"

    def handle_dashboard_ready(self, dashboard_url: str, open_browser: bool, copy: bool) -> None:
        if not dashboard_url:
            return
        self.dashboard_url = dashboard_url
        if copy:
            self.copy_text_to_clipboard(dashboard_url, "控制台地址已复制到剪贴板。")
        if open_browser:
            self.open_dashboard_url(dashboard_url)

    def build_openclaw_command(self, *args: str) -> list[str]:
        openclaw_cmd = resolve_openclaw_command()
        if not openclaw_cmd:
            raise RuntimeError("未检测到 OpenClaw，请先完成一键安装。")
        return [openclaw_cmd, *args]

    def quote_powershell_arg(self, value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def format_console_step(self, command: list[str]) -> str:
        if not command:
            raise RuntimeError("启动命令为空。")
        executable = self.quote_powershell_arg(command[0])
        if len(command) == 1:
            return f"& {executable}"
        rendered_args = " ".join(self.quote_powershell_arg(arg) for arg in command[1:])
        return f"& {executable} {rendered_args}"

    def open_command_in_console(
        self,
        commands: list[list[str]],
        *,
        env: dict[str, str] | None = None,
        keep_open: bool = True,
    ) -> subprocess.Popen[str]:
        if not commands:
            raise RuntimeError("???????")
        console_command = " ; ".join(subprocess.list2cmdline(command) for command in commands)
        script_lines = [
            "$OutputEncoding = [System.Text.Encoding]::UTF8",
            "[Console]::InputEncoding = [System.Text.Encoding]::UTF8",
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
            f"Set-Location -LiteralPath {self.quote_powershell_arg(str(self.base_dir))}",
        ]
        script_lines.extend(self.format_console_step(command) for command in commands)
        script_text = "\r\n".join(script_lines) + "\r\n"
        temp_dir = Path(tempfile.gettempdir())
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="",
            suffix=".ps1",
            prefix="openclaw-helper-",
            dir=temp_dir,
            delete=False,
        ) as script_file:
            script_file.write(script_text)
            script_path = script_file.name
        powershell_path = resolve_command("powershell.exe", "powershell", "pwsh.exe", "pwsh") or "powershell.exe"
        cmd_args = [
            powershell_path,
            "-NoLogo",
            "-ExecutionPolicy",
            "Bypass",
            *(["-NoExit"] if keep_open else []),
            "-File",
            script_path,
        ]
        self.log(f"$ {console_command}")
        process = subprocess.Popen(
            cmd_args,
            cwd=str(self.base_dir),
            env=env or os.environ.copy(),
            shell=False,
            creationflags=CREATE_NEW_CONSOLE,
        )
        return process

    def launch_official_plugin_setup(self, channel_name: str, commands: list[list[str]]) -> None:
        refresh_process_path_from_registry()
        npx_path = resolve_command("npx", "npx.cmd")
        if not npx_path:
            raise RuntimeError("未检测到 npx，请先完成一键安装。")
        resolved_commands: list[list[str]] = []
        for command in commands:
            if command and command[0].lower() == "npx":
                resolved_commands.append([npx_path, *command[1:]])
            else:
                resolved_commands.append(command)
        self.log(f"开始打开{channel_name}官方接入控制台。")
        self.open_command_in_console(resolved_commands, env=os.environ.copy(), keep_open=True)
        self.log(f"{channel_name}接入窗口已启动。扫码完成后，请先给机器人发一条私信拿到配对码，再回“聊天接入”页批准。")

    def normalize_pairing_code(self) -> str:
        code = re.sub(r"[^A-Za-z0-9]", "", self.pairing_code_var.get().strip().upper())
        self.pairing_code_var.set(code)
        return code

    def approve_pairing_for_ui(self) -> str:
        openclaw_cmd = resolve_openclaw_command()
        if not openclaw_cmd:
            raise RuntimeError("未检测到 OpenClaw 命令。")

        pairing_code = self.normalize_pairing_code()
        if len(pairing_code) != 8:
            raise RuntimeError("配对码应为 8 位，请检查后重试。")

        channel = self.pairing_channel_var.get().strip() or "openclaw-weixin"
        self.log(f"开始批准配对码：渠道={channel}，配对码={pairing_code}")
        run_stream(
            [openclaw_cmd, "pairing", "approve", "--channel", channel, pairing_code, "--notify"],
            self.log,
            env=os.environ.copy(),
        )
        self.log("配对码批准完成。")
        return pairing_code

    def list_pairing_for_ui(self) -> str:
        openclaw_cmd = resolve_openclaw_command()
        if not openclaw_cmd:
            raise RuntimeError("未检测到 OpenClaw 命令。")

        channel = self.pairing_channel_var.get().strip() or "openclaw-weixin"
        self.log(f"读取待配对请求：渠道={channel}")
        run_stream(
            [openclaw_cmd, "pairing", "list", "--channel", channel],
            self.log,
            env=os.environ.copy(),
        )
        return channel

    def start_install(self) -> None:
        if self.is_running:
            return
        preset = self.collect_preset()
        validation_error = self.validate_before_start(preset)
        if validation_error:
            messagebox.showerror(APP_NAME, validation_error)
            return
        self.is_running = True
        self.set_busy(True)
        worker = threading.Thread(target=self.install_worker, args=(preset,), daemon=True)
        worker.start()

    def validate_before_start(self, preset: InstallerPreset) -> str:
        if not preset.base_url:
            return "请填写大模型 Base URL。"
        if not preset.model_id:
            return "请填写模型 ID。"
        if not preset.api_key:
            return "请填写 API Key。"
        if preset.compatibility not in {"openai", "anthropic"}:
            return "兼容协议只能是 openai 或 anthropic。"

        node_path = resolve_command("node", "node.exe")
        _, node_version = command_version(node_path, ["-v"])
        if not (node_path and version_is_usable(node_version)):
            if not preset.node_installer or not Path(preset.node_installer).exists():
                return "未检测到可用 Node，且未找到 Node 安装包。请把 Node 安装包放到 payload 目录或手动选择。"

        if preset.install_python:
            python_path = resolve_command("python", "python.exe")
            if not python_path and (not preset.python_installer or not Path(preset.python_installer).exists()):
                return "未检测到 Python，且未找到 Python 安装包。"

        if preset.install_git_if_missing:
            git_path = resolve_command("git", "git.exe")
            if not git_path and preset.git_installer and not Path(preset.git_installer).exists():
                return "Git 安装包路径无效。"

        return ""

    def install_worker(self, preset: InstallerPreset) -> None:
        try:
            self.log("开始执行一键安装。")
            self.log(f"目标模型：{preset.model_id}")
            self.log(f"Base URL：{preset.base_url}")
            self.log(f"Provider ID：{preset.provider_id}")
            self.log(f"API Key：{redact_api_key(preset.api_key)}")

            self.ensure_node(preset)
            if preset.install_python:
                self.ensure_python(preset)
            else:
                self.log("已跳过 Python 安装。")
            self.ensure_git_if_needed(preset)

            openclaw_cmd = self.ensure_openclaw()
            dashboard_url = self.configure_openclaw(openclaw_cmd, preset)

            self.log("安装流程完成。")
            self.root.after(0, self.refresh_statuses)
            self.root.after(
                0,
                lambda url=dashboard_url: self.show_success_message(url),
            )
        except Exception as exc:
            self.log(f"安装失败：{exc}")
            self.root.after(0, lambda: messagebox.showerror(APP_NAME, f"安装失败：\n{exc}"))
        finally:
            self.is_running = False
            self.root.after(0, lambda: self.set_busy(False))

    def ensure_node(self, preset: InstallerPreset) -> None:
        self.log("检查 Node 环境。")
        node_path = resolve_command("node", "node.exe")
        _, version = command_version(node_path, ["-v"])
        if node_path and version_is_usable(version):
            self.log(f"Node 已满足要求：{format_version(version)}")
            return

        installer = Path(preset.node_installer)
        if not installer.exists():
            raise RuntimeError("Node 安装包不存在。")

        self.log(f"开始安装 Node：{installer}")
        if installer.suffix.lower() == ".msi":
            run_stream(
                ["msiexec.exe", "/i", str(installer), "/qn", "/norestart"],
                self.log,
                env=os.environ.copy(),
            )
        else:
            run_stream(
                [str(installer), "/quiet", "/norestart"],
                self.log,
                env=os.environ.copy(),
            )

        refresh_process_path_from_registry()
        node_path = resolve_command("node", "node.exe")
        _, version = command_version(node_path, ["-v"])
        if not (node_path and version_is_usable(version)):
            raise RuntimeError("Node 安装后仍未检测到可用版本。")
        self.log(f"Node 安装完成：{format_version(version)}")

    def ensure_python(self, preset: InstallerPreset) -> None:
        self.log("检查 Python 环境。")
        python_path = resolve_command("python", "python.exe")
        if python_path:
            output, version = command_version(python_path, ["--version"])
            self.log(f"Python 已存在：{format_version(version)}")
            if output:
                self.log(output)
            return

        installer = Path(preset.python_installer)
        if not installer.exists():
            raise RuntimeError("Python 安装包不存在。")

        self.log(f"开始安装 Python：{installer}")
        if installer.suffix.lower() == ".msi":
            run_stream(
                ["msiexec.exe", "/i", str(installer), "/qn", "/norestart"],
                self.log,
                env=os.environ.copy(),
            )
        else:
            run_stream(
                [
                    str(installer),
                    "/quiet",
                    "InstallAllUsers=1",
                    "PrependPath=1",
                    "Include_pip=1",
                    "Include_launcher=1",
                    "Include_test=0",
                ],
                self.log,
                env=os.environ.copy(),
            )

        refresh_process_path_from_registry()
        python_path = resolve_command("python", "python.exe")
        if not python_path:
            raise RuntimeError("Python 安装后仍未检测到命令。")
        _, version = command_version(python_path, ["--version"])
        self.log(f"Python 安装完成：{format_version(version)}")

    def ensure_git_if_needed(self, preset: InstallerPreset) -> None:
        self.log("检查 Git 环境。")
        git_path = resolve_command("git", "git.exe")
        if git_path:
            _, version = command_version(git_path, ["--version"])
            self.log(f"Git 已存在：{format_version(version)}")
            return

        if not preset.install_git_if_missing:
            self.log("未检测到 Git，但当前设置为跳过 Git 安装。")
            return

        if not preset.git_installer:
            self.log("未提供 Git 安装包，继续尝试后续流程。")
            return

        installer = Path(preset.git_installer)
        if not installer.exists():
            raise RuntimeError("Git 安装包不存在。")

        self.log(f"开始安装 Git：{installer}")
        if installer.suffix.lower() == ".msi":
            run_stream(
                ["msiexec.exe", "/i", str(installer), "/qn", "/norestart"],
                self.log,
                env=os.environ.copy(),
            )
        else:
            run_stream(
                [str(installer), "/VERYSILENT", "/NORESTART", "/NOCANCEL", "/SP-"],
                self.log,
                env=os.environ.copy(),
            )

        refresh_process_path_from_registry()
        git_path = resolve_command("git", "git.exe")
        if git_path:
            _, version = command_version(git_path, ["--version"])
            self.log(f"Git 安装完成：{format_version(version)}")
        else:
            self.log("Git 安装后仍未检测到，后续将继续尝试使用 npm 安装 OpenClaw。")

    def ensure_openclaw(self) -> str:
        self.log("检查 OpenClaw。")
        openclaw_cmd = resolve_openclaw_command()
        if openclaw_cmd:
            _, version = command_version(openclaw_cmd, ["--version"])
            self.log(f"已检测到 OpenClaw：{format_version(version)}")
            return openclaw_cmd

        npm_path = resolve_command("npm", "npm.cmd")
        if not npm_path:
            raise RuntimeError("未检测到 npm，无法安装 OpenClaw。")

        self.log("开始通过 npm 安装 OpenClaw。")
        env = os.environ.copy()
        env["npm_config_loglevel"] = "info"
        run_stream([npm_path, "install", "-g", "openclaw@latest"], self.log, env=env)

        prefix = npm_global_prefix()
        if prefix:
            ensure_user_path(prefix)
            refresh_process_path_from_registry()

        openclaw_cmd = resolve_openclaw_command()
        if openclaw_cmd:
            _, version = command_version(openclaw_cmd, ["--version"])
            self.log(f"OpenClaw 安装完成：{format_version(version)}")
            return openclaw_cmd

        self.log("npm 安装后仍未检测到 OpenClaw，改用官方 install.ps1 兜底。")
        ps_command = "& ([scriptblock]::Create((iwr -useb 'https://openclaw.ai/install.ps1'))) -NoOnboard"
        run_stream(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_command],
            self.log,
            env=os.environ.copy(),
        )

        prefix = npm_global_prefix()
        if prefix:
            ensure_user_path(prefix)
        refresh_process_path_from_registry()
        openclaw_cmd = resolve_openclaw_command()
        if not openclaw_cmd:
            raise RuntimeError("OpenClaw 安装完成后仍未找到命令。")
        _, version = command_version(openclaw_cmd, ["--version"])
        self.log(f"OpenClaw 安装完成：{format_version(version)}")
        return openclaw_cmd

    def resolve_gateway_console_command(self, openclaw_cmd: str) -> list[str]:
        return [openclaw_cmd, "gateway", "--port", str(DEFAULT_GATEWAY_PORT)]

    def wait_for_gateway(self, timeout_seconds: float = GATEWAY_START_TIMEOUT) -> bool:
        deadline = time.time() + timeout_seconds
        port_ready_logged = False
        fallback_ready_logged = False
        stable_hits = 0
        port_ready_since: float | None = None
        while time.time() < deadline:
            if not can_connect(DEFAULT_GATEWAY_HOST, DEFAULT_GATEWAY_PORT, timeout=1.0):
                stable_hits = 0
                port_ready_since = None
                time.sleep(0.5)
                continue

            now = time.time()
            if port_ready_since is None:
                port_ready_since = now

            if not port_ready_logged:
                self.log("Gateway ????????????????????")
                port_ready_logged = True

            if is_http_service_ready(DEFAULT_GATEWAY_HOST, DEFAULT_GATEWAY_PORT, timeout=1.5):
                stable_hits += 1
                if stable_hits >= GATEWAY_READY_STABLE_HITS:
                    return True
            else:
                stable_hits = 0
                if now - port_ready_since >= GATEWAY_PORT_READY_GRACE_SECONDS:
                    if not fallback_ready_logged:
                        self.log("Gateway ??????????????????????????????")
                        fallback_ready_logged = True
                    return True
            time.sleep(0.5)

        if port_ready_since is not None:
            self.log("Gateway ??????????????????????????")
            return True
        return False

    def stop_gateway_listener(self) -> bool:
        code, output = run_capture(["netstat", "-ano", "-p", "tcp"], env=os.environ.copy())
        if code != 0:
            self.log("无法读取网关端口占用信息。")
            return False

        pids: set[str] = set()
        target = f":{DEFAULT_GATEWAY_PORT}"
        for line in output.splitlines():
            if target not in line:
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            local_address = parts[1]
            state = parts[3].upper()
            pid = parts[-1]
            if local_address.endswith(target) and state == "LISTENING":
                pids.add(pid)

        if not pids:
            self.log("未发现正在监听 OpenClaw 网关端口的进程。")
            return True

        for pid in sorted(pids):
            self.log(f"停止占用 {DEFAULT_GATEWAY_PORT} 端口的进程 PID={pid}")
            code, output = run_capture(["taskkill", "/PID", pid, "/F"], env=os.environ.copy())
            if output:
                for line in output.splitlines():
                    line = line.strip()
                    if line:
                        self.log(line)
            if code != 0:
                self.log(f"停止 PID={pid} 失败，退出码 {code}")

        deadline = time.time() + 8.0
        while time.time() < deadline:
            if not can_connect(DEFAULT_GATEWAY_HOST, DEFAULT_GATEWAY_PORT, timeout=0.5):
                return True
            time.sleep(0.5)
        return False

    def close_tracked_gateway_console(self) -> bool:
        process = self.gateway_console_process
        if process is None:
            return False
        if process.poll() is not None:
            self.gateway_console_process = None
            return False

        self.log(f"???????????? Gateway ????? PID={process.pid}")
        code, output = run_capture(["taskkill", "/PID", str(process.pid), "/T", "/F"], env=os.environ.copy())
        if output:
            for line in output.splitlines():
                line = line.strip()
                if line:
                    self.log(line)

        deadline = time.time() + 3.0
        while process.poll() is None and time.time() < deadline:
            time.sleep(0.2)

        if process.poll() is None and code != 0:
            self.log(f"??? Gateway ????????? {code}")
            return False

        self.gateway_console_process = None
        return True

    def ensure_gateway_running(self, openclaw_cmd: str) -> bool:
        process = self.gateway_console_process
        if process is not None and process.poll() is not None:
            self.gateway_console_process = None

        if can_connect(DEFAULT_GATEWAY_HOST, DEFAULT_GATEWAY_PORT, timeout=1.0):
            self.log("Gateway ?????")
            if self.wait_for_gateway(timeout_seconds=8.0):
                return True
            self.log("Gateway ???????????????????????????")
            return True

        if self.gateway_console_process is not None and self.gateway_console_process.poll() is None:
            self.log("????? Gateway ???????????????????????")
            self.close_tracked_gateway_console()

        command = self.resolve_gateway_console_command(openclaw_cmd)
        self.log("?????????? Gateway?")
        self.gateway_console_process = self.open_command_in_console([command], env=os.environ.copy(), keep_open=True)

        if self.wait_for_gateway():
            self.log("Gateway ??????????")
            return True

        if can_connect(DEFAULT_GATEWAY_HOST, DEFAULT_GATEWAY_PORT, timeout=1.0):
            self.log("Gateway ???????????????????????")
            return True

        process = self.gateway_console_process
        if process is not None and process.poll() is None:
            self.log("Gateway ??????????????????????")
            if self.wait_for_gateway(timeout_seconds=15.0):
                self.log("Gateway ??????????")
                return True
            if can_connect(DEFAULT_GATEWAY_HOST, DEFAULT_GATEWAY_PORT, timeout=1.0):
                self.log("Gateway ??????????????????????????")
                return True

        return False

    def start_gateway_for_ui(self) -> str:
        openclaw_cmd = resolve_openclaw_command()
        if not openclaw_cmd:
            raise RuntimeError("未检测到 OpenClaw 命令。")
        if not self.ensure_gateway_running(openclaw_cmd):
            raise RuntimeError("Gateway 启动失败，请查看日志。")
        return self.resolve_dashboard_url(openclaw_cmd, os.environ.copy())

    def restart_gateway_for_ui(self) -> str:
        openclaw_cmd = resolve_openclaw_command()
        if not openclaw_cmd:
            raise RuntimeError("???? OpenClaw ???")
        self.log("???? Gateway?")
        self.close_tracked_gateway_console()
        stopped = self.stop_gateway_listener()
        if not stopped:
            self.log("???????????????????")
        if not self.ensure_gateway_running(openclaw_cmd):
            raise RuntimeError("Gateway ???????????")
        return self.resolve_dashboard_url(openclaw_cmd, os.environ.copy())

    def open_dashboard_for_ui(self) -> str:
        openclaw_cmd = resolve_openclaw_command()
        if not openclaw_cmd:
            raise RuntimeError("未检测到 OpenClaw 命令。")
        if not self.ensure_gateway_running(openclaw_cmd):
            raise RuntimeError("Gateway 尚未成功启动。")
        dashboard_url = self.dashboard_url or self.resolve_dashboard_url(openclaw_cmd, os.environ.copy())
        if not dashboard_url:
            raise RuntimeError("未能获取带 token 的控制台地址。")
        return dashboard_url

    def copy_dashboard_for_ui(self) -> str:
        openclaw_cmd = resolve_openclaw_command()
        if not openclaw_cmd:
            raise RuntimeError("未检测到 OpenClaw 命令。")
        dashboard_url = self.dashboard_url or self.resolve_dashboard_url(openclaw_cmd, os.environ.copy())
        if not dashboard_url:
            raise RuntimeError("未能获取带 token 的控制台地址。")
        return dashboard_url

    def configure_openclaw(self, openclaw_cmd: str, preset: InstallerPreset) -> str:
        self.log("写入 CUSTOM_API_KEY 到当前用户环境变量。")
        set_user_env("CUSTOM_API_KEY", preset.api_key)

        args = [
            openclaw_cmd,
            "onboard",
            "--flow",
            "quickstart",
            "--non-interactive",
            "--accept-risk",
            "--mode",
            "local",
            "--auth-choice",
            "custom-api-key",
            "--custom-base-url",
            preset.base_url,
            "--custom-model-id",
            preset.model_id,
            "--custom-provider-id",
            preset.provider_id,
            "--custom-compatibility",
            preset.compatibility,
            "--secret-input-mode",
            "ref",
            "--gateway-port",
            "18789",
            "--gateway-bind",
            "loopback",
            "--node-manager",
            "npm",
            "--skip-channels",
            "--skip-search",
            "--skip-ui",
            "--skip-health",
            "--json",
        ]
        if preset.install_daemon:
            args.extend(["--install-daemon", "--daemon-runtime", "node"])
        if preset.skip_skills:
            args.append("--skip-skills")

        env = os.environ.copy()
        env["CUSTOM_API_KEY"] = preset.api_key

        self.log("开始执行 OpenClaw 非交互式配置。")
        run_stream(args, self.log, env=env)

        config_file = Path.home() / ".openclaw" / "openclaw.json"
        if config_file.exists():
            self.log(f"已写入 OpenClaw 配置：{config_file}")
        else:
            self.log("未找到 openclaw.json，但 onboard 命令已执行完成。")

        gateway_ready = False
        if preset.install_daemon:
            self.log("已开启 OpenClaw 自启动，先等待系统自动拉起 Gateway。")
            gateway_ready = self.wait_for_gateway(timeout_seconds=12.0)
            if gateway_ready:
                self.log("检测到 OpenClaw 自启动已拉起 Gateway，不再重复打开控制台窗口。")

        if not gateway_ready:
            gateway_ready = self.ensure_gateway_running(openclaw_cmd)

        if gateway_ready:
            self.log("安装后已完成 Gateway 启动。")
        else:
            self.log("安装后自动启动 Gateway 失败，可稍后点击“启动网关”。")

        dashboard_url = self.resolve_dashboard_url(openclaw_cmd, env)
        if dashboard_url:
            self.log(f"控制台地址：{dashboard_url}")
            self.root.after(0, lambda url=dashboard_url: self.copy_text_to_clipboard(url, "控制台地址已复制到剪贴板。"))
            self.root.after(0, lambda url=dashboard_url: self.open_dashboard_url(url))
        else:
            self.log("未能自动获取控制台地址，请稍后运行 openclaw dashboard。")

        return dashboard_url

    def resolve_dashboard_url(self, openclaw_cmd: str, env: dict[str, str]) -> str:
        self.log("获取带授权 token 的控制台地址。")
        code, output = run_capture([openclaw_cmd, "dashboard", "--no-open"], env=env)
        if output:
            for line in output.splitlines():
                line = line.strip()
                if line:
                    self.log(line)
        if code != 0:
            self.log(f"获取控制台地址失败，退出码 {code}")
            return ""
        dashboard_url = extract_dashboard_url(output)
        if dashboard_url:
            self.dashboard_url = dashboard_url
        return dashboard_url

    def copy_text_to_clipboard(self, value: str, success_message: str) -> None:
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(value)
            self.root.update_idletasks()
            self.log(success_message)
        except tk.TclError as exc:
            self.log(f"复制到剪贴板失败：{exc}")

    def copy_to_clipboard(self, value: str) -> None:
        self.copy_text_to_clipboard(value, "控制台地址已复制到剪贴板。")

    def open_dashboard_url(self, url: str) -> None:
        try:
            if hasattr(os, "startfile"):
                os.startfile(url)
            else:
                webbrowser.open(url)
            self.log("已尝试自动打开控制台页面。")
        except OSError as exc:
            self.log(f"自动打开控制台失败：{exc}")

    def show_success_message(self, dashboard_url: str) -> None:
        message = "OpenClaw 已完成安装和预配置，Gateway 已在独立控制台窗口中启动。"
        if dashboard_url:
            message += f"\n\n控制台地址已复制到剪贴板，并已尝试自动打开：\n{dashboard_url}"
        message += "\n\n如果已开启自启动，建议注销或重启一次后再让对方开始使用。"
        messagebox.showinfo(APP_NAME, message)


def main() -> None:
    refresh_process_path_from_registry()
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    InstallerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
