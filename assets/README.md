# 项目素材库

本项目选择导入的文件统一保存到固定相对目录 `assets/imported/`。

- `images/`：选手照片与保留的原图。
- `logos/`：队徽和赛事 Logo。
- `backgrounds/`：播出背景图片。
- `music/`：本地背景音乐。
- `documents/`：导入赛事的数据原档。

配置保存相对于项目的路径；相同内容按校验值去重。更换外部源文件位置不影响已导入的副本。搬迁项目时应保留此文件夹及 `data/`。

`data/`、`assets/imported/` 和本机自备的赛事标题卡与 Logo 由 `.gitignore` 排除。用于发行和测试的演示源数据、公开来源素材、字体与牌面存放在 `resources/`，随项目提交。
