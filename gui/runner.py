"""Steamauto 子进程管理：启动/停止 + 日志增量读取。

GUI 不 import Steamauto 核心模块，而是以子进程方式启动 Steamauto.py，
日志通过读取 logs/ 目录下的日志文件增量获取，两者完全解耦。
"""
import os
import subprocess
import sys
from typing import List, Optional, Tuple

from . import config_editor

_proc: Optional[subprocess.Popen] = None
_log_offsets: dict = {}
_current_log_file: Optional[str] = None


def is_running() -> bool:
    return _proc is not None and _proc.poll() is None


def get_pid() -> Optional[int]:
    return _proc.pid if is_running() else None


def start() -> Tuple[bool, str]:
    global _proc
    if is_running():
        return False, "Steamauto 已在运行中"
    script = os.path.join(config_editor.PROJECT_ROOT, "Steamauto.py")
    if not os.path.exists(script):
        return False, "未找到入口文件：" + script
    creationflags = 0
    if os.name == "nt":
        # 不弹独立控制台窗口；Steamauto 日志由 FileHandler 写文件，GUI 通过 tail 实时显示
        creationflags = subprocess.CREATE_NO_WINDOW
    try:
        env = dict(os.environ)
        env["STEAMAUTO_NO_PAUSE"] = "1"  # GUI 子进程无交互终端，出错不等待按键
        _proc = subprocess.Popen(
            [sys.executable, script],
            cwd=config_editor.PROJECT_ROOT,
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,  # 丢弃 stdout（日志走文件 tail，避免与 FileHandler 重复）
            stderr=subprocess.DEVNULL,
            env=env,
        )
    except Exception as e:  # noqa: BLE001
        _proc = None
        return False, "启动失败：" + str(e)
    _reset_log_state()
    return True, "已启动 Steamauto，PID " + str(_proc.pid)


def stop() -> Tuple[bool, str]:
    global _proc
    if not is_running():
        return False, "Steamauto 未在运行"
    proc = _proc
    try:
        proc.terminate()
    except Exception as e:  # noqa: BLE001
        return False, "停止失败：" + str(e)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass
    _proc = None
    return True, "已停止 Steamauto"


def _reset_log_state() -> None:
    global _log_offsets, _current_log_file
    _log_offsets = {}
    _current_log_file = None


def latest_log_file() -> Optional[str]:
    """返回 logs/ 下最新（按修改时间）的 .log 文件绝对路径，无则 None。"""
    logs_dir = config_editor.LOGS_FOLDER_PATH
    if not os.path.isdir(logs_dir):
        return None
    files = [os.path.join(logs_dir, f) for f in os.listdir(logs_dir) if f.endswith(".log")]
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def _tail_lines(path: str, n: int) -> List[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            read_size = min(size, max(n * 200, 4096))
            f.seek(max(0, size - read_size))
            data = f.read()
        lines = data.decode("utf-8", errors="replace").splitlines()
        return lines[-n:] if len(lines) > n else lines
    except OSError:
        return []


def read_logs(tail: Optional[int] = None, flush: bool = False) -> Tuple[List[str], Optional[str]]:
    """读取日志。

    tail=N：返回最新日志文件最后 N 行，并把读取位置重置到文件末尾。
    否则：增量模式，返回自上次读取以来的新行（只返回完整行，避免半行断裂）。
    flush=True 时强制返回剩余内容（含最后不完整行）。
    """
    global _log_offsets, _current_log_file
    path = latest_log_file()
    if path is None:
        return [], None

    if tail is not None:
        lines = _tail_lines(path, tail)
        _current_log_file = path
        try:
            _log_offsets[path] = os.path.getsize(path)
        except OSError:
            _log_offsets[path] = 0
        return lines, os.path.basename(path)

    if _current_log_file != path:
        _current_log_file = path
        _log_offsets[path] = 0

    offset = _log_offsets.get(path, 0)
    lines: List[str] = []
    try:
        size = os.path.getsize(path)
        if size < offset:
            offset = 0
        if size > offset:
            with open(path, "rb") as f:
                f.seek(offset)
                data = f.read()
            if flush or not is_running() or data.endswith(b"\n"):
                lines = data.decode("utf-8", errors="replace").splitlines()
                _log_offsets[path] = offset + len(data)
            else:
                last_nl = data.rfind(b"\n")
                if last_nl == -1:
                    return [], os.path.basename(path)
                complete = data[: last_nl + 1]
                lines = complete.decode("utf-8", errors="replace").splitlines()
                _log_offsets[path] = offset + len(complete)
    except OSError:
        pass
    return lines, os.path.basename(path)


#: 所有实例日志的增量偏移：{instance_name: {"file": path, "offset": int}}
_all_instance_offsets: dict = {}


def _read_instance_log(name: str, path: str, tail: Optional[int], flush: bool) -> List[str]:
    """读取单个实例最新日志文件的增量/末尾行。"""
    global _all_instance_offsets
    if tail is not None:
        ls = _tail_lines(path, tail)
        try:
            _all_instance_offsets[name] = {"file": path, "offset": os.path.getsize(path)}
        except OSError:
            _all_instance_offsets[name] = {"file": path, "offset": 0}
        return ls
    st = _all_instance_offsets.get(name, {"file": None, "offset": 0})
    if st.get("file") != path:
        st = {"file": path, "offset": 0}
    offset = st["offset"]
    ls: List[str] = []
    try:
        size = os.path.getsize(path)
        if size < offset:
            offset = 0
        if size > offset:
            with open(path, "rb") as f:
                f.seek(offset)
                data = f.read()
            if flush or data.endswith(b"\n"):
                ls = data.decode("utf-8", errors="replace").splitlines()
                offset += len(data)
            else:
                last_nl = data.rfind(b"\n")
                if last_nl == -1:
                    return []
                complete = data[: last_nl + 1]
                ls = complete.decode("utf-8", errors="replace").splitlines()
                offset += len(complete)
    except OSError:
        pass
    _all_instance_offsets[name] = {"file": path, "offset": offset}
    return ls


def read_all_instances_logs(tail: Optional[int] = None, flush: bool = False) -> List[str]:
    """聚合所有实例的最新日志（带实例名标记）。"""
    from utils import instance
    lines: List[str] = []
    try:
        entries = instance.list_instances()
    except Exception:
        entries = []
    for entry in entries:
        name = entry.get("name")
        logs_dir = os.path.join(entry.get("base_dir", ""), "logs")
        if not name or not os.path.isdir(logs_dir):
            continue
        try:
            files = [os.path.join(logs_dir, f) for f in os.listdir(logs_dir) if f.endswith(".log")]
        except OSError:
            continue
        if not files:
            continue
        path = max(files, key=os.path.getmtime)
        inst_lines = _read_instance_log(name, path, tail, flush)
        if inst_lines:
            lines.append("=== 实例 %s（%s）===" % (name, os.path.basename(path)))
            lines.extend(inst_lines)
    return lines
