# 架构与数据边界

程序通过 PySide6 提供 Windows 原生导播台和透明播出窗口。赛事文件、正式计分、画面播放与识别草稿各有独立入口。

## 模块职责

| 入口 | 职责 |
| --- | --- |
| [run.py](../run.py) | 启动独立导播或赛事模式，定位工作区和当前赛事 |
| [导播台](../src/console.py) · [赛事管理](../src/event_workspace.py) | 本桌录入、设置草稿、赛程、名册、榜单和备份 |
| [Model](../src/model.py) · [计分引擎](../resources/engine.js) | 对局状态、撤销、支付和终局计算 |
| [赛事库](../src/event_store.py) · [会话仓储](../src/session_repository.py) | 赛事索引、兼容读取、素材校验和原子保存 |
| [捕获窗口](../src/overlays.py) · [播出绘制](../src/broadcast_artwork.py) | OBS 窗口、布局、榜单和动画输出 |
| [识别可用性](../src/recognition_runtime.py) · [草稿接口](../src/automation_api.py) | 可选视觉运行库、识别草稿和本机 HTTP 接口 |

## 保存与切换

- 会话使用短文件锁和修订校验。保存成功后才发布新的对局与显示状态。
- 赛事归档和已确认的其他桌成绩按稳定 ID 关联。本桌撤销可与其他桌的补录共存。
- 设置草稿按实际修改的字段保存。切换赛事前处理未保存修改，同一字段发生冲突时可重新读取。
- 画面预览在设置页内绘制。播放、上屏和结算分别由明确的操作触发。
- 识别组件按需加载。识别结果进入候选草稿，导播确认后才写入对局。

Qt 的 `QDockWidget` 负责面板拖放与浮动，应用分别保存导播面板和播出窗口的位置。字体和字号也分别随系统偏好与赛事配置保存。 [Qt 停靠窗口说明](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QDockWidget.html)

数据路径、素材迁移和备份见[赛事数据保存](EVENT_DATA_STORAGE.md)。运行检查见[开发与测试](CHECKS.md)。
