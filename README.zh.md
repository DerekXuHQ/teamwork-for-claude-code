# Teamwork for Claude Code

[English](README.md) · [中文](README.zh.md)

**在 claude.ai 上带一支 Claude 小队：一个 lead 统筹，几个线程分头干活，你提的每件事都有着落。**

一个会话当 **lead**，负责拆活、开**线程**（其他会话）。新需求来了，lead 交给原本就管这一块的线程，盯进度，必要时把活收回来或换人，最后把成果合到一起。每个线程都知道其他线程是谁、各管什么，不归自己管的活会转给对应的线程，干完按固定格式汇报。每个项目的看板、说明和记忆都放在 `.teamwork/` 目录里，会话再长、中途重启，也丢不了东西。

这是社区做的 skill，不是 Anthropic 官方产品。想用 [Claude Code Projects beta](https://code.claude.com/docs/en/claude-projects.md) 那套协作方式、但还没拿到资格，或者需要线程直接在自己电脑的文件夹里干活，都可以用它。

> **截图位（待补）：** lead 的状态回复，旁边是 `.teamwork/BOARD.md`，线程按「等你处理 / 待审 / 进行中」分组。

## 30 秒上手

先准备好：线程要用哪台电脑上的文件夹，就在那台电脑上开着 Claude Code 的 Remote Control，登录的账号和 claude.ai 上用的是同一个。然后在这个 Remote Control 环境里开一个会话，让它跑下面三条命令。`<served folder>` 换成 Remote Control 服务的文件夹，`<project>` 换成里面的项目文件夹。

**1. 下载 skill**

```bash
git clone https://github.com/DerekXuHQ/teamwork-for-claude-code <served folder>/teamwork-for-claude-code
```

**2. 先预演一遍，看安装会改哪些地方（只预演，不写入任何文件）**

```bash
python3 <served folder>/teamwork-for-claude-code/scripts/install.py --link --project <served folder>/<project> --served <served folder>
```

**3. 正式安装**

```bash
python3 <served folder>/teamwork-for-claude-code/scripts/install.py --link --project <served folder>/<project> --served <served folder> --apply
```

装好后，在同一个环境里**新开**一个会话（skill 只在会话启动时加载），输入：

```
/teamwork new <project>
```

lead 会先问目标，再列出建议的线程和任务，等你点头才开工。

**先确认配置目录。** 安装脚本往 `$CLAUDE_CONFIG_DIR` 里写；没设这个变量，就写 `~/.claude`。`--help` 和每次预演都会打印它找到的是哪一个。如果你的 Remote Control 服务用的是别的目录（比如 `CLAUDE_CONFIG_DIR=~/.claude-work`），加上 `--config-dir ~/.claude-work`，不然会找不到 `/teamwork`。

## 安装脚本做了什么

它不删任何东西。新规则合并进已有设置，不覆盖原有内容；改设置文件之前先留一份 `.bak` 备份。

1. 把 skill 链到 `<config dir>/skills/teamwork`。加 `--link`（推荐）是软链接到你 clone 下来的目录，以后 `git pull` 就是升级；不加就是复制一份，之后不再变。已经存在的 `skills/teamwork` 不会被覆盖。
2. 在 `<config dir>/settings.json` 里加权限规则：一组 allow 规则，让线程汇报时不会卡在权限确认上；几个临时目录；还有一组 ask 规则，删仓库、改仓库可见性这类大操作会先问你（按命令文字匹配，只防误操作，不是安全边界）。不想要 allow 规则就加 `--no-allow`。
3. 给了 `--project` 和 `--served` 的话：在这两个文件夹里加 SessionStart hook，在那里启动的会话都能看到看板；同时把文件夹级的规则写进它们的 `.claude/settings.local.json`（项目的 worktree 也一样）。

其他选项：`--pre-push` 装一个 git pre-push hook，推送内容里出现 `.teamwork/PRIVATE.md` 列出的词或路径就拦下来。`--default-mode {auto,acceptEdits,default}` 设定这台电脑上所有新会话的默认模式。Teamwork 在 `auto` 下最顺手，但这会影响这台电脑上的每个会话，所以由你来定。全部选项见 `python3 scripts/install.py --help`。

## 升级与回滚

用 `--link` 装的话，检出分支上合进去的改动，之后新开的会话马上就会用上。想自己决定什么时候生效，就用发布快照：

```bash
python3 <served folder>/teamwork-for-claude-code/scripts/release.py status
python3 <served folder>/teamwork-for-claude-code/scripts/release.py publish --ref main
python3 <served folder>/teamwork-for-claude-code/scripts/release.py publish --ref main --apply
python3 <served folder>/teamwork-for-claude-code/scripts/release.py rollback --apply
python3 <served folder>/teamwork-for-claude-code/scripts/release.py edge --apply
```

`status` 看每个配置目录现在指向哪里。`publish` 给某个 ref 打快照，把所有已链接这个 skill 的配置目录切过去，不加 `--apply` 只是预演。`rollback` 退回上一个版本；只发布过一次时没有上一个版本，它会直接报错，什么也不改。`edge` 改回直接跑 clone 目录。旧快照一律保留。详见 [`references/setup.md`](references/setup.md)。

## 和 Projects beta 比一比

| | Claude Code Projects beta | Teamwork |
|---|---|---|
| 谁来统筹 | 项目对话 | 跑着 `/teamwork` 的 lead 会话 |
| 一个线程管什么 | 一个任务 | 一块固定的职责范围；后续需求还交给它，前面的来龙去脉都在 |
| 线程在哪儿跑 | 默认在云端，也可以经 Remote Control 跑在本地 | 每个线程自己选：Remote Control 跑在你的文件夹里（各用各的 git worktree），或者跑在云端 |
| 线程之间能不能对话 | 文档没写 | 每个线程都有花名册；不归自己管的活转给对应线程，同时抄送 lead |
| 开工前有没有计划 | 建议开哪些线程 | 一张任务表：谁负责、依赖什么、做到什么程度算完，你批准后才开工 |
| 记忆 | 云端线程读 `MEMORY.md` | `.teamwork/MEMORY.md` 发给每个线程，Remote Control 线程也有 |
| 断了怎么接上 | 靠最近的消息和记忆 | 靠磁盘上的台账，会话启动时和上下文压缩后自动重新注入 |
| 界面 | 侧边栏总览 | 一个看板文件加 lead 的状态回复，没有侧边栏 |
| 隔离 | 项目线程不出现在普通会话列表里 | 靠约定：文件夹、`[project] thread · focus` 格式的标题、标签 |
| 谁能用 | 拿到 beta 资格的人 | 有 Claude Code 和 Remote Control 就能用 |

逐项对比见 [`references/projects-parity.md`](references/projects-parity.md)（英文）。

## 工作原理

```
claude.ai（网页 / App）
   │  你可以直接和任何一个会话说话
   ▼
lead  ── Remote Control 会话，运行 /teamwork，管一个项目的 .teamwork/
   │  create_session / send_message / get_session / list_events / interrupt_session
   ├── rc 线程 "ui"        ── 项目文件夹里的 Remote Control 会话（有自己的 git worktree）
   ├── rc 线程 "api"       ── 项目文件夹里的 Remote Control 会话
   └── 云端线程 "research" ── 云端会话，通过对话记录和 git 汇报
```

- **你提的每件事都会一直跟进，直到你说可以结束。** lead 先把你的原话记下来，再派出去，检查交回来的东西，向你汇报，之后一直挂在清单上，直到你说「做完了」或「不要了」。
- **看板**：`.teamwork/BOARD.md`，每次有变动都从 `state.json` 重新生成。线程的分组和 Projects 总览一样（等你处理、待审、进行中、合并中、空闲、已完结），另外列出待回答的问题、花名册和计划。
- **消息**：每条消息第一行是固定格式的消息头，比如 `[tw:shop] TASK lead -> ui T3`。lead 发 TASK、FOLLOWUP、RECALL、ROSTER、ANSWER；线程发 ACK、DONE、BLOCKED、QUESTION、HANDOFF、MEMORY。
- **状态**：线程每一轮结束都附一个 `<tw-status>` 块，写清做了什么、结果、怎么验证的、做了哪些假设、哪些地方访问不到、在等谁、转给了谁、下一步。

**完整机制**写在 [`docs/framework-zh.md`](docs/framework-zh.md)（[English](docs/framework.md)）：角色、台账、消息协议、状态机、路由、同意边界、故障恢复，以及换一个宿主平台要具备什么才能跑起这套框架。建议先读开头的「5 分钟概览」。

哪种会话能给哪种会话发消息，是在真实账号上逐一试过的，见 [`references/transport.md`](references/transport.md)（英文）。简单说：Remote Control 会话之间可以互发；lead 能创建云端线程、给它们发消息；云端线程发不了消息给 Remote Control 上的 lead，所以 lead 去读它们的对话记录。

> **截图位（待补）：** 一个线程发来的 `DONE` 消息，以及 lead 转述给你的汇报。

## 命令

| | |
|---|---|
| `/teamwork new <name>` | 新建项目，定目标和说明 |
| `/teamwork use <name>` | 让当前会话接手一个已有项目，当它的 lead |
| `/teamwork projects` | 总览所有项目：各自的 lead，有什么在等你 |
| `/teamwork plan` | 列出建议的线程和任务，等你点头 |
| `/teamwork go` | 开始已批准的工作 |
| `/teamwork status` | 挨个查一遍线程，汇总汇报 |
| `/teamwork route <request>` | 把一个需求交给负责它的线程 |
| `/teamwork recall <thread>` | 叫停一个线程，把它手上的活收回来 |
| `/teamwork close` | 收尾，结束项目 |

也可以直接跟 lead 说话：「这个交给 UI 线程」「所有线程什么进度」「最多同时跑两个」。lead 还会在汇报前先核对成果，需要时把自己交接给新会话，推送前挡住不该公开的内容；完整做法见 [`SKILL.md`](SKILL.md)。

## 需要什么

- 带 Remote Control 的 Claude Code，登录的账号和 claude.ai 上的一致。
- 线程要用哪台电脑的文件夹，那台电脑上就得开着 Remote Control 服务。其余操作都在 claude.ai 上完成。
- 那台电脑上装有 Python 3 和 git。
- 云端线程要改代码的话：一个你的账号能访问的 GitHub 仓库。

## 限制

- 一个 Remote Control 服务默认最多挂 32 个会话，上面所有项目和 lead 共用，云端线程不算在内。除此之外线程数量不设上限，但套餐的用量限制照样算，每个线程都是一个完整的会话。
- 没有侧边栏，看板就是一个文件，再加上 lead 的状态回复。
- 隔离靠约定（文件夹、标题、标签），平台本身不强制。线程会出现在你平时的会话列表里。
- 云端线程的状态是 lead 去拉的，不是它们推过来的，所以状态只更新到 lead 上一次巡检的时候。

## 目录结构

```
SKILL.md                     lead 的操作手册（/teamwork 做什么）
templates/worker-protocol.md 通过 append_system_prompt 发给每个线程的协议
scripts/tw.py                台账：看板、花名册、计划、路由提示、hook 输出
scripts/install.py           安装脚本（默认只预演）
scripts/release.py           发布快照：发布、回滚、查看状态
docs/framework.md            把机制写成可复用框架的说明（中文：docs/framework-zh.md）
references/                  传输矩阵、安装设置、消息格式、与 Projects 的对比
```

## 许可证

MIT，见 [LICENSE](LICENSE)。
