# 立直声明音效

`riichi_declare.wav` 是独立合成的立直扫掠声，格式为 48 kHz、双声道 PCM16 WAV。文件总长 2.5 秒，有声音的区间约为 0.46–1.36 秒，随声明动画起点同步播放。规格和 SHA-256 见 [素材清单](sources.json)。

每次立直进入播放队列后只触发一次音效。立直音效使用独立的单次播放通道，背景音乐可同时继续播放。

`QT_QPA_PLATFORM=offscreen` 或 `minimal` 的验收环境默认静音，仍记录声明序号并去重。测试可显式指定 `enabled`。离线工具可使用 `prepare_riichi_sound()` 生成指定音量的 PCM 文件。
