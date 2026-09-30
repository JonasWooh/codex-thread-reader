# Codex Thread Reader

[English](README.md) · **简体中文**

这是一个在本机运行、只读的 Codex 插件，用于查找和读取旧对话中的可见内容。向 Codex 提供 `codex://threads/<uuid>` 链接、线程 ID 或搜索词，插件就会读取本机相应的会话记录，并把结果返回给 agent。

## 提供的工具

| 工具 | 作用 |
| --- | --- |
| `read_thread` | 根据深链接或 UUID 读取线程；在字符预算内优先返回最新内容，并支持向前翻页。 |
| `list_threads` | 列出最近的本地线程，默认包含已归档线程。 |
| `search_threads` | 在最近 200 个本地线程的可见用户和助手文本中搜索。 |
| `thread_reader_status` | 查看 Codex 数据目录、会话文件数量、Python 版本和 Zstandard 解码支持情况。 |

插件包含 MCP 服务和一个 Skill，用于指导 Codex 在你询问本地历史对话时调用这些工具。

## 环境要求

- 支持 `codex plugin` 命令、且本机保存有会话历史的 Codex。
- 能通过 `python` 命令启动 Python。读取普通 `.jsonl` 文件只需要标准库。
- 如果历史文件是压缩的 `.jsonl.zst`，还需要 Python 的 `compression.zstd` 模块、`zstandard` 包或 `zstd` 命令行程序三者之一。

服务优先使用 `CODEX_HOME` 环境变量；未设置时使用 `~/.codex`。读取范围为其中的 `sessions/` 和 `archived_sessions/`。

## 安装

先把本仓库注册为 Git marketplace，再安装插件：

```sh
codex plugin marketplace add https://github.com/JonasWooh/codex-thread-reader.git
codex plugin add codex-thread-reader@codex-thread-reader
```

运行 `codex plugin list` 检查安装状态。如果已打开的对话中还看不到新工具，请新建一个 Codex 对话。

## 使用

直接用自然语言提出请求，例如：

```text
读取这段旧对话：codex://threads/01234567-89ab-7cde-8f01-23456789abcd
列出我最近的 5 个本地 Codex 线程。
搜索本地 Codex 线程中的“browser extension”。
```

对于较长对话，`read_thread` 会在 `max_chars` 限制内返回较新的部分。如果结果含有 `truncated: true`，将 `next_older_end_item` 作为下一次调用的 `end_item`，即可读取更早的部分。若会话文件中没有可见消息，插件仍可能定位到该线程，但返回空 transcript。

## 隐私与范围

- 服务只读取本机会话文件，不修改 Codex 历史或 SQLite 状态，也不发起网络请求；上面的 Git 地址仅供 Codex 安装插件使用。
- 隐藏推理记录始终被排除；默认也不返回工具调用记录和 system/developer 消息。
- `include_tools=true` 可能暴露工具参数及输出；`include_instructions=true` 可能暴露该本地会话中的 system/developer 消息。仅在确有需要时启用。
- 历史对话可能包含个人或保密信息。请只让 agent 读取你愿意在当前对话中使用的线程。
- 插件只能读取运行 Codex 的这台机器上已有的会话文件，不能检索仅保存在其他设备或云端账户中的对话。

## 开发与测试

用 Python 运行自带测试：

```sh
python -m unittest discover -s tests -v
```

仓库结构：

```text
.agents/plugins/marketplace.json  Codex marketplace 清单
plugin.json                       插件元数据
mcp.json                          本地 MCP 服务配置
server/                           会话读取逻辑和 MCP 服务
skills/read-codex-thread/         Codex 使用指导
tests/                            读取逻辑及协议测试
```

本项目采用 [MIT 许可证](LICENSE)。
