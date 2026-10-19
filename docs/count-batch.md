# 未知枚数のcount → Box fan-out（Issue #15）

実測を再開した。[190 HTTPまでの結果とraw診断](live-batch-followups.md)を参照。以下の準備時の条件を保持し、採用とraw候補を別集計している。

## 1質問と1リクエストの区別

[公式JS型定義](https://github.com/typesafe-ai/typesafe-sdk-js/blob/main/src/types.ts)と[Python wire schema](https://github.com/typesafe-ai/typesafe-sdk-python/blob/f36e68549ceca3191e5791e5f99ea1baa1a258b5/src/typesafe_sdk/_schemas/models.py)では、`questions`は複数質問の辞書、各`BoxAnswer`は単一Box、`answers`は質問ごとの辞書である。**1つのBox質問の単一出力は、1リクエスト全体を1Boxへ制限する意味ではない。** アプリ側で複数回答を配列へ整理できるが、1つのBox質問から任意長配列が返るとは仮定しない。

依頼元から提供された[公式Box](https://docs.typesafe.ai/primitives/box)と[fan-out](https://docs.typesafe.ai/patterns/fan-out)の指針に沿い、複雑な空間分割より先に単純な2 HTTP方式を比較する。docs hostは環境から未許可のため直接アクセスせず、SDKの型は許可済みGitHubで照合した。同じstateに対する独立質問なので、質問IDだけや同じinstructionsの複製では別のカードを選べない。

## 初期固定条件

1. 元画像1枚にChoiceで0..16/over_capacityの枚数とNoulの在否を質問する。16は一般的な探索容量であり、写真の正解枚数ではない。confidence>=0.8と在否の整合性を満たさなければBoxへ進まない。容量超過は未解決にする。
2. モデルが画像から推定したNに応じて、N個のBoxとN個の存在Noulを**同一画像・同一HTTP**に載せる。各instructionsに「全可視カードの印刷領域AABB中心x、同値ならyで並べた1-based rank i」を明記する。真値のN・名前・座標は使わない。
3. Noul>=0.8・Box confidence>=0.6なら採用候補。在否<=0.2は棄却、間の値・null/低Box確信は未解決。同じ矩形等でIoU>=0.85となる候補対は同一性未確認として両方を保持し、union・confidence昇格・枚数合わせはしない。
4. 順位不整合は別に記録し、真値に合わせて並べ替えない。重複でない高確信の物理枠は予測集合として保持するが、不整合があれば探索状態はincomplete。採用/棄却/未解決と、採点側の未検出を分ける。

`mtg_jev.batch.detect_batch`がこの2段階を実装する。count=0なら2回目は不要。通常のN>0ならHTTP=2、質問数=2+2N、画像は各HTTPで1枚。直接Jevの名前同定は実行しない。

## 予算と現在の実行状態

新承認は既消費US$0.003473862を含むPoC累計US$5。旧30回上限・追加2回だけという制限は解除された。アダプターの承認上限をUS$5へ更新し、batchでは質問数に応じて65,536入力token/質問の予約額を増やす。これは文書料金からの保守的な推定であり、プロバイダーの厳密な請求上限を設定したものではない。全実験の累積ledgerを共有し、usage不明の失敗では停止する。

準備時は無料残高の数値確認待ちだったが、その後の本人の訂正・再承認で、無料枠自動停止を前提に実測を再開した。取得済みSDKには無料残高参照APIを確認できず、固定systemone先から得られるusageを全試行で保存する。無料枠不足応答や費用不明で停止し、支払/自動チャージ設定は変更しない。最新の使用量は上記実測文書に記録する。

## オフライン検証

92テスト成功。モデルcountに応じた質問生成、uncertain/over_capacity/0枚、存在矛盾、null、重複、順位不整合、単一画像＋複数Boxのmultipart、Box配列を仮定しない検証、US$5と質問数比例の予算ガードを含む。SDK wire schemaへ2質問と複数Boxの6質問文書を渡し、ローカル構造検証は成功した。サーバーでのbatch受理と認識精度は未実測。

実API再開時はscene-Aを先に処理し、同じ条件でscene-Bを診断する。検出図は採用予測だけを主表示し、棄却/未解決候補、手動正解は別図にする。IoU 0.50/0.75のTP/FP/FN、precision/recall、重複、時間、費用を分けて報告する。旧実測・修正PR・私的な30回ledgerは保持し、mainにはマージしない。
