# 検証結果

確認日: 2026-10-01。対象はこの配布物の生成器・生成JMX・ローカル模擬HTTPサーバーです。実Cognito／実API Gatewayへのアクセスは実施していません。

## 実施環境

| 項目 | 確認した値 |
|---|---|
| OS / shell | Windows、Windows PowerShell 5.1.26100.9549 |
| Python | 3.12.10 |
| Java | 17.0.11 |
| JMeter | Apache JMeter 5.6.3。runnerでも版を検査 |
| ライブラリー | requirements.lock.txtと照合。Python要求下限も確認 |
| Bash静的解析 | ShellCheck 0.11.0 |

## 成功した検証

| 検証 | 結果 |
|---|---|
| 共通元実装の変更前テスト | 40件成功。最初の起動はcwd不一致でimport失敗し、正しいディレクトリーで再実行 |
| この配布物の単体テスト | 20件成功 |
| JMeter実通信の結合試験 | 17ケース成功。下表参照 |
| PowerShell生成入口 | YAMLからサンプルJMX一式の生成成功 |
| PowerShell実行入口 | 初期1件、2スレッド×2ループの両方で送信件数を検証 |
| JTLのJMeter読込 | SaveService.loadTestResults＋ResultCollectorHelperで2件／6件を復元 |
| Python compileall | 成功 |
| Python pyflakes | 問題なし |
| PowerShell構文解析 | 2スクリプトとも問題なし |
| ShellCheck | generate.sh / run.shとも問題なし |
| Excel構造 | 27シート、Meiryo UI、全使用セル折返し、モノトーン、印刷設定、5計算式 |
| 資格情報の保存検査 | 模擬secret、Basic値、発行tokenが各ケースのJMX・JTL・logにないことを確認 |
| 差分・空白検査 | 新規ファイルをgit diff --no-index --checkで検査。最終機械検査は[release-validation.json](docs/release-validation.json) |

JTLの読込確認はGUI Listenerが利用するJMeterの読込APIで行いました。GUI画面上でのマウス操作の確認とは区別します。

## JMeter結合試験

| ケース | 認証HTTP件数 | API件数 | runner終了コード |
|---|---:|---:|---:|
| default_one | 1 | 1 | 0 |
| reuse_three | 1 | 3 | 0 |
| renew_before_expiry | 3 | 3 | 0 |
| one_token_per_thread | 3 | 6 | 0 |
| all_five | 1 | 5 | 0 |
| explicit_each_request | 3 | 3 | 0 |
| oauth400 | 1 | 0 | 1 |
| oauth429_no_retry | 1 | 0 | 1 |
| missing_token | 1 | 0 | 1 |
| missing_expiry | 1 | 0 | 1 |
| wrong_type | 1 | 0 | 1 |
| zero_expiry | 1 | 0 | 1 |
| api401_no_replay | 1 | 1 | 1 |
| api403_no_replay | 1 | 1 | 1 |
| missing_secret | 0 | 0 | 1 |
| powershell_options | 2 | 4 | 0 |
| malformed_json | 1 | 0 | 1 |

終了コード1を期待する異常系も、期待どおり停止したことでテスト成功としています。missing_secretは内部設定エラーの1サンプルを記録し、HTTP送信はありません。期限更新のケースだけ、模擬サーバーが4秒のexpires_inを返します。Cognito実設定の値ではありません。

全APIケースでは、method、エンコード済みpath/query、JSON／フォーム本文、Bearer、X-API-KEYが期待値と一致することを模擬サーバーで確認しました。3スレッドのケースでは、各トークンが2回ずつ使われることも確認しました。

実行済みJTL・logとケース一覧は[output/verification](output/verification/README.md)にあります。

2026-10-01の再開作業で17ケースを通して再実行し、完了した実行の結果を収録しました。中断された実行の途中結果は配布物に含めていません。配布用logは元のCP932をUTF-8・LFへ変換し、ローカルの絶対パス・IP・端末名を伏せています。JTLは原本と同一です。summary.jsonには原本logと配布用logを区別したSHA-256も記録しています。

最終空白検査で見つかった末尾の余分な空行を取り除き、サンプルとExcelを再生成しました。Excelの27シートすべてでセル内容が修正前と一致することを確認しています。PowerShell生成入口は空白と日本語を含む出力パスでも確認しました。

## 未実施・環境上の制約

- 実Cognito／API Gateway: 実定義・資格情報がないため未実施。認可設定、実scope、実データ、DNS、TLS経路、WAF、クォータ、請求は実環境で確認が必要。
- RHEL 9.8実機: 未実施。WindowsのGit Bash起動はsignal pipeのWin32 error 5で失敗。Bash実行成功とは報告せず、ShellCheckと共通処理の検証を記録。
- PowerShell 7: 未実施。5.1で構文・実行を確認。
- Excel本体での表示・再計算: COM生成が0x80080005（CO_E_SERVER_EXEC_FAILURE）で失敗。openpyxlによる再読込・セル設定・式の構造検査を実施。実Excelでの目視・印刷確認は未実施。式はExcelが開いたときに再計算する設定。
- 新規venvへのpip導入: この管理環境ではensurepipの一時ディレクトリー作成後のアクセスがPermissionErrorで失敗。既存の検証済み同一固定版ライブラリーを作業用venvへコピーして検証。新規インストールがこの環境で成功したとは報告しない。配布ZIPにvenvは含めない。
- デスクトップの共有ルートと新規作業フォルダーはGitリポジトリーではないため、通常のgit status／git diffは使えない。ユーザー既存ファイルは変更せず、新規成果物をno-index diffで確認。commit/pushは実施していない。

## 再現コマンド

Windows:

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q apikit cognito_jmeter.py scripts\build_workbook.py tests
.\.venv\Scripts\python.exe -m pyflakes apikit cognito_jmeter.py scripts\build_workbook.py tests
.\.venv\Scripts\python.exe tests\integration_jmeter.py --jmeter-home 'C:\tools\apache-jmeter-5.6.3'
~~~

pyflakesを使う環境にはrequirements-dev.txtを導入します。結合試験の--only case_nameで対象を絞れます。Bashではpythonの場所を.venv/bin/pythonへ置き換えて同じテストを実行します。

JTL読込検査用Javaはtests/JtlLoadCheck.javaです。JMeterのlib/*とlib/ext/*をclasspathへ追加し、JMeterホーム、JTLパス、期待サンプル数を渡します。
