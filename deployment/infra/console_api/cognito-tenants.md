# Cognito + API authorizer changes for tenancy (Scenario 2)

Nothing here has been run. Every command changes live AWS;  runs them (or says "go") in Phase 8 step 9 (Cognito) and step 10 (mobile
routes). Region `us-east-1`. PowerShell syntax (use `;`, not `&&`).

## Read this first: custom attributes are irreversible

`add-custom-attributes` cannot be undone. A Cognito custom attribute can never be removed, renamed or have its type changed. This file adds exactly
**one** attribute to each pool, `custom:tenantId` (String, max 64). The mobile pool already has `custom:id` (the technician id), so no
`technicianId` attribute is added there (the console pool already has an unused `custom:technicianId`; it stays unused).

Other changes are reversible: user attributes (set them again), new users (delete them), the app-client write list (rollback file), route authorizers
(`--authorization-type NONE`).

## What the code expects (already written, Phases 5 and 6)

| Caller | Token | Claims read |
|---|---|---|
| Console (`console_api/*`) | ID token | `custom:tenantId` (403 without), `cognito:username` |
| Mobile (`/mobile-web-app/*`) | ID token | `custom:id` = technician id (401 without), `custom:tenantId` (assistant only), `cognito:username` |

Both clients keep `ReadAttributes` null (= every attribute readable), so the new attribute reaches the ID token without a client change.

## Pools, clients, users (from Phase 0.2)

| | Console | Mobile |
|---|---|---|
| Pool | `us-east-1_wJv8NbJQR` | `us-east-1_hZ90xjeek` |
| App client | `4hgmbfm02hnjv22bic3bdeu7gq` | `2dqi4dqd31bl2hhjg2k457rg7i` |
| Users now | `john_doe`, `sam_miller` | `sam_miller`, `ryan_kumar` (`custom:id` TECH-204), `samuel_menon` (`custom:id` TECH-311) |
| Authorizer on API `o3sve2isd6` | `odrce7` (console routes, already attached) | `1o2ait` `opsresolve-dev-field-cognito` (exists, attached to no route yet) |

## Step 1. Add the attribute (irreversible)

```powershell
aws cognito-idp add-custom-attributes --region us-east-1 --user-pool-id us-east-1_wJv8NbJQR --custom-attributes "Name=tenantId,AttributeDataType=String,Mutable=true,StringAttributeConstraints={MinLength=1,MaxLength=64}"
aws cognito-idp add-custom-attributes --region us-east-1 --user-pool-id us-east-1_hZ90xjeek --custom-attributes "Name=tenantId,AttributeDataType=String,Mutable=true,StringAttributeConstraints={MinLength=1,MaxLength=64}"
```

Blast radius: two pools gain one attribute. No user, token or client changes yet. Check: `aws cognito-idp describe-user-pool --user-pool-id <pool> --query "UserPool.SchemaAttributes[?Name=='custom:tenantId']"`.

## Step 2. Put the existing users in `meravix-foods`

Do this before step 4: after the Lambdas deploy, a console user without the attribute gets 403.

```powershell
foreach ($u in "john_doe","sam_miller") { aws cognito-idp admin-update-user-attributes --region us-east-1 --user-pool-id us-east-1_wJv8NbJQR --username $u --user-attributes Name=custom:tenantId,Value=meravix-foods }
foreach ($u in "sam_miller","ryan_kumar","samuel_menon") { aws cognito-idp admin-update-user-attributes --region us-east-1 --user-pool-id us-east-1_hZ90xjeek --username $u --user-attributes Name=custom:tenantId,Value=meravix-foods }
```

Existing sessions keep their old token until it expires (ID tokens carry the attribute from the time of login): users must log in again.
Mobile `sam_miller` may have no `custom:id`; check with `admin-get-user`. A mobile user without `custom:id` gets 401 on every call, so give them the
technician id they use, or leave that user unused.

## Step 3. New S2 users

The shared S2 demo password is not written to the repo: set it in your own shell first (`$env:S2_PW = "<the password>"`) and the commands read it.
Emails are `<firstname>.<secondname>@meravixbroadband.example`.

| Pool | Username | Role | Email | Attributes |
|---|---|---|---|---|
| Console | `nikhil_varma` | S2 service manager (the S1 equivalent is `john_doe`) | nikhil.varma@meravixbroadband.example | `custom:tenantId=meravix-broadband` |
| Console | `rohan_desai` | S2 admin (the S1 equivalent is `sam_miller`) | rohan.desai@meravixbroadband.example | `custom:tenantId=meravix-broadband` |
| Mobile | `arjun_iyer` | technician TEL-801 (A. Iyer; his phone gets the live job) | arjun.iyer@meravixbroadband.example | `custom:id=TEL-801`, `custom:tenantId=meravix-broadband` |
| Mobile | `rohan_desai` | S2 admin on the mobile app (acts as TEL-801, the one S2 technician with a login) | rohan.desai@meravixbroadband.example | `custom:id=TEL-801`, `custom:tenantId=meravix-broadband` |

`sam_miller` stays a `meravix-foods` user in both pools (no global-admin role: one tenant per user).

```powershell
$cp = "us-east-1_wJv8NbJQR"; $mp = "us-east-1_hZ90xjeek"; $t = "Name=custom:tenantId,Value=meravix-broadband"
# Console pool
aws cognito-idp admin-create-user --region us-east-1 --user-pool-id $cp --username nikhil_varma --message-action SUPPRESS --user-attributes Name=email,Value=nikhil.varma@meravixbroadband.example Name=email_verified,Value=true $t
aws cognito-idp admin-create-user --region us-east-1 --user-pool-id $cp --username rohan_desai --message-action SUPPRESS --user-attributes Name=email,Value=rohan.desai@meravixbroadband.example Name=email_verified,Value=true $t
# Mobile pool
aws cognito-idp admin-create-user --region us-east-1 --user-pool-id $mp --username arjun_iyer --message-action SUPPRESS --user-attributes Name=email,Value=arjun.iyer@meravixbroadband.example Name=email_verified,Value=true Name=custom:id,Value=TEL-801 $t
aws cognito-idp admin-create-user --region us-east-1 --user-pool-id $mp --username rohan_desai --message-action SUPPRESS --user-attributes Name=email,Value=rohan.desai@meravixbroadband.example Name=email_verified,Value=true Name=custom:id,Value=TEL-801 $t
# Passwords (permanent, so no forced change at first login)
foreach ($u in "nikhil_varma","rohan_desai") { aws cognito-idp admin-set-user-password --region us-east-1 --user-pool-id $cp --username $u --password $env:S2_PW --permanent }
foreach ($u in "arjun_iyer","rohan_desai") { aws cognito-idp admin-set-user-password --region us-east-1 --user-pool-id $mp --username $u --password $env:S2_PW --permanent }
```

Blast radius: four new users, nothing existing changes. Rollback: `aws cognito-idp admin-delete-user --user-pool-id <pool> --username <name>`.
The shared password is for the demo only; do not reuse it anywhere else.

## Step 4. Stop users writing their own tenant

Today both app clients have `WriteAttributes` null, which means every attribute is user-writable, including `custom:tenantId` and the mobile
`custom:id`. With an explicit list a user could still not change them. `update-user-pool-client` resets every setting you do not pass, so the
helper builds the update from the client's current configuration (read-only AWS calls) and prints the commands:

```powershell
python scripts/cognito_client_write_attributes.py --pool us-east-1_wJv8NbJQR --client 4hgmbfm02hnjv22bic3bdeu7gq --protect custom:tenantId --out .agents/backups/<ts>/cognito
python scripts/cognito_client_write_attributes.py --pool us-east-1_hZ90xjeek --client 2dqi4dqd31bl2hhjg2k457rg7i --protect custom:tenantId custom:id --out .agents/backups/<ts>/cognito
```

Read the printed "before / after" lists, then run the printed `aws cognito-idp update-user-pool-client ... --cli-input-json file://...-after.json`.
Blast radius: one app client per pool; sign-in and token settings are copied unchanged; the profile attributes a user may edit stay editable.
Rollback: the printed `...-rollback.json` command.

## Step 5. Mobile routes get the authorizer (Phase 7.2, together with the mobile Lambda deploy)

Order matters (D5). First deploy the 4 mobile Lambdas with the new `_common.py` and `availability-slots`, **then** attach the authorizer, **and** have
the mobile frontend dev ship the change that sends `Authorization: <ID token>` (login against pool `us-east-1_hZ90xjeek`). Between the attach and
that frontend change the current mobile app gets 401 on every call.

Route ids (Phase 0.3), all currently `AuthorizationType NONE`: `20czgij 3jjt1if 4c8ittf 4tvfwje 8d2artt afw578i ffghz04 h8c6b8m q9e0dwh uic2g76 unx2zb5 uzmuygt v67mer5 wtjrqw2 yy1ssfe` (15).

```powershell
$routes = "20czgij","3jjt1if","4c8ittf","4tvfwje","8d2artt","afw578i","ffghz04","h8c6b8m","q9e0dwh","uic2g76","unx2zb5","uzmuygt","v67mer5","wtjrqw2","yy1ssfe"
foreach ($r in $routes) { aws apigatewayv2 update-route --region us-east-1 --api-id o3sve2isd6 --route-id $r --authorization-type JWT --authorizer-id 1o2ait }
```

Does `update-route` keep the route's other settings? It changes only the passed fields. Check one route first with
`aws apigatewayv2 get-route --api-id o3sve2isd6 --route-id 3jjt1if`, and afterwards confirm all 15 show `AuthorizationType JWT`.
Rollback: the same loop with `--authorization-type NONE` (omit `--authorizer-id`).

## Order inside Phase 8

Step 1 (attributes), step 2 (existing users), step 3 (new users) and step 4 (write list) can run before any Lambda deploy: nothing reads the claims
yet. Deploy the console Lambdas only after steps 1-3, or the console returns 403 to users without the attribute. Step 5 goes last, with the mobile
Lambdas.

## Verify

```powershell
aws cognito-idp admin-get-user --region us-east-1 --user-pool-id us-east-1_wJv8NbJQR --username nikhil_varma --query "UserAttributes[?Name=='custom:tenantId']"
aws cognito-idp admin-get-user --region us-east-1 --user-pool-id us-east-1_hZ90xjeek --username arjun_iyer --query "UserAttributes[?Name=='custom:id' || Name=='custom:tenantId']"
```

After logging in, decode the ID token (header and payload only, never share the token) and check `custom:tenantId` (console) and `custom:id` +
`custom:tenantId` (mobile) are present.
