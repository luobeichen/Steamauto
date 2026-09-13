"""Steamauto GUI 的配置读写模块。

数据路径（config/logs/session）复用 utils.static 的路径常量，因此会跟随
``--instance <name>`` 切换（``instances/<name>/``），与 CLI / 后台子进程读写同一份数据。
"""
import json5
import os
from typing import Optional, Tuple, Union

from utils import static

# 项目根目录（代码根，不变；用于定位 Steamauto.py、gui/ 等）
PROJECT_ROOT = static.PROJECT_ROOT

# 数据路径转发：config_editor.<NAME> 动态转发到 static.<NAME>，跟随
# static.set_base_dir（--instance 切换）实时变化。旧代码里的
# config_editor.CONFIG_FILE_PATH / .ACCOUNT_FILE_PATH / .LOGS_FOLDER_PATH 无需改动，
# 命中下方 __getattr__。
_PATH_FORWARD = {
    "CONFIG_FILE_PATH": "CONFIG_FILE_PATH",
    "ACCOUNT_FILE_PATH": "STEAM_ACCOUNT_INFO_FILE_PATH",
    "LOGS_FOLDER_PATH": "LOGS_FOLDER",
    "CONFIG_FOLDER": "CONFIG_FOLDER",
    "LOGS_FOLDER": "LOGS_FOLDER",
    "SESSION_FOLDER": "SESSION_FOLDER",
}


def __getattr__(name):
    target = _PATH_FORWARD.get(name)
    if target is not None:
        return getattr(static, target)
    raise AttributeError("module 'gui.config_editor' has no attribute %r" % name)

# 账号信息默认值（对应 utils/static.py 的 DEFAULT_STEAM_ACCOUNT_JSON）
ACCOUNT_DEFAULT = {
    "shared_secret": "",
    "identity_secret": "",
    "steam_username": "",
    "steam_password": "",
}


def read_text(path: str) -> Optional[str]:
    """读取文件原文，不存在返回 None。"""
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def validate_json5(text: str) -> Tuple[bool, Union[object, str]]:
    """校验 JSON5 文本，返回 (ok, value_or_error)。"""
    try:
        value = json5.loads(text)
        return True, value
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def save_text(path: str, text: str) -> Tuple[bool, str]:
    """校验后写回文本（保留注释等原文），返回 (ok, msg)。"""
    ok, value = validate_json5(text)
    if not ok:
        return False, "JSON5 语法错误：" + str(value)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return True, "保存成功"


def load_json5(path: str) -> Optional[dict]:
    """解析配置文件为 dict，失败返回 None。"""
    text = read_text(path)
    if text is None:
        return None
    ok, value = validate_json5(text)
    return value if ok and isinstance(value, dict) else None


def save_json5(path: str, obj: dict) -> bool:
    """将 dict 序列化写回（注意：会丢失原文注释）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = json5.dumps(obj, indent=2, ensure_ascii=False, trailing_commas=False)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return True
