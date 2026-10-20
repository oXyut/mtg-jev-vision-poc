# Jev公式一次資料とカメラ修正の対応（2026-10-19）

## 調査結果

| 公式一次資料 | 確認事項 | この実装での扱い |
| --- | --- | --- |
| [2026-10-14 Jev 1.14公式ブログ](https://typesafe.ai/blog/jev-1-14) | 画像/Boxは本番品質に未到達。質問を最小の判断単位へ分ける | 画像精度未達をUI/文書に残し、在否・局所単一性・Boxを分ける |
| [Models](https://docs.typesafe.ai/models) | 1.14.0は同日公開。previewは1.14、latestは1.13。英語に強い | `jev-1.14.0`を固定し、英語instructionsを使用。latestへ置き換えない |
| [Box](https://docs.typesafe.ai/primitives/box) | 1質問1Box。不在でもBoxを返す。対象を一意に指定し、区別困難ならChoice/分割/cropや辺のNoul確認を検討 | Noul併設、単一対象caption、原画像cropの確認を使用。複数枚列挙の保証とは扱わない。辺Noulは未実装 |
| [Primitives](https://docs.typesafe.ai/primitives) | 質問は独立並列。IDはモデルに見えず、他の質問の回答を参照できない | 各instructionsで対象を記述し、同一response内の別回答に依存させない |
| [Fan-out](https://docs.typesafe.ai/patterns/fan-out) | 共通stateに対する独立質問をまとめられる | 各段階の在否/Box/単一性を同じ画像stateでまとめる。前のBoxからcropを作る段階だけ次HTTPへ進む |
| [Images](https://docs.typesafe.ai/images) | PNG/JPEG、辺280–1568、16MiB。自動縮小/EXIF向き補正なし。共通画像をstateで共有 | 送信前にEXIFを画素へ反映し除去、縮小/paddingを実施。1つのrequest内では画像を1度だけassetsへ入れる |

独立に取得した公式SDK Python最新mainは [f36e68549ceca3191e5791e5f99ea1baa1a258b5](https://github.com/typesafe-ai/typesafe-sdk-python/tree/f36e68549ceca3191e5791e5f99ea1baa1a258b5)（v0.7.5）。[JS v0.7.0 release](https://github.com/typesafe-ai/typesafe-sdk-js/releases/tag/v0.7.0)も互換性資料。公式の[skills/質問設計指針](https://github.com/typesafe-ai/skills/blob/65a39f393687675ce170e6094757de20370365b9/skills/typesafe-ai/SKILL.md)は、十分なstate、1問1つの判断、独立質問のまとめ、実データでの閾値評価、typed outputと正しさの分離を説明している。

モデル変更は行わない。費用は既存係数（入力100万token US$0.042、出力無料）の見積を継続し、usageを保存する。請求確定額や無料枠残量の保証ではない。新しいhostへの接続許可や課金設定は変更しない。

## プロンプトの修正

cameraが実際に使う指示は [camera_prompts.py](../mtg_jev/camera_prompts.py) に集約した。UIの「実際のプロンプトを確認」、`GET /api/camera/prompts`でも選択中の方式・上限・instructions・profile/hashを確認できる。キーや画像は返さない。

- 単一カードは現在frameにカードがあるかと、その左端1枚の可視印刷領域を問う。未報告条件/空excluded_boxesは使わない。
- 複数枚探索は、実際に灰色で隠した採用済みモデル枠の外側にある印刷領域を問う。元画像cropでは存在/単一性/中央対象のBoxを別質問にする。
- 順位一括は各順位の意味をinstructionsへ書く。質問IDだけで区別しない。ただし独立した順位回答が一意な全カード集合を作る保証はないため、比較用の実験モードにする。
- 可視印刷領域、sleeve除外、隠れた辺の全体推定をしない採点規則は維持する。新しい成功判定のために枠規則を変えない。

これらは公式の逐語推奨プロンプトではなく、公式の質問設計原則を参照したPoCの指示である。confidence .6やcrop整合IoU .75はアプリ側の方針で、公式既定閾値ではない。

## 既定を複数枚探索へ変えた理由と限界

旧カメラ既定は1枚、複数モードも4候補で、8枚を検出する用途を構造的に満たさなかった。修正後の既定は一般容量16、1frame最大64API、起動全体は別の回数/費用枠を持つ。8という真値はプロンプトや停止判定へ使わない。

既存の短caption→実画素mask→原画像crop/再中心化をカメラ経路へ接続した。過去の1runでは整列8/8・盤面1/8だった方式に近いが、camera用の指示と入力サイズを変えた今回の精度は別測定とする。矩形maskが重なりカードを隠すこと、局所cropが別カードを再選択すること、複数HTTPで遅くなることは解消を保証していない。公式がこの列挙方式を推奨/保証しているとは書かない。

## 過去の失敗と今回繰り返さないこと

- 推定N→特定順位の一括Boxは、整列でN=8でも低confidence・重複を生んだ。fan-outのAPI対応と8枚成功は別。今回の既定にはしない。
- none/one/several＋領域分割も既に実測し、当時の回数枠では未走査領域が残り、採用0だった。境界/深さ等の実装不具合は別途修正したが、実写真改善を確認したことにはならない。公式に分割が載るという理由で同一試行を繰り返さない。
- 対応cropは固定候補1件で改善したが、別件では既採用個体へ戻った。全体方式の改善成功として採用しない。
- 2枚の開発写真での結果を、未知のMacカメラ映像への汎化性能に置き換えない。

## 比較方法

旧commit `87191276b62e1369619b1b7120d203078e514b9d` のsingle/ordinal4と、新single/新ordinal16/新既定multiを比較する。2枚を長辺1280・JPEG品質82・EXIFなしへ処理し、方式間で同じ入力byteを使う。予測保存後に可視印刷領域AABBの一対一IoU .50/.75を採点し、HTTP/質問/usage/見積費用/遅延も分ける。

Mac試用分を含む予算確認後に実測する。接続・依存不足は認識精度の失敗と分ける。実測結果は後続の結果表へ追記し、改善なし/悪化でもそのまま記録する。

遅延の参考として、過去の別入力1536pxのR整列は17HTTP/12.672秒だった。これはTTL1.5秒を超えるためlive枠ではなく過去frame表示となる。今回の1280px比較とは別結果で、速度改善を意味しない。現行UIは検出件数と枠非表示を区別し、10秒超の応答後は自動一時停止、1APIの高速比較を提示する。
