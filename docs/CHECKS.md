# 检查与开发工具

日常运行 `CHECK-Project.cmd`，或：

```powershell
.\.venv\Scripts\python.exe scripts\check_project.py
```

日常检查覆盖听胡牌、自动化草稿、新帧与停帧保护、四位置识别、手牌修正、导播操作、数字步进和关键赛事隔离。广泛修改赛事、存储或公共控件后使用 `--full`；计分引擎另外运行 `node --test tests/original-engine.test.cjs`。

## 当前保留工具

- `tests/render_inline_automation_review.py --theme light`：当前原生导播台的浅色验收；省略参数为深色。输出到 `previews/checks/operator-console/`。
- `tests/render_ml_programme_review.py` 渲染团队赛完整流程。[动画生成、混音与核验](TEAM_MATCH_ANIMATION_DEMO.md)
- `tests/verify_recognition.py`：真实来源图片与构造回放的离线识别复核。
- `tests/verify_four_seat_recognition.py`：四个固定位置的构造回放联调。
- `tests/verify_automation_pipeline.py`：识别与计算结果经本机 HTTP 草稿接口进入导播台。
- `scripts/verify_spout_transport.py`、`scripts/verify_spout_integration.py`、`scripts/verify_obs_spout.py`：按需验证原生 Spout、实际 ONNX 和隔离 OBS 切镜；硬件与额外依赖以脚本说明为准。

按需验证输出统一写到 `previews/checks/`，不会每次启动程序都生成截图或录像。

## 测试资料与公共函数

离线识别检查使用 `reference/local-only/resources/recognition/` 内的本地素材。设置页也从该目录读取可用的样例。仓库保留识别原图的来源记录、模型清单、人工标注和原始预测。[本地样例准备](../resources/recognition/samples/README.md) · [准确率复核资料](../tests/fixtures/recognition/accuracy/README.md)

公共界面检查函数已归到 `tests/ui_test_support.py`；播出夹具、音视频检查及离线播放器工具分别位于 `broadcast_test_support.py`、`programme_audio_tools.py` 和 `programme_viewer_tools.py`，不再依赖整套旧版渲染脚本。

开发前从 [架构与数据边界](ARCHITECTURE_UX.md) 定位模块。回归测试优先使用临时赛事和内置样例。窗口相关检查使用 Qt 离屏模式，OBS 捕获与硬件摄像头使用各自的现场检查流程。

现场验收应记录摄像头、分辨率、ROI、照明、模型与运行时长。图片、构造回放、原生收发和实际 OBS 切镜结果分别记录，不混作硬件摄像头准确率。
