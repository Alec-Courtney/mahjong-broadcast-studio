# 团队赛流程演示生成

从内置团队赛数据渲染开场、代表性对局、终局和赛后节目。演示成绩由计分引擎模拟，输出保存在项目的 `previews/`。渲染器使用赛事文字标题和内置示例 Logo。

## 本地生成

先初始化项目 Python 环境，并让 `ffmpeg` 与 `ffprobe` 能在命令行运行。由项目根目录执行：

```powershell
$demoDir = "previews/latest/programme"
.\.venv\Scripts\python.exe tests/render_ml_programme_review.py --output $demoDir
```

渲染器生成静音视频、章节、关键帧、计分核验数据和可导入的演示 JSON。混音工具按时间线加入立直与赛后音效。已有适用的本地曲目时，可附加 `--music "路径"`，在开场和片尾加入背景音乐。

```powershell
.\.venv\Scripts\python.exe tests/mix_ml_programme_audio.py `
  --input "$demoDir/.ml-programme-silent.mp4" `
  --output "$demoDir/ML_开场至结束_精简对局演示.mp4" `
  --timeline "$demoDir/verification.json" `
  --proof "$demoDir/analysis/programme-audio-proof.json"
```

## 核验与播放

```powershell
.\.venv\Scripts\python.exe tests/verify_ml_programme.py --folder $demoDir --require-rendered
.\.venv\Scripts\python.exe tests/build_ml_programme_viewer.py --output-dir $demoDir
```

播放器和成片位于同一个输出目录。离线核验同时检查点数、pt、场次归档、音频时点以及从单个演示 JSON 恢复素材。示例 Logo 和合成音效由项目提供。演示肖像及自选音乐按各自来源的使用条件处理。[素材来源与许可](ORIGINAL_THIRD_PARTY_NOTICES.md)
