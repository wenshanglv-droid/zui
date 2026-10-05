# zui CLI 契约

所有子命令：

- 支持 `--json`（**单行** JSON 输出，便于 Agent / systemd / NSSM 解析）
- 使用稳定退出码
- 网络请求统一 3 秒超时，失败降级不阻塞
- 破坏性操作默认 dry-run，需显式 `--apply` 才落盘

设置 `ZUI_DATA_DIR` 可覆盖数据目录（默认 `%LOCALAPPDATA%\zui` / `~/Library/Application Support/zui` / `~/.local/share/zui`），测试与便携版使用。

## 退出码

| 码 | 语义 | 典型命令 |
| --- | --- | --- |
| 0 | 成功 / 目标运行中 | `status` 运行中、`stop` 幂等成功 |
| 1 | 运行时异常 | 修复失败、安装失败 |
| 2 | 用法错误 | 子命令或参数不合法 |
| 3 | 目标未运行 | `status` 未运行（**非错误语义**） |
| 4 | 超时 | `start` 等待 `/system_stats` 超时 |

## 输出 schema

成功：

```json
{"ok":true,"data":{"key":"value"}}
```

失败：

```json
{"ok":false,"error":{"code":"ENV_BROKEN","msg":"...","hint":"..."}}
```

## 命令计划（随里程碑补齐）

| 命令 | 里程碑 | 说明 |
| --- | --- | --- |
| `zui version` | M0 | 版本号 |
| `zui info [--json]` | M0 | 平台 / GPU 能力 / 数据目录 |
| `zui serve --port 0` | M0 | 本地守护进程（REST + WebSocket） |
| `zui instance adopt --path <dir>` | M0 | 接管现有 ComfyUI 环境（只读诊断） |
| `zui instance list` | M0 | 实例注册表 |
| `zui env inspect <inst>` | M1 | 环境与 torch 栈检查 |
| `zui env doctor <inst>` | M1 | 体检报告 |
| `zui env repair <inst> [--apply]` | M1 | 环境自愈，默认 dry-run |
| `zui env create <name> --stack <id> [--apply]` | M1 | 用 uv 建新环境，默认 dry-run |
| `zui env stacks` | M1 | 列出 torch 栈矩阵与推荐栈 |
| `zui launch <inst> [--profile p] [--dry-run]` | M2 | 启动，`--dry-run` 打印完整 env + 命令行 |
| `zui stop <inst> [--force]` | M0 | 停进程树（不按端口扫杀） |
| `zui status <inst>` | M0 | 基于 pid-file + 心跳 |
| `zui logs <inst>` | M0 | 日志流（VT100 语义） |
| `zui snapshot create/diff/restore` | M2 | 快照与回滚 |
| `zui args capture <inst> [--static]` | M2 | 抓取参数表（venv 损坏时自动静态解析 `cli_args.py`） |
| `zui args show <inst> [--form]` | M2 | 查看参数表 / 输出 UI 表单 schema |
| `zui args preview <inst> [--profile p]` | M2 | 打印档位最终命令行 + 环境差异 |
| `zui args set <inst> --profile p --arg X` | M2 | 保存启动档位 |
| `zui args list <inst>` | M2 | 列出档位 |
| `zui node list/install/update/rollback/enable/disable` | M2 | 自定义节点管理，`install/update/rollback` 默认 dry-run |
| `zui model scan/report/unused/budget` | M5 | 模型资产；`unused` 比对工作流引用，重复按内容 hash 判重 |
| `zui run report <inst> --last` | M3 | 单次运行性能报告 |
| `zui optimize analyze/apply <inst>` | M4 | 一键加速（可回滚），`apply` 默认 dry-run |
| `zui rules list/update --url <base> [--apply]` | M4 | 外部化规则查看与远端更新，默认 dry-run |
| `zui instance remove <inst> [--apply]` | M2 | 注销实例（不删文件），默认 dry-run |
| `zui diag export` | M6 | 一键诊断包 |

## 约定补充

- **端口**：`launch` 请求的端口若已被其它进程占用，自动顺延到下一个空闲端口，并在 `port_note` 中说明；永不杀掉占用端口的进程。
- **镜像**：`launch` / `optimize apply` / `env create` / `node *` 都接受 `--pypi-mirror`（`official` / `aliyun` / `tsinghua` / `huaweicloud` 或直填 URL），并透传给子进程 `PIP_INDEX_URL` / `UV_INDEX_URL` / `UV_DEFAULT_INDEX`；`--hf-mirror` 走 `HF_ENDPOINT`。未指定时继承进程环境。
- **规则**：体检项由 `rules/doctor_rules.yaml` 的顺序与 `platforms` 字段驱动，可用 `zui rules update` 从远端刷新（写前备份 `.bak`）。
