# Steamauto CLI 使用说明（Skill）

> 面向 **AI Agent / 脚本调用者**。说明如何调用 CLI、每个命令的参数、返回什么结果（输出格式 / JSON 字段 / 退出码）。
>
> 命令清单的唯一事实来源是 `python Steamauto.py --help`，本文档把它翻译成「怎么调 + 返回什么 + 怎么判成败」。

## 1. 入口与环境

- 项目路径：`F:\Code\steam\Steamauto`
- 运行方式（PowerShell）：
  ```powershell
  cd F:\Code\steam\Steamauto; python Steamauto.py <命令>
  ```
- 命令**全部是 `--` 长选项风格**，无子命令（旧子命令已删除）。
- 运行 Python：`python`（3.14.3）；测试环境是 `D:\python3\python31109\python.exe`。

## 2. 通用约定（所有命令适用）

### 2.1 退出码（判定成败的关键）

| 退出码 | 含义 | 典型场景 |
|---|---|---|
| `0` | 成功 | 查询到数据、启动/停止/重启成功、配置读写成功；含幂等成功（如停止未运行实例、查询到空结果） |
| `1` | 操作失败 / 业务错误 | 启动/停止失败、删除运行中实例被拒、配置项不存在、平台 API 报错、`--ctl` 未启动时调用 |
| `2` | 参数 / 用法错误 | 缺参数、未知命令、未知参数、写操作未加 `--yes` 被拦截 |
| `3` | 目标「未运行」 | `--status <实例>` 查未运行实例（`--json` 下 `running:false`） |

> 判定成败看进程退出码（PowerShell `$LASTEXITCODE`，CMD `%ERRORLEVEL%`），不要只看输出文字。查询类命令「查不到数据」仍返回 0（命令成功执行），要区分「命令成功」和「业务为空」。

### 2.2 输出格式：表格（默认）vs JSON

- **默认输出是「表格」**（人类可读，中文按显示宽度对齐）。给 Agent / 脚本解析时**必须手动加 `--json`**。
- 加 `--json` 输出机器可读 JSON。
- 例外：`--status`（不带值）与 `--instances` 走「实例列表」渲染，**无 `--json` 开关**，永远输出树形文本。

### 2.3 全局参数 `--instance <name>`

几乎所有命令都可前置 `--instance <name>`，把「当前实例」切换到指定实例（数据目录 `instances/<name>/` 隔离，不同账号/平台组合互不影响）：

```powershell
python Steamauto.py --instance alice --buff balance
python Steamauto.py --instance alice --config --list
```

- 首次 `--instance <name>` 自动创建目录 + 默认 config + 自动分配端口（45917 起递增）。
- `default` 实例 = 不带 `--instance`。
- `--instance` 在 parse 之前被提取（`_extract_instance`），会影响 static 路径，放在命令最前面最稳。

## 3. 命令清单与返回结果

### 3.1 运行 / 服务

| 命令 | 说明 | 返回 |
|---|---|---|
| `python Steamauto.py` | 无参：前台初始化后自动转后台 | 控制台打印初始化过程，成功后交还终端 |
| `--run [-d\|--daemon]` | 前台常驻运行；带 `-d` 直接后台 | 前台常驻不退；`-d` 返回 0 |
| `--start` | 后台启动 | 成功：`[OK] Steamauto 已在后台启动（PID …）`+ 日志路径，退出码 0；已运行：报「已在运行」非 0 |
| `--stop [--force]` | 停止（默认优雅，`--force` 强杀） | 成功：`[OK] Steamauto 已停止（PID …）`，0；未运行：幂等成功 0 |
| `--restart [--force]` | 重启 | 同上 |
| `--status [<实例名>\|all\|account]` | 查看状态 | 见下 |

**`--status` 的三种形态与返回**：

- `--status`（不带值）＝ `all`：所有实例的树形列表（`*` 标记当前实例，含 PID / 运行状态 / 数据目录）。退出码 0。**此形态无 `--json`**。
- `--status <实例名>`：单实例状态。加 `--json` 输出：
  ```json
  { "pid": 72756, "version": "5.9.1", "started_at": 1789336899.0,
    "mode": "daemon", "log_file": "…", "console_log": "…",
    "port": 45917, "host": "127.0.0.1", "instance": "default", "running": true }
  ```
  **运行中退出码 0，未运行退出码 3**。
- `--status account`：各平台登录/连接状态（见 3.4）。

### 3.2 实例（多开，数据目录隔离）

| 命令 | 说明 | 返回 |
|---|---|---|
| `--instances` | 列出所有实例及运行状态 | 树形文本（`*` 当前实例），退出码 0 |
| `--instance <NAME> --run` | 启动指定实例 | 同 `--run` |
| `--instance <NAME> --status` | 查看指定实例状态 | 同 `--status <实例名>` |
| `--instance <NAME> --remove` | 删除实例（运行中拒绝） | 成功 0；运行中报错退出码 1 |
| `--instance <NAME> --rename <新名>` | 重命名实例 | 成功 0；运行中报错 1 |

### 3.3 日志

| 命令 | 说明 | 返回 |
|---|---|---|
| `--log` | 翻阅最新日志（末尾 50 行） | 日志文本，退出码 0 |
| `--log 200` / `--log -n 200` | 末尾 200 行 | 同上 |
| `--log console` / `--log --console` | 后台控制台日志 | 同上 |
| `--log app` | 应用（技术）日志 | 同上 |
| `--log error\|warning\|info\|debug` | 按级别过滤（error 只错误 / debug 全显） | 同上 |
| `--log [-f\|--follow]` | 持续跟随（Ctrl+C 退出） | 流式输出 |
| `--log --file <PATH>` | 指定日志文件 | 同上 |

### 3.4 账号

| 命令 | 说明 | 返回 |
|---|---|---|
| `--status account [--json\|--table] [--no-live]` | 各平台登录/连接状态 | 见下 |
| `--login <平台>` | 登录（需交互终端：BUFF 扫码 / UU 短信） | 交互式；成功 0 |
| `--logout <平台>` | 登出（清凭据与相关配置项） | 成功 0 |

**`--status account --json` 返回结构**（实测）：

```json
{
  "source": "运行中的进程（实时）", "live": true,
  "accounts": {
    "buff": { "platform": "buff", "display": "BUFF（网易BUFF）",
      "configured": true, "logged_in": true, "connected": true,
      "account": "洛北辰", "credential_file": "…buff_cookies_xxx.txt",
      "error": null, "balance": "156.18" },
    "uu":   { "…": "同结构", "balance": 6.53 },
    "c5":   { "configured": false, "logged_in": false, "error": "未配置 AppKey…" },
    "eco":  { "configured": false, "logged_in": false, "error": "未配置 partnerId…" }
  },
  "steam": { "platform": "steam", "configured": true, "logged_in": false,
    "account": "529918871", "error": "当前为离线模式（未登录 Steam）…" }
}
```

- 每平台字段：`platform / display / configured / logged_in / connected / account / credential_file / error / balance`。
- `--no-live`：只读本地凭据，不联网校验（更快）。
- 平台名：`buff | uu | c5 | eco`，逗号分隔多个，大小写不敏感；别名 `buffapi | uuyoupin | c5game | ecosteam`。

### 3.5 配置

| 命令 | 说明 | 返回 |
|---|---|---|
| `--config --get <KEY>` | 读配置值（点分路径，如 `c5_auto_accept_offer.app_key`） | 值，退出码 0；不存在 1 |
| `--config --set <KEY> <VALUE> [--str] [--no-apply]` | 改配置（保留注释）；多值成数组（`--set k A B` 或 `--set k '["A","B"]'`） | 成功 0 |
| `--config --unset <KEY>` | 删配置项 | 成功 0 |
| `--config --list [--json]` | 列出全部配置 | 表格或 JSON |
| `--config --reload` | 让运行中进程重读配置 | 成功 0 |

### 3.6 平台 API（BUFF / UU / C5 / ECO）

调用格式：`python Steamauto.py --buff <op> [args] [--table|--json]`（`--buff` 可换 `--uu` / `--c5` / `--eco`）。默认表格，`--json` 拿 JSON。每个平台 `--<平台> --help` 看全部操作。

**BUFF（`--buff`）21 个 op**：

- 只读：`balance` `nickname` `search <关键词> [game]` `search-market <关键词> [页]` `inventory` `on-sale [页]` `sell-history [appid]` `buy-order <goods_id>` `sell-order <goods_id>` `bill-order <goods_id>` `highest-buy <goods_id>` `lowest-sell <goods_id>` `waiting-offer` `item-map [--refresh]`
- 写（默认需二次确认 / `--yes`）：`list <assetid> <price>` `undercut <assetid>` `sell-bidder <assetid> <goods_id>` `off-shelf <sell_order_id>...` `change-price <sell_order_id> <price>` `set-remark <assetid> <文字>` `buy <goods_id> <sell_order_id> <price> [pay_method]`

**UU（`--uu`）17 个 op**：

- 只读：`balance` `nickname` `inventory` `on-sale` `leased-out` `wait-deliver` `buy-order [页]` `search <关键词>` `highest-buy <template_id>` `lowest-sell <template_id>` `item-map [--refresh]`
- 写（默认需二次确认）：`sell <assetid> <price>` `undercut <assetid>` `off-shelf <commodity_id>...` `buy <template_id> <price> [num]` `change-price <commodity_id> <price>`；`sell-bidder` **已禁用**（UU 网页 API 不支持塞求购，仅 APP 支持）。

**C5（`--c5`）**：`balance` `orders [status] [page]`（0 全部 / 10 完成 / 11 取消）`check-key`
**ECO（`--eco`）**：`balance` `on-sale` `inventory`

### 3.7 调试

| 命令 | 说明 | 返回 |
|---|---|---|
| `--ctl <COMMAND> [k=v ...]` | 直接向控制通道发指令 | 指令返回；未启动时退出码 1 |
| `--help` / `--<平台> --help` | 帮助 | 命令树，退出码 0 |

## 4. 关键 JSON 返回结构（实测样本）

### 4.1 余额（`--buff balance --json` / `--uu balance --json`）

```json
{ "available": "156.18", "trading_only": "0", "frozen": "0", "total": "156.18" }
```

（BUFF 全是字符串；UU 的 `balance` 字段可能是 number。）

### 4.2 库存（`--buff inventory --json` / `--uu inventory --json`）

返回**数组**，每项：

```json
{ "assetid": "53469172739", "market_hash_name": "CS20 Sticker Capsule",
  "name": "反恐精英20周年印花胶囊", "goods_id": 773534,
  "sell_order_price": "0", "sell_min_price": "4.05", "buy_max_price": "0",
  "state_text": "可出售", "steam_price": "0.93", "remark": "", "buy_price": null }
```

## 5. 写操作的安全约定

写操作（上架 / 塞求购 / 下架 / 改价 / 购买）**默认需二次确认**：

- CLI 层：非交互式终端（Hermes terminal）必须加 `--yes` 才会真实执行，否则拦截（退出码 2）；`--dry-run` 只预览参数不执行。
- 实盘白名单 `LIVE_ALLOW`：仅白名单饰品可真实下单。
- 全自动交易时，只需让调用统一加 `--yes` 并放宽风控规则，CLI 代码无需改动。
