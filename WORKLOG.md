# 作業記録

- 対象: 新規の JMeter_Cognito_Token_Codex。開始時は空。親ディレクトリーはGitリポジトリーではない。
- 認証方式: ユーザー回答によりCognito M2M client_credentialsで確定。
- 実APIのOpenAPI/認証設定は未提供。明示的なサンプルとローカル模擬サーバーで検証する。
- 計画: 公式仕様と既存実装を調査 → 解析・認証付きJMX生成と両OSの入口を実装 → 単体・JMeter実機結合試験 → 日本語Markdown/Excel → 差分と成果物の検査。
- 既存JMeter_Tools_Codexのローカル参照解決・データ生成・HTTPシリアライズ処理を独立したコピーとして再利用。元プロジェクトは変更しない。
- Windows PowerShell 5.1 / Python 3.12.10 / Java 17.0.11を確認。RHEL 9.8実機の検証は別途必要。
- 2026-10-01再開: 不足していた公開用JTL・log、最終検査レポート、配布ZIPの仕上げを実施。
- 再開時の37ファイルのハッシュと原本を.work/resume-baselineへ保持。処理ロジックの変更はなく、空白検査に合わせて余分な末尾空行だけを修正。README・検証説明を更新。
- 単体20件、compileall、pyflakes、PowerShell構文、ShellCheckを再確認。中断された結合試験は成功扱いにせず、改めて17ケースすべてを完了。
- output/verificationへ17組のJTL・logとsummary.jsonを収録。logはUTF-8・LFへ変換しローカル環境情報をマスク。原本・配布版のハッシュを区別して保持。
- サンプルを現行ソースから再生成し、Excelも再生成。27シートの全セル内容が既存版と一致することを確認。
- 最終機械検査で72テキストファイルのUTF-8・空白と資料内リンクを確認。JMeter読込APIで公開JTLの2件／6件を復元。
- 配布先はdist/JMeter_Cognito_Token_Codex_20261001.zip。対応する.zip.sha256とpackage-validation.jsonでZIPの整合性と展開後の生成確認を記録。
