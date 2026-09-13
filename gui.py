"""Steamauto GUI 启动入口（可选入口，不侵入核心代码）。

用法：
    python gui.py [--host 127.0.0.1] [--port 8080] [--no-browser] [--instance default]
    python gui.py --start [--port 8080] [--instance default]    # 后台启动（脱离终端）
    python gui.py --stop [--instance default]                   # 停止后台 GUI
    python gui.py --restart [--instance default]                # 重启后台 GUI
    python gui.py --log [gui|cli|core|all] [-n 50] [--debug|--info|--warning|--error]

    --log 来源：gui=GUI 日志；cli=CLI/Steamauto 控制台日志；core=核心技术日志；
                默认 all（全部）。级别：debug 全显 → error 只错误（标准 logging 语义）。

启动后浏览器访问 http://127.0.0.1:8080
"""
import argparse
import os
import sys
import threading
import webbrowser

# 保证以 `python gui.py` 方式运行时能导入同目录下的 gui 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui.server import app  # noqa: E402


# ---------------------------------------------------------------- 日志工具
_LOG_LEVELS = {"debug": 10, "info": 20, "warning": 30, "warn": 30, "error": 40}


def _line_level(line):
    """解析日志行级别数值；无法识别返回 0（视为 debug，总是显示）。"""
    import re
    m = re.search(r"\]\s*-\s*(\w+):", line)
    if not m:
        return 0
    return _LOG_LEVELS.get(m.group(1).lower(), 0)


def _filter_by_level(lines, level_name):
    """按级别过滤：只保留级别 >= 阈值的行（debug 全显，error 只错误）。"""
    threshold = _LOG_LEVELS.get(level_name, 0)
    return [l for l in lines if _line_level(l) >= threshold]


def _read_tail(path, lines):
    """读文件末尾若干行。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            data = f.readlines()
    except OSError:
        return []
    return [line.rstrip("\r\n") for line in data[-max(1, int(lines)):]]


def _dump_log(path, lines, level, label=None):
    tail = _read_tail(path, lines)
    if level:
        tail = _filter_by_level(tail, level)
    tag = ("[%s] " % label) if label else ""
    level_txt = ("，级别 %s" % level) if level else ""
    print("== %s%s（末尾 %d 行%s）==" % (tag, path, len(tail), level_txt))
    for line in tail:
        print(line)
    print()


def _show_log(source="all", lines=50, level=None):
    """查看日志。source: gui/cli/core/all。返回退出码。"""
    from utils import daemon, static

    folder = static.LOGS_FOLDER
    if not os.path.isdir(folder):
        print("未找到日志目录：%s" % folder)
        return 1

    if source == "gui":
        path = os.path.join(folder, "gui.log")
        if not os.path.exists(path):
            print("未找到 GUI 日志：%s" % path)
            return 1
        _dump_log(path, lines, level)
        return 0

    if source == "cli":
        path = daemon.latest_log_file("console")
        if not path:
            print("未找到 CLI 日志（目录：%s）" % folder)
            return 1
        _dump_log(path, lines, level)
        return 0

    if source == "core":
        path = daemon.latest_log_file("app")
        if not path:
            print("未找到 core 日志（目录：%s）" % folder)
            return 1
        _dump_log(path, lines, level)
        return 0

    # all：gui + cli(console) + core(app)
    found = False
    gui_path = os.path.join(folder, "gui.log")
    if os.path.exists(gui_path):
        _dump_log(gui_path, lines, level, label="gui")
        found = True
    console_path = daemon.latest_log_file("console")
    if console_path:
        _dump_log(console_path, lines, level, label="cli")
        found = True
    app_path = daemon.latest_log_file("app")
    if app_path:
        _dump_log(app_path, lines, level, label="core")
        found = True
    if not found:
        print("未找到日志文件（目录：%s）" % folder)
        return 1
    return 0


# ---------------------------------------------------------------- 进程管理
def _gui_pid_file():
    """后台 GUI 的 PID 文件（跟随实例数据目录）。"""
    from utils import static
    return os.path.join(static.RUN_FOLDER, "gui.pid")


def _pid_alive(pid):
    """判定 PID 是否对应一个存活进程（不发送会终止进程的信号）。"""
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _start_background(args):
    """后台启动 GUI（脱离终端），记录 PID。"""
    import subprocess
    from utils import static

    pid_file = _gui_pid_file()
    if os.path.exists(pid_file):
        try:
            with open(pid_file, encoding="utf-8") as f:
                old_pid = int(f.read().strip())
            if _pid_alive(old_pid):
                print("GUI 已在运行（PID %s）" % old_pid)
                return
        except (OSError, ValueError):
            pass

    script = os.path.join(static.PROJECT_ROOT, "gui.py")
    cmd = [sys.executable, script, "--run", "--instance", args.instance, "--no-browser"]
    if args.host != "127.0.0.1":
        cmd += ["--host", args.host]
    if args.port != 8080:
        cmd += ["--port", str(args.port)]

    os.makedirs(static.RUN_FOLDER, exist_ok=True)
    os.makedirs(static.LOGS_FOLDER, exist_ok=True)
    log_path = os.path.join(static.LOGS_FOLDER, "gui.log")
    kwargs = {
        "cwd": static.PROJECT_ROOT,
        "stdin": subprocess.DEVNULL,
        "env": os.environ.copy(),
    }
    with open(log_path, "ab") as out:
        kwargs["stdout"] = out
        kwargs["stderr"] = out
        if os.name == "nt":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP：脱离当前终端独立运行
            kwargs["creationflags"] = 0x00000008 | 0x00000200
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(cmd, **kwargs)

    with open(pid_file, "w", encoding="utf-8") as f:
        f.write(str(proc.pid))

    print("GUI 已在后台启动（PID %s）" % proc.pid)
    print("  访问地址：http://%s:%d" % (args.host, args.port))
    print("  日志：%s" % log_path)
    print("  停止：python gui.py --stop --instance %s" % args.instance)


def _stop_background(quiet=False):
    """停止后台 GUI。返回退出码。"""
    import subprocess
    from utils import static

    pid_file = _gui_pid_file()
    if not os.path.exists(pid_file):
        if not quiet:
            print("GUI 未在运行")
        return 0

    try:
        with open(pid_file, encoding="utf-8") as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        if not quiet:
            print("PID 文件无效，已清理")
        try:
            os.remove(pid_file)
        except OSError:
            pass
        return 0

    if not _pid_alive(pid):
        try:
            os.remove(pid_file)
        except OSError:
            pass
        if not quiet:
            print("GUI 未在运行（PID %s 已退出）" % pid)
        return 0

    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)
    else:
        import signal
        os.kill(pid, signal.SIGTERM)

    try:
        os.remove(pid_file)
    except OSError:
        pass
    print("GUI 已停止（PID %s）" % pid)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Steamauto 图形控制台")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8080, help="监听端口")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--instance", metavar="NAME", default="default",
                        help="实例名（数据目录 instances/<name>；默认 default）")
    parser.add_argument("--start", action="store_true", help="后台启动 GUI（脱离终端运行）")
    parser.add_argument("--stop", action="store_true", help="停止后台运行的 GUI")
    parser.add_argument("--restart", action="store_true", help="重启后台运行的 GUI（先停后启）")
    parser.add_argument("--log", nargs="?", const="all", metavar="[gui|cli|core|all]",
                        help="查看日志（默认 all；可指定 gui/cli/core）")
    parser.add_argument("-n", "--lines", type=int, default=50, help="配合 --log：显示末尾行数（默认 50）")
    level = parser.add_mutually_exclusive_group()
    level.add_argument("--debug", action="store_const", const="debug", dest="log_level", help="配合 --log：debug 级别（全显）")
    level.add_argument("--info", action="store_const", const="info", dest="log_level", help="配合 --log：info 及以上")
    level.add_argument("--warning", "--warn", action="store_const", const="warning", dest="log_level", help="配合 --log：warning 及以上")
    level.add_argument("--error", action="store_const", const="error", dest="log_level", help="配合 --log：仅 error")
    parser.add_argument("--run", action="store_true", help=argparse.SUPPRESS)  # 内部：前台常驻
    args = parser.parse_args()

    # 激活实例：切换数据目录到 instances/<name>/，与 CLI / 后台子进程读写同一份数据
    from utils import instance

    instance.activate(args.instance)

    if args.log is not None:
        return _show_log(args.log, args.lines, args.log_level)
    if args.start:
        return _start_background(args)
    if args.stop:
        return _stop_background()
    if args.restart:
        _stop_background(quiet=True)
        return _start_background(args)

    # 前台运行（默认 或 --run）
    url = "http://%s:%d" % (args.host, args.port)
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print("Steamauto GUI 已启动，请访问: " + url)
    print("按 Ctrl+C 退出。")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
