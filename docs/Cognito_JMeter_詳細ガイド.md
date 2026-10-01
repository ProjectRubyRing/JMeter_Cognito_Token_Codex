# Cognito M2M × API Gateway × JMeter 5.6.3 詳細実装ガイド

公式情報の確認日: 2026-10-01。対象: CognitoユーザープールのOAuth 2.0 client_credentials、JMeter 5.6.3、Windows 11、RHEL 9.8。M2Mを前提とすることは依頼者に確認済みです。実サービスのOpenAPI・Cognito設定・認証情報は未提供のため、添付のサンプル定義とローカル模擬サーバーを使って検証します。実AWS環境での成功を保証した資料ではありません。

本書で「推奨」と書く設定のうち、1スレッド、1ループ、タイムアウト、停止方針などは、この配布物の少量動作確認用の設計判断です。あらゆる本番システムに最適な性能試験値という意味ではありません。AWS公式の推奨と、この配布物の設計判断を区別して説明します。

## 01 結論と最初に選ぶ構成

**基本方針は、性能試験専用のCognitoアプリクライアントを作り、既存システムと同じ認可条件を再現し、トークンを有効期間の途中まで再利用することです。** 性能試験のためだけに、現用クライアントのフロー・scope・有効期間を変更することは通常避けます。専用クライアントの利用は設計上の推奨であり、AWSが一律に強制している制約ではありません。

| 質問 | 回答 | 理由 |
|---|---|---|
| 性能試験専用のclient ID / secretを用意するか | 原則として用意する | 試験の設定変更、秘密の配布、ローテーション、識別を分けられる |
| 現用クライアントを調整するか | 既存の試験専用クライアントなら再利用を検討。現用アプリの設定を試験都合で変えない | 認可条件と既存アプリへの影響を混ぜない |
| APIのたびにトークンを取得するか | 通常のAPI性能試験では取得しない | 認証サーバーの待ち時間や制限をAPI性能と混同するため |
| トークンを使い回せるか | 可能。各スレッドのJMeter変数へ保存する | 期限確認の後、同じ文字列をAuthorizationヘッダーに入れられる |
| 正規表現抽出器を使うか | 利用可能だが、JSON Extractorを採用する | JSONのプロパティを名前で読み取る方が明確 |
| 更新にrefresh_tokenを使うか | client_credentialsでは使わない | 同じclient ID / secretで、新しいaccess_tokenを発行してもらう |
| 最初に何件送るか | 認証1件＋選択したAPI1件 | APIを1件確認する設定であり、総HTTP通信が1件という意味ではない |
| 全APIは生成されるか | 対応可能な全operationを収録する | 初期状態ではGET/HEADの最初の1件、なければ先頭1件だけ有効 |

Cognitoのclient_credentialsは人がログインするフローとは異なり、access_tokenのみを返します。[AWS公式：token endpoint](https://docs.aws.amazon.com/cognito/latest/developerguide/token-endpoint.html)

**最新情報の訂正:** 2025年11月3日、M2Mのアプリクライアント単位の料金項目は廃止されました。現在の判断で「専用クライアントを1つ作るたびに従来の固定料金が増える」と説明するのは不正確です。成功したM2Mトークンリクエスト数に対する課金は残ります。古い解説やDeveloper Guideの一部と食い違う場合は、この変更告知と現行料金表を優先します。[AWSの料金変更告知](https://aws.amazon.com/about-aws/whats-new/2025/11/amazon-cognito-removes-machine-machine-app-client-price-dimension/)、[現行料金表](https://aws.amazon.com/cognito/pricing/)

## 02 小学生向けのたとえで全体をつかむ

図書館を想像してください。JMeterは「本を借りに行く係」、APIは「本を出してくれる窓口」、API Gatewayは「入口の受付」、Cognitoは「期限つき入場券を発行する係」です。

係の名前がclient ID、係だけが知っている合言葉がclient secretです。合言葉は入場券をもらうときだけCognitoへ見せます。APIの窓口に毎回合言葉を配る代わりに、有効な入場券を提示します。券に「読むだけ」と書いてあれば、読書室に入れても本の登録室には入れません。この「できること」の名前がscopeです。

入場券はコピーできる文字列です。「Bearer」は、その券を持っている者が使える方式という意味です。誰かに盗まれると、その人が期限まで使えるおそれがあるため、券の全文をログへ書かないことが大切です。署名つきでも、暗号化されて中身が読めなくなっているとは限りません。[Bearerの標準仕様](https://www.rfc-editor.org/rfc/rfc6750)、[JWTの標準仕様](https://www.rfc-editor.org/rfc/rfc7519.html)

| 実際の言葉 | 図書館のたとえ | 誤解してはいけない点 |
|---|---|---|
| client ID | 係の識別番号 | それだけで認証はできない |
| client secret | 係が管理する合言葉 | AWSアクセスキーとは別物 |
| access_token | 期限つきの入場券 | APIへ渡すのはこちら |
| scope | 入れる部屋・できる仕事 | 名前が似ていても完全一致が必要 |
| expires_in | 券が何秒有効か | 毎回同じ秒数と決めつけない |
| API Gateway authorizer | 券を調べる受付 | 発行元、署名、期限、必要なscopeなどを確認 |
| X-API-KEY | 利用を集計する会員番号に近いもの | Bearer認証の代わりにはならない |
| JMeterスレッド | 同時に動く係1人 | アプリクライアント1個と同じ数とは限らない |
| JTL | 実験の記録帳 | 拡張子だけではCSV/XMLの違いは決まらない |
| log | 実験道具の診断日誌 | APIの成績表そのものではない |

入口の会員番号が合っていても、入場券がなければ入れない設計にできます。したがって、APIリクエストにはAuthorizationとX-API-KEYの両方が必要になる場合があります。APIキーを認証・認可の代わりに使わないというAWSの説明とも一致します。[API GatewayのAPIキーと利用量プラン](https://docs.aws.amazon.com/apigateway/latest/developerguide/api-gateway-api-usage-plans.html)

## 03 どうしてこの仕組みが生まれたのか

昔から「別のアプリに仕事を頼みたいが、長く使えるパスワードを渡し続けたくない」という課題がありました。認可サーバーが用途と期限を持つトークンを発行し、API側がそれを確認する仕組みは、資格情報の直接配布を減らすために役立ちます。これは背景の説明であり、すべてのシステムが同じ順序で発展したという意味ではありません。

| 年・時期 | 標準・製品の変化 | 今回の試験へのつながり |
|---|---|---|
| Webサービスの拡大 | 手動で少数アクセスするだけでは同時利用時の遅さを見つけにくくなった | JMeterのような負荷生成・測定ツールが必要になる |
| 2012年10月 | OAuth 2.0のRFC 6749、Bearer利用のRFC 6750が公開 | client_credentialsとAuthorization: Bearerの共通ルール |
| 2014年2月 | OpenID Connectが標準化 | 「誰がログインしたか」を表すIDトークンと、アクセス権を区別する |
| 2014年7月 | Amazon Cognitoが登場 | 当初のIdentity・同期機能と、現在のUser Poolsを混同しない |
| 2015年5月 | JWTのRFC 7519が公開 | 小さな署名つきの情報をトークンとして運べる |
| 2017年8月 | Cognito User PoolsのOAuth機能拡張でClient Credentialsとカスタムscopeが案内された | 人が画面でログインしなくても機械の権限を発行できる |
| 2025年11月3日 | M2Mアプリクライアント料金項目を廃止 | 専用クライアントの判断に古い料金情報を使わない |
| 2026年2月26日の告知 | client secretのローテーションとカスタムsecret | 1クライアントで最大2つの有効secretを並存して移行できる |
| 2026年8月31日 | GetClientTokenを発表 | ユーザープールドメイン不要でM2Mトークンを取得する選択肢が追加された |

歴史の根拠: [OAuth 2.0](https://www.rfc-editor.org/rfc/rfc6749)、[OIDC発表](https://openid.net/the-openid-foundation-launches-the-openid-connect-standard/)、[Cognito登場](https://aws.amazon.com/about-aws/whats-new/2014/07/10/introducing-amazon-cognito/)、[JWT](https://www.rfc-editor.org/rfc/rfc7519.html)、[2017年の機能拡張](https://aws.amazon.com/about-aws/whats-new/2017/08/amazon-cognito-launches-general-availability-of-a-built-in-customizable-user-experience-for-sign-in-oauth-2-0-support-and-federation-with-facebook-login-with-amazon-google-and-saml-providers-for-user-pools/)、[secret管理の更新](https://aws.amazon.com/about-aws/whats-new/2026/02/amazon-cognito-client-secret-lifecycle/)、[GetClientToken発表](https://aws.amazon.com/about-aws/whats-new/2026/08/amazon-cognito-get-client-token/)

今回の実装は、長く使われているHTTPS POSTの `/oauth2/token` を使います。GetClientTokenは別のAPI契約・設定です。本ツールが自動的にそちらへ切り替わることはありません。「最新機能がある」ことと、「今回その方式を実装した」ことを分けて考えてください。

JMeterは、もともとWebアプリケーションのテスト向けに設計され、その後さまざまなプロトコルへ広がったJava製のツールです。ブラウザーそのものではなく、HTTPなどの通信を組み立てて送ります。そのため、JavaScriptでログイン画面を操作する代わりに、今回のM2MではOAuthのHTTPリクエストを直接送れます。[JMeter公式の沿革と役割](https://jmeter.apache.org/)

OpenAPIはSmartBearから寄贈されたSwagger仕様を基に、Linux Foundation配下のOpenAPI Initiativeで標準化が進められたAPI記述形式です。「どのURLに、どの型のデータを送るか」を人と機械で共有できるため、本ツールのような自動生成へつながります。ただし、APIの説明書であることと、業務のテスト手順まで全部書いてあることは同じではありません。[OpenAPI Initiativeの説明](https://www.openapis.org/about)

## 04 専用クライアントと既存クライアントの比較

「クライアントクレデンシャルを登録する」という作業は、通常、Cognitoユーザープールにアプリクライアントを作り、client IDとclient secret、許可フロー、scopeなどを設定することを指します。JMeterという名前の特別な種類のCognitoクライアントが存在するわけではありません。

| 比較項目 | 性能試験専用クライアント | 現用クライアントを流用・変更 |
|---|---|---|
| 秘密の配布 | 試験用secretだけを実行環境へ渡せる | 現用secretの取扱者・保管先が増える |
| scope・期限の変更 | 試験用設定として管理しやすい | 現用アプリにも影響する可能性 |
| client_idの一致 | 別IDになるため認可設定を合わせる必要がある | 現用ID依存の振る舞いをそのまま確認できる |
| 記録の識別 | client_idで試験通信を識別しやすい | 通常通信と試験通信の識別が難しくなる |
| ローテーション | 試験日程に合わせて管理しやすい | アプリ側の切替調整が必要 |
| サービス制限の隔離 | 完全には隔離されない | 同様に他の通信と共有する制限がある |
| 推奨用途 | 通常のAPI性能試験・継続的な試験 | 現用IDそのものを確認する限定試験、既存の試験専用クライアント |

Cognitoは1ユーザープールに複数のアプリクライアントを設定できます。[アプリクライアントの公式説明](https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-settings-client-apps.html)

専用クライアントでも、scope、トークン有効期間、Lambdaトリガー、authorizer、バックエンドの権限マッピングが本番と大きく違うと、本番を代表する測定になりません。「認証を通すためにscopeを全部許可」「authorizerを外す」「現用より極端に長寿命にする」は、試験条件を変えてしまいます。

HTTP APIのJWT authorizerが現用client IDだけをaudienceとして許可している場合、試験用IDが通るように試験環境のauthorizer側も整合させます。バックエンドがclient_idで利用者・テナントを判定していれば、そのマッピングも必要です。これらのAWS設定変更は、本配布スクリプトでは実行しません。

クライアントを分けても、アカウント・リージョン・ユーザープール・ドメイン等の範囲で共有する制限は残り得ます。負荷上限は、実際の構成と最新のService Quotasを確認します。[Cognitoのクォータ](https://docs.aws.amazon.com/cognito/latest/developerguide/quotas.html)

## 05 Cognito側で準備するもの

下表の値は例です。`demo-api/read` や `YOUR_CLIENT_ID` をそのまま本物の資格情報として使うことはできません。sampleの `example.invalid` も、意図的に接続先にできない説明用ドメインです。

| 順番 | 準備項目 | 具体的な意味 |
|---|---|---|
| 1 | 試験対象のユーザープール | API側が信頼する発行元と一致させる |
| 2 | リソースサーバー | 例: 識別子demo-api。API用のscopeを定義する入れ物 |
| 3 | カスタムscope | 例: read、write。リクエストではdemo-api/readの形で指定 |
| 4 | 試験専用アプリクライアント | 例: perf-test-m2m。secretを安全に保持できるconfidential client |
| 5 | client_credentialsを許可 | このOAuthフロー用の設定を行う。ユーザーログイン用フローと混同しない |
| 6 | 許可scopeを関連付ける | トークンで要求するscopeを、クライアントにも許可する |
| 7 | User Pool domain | 今回採用した/oauth2/token方式で必要。issuerとは別のURL |
| 8 | access token有効期間 | 本番を再現する値を使い、コードに3600秒を固定しない |
| 9 | ID・secretを試験実行環境へ注入 | JMX、Git、コマンド引数へsecretを埋め込まない |
| 10 | API側のauthorizer・scope・APIキー | トークンが取れるだけではAPIの認可成功にはならない |

アプリクライアントのAllowedOAuthFlowsでclient_credentialsを有効にするときは、そのOAuthフローだけを指定します。現用のauthorization_code等の設定へ、試験のために単純に追加する考え方は適切ではありません。これも専用クライアントを勧める理由です。[CreateUserPoolClientのフロー制約](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_CreateUserPoolClient.html)

M2Mトークンで使うのはカスタムscopeです。一般ユーザー情報用のopenid、email、profileを、そのままM2Mの権限として要求する構成ではありません。今回のエンドポイント方式ではUser Pool domainが必要ですが、最新のGetClientToken方式では不要です。[M2Mとリソースサーバー](https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pools-define-resource-servers.html)

有効期間の設定範囲は、Cognito access tokenでは5分〜1日です。ローカル結合試験では更新処理を短時間で試すために、模擬サーバーだけで4秒を使っています。実Cognitoに4秒を設定する例ではありません。[access tokenの有効期間](https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-the-access-token.html)

コンソールのメニュー名は更新されることがあります。「Machine-to-machineのアプリクライアント」「client secretあり」「client_credentials」「カスタムscope」「ドメイン」「トークン有効期間」という設定の意味で確認してください。管理APIやIaCで作る場合も、これらの意味を保ちます。

## 06 API GatewayのREST APIとHTTP APIの違い

| 項目 | REST API | HTTP API |
|---|---|---|
| Cognito連携の代表 | COGNITO_USER_POOLS authorizer | JWT authorizer |
| access tokenでの認可 | メソッドにAuthorization Scopesを設定 | ルートにauthorizationScopesを設定 |
| 発行元 | authorizerで指定したユーザープール | issuerを設定 |
| 対象クライアントの扱い | HTTP APIのaudience設定と同じと考えない | audがなければclient_idをaudienceと比較 |
| X-API-KEY | Usage Plan・API key requiredと関連 | REST APIと同じ組込みAPIキー機能はない |
| 設定反映 | 対象ステージへのデプロイも確認 | ルート・ステージの反映設定を確認 |

REST APIでは、scopeを指定しない構成と、scopeを指定してaccess tokenを使う構成で扱いが異なります。今回のM2M access tokenを使うメソッドには必要なscopeを設定してください。IDトークンのaudを検査するためのToken validation正規表現を、M2M access tokenへ安易に流用すると失敗原因になります。[REST APIのCognito authorizer](https://docs.aws.amazon.com/apigateway/latest/developerguide/apigateway-enable-cognito-user-pool.html)

HTTP APIではJWT authorizerが署名、issuer、audまたはclient_id、exp等を検査します。audが存在すればaudが優先されます。「Cognito access tokenには絶対にaudがない」と一般化しないでください。ユーザー向けフローではresource bindingによるaudもあるため、使用フローと実際のトークン契約を確認します。ルートに複数scopeを設定したときの組込み判定は少なくとも1つの一致であり、全部のAND条件を期待するなら別途認可設計が必要です。[HTTP API JWT authorizer](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html)

X-API-KEYのヘッダー名は指定どおり固定で生成します。初期値XXXXXXXXXXXXは説明用です。REST APIでAPI key requiredが有効なら、有効な試験用APIキーをAPI_KEY環境変数へ設定します。ヘッダーを付けただけでUsage Planとの関連付けが作られるわけではありません。

## 07 実際のHTTP通信を順番に見る

初回は「認証」と「API」の2回の通信です。以後、再利用中はAPIの通信だけになります。

~~~text
JMeterのスレッド
  1. メモリー内のaccess_tokenと更新時刻を確認
  2. 初回なのでCognitoへPOST /oauth2/token
  3. JSONのaccess_token / expires_in / token_typeを取り出す
  4. 期限から更新時刻を計算してメモリーに保存
  5. Authorization: Bearer <access_token> を付けてAPI Gatewayへ
  6. API Gatewayが認可し、バックエンドAPIへ
  7. 応答時間・HTTPコード・成功/失敗をJTLへ保存
次のAPI
  8. 更新時刻より前なら、手順5へ
  9. 更新時刻を過ぎていれば、手順2から新しい券をもらう
~~~

トークン取得リクエストの概念例です。Basicの値はclient IDとsecretから実行時に作ります。これは単なるBase64表現であり暗号化ではないため、HTTPSが必要です。client_credentialsでSECRET_HASHやAWS SigV4署名を付ける処理ではありません。

~~~http
POST /oauth2/token HTTP/1.1
Host: <domain>.auth.<region>.amazoncognito.com
Authorization: Basic <実行時に作るBase64値>
Content-Type: application/x-www-form-urlencoded
Accept: application/json

grant_type=client_credentials&scope=demo-api%2Fread+demo-api%2Fwrite
~~~

フォーム内の空白は+または%20等に変換されます。JMeterのHTTPArgumentでURL Encodeを有効にするため、scope文字列を手作業で二重エンコードしません。スクリプトはBasicの各要素もOAuthの規則に従ってフォームエンコードしてから結合します。Cognitoが通常発行する英数字の資格情報では、そのまま結合した場合と同じになります。

~~~json
{
  "access_token": "<実際には長いトークン>",
  "expires_in": 3600,
  "token_type": "Bearer"
}
~~~

APIリクエストではclient secretを送信しません。Cognito向けのBasicヘッダーと、API向けのBearerヘッダーは、別々のHTTP Header Managerを各サンプラーの直下へ配置します。Thread Group全体に両方の認証ヘッダーを置くと混ざるため、生成器はその配置を避けています。

~~~http
GET /items?limit=3 HTTP/1.1
Host: localhost:8080
Authorization: Bearer <取り出したaccess_token>
X-API-KEY: XXXXXXXXXXXX
~~~

## 08 毎回取得と再利用を数字で比べる

性能試験でAPIの速さを知りたいとき、毎回トークンを取得すると「Cognitoの待ち時間＋APIの待ち時間」が混ざります。また、認証側が429を返すとAPIに到達する数が減り、「APIが余裕で動く」という誤った見え方になる可能性があります。

| 試験目的 | トークンの扱い | 結果の分け方 |
|---|---|---|
| 業務APIの定常性能 | 本番の取得・再利用方法を再現する | APIラベルを中心に集計。認証は別ラベル |
| 起動時の混雑 | 最初の取得バーストも含める | 立上げ時と定常時を分ける |
| Cognito発行性能 | 計画した発行頻度で取得する | API試験と別シナリオで実施 |
| 完全な業務体験 | 実アプリと同じタイミングで取得する | E2E時間と個別API時間を両方測る |
| 少量疎通確認 | 初回に1回取得してAPI1件 | 本配布物の初期設定 |

AWSはトークンの寿命の約75%を使ってから再取得する考え方を案内しています。本実装はこれを初期値0.75にし、最大5%だけ更新時刻を早めるランダムな分散を加えています。厳密に期限切れまで使う設計ではありません。[AWSのトークンキャッシュ推奨](https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-tokens-caching-tokens.html)

例えば100スレッドが合計100 APIリクエスト/秒を1時間続け、トークン寿命3600秒、再利用率75%とします。APIは360,000件です。毎回取得なら概ね360,000回の発行、スレッドごとの再利用なら開始時100回＋約45分後100回＝約200回の発行が目安になります。HTTPエラー・開始時刻のずれ・停止・実際の呼出頻度は無視した説明用の概算です。

頻繁にAPIを呼ぶ場合、キャッシュ数をC、寿命をL秒、再利用率をrとすると、定常時の発行頻度の目安は `C / (L × r)` 回/秒です。開始時のC回は別に発生します。開始直後からこの平均値へ平らになるわけではありません。APIを呼ばない間は更新処理も動かないため、実装は時間だけで定期発行するバックグラウンドタイマーではありません。

料金はリージョン・機能等によって確認が必要です。金額を固定した表より、「成功したトークン発行件数」「APIリクエスト件数」「試験時間」を別々に見積もることが再利用できます。[現行Cognito料金](https://aws.amazon.com/cognito/pricing/)

## 09 再利用の寿命計算と動作原理

この実装では、壁時計の時刻ではなく `System.nanoTime()` で経過時間を比較します。これは「何時何分か」を表示する時計ではなく、「前の処理からどれだけ時間がたったか」を測るための時計です。OS時刻の補正で、再利用期限が突然前後する影響を抑えます。API Gatewayがexpを判定するときはUTCの時刻を使うため、実行機やサービスの時刻同期が不要になるわけではありません。

~~~text
S = トークン取得を始めた時刻（nanoTime）
L = 応答のexpires_in（秒）
r = token_reuse_ratio（初期値0.75）
j = 0以上、token_jitter_fraction未満の乱数（初期上限0.05）
m = min(token_safety_seconds, L × 0.10)（初期設定30秒）

使用する秒数 = min(L × r × (1 - j), L - m)
更新時刻 = S + 使用する秒数
API直前に 現在時刻 >= 更新時刻 なら再取得
~~~

寿命3600秒なら、初期設定の更新時刻は取得開始から約2565〜2700秒、つまり約42分45秒〜45分です。応答を受け取った時刻ではなく、取得を始めた時刻から数えるため、認証の通信時間も使用可能時間から差し引く形になります。30秒の安全余裕はさらに30秒を無条件に二重に引く実装ではなく、上のmin式で選択します。

HTTP200でも、access_tokenが空、expires_inが欠落・0・不正、token_typeがBearerでない、認証応答が更新期限より遅れて届く場合は失敗です。古いトークンを消してから取得するため、失敗時に古い値で続行しません。

JWTのpayloadにあるexpをJMeterで読んでも、それだけで署名検証したことにはなりません。本実装はOAuth応答のexpires_inをキャッシュ制御へ使い、APIへのアクセス可否はAPI Gatewayのauthorizerへ委ねます。JMeterが独立したJWT検証器になる実装ではありません。

## 10 スレッド・JVM・分散試験での共有範囲

| 入れ物 | 見える範囲 | 今回の用途 |
|---|---|---|
| vars | 同じJMeterスレッド内 | access_token、更新時刻、抽出した一時値 |
| props | 同じJMeter JVM内で共通 | 再利用率などの非秘密の実行設定 |
| 環境変数 | 起動したプロセスとその子が受け取る | client ID、secret、scope、token URL、API key |
| JMXファイル | ファイルを開く利用者 | 構造とスクリプト。実secretは保存しない |
| 複数JMeterエンジン | メモリーは別々 | 自動でトークンを共有しない |

1つのclient IDを100スレッドが使っても、今回の実装では100個のキャッシュができます。「同じ資格情報を使う」と「同じ発行済みトークンを共有する」は違います。最初に100回の発行が発生するため、Ramp-upを短くしすぎるとCognitoへ集中します。更新jitterは更新時刻を分散しますが、初回の取得集中をなくす機能ではありません。

本番が「アプリの1プロセスで1つのトークンを共有する」なら、スレッド単位キャッシュは本番より発行回数が多くなります。業務API性能だけを測る場合は、事前取得やJVM単位共有を検討できます。ただし、共有する実装には更新ロック、client IDとscopeを含むキャッシュキー、二重発行防止、異なる資格情報の混同防止が必要です。今回の生成器はスレッド単位を実装し、共有キャッシュ機能を実装済みとは扱いません。

JMeter分散実行では、コントローラーの環境変数が遠隔エンジンへ自動配布されるわけではありません。各エンジンを起動する前に、各機械へ資格情報と必要な設定を用意します。JMXにはGroovyを埋め込んでいるためruntimeフォルダーを実行先へ配る必要はありませんが、資格情報・証明書・ネットワーク到達性は各機械に必要です。

## 11 正規表現抽出とJSON Extractorを実画面で設定する

生成JMXではCognitoのHTTP Requestの子にJSON Extractorを置き、その後ろに検証・キャッシュ・秘匿化用のJSR223 PostProcessorを置きます。順序を逆にすると、秘匿化後の文字列をJSONとして読もうとして失敗します。

| JSON Extractorの欄 | 設定値 |
|---|---|
| Names of created variables | raw_access_token;raw_expires_in;raw_token_type |
| JSON Path expressions | $.access_token;$.expires_in;$.token_type |
| Match No. | 1;1;1 |
| Default Values | __MISSING__;__MISSING__;__MISSING__ |
| Compute concatenation var | 無効 |
| Apply to | Main sample。リダイレクトを追わないため主応答を対象 |

取得直後の値をraw_付きで保持し、妥当な場合だけaccess_tokenへ移します。APIのHTTP Header Managerには `Authorization` と `Bearer ${access_token}` を設定します。「Bearer」の後ろは半角空白1個です。トークンをURLのqueryへ入れる必要はありません。

正規表現を学ぶための代替設定は以下です。これは生成器の既定動作ではありません。APIへ渡す前に、同等のHTTPコード・期限・token_typeの検査が必要です。

| Regular Expression Extractorの欄 | 設定例 |
|---|---|
| Field to check | Body |
| Reference Name | raw_access_token |
| Regular Expression | "access_token"\s*:\s*"([^"]+)" |
| Template | $1$ |
| Match No. | 1 |
| Default Value | __MISSING__ |

正規表現の `\s*` は「空白や改行が0個以上」、`([^"]+)` は「次のダブルクォートまでを1番目のまとまりとして取り出す」という意味です。応答が単純なJWT文字列なら通常抽出できますが、JSONのエスケープ、同名キー、構造変化を文字列の規則だけで扱うことになります。JSON ExtractorはJSONとして解析し、プロパティ名で指定できます。[JMeterコンポーネントリファレンス](https://jmeter.apache.org/usermanual/component_reference.html)

失敗時の既定値を空欄のままにすると、未取得と空文字の区別が曖昧になります。本実装は専用マーカーを使い、前回値もクリアします。「HTTP200だったから成功」と「正しいトークンが取れたから成功」は分けて確認します。



## 12 生成JMXの木構造と各部品の責任

~~~text
Test Plan
├─ User Defined Variables: host / port / protocol / api_key
├─ Thread Group: threads=1 / ramp_seconds=1 / loops=1
│  ├─ HTTP Request Defaults
│  ├─ Simple Controller: GET listItems（初期状態ではこの1件だけ有効）
│  │  ├─ JSR223 Sampler: 設定読込・待機・更新要否の判定
│  │  ├─ If Controller: token_needed が true のとき
│  │  │  └─ HTTP Request: AUTH Cognito /oauth2/token
│  │  │     ├─ HTTP Header Manager: Basic、フォーム形式
│  │  │     ├─ JSON Extractor
│  │  │     └─ JSR223 PostProcessor: 検査・保存・応答秘匿化
│  │  └─ HTTP Request: API GET listItems
│  │     ├─ HTTP Header Manager: Bearer、X-API-KEY
│  │     ├─ JSR223 PostProcessor: 送信ヘッダー情報を消去
│  │     └─ Response Assertion: OpenAPIに基づくHTTPコード
│  └─ 他のoperationのSimple Controller（初期状態は無効）
└─ Simple Data Writer: 結果をXML JTLへ保存
~~~

GroovyコードはJMX内に埋め込まれています。JMXの横にGroovyファイルを置く必要はありません。配布物のruntimeフォルダーは、生成器の保守と再生成用ソースです。

| 部品 | 担当する仕事 | 失敗時の動作 |
|---|---|---|
| Prepare / check token | 環境変数・通信先・数値の確認、必要なら取得準備 | CONFIG_ERRORを記録しスレッド停止 |
| If Controller | 更新の必要性で分岐 | falseなら認証HTTPを実行しない |
| AUTH HTTP Request | CognitoにPOST | 応答を後処理で検査 |
| JSON Extractor | 3つの値を抽出 | 欠落なら専用マーカー |
| token_result.groovy | 妥当性検査、期限計算、token保存、秘匿化 | token削除、失敗記録、停止 |
| API HTTP Request | 定義されたpath/query/header/bodyで送信 | HTTPコードをAssertionで検査 |
| Simple Data Writer | HTTP結果を保存 | runnerがJTLの有無と整合性も確認 |

Prepare成功時の内部サンプルは `SampleResult.setIgnore()` で集計から外します。設定エラー時は失敗サンプルとして記録します。APIと認証のラベルは `API ` と `AUTH ` で始まるため、集計時に区別できます。

Groovyのコンパイルキャッシュを有効にしています。動的な値は `vars.get(...)` や `props.getProperty(...)` で読み、スクリプト文字列そのものへ展開値を埋め込みません。[JMeterの推奨事項](https://jmeter.apache.org/usermanual/best-practices.html)

## 13 配布物一覧と初期値

スクリプト1本だけを抜き出さず、配布フォルダー全体を配置してください。BashとPowerShellは共通のPython実装を呼び出します。同じ入力とseedからOS間で同じデータ・JMX構造を作るためです。Pythonなしで動く単体スクリプトではありません。

| ファイル・場所 | 役割 | 用途 |
|---|---|---|
| scripts/generate.sh | RHELの生成入口 | OpenAPIからJMX作成 |
| scripts/Generate-CognitoJmx.ps1 | Windowsの生成入口 | 同上 |
| scripts/run.sh | RHELの実行入口 | JMeter起動・結果合否確認 |
| scripts/Run-JMeter.ps1 | Windowsの実行入口 | 同上 |
| cognito_jmeter.py | 共通CLI | 引数、出力、実行・結果検査 |
| apikit/spec.py | 定義読込・検証・データ生成 | 自動実行 |
| apikit/jmx.py | HTTPデータの直列化 | 自動実行 |
| apikit/plan.py、runtime/ | 認証付きJMX構築 | 自動実行 |
| examples/ | JSON/YAMLサンプル | 学習・動作確認 |
| output/sample/ | サンプル生成物 | GUIで構造を確認 |
| docs/ | Excel、Markdown、出典 | 設計・設定・運用の説明 |
| tests/ | 単体・結合・JTL読込検証 | 変更後の再確認 |
| requirements.lock.txt | 固定した依存版 | venvへ導入 |

| 設定 | 初期値 | 意図 |
|---|---|---|
| API接続先 | http://localhost:8080 | 指定された初期値 |
| 認証接続先 | COGNITO_TOKEN_URLで明示 | APIとCognitoの接続先を区別 |
| 有効API数 | 1件 | 小さく確認 |
| スレッド・ループ | 1・1 | 最小の疎通 |
| Ramp-up | 1秒 | 1スレッドでは開始前に必ず1秒待つ意味ではない |
| 接続・応答タイムアウト | 5000・30000ms | 無限に待たない |
| 実行全体の期限 | 180秒 | ハングを成功扱いしない |
| Keep-Alive | 有効 | 接続再利用 |
| Redirect・埋込取得 | 無効 | 追加通信を抑制 |
| HTTP自動再試行 | 0 | 隠れた重複送信を避ける |
| エラー時 | 該当スレッド停止 | 失敗のまま続けない |
| token reuse ratio | 0.75 | 寿命の途中で再取得 |
| jitter上限 | 0.05 | 再取得を少し早めて分散 |
| think_time_ms | 0 | 初回確認に余分な待機を入れない |
| APIキーヘッダー | X-API-KEY: XXXXXXXXXXXX | ヘッダー名を固定 |
| JTL | XML、本文・要求内容を保存しない | GUIで時間・HTTPコード・成否確認 |
| log | 実行ごとの一意なファイル | 前回と混在させない |

正常応答に40秒かかることが仕様のAPIへ応答タイムアウト30秒を当てれば、試験側でタイムアウトします。設定の推奨初期値とシステムの性能要件は同じではありません。

## 14 Windows 11で導入・生成・実行する

PowerShell 5.1以上、Python 3.11以上が必要です。本作業で実行検証したのはPowerShell 5.1、Python 3.12、Java 17です。PowerShell 7も使用する設計ですが実機未検証です。以下は配布フォルダーのルートで実行します。JMeterは別途公式配布を導入してください。[JMeter導入手順](https://jmeter.apache.org/usermanual/get-started.html)

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\scripts\Generate-CognitoJmx.ps1 -InputFile '.\examples\openapi.sample.yaml' -OutputDirectory '.\output\my-api' -Seed 42
~~~

Python 3.11なら `py -3.11` とします。独自のPythonは `-PythonExecutable 'C:\path with spaces\python.exe'` で指定できます。生成時はAPIを呼びません。出力先にファイルがあれば上書きしないため、新しいフォルダー名を使ってください。

下のIDとURLを実値へ置き換え、secretは入力欄へ入れます。secretをコマンド文字列として直接書かないので、コマンド履歴への記録を避けやすくなります。

~~~powershell
$env:COGNITO_TOKEN_URL = 'https://YOUR_DOMAIN.auth.ap-northeast-1.amazoncognito.com/oauth2/token'
$env:COGNITO_CLIENT_ID = 'YOUR_CLIENT_ID'
$env:COGNITO_SCOPE = 'demo-api/read'
$taskSecret = Read-Host 'Cognito client secret' -AsSecureString
$env:COGNITO_CLIENT_SECRET = [System.Net.NetworkCredential]::new('', $taskSecret).Password
~~~

REST APIが本物のAPIキーを要求する場合は、次の要領で試験用キーも設定します。

~~~powershell
$taskApiKey = Read-Host 'API key' -AsSecureString
$env:API_KEY = [System.Net.NetworkCredential]::new('', $taskApiKey).Password
.\scripts\Run-JMeter.ps1 -Scenario '.\output\my-api\scenario.jmx' -JMeterHome 'C:\tools\apache-jmeter-5.6.3' -ResultsDirectory '.\results\my-api'
~~~

secretはJavaが読むため環境変数内では平文です。SecureStringで入力しても、Javaのメモリーが暗号化されるわけではありません。CIでは管理されたsecret injectionを使用します。

localhost:8080でAPIが待ち受けていなければ接続エラーになります。API Gatewayを使う場合、実hostとstageに合わせて生成します。

~~~powershell
.\scripts\Generate-CognitoJmx.ps1 -InputFile 'C:\work\openapi.json' -OutputDirectory '.\output\gateway-smoke' -Server 'YOUR_API_ID.execute-api.ap-northeast-1.amazonaws.com' -Port 443 -Protocol https -BasePath '/test' -Operation 'listItems'
~~~

カスタムドメインやHTTP APIの$defaultステージでは `/test` が不要な場合があります。OpenAPIのserversは自動採用しません。明示したhost/port/protocol/base-pathへ送信します。

終了後の親PowerShellからsecretを消す例です。既に起動したJavaには環境のコピーが残るため、試験終了も確認します。

~~~powershell
Remove-Item Env:COGNITO_CLIENT_SECRET -ErrorAction SilentlyContinue
Remove-Item Env:API_KEY -ErrorAction SilentlyContinue
Remove-Variable taskSecret,taskApiKey -ErrorAction SilentlyContinue
~~~

実行ポリシーで止まる場合は組織の方針を確認してください。本作業では子プロセスだけに限定した `powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File ...` で検証しました。永続的にExecutionPolicyを変更する処理は含めません。

## 15 RHEL 9.8で導入・生成・実行する

Bash、Python 3.11以上、Java、JMeter 5.6.3を使います。OSの標準Pythonを上書きする必要はありません。管理方針と有効リポジトリーに応じて追加Python/JDKを導入してください。RHEL 9.8の存在・提供情報は公式資料を確認しています。[RHEL 9.8 Release Notes](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html-single/9.8_release_notes/index)

~~~bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
bash scripts/generate.sh \
  --input ./examples/openapi.sample.yaml \
  --out ./output/my-api \
  --seed 42
~~~

ZIP展開で実行ビットが付かなくても、上のようにbashへ渡せば起動できます。独自Pythonを使う場合は、PYTHON_EXECUTABLEへ実行可能ファイルの絶対パスを指定します。通常の.venvを使う場合、その指定は不要です。

~~~bash
# 必要な場合のみ指定
# export PYTHON_EXECUTABLE=/opt/test-python/bin/python
export COGNITO_TOKEN_URL='https://YOUR_DOMAIN.auth.ap-northeast-1.amazoncognito.com/oauth2/token'
export COGNITO_CLIENT_ID='YOUR_CLIENT_ID'
export COGNITO_SCOPE='demo-api/read'
read -r -s -p 'Cognito client secret: ' COGNITO_CLIENT_SECRET
printf '\n'
export COGNITO_CLIENT_SECRET
bash scripts/run.sh \
  --scenario ./output/my-api/scenario.jmx \
  --jmeter-home /opt/apache-jmeter-5.6.3 \
  --results ./results/my-api
unset COGNITO_CLIENT_SECRET API_KEY
~~~

必要なAPIキーもAPI_KEY環境変数へ同じ要領で入力します。secretを `-Jclient_secret=...` のようなプロセス引数へ載せる実装ではありません。シェルスクリプトはUTF-8/LFのまま使ってください。

**RHEL 9.8実機での実行は未検証です。** この作業環境はWindowsです。Bash静的解析と共通Python処理の検証を行っていますが、RHELでの実機試験とは区別します。実機では付属testsを用い、JDK・権限・ネットワーク・SELinux・証明書設定も確認してください。

## 16 OpenAPIから何をどう生成するか

処理順は「読込 → OpenAPI構造検査 → 操作列挙 → ランダム候補生成 → 元スキーマによる検査 → HTTP表現へ変換 → JMX出力」です。単純な文字列置換ではありません。

| OpenAPI情報 | 生成内容 | 例 |
|---|---|---|
| paths / method | operationごとのControllerとHTTP Request | GET /items |
| path parameter | URLへの埋込みとエンコード | /items/{itemId} → /items/82 |
| query parameter | URL query | ?limit=3 |
| header parameter | API専用Header Manager | X-Test: abc |
| cookie parameter | Cookieヘッダー | scalarのみ |
| requestBody | 型を保った本文 | 数値、boolean、配列、object |
| enum | 候補の中から選択 | bookまたはtoy |
| minimum / maximum | 数値の範囲 | 1〜10 |
| minLength / maxLength | 文字の長さ | 3〜24 |
| format | 形式に合う候補 | date、date-time、uuid、email |
| pattern | 対応できる正規表現から候補生成 | AB123 |
| readOnly | 入力から除外 | サーバーが採番するid |
| responses | ステータスAssertion | 201、204 |
| security | M2M対応の検査・必要scopeの一覧 | manifest.json |

OpenAPI 3.0.x/3.1.xのUTF-8 JSON/YAMLを対象にします。BOMも許容します。Swagger2、OpenAPI3.2、独自dialectは対象外です。3.1のnull、型配列、const、数値exclusive boundsにも対応します。「最新OpenAPIの全機能に対応」という意味ではありません。[OpenAPI 3.0.4](https://spec.openapis.org/oas/v3.0.4.html)、[OpenAPI 3.1.1](https://spec.openapis.org/oas/v3.1.1.html)

内部の `#/components/schemas/...` を解決します。外部ファイルやhttps URLの$ref、YAML aliasは拒否するので、信頼できる手順で1ファイルへbundleしてください。重複キーも拒否します。

allOf/oneOf/anyOfの候補は元の制約でも検証します。複雑な条件を満たせない場合、80回で停止し、`--data-file` で妥当な手動値を指定できるようにします。深さ・文字数・配列数にも上限があります。任意の制約から必ずデータを合成できるツールではありません。

## 17 ランダムデータを実用データへ替える

**初期値は生成時にランダムに決まり、同じJMXの実行中は固定です。** 3回のAPI呼出なら、3回とも同じpath/query/bodyを使います。毎回違うレコードを大量生成する設定ではありません。

`--seed 42` / `-Seed 42` で再現しやすくなります。新しい値には再生成を使います。スレッド別・リクエスト別の動的分配が必要ならCSV Data Set Configなどを追加して設計してください。

`test-data.json` はoperationIdごとに値を保存します。必要な部分だけ抜き出した手動JSONも入力できます。

~~~json
{
  "getItem": {"path": {"itemId": 42}},
  "createItem": {
    "body": {
      "name": "performance test item",
      "quantity": 2,
      "active": true,
      "category": "book",
      "code": "AB123"
    }
  }
}
~~~

~~~powershell
.\scripts\Generate-CognitoJmx.ps1 -InputFile '.\examples\openapi.sample.json' -OutputDirectory '.\output\with-data' -DataFile '.\my-data.json' -Operation 'getItem'
~~~

~~~bash
bash scripts/generate.sh --input ./examples/openapi.sample.json \
  --out ./output/with-data --data-file ./my-data.json --operation getItem
~~~

手動値もスキーマ検証します。Authorizationの上書きは拒否し、トークンから構築します。X-API-KEY初期値は固定し、実キーは実行時に渡します。

型に正しくても「存在する顧客ID」「在庫がある商品」「開始日より後の終了日」などは基本型だけでは判断できません。400や404が出た場合は、型の不整合と業務データの不足を分けて調べます。

全APIを有効にしても、作成結果のidを次のGETへ自動で引き継ぐわけではありません。定義順で個別に呼びます。API間の依存、データ抽出、順序、後片付けは実APIに合わせて追加します。

## 18 操作選択と負荷設定を変える

初期状態は最初のGET/HEADだけを有効化します。どちらもなければ先頭operationが有効です。manifest.jsonのenabled_operationsで確認してください。変更系APIを選べば実際にその処理を送信します。

| 目的 | PowerShell | Bash |
|---|---|---|
| APIを1件選ぶ | -Operation listItems | --operation listItems |
| 全API有効 | -EnableAll | --enable-all |
| seed固定 | -Seed 42 | --seed 42 |
| 手動データ | -DataFile my-data.json | --data-file my-data.json |
| path接頭辞 | -BasePath /test | --base-path /test |
| 同時スレッド数 | -Threads 10 | --threads 10 |
| ループ数 | -Loops 100 | --loops 100 |
| 立上げ秒数 | -RampSeconds 30 | --ramp-seconds 30 |
| API前待機 | -ThinkTimeMs 1000 | --think-time-ms 1000 |
| 実行期限 | -TimeoutSeconds 1800 | --timeout 1800 |

~~~powershell
.\scripts\Run-JMeter.ps1 -Scenario '.\output\my-api\scenario.jmx' -JMeterHome 'C:\tools\apache-jmeter-5.6.3' -ResultsDirectory '.\results\load-01' -Threads 10 -Loops 100 -RampSeconds 30 -ThinkTimeMs 1000 -TimeoutSeconds 1800
~~~

~~~bash
bash scripts/run.sh --scenario ./output/my-api/scenario.jmx \
  --jmeter-home /opt/apache-jmeter-5.6.3 --results ./results/load-01 \
  --threads 10 --loops 100 --ramp-seconds 30 \
  --think-time-ms 1000 --timeout 1800
~~~

失敗なしなら10スレッド×100ループ×有効API1件＝1000 APIリクエストです。API5件なら5000件。AUTH件数は別です。10スレッドは10 RPSと同じではありません。応答時間と待機時間で送信頻度が変わります。

scopeは有効APIに合わせます。manifest.jsonのrequired_scopes_selectedは定義から読み取れた参考値であり、APIの実認可設定を保証しません。実際の許可条件と照合しCOGNITO_SCOPEへ指定します。

GUIではAPI全体を囲むSimple Controllerを有効にします。親が無効のまま、子のHTTP Requestだけを有効にしても動きません。

毎回発行する比較実験では `token_each_request=true` を非秘密propertiesへ書き、`-PropertiesFile` / `--properties` で指定します。初期値はfalseです。AUTH件数と試験目的を記録してください。



## 19 JTLとlogを読み、GUIで確認する

runnerはUTC日時＋ランダム識別子でJTLとlogを作ります。JMX内のSimple Data WriterがJTLを書くため、runnerは `-Jresult_file=...` を渡します。同じファイルへ `-l` を追加して、writerを二重登録しないでください。

| ファイル | 読む内容 | 通常保存しない内容 |
|---|---|---|
| JTL | ラベル、HTTPコード、成否、経過時間、接続時間、latency | 要求・応答本文、要求・応答ヘッダー |
| log | 起動、スクリプト、ネットワーク等の診断 | secretやtoken全文を意図的に出力しない |
| manifest.json | 入力ハッシュ、有効操作、scope、警告 | 実資格情報 |
| test-data.json | 生成した入力データ | 取得したaccess_token |

HTTP200でもsuccess=falseの場合があります。例えばOAuth200でtoken_typeが不正なら後処理が失敗にします。HTTPコードだけで合否を判断しません。

1. JMeter 5.6.3を起動し、scenario.jmxを開きます。
2. Test Planを右クリックし、Add → Listener → View Results Treeを追加します。
3. ListenerのFilenameで、生成済みJTLを選びます。
4. AUTHとAPIの各行を選び、Sampler resultの時間・コード・成否を確認します。
5. 結果を読むだけならStartを押す必要はありません。

JTLはJMeterが読めるXMLです。Response dataやRequest詳細が空なのは破損ではなく、秘密や応答データを保存しない設定のためです。負荷試験はCLIで実施し、少量の結果を後からGUIで確認します。[Listenerと保存形式](https://jmeter.apache.org/usermanual/listeners.html)

API応答本文が必要な診断では、合成データによる少量試験で保存設定を変えられます。OAuth応答はPostProcessorが秘匿化しますが、APIがtokenを応答へ反射すればAPI本文にも秘密が入り得ます。保存範囲を変えた結果の共有には注意してください。

HTML DashboardはCSVを前提とするため、このXML JTLをそのまま `-g` に渡す手順ではありません。大量負荷向けにCSVへ変える場合、JMXの保存設定とrunnerのXML結果検査を両方変更・検証します。本配布物はGUIでの結果確認を優先しています。

GUIから実行する場合は、資格情報を設定したシェルから起動してください。既に別の場所から起動したGUIへ、環境変数はあとから反映されません。次のように一意のresult_fileを指定すると、旧results.jtlへの追記も避けられます。

~~~powershell
New-Item -ItemType Directory -Path '.\results\gui' -Force | Out-Null
$taskRunId = Get-Date -Format 'yyyyMMdd-HHmmssfff'
$taskJtl = Join-Path (Get-Location) "results\gui\$taskRunId.jtl"
$taskLog = Join-Path (Get-Location) "results\gui\$taskRunId.log"
& 'C:\tools\apache-jmeter-5.6.3\bin\jmeter.bat' -t '.\output\my-api\scenario.jmx' -q '.\output\my-api\jmeter.properties' "-Jresult_file=$taskJtl" -j "$taskLog"
~~~

## 20 エラーの切り分けを順番に行う

| 症状 | 主な原因候補 | 最初の確認 |
|---|---|---|
| CONFIG_ERROR、通信0件 | 環境変数・URL・数値設定 | JMeterを起動したプロセスが値を持つか。秘密の値をログに出さない |
| token endpointで400 | grant_type・scope・許可フロー | client_credentialsとカスタムscopeの関連付け |
| invalid_client | IDとsecretの不一致 | 環境・クライアントを混ぜていないか |
| invalid_scope / invalid_grant | 未定義scope・未許可scope等 | resource server識別子と許可scope |
| Cognito429 | 発行頻度・バースト・上限 | 毎回発行や急なRamp-upになっていないか |
| token成功後のAPI401 | issuer・aud/client_id・期限・Bearer | REST/HTTP APIの種類とauthorizer設定 |
| API403 | scope、APIキー、WAF、resource policy等 | 実際の拒否理由を秘匿された運用ログで確認 |
| API404 | host、stage、base-path、path、実在ID | URLとデータ |
| API400/422 | 型、値、業務上の制約 | test-data.jsonとAPI契約 |
| 接続不能 | localhost未起動、DNS、port、ネットワーク | 待受先と通信経路 |
| TLS関係 | 証明書、プロキシ、Java、接続先 | AWSドメインと組織のTLS経路 |
| Java終了0なのに試験NG | JMeterサンプル失敗 | JTLのsuccess。runnerは終了コード以外も検査 |
| tokenのみ成功しAPI0件 | operationの無効化・JMX編集 | runnerはAPI件数0も失敗にする |
| 180秒で終了 | process deadline | 長時間試験ならtimeoutを明示的に延ばす |

401/403の自動再送はしません。POSTでは応答を受け取れなくても、サーバー側で処理が終わっている可能性があり、勝手な再送は二重登録を招きます。Cognito429も初期設定では再試行しません。再試行を設計するなら、回数、待機、jitter、冪等性、計測上の扱いを定めます。

OAuth応答全文を消すため、error_descriptionも通常JTLには残りません。詳細は管理された診断環境で調査します。HTTP wire/debugログや外部Listenerを追加すれば、通常の秘匿範囲を超えてヘッダーを保存する可能性があります。

## 21 セキュリティと測定の境界

実secretや取得tokenをJMXへ固定せず、APIへBasic資格情報を送らず、JTLへ通常の要求・応答内容を保存しません。ただし、OSのプロセス環境、メモリーダンプ、外部Listener、debugログは別に管理が必要です。

**JMeter標準のHTTPS特性:** HTTP Samplerはテストの柔軟性のため、サーバー証明書を広く受け入れる仕様です。「URLがHTTPS」という条件を「本番クライアントと同等の証明書・ホスト名検証を保証」と読み替えないでください。厳格なTLS検証自体が試験要件なら、その検証を行う通信実装・経路を別途設計し検証してください。本配布物はこのJMeterの標準動作を変更するプラグインを導入していません。[JMeter公式HTTPS説明](https://jmeter.apache.org/usermanual/get-started.html)

localhost向けAPIは指定どおりHTTPを許容します。token URLはHTTPSを要求し、模擬サーバーに限り `allow_http_token_loopback=true` とloopbackアドレスの両方でHTTPを許可します。実CognitoへHTTPでsecretを送る例ではありません。

リモートAPIのHTTPは既定で拒否します。管理された試験ネットワークで必要な場合だけ `allow_http_api=true` を指定します。API Gatewayの一般的な外部接続はHTTPS:443です。

現在のCognitoは最大2つの有効secretを使う段階的ローテーションに対応します。「secretは変更できず必ずクライアントを作り直す」という旧説明は採用しません。ただし実行中Javaの環境変数は自動更新されないため、このツールはsecret切替時に新しい環境で再起動します。[AWSのsecret管理更新](https://aws.amazon.com/about-aws/whats-new/2026/02/amazon-cognito-client-secret-lifecycle/)

## 22 性能試験として成立させる見方

少量疎通の成功は高負荷での性能保証ではありません。1件で認証・URL・データを確認し、少数スレッド、立上げ、定常負荷、終了へ段階的に進みます。SLA/SLO等の合格値は実システムの要件から決めます。

| 観点 | 測ること | 理由 |
|---|---|---|
| API時間 | p50、p95、p99、最大値 | 平均だけでは遅い一部を見逃す |
| 成功率 | HTTPとAssertion、業務結果 | 速いエラー応答を成功に数えない |
| 実到達件数 | APIラベルとサーバー側件数 | 認証失敗でAPIへ届いていない可能性 |
| 認証負荷 | AUTH件数、時間、429 | APIとは別の限界 |
| 起動・定常 | 初回発行、TLS接続、キャッシュ | 立上げと定常を混ぜない |
| 負荷生成機 | CPU、GC、メモリー、接続 | JMeter側の限界を誤認しない |
| データ分布 | 同一ID、多数ID、cache hit | 固定データが高いキャッシュ効果を生む可能性 |
| 認可 | 本番同等のauthorizer/scope/claims | 認可省略の数値と混同しない |

API HTTPサンプルのelapsedには、前のtoken判定スクリプトやthink timeを含めません。全体の業務時間を測る場合はTransaction Controller等を別途設計し、親と子を二重計上しないようにします。

初期設定では失敗したスレッドが止まり、その後のRPSが下がります。負荷中の失敗率を継続観測したい場合は、停止方針と終了条件を設計し直してください。単にcontinueへ変えればよいという話ではありません。

## 23 対応範囲と実環境で残る作業

| 分類 | 実装 | 境界 |
|---|---|---|
| OS入口 | Bash、PowerShell5.1以上 | RHEL9.8/PowerShell7実機は未検証 |
| JMeter | 5.6.3 | runnerは他版を拒否 |
| 認証 | /oauth2/token client_credentials | GetClientToken、ユーザーログイン、SigV4は生成しない |
| キャッシュ | スレッドごと | JVM全体・複数機械での共有なし |
| 入力 | OpenAPI3.0.x/3.1.x JSON/YAML | 3.2、Swagger2、外部$ref、YAML alias対象外 |
| method | GET/PUT/POST/DELETE/OPTIONS/HEAD/PATCH/TRACE | ANYは具体的methodへ変換 |
| parameters | form/simple/label/matrix、平坦deepObject等 | 複雑な入れ子・allowReserved等は拒否 |
| body | JSON/+json、form、text、限定multipart、模擬octet-stream | XML、実ファイルuploadの完全再現は対象外 |
| schema | 基本型、主な制約、内部参照、composition | 任意の複雑な制約の生成保証はない |
| 判定 | HTTPコード、token応答、JTL | API応答の完全な業務検証はない |
| データ | 生成時ランダム、seed、上書き | 実在性・ID相関・順序依存は利用者が設計 |

対応外の操作を黙って落として「全APIを生成」とはしません。必要な処理が対応外なら停止します。callbacksはサーバーから来る通信なので受信サーバーは生成せず、警告に記録します。

実環境では、実OpenAPIの投入、実データ、Cognitoクライアント・scope・domain、API Gateway認可、資格情報注入、実URL、少量疎通を確認します。これらが未提供の状態で「実APIも検証済み」とは扱いません。

## 24 保守と再検証

以下は配布物を変更した後の検証方法です。結合試験は自身で起動したloopback模擬サーバーだけへ接続し、実AWSを呼びません。

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q apikit cognito_jmeter.py tests
.\.venv\Scripts\python.exe tests\integration_jmeter.py --jmeter-home 'C:\tools\apache-jmeter-5.6.3'
~~~

~~~bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q apikit cognito_jmeter.py tests
shellcheck -s bash scripts/generate.sh scripts/run.sh
.venv/bin/python tests/integration_jmeter.py --jmeter-home /opt/apache-jmeter-5.6.3
~~~

実施結果・未実施事項はVALIDATION.mdを参照してください。Meiryo UIはファイルに指定していますが、閲覧機にフォントがない場合は代替表示になります。フォント自体は配布しません。

Excelは同じMarkdownから生成し、モノトーン、折返し、列幅・行高、固定見出し、印刷範囲、リンクを設定します。コードもMeiryo UIなので、等幅フォントの桁揃えとは見え方が異なる場合があります。

「発行数試算」シートは入力を変えると概算が変わる計算例で、JMeterの実行設定を変更する機能ではありません。十分頻繁なAPI呼出、エラーなし、jitterなし等の仮定を置きます。クォータや請求額の保証には使いません。
