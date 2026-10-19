# ローカルでのcrop同定（Issue #2）

静止画基盤のPR #6に積み重ねる変更。外部有料APIは呼ばない。全カードをJevのクラスへ列挙する方式は使わない。検出CLI・Web UIは従来どおりunknownを返し、別の`identify` CLIが後処理する。

## 比較と選定

| 方法 | 今回の判断 | 未解決点 |
| --- | --- | --- |
| ローカルOCR → 名前/alias検索 → 厳格な採否判定 | 採用。APIキー・外部画像送信不要。ローカルカタログ全体を検索 | OCR誤読、カタログの欠落、タイトルが見えない画像 |
| 参照画像の特徴量/ハッシュ等で候補検索 | 比較候補。今回は実装しない | 参照画像の権利、版違い、射影・照明・画角差の実データ評価 |
| 検索候補だけをJev Choice等で比較 | 将来の補助候補。今回は実装/呼出ししない | 正解候補欠落とnone判定、実API費用・遅延・精度 |

[Tesseract公式の前処理ガイド](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html)を確認した。4方向の90度回転、各向きの上部タイトル領域、グレースケール/コントラスト調整/余白/拡大、PSM 6を使う。任意角のdeskew、透視補正、カードレイアウト別のタイトル領域は未実装。4方向各5秒が既定のタイムアウト。画像はstdin、OCR結果はstdoutで受け取り、TesseractのstderrやOCR本文をログ・結果JSONへ出さない。

## unknownの決定

名前とaliasはUnicode NFKC・casefold後、英数字/文字を残して比較する。同じ正規化名を持つ別IDを統合して断定しない。

- 全カタログで一意の完全一致、タイトル4文字以上、最も低い単語のOCR confidenceが80以上、OCR処理完了の場合のみidentified。
- 類似文字列は候補として返すが、自動採用しない。OCR confidence 60未満の行は候補検索にも使わない。
- 部分カタログは完全一致してもcandidate-onlyとしてunknown。
- alias衝突、向き間の矛盾、文字なし、低品質、engine/言語データなし、処理失敗はunknown。
- top-kは出力数だけに適用する。top-k=1でも、全カタログで検出した曖昧さを消さない。

類似度とTesseract confidenceは校正済みの正答確率ではない。閾値は実カードで未調整。完全一致でもOCRが別名へ誤読する可能性は残る。`reference`は利用者が宣言する評価対象範囲であり、完全性を検証した証拠にはならない。部分カタログをreferenceと偽ると正解候補欠落を検出できない。

## カタログと権利（2026-10-19確認）

- [Tesseract](https://github.com/tesseract-ocr/tesseract/blob/main/LICENSE)はApache-2.0。engine/言語データはOSの公式パッケージで別途導入する。
- [MTGJSON公式License](https://mtgjson.com/license/)はMITで、コピーや相当部分の配布には著作権・許諾表示が必要。[Atomicモデル](https://mtgjson.com/data-models/card/card-atomic/)にはname、faceName、foreignDataがある。これは第三者のカード画像の権利まで確認できたことを意味しない。
- ScryfallのAPI/画像/カタログ利用条件はこの環境で403のため確認できず、採用・取得しない。
- 実DB、実カード画像、参照画像集は同梱・自動ダウンロードしない。全画像の再配布はしない。将来、許諾済みの名前カタログをローカルで用意し、出典・ライセンス・取得日・利用権確認を記録する。
- 同梱生成器は自作の架空名3件と無地図形だけを使う。MTGの画像・ロゴ・公式デザインは使わない。生成画像・カタログ・出力は`demo/`、`data/`、`results/`に置きgit対象外にする。

ローカルカタログ形式（aliasesは任意、最大16件/名）:

```json
{
  "schema_version": 1,
  "coverage": "partial",
  "provenance": {
    "source": "YOUR_VERIFIED_SOURCE",
    "license": "YOUR_VERIFIED_LICENSE",
    "retrieved_at": "YYYY-MM-DD",
    "rights_confirmed": true
  },
  "cards": [{"id": "fictional-amber", "name": "Amber Harbor", "aliases": []}]
}
```

`rights_confirmed`は利用者の申告であり、ソフトウェアによる法的確認ではない。実カタログを無条件に公開してよいという意味ではない。CLIは最大32MiB・50,000件のカタログを扱う。

## キーなし再現

Ubuntu例: `sudo apt-get install tesseract-ocr tesseract-ocr-eng`。日本語には対応言語データの別途導入が必要。現在のローカル環境で確認できたのはeng/osdで、日本語カードのOCR実測はない。

```bash
python -m mtg_jev identification-demo --output demo/identification
python -m mtg_jev identify demo/identification/photo.png \
  --detections demo/identification/detections.json \
  --catalog demo/identification/catalog.json \
  --crop-source ground_truth --output results/identification/result.json
python -m mtg_jev evaluate-identification results/identification/result.json \
  demo/identification/truth.json --top-k 5 \
  --output results/identification/metrics.json
```

このデモは既知幾何の正解boxからcropし、自作の架空タイトル2件へ実Tesseractを実行する。Jevによる位置検出を経ておらず、実MTG認識の成功率ではない。`mode`と検出evidenceを保持し、`identification_run`へOCR方法・crop_source・カタログhash・実行時間・外部API呼出数0を付ける。

実画像を評価するときは、まず手動正解boxをdetections配列に入れて`--crop-source ground_truth`で同定単体を測る。別に予測box JSONへ`--crop-source predicted`で同じOCRを実行し、検出誤差込みの性能を測る。同じ画像サイズと向きのフレームを使う。JSONは画像内容そのものの同一性を保証しないため、評価者が同じ画像であることを確認する。

## 同定評価の定義

正解は静止画評価と同じcoordinate_system/imageに、`objects: [{"box": {...}, "card_id": "canonical-id"}]`を付ける。card_id=nullは名前が未知の対象。IoU閾値による一対一の対応を先に作り、位置指標は`localization`に分けて返す。

| 指標 | 分子 / 分母 |
| --- | --- |
| top1_on_matched_known | 採用名が正しい対応box数 / 名前既知の対応box数 |
| topk_on_matched_known | 候補k件内に正解がある対応box数 / 名前既知の対応box数 |
| unknown_rate | unknown予測数 / 全予測数 |
| misidentification_rate_among_accepted | 誤った採用名数 / 全採用名数。位置FP上の採用も誤り |
| end_to_end_name_recall | 正しい位置対応と採用名の数 / 名前既知の全正解数 |
| end_to_end_name_precision | 正しい位置対応と採用名の数 / 全採用名数 |

分母0はnull。unknownの候補recallと、断定した名前の正答を混同しない。正解crop/予測cropは別実行・別集計する。

## 未完の実評価

実カードでの日本語/英語、細字、反射、遮蔽、任意角回転、表裏、辞書外、候補欠落、実カタログ規模での速度と閾値調整は未完。版・印刷の同定も対象外。合成OCRの結果を実MTG精度として報告しない。

2026-10-19追記: 写真2枚のJev位置検出baselineを実行したが、両方とも1枠後に低確信で停止した。一般カタログ未取得・日本語OCR言語データ未導入のため、名前同定は未評価。直接Jevによる名前同定も現行baselineにはなく、OCR方式との精度比較は実行していない。[実測と制約](live-baseline.md)、Issue #11参照。以下の計画は実行前の記録として残す。

## 提供された実写真2枚の評価計画（未実行）

| 評価ID | 条件 | 用途 |
| --- | --- | --- |
| scene-A | 8枚が重なり、一部横向き、ダイスによる遮蔽、反射がある盤面 | 実用上の最低ライン・主評価 |
| scene-B | 同じ8枚を2行4列に整列した盤面 | 原因切り分け用の基準写真 |

scene-Aで8枚をそれぞれ検出し、各カード名を正しく同定することを初期合格案とする。scene-Bだけの成功では目的達成としない。見落とし・重複・複数カードをまとめたbox・unknown・誤同定を整列版の成績で相殺しない。動画検証（Issue #3）より先にこの静止画条件を評価する。

### 正解と推論の分離

正解8名とscene-Bの並びとの対応はユーザー確認済み。具体名と対応表は非公開の採点側だけで扱い、この文書・公開Issue・ソース・テストfixtureへ含めない。日本語/英語の表記差は一般的なcanonical ID/alias規則で採点し、8枚専用の推論ルールは作らない。

推論は各写真を独立に処理する。正解名・正解枚数・正解対応・8枚に絞った候補集合を渡さず、通常の権利確認済みカタログと共通の検索手法を使う。scene-Bから作ったcrop・認識名・画像固有のcacheをscene-Aへ渡すこともしない。画像と一般設定だけを推論側へ置き、採点ファイルへはアクセスさせない手順を実行前に用意する。

モデル・カタログ版・候補検索・閾値・探索上限・前処理を事前に固定する。予測を確定してから正解を採点側へ読み込み、正解枚数に合わせて探索を打ち切ったり、候補を選び直したりしない。画像を見て調整した結果は開発用の結果として記録し、未見画像での汎化性能とは区別する。

### 実行前に固定する採点条件

- 各写真の可視範囲AABBを手動で作成し、隠れた部分の推測boxと混ぜない。不確かな辺は注記する。
- カードごとの一対一対応、TP/FP/FN、precision/recall、各カードのIoU・平均・中央値・最小値を記録する。対応用のIoU閾値0.50を暫定案とし、0.75でも別集計する。最終の正解boxと閾値は実行前に確定する。
- 名前の初期目標はscene-Aの採用top-1名8/8、unknown 0、誤同定0。候補top-k内の正解率は診断値であり、名前同定成功の代替ではない。
- 枠の精度と名前の成績を別々に報告する。位置対応を含むend-to-endで採点し、正解cropのOCRだけの成功を最低ライン達成と扱わない。
- scene-A/scene-B、正解crop/予測cropを別集計し、検出・crop・OCR・候補検索・採否の失敗を分ける。上限停止はincompleteとして記録し、box数だけから完全検出と主張しない。

### 非公開素材と保留条件

写真、crop、overlay、正解名の対応表、EXIF、個人情報を公開リポジトリ・Issue・CI artifactへ置かない。将来の出力も非公開の評価場所で扱い、公開報告は匿名の集計値と失敗分類に限る。写真2枚だけの成績から一般性能を主張しない。

実API利用、PoC専用環境の通信許可、本人キー入力は依然保留。今回の変更は要件と評価計画の追記だけで、写真の取得・保存・処理、実API呼出し、実評価は行っていない。採点用の入力分離や正解boxの準備も今後の作業である。
