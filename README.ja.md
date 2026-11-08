# Mahjong Overlay Studio

[简体中文](README.md) · [English](README.en.md) · **日本語**

リーチ麻雀大会の配信と OBS へのオーバーレイ出力に使える Windows アプリです。Python と PySide6 で構築しています。配信操作画面で対局の進行と画面表示を操作し、大会管理画面で選手名簿、対局日程、順位表、成績の出力を管理できます。

## クイックスタート

Windows に Python 3.10 以降の対応バージョンをインストールし、プロジェクトフォルダーで起動用ファイルをダブルクリックします。

| 起動用ファイル | 用途 |
| --- | --- |
| [`START-Windows.cmd`](START-Windows.cmd) | 配信操作画面と大会一覧を開きます。Mリーグの選手名簿を使った内蔵デモ（「【演示】M.League 团队积分赛」）で操作を試せます。 |
| [`START-UI-Only.cmd`](START-UI-Only.cmd) | 配信操作画面を単独で起動し、専用のローカル作業領域を使用します。 |

初回起動時にプロジェクト専用の Python 環境を作成し、`PySide6-Essentials` と `mahjong` をインストールします。手動での点数入力、大会管理、OBS 用の出力ウィンドウ、成績表は、この基本環境で利用できます。[基本依存関係](requirements.txt)

## デモ大会をインポートする

大会管理の大会一覧（「赛事管理 → 赛事库」）で「导入完整赛事 JSON」を押し、以下のファイルを選びます。どの JSON にも選手の肖像が含まれており、それぞれ独立した大会として追加されます。[デモの説明](demo-suite/README.md)

- チームリーグは10チーム・40名で、終了した50半荘の記録があります。個人・チーム順位、局ごとの記録、選手の統計を確認できます。[チームリーグの JSON](demo-suite/team-league-demo.json)
- 個人リーグは16名で、終了した20半荘の成績、個人順位、選手の統計を確認できます。[個人リーグの JSON](demo-suite/individual-league-demo.json)
- 個人トーナメントは16名・3ラウンドで、終了した28半荘の記録があります。対戦表から各ラウンドの勝ち上がりと優勝者を確認できます。[個人トーナメントの JSON](demo-suite/individual-knockout-demo.json)

選手名、チーム名、選手写真は Mリーグ公式サイトに基づきます。点数、局の記録、統計は、点数計算エンジンでシミュレーションした対局に基づきます。[名簿と選手写真の出典](resources/demo/roster.json)

## 手動で配信する

1. 大会管理（「赛事管理」）で個人リーグ、チームリーグ、または個人トーナメントを作成し、ルールと選手名簿を設定します。内蔵デモ大会でも操作を試せます。
2. 配信操作画面で席を選び、持ち点、ドラ、副露、テンパイを入力します。和了や流局の際は点数の受け渡しを確認してから精算を確定します。
3. 大会設定（「赛事设置」）で選手カード、通過条件、用語解説の字幕、開幕・試合後の演出を設定します。字幕、通過条件の表示、タイトルカード、大会ロゴをプレビューでき、リーチ演出の再生も試せます。ローカルの音楽ファイルを選ぶと、試聴や開幕・試合後の再生ができます。OBS では、背景が透過した選手カード、サイドバー、全画面演出などのウィンドウを個別にキャプチャできます。[OBS のウィンドウキャプチャ](docs/OBS_WINDOW_CAPTURE.md) · [試合後の再生と音声](docs/POSTMATCH_PLAYBACK.md)
4. 半荘終了後、「半荘管理」で成績を確定し、次の対局に出場する4人を選びます。順位と結果（「排名与结果」）で順位表の確認や PNG・Excel 形式での出力ができます。[配信操作ガイド](docs/LIVE_CONSOLE.md) · [大会管理ガイド](docs/EVENT_WORKSPACE_GUIDE.md)

システム設定はアプリ全体で共通です。大会設定と対局の進行状況は大会ごとに保存します。共通設定と大会設定はそれぞれインポート・エクスポートできます。大会の完全バックアップには、選手名簿、成績、使用中の画像や音声を含められます。既定の大会データの保存先は `data/` です。[設定ガイド](docs/SETTINGS_NAVIGATION.md)

## OBS の4カメラ切り替えスクリプト

OBS 標準の Lua 機能を使って、同じ対局シーン内で4台のカメラを切り替えます。スクリプトが操作するのは指定したカメラソースの表示です。選手カード、スコアボード、字幕は、同じシーンでカメラの上に重ねられます。[プロジェクト付属のスクリプト](mahjong_camera_switch.lua)

1. **対局シーンを用意します。** 4つの異なる映像ソースを追加するか、既存のソースを使って、配信画面上の位置を調整します。卓上の固定位置に通常のツモ順で名前を付けます。例は `机位A → 机位B → 机位C → 机位D → 机位A` です。親が交代してもこの割り当てを使います。練習には色の異なる4つの色ソースも使えます。
2. **スクリプトを追加します。** OBS の「ツール → スクリプト」で `+` を押し、プロジェクトフォルダー内の `mahjong_camera_switch.lua` を選びます。[OBS のスクリプト管理ガイド](https://obsproject.com/kb/scripting-guide#scripts-management-window)
3. **ソース名を入力します。** 「机位 1」から「机位 4」に、OBS のソース一覧に表示される名前をツモ順に入力します。既定名は `机位A` から `机位D` です。4つとも現在の配信シーンに置き、グループ内にある場合は親グループも表示します。
4. **開始位置を決めて試します。** 「检查当前场景」（現在のシーンを確認）を押します。ソースが見つからない場合はシーンと名前を確認します。「显示机位 1（作为切换起点）」（カメラ1を表示）を押して、下家・上家・対面のテストボタンを試します。スタジオモードでは右側のプログラム側のシーンを操作します。配信開始前に試してください。[OBS のスタジオモードでのシーン処理](https://github.com/obsproject/obs-studio/blob/32.2.2/frontend/OBSStudioAPI.cpp#L51-L59)

スクリプト画面を閉じて、キーでも試します。初回追加時の割り当ては以下のとおりです。

| キー | 切り替え先 | `机位A` を表示中の場合 |
| --- | --- | --- |
| `→` | 下家 | `机位B` |
| `←` | 上家 | `机位D` |
| `↑` | 対面 | `机位C` |

切り替え前に、表示するカメラを1つにします。表示が0台または複数の場合は「显示机位 1」を押して開始位置に戻します。テストボタンで切り替えられるのに矢印キーが反応しない場合は、OBS の「設定 → ホットキー」で `日麻镜头` を検索して割り当てを確認または変更します。「绑定方向键」を押すと3つのキーが既定の矢印キーに戻ります。

カメラ切り替えは単独で使えます。牌認識にも4つの映像を送る場合は、各ソースの Spout 連続出力を設定します。[4カメラの牌認識設定](docs/OBS_MULTI_CAMERA_DESIGN.md)

## 牌認識のデモ

牌認識は配信操作を補助するオプション機能です。映像ソースは初期状態で無効になっています。ソース版と標準の Windows ビルドでは、認識モデルと OBS Spout のインストーラーは必要に応じて別途入手します。ソース版で試す場合は、Python 3.11 以降の対応バージョンで `INSTALL-Automation.cmd` を実行します。オプションの依存関係をインストールし、固定バージョンの Nano と Small の ONNX モデルを SHA-256 で検証して `reference/local-only/resources/recognition/` に保存します。[牌認識のインストールスクリプト](INSTALL-Automation.cmd) · [オプションの依存関係](requirements-recognition.txt)

「赛事管理 → 赛事设置 → 画面と機器 → 牌面识别」を開き、画像、動画、カメラ、OBS Spout のいずれかを選びます。プレビュー上で手牌の範囲を指定します。OBS Spout を使う場合は、対応するプラグインを OBS に別途インストールします。公式リリースから取得するか、プロジェクトのスクリプトで固定バージョンを `reference/local-only/resources/obs-spout/` に保存できます。[牌認識の操作ガイド](docs/AUTOMATION_API.md) · [OBS の4カメラ構成](docs/OBS_MULTI_CAMERA_DESIGN.md) · [Spout の公式リリース](https://github.com/Off-World-Live/obs-spout2-plugin/releases) · [プラグインのダウンロードスクリプト](scripts/download_obs_plugin.py)

認識結果は入力候補として表示されます。配信担当者が確認し、必要に応じて修正してから確定します。牌が隠れたり、斜めから撮影したり、別の牌が映り込んだりすると、見落としや誤認識が起きることがあります。お手持ちの画像や動画で試せます。開発用の実写画像や検証用リプレイはローカルの参考資料フォルダーに保管します。[検証用リプレイの説明](resources/recognition/samples/README.md)

[`UNINSTALL-Automation.cmd`](UNINSTALL-Automation.cmd) でオプションの牌認識コンポーネントを削除し、引き続き手動で配信できます。

### モデルの出典

モデルの重みは、`nikmomo/Mahjong-YOLO` のコミット `28ffceed232ad95fd019c47a6c51ae7c78791a0e` に由来します。[上流リポジトリ](https://github.com/nikmomo/Mahjong-YOLO) · [参照コミットの v2 学習済み重み](https://github.com/nikmomo/Mahjong-YOLO/tree/28ffceed232ad95fd019c47a6c51ae7c78791a0e/trained_models_v2)

- インストールスクリプトは `mahjong-yolon-best.onnx` と `mahjong-yolos-best.onnx` を上流から取得します。アプリは選択したサイズを優先し、見つからない場合は別の導入済みモデルを使います。たとえば Medium v2 が未導入なら、ダウンロードした Small で実行できます。推論は ONNX Runtime を使って CPU 上で実行します。[ダウンロードとチェックサムの一覧](resources/recognition/manifest.json)
- v2 モデルを自分で用意する場合は、ダウンロードスクリプトに `--checkpoints` を付けて上流の `.pt` を取得し、専用の変換環境で Nano、Small、Medium の ONNX モデルに変換します。[モデルのダウンロードスクリプト](scripts/download_recognition_models.py) · [変換手順](resources/recognition/THIRD_PARTY.md) · [変換記録](resources/recognition/export-manifest.json)
- 上流リポジトリのライセンスファイルは MIT です。ONNX モデルのメタデータには Ultralytics AGPL-3.0 の記載があります。[上流のライセンスファイル](https://raw.githubusercontent.com/nikmomo/Mahjong-YOLO/28ffceed232ad95fd019c47a6c51ae7c78791a0e/LICENSE) · [モデルとサンプル素材の出典](resources/recognition/THIRD_PARTY.md)

## 開発と関連資料

- [操作と開発のドキュメント一覧（中国語）](docs/README.md)
- [`CHECK-Project.cmd`](CHECK-Project.cmd) でプロジェクトの基本チェックを実行します。[チェックと開発ツール](docs/CHECKS.md)
- [`BUILD-Windows.cmd`](BUILD-Windows.cmd) で Windows 向けアプリをビルドします。
- [デモ大会](demo-suite/README.md) · [試合後の連続再生](docs/POSTMATCH_PLAYBACK.md)

## ライセンス

本プロジェクト独自のコード、ドキュメント、デモ素材には MIT ライセンスを適用します。Mリーグの選手写真、牌画像、フォント、牌認識モデルなどの第三者素材には、それぞれの利用条件が適用されます。[MIT ライセンス](LICENSE) · [サードパーティーの出典とライセンス](docs/ORIGINAL_THIRD_PARTY_NOTICES.md)
