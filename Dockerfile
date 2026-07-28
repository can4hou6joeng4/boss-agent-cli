# boss-agent-cli 容器镜像 —— 只用于运行 MCP server 与只读 / 本地命令。
#
# 刻意**不**包含浏览器内核：登录（扫码 / 浏览器 Cookie 提取）依赖真实浏览器，
# 硬塞进容器只会得到一个「看起来能跑、实际登不上」的镜像。正确用法是先在宿主机
# 完成 `boss login`，再把 ~/.boss-agent 挂载进来。详见 docs/integrations/docker.md。
#
# 数据目录：容器内 HOME=/data，因此 CLI 默认的 `~/.boss-agent` 会解析为
# /data/.boss-agent —— 无论走 boss-mcp 还是 `--entrypoint boss` 都一致。

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
	UV_LINK_MODE=copy \
	UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# 依赖层：只在 pyproject.toml / uv.lock 变化时失效。
# --frozen 强制严格按 uv.lock 安装，锁不上直接构建失败，杜绝「镜像里依赖漂了」。
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project --extra mcp

# 项目层：--no-editable 让 .venv 自包含，运行阶段不必再带上 src/。
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable --extra mcp


FROM python:${PYTHON_VERSION}-slim AS runtime

LABEL org.opencontainers.image.title="boss-agent-cli" \
	org.opencontainers.image.description="MCP server and read-only CLI surface for boss-agent-cli" \
	org.opencontainers.image.source="https://github.com/can4hou6joeng4/boss-agent-cli" \
	org.opencontainers.image.licenses="MIT"

ENV PATH="/app/.venv/bin:$PATH" \
	PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	HOME=/data

# 非 root 运行。UID 固定 1000；宿主机 UID 不同时（macOS 通常是 501）用
# `--user "$(id -u):$(id -g)"` 覆盖，避免挂载目录里出现属主错乱的文件。
#
# /data 必须对 other 保留 r-x：useradd --create-home 默认给 0700，
# 那样一旦 --user 覆盖成非 1000 的 UID，连遍历 /data 都会被拒，
# 挂载 /data/.boss-agent 直接 PermissionError——而挂载凭据是本镜像的主用法。
# /data/.boss-agent 预建一份，让未挂载的默认 UID 也能开箱可用。
RUN useradd --uid 1000 --home-dir /data --create-home --shell /usr/sbin/nologin boss \
	&& mkdir -p /data/.boss-agent \
	&& chown -R boss:boss /data \
	&& chmod 0755 /data /data/.boss-agent

COPY --from=builder --chown=boss:boss /app/.venv /app/.venv

USER boss
WORKDIR /data

# stdio 是 MCP 宿主的默认接法；HTTP / SSE 传输见 docs/integrations/docker.md。
ENTRYPOINT ["boss-mcp"]
CMD ["--transport", "stdio"]
