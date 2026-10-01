# Cognito M2M対応 OpenAPI → JMeter 5.6.3

OpenAPIのJSON/YAMLから、Cognitoでaccess tokenを取得し、API GatewayへBearerとX-API-KEYを渡すJMXを生成します。Windows 11用PowerShellとRHEL 9.8用Bashの入口を備えます。両者が共通Python処理を呼ぶため、フォルダー全体を配置してください。

- [詳しい解説（Excel）](docs/Cognito_JMeter_詳細ガイド.xlsx): Meiryo UI、モノトーン、27シート。
- [詳しい解説（Markdown）](docs/Cognito_JMeter_詳細ガイド.md): 約3.2万文字。判断基準・歴史・仕組み・設定・操作・限界を説明。
- [検証結果](VALIDATION.md): 実行した確認と未確認事項。
- [生成済みサンプルJMX](output/sample/scenario.jmx): localhost:8080、1スレッド、1ループ、有効API1件。
- [模擬通信のJTL・log](output/verification/README.md): GUIで読むための実行済みサンプル。
- [最終検査レポート](docs/release-validation.json): 同梱ファイル、試験結果、文字コード・空白検査。

配布用ZIPとSHA-256は作業フォルダーの `dist` に出力します。ZIPにはスクリプト・資料・サンプル・検証結果を含みます。Python、Java、JMeter本体は下記の手順に従って別途用意してください。

**実APIのOpenAPI・認証情報は未提供です。サンプルは実サービスの定義ではありません。AWS設定を作成・変更するツールではありません。**

## 設計

初期値は「token取得1回＋API1件」です。トークンはスレッドごとに再利用し、寿命の約71.25〜75%で次のAPI直前に再取得します。HTTP200だけでなく、access_token、expires_in、token_typeを検査します。認証失敗時は後続APIを呼ばず、そのスレッドを停止します。

専用の性能試験用M2Mアプリクライアントを基本にし、scope・authorizer・claims等は本番相当の条件へ合わせます。secretは環境変数から読み、JMXへ書きません。JTLはXMLで時間・ステータスを記録し、通常は本文・要求ヘッダーを保存しません。

公式情報確認日は2026-10-01です。M2Mクライアント料金項目の廃止、secret rotation、GetClientTokenの追加も解説に反映しています。本実装はHTTPS POSTの/oauth2/token方式です。

## 必要なもの

- Python 3.11以上とrequirements.lock.txtの依存関係。
- JMeter 5.6.3と対応Java。検証ではJava 17.0.11。
- WindowsはPowerShell 5.1以上。RHELはBash。
- 実行時はCognitoのM2M client ID / secret、token URL、カスタムscope。
- Meiryo UIは閲覧環境に導入済みであること。フォント自体は非同梱。

## Windows

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\scripts\Generate-CognitoJmx.ps1 -InputFile '.\examples\openapi.sample.yaml' -OutputDirectory '.\output\my-api' -Seed 42
~~~

認証設定と実行例です。URL・ID・scopeを実環境の値へ置き換えます。

~~~powershell
$env:COGNITO_TOKEN_URL = 'https://YOUR_DOMAIN.auth.ap-northeast-1.amazoncognito.com/oauth2/token'
$env:COGNITO_CLIENT_ID = 'YOUR_CLIENT_ID'
$env:COGNITO_SCOPE = 'demo-api/read'
$taskSecret = Read-Host 'Cognito client secret' -AsSecureString
$env:COGNITO_CLIENT_SECRET = [System.Net.NetworkCredential]::new('', $taskSecret).Password
.\scripts\Run-JMeter.ps1 -Scenario '.\output\my-api\scenario.jmx' -JMeterHome 'C:\tools\apache-jmeter-5.6.3' -ResultsDirectory '.\results\my-api'
Remove-Item Env:COGNITO_CLIENT_SECRET -ErrorAction SilentlyContinue
~~~

必要な実APIキーはAPI_KEY環境変数へ設定します。未設定ではXXXXXXXXXXXXです。secretはプロセス環境では平文です。実行ポリシーは組織の方針に従ってください。スクリプトは永続的なポリシー変更を行いません。

## RHEL 9.8

~~~bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
bash scripts/generate.sh --input ./examples/openapi.sample.yaml --out ./output/my-api --seed 42

export COGNITO_TOKEN_URL='https://YOUR_DOMAIN.auth.ap-northeast-1.amazoncognito.com/oauth2/token'
export COGNITO_CLIENT_ID='YOUR_CLIENT_ID'
export COGNITO_SCOPE='demo-api/read'
read -r -s -p 'Cognito client secret: ' COGNITO_CLIENT_SECRET
printf '\n'
export COGNITO_CLIENT_SECRET
bash scripts/run.sh --scenario ./output/my-api/scenario.jmx \
  --jmeter-home /opt/apache-jmeter-5.6.3 --results ./results/my-api
unset COGNITO_CLIENT_SECRET API_KEY
~~~

RHEL 9.8実機は未検証です。ShellCheckと共通Python/JMeter処理のWindows上での検証を実施しています。

## 主な生成オプション

| 目的 | PowerShell | Bash |
|---|---|---|
| 対象API | -Operation listItems | --operation listItems |
| 全API有効 | -EnableAll | --enable-all |
| 送信先 | -Server localhost -Port 8080 -Protocol http | --host localhost --port 8080 --protocol http |
| ステージ等 | -BasePath /test | --base-path /test |
| 手動データ | -DataFile my-data.json | --data-file my-data.json |
| 再現性 | -Seed 42 | --seed 42 |

初期状態では最初のGET/HEADを選び、なければ先頭operationを選びます。生成後にmanifest.jsonで確認してください。出力は新規または空のディレクトリーに限ります。既存ファイルは上書きしません。

OpenAPIのserversとtokenUrlは自動で実送信先へ採用しません。CognitoはCOGNITO_TOKEN_URLで明示し、API Gatewayを呼ぶ場合は通常https / 443 / 実ホスト / 必要なbase-pathを指定します。

生成値は生成時にランダムに決まり、同じJMXの実行中は固定です。実在IDや業務上の関係は生成できないため、test-data.jsonまたは手動データで調整します。OpenAPIの型だけでAPIの業務成功を保証しません。

## 実行時の設定

PowerShellでは -Threads、-Loops、-RampSeconds、-ThinkTimeMs、-TimeoutSeconds が使えます。Bashでは --threads、--loops、--ramp-seconds、--think-time-ms、--timeout です。詳細はガイド18章を参照してください。

追加の非秘密設定は -PropertiesFile / --properties で読みます。例は [runtime.properties.example](examples/runtime.properties.example) です。環境変数はJMeter起動前に設定します。既に起動したGUIや遠隔エンジンへ自動伝搬しません。

runnerの終了コードは、0=成功、1=実行・サンプル失敗またはタイムアウト、2=入力・設定エラーです。認証設定をJMX内のGroovyが検査して失敗した場合は、サンプル失敗なので1です。Javaの終了コードだけでなく、JTLの失敗とAPI件数も確認します。

## JTLをGUIで読む

JMeterのTest PlanへView Results Treeを追加し、FilenameからJTLを選びます。再実行は不要です。通常の保存設定ではResponse dataは空で、時間・HTTPコード・成否を確認できます。JTLはJMXのSimple Data Writerが書くため、runnerと同じファイルへ-lを追加しません。

## 対応範囲と注意

OpenAPI3.0.x/3.1.x、JSON/YAML、内部$ref、基本型・主な制約、JSON/フォーム等の本文に対応します。Swagger2、OpenAPI3.2、外部$ref、XML本文、SigV4、ユーザーログイン等は対象外です。対応外機能を黙って落とさずエラーにします。詳細はガイド23章を参照してください。

JMeterの標準HTTP Samplerはサーバー証明書を広く受け入れます。本ツールのHTTPS URL制限を、厳格な証明書検証の保証とは扱わないでください。tokenのメモリー共有、API間のID相関、業務Assertion等も対象に合わせた追加設計が必要です。

## 再検証・資料再生成

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tests\integration_jmeter.py --jmeter-home 'C:\tools\apache-jmeter-5.6.3'
.\.venv\Scripts\python.exe scripts\build_workbook.py
~~~

Linuxでも.venv/bin/pythonで同じPythonスクリプトを実行できます。scripts/build_workbook.pyはdocsのMarkdownからExcelと出典JSONを再生成し、フォント・折返し・計算式を検査します。
