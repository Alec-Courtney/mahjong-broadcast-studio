# 牌面识别模型与测试素材

模型来源：[nikmomo/Mahjong-YOLO](https://github.com/nikmomo/Mahjong-YOLO)，固定提交 `28ffceed232ad95fd019c47a6c51ae7c78791a0e`。

Windows 基础版只包含手动导播功能所需的文件。从源码运行 `INSTALL-Automation.cmd` 可安装可选识别组件，并校验下载上游 Small 和 Nano ONNX。权重保存在本机 `reference/local-only/resources/recognition/`。设置优先使用已选尺寸的 v2 模型，其次尝试本机可用的模型。全新安装选择 Medium 时会使用已下载的 Small。

- `manifest.json` 记录上游下载地址、字节数和 SHA256；下载脚本同时校验固定 SHA256。
- `export-manifest.json` 记录 v2 的 nano、small、medium 权重转换参数、工具版本、输出 SHA256 和模型元数据。
- 上游仓库的 MIT 许可原文保留在 `LICENSE-Mahjong-YOLO.txt`。ONNX 元数据另行标注 **Ultralytics AGPL-3.0**。[Ultralytics 授权说明](https://www.ultralytics.com/license)
- 应用通过 ONNX Runtime CPU 执行 ONNX；PT 权重留作来源归档，仅在独立转换工具中加载。转换工具会先核对固定 SHA256。
- 默认 medium v2 在本地 M.LEAGUE 正立手牌截图测试中表现更好，约 0.3 秒/帧。small、nano 可更换；旧版 ONNX 也保留供对照。类别名称从各模型元数据读取：旧导出为 37 类，v2 为 38 类并含 UNKNOWN，不能共用硬编码索引。

本机复核使用 ABEMA TIMES 公开报道中的 M.LEAGUE 截图。仓库保留来源网址、校验值和署名。原始图片与其衍生测试图、回放保存在本机的参考目录。[图片来源记录](sample-manifest.json) · [本机素材路径](samples/README.md)

离线验收输出在 `previews/checks/vision/`。

- 检测数据和标注图来自三张报道截图，覆盖多个模型和阈值。
- `mleague-hand-replay.avi`、`mleague-still-replay.avi` 用静态截图生成，画面标明 STILL-IMAGE REPLAY。
- `constructed-tenpai.png` 和对应回放使用裁切后重新排列的牌面，验证从识别到计算、草稿的完整流程。牌面为 `999m333p5s666777z`，待 `5s`。[构造样例记录](samples/constructed-tenpai-source.json)

遮挡、倒牌、斜拍、红五、其他选手或牌山进入区域时仍有漏检、误判。牌数正确和连续稳定也不能保证类别正确，自动结果必须保留人工核对步骤。

准备 v2 权重时先运行 `scripts/download_recognition_models.py --checkpoints`。在独立 Python 环境安装 `scripts/requirements-recognition-export.txt` 后，使用该环境运行 `scripts/export_recognition_models.py`，导出的 ONNX 会与本机权重放在同一目录。

复核时运行 `scripts/download_recognition_models.py --samples` 准备本机样本。包含构造听牌流程的复核需先准备 Medium v2，再运行 `tests/verify_recognition.py` 生成验收图、回放和报告。单独重建构造图时运行 `tests/build_constructed_recognition_fixture.py`。
