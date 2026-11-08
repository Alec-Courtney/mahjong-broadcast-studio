# OBS 四机位持续识别配置

同一台 Windows 电脑上，OBS 负责四个固定桌位的摄像头采集。每个来源通过独立的 Spout 滤镜发送画面，牌面识别使用对应发送名接收。直播切镜由 OBS 控制。 [切镜脚本教程](../README.md#obs-四机位切换脚本)

## 首次连接

1. 从源码运行 `INSTALL-Automation.cmd`，安装识别、ONNX 模型和 Spout 接收组件。脚本会为当前 Windows 用户启用新帧计数，并保留原值供卸载时恢复。[模型与许可](../resources/recognition/THIRD_PARTY.md)
2. 关闭 OBS，从官方发布页安装匹配版本的 Spout 插件。OBS 32 可用 Spout 1.13.0，Spout 1.15.0 要求 OBS 33 或更高版本。也可运行 `scripts/download_obs_plugin.py` 把校验后的安装包保存到本机 `reference/local-only/resources/obs-spout/`，再手动安装。[Spout 1.13.0](https://github.com/Off-World-Live/obs-spout2-plugin/releases/tag/1.13.0) · [Spout 1.15.0](https://github.com/Off-World-Live/obs-spout2-plugin/releases/tag/1.15.0) · [校验记录](../resources/obs-spout/installers.json)
3. 重启 OBS。在四个原始摄像头来源各加一个 **Spout Filter**，分别使用 `Mahjong_Position_1` 到 `Mahjong_Position_4` 等互不相同的发送名。在「工具 → Spout Output Settings」开启 **Continuous filter broadcast**，并保持四个滤镜启用。
4. 在导播台「赛事管理 → 赛事设置 → 画面与设备 → 牌面识别」逐一选择固定位置，来源类型选 **OBS Spout**，填入对应发送名。连接预览，框选暗手牌区域，启用并保存。
5. 逐路检查预览和识别结果，再切换 OBS 的 Program 机位。四个桌位均应继续收到新画面。换庄和调整当前选手时沿用桌位绑定。

## 连接与断流排查

- **看不到发送名**。确认原摄像头来源有画面、Spout 滤镜已启用，并核对发送名。
- **切镜后有一路停止刷新**。检查 **Continuous filter broadcast** 和该路滤镜。持续模式让当前未上镜的来源保持发送。 [Spout 1.13 连续输出实现](https://github.com/Off-World-Live/obs-spout2-plugin/blob/1.13.0/source/win-spout-filter.cpp)
- **连接后黑屏**。在 Windows 图形设置中让 OBS 和项目的 `.venv/Scripts/python.exe` 使用同一显卡，OBS 使用 Direct3D 11。 [插件运行条件](https://github.com/Off-World-Live/obs-spout2-plugin/blob/1.15.0/README.md)
- **识别建议过期**。检查摄像头本身是否仍产生新帧，再看 ROI、光照与牌面角度。OBS 重发静止画面时，传输有更新也可能没有新的现场画面。

识别建议由导播核对后提交。模型、离线构造回放和接口使用见[识别与自动化](AUTOMATION_API.md)。硬件验收需用实际摄像头与布光分别验证四路画面和换镜。
