# Mahjong Broadcast Studio

**简体中文** · [English](README.en.md) · [日本語](README.ja.md)

Windows 上的日麻赛事导播与 OBS 字幕工具，使用 Python 和 PySide6。导播台负责本桌录入和上屏，赛事管理负责名册、赛程、积分和报表。

## 快速开始

### 下载 Windows 便携版

**[下载 Windows x64 ZIP](https://github.com/Alec-Courtney/mahjong-broadcast-studio/releases/download/v0.1.2-preview/Mahjong-Broadcast-Studio-Windows-x64.zip)** · [预览版说明](https://github.com/Alec-Courtney/mahjong-broadcast-studio/releases/tag/v0.1.2-preview) · [本版更新](docs/releases/v0.1.2-preview.md)

1. 把整个 ZIP 解压到可写的个人文件夹，例如 `文档\MahjongStudio`。
2. 双击 `MahjongOverlayStudio.exe`。保留同目录的 `_internal` 文件夹。包内附有 `START-HERE.txt`。

程序首次运行时在 EXE 同级创建 `data/`，保存赛事、进度和全局设置。导入的素材按需存入同级的 `assets/imported/`。便携版自带手动计分、赛事管理和 OBS 画面输出所需的运行库。牌面识别 Demo 可从源码按需安装。

### 从源码启动

1. 在 GitHub 点击 **Code → Download ZIP**，把整个压缩包解压到个人目录。文件夹名称可以简短一些，例如 `文档\MahjongStudio`。
2. 安装 64 位 Python 3.10–3.15。初次使用可选本项目验证过的 Python 3.13。[Python 官方下载](https://www.python.org/downloads/windows/)
3. 在解压后的目录双击 [`START-Windows.cmd`](START-Windows.cmd)。首次运行联网下载基础组件，之后可直接双击启动。手动计分、赛事管理和 OBS 画面使用基础组件。

只使用独立导播台时，可从源码目录双击 [`START-UI-Only.cmd`](START-UI-Only.cmd)。

### 先试一场演示

1. 点击 **赛事管理 → 赛事库**，选中 **【演示】M.League 团队积分赛**，点击 **打开选中赛事**。
2. 在 **排名与结果** 查看个人榜、团队榜及历史成绩。回到导播台，可点击 **点数走势** 查看播出画面。
3. 要试接 OBS，点击 **显示播出窗口**，按钮亮色描边表示开启。在 OBS 添加 **窗口捕获**，选择 **麻将UI · 四家选手**。[OBS 捕获步骤](docs/OBS_WINDOW_CAPTURE.md)

## 导入演示赛事

在「赛事管理 → 赛事库」点击「导入完整赛事 JSON」，选择下面任一文件。每份 JSON 已内嵌选手肖像，导入后会在赛事库新增一场独立赛事。[演示说明](demo-suite/README.md)

- 团队积分赛包含 10 队 40 人和 50 个已归档半庄，可查看个人与团队积分、逐局记录和技术统计。[团队赛 JSON](demo-suite/team-league-demo.json)
- 个人积分赛包含 16 人和 20 个已归档半庄，可查看个人榜单、历场结果和技术统计。[个人赛 JSON](demo-suite/individual-league-demo.json)
- 个人淘汰赛包含 16 人、3 轮和 28 个已归档半庄，可查看对阵、各轮晋级和最终结果。[淘汰赛 JSON](demo-suite/individual-knockout-demo.json)

三套为独立演示，使用 M.League 官网的队伍、选手名和肖像。比分、逐局记录和技术统计来自计分引擎的模拟对局。[演示照片说明](demo-suite/PHOTO_NOTICE.md)

## 手动导播

1. 在「赛事管理」创建个人积分赛、团队积分赛或个人淘汰赛，设置规则和名册。也可以先打开内置演示赛事。
2. 在导播台选择席位，录入持点、宝牌、副露和听牌。和牌或流局时核对支付并提交结算。
3. 从「赛事设置」调整选手卡、晋级条件、术语字幕、开场和赛后节目。在设置页可预览字幕、晋级画面、标题卡和 Logo，也可手动播放立直预览。选用本地音乐后可试听并用于开场和片尾。OBS 可单独捕获透明选手卡、侧栏和全屏节目等窗口。[OBS 窗口捕获](docs/OBS_WINDOW_CAPTURE.md) · [赛后连播与声音](docs/POSTMATCH_PLAYBACK.md)
4. 半庄结束后在「半庄管理」确认成绩、选择下一场四席。在「排名与结果」查看榜单或导出 PNG 和 Excel。[导播台操作](docs/LIVE_CONSOLE.md) · [赛事管理](docs/EVENT_WORKSPACE_GUIDE.md)

系统设置随应用保存，赛事设置和进度分别随各赛事保存。全局设置和赛事设置可各自导入导出，完整赛事备份可携带名册、成绩及所用的图片和音频。默认赛事库位于 `data/`。[设置入口](docs/SETTINGS_NAVIGATION.md)

## OBS 四机位切换脚本

用 OBS 自带的 Lua 功能，在同一个对局场景内切换四路镜头。脚本只调整指定机位的显示状态，选手卡、计分板和字幕可继续叠放在镜头上方。[项目脚本](mahjong_camera_switch.lua)

1. **准备场景。** 在对局场景里添加或复用四个不同的视频来源，逐个调整到要播出的位置。沿牌桌的正常摸牌方向给四个固定桌位命名，例如 `机位A → 机位B → 机位C → 机位D → 机位A`。换庄时沿用这套绑定。演练也可以用四个不同颜色的色源。
2. **添加脚本。** 在 OBS 打开「工具 → 脚本」，点击 `+`，选择项目根目录的 `mahjong_camera_switch.lua`。[OBS 脚本管理说明](https://obsproject.com/kb/scripting-guide#scripts-management-window)
3. **填写来源。** 在「机位 1」到「机位 4」按下家顺序填入 OBS「来源」栏显示的名称。默认值为 `机位A` 到 `机位D`，用其他名称时填写实际名称。四路都要在当前播出场景中。镜头放在分组里时，父分组也要显示。
4. **设置起点并试切。** 点击「检查当前场景」。如果提示找不到来源，核对场景和名称。确认后点「显示机位 1（作为切换起点）」，再点下家、上家和对家的测试按钮。工作室模式下，脚本操作右侧 Program 画面里的场景，先在开播前完成测试。[OBS 工作室模式的场景处理](https://github.com/obsproject/obs-studio/blob/32.2.2/frontend/OBSStudioAPI.cpp#L51-L59)

关闭脚本窗口后试按方向键。脚本首次添加时会自动绑定以下按键。

| 按键 | 动作 | 当前显示 `机位A` 时 |
| --- | --- | --- |
| `→` | 切到下家 | `机位B` |
| `←` | 切到上家 | `机位D` |
| `↑` | 切到对家 | `机位C` |

切换前要恰好显示一个机位。四路全关或同时显示多路时，点击「显示机位 1」恢复起点。测试按钮能切换但方向键无效时，在 OBS「设置 → 快捷键」搜索 `日麻镜头`，检查绑定或换成组合键。脚本页的「绑定方向键」会把这三项恢复为默认方向键。

切镜脚本可独立使用。需要四路同时供牌面识别时，再配置每个来源的 Spout 持续发送。[四机位识别接入](docs/OBS_MULTI_CAMERA_DESIGN.md)

## 牌面识别 Demo

识别是可选的导播辅助功能，识别来源默认关闭。源码版和基础 Windows 构建均把识别权重与 OBS Spout 安装包交由用户按需下载。从源码试用时，使用 Python 3.11 或更高的兼容版本，运行 `INSTALL-Automation.cmd` 安装可选依赖。脚本会下载固定版本的 Nano 和 Small ONNX 模型、校验 SHA-256，并将文件存放到本机的 `reference/local-only/resources/recognition/`。[识别安装脚本](INSTALL-Automation.cmd) · [可选依赖](requirements-recognition.txt)

在「赛事管理 → 赛事设置 → 画面与设备 → 牌面识别」选择图片、视频、摄像头或 OBS Spout，再预览并框选手牌区域。使用 OBS Spout 时，在 OBS 端另行安装兼容版本的插件。可从官方发布页下载，也可运行项目的下载脚本，将固定版本的安装包存到本机的 `reference/local-only/resources/obs-spout/`。[识别操作](docs/AUTOMATION_API.md) · [OBS 四机位](docs/OBS_MULTI_CAMERA_DESIGN.md) · [Spout 官方发布](https://github.com/Off-World-Live/obs-spout2-plugin/releases) · [插件下载脚本](scripts/download_obs_plugin.py)

识别结果先进入草稿，由导播核对或修正后提交。遮挡、斜拍和背景中的其他牌都可能造成漏检或误判。可使用自己的图片和视频试用，开发用的实拍素材及构造回放留在本地参考目录。[本地构造回放说明](resources/recognition/samples/README.md)

可用 [`UNINSTALL-Automation.cmd`](UNINSTALL-Automation.cmd) 移除可选识别组件，继续手动导播。

### 模型来源

模型权重来自 `nikmomo/Mahjong-YOLO` 的固定提交 `28ffceed232ad95fd019c47a6c51ae7c78791a0e`。[上游仓库](https://github.com/nikmomo/Mahjong-YOLO) · [固定版本的 v2 权重](https://github.com/nikmomo/Mahjong-YOLO/tree/28ffceed232ad95fd019c47a6c51ae7c78791a0e/trained_models_v2)

- 安装脚本直接下载上游的 `mahjong-yolon-best.onnx` 和 `mahjong-yolos-best.onnx`。程序会优先使用所选尺寸的本机模型，缺少时使用其他已安装的模型。例如，Medium v2 尚未安装时可使用下载的 Small。推理通过 ONNX Runtime 在 CPU 上运行。[下载与校验清单](resources/recognition/manifest.json)
- 有需要时，可给模型下载脚本加 `--checkpoints` 下载 v2 的 `.pt` 权重，再在独立的转换环境生成 Nano、Small 和 Medium 的 ONNX 模型。[模型下载脚本](scripts/download_recognition_models.py) · [转换说明](resources/recognition/THIRD_PARTY.md) · [转换记录](resources/recognition/export-manifest.json)
- 上游仓库许可文件为 MIT。ONNX 模型元数据另标注 Ultralytics AGPL-3.0。[上游许可文件](https://raw.githubusercontent.com/nikmomo/Mahjong-YOLO/28ffceed232ad95fd019c47a6c51ae7c78791a0e/LICENSE) · [模型与素材说明](resources/recognition/THIRD_PARTY.md)

## 开发与参考

- [使用与开发文档索引](docs/README.md)
- [`CHECK-Project.cmd`](CHECK-Project.cmd) 运行日常检查。[检查与开发工具](docs/CHECKS.md)
- [`BUILD-Windows.cmd`](BUILD-Windows.cmd) 构建 Windows 程序。
- [演示赛事](demo-suite/README.md) · [赛后连播](docs/POSTMATCH_PLAYBACK.md)

## 许可

本项目自有代码、文档和演示素材采用 MIT 许可。M.League 肖像、牌面、字体和识别模型等第三方内容各有其使用条款。[MIT 许可](LICENSE) · [第三方来源与许可](docs/ORIGINAL_THIRD_PARTY_NOTICES.md)
