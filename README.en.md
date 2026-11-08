# Mahjong Overlay Studio

[简体中文](README.md) · **English** · [日本語](README.ja.md)

A Windows desktop app for riichi mahjong broadcasts and OBS overlays, built with Python and PySide6. The broadcast desk handles live table input and cues. The event workspace manages rosters, schedules, standings, and exports.

## Quick start

Install a compatible version of Python 3.10 or newer on Windows, then run a launcher from the project folder.

| Launcher | Use |
| --- | --- |
| [`START-Windows.cmd`](START-Windows.cmd) | Open the broadcast desk and event library. You can try the built-in M.League roster demo (`【演示】M.League 团队积分赛`). |
| [`START-UI-Only.cmd`](START-UI-Only.cmd) | Open the broadcast desk with a separate local workspace. |

On first launch, the script creates a Python environment for the project and installs `PySide6-Essentials` and `mahjong`. Manual scoring, event management, OBS output windows, and reports work with these base dependencies. [Base requirements](requirements.txt)

## Import a demo event

In **Event Management → Event Library** (`赛事管理 → 赛事库`), click **Import Full Event JSON** (`导入完整赛事 JSON`) and choose one of the files below. Each JSON includes its player portraits and imports as a separate event. [Demo guide](demo-suite/README.md)

- The team league has 10 teams, 40 players, and 50 archived hanchan. It includes team and individual standings, hand records, and player statistics. [Team league JSON](demo-suite/team-league-demo.json)
- The individual league has 16 players and 20 archived hanchan, with player standings, match results, and player statistics. [Individual league JSON](demo-suite/individual-league-demo.json)
- The individual knockout has 16 players, 3 rounds, and 28 archived hanchan. Follow the bracket through to the champion. [Knockout JSON](demo-suite/individual-knockout-demo.json)

The demos use team names, player names, and portraits from the M.League website. The scoring engine simulates the matches and provides the scores, hand records, and statistics. [Roster and portrait sources](resources/demo/roster.json)

## Manual broadcast workflow

1. In Event Management, create an individual league, team league, or individual knockout event and set up the rules and roster. You can also start with the built-in demo event.
2. At the broadcast desk, select a seat and enter scores, dora, melds, and tenpai. Review payments before submitting a win or draw.
3. In Event Settings, configure player cards, qualification conditions, commentary captions, and opening and post-match segments. Preview captions, qualification layouts, title cards, and logos, or click to play a riichi animation. Choose a local music file to preview it and play it during the opening and ending. Capture transparent player cards, sidebars, and full-screen programme windows separately in OBS. [OBS window capture guide](docs/OBS_WINDOW_CAPTURE.md) · [Post-match playback and audio](docs/POSTMATCH_PLAYBACK.md)
4. After a hanchan, confirm its results and select the next lineup in Match Management. View standings or export PNG and Excel files from Rankings and Results. [Broadcast desk guide](docs/LIVE_CONSOLE.md) · [Event workspace guide](docs/EVENT_WORKSPACE_GUIDE.md)

System preferences apply across events. Each event keeps its own settings and match progress. Global and event settings have separate import and export options. A full event backup can include its roster, results, and referenced images and audio. The default event library lives in `data/`. [Settings guide](docs/SETTINGS_NAVIGATION.md)

## OBS four-camera switching script

Use OBS's built-in Lua support to switch between four camera sources in one match scene. The script toggles the selected camera sources. Player cards, scoreboards, and captions can sit above the cameras in the same scene. [Project script](mahjong_camera_switch.lua)

1. **Prepare the scene.** Add or reuse four distinct video sources and position each one for the broadcast. Name the fixed table positions in normal turn order, for example `机位A → 机位B → 机位C → 机位D → 机位A`. Keep that order when the dealer changes. Four different Color Sources also work for a practice run.
2. **Add the script.** In OBS, open **Tools → Scripts**, click `+`, and select `mahjong_camera_switch.lua` from the project folder. [OBS script management guide](https://obsproject.com/kb/scripting-guide#scripts-management-window)
3. **Enter the source names.** Fill the four camera fields (`机位 1` to `机位 4`) in draw order, using the names shown in OBS's Sources list. The defaults are `机位A` through `机位D`. Place all four sources in the current output scene and keep any parent groups visible.
4. **Set a starting camera and test.** Click **检查当前场景** (Check current scene). If a source is missing, check the scene and its source names. Click **显示机位 1（作为切换起点）** (Show camera 1), then try the next, previous, and opposite seat test buttons. In Studio Mode the script acts on the Program scene. Test it before going live. [OBS Studio Mode scene handling](https://github.com/obsproject/obs-studio/blob/32.2.2/frontend/OBSStudioAPI.cpp#L51-L59)

Close the Scripts dialog and try the keys. The script assigns these bindings when you first add it.

| Key | Action | From `机位A` |
| --- | --- | --- |
| `→` | Next seat | `机位B` |
| `←` | Previous seat | `机位D` |
| `↑` | Opposite seat | `机位C` |

Keep exactly one camera visible before switching. If zero or several are visible, click **显示机位 1** to reset. If the test buttons work and the arrow keys do not, search for `日麻镜头` under **Settings → Hotkeys** to check or change the bindings. Click **绑定方向键** in the Scripts dialog to restore the three arrow-key defaults.

Camera switching works on its own. To feed all four sources to tile recognition, configure continuous Spout output for each source. [Four-camera recognition setup](docs/OBS_MULTI_CAMERA_DESIGN.md)

## Tile recognition demo

Tile recognition is an optional operator aid. Capture sources start disabled. The source checkout and standard Windows build keep model weights and OBS Spout installers as separate downloads. To try recognition from source, use a compatible version of Python 3.11 or newer and run `INSTALL-Automation.cmd`. It installs the optional dependencies and downloads pinned Nano and Small ONNX models to `reference/local-only/resources/recognition/`, verifying their SHA-256 checksums. [Recognition installer](INSTALL-Automation.cmd) · [Optional requirements](requirements-recognition.txt)

Open **Event Management → Event Settings → Video & Devices → Tile Recognition** (`赛事管理 → 赛事设置 → 画面与设备 → 牌面识别`). Choose an image, video, camera, or OBS Spout source, then preview and crop the hand area. For OBS Spout, install a compatible plugin in OBS separately. Download it from the official releases or use the project's download script to save pinned installers in `reference/local-only/resources/obs-spout/`. [Recognition workflow](docs/AUTOMATION_API.md) · [OBS four-camera setup](docs/OBS_MULTI_CAMERA_DESIGN.md) · [Official Spout releases](https://github.com/Off-World-Live/obs-spout2-plugin/releases) · [Plugin download script](scripts/download_obs_plugin.py)

Recognition results are drafts for the operator to check, correct, and submit. Occlusion, angled shots, and other tiles in the frame can cause missed or incorrect detections. Try recognition with your own images or videos. Development photographs and constructed replays live in a local reference folder. [Local replay sample notes](resources/recognition/samples/README.md)

Run [`UNINSTALL-Automation.cmd`](UNINSTALL-Automation.cmd) to remove optional recognition components and continue with manual operation.

### Model provenance

Model weights come from `nikmomo/Mahjong-YOLO` at pinned revision `28ffceed232ad95fd019c47a6c51ae7c78791a0e`. [Upstream repository](https://github.com/nikmomo/Mahjong-YOLO) · [Pinned v2 checkpoints](https://github.com/nikmomo/Mahjong-YOLO/tree/28ffceed232ad95fd019c47a6c51ae7c78791a0e/trained_models_v2)

- The install script downloads `mahjong-yolon-best.onnx` and `mahjong-yolos-best.onnx` from upstream. The app prefers the selected model size and uses another installed model if that size is unavailable. For example, it can use the downloaded Small model when Medium v2 is absent. ONNX Runtime runs inference on the CPU. [Download and checksum manifest](resources/recognition/manifest.json)
- To build v2 models locally, use `--checkpoints` with the model download script to fetch the upstream `.pt` files. Convert them to Nano, Small, and Medium ONNX models in a separate export environment. [Model download script](scripts/download_recognition_models.py) · [Conversion guide](resources/recognition/THIRD_PARTY.md) · [Conversion manifest](resources/recognition/export-manifest.json)
- The upstream repository has an MIT license file. The ONNX model metadata also lists Ultralytics AGPL-3.0. [Upstream license file](https://raw.githubusercontent.com/nikmomo/Mahjong-YOLO/28ffceed232ad95fd019c47a6c51ae7c78791a0e/LICENSE) · [Model and sample notices](resources/recognition/THIRD_PARTY.md)

## Development and references

- [User and developer documentation index (Chinese)](docs/README.md)
- [`CHECK-Project.cmd`](CHECK-Project.cmd) runs the routine project checks. [Checks and development tools](docs/CHECKS.md)
- [`BUILD-Windows.cmd`](BUILD-Windows.cmd) builds the Windows application.
- [Demo events](demo-suite/README.md) · [Post-match playback](docs/POSTMATCH_PLAYBACK.md)

## License

The project's own code, documentation, and demo assets are available under the MIT license. M.League portraits, tiles, fonts, and recognition models follow their respective third-party terms. [MIT license](LICENSE) · [Third-party notices](docs/ORIGINAL_THIRD_PARTY_NOTICES.md)
