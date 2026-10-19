# Network secretの互換性と未検証範囲（Issue #8）

2026-10-19追記: 承認済み専用環境で、Network secretのplaceholderと既定HTTPS proxyを使った画像付き実API呼出しが初回4件・分割追試26件、計30件成功した。固定先・モデル・redirect禁止は維持し、キー実値を取得・表示・保存していない。[初回実測](live-baseline.md)と[分割追試](partition-experiment.md)を参照。以下の「未実行」はコード側互換性を検証した当初の記録である。他環境や全エラー条件での実置換を保証するものではない。

## 確認した仕様

2026-10-19に[Cloud environments公式](https://learn.chatgpt.com/docs/environments/cloud-environments#configure-environment-variables-and-network-secrets)を確認した。Network secretはプロセスにplaceholderを渡し、許可した宛先へのHTTPS/443通信で管理proxyが実値に置換する。実値をローカルプロセスやファイルに置く方式とは異なる。同名の直接環境変数とは重複させない。

この資料はコード互換性の説明であり、資格情報設定やAPI実行の承認ではない。今回は秘密の取得・設定・実API呼出しを行っていない。

## 現アダプターが行うこと

- `TYPESAFE_API_KEY`をopaqueな値としてBearerへ入れる。raw keyの形式を仮定せず、placeholderを自前で置換・復号・生成しない。
- 値がない場合はネットワーク処理前に停止する。`.env.example`の説明用リテラルも拒否する。認証なしfallbackを追加しない。
- 宛先は`https://api.typesafe.ai/v1/systemone`で固定。HTTPSの既定portは443。任意のbase URLやportを受け取る設定はない。
- `urllib.request.build_opener`の既定ProxyHandlerを保持する。[Python公式](https://docs.python.org/3/library/urllib.request.html#urllib.request.ProxyHandler)の環境proxy処理を使い、`HTTPS_PROXY`/`https_proxy`を尊重する。proxyエラー時に直接接続や暗黙retryへ切り替えない。
- TLS証明書検証を無効化しない。リダイレクトは同一宛先も含めて拒否し、別host・HTTP downgrade・別portへ再送しない。
- ヘッダー、placeholder、リクエスト/エラー本文、画像をログや結果JSONへ出さない。

環境のproxy設定は変更しない。既定のNO_PROXY等で宛先が除外されている場合は、管理proxyの置換が使えない可能性がある。実環境での確認時に環境設定を確認する必要があり、このコードはそれを回避しない。

## キーなし検証の範囲

人工markerを環境変数へ与え、実urllib opener/ProxyHandler/ErrorProcessor/RedirectHandlerと偽HTTPS transportを組み合わせた。DNS/socket接続はテスト中に禁止する。

Bearerへ値がそのまま入ること、元HTTPS URLとproxy tunnel対象、環境proxyの大文字/小文字形式を検証する。301/302/303/307/308で別host・HTTP・別port・相対パスを返し、送信が1回で止まることも検証する。401/不正JSON/proxy失敗時の非表示、未設定/未承認時の停止も対象。

このテストはTLS接続、実Cloud proxyの置換、TypeSafeによる認証成功を実測しない。人工markerの表記は実際のCloud placeholderの形式を主張するものではない。

## 承認後に必要な実環境確認

PoC専用Cloud環境にNetwork secretのkeyとして`TYPESAFE_API_KEY`を要求し、Allowed domainsを`api.typesafe.ai`に限定する。同名の直接変数を追加せず、実値をチャット・コマンド引数・リポジトリへ記載しない。環境の保存・公開/再公開と、新しいtaskへの適用を確認する。

そのうえでキー供給と呼出回数/費用の承認を得て、権利確認済み画像による実リクエストを別途評価する。現在はこの設定・実行とも未着手。PR #6/#7の「proxy未対応」は「コード側互換性を検証、実環境未検証」へ読み替え、対応PRへのリンクを残す。
