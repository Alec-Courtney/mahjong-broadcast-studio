# 第三方来源与许可

项目自有代码、文档和演示素材采用 MIT 许可。下列第三方内容按各自的许可或使用条款处理。[项目 MIT 许可](../LICENSE)

项目早期的向听数花色分解逻辑改编自 Satoshi Kobayashi 的
`kobalab/majiang-core/lib/xiangting.js`。役符实现参考该项目公开的 hule 算法和规则接口，
但采用了本项目自己的结构化拆分、解释与状态适配，不是原库的逐字或完整发行版。
上游地址：https://github.com/kobalab/majiang-core
核对日期：2026-10-20。未将上游在其他系统中的验证规模当成本项目测试结果。

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

## 牌面和点棒

FluffyStuff riichi-mahjong-tiles：CC0 1.0 / public domain。上游完整许可保存于resources/tiles/LICENSE-FluffyStuff.txt。

hemisemidemipresent riichinator：MIT，Copyright (c) 2025 hemidemisemipresent。点棒截取自其公开示例图；完整许可保存于resources/sticks/LICENSE-riichinator.txt。未使用autotable的牌面或点棒素材。

来源提交与文件标识见resources/ASSET_SOURCES.json。

## M.League 名册与素材

演示赛事使用 M.League 官网的选手名、队伍名和肖像。肖像的来源页面及原图地址逐张记录在名册中，权利与使用条件按原素材处理。演示成绩由项目计分引擎逐局模拟。[名册与肖像来源](../resources/demo/roster.json) · [M.League 粉丝创作指引](https://m-league.jp/news202406261200/)

开发参考截图、节目片段及其提取音效保留在本机 `reference/`。此前选用的 `M.LEAGUE HIGHLIGHT` 音乐试听也留在本地，程序可由用户选择自己的音乐。[原声带发行信息](https://linkco.re/Fhbs9gXc?lang=ja)

## 素材来源记录

- [牌面和点棒来源](../resources/ASSET_SOURCES.json) · [Noto 字体许可](../resources/OFL-Noto.txt)
- [赛后合成音效](../resources/postmatch_audio/README.md) · [立直合成音效](../resources/riichi_audio/README.md)
- [识别模型与样本](../resources/recognition/THIRD_PARTY.md) · [Spout 插件版本与来源](../resources/obs-spout/installers.json)
