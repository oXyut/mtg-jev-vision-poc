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
