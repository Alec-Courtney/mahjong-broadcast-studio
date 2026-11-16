# 赛后连播音效

本目录提供五个转场音效，均为 48 kHz、双声道 PCM16 WAV。绘线声持续 2.667 秒。`sources.json` 记录音效规格及 SHA-256。标题、绘线和布局转换音效已统一为本地播出使用的版本，源码和安装版均直接使用本目录，不再隐式读取本机参考目录。

| 音效 | 默认触发秒 | 相对增益 |
| --- | ---: | ---: |
| 标题和坐标轴 `title_axes_reveal` | 0.967 | −3 dB |
| 绘线 `graph_reveal` | 2.000 | 0 dB |
| 选手卡入场 `photo_cards_reveal` | 4.817 | −4 dB |
| 布局转换 `layout_sweep` | 21.087 | 0 dB |
| 统计扫入 `data_sweep` | 23.187 | −2 dB |

每个音效播放一次。调整折线图停留时间会同步移动后两个音效的触发点。导播可以分别调整背景音乐和音效的音量。

背景音乐默认留空。选择本地音乐后，播放器在开场和片尾循环播放该曲目。支持 WAV、MP3、FLAC、OGG、M4A 和 AAC；Windows 安装版内置解码组件，无需另装 ffmpeg。离线混音也可单独输出音效。[本地音乐设置](../../docs/POSTMATCH_PLAYBACK.md#声音) · [离线混音](../../docs/TEAM_MATCH_ANIMATION_DEMO.md)
