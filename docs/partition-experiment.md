# none/one/severalによる分割実験（Issue #11、baselineとは別）

## 仕様照合

公式SDK [schemas/models.py](https://github.com/typesafe-ai/typesafe-sdk-python/blob/f36e68549ceca3191e5791e5f99ea1baa1a258b5/src/typesafe_sdk/_schemas/models.py) と [endpoints.py](https://github.com/typesafe-ai/typesafe-sdk-python/blob/f36e68549ceca3191e5791e5f99ea1baa1a258b5/src/typesafe_sdk/_core/endpoints.py) をGitHubから取得して照合した。画像をstate中のopaque参照とassetsで結び、request JSONとPNGのmultipartで送る既存形式は一致している。Boxの左右は幅比、上下は高さ比で、Box confidenceはnullも許される。基準コードは数値の低確信をincompleteとして扱い、不在とは扱っていなかった。nullも不在の根拠にはしない。

一方、`excluded_boxes`はAPIの除外演算ではなくstateの任意データと文章指示である。1質問が常に1つのBoxを返すため、未報告物体の確実な列挙を保証しない。複数質問は同じstateに対する独立した判断であり、キー名の連番や同じinstructionsを並べても列挙にならない。

[公式Box文書](https://docs.typesafe.ai/primitives/box)のnone/one/severalと領域分割の提案に沿い、従来のNoul＋未報告左端探索を残したまま、別モジュール`mtg_jev.partition`で実験する。今回の環境から未許可のdocs hostへは接続しておらず、同文書の指針は依頼元から提供されたもの。SDKの形式はGitHub上の一次資料から直接確認した。

## API実行前に固定する条件

- 全写真に同じ条件を適用し、正解名・正解枚数・正解枠・正解配置に基づく領域指定を使わない。入力は単一写真と一般設定だけ。
- rootは全画像。長辺の中点で2分割し、子領域を親の25%幅だけ重ねる。breadth-firstで両子領域を走査する。深さ上限4、1写真13呼出しまで。画像の最有力領域だけを選んで終わらない。予算で未走査領域が残ればincompleteと明記する。
- 1リクエストに1つのcropとNoul、Choice、Boxを送る。質問キー名に意味を持たせない。Noul/Choice/Boxは互いの回答を見ずに同じ画像へ回答し、アプリ側でゲートする。
- Noul<=0.2、Choice=noneかつconfidence>=0.8なら、その領域のBoxを棄却して空とする。Noul>=0.8、Choice=oneかつconfidence>=0.8、Box confidence>=0.6のときだけ採用する。several、矛盾、不確実、Box confidence=null／低値は分割または未解決とし、不在へ変換しない。
- 元画像とcropのメタデータを落とし、既存のresize/paddingで各辺280〜1568pxにする。Boxはpaddingを逆変換してcrop座標へ戻した後、元画像座標へ写す。
- 複数領域での重複はIoU>=0.65、または小さい枠の包含率>=0.9でグループ化し、unionする。重なった別カードを誤統合する恐れは評価上の制限として残す。
- 採点はbaselineと同じ可視部分ルール・IoU 0.50/0.75。初回事前注釈v1を保持し、座標訂正v2の結果を分離する。分割線をまたぐ正解の見落とし、未走査領域、統合前後の件数も報告する。
- 先の4回を含む累積30回／US$1を共有ledgerで管理し、不明usageで以後のAPIを止める。最大26回を追加可能。キー実値を取得せず、固定先・HTTPS proxy・redirect禁止・自動retryなしを維持する。

## オフライン検証

65テスト成功。分割両子の走査、padding逆変換と元画像座標、none時の架空Box棄却、nullと低確信、Noul/Choice矛盾、重複統合、上限と未解決のincomplete、既存baselineと秘密供給の回帰を含む。合成・スタブの成功を実画像認識と混同しない。

このモジュールは実験用のPython APIで、既存detect CLI/Webの方式は変更しない。日本語OCRと一般カタログは独立の未準備課題である。
