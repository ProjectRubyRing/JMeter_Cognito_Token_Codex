# 実行済みの模擬試験結果

これらはJMeter 5.6.3と、この試験自身が起動したloopback HTTPサーバーによる結果です。AWSへ接続した証拠ではありません。

- default_one.jtl: token取得1件＋API1件。
- reuse_three.jtl: token取得1件＋API3件。
- renew_before_expiry.jtl: 模擬4秒トークンの再取得。
- all_five.jtl: サンプル定義の5APIと認証1件。
- その他のファイル: summary.jsonとVALIDATION.mdの異常系・並行実行確認。

同名.logは各実行のJMeter診断ログです。異常系は失敗サンプルが記録されることが正しい結果です。配布用ログは元の文字コードを判定してUTF-8・LFへ変換し、ローカルの絶対パス・IP・端末名をプレースホルダーへ置き換えています。loopbackのport番号は検証時の値です。JTLは実行結果をそのまま保存しています。

[summary.json](summary.json) に17ケースの成否、認証/API件数、runner終了コード、JTL・logのSHA-256を記録しています。元ログのSHA-256も保持し、配布時の加工を区別しています。

JMeterのView Results TreeのFilenameからJTLを選び、時間・HTTPコード・successを確認できます。応答本文や送信ヘッダーを記録しない設定のため、その欄が空なのは正常です。
