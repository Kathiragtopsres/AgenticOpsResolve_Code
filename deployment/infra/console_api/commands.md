# Ops Console backend API: deploy + verify commands (OPS-014/015 assist, T7/T8)

Nothing here has been run. Every block below is an AWS mutation unless it says READ-ONLY.
Run from `tcs-agentic-opsresolve\` in PowerShell, one block at a time, after go-ahead.
Account `[AWS-ACCOUNT-NUMBER]`, region `us-east-1`. All JSON is in files next to this doc.

Files: `trust-lambda.json`, `policy-read.json`, `policy-approval-decision.json`,
`policy-scenario.json`, `lambda-env.json`, `cors.json`.

Prereqs (local, no AWS mutation):

```powershell
$env:AWS_DEFAULT_REGION = "us-east-1"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\package_console_api.ps1
```

Pre-deploy check (READ-ONLY): the read Lambda queries index `GSI1` on the cases table. Confirm the name
and that it projects ALL (the read handler needs full items):

```powershell
aws dynamodb describe-table --table-name opsresolve-dev-cases --query "Table.GlobalSecondaryIndexes[].{n:IndexName,p:Projection.ProjectionType}"
```

## 1. IAM roles (3)

Blast radius: creates 3 new IAM roles + 3 inline policies. Nothing existing is modified. Reversible with
`delete-role-policy` / `delete-role`. Each policy is scoped to named tables, the dev bus, the telemetry topic
prefix, and the Lambda's own log group.

```powershell
aws iam create-role --role-name opsresolve-dev-console-read-role --assume-role-policy-document file://infra/console_api/trust-lambda.json
aws iam put-role-policy --role-name opsresolve-dev-console-read-role --policy-name opsresolve-dev-console-read --policy-document file://infra/console_api/policy-read.json

aws iam create-role --role-name opsresolve-dev-console-approval-decision-role --assume-role-policy-document file://infra/console_api/trust-lambda.json
aws iam put-role-policy --role-name opsresolve-dev-console-approval-decision-role --policy-name opsresolve-dev-console-approval-decision --policy-document file://infra/console_api/policy-approval-decision.json

aws iam create-role --role-name opsresolve-dev-console-scenario-role --assume-role-policy-document file://infra/console_api/trust-lambda.json
aws iam put-role-policy --role-name opsresolve-dev-console-scenario-role --policy-name opsresolve-dev-console-scenario --policy-document file://infra/console_api/policy-scenario.json
```

Policy summary (cross-checked against the handler code):

| Role | Actions | Resources | Code that needs it |
|---|---|---|---|
| read | `dynamodb:Query`, `dynamodb:Scan` | cases + `cases/index/*`, approvals | `t.query(IndexName=GSI1)`, `scan` on cases/approvals |
| read | `dynamodb:GetItem` | `mock-customers` | customer name lookup |
| decision | `dynamodb:GetItem`, `dynamodb:UpdateItem` | approvals | `update_item` + `get_item` on the conflict path |
| decision | `events:PutEvents` | `opsresolve-dev-bus` | `Approval Decided` |
| scenario | `dynamodb:Scan`, `dynamodb:BatchWriteItem` | cases, approvals, conversations | reset scan + `batch_writer` delete/put |
| scenario | `dynamodb:Query` | cases | case lookup for scenario buttons |
| scenario | `events:PutEvents` | `opsresolve-dev-bus` | delay / shortage / complete |
| scenario | `iot:Publish` | `topic/opsresolve/telemetry/*` | telemetry |
| all | `logs:CreateLogGroup/CreateLogStream/PutLogEvents` | own `/aws/lambda/<fn>` | logging |

Only `mock-customers` is read by the console code. The scenario Lambda reads no mock table, so it gets none.
`batch_writer` issues `BatchWriteItem` for puts and deletes, so no `PutItem`/`DeleteItem` is granted.

## 2. Lambdas (3)

Blast radius: creates 3 new functions. No existing function is touched. Wait ~10s after the roles exist for IAM
propagation; if `create-function` says the role cannot be assumed, retry.

```powershell
aws lambda create-function --function-name opsresolve-dev-console-read --runtime python3.12 --architectures x86_64 --handler opsresolve.console_api.read_handler.handler --role arn:aws:iam::[AWS-ACCOUNT-NUMBER]:role/opsresolve-dev-console-read-role --zip-file fileb://build/console_api/console-read.zip --timeout 15 --memory-size 512 --environment file://infra/console_api/lambda-env.json

aws lambda create-function --function-name opsresolve-dev-console-approval-decision --runtime python3.12 --architectures x86_64 --handler opsresolve.console_api.approval_decision_handler.handler --role arn:aws:iam::[AWS-ACCOUNT-NUMBER]:role/opsresolve-dev-console-approval-decision-role --zip-file fileb://build/console_api/console-approval-decision.zip --timeout 10 --memory-size 256 --environment file://infra/console_api/lambda-env.json

aws lambda create-function --function-name opsresolve-dev-console-scenario --runtime python3.12 --architectures x86_64 --handler opsresolve.console_api.scenario_handler.handler --role arn:aws:iam::[AWS-ACCOUNT-NUMBER]:role/opsresolve-dev-console-scenario-role --zip-file fileb://build/console_api/console-scenario.zip --timeout 60 --memory-size 512 --environment file://infra/console_api/lambda-env.json
```

Code-only redeploy later (updates the function code only):

```powershell
aws lambda update-function-code --function-name opsresolve-dev-console-read --zip-file fileb://build/console_api/console-read.zip
```

## 3. HTTP API, authorizer, integrations, routes, CORS

Blast radius: creates one new HTTP API (public endpoint, but every route requires a Cognito ID token),
one authorizer, 3 integrations, 15 routes, a `$default` auto-deploy stage, and 3 Lambda resource policies
scoped to this API's ARN. Nothing existing is modified. Reversible with `delete-api` and `remove-permission`.

Create the API with CORS (origins: the S3 website and `http://localhost:5173` only):

```powershell
$api = aws apigatewayv2 create-api --name opsresolve-dev-console-api --protocol-type HTTP --cors-configuration file://infra/console_api/cors.json --query ApiId --output text
$api
```

JWT authorizer (ID token; `aud` claim must equal the SPA client id):

```powershell
$auth = aws apigatewayv2 create-authorizer --api-id $api --name opsresolve-dev-console-cognito --authorizer-type JWT --identity-source '$request.header.Authorization' --jwt-configuration "Audience=4hgmbfm02hnjv22bic3bdeu7gq,Issuer=https://cognito-idp.us-east-1.amazonaws.com/us-east-1_wJv8NbJQR" --query AuthorizerId --output text
$auth
```

One AWS_PROXY (payload 2.0) integration per Lambda:

```powershell
$fnRead = "arn:aws:lambda:us-east-1:[AWS-ACCOUNT-NUMBER]:function:opsresolve-dev-console-read"
$fnDec  = "arn:aws:lambda:us-east-1:[AWS-ACCOUNT-NUMBER]:function:opsresolve-dev-console-approval-decision"
$fnScn  = "arn:aws:lambda:us-east-1:[AWS-ACCOUNT-NUMBER]:function:opsresolve-dev-console-scenario"
$iRead = aws apigatewayv2 create-integration --api-id $api --integration-type AWS_PROXY --integration-uri $fnRead --payload-format-version 2.0 --query IntegrationId --output text
$iDec  = aws apigatewayv2 create-integration --api-id $api --integration-type AWS_PROXY --integration-uri $fnDec  --payload-format-version 2.0 --query IntegrationId --output text
$iScn  = aws apigatewayv2 create-integration --api-id $api --integration-type AWS_PROXY --integration-uri $fnScn  --payload-format-version 2.0 --query IntegrationId --output text
```

Routes, each with the JWT authorizer. HTTP APIs route to the most specific match, so `GET /cases/summary` and
`GET /cases/names` coexist with `GET /cases/{id}`. `GET /cases?recent=5` and `?column=` are query strings on
`GET /cases`. There are no `OPTIONS` routes: the API's CORS config answers preflights before the authorizer runs.

```powershell
$routes = @(
  @("GET /cases", $iRead), @("GET /cases/summary", $iRead), @("GET /cases/names", $iRead),
  @("GET /cases/{id}", $iRead), @("GET /timeline", $iRead), @("GET /cases/{id}/timeline", $iRead),
  @("GET /cases/{id}/trace", $iRead), @("GET /approvals", $iRead), @("GET /kpis", $iRead),
  @("POST /approvals/{id}/decision", $iDec),
  @("POST /scenario/reset", $iScn), @("POST /scenario/telemetry", $iScn),
  @("POST /scenario/technician-delay", $iScn), @("POST /scenario/part-shortage", $iScn),
  @("POST /scenario/complete-work-order", $iScn),
  @("GET /scenario/telemetry/profiles", $iScn)
)
foreach ($r in $routes) {
  aws apigatewayv2 create-route --api-id $api --route-key $r[0] --target "integrations/$($r[1])" --authorization-type JWT --authorizer-id $auth --query RouteKey --output text
}
```

`$default` stage with auto-deploy:

```powershell
aws apigatewayv2 create-stage --api-id $api --stage-name '$default' --auto-deploy
```

Allow this API (and only this API) to invoke each Lambda:

```powershell
$acct = "[AWS-ACCOUNT-NUMBER]"
$src = "arn:aws:execute-api:us-east-1:${acct}:${api}/*/*/*"
aws lambda add-permission --function-name opsresolve-dev-console-read --statement-id apigw-console-api --action lambda:InvokeFunction --principal apigateway.amazonaws.com --source-arn $src
aws lambda add-permission --function-name opsresolve-dev-console-approval-decision --statement-id apigw-console-api --action lambda:InvokeFunction --principal apigateway.amazonaws.com --source-arn $src
aws lambda add-permission --function-name opsresolve-dev-console-scenario --statement-id apigw-console-api --action lambda:InvokeFunction --principal apigateway.amazonaws.com --source-arn $src
```

Base URL (READ-ONLY; record it in `resource.md` and give it to the frontend dev):

```powershell
aws apigatewayv2 get-api --api-id $api --query ApiEndpoint --output text
```

## 4. Seed run (separate, data mutation)

Blast radius: DELETES every item in `opsresolve-dev-cases`, `opsresolve-dev-approvals` and
`opsresolve-dev-conversations`, then writes the 12-case / 8-approval baseline. Mock tables and the idempotency
table are untouched. The 5 live cases are replaced by their baseline versions (customer `CUST-1042`).
Not reversible except via PITR on `cases`. Dry run first (no AWS calls):

```powershell
python scripts\seed_console_demo.py --dry-run
python scripts\seed_console_demo.py
```

The second command prompts for `yes`. Use `--yes` to skip the prompt.

## 5. Fix the 4 UNKNOWN customers (separate, update only)

Skip this if you ran the seed above: the reset already replaces those cases with `CUST-1042`.
Blast radius: updates `customerId` on 4 live case META items. No deletes. Reversible by setting the value back.

Write and run a temp script (no heredocs on PowerShell):

```powershell
$py = @'
import boto3
t = boto3.resource("dynamodb", region_name="us-east-1").Table("opsresolve-dev-cases")
for cid in ["SR-FE7018E586", "SR-AB399E0FBA", "SR-BB04413006", "SR-B869D97F45"]:
    r = t.update_item(
        Key={"pk": f"CASE#{cid}", "sk": "META"},
        UpdateExpression="SET customerId = :c",
        ConditionExpression="attribute_exists(pk) AND customerId = :u",
        ExpressionAttributeValues={":c": "CUST-1042", ":u": "UNKNOWN"},
        ReturnValues="UPDATED_NEW",
    )
    print(cid, r["Attributes"])
'@
Set-Content $env:TEMP\fix_unknown.py $py
python $env:TEMP\fix_unknown.py
Remove-Item $env:TEMP\fix_unknown.py
```

The condition makes it fail safely on any case that is already fixed or missing. If the META key or attribute name
differs from what `resource.md` says, the condition check fails and nothing is written.

## 6. OPTIONAL: make the Agent Invoker and the AgentCore agent pick up T2/T3

Not needed for the console. Until this runs, the deployed agent still uses the old `openWaits` (the retired `APR#`
path) and writes no trace fields on new runs. Seeded cases are unaffected.

IAM: no change needed. S1 verified that `opsreslove-lambda-exe-role` and `opsresolve-agentcore-agent-exe-role`
both already carry `AmazonDynamoDBFullAccess`, which covers `Scan` on `opsresolve-dev-approvals`. If those were ever
narrowed, this is the statement to add:

```json
{"Effect":"Allow","Action":["dynamodb:Scan"],"Resource":["arn:aws:dynamodb:us-east-1:[AWS-ACCOUNT-NUMBER]:table/opsresolve-dev-approvals"]}
```

Blast radius: replaces the code of the live Invoker Lambda (fires on all 8 EventBridge rules) and redeploys the
AgentCore Runtime agent. A bad build breaks event handling until rolled back. Rebuild the Invoker zip exactly as
`resource.md` (OPS-005 Lambda section) describes, since it is a manual zip with pinned deps, then:

```powershell
aws lambda update-function-code --function-name opsresolve-dev-agent-invoker --zip-file fileb://<path-to-rebuilt-invoker.zip>
aws lambda get-function-configuration --function-name opsresolve-dev-agent-invoker --query "{State:State,Update:LastUpdateStatus}"
```

AgentCore agent (run from the agentcore project dir, as in the earlier redeploys):

```powershell
agentcore deploy
```

## 7. Verification

Setup (READ-ONLY on AWS; reads credentials from `config\secrets.json` at runtime and never prints them).
`initiate-auth` only issues tokens. Paste `$api` from step 3, or fetch it with
`aws apigatewayv2 get-apis --query "Items[?Name=='opsresolve-dev-console-api'].ApiId" --output text`.

```powershell
$base = "https://$api.execute-api.us-east-1.amazonaws.com"
$users = (Get-Content config\secrets.json -Raw | ConvertFrom-Json).'opsresolve/dev/console-users'

function Get-IdToken($u) {
  $f = Join-Path $env:TEMP "ops_auth.json"
  @{ USERNAME = $u.username; PASSWORD = $u.password } | ConvertTo-Json | Set-Content $f -Encoding ascii
  try {
    aws cognito-idp initiate-auth --auth-flow USER_PASSWORD_AUTH --client-id 4hgmbfm02hnjv22bic3bdeu7gq --auth-parameters "file://$($f -replace '\\','/')" --query AuthenticationResult.IdToken --output text
  } finally { Remove-Item $f -Force }
}

# Prints "<status> <body>". $tok = $null sends no Authorization header.
function Call($method, $path, $tok, $body) {
  $curlArgs = @("-s", "-X", $method, "-w", " [%{http_code}]", "$base$path")
  if ($tok) { $curlArgs += @("-H", "Authorization: Bearer $tok") }
  if ($null -ne $body) {
    $bf = Join-Path $env:TEMP "ops_body.json"
    Set-Content $bf $body -Encoding ascii
    $curlArgs += @("-H", "Content-Type: application/json", "--data-binary", "@$($bf -replace '\\','/')")
  }
  try { curl.exe @curlArgs } finally { Remove-Item (Join-Path $env:TEMP "ops_body.json") -Force -ErrorAction SilentlyContinue }
  ""
}

$tok1 = Get-IdToken $users[0]
$tok2 = Get-IdToken $users[1]
"tokens: " + ($tok1.Length -gt 100) + " " + ($tok2.Length -gt 100)
```

7a. Positive read routes with user 1 (expect `[200]` each). `Call` output can be long; append `| Select-Object -First 1`
and pipe to `Out-String` as needed.

```powershell
Call GET "/cases/summary" $tok1
Call GET "/cases?limit=20" $tok1
Call GET "/cases?column=Open&limit=10" $tok1
Call GET "/cases?recent=5" $tok1
Call GET "/cases/names" $tok1
Call GET "/cases/SR-5E20B84F19" $tok1
Call GET "/timeline?limit=10" $tok1
Call GET "/cases/SR-5E20B84F19/timeline" $tok1
Call GET "/cases/SR-5E20B84F19/trace" $tok1
Call GET "/approvals?status=PENDING" $tok1
Call GET "/approvals?status=DECIDED" $tok1
Call GET "/kpis?weeks=4" $tok1
```

7b. Same read with user 2, to prove both logins are accepted (expect `[200]`):

```powershell
Call GET "/cases/summary" $tok2
```

7c. Negatives that do not change data:

```powershell
Call GET "/cases/summary" $null                      # no token -> [401]
Call GET "/cases/summary" "not.a.jwt"                # bad token -> [401]
Call GET "/cases/SR-DOES-NOT-EXIST" $tok1            # [404]
Call GET "/cases?column=Nope" $tok1                  # [400]
Call POST "/approvals/APR-0601-1/decision" $tok1 "{not json"          # [400]
Call POST "/approvals/APR-0601-1/decision" $tok1 '{"decision":"MAYBE"}' # [400]
Call POST "/approvals/APR-DOES-NOT-EXIST/decision" $tok1 '{"decision":"APPROVE"}'  # [404]
Call POST "/scenario/telemetry" $tok1 '{"profile":"nope"}'              # [400]
Call POST "/scenario/telemetry" $null '{"profile":"normal"}'            # [401]
```

Also confirm CORS preflight (READ-ONLY, no token needed). Expect `access-control-allow-origin` echoing the S3
origin, and no such header for a different origin:

```powershell
curl.exe -s -i -X OPTIONS "$base/cases/summary" -H "Origin: http://agentic-ops-resolve-ui.s3-website-us-east-1.amazonaws.com" -H "Access-Control-Request-Method: GET" -H "Access-Control-Request-Headers: authorization"
curl.exe -s -i -X OPTIONS "$base/cases/summary" -H "Origin: http://evil.example.com" -H "Access-Control-Request-Method: GET"
```

7d. MUTATING checks. These change seeded demo data. Re-run the reset afterwards.

Approval decision, then double decision (expect `[200]` then `[409]`). Picks the first PENDING approval from the API:

```powershell
$pending = (curl.exe -s "$base/approvals?status=PENDING" -H "Authorization: Bearer $tok1") | ConvertFrom-Json
$aid = @($pending.approvals)[0].approvalId
$aid
Call POST "/approvals/$aid/decision" $tok1 '{"decision":"APPROVE","reason":"verification run"}'   # [200]
Call POST "/approvals/$aid/decision" $tok2 '{"decision":"REJECT"}'                                  # [409]
```

Each decision also publishes `Approval Decided` to `opsresolve-dev-bus`, which the Invoker picks up and may start
an agent run for that case.

Scenario routes (expect `[200]` with `logs`, or `[409]` when no applicable case exists). These publish to the bus
or IoT. The Invoker and agent will run for real, which costs model tokens:

```powershell
Call POST "/scenario/telemetry" $tok1 '{"profile":"normal"}'
Call POST "/scenario/technician-delay" $tok1 '{}'
Call POST "/scenario/part-shortage" $tok1 '{}'
Call POST "/scenario/complete-work-order" $tok1 '{}'
Call POST "/scenario/reset" $tok1 '{}'
Call GET "/cases/summary" $tok1                       # back to 12 cases
```

Clean up the token variables when done:

```powershell
Remove-Variable tok1, tok2, users
```
