# Macブラウザでカメラと予測Boxを試す

Mac上でローカルサーバーを起動し、ブラウザから選択したカメラを開く試用版です。CloudがMacのカメラへ直接アクセスする仕組みではありません。カメラの開始と推論の開始は別の操作です。静止画実験での盤面8枚検出は未達で、カード名同定も未評価です。

## まずmockで操作を確認する

Python 3.11以上が必要です。Node.jsやPlaywrightは通常の利用には不要です。

```bash
git clone https://github.com/oXyut/mtg-jev-vision-poc.git
cd mtg-jev-vision-poc
git switch feat/20-local-camera-ui
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
python3 -m mtg_jev camera
```

Macのブラウザで **http://127.0.0.1:8766** を開きます。

1. 使用するカメラを選び、「カメラを開始」を押してブラウザの許可を与える。
2. 映像が表示されたことを確認し、「MOCKの動作確認を開始」を押す。
3. 台本の枠、confidence、撮影時刻、遅延、過去の推論フレームを確認する。
4. 「すべて停止」を押し、カメラ使用中表示と枠が消えることを確認する。

**mockの枠は画像認識ではありません。** カメラの内容にかかわらず決まった位置を返し、画面と枠にもMOCKと表示します。外部API呼出しは0です。カメラを開始するだけではmock推論も始まりません。

既存の静止画アノテーションUIは `python3 -m mtg_jev serve`、ポート8765に残しています。カメラ画面には正解枠・ファイルアップロード・録画・画像保存の操作はありません。

## Jevで試す

mockサーバーをCtrl+Cで終了します。**Macユーザー自身のローカルキー**を使って次のコマンドを起動します。Cloud Vaultのキーを取り出したりコピーしたりする手順ではありません。

```bash
MTG_JEV_ENABLE_LIVE=1 python3 -m mtg_jev camera \
  --mode live --approve-live --prompt-key \
  --max-requests 20 --budget-usd 0.05
```

端末のキー入力は非表示で、コマンド履歴にキー値を書きません。対話端末でない場合、`--prompt-key`は拒否します。既にMacのプロセス環境に `TYPESAFE_API_KEY` を安全に設定済みなら `--prompt-key` を省略できます。キーはPythonサーバーのメモリで扱い、ブラウザのHTML/JS/config、ログ、使用量台帳へ出しません。

同じローカルURLを開き、カメラ開始後に **「Jevに送信して推論開始」** を押します。選択したカメラを間引いたJPEG静止フレームだけが、ローカルサーバーから既存の固定 `https://api.typesafe.ai/v1/systemone` へ送られます。モデルは `jev-1.14.0`、画像header・proxy・redirect拒否は既存アダプターを使います。送信先を画面から変更する機能はありません。

### 費用と使用量

`--budget-usd`は**この起動で追加する見積費用枠**、`--max-requests`はこの起動の実API回数枠です。1フレーム最大1HTTPで、成功/失敗にかかわらず暗黙retryはしません。

`camera-usage.json`へattempt/質問数/token/見積費用だけを永続化します。画像、動画、モデル回答、端末のカメラ名/deviceId、キーは保存しません。既消費US$0.025210836を初期値とし、カメラ分の既知usageを加えた**PoC累計US$5**を保持します。同じ台帳の同時起動は排他します。`--usage-ledger`で場所を指定でき、既存PoC消費が増えた場合は `--prior-spend-usd` を引き上げて反映できます。既知の初期消費を下げる指定は拒否します。台帳を消したり別ファイルに切り替えたりして回数/費用枠をリセットしないでください。

次の呼出しには、質問数ごとに65,536入力tokenの見積予約を行います。料金表に基づく推定で、プロバイダーの請求確定額/厳密な課金上限ではありません。無料枠不足のHTTP応答・接続失敗・usage不明で推論を停止します。usage不明またはクラッシュ時のpendingを台帳に残し、再起動で自動再送しません。このUIに台帳リセット機能はなく、再開にはプロバイダー使用量との整合確認が必要です。支払/自動チャージ設定は変更しません。

### 検出方式

| 画面の方式 | 1フレームのHTTP/質問 | 意味 |
| --- | --- | --- |
| 1枚提案 | 1HTTP / 2質問 | 在否と左端1枚のBox。全カードの検出ではない |
| 複数提案・最大4候補 | 1HTTP / 8質問 | 一般容量4の短ordinal。各候補の在否とBox。重複が疑われる枠は保留 |

在否0.8、confidenceは画面の表示閾値を使います。低confidenceを位置誤りと断定しません。採用されない候補は「保留」として件数を表示します。confidenceの値は昇格・補正しません。画像にはモデル予測だけを描き、正解枠は送りません。

## 遅延と停止の動作

- videoはブラウザが描画し、推論とは独立して動きます。推論完了後に2/3/5/10秒のtimerを置きます。フロントとサーバーで同時1件に制限し、未処理frameのqueueを作りません。
- 推論中は以前のlive枠を消します。撮影から1.5秒以内の枠だけを破線で重ね、「過去の予測」と経過時間を表示します。追跡・現在位置の保証ではありません。
- 遅い結果はlive映像へ重ねず、送信した静止フレーム側に対応する枠を表示します。こちらも「過去の静止画」と表示します。ミラー反転はlive表示だけで、Jevへ送る画素と静止previewは左右反転しません。
- 一時停止は以後の送信を止め、映像は継続します。「すべて停止」、カメラ変更、タブ非表示、pagehideでは映像track・timer・rAF・fetchを解放し、枠と静止previewを消します。再開始は手動です。
- stop前に送った1件はサーバーで完了・課金され得ます。ブラウザのキャンセルで請求が取り消されるとは扱いません。サーバーは完了usageを記録し、旧sessionの結果は画面へ戻しません。カメラ許可がstop後に解決した場合も取得trackを直ちに止めます。

## ローカル提供を選んだ理由

カメラAPIはsecure contextが必要ですが、loopbackは信頼可能originとして扱われるため、Macのlocalhost HTTPで使う構成を選びました。外部公開やCloudからMacへの接続は不要です。127.0.0.1だけでlistenし、Host/Origin/CSRF nonce/Content-Type/サイズを確認します。micは要求せず、Permissions-Policyでも無効です。画像・API本文・URLのHTTPログを出しません。

参照した一次仕様: [W3C Media Capture and Streams](https://github.com/w3c/mediacapture-main/blob/main/getusermedia.html)、[W3C Secure Contexts](https://github.com/w3c/webappsec-secure-contexts/blob/main/index.bs)。localhostのブラウザ挙動とmacOS権限はMac実機で別途確認が必要です。

## Cloudでの検証と、Mac実機の最小確認

```bash
python3 -m unittest discover -s tests -v
node --test tests/camera-core.test.mjs
# 開発用。Playwright＋ローカルChromiumがある環境のみ。
python3 tests/run_camera_browser.py --chromium /usr/bin/chromium
```

Cloudでは合成画像、stub API、Chromium fake cameraだけを使います。キーを使う実APIループ、実Macカメラ、Safariでの確認はしていません。

2026-10-19のCloud確認: Python 124テスト、JavaScript 5テストが成功。Chromium fake cameraで自動開始なし、デバイス選択、動画時計の進行、定期送信、枠の期限切れ、pause/stop、遅い応答の破棄、許可拒否、遅い許可解決時のtrack解放を確認しました。実APIは0回。wheelへHTML/JSを同梱できることも確認しています。

Mac側の最小確認は次の順です。

1. mockで起動し、開始前にカメラ使用中表示がないことを確認する。
2. 内蔵カメラを開始し、映像が動くこと、推論開始までは送信されないことを確認する。許可されない場合はブラウザとmacOSのカメラ権限を確認する。
3. mockの枠・反転・期限切れ・一時停止・カメラ切替を確認する。
4. すべて停止してカメラ使用中表示が消えることを確認する。タブ切替/非表示でも停止することを確認する。
5. liveで少額・少回数の枠を指定し、自分のカードを映して送信を明示開始する。時刻、遅延、confidence、過去frameの対応を確認し、停止する。

実機未確認を精度検証済みとは扱いません。照明、反射、カメラ距離、重なりが変わるため、このUIで精度が改善したとは主張しません。
