# MTG × Jev vision PoC

Jev 1.14の画像入力とBoxで、複数のMTGカードの位置を検出できるか調べるPoCです。**実写真2枚の初回検証では、各1/8枚を検出後に低確信で停止し、実用目標は未達です。実カード名の同定精度は未評価です。** [固定条件と実測結果](docs/live-baseline.md)を参照してください。

Issue #13で境界断片・誤統合・短名・完了条件を[オフライン修正](docs/offline-fixes.md)し、送信機能のない4×4core/64質問plannerを追加した。旧実測は保持し、追加API呼出しと実写真の改善評価は行っていない。

Issue #15では、画像からNを推定し、そのN個のBoxを同一画像・同一リクエストで求める[単純なfan-out方式](docs/count-batch.md)を実測した。Issue #17で局所cropも比較し、[190 HTTP・推定US$0.017095974までの経過](docs/live-batch-followups.md)を記録。8枚完全の要件は未達で、低確信とraw位置精度を分けて診断している。PoC累計上限US$5、無料枠不足応答で停止する。

| 機能 | 状態 |
| --- | --- |
| CLI・モック専用Web UI・box重畳・JSON保存 | 実装済み、API不要 |
| 手動正解box・IoU / precision / recall / F1 | 実装済み |
| Jev HTTPアダプター | 画像付き実APIで認証・推論成功。Network secret＋環境proxyで実測 |
| 実MTGカードの検出精度 | 2写真ともTP=1、FP=0、FN=7。低Box確信でincomplete停止 |
| cropから名前OCR・候補検索・unknown判定 | [Issue #2](https://github.com/oXyut/mtg-jev-vision-poc/issues/2)、ローカルCLIを実装。実カードは未評価 |
| 動画・リアルタイム表示 | [Issue #3](https://github.com/oXyut/mtg-jev-vision-poc/issues/3)、未実装 |

静止画MVPの目的・受入条件・検証・残課題は[Issue #1](https://github.com/oXyut/mtg-jev-vision-poc/issues/1)で追跡します。

## キーなしで動かす

Python 3.11以上を使用します。依存はPyPIのPillowだけです。

```bash
git clone https://github.com/oXyut/mtg-jev-vision-poc.git
cd mtg-jev-vision-poc
git switch feat/2-local-card-identification
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --index-url https://pypi.org/simple -e .
python -m unittest discover -s tests -v
python -m mtg_jev serve
```

`http://127.0.0.1:8765` を開きます。Web UIはloopbackのみ、モック専用です。キーの入力欄はありません。合成シナリオを表示して「モックboxを表示」、手動正解boxを描いて「手動正解と比較」を押します。座標の数値入力、正解JSONの読込・保存、結果JSON・オーバーレイPNGの保存にも対応します。

名前のOCRは別CLIです。Tesseractと利用言語データをOSの公式パッケージから導入し、[同定の再現手順・方式比較・権利・評価定義](docs/identification.md)に沿って実行してください。Tesseract未導入時はunknownを返します。CIでは公式Ubuntuパッケージを導入し、実ローカルOCRの合成テストも実行します。

任意のPNG/JPEGも選べますが、**モックboxの位置は選択シナリオで固定され、アップロードした画像は認識しません。** 入力と処理結果はサーバーメモリ内で扱い、サーバーは保存しません。保存ボタンを押すと、画像を含むファイルをブラウザーでダウンロードします。公開ネットワークへこの開発サーバーを公開する構成は対象外です。

CLIの再現例:

```bash
python -m mtg_jev demo --output demo
python -m mtg_jev detect demo/multiple.png \
  --mode mock --mock-script demo/multiple.mock.json \
  --output results/multiple --save-crops
python -m mtg_jev evaluate results/multiple/result.json \
  demo/multiple.truth.json --iou 0.5 --output results/multiple/metrics.json
```

出力先は新しいディレクトリを指定します。`result.json`、`overlay.png`、指定時のみ各cropを保存します。`demo/`と`results/`はgit対象外です。0枚・1枚・複数・回転・重なり・画面端の合成データを生成できます。台本は既知の図形座標から作るため、perfect scoreが出ても画像認識の成功を示しません。

## 現行の公式仕様

2026-10-19に[告知](https://typesafe.ai/blog/jev-1-14)、[Images](https://docs.typesafe.ai/images)、[Box](https://docs.typesafe.ai/primitives/box)、[Noul](https://docs.typesafe.ai/primitives/noul)、[Models](https://docs.typesafe.ai/models)、[API](https://docs.typesafe.ai/api)を確認しました。

- 画像とBoxは実験機能で、`TypeSafe-Danger-Zone-Images: true`が必要です。
- Box用には`jev-1.14.0`を固定します。確認時点の`jev-latest`は1.13です。
- Boxは1質問につき1個。対象がなくてもboxを返すため、Noulで未検出対象の在否を確認します。
- 各辺は0–1。PNG/JPEG、各辺280–1568px、16MiB/画像、32MiB/リクエストが上限です。
- 送信前にEXIFの向きを画素へ反映し、メタデータを落とし、縮小・必要な余白を追加します。結果は向き補正後の元画像の座標へ戻します。

## 複数カード探索の仮説と制限

1回のリクエストに未報告カードのNoulと、左端の未報告カードを求めるBoxを入れます。見つかった位置を次のstateへ渡し、既報告カードを除外するよう指示します。画像は変更・塗りつぶしません。重複、低確信、不正応答、カード上限、呼出上限で止まり、部分結果を残します。

**この反復方式は未検証のアプリ側仮説です。** Jevに複数物体検出や列挙の保証があるわけではありません。重なった別カードを同じ対象と扱う、同一カードを再回答する、小さいカードを見落とす可能性があります。公式のnone/one/severalと領域分割方式は比較候補として残しています。閾値も未調整です。

`status=complete`はNoulが「未報告対象なし」と判断したことだけを意味し、実世界での全カード検出を保証しません。上限・低確信・重複は`incomplete`、不正応答・通信失敗は`error`です。カード名は現在`unknown`です。

## 正解と評価

座標はEXIF向き補正後の元画像に対する、軸に平行な正規化座標です。回転したカードにも回転boxや四隅は返しません。初期評価は見えているカード部分の範囲で統一し、隠れた部分を推測するamodal評価と混ぜません。

```json
{
  "schema_version": 1,
  "coordinate_system": "normalized_xyxy_exif_oriented_original",
  "image": {"width": 800, "height": 600},
  "boxes": [{"left": 0.1, "top": 0.2, "right": 0.3, "bottom": 0.7}]
}
```

評価は指定IoU閾値以上の一対一対応を最大個数で求めます。重複予測はFP、未対応の正解はFNです。precision=TP/(TP+FP)、recall=TP/(TP+FN)、F1=2TP/(予測数+正解数)。分母0はJSONのnullです。両方0件は`empty_scene_correct=true`を別に記録します。これはCOCOのAP/mAPではありません。

JSONにはmode、evidence、画像サイズ、前処理変換、閾値、要求/応答モデル、終了理由、API呼出数、usage、推定費用を記録します。ファイル名・元パス・リクエスト本文・画像base64・APIキーは含めません。位置とカード名の精度は別々に評価します。

## 実APIの初回結果と実行条件

2026-10-19の初回実測は計4呼出し、入力20,417／出力144 token、文書記載料金による推定費用US$0.000857514でした。別方式の[none/one/several＋分割実験](docs/partition-experiment.md)を26回追加し、累計30回、推定US$0.003473862です。追試は両写真0採用・未走査領域ありで、改善成功ではありません。回数上限で実APIを停止しました。費用は請求額の確定値ではありません。追加の実行でも次を揃えてください。

1. 権利と外部送信許可を確認したPNG/JPEG、および手動正解box。
2. 承認済みの費用枠・リクエスト回数上限。
3. PoC専用環境での安全なキー供給。既存の別プロジェクト環境へ追加しないこと。

このアダプターは`TYPESAFE_API_KEY`をopaqueな値として受け取り、固定HTTPSエンドポイントのBearerヘッダーへ入れます。CloudのNetwork secretで渡されるプレースホルダーと環境のHTTPSプロキシを使い、今回の専用環境ではTypeSafe認証と画像推論が成功しました。置換そのものはCloud側の機能です。[仕様とキーなし検証](docs/network-secrets.md)を参照してください。`.env`を自動読込せず、キーを探しません。値が未設定なら停止し、認証なしfallbackは行いません。コマンドライン引数へキーを渡さないでください。

承認後の実行形（キー値は記載しません）:

```bash
# 安全な方法でTYPESAFE_API_KEYを供給した専用環境のみ。
MTG_JEV_ENABLE_LIVE=1 python -m mtg_jev detect data/permitted-photo.jpg \
  --mode live --approve-live --max-requests 8 --max-cards 7 \
  --budget-usd 0.05 --output results/live-001 --save-crops
```

上記の値は実行例であり、費用承認を意味しません。環境フラグ・明示的な承認フラグ・正の予算・キーがなければ、画像送信前に停止します。暗黙リトライはせず、リダイレクトも拒否します。HTTPエラー本文は保存・表示しません。

確認時の料金は入力100万tokenあたり$0.042、出力無料です。画像も入力usageへ算入され、事前token見積もりの指針は未公開です。ローカル予算ガードは次の呼出しに65,536入力token分を予約し、判明したusageを加算します。**これは料金表に基づく推定で、プロバイダー側の厳密な課金上限ではありません。** 失敗・タイムアウトでも課金され、usageが返らない可能性があります。厳密な上限にはプロバイダー側の制限も必要です。

## 素材・開発運用

同梱の生成器は自作の無地矩形だけを描きます。MTGのカード画像・ロゴ、ユーザー画像は同梱しません。MTGの商標・カード権利は各権利者に帰属します。公開できない画像や結果をgitやCI artifactへ追加しないでください。

実装希望・バグは先にIssueへ登録し、作業branchとPRからリンクします。IssueとPRに目的・受入条件・変更・テスト・結果・残課題を残します。mainへ直接実装せず、マージには確認が必要です。CIはモック/スタブのみで、APIキーは設定しません。
