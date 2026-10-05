# zui

ComfyUI 环境操作系统：装得稳、跑得快、坏能修、换机就走。

状态：**M0–M5 主链路已贯通（M6 前端与分发未做）** —— 环境接管/自愈、进程托管与启动闭环、参数表与档位、快照回滚、加速闭环、模型资产与预算、自定义节点管理均可运行；`uv run python -m pytest` 146 项全绿，架构闸门通过。

## 快速开始

```bash
uv python install 3.12
uv sync
uv run zui --help
uv run zui info
uv run zui info --json
```

日常闭环：

```bash
uv run zui instance adopt F:\ZAI\ComfyUI      # 接管现有环境（只读诊断）
uv run zui env doctor zai                     # 体检（规则来自 rules/doctor_rules.yaml）
uv run zui env repair zai --apply             # 自愈，先快照、失败回滚
uv run zui args capture zai                   # 抓取带默认值的完整参数表
uv run zui launch zai --pypi-mirror tsinghua  # 启动（镜像会真正传给子进程）
uv run zui run report zai                     # 单次运行性能报告
uv run zui stop zai                           # 停进程树
```

模型与节点：

```bash
uv run zui model scan zai --hash              # 建索引（可选 blake3）
uv run zui model report zai                   # 重复（按内容 hash）与体积分布
uv run zui model unused zai                   # 未被任何工作流引用的资产
uv run zui node list zai                      # 自定义节点（git 状态 / 启用状态）
uv run zui node rollback <节点> --apply       # 回到上一个 commit
```

## 架构铁律

1. ComfyUI 是黑盒：只用「进程 + HTTP/WS + 日志」，永不 import ComfyUI 模块。
2. 管理器跑独立解释器，绝不跑在被管理的 venv 里。
3. OS / GPU 差异只允许出现在 `src/zui/platform/` 与 `src/zui/gpu/`。
4. 任何破坏性操作：快照 → 执行 → 可回滚。

以上规则由 CI 自动校验：

```bash
uv run python scripts/check_architecture.py
```

## 目录

```
src/zui/
  cli/          CLI 子命令、退出码契约、单行 JSON 输出
  platform/     OS 适配（Windows / macOS / Linux）
  gpu/          GPU 能力抽象（NVIDIA / AMD / Intel / Apple / none）
  core/         环境、进程、快照、资产、节点、诊断
  store/        SQLite schema 与唯一写库入口
rules/          外部化数据与规则（仓库根），`zui rules update` 可远端刷新并备份
ui/             单文件原生 HTML 工作台（React/TS/xterm.js 尚未实现，见 M6）
scripts/        架构闸门与辅助脚本
tests/          契约、平台、进程树与核心模块测试
docs/           cli.md
```

## 里程碑

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| M0 | 骨架、进程托管、CLI 契约、实例接管（只读） | ✅（DI 接口层 `interfaces.py` 尚未接入，属空壳） |
| M1 | 环境自愈、子进程环境变量注入、镜像策略、torch 换栈 | ✅（`env create --stack` / `env repair` / 镜像均已落到真实命令） |
| M2 | 启动参数可视化、参数档位、快照 diff/回滚、自定义节点管理 | ✅（多实例为「注册表 + 端口自动分配」，无并发编排） |
| M3 | NVML 采样、日志流水线解析、单次运行性能报告、磁盘测速 | ✅ |
| M4 | 环境体检规则引擎、一键加速闭环（可回滚） | ✅（检查项由 `rules/doctor_rules.yaml` 驱动，可远端更新） |
| M5 | 模型资产索引、重复（按内容 hash）/未使用识别、工作流资源预算 | ✅（**迁移到 SSD 明确不做**） |
| M6 | xterm.js 终端、一键诊断包、`uv tool` 分发、三端 CI | ⏳（诊断包与三端 CI 已有；xterm.js 前端与 `uv tool` 分发未做） |
