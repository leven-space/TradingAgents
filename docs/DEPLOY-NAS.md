# TradingAgents 绿联 NAS 部署手册

本文说明如何在 Mac 上构建 Docker 镜像、导出后在绿联 NAS 上离线导入并运行。所有业务配置通过 **环境变量 / `.env` 文件** 外置，无需重新构建镜像即可修改 LLM 提供商、模型、语言等参数。

---

## 1. 架构概览

```
Mac（Apple Container / Docker）
  └─ 构建镜像 → 导出 .tar
        ↓ U 盘 / 网络共享
绿联 NAS（Docker）
  └─ docker load 导入镜像
  └─ docker-compose.nas.yml + .env + data/ 目录
  └─ docker compose run 启动交互式 CLI
```

持久化数据（缓存、分析报告、决策日志）保存在宿主机 `./data` 目录，升级镜像不会丢失。

---

## 2. 确认 NAS 平台架构

| NAS 类型 | 常见 CPU | 构建平台参数 |
|----------|----------|--------------|
| Intel 系列（如 DXP、DX 部分型号） | x86_64 | `linux/amd64` |
| ARM 系列（如 **DH4300 Plus**、DXP4800 Plus 等 RK3588） | aarch64 | `linux/arm64` |

**绿联 DH4300 Plus**：瑞芯微 RK3588C（ARM64 / aarch64），8GB 内存。请使用 `linux/arm64` 镜像；在 Apple Silicon Mac 上构建的 arm64 包可直接导入，**无需**再构建 amd64 版本。

在 NAS SSH 中执行 `uname -m`：
- 输出 `x86_64` → 使用 `linux/amd64`
- 输出 `aarch64` → 使用 `linux/arm64`（DH4300 Plus 应为此值）

**重要**：镜像平台必须与 NAS 一致，否则无法运行。

### DH4300 Plus 补充说明

| 项目 | 建议 |
|------|------|
| 镜像平台 | `linux/arm64` |
| 镜像格式 | 须为 **Docker load 格式**（tar 内含 `manifest.json`），见下文「OCI vs Docker 格式」 |
| 推荐用法 | 云端 LLM（OpenAI / DeepSeek 等），容器主要做编排与数据缓存，8GB 内存足够 |
| Ollama 本地模型 | 8GB 内存较紧，仅适合小模型（如 3B–7B）；大模型建议用 API 或外网 Ollama |
| 运行方式 | SSH 进 NAS 后 `docker compose run`（Rich TUI 需完整终端） |
| 部署路径示例 | `/volume1/docker/tradingagents`（以 UGOS 实际共享目录为准） |

---

## 2.1 OCI vs Docker 格式（重要）

| 工具 | 导出命令 | tar 内关键文件 | 绿联 `docker load` |
|------|----------|----------------|-------------------|
| Apple Container | `container image save` | `index.json` + `oci-layout` | **不支持** |
| Docker | `docker save` | `manifest.json` | 支持 |
| skopeo 转换后 | 见下方 | `manifest.json` | 支持 |

若在 NAS 导入时报错：

```text
invalid archive: does not contain a manifest.json
```

说明 tar 是 **OCI 格式**，需重新导出或转换。

**推荐**：使用项目脚本（已自动转换）：

```bash
brew install skopeo   # Apple Container 用户必需
./scripts/docker-build-export.sh 0.3.0 linux/arm64
```

**已有 OCI tar 时手动转换**（DH4300 Plus 用 arm64）：

```bash
brew install skopeo
./scripts/docker-convert-oci-to-docker.sh \
  dist/tradingagents-0.3.0-linux-arm64.tar \
  dist/tradingagents-0.3.0-linux-arm64-docker.tar

# NAS 上导入转换后的文件
docker load -i tradingagents-0.3.0-linux-arm64-docker.tar
```

验证 tar 格式（Mac 上）：

```bash
tar -tf dist/tradingagents-0.3.0-linux-arm64.tar | head -5
# Docker 格式应看到 manifest.json
# OCI 格式则是 oci-layout、index.json
```

---

## 3. 在 Mac 上构建并导出镜像

### 3.1 前置条件

- 已安装 [Apple Container](https://github.com/apple/container)（`container` 命令）或 Docker
- **Apple Container 用户还需安装 skopeo**（用于 OCI → Docker 格式转换）：

```bash
brew install skopeo
```

- 项目根目录已 clone 到本地

### 3.2 一键构建导出

```bash
cd /path/to/TradingAgents
chmod +x scripts/docker-build-export.sh

# DH4300 Plus（ARM）
./scripts/docker-build-export.sh 0.3.0 linux/arm64

# Intel 绿联 NAS
./scripts/docker-build-export.sh 0.3.0 linux/amd64
```

产物位于 `dist/tradingagents-0.3.0-linux-arm64.tar`，为 **Docker load 兼容格式**（内含 `manifest.json`）。

### 3.3 手动命令（Apple Container + skopeo）

```bash
container build -t tradingagents:0.3.0 -f Dockerfile --platform linux/arm64 .
container image save -o /tmp/tradingagents-oci.tar tradingagents:0.3.0

skopeo --override-os linux --override-arch arm64 copy \
  oci-archive:/tmp/tradingagents-oci.tar \
  docker-archive:dist/tradingagents-0.3.0-linux-arm64.tar:tradingagents:0.3.0
```

### 3.4 手动命令（Docker）

```bash
docker build -t tradingagents:0.3.0 -f Dockerfile --platform linux/amd64 .
docker save -o dist/tradingagents-0.3.0-linux-amd64.tar tradingagents:0.3.0
```

---

## 4. 在绿联 NAS 上部署

### 4.1 准备部署目录

在 NAS 上创建专用目录（示例路径，可按习惯调整）：

```bash
mkdir -p /volume1/docker/tradingagents
cd /volume1/docker/tradingagents
mkdir -p data/{cache,logs,memory}
```

将以下文件复制到该目录：

| 文件 | 说明 |
|------|------|
| `dist/tradingagents-*.tar` | 镜像包 |
| `docker-compose.nas.yml` | Compose 配置 |
| `.env.example` | 配置模板 |

### 4.2 导入镜像

通过 SSH 或绿联 Docker 图形界面「导入镜像」：

```bash
docker load -i tradingagents-0.3.0-linux-amd64.tar
docker images | grep tradingagents
# 应看到 tradingagents   0.3.0   ...
```

### 4.3 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，至少填写所用 LLM 的 API Key
```

**推荐**：在 `.env` 中同时设置 `TRADINGAGENTS_*` 变量，跳过 CLI 交互式选择，适合 NAS 无图形终端场景：

```dotenv
# --- LLM API Keys（按需填写）---
OPENAI_API_KEY=sk-...

# --- 非交互运行配置（推荐在 NAS 上设置）---
TRADINGAGENTS_LLM_PROVIDER=openai
TRADINGAGENTS_DEEP_THINK_LLM=gpt-5.5
TRADINGAGENTS_QUICK_THINK_LLM=gpt-5.4-mini
TRADINGAGENTS_OUTPUT_LANGUAGE=Chinese
TRADINGAGENTS_MAX_DEBATE_ROUNDS=1
TRADINGAGENTS_MAX_RISK_ROUNDS=1
TRADINGAGENTS_TEMPERATURE=0.0
TRADINGAGENTS_CHECKPOINT_ENABLED=true

# --- 可选：宏观数据 ---
# FRED_API_KEY=...

# --- 时区 ---
TZ=Asia/Shanghai
```

完整可配置项见 `.env.example` 注释及 `tradingagents/default_config.py` 中的 `_ENV_OVERRIDES`。

Azure 企业版额外复制 `.env.enterprise.example` 为 `.env.enterprise` 并填写。

### 4.4 启动

**交互式分析（需 TTY，推荐 SSH 终端）：**

```bash
docker compose -f docker-compose.nas.yml run --rm tradingagents
```

**查看帮助：**

```bash
docker compose -f docker-compose.nas.yml run --rm tradingagents --help
```

**非交互参数示例（若 CLI 支持直接传 ticker）：**

```bash
docker compose -f docker-compose.nas.yml run --rm tradingagents analyze AAPL
```

> 具体子命令以 `tradingagents --help` 输出为准。

### 4.5 使用 Ollama 本地模型（可选）

若 NAS 内存充足（建议 16GB+），可启用 `ollama` profile：

```bash
# 先启动 Ollama 并拉取模型
docker compose -f docker-compose.nas.yml --profile ollama up -d ollama
docker compose -f docker-compose.nas.yml exec ollama ollama pull llama3.2

# 运行 TradingAgents（自动连接同 compose 网络中的 ollama 服务）
docker compose -f docker-compose.nas.yml --profile ollama run --rm tradingagents-ollama
```

若 Ollama 运行在 NAS 其他地址，在 `.env` 中设置：

```dotenv
OLLAMA_BASE_URL=http://192.168.1.100:11434/v1
```

---

## 5. 目录与数据说明

| 宿主机路径 | 容器路径 | 内容 |
|------------|----------|------|
| `./data/cache` | `/home/appuser/.tradingagents/cache` | 行情/新闻缓存 |
| `./data/logs` | `/home/appuser/.tradingagents/logs` | 分析报告输出 |
| `./data/memory` | `/home/appuser/.tradingagents/memory` | 决策记忆日志 |

自定义数据目录（可选）：

```dotenv
TRADINGAGENTS_DATA_DIR=/volume1/docker/tradingagents/data
```

---

## 6. 升级镜像

1. 在 Mac 重新构建并导出新版 tar
2. NAS 上 `docker load -i tradingagents-<新版本>.tar`
3. 修改 `.env` 或 compose 中的镜像 tag：

```dotenv
TRADINGAGENTS_IMAGE=tradingagents:0.4.0
```

或在命令行：

```bash
TRADINGAGENTS_IMAGE=tradingagents:0.4.0 docker compose -f docker-compose.nas.yml run --rm tradingagents
```

`./data` 目录无需变动。

---

## 7. 常见问题

### 镜像导入后无法启动：`exec format error`

镜像平台与 NAS CPU 不匹配。请用正确的 `--platform` 重新构建（见第 2 节）。

### 导入报错：`invalid archive: does not contain a manifest.json`

tar 为 Apple Container 的 **OCI 格式**，绿联 Docker 无法直接 `docker load`。请使用 `scripts/docker-build-export.sh` 重新导出，或对已有 OCI tar 运行 `scripts/docker-convert-oci-to-docker.sh`（见第 2.1 节）。

### 容器内无法写入文件

确保 `data` 目录权限允许容器用户写入：

```bash
chmod -R 777 data   # 或 chown 为 Docker 运行用户
```

### CLI 在 NAS 图形终端中显示异常

TradingAgents 使用 Rich TUI，需要完整 TTY。请通过 **SSH** 连接 NAS 执行 `docker compose run`，或在绿联 Docker 的「终端」功能中运行。

### 修改配置后不生效

确认修改的是 **部署目录下的 `.env`**，且变量名正确。重启容器后 `TRADINGAGENTS_*` 会在启动时自动加载（见 `tradingagents/__init__.py`）。

### 需要连接 NAS 外的 LLM / Ollama

确保 NAS 防火墙允许出站 HTTPS（443），或在 `.env` 中将 `OLLAMA_BASE_URL` / `TRADINGAGENTS_LLM_BACKEND_URL` 指向局域网可达地址。

---

## 8. MCP 服务（OpenClaw 集成）

TradingAgents 内置 MCP 服务器，通过 **Streamable HTTP** 暴露分析工具，供 OpenClaw 等 MCP 客户端调用（无需 TUI、无需 `docker exec`）。

### 8.1 架构

```
OpenClaw 容器 ──HTTP──► tradingagents-mcp:8080/mcp
                              │
                              ▼
                     TradingAgentsGraph.propagate()
                              │
                              ▼
                     ./data/logs/reports/…
```

两个容器需共享同一 `data` 卷（或 OpenClaw 能访问 MCP 返回的报告路径）。

### 8.2 启动 MCP 服务（NAS）

`.env` 中配置好 LLM 与非交互参数（见 4.3 节）后：

```bash
cd /volume1/docker/trading-agents
docker compose -f docker-compose.nas.yml up -d tradingagents-mcp
docker compose -f docker-compose.nas.yml logs -f tradingagents-mcp
```

验证端点（NAS 上或局域网内）：

```bash
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/mcp
# 405 或 406 均表示服务在监听（MCP 需 POST，GET 会返回非 200）
```

### 8.3 暴露的工具

| 工具 | 说明 |
|------|------|
| `get_server_info` | 服务版本、LLM 配置、目录路径、工具清单 |
| `validate_ticker` | 分析前校验/规范化 ticker 与资产类型 |
| `get_analysis_status` | 是否有分析任务正在运行 |
| `analyze_stock` | 完整多智能体分析（支持 analysts 子集） |
| `list_reports` | 按时间倒序列历史报告 |
| `list_report_files` | 列出某次报告目录下的 markdown 文件 |
| `get_report` | 按 section 读取报告内容 |
| `list_decision_history` | 读取决策记忆日志 |
| `get_checkpoint_status` | 查询 checkpoint 是否可恢复 |

### 8.4 配置 OpenClaw

在 OpenClaw 配置（`~/.openclaw/openclaw.json` 或容器内等价路径）添加 MCP 服务器：

```json
{
  "mcp": {
    "servers": {
      "tradingagents": {
        "url": "http://tradingagents-mcp:8080/mcp",
        "transport": "streamable-http",
        "timeout": 1200,
        "connectTimeout": 10
      }
    }
  }
}
```

**Docker 网络要点：**

- OpenClaw 与 TradingAgents **在同一 Docker Compose 网络** 或自定义 bridge 网络时，URL 用服务名 `http://tradingagents-mcp:8080/mcp`。
- 若 OpenClaw 在宿主机、MCP 映射了端口，则用 `http://<NAS局域网IP>:8080/mcp`。
- `timeout` 建议 ≥ 1200 秒（单次分析耗时长）。

配置后执行：

```bash
openclaw mcp reload
openclaw mcp probe tradingagents
openclaw mcp tools tradingagents
```

### 8.5 本地开发启动 MCP

```bash
cp .env.example .env
docker compose --profile mcp up -d tradingagents-mcp
# 或不用 Docker：
pip install ".[mcp]"
tradingagents-mcp
```

### 8.6 与 OpenClaw 对话示例

配置完成后，可对 OpenClaw 说：

> 用 TradingAgents 分析一下 NVDA，日期用 2026-07-05，中文总结结论。

OpenClaw 会自动调用 `analyze_stock`，再视需要用 `get_report` 读取完整报告。

---

## 9. 文件清单（NAS 最小部署包）

```
tradingagents/
├── docker-compose.nas.yml
├── .env                    # 从 .env.example 复制并编辑
├── .env.enterprise         # 可选，Azure 用户
├── data/
│   ├── cache/
│   ├── logs/
│   └── memory/
└── tradingagents-0.3.0-linux-arm64.tar   # 导入后可删除以节省空间
```

---

## 10. 本地开发（Mac Apple Container）

不导出、直接本地构建运行：

```bash
cp .env.example .env
container build -t tradingagents:0.3.0 .
container run --rm -it --env-file .env \
  -v "$(pwd)/data:/home/appuser/.tradingagents" \
  tradingagents:0.3.0
```

或使用项目自带的 `docker-compose.yml`（需安装 Docker Compose 或使用 `docker compose` 兼容工具）。
