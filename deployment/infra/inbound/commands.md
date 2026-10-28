# RCS integration: deploy commands (inbound normalizer + Gateway Lambda)

Run from `tcs-agentic-opsresolve\` in PowerShell. Account `[AWS-ACCOUNT-NUMBER]`, region `us-east-1`.

## Already created (2026-10-08)

- Secret `opsresolve/dev/postback-hmac-key` (64 hex chars; shared by the Gateway Lambda to sign and the normalizer to verify)
- SQS `opsresolve-dev-inbound` (standard, visibility 180 s, redrive to the DLQ after 3 receives, policy allows only SNS topic `agentic-opsresolve-topic`)
- SQS `opsresolve-dev-inbound-dlq` (standard, 14-day retention)
- SNS subscription: `agentic-opsresolve-topic` -> `opsresolve-dev-inbound` (raw delivery off, so records hold the SNS envelope)

## Normalizer Lambda: DEPLOYED 2026-10-08

Role, function and SQS trigger (UUID `7e9943fc-4064-4c69-ba8a-0fe59a8ffcfd`) exist. The first `create-role` hit the 3000-role quota;
it worked after a slot was freed. Commands as run (files: `policy-normalizer.json`, `lambda-env.json`; zip from `scripts\package_inbound_normalizer.ps1`):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\package_inbound_normalizer.ps1

aws iam create-role --role-name opsresolve-dev-inbound-normalizer-role --assume-role-policy-document file://infra/console_api/trust-lambda.json
aws iam put-role-policy --role-name opsresolve-dev-inbound-normalizer-role --policy-name opsresolve-dev-inbound-normalizer --policy-document file://infra/inbound/policy-normalizer.json

aws lambda create-function --function-name opsresolve-dev-inbound-normalizer --runtime python3.12 --architectures x86_64 --handler opsresolve.inbound.normalizer.handler --role arn:aws:iam::[AWS-ACCOUNT-NUMBER]:role/opsresolve-dev-inbound-normalizer-role --zip-file fileb://build/inbound/inbound-normalizer.zip --timeout 30 --memory-size 256 --environment file://infra/inbound/lambda-env.json

aws lambda create-event-source-mapping --function-name opsresolve-dev-inbound-normalizer --event-source-arn arn:aws:sqs:us-east-1:[AWS-ACCOUNT-NUMBER]:opsresolve-dev-inbound --batch-size 10 --function-response-types ReportBatchItemFailures
```

Code-only redeploy later:

```powershell
aws lambda update-function-code --function-name opsresolve-dev-inbound-normalizer --zip-file fileb://build/inbound/inbound-normalizer.zip
```

## WhatsApp (OPS-012): DEPLOYED 2026-10-12

```powershell
# Templates (Meta review; re-run without --submit to see status)
python scripts/whatsapp_templates.py --submit

# Inbound: own SNS topic -> the same SQS queue / normalizer
aws sns create-topic --name opsresolve-dev-whatsapp-events
aws sns set-topic-attributes --topic-arn arn:aws:sns:us-east-1:[AWS-ACCOUNT-NUMBER]:opsresolve-dev-whatsapp-events --attribute-name Policy --attribute-value file://infra/whatsapp/sns-topic-policy.json
aws sqs set-queue-attributes --queue-url https://sqs.us-east-1.amazonaws.com/[AWS-ACCOUNT-NUMBER]/opsresolve-dev-inbound --attributes file://infra/whatsapp/queue-policy.json
aws sns subscribe --topic-arn arn:aws:sns:us-east-1:[AWS-ACCOUNT-NUMBER]:opsresolve-dev-whatsapp-events --protocol sqs --notification-endpoint arn:aws:sqs:us-east-1:[AWS-ACCOUNT-NUMBER]:opsresolve-dev-inbound
aws socialmessaging put-whatsapp-business-account-event-destinations --id waba-4fba8293f662494ca0a1335bce91f6a6 --event-destinations eventDestinationArn=arn:aws:sns:us-east-1:[AWS-ACCOUNT-NUMBER]:opsresolve-dev-whatsapp-events

# IAM
aws iam put-role-policy --role-name OpsResolve-target-tools-lambda-role-542zgxm6 --policy-name opsresolve-dev-gateway-tools --policy-document file://infra/inbound/policy-gateway-tools.json
aws iam put-role-policy --role-name opsresolve-dev-inbound-normalizer-role --policy-name opsresolve-dev-inbound-normalizer --policy-document file://infra/inbound/policy-normalizer.json

# Normalizer code
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\package_inbound_normalizer.ps1
aws lambda update-function-code --function-name opsresolve-dev-inbound-normalizer --zip-file fileb://build/inbound/inbound-normalizer.zip

# Gateway Lambda: code, handler, env
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\package_gateway_tools.ps1
aws lambda update-function-code --function-name OpsResolve-target-tools-lambda --zip-file fileb://build/gateway/gateway-tools.zip
aws lambda update-function-configuration --function-name OpsResolve-target-tools-lambda --handler lambda_handler.lambda_handler --environment file://infra/inbound/gateway-lambda-env.json
```

Allowlist secret `opsresolve/dev/whatsapp-allowlist` (`{"numbers": ["+91..."]}`) and the TECH-204 `phone` are set by 
only (the number is never shared with the agent). Without the secret every WhatsApp send is a DynamoDB-only record.

## Gateway Lambda (`OpsResolve-target-tools-lambda`): DEPLOYED 2026-10-12 (see WhatsApp above)

Code: `gateway tools\lambda_handler.py` (renamed from `lambda_handler_rcs.py`; the original is `lambda_handler_backup.py`).
The steps below are the original RCS hand-off notes, superseded by the WhatsApp deploy (handler, env and IAM are applied).

1. Handler: change to `lambda_handler_rcs.lambda_handler`. Upload the file as the function code.
2. Environment variables:
   - `RCS_SEND_ENABLED` = `false` (leave off; no test phone numbers. `true` makes real `SendRcsMessage` calls)
   - `RCS_AGENT_ID` = `rcs-24696a96570f4a60b41ba719424abf83`
   - `POSTBACK_KEY_SECRET_ID` = `opsresolve/dev/postback-hmac-key`
3. Add to role `OpsResolve-target-tools-lambda-role-542zgxm6`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": "sms-voice:SendRcsMessage",
      "Resource": "arn:aws:sms-voice:us-east-1:[AWS-ACCOUNT-NUMBER]:rcs-agent/rcs-24696a96570f4a60b41ba719424abf83" },
    { "Effect": "Allow", "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:us-east-1:[AWS-ACCOUNT-NUMBER]:secret:opsresolve/dev/postback-hmac-key-*" }
  ]
}
```

   Checked 2026-10-08: that role has ONLY `AWSLambdaBasicExecutionRole` (logging) attached and no inline policy, so it shows no
   DynamoDB access at all. Either the function runs under another role now, or its DynamoDB/S3/Bedrock calls are failing. Confirm
   with `aws lambda get-function-configuration --function-name OpsResolve-target-tools-lambda --query Role`. For
   `sendCustomerMessage` it needs `dynamodb:GetItem` on `opsresolve-dev-mock-contacts`, `PutItem` on `opsresolve-dev-conversations`
   and `GetItem`/`PutItem` on `opsresolve-dev-idempotency`.
4. Python 3.14 runtime: if the built-in boto3 lacks `send_rcs_message`, bundle `boto3>=1.43` with the code. Only matters once
   `RCS_SEND_ENABLED=true`.
