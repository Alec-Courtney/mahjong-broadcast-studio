# 第三方来源与许可

项目自有代码、文档、演示数据和素材采用 MIT 许可。演示中收录的 M.League 肖像和其他第三方素材各有独立的权利与使用条件。[项目 MIT 许可](../LICENSE)

本文的相对链接按源码目录组织。Windows 程序包根目录的 `licenses/` 提供运行库许可，`demo-suite/` 提供肖像说明和逐张来源。随包的字体、牌面、点棒和计分库许可在 `_internal/resources/`。

## 计分代码

项目早期的向听数花色分解逻辑改编自 Satoshi Kobayashi 的
`kobalab/majiang-core/lib/xiangting.js`。役符实现参考该项目的 hule 算法和规则接口，并采用本项目的结构化拆分、解释与状态适配。[majiang-core 上游源码](https://github.com/kobalab/majiang-core) · [上游 MIT 许可](https://github.com/kobalab/majiang-core/blob/master/LICENSE)

下方为 `majiang-core` 的完整许可。手牌计算另外使用 `mahjong` 2.0.0，随附 MahjongRepository 贡献者的 MIT 许可。[mahjong 2.0.0](https://pypi.org/project/mahjong/2.0.0/) · [mahjong 许可](../resources/LICENSE-MahjongRepository.txt)

MIT License

Copyright (c) 2021 Satoshi Kobayashi

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## 字体、牌面和点棒

- **Noto Sans CJK JP 2.004** · © 2014–2021 Adobe. Noto 是 Google Inc. 的商标。字体按 SIL Open Font License 1.1 随程序分发。[上游版本](https://github.com/notofonts/noto-cjk/releases/tag/Sans2.004) · [字体许可](../resources/OFL-Noto.txt)
- **FluffyStuff riichi-mahjong-tiles** · 牌面来自 CC0 1.0 素材，项目使用适配后的 SVG。[上游牌面许可](https://github.com/FluffyStuff/riichi-mahjong-tiles/blob/26e127ba2117f45cdce5ea0225748cc0cfad3169/LICENSE.md) · [牌面许可记录](../resources/tiles/LICENSE-FluffyStuff.txt) · [CC0 1.0 完整法律文本](https://creativecommons.org/publicdomain/zero/1.0/legalcode.en)
- **hemisemidemipresent riichinator** · 点棒取自上游公开示例图，按 MIT 许可随程序分发。[上游素材](https://github.com/hemisemidemipresent/riichinator/tree/60768cc9aeb75a093e11af6c0df1b7182b8f2549) · [点棒许可](../resources/sticks/LICENSE-riichinator.txt)

素材来源清单记录了固定的上游提交、文件和项目改动。头像区域可用的四张演示队徽为项目制作的虚构素材。[素材来源清单](../resources/ASSET_SOURCES.json) · [演示队徽制作记录](../resources/demo_team_logos/README.md)

## M.League 名册和肖像

三份演示赛事使用 M.League 官网公开的选手名、队伍名和肖像。可导入的演示 JSON 内也带有照片。每张照片的官网页面及原图链接均记录在名册中。演示赛事由本项目独立制作，半庄成绩、赛事进度、排名和选手统计均为模拟数据。[演示肖像说明](../demo-suite/PHOTO_NOTICE.md) · [逐张来源名册](../resources/demo/roster.json)

照片及其他相关权利归各自权利方。公开展示和逐张署名可核对素材来源。随软件或演示文件复制、分发官网原照的许可需另外核实，仓库目前没有收录相关授权记录。[M.League 官网队伍页](https://m-league.jp/teams/) · [M.League 粉丝创作指引](https://m-league.jp/news202406261200/)

## 音效和用户导入媒体

随程序提供的立直、赛后连播音效为项目合成 WAV，各音效的制作记录和校验值均随仓库保留。用户可在赛事设置中选择有权使用的音乐、照片和其他媒体。[立直合成音效](../resources/riichi_audio/README.md) · [赛后合成音效](../resources/postmatch_audio/README.md)

## 可选集成

- **OBS Studio** · 相机切换 Lua 脚本使用 OBS 自带的脚本接口，OBS 由用户另行安装。[OBS 脚本指南](https://obsproject.com/kb/scripting-guide)
- **Spout 输出** · Python SpoutGL 0.1.1 按 BSD 3-Clause 许可发布。OBS 接收端为独立安装的 Off World Live OBS Spout2 插件，版本、下载地址和 SHA256 可在清单中核对。[SpoutGL 源码](https://github.com/jlai/Python-SpoutGL) · [SpoutGL 许可](../resources/LICENSE-SpoutGL.txt) · [插件源码与许可](https://github.com/Off-World-Live/obs-spout2-plugin) · [插件版本清单](../resources/obs-spout/installers.json)
- **牌面识别演示** · 模型来自 `nikmomo/Mahjong-YOLO`。上游代码许可和 ONNX 内的 Ultralytics 授权标记分别记录，模型和测试素材通过单独的脚本准备。[识别模型、许可和样本来源](../resources/recognition/THIRD_PARTY.md)
- **FFmpeg** · 导入部分音乐格式时可调用本机另行安装的 `ffmpeg`。许可随 FFmpeg 发行版本和构建选项而定。[FFmpeg 许可](https://ffmpeg.org/doxygen/trunk/md_LICENSE.html)

## Windows 程序运行库

Windows 基础程序包随附 CPython、Qt、PySide6、Shiboken 和其他运行依赖。许可原文、对应源码和库的替换方式见[Windows 运行库说明](../resources/licenses/WINDOWS_RUNTIME_NOTICES.md)。
