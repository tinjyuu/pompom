---
title: PomPom
emoji: 🍅
colorFrom: orange
colorTo: pink
sdk: static
app_file: index.html
pinned: false
short_description: A dancing focus buddy for Reachy Mini Wireless
tags:
  - reachy_mini
  - reachy_mini_python_app
---

# PomPom 🍅

集中が続くと踊り、スマホを持つと声をかける、Reachy Mini Wirelessの相棒です。
本体からOpenAI Decisions APIへ直接接続します。共有APIやMacの常駐は不要です。

## 使い方

1. Reachy Miniのダッシュボードから、このSpaceをアプリとしてインストールし、`pompom`を起動します。
2. **http://reachy-mini.local:8043/**（または本体のIP:8043）を開きます。
3. OpenAI APIキーを登録し、カメラ確認・通知テストを行います。
4. 集中する時間（初期値25分）を保存し、画像送信への同意後に開始します。

このSpaceは紹介・配布ページです。カメラやOpenAI APIキーをSpaceへ送る操作画面ではありません。
APIキーは利用者自身のものを本体で入力してください。API利用料金とOpenAIの保持条件が適用されます。

## 動作

- 手元が見えてスマホを持っていない判定が10秒続くと、公式`dance1`で踊ります。
- ダンス中に最初のスマホ保持判定が返ったら、ダンスを中断して公式`fear1`へ切り替えます。
- 通常は保持確率0.8以上・可視性0.7以上が2秒続くと注意します。クールダウンはありません。
- プリセット音声の後に、2種類の日本語の声かけからランダムに1つを再生します。
- 手元が見えてスマホを置いたと判定したら、公式`cheerful1`で1回喜びます。
- 一時停止・終了・設定変更・期限切れはローカルで先に処理し、遅延した判定を破棄します。
- 判定不能・通信失敗・10秒以上の判定欠落でも集中の継続時間をリセットし、ダンスを止めます。
- アプリが動作していれば、操作ページを閉じても本体のタイマーと観測は継続する構成です。

判定は撮影・通信・推論の速度に依存します。実際にスマホを持ってから正確に2秒で反応する保証はありません。
机に置いたスマホは対象外です。仕事でスマホを使う際は一時停止してください。
集中力・意図・医学的な状態を診断するものではありません。

## OpenAIへの直接接続

送信先は`https://api.openai.com/v1/decisions`に固定し、モデルは`gpt-6-luna`を使用します。
[公式Decisions APIガイド](https://developers.openai.com/api/docs/guides/decisions)に準拠します。
Decisions APIの利用権限があるAPIキーが必要です。APIエラー時は画面に状態を表示します。

JPEGはメモリ内で長辺640px以下へ縮小し、1MiB以下に制限します。画像はこのアプリでは永続保存しません。
同時判定は1件、次の撮影は最短1秒間隔です。レート制限時は30秒待機し、残高・認証・権限エラーは一時停止します。
APIキーは`~/.config/pompom/settings.json`に0600で保存し、画面へのAPI応答やログに出しません。
`OPENAI_API_KEY`環境変数は、まだ設定ファイルがないときの初期値としても利用できます。

## 既存アプリとの共存

共有API版[Reachy Focus](https://github.com/aieo-product/devdayHackathon261018)とは別のアプリです。
`pompom`パッケージ・entry point、ポート8043、`~/.config/pompom/`を使用します。
既存の`reachy_focus`（ポート8042・設定`~/.config/reachy-focus/`）を削除・上書きしません。
本体のアプリ管理で、使いたいアプリを選んで起動してください。

## 開発・検証

```sh
python -m pip install -e '.[dev]'
python -m pytest -q
ruff check pompom tests
python -m build
python scripts/check-package.py
reachy-mini-app-assistant check .
```

LinuxではSDKのビルドにCairo・GObjectの開発ライブラリが必要です。CIのインストール手順を参照してください。

共通API版の実装・実機で確認したモーション・音声素材を引き継いでいます。
PomPomの直接接続、判定失敗、停止後の遅延応答、タイマー、音声ヘッダーは自動テストで確認します。
本リリース時点で、PomPomからの有料OpenAI実要求と実画像認識、実機の長時間継続は未確認です。
公開したSpaceと、Reachyの標準アプリ一覧への掲載は別です。
