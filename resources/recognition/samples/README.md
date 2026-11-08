# 本地识别回放样例

`constructed-tenpai.png` 与 `constructed-tenpai-replay.avi` 用实拍牌面裁片排列而成，用于本机验证视频输入、框选、稳定帧、听牌计算和草稿接口。

安装可选识别组件并准备 Medium v2 模型后，在项目根目录运行以下命令准备样例。[模型准备](../THIRD_PARTY.md)

```powershell
.\.venv\Scripts\python.exe scripts/download_recognition_models.py --samples
.\.venv\Scripts\python.exe tests/build_constructed_recognition_fixture.py
```

实拍参考图放在 `reference/local-only/resources/recognition/`。生成的构造图片和回放位于该目录下的 `samples/`。重新打开牌面识别设置页后，可从「离线样例」选择本机已有的样例。也可直接选择自己的图片或视频。

仓库保留原图来源、SHA-256、裁片位置、期望牌张及历史检测记录。[实拍图来源](../sample-manifest.json) · [构造样例记录](constructed-tenpai-source.json)
