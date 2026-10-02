# Paean · 口播视频配乐 Agent Skill

为本机口播视频批量添加 BGM，突出人声、保持原画面，测量响度与真峰值并核验成品。支持成片文件夹、Markdown 清单、自备曲库、指定曲目、音量调整和已有成片配乐识别。

这是 `paean` 的公开分发版，适用于支持本地 skills、文件工具和 Python 命令的 Codex 或具备等效能力的 agent。配乐处理在使用者本机完成；没有后台服务或外部发布接口。

## 可以直接发给用户的完整安装提示词

让用户把下面整段复制给自己的 agent：

```text
请帮我安装「Paean · 口播视频配乐」skill。

官方原仓库：
https://github.com/MisakaZhu/paean

需要安装的目录：
skills/paean

请按以下要求完成：

1. 只使用上述原仓库，不替换成 fork 或其他镜像。先只读检查仓库身份、默认分支、README、SKILL.md、references、scripts 和依赖，确认没有泄露凭证、异常二进制、隐藏联网或与该配乐流程无关的操作。发现实际风险时说明具体问题；检查通过后直接安装。
2. 优先使用当前环境自带的 skill-installer，安装指定目录并保留全部文件。Codex 安装到 CODEX_HOME 对应目录下的 skills；未设置 CODEX_HOME 时使用 ~/.codex/skills。其他 agent 请先确认其官方支持的本地 skills 机制，不猜安装位置或声称已经兼容。
3. 如果存在同名 skill，先读取并比较，不覆盖或删除。内容一致则报告已安装；不同则说明差异，给出保留原文件和私人配置的升级方案，等我决定后再更新。
4. 安装后重新读取目标 SKILL.md，检查 YAML、名称、全部相对引用、配置模板和脚本完整性。报告实际安装路径、仓库来源、所用提交 SHA，以及何时可被当前 agent 发现。
5. 检查 Python 3.10+、NumPy 和 ffmpeg/ffprobe 是否可用。缺少时列出实际缺项，不自动安装额外软件；文件安装成功不等于配乐环境已就绪。
6. 首次制作时，再按 config.example.json 帮我在工作目录建立 .paean-config.json。来源视频、自备且有权使用的曲库、工作区与可选本机导入目录由我提供。不要继承作者路径，不自动读取剪映缓存或草稿，不下载未授权音乐，不把私人配置、曲库、视频和日志提交到 GitHub。
7. 完成后给我一条可以直接复制的 $paean 调用示例。本次只安装和检查，不处理视频、不替换成片、不拷贝缓存或草稿、不发布内容。

在已授权安装范围内尽量自主完成，不对常规步骤反复询问；确实需要我处理时，说明已完成部分、具体缺项和下一步。
```

## 功能与输出

- 默认人声比背景音乐高 9 LU，整体 -16 LUFS，验证真峰值不超过 -1 dBTP。
- 人声高通与轻压缩，BGM 做频段让位、平缓音量、淡入淡出，曲目短时按条件循环。
- 视频流复制，音频输出 AAC 192k、48 kHz 立体声；核对时长、视频流 SHA-256、全片解码和源文件未变。
- 各次运行及各条输出独立，默认只存本机工作区；可显式指定像素蛋糕等软件的本机导入目录。
- 导入目标同名时保留已有文件；明确 `--replace` 时先备份，再替换并记录备份路径。
- 比对自己的曲库识别配乐；应用缓存和草稿仅在使用者显式指定目录后读取。

**本仓库不附带任何音乐文件。** 原个人版曲库及其平台许可不随代码分发，使用者应提供有权使用的音频。指标核验不等于版权核验或主观听音验收。

## 首次配置

把 [config.example.json](skills/paean/config.example.json) 复制到你的工作目录，命名 `.paean-config.json`。完整规则见 [configuration.md](skills/paean/references/configuration.md)。

- 提供自己的视频文件夹或清单，曲库放到配置的 `music_dir`。
- 将策略别名映射到自己的曲目名称；例如 `"舒缓": "我的舒缓曲"` 对应曲库中的 `我的舒缓曲.wav`。
- `destination_dir` 默认空，成品只留工作区。需要导入本机软件时再指定正确导入目录。
- `cache_dir`、`drafts_dir` 默认空，不会自动扫描应用缓存或草稿。

相对配置路径以配置文件所在目录为基准。工作区输出为 `output/运行ID/序号/标题.mp4`，导入文件为 `标题.mp4`。真实配置、音乐、视频、备份及日志全部留在本机。

## 使用示例

给 agent：

```text
使用 $paean，读取当前工作目录的 .paean-config.json。
先检查我提供的成片清单和自备曲库，再为第 1 至 5 条视频配乐。
人声保持清楚，曲目按内容自主选择，先只生成本机预览，不导入，也不覆盖已有文件。
完成后报告每条使用的曲目、验证结果和实际成片路径。
```

直接运行脚本（将路径换为自己的安装位置和来源）：

```bash
python skills/paean/scripts/render.py ./videos --list
python skills/paean/scripts/render.py ./list.md 1-5 --no-publish
python skills/paean/scripts/render.py ./list.md 2=舒缓 --smr 11 --no-publish
python skills/paean/scripts/render.py ./list.md 2=舒缓 --dst ./import --replace
python skills/paean/scripts/identify_music.py ./finished.mp4 --source ./source.mp4 --music ./music
```

文件夹识别 `序号-标题（无音乐）.mp4`；Markdown 清单包含序号、标题、话题和本机 MP4 链接，可有额外列。清单标题与实际视频文件名可以不同。日期或 `latest` 写法需配置 `source_root`。

`--smr 7` 让音乐更明显，`--smr 11` 更突出人声；`--target -14`/`-18` 调整体响度。`--no-publish` 是兼容参数名，仅表示不导入本机目标目录，脚本不支持抖音或视频号发布。

## 安装环境

需要 Python 3.10+、NumPy 1.26 或 2.x、已安装的 FFmpeg/ffprobe。Python 依赖见 [requirements.txt](skills/paean/requirements.txt)。先检查现有环境，安装或配置额外依赖按使用者实际授权处理。两个脚本没有自动安装代码或后台联网调用。

FFmpeg 不含 libsoxr 时，保留内置重采样兼容分支。配置可填写自己的媒体工具完整路径，不需要编辑脚本，更不需要作者机器上的 Python 或 FFmpeg 目录。

## 仓库与验证

```text
skills/paean/
  SKILL.md
  agents/openai.yaml
  config.example.json
  requirements.txt
  scripts/                  混音、识别与本机配置
  references/               配置、配方与识别边界
tests/                      隔离目录行为及合成媒体测试
tools/audit_package.py       分发文件与引用检查
```

安装时只需要 `skills/paean`。开发检查：

```bash
python tools/audit_package.py
python -m unittest discover -s tests -v
```

测试使用合成音视频，不使用真实私人作品或商业曲库。`.gitignore` 忽略常见本机配置、运行产物及媒体；发布前仍应检查实际文件。

本仓库采用 [MIT License](LICENSE)，适用于说明和代码。音视频、平台、软件、曲库及缓存的许可由各自权利人约定，不随本许可转授。
