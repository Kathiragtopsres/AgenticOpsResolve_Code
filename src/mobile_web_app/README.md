# Mobile web app backend (downloaded from AWS, 2026-10-13)

Lambda code for the `/mobile-web-app/*` routes of API `opsresolve-dev-console-api` (`o3sve2isd6`), pulled with
`aws lambda get-function` and unzipped as deployed. Not built or owned by this repo's other code; edits here do not
deploy anything. Resource inventory: `resource.md`, section "Mobile web app exclusive resources".

| Folder | Lambda | Handler |
|---|---|---|
| `lambdas/field-read` | `opsresolve-dev-field-read` | `opsresolve.field_api.field_read.handler` |
| `lambdas/field-actions` | `opsresolve-dev-field-actions` | `opsresolve.field_api.field_actions.handler` |
| `lambdas/field-uploads` | `opsresolve-dev-field-uploads` | `opsresolve.field_api.field_uploads.handler` |
| `lambdas/field-assistant` | `opsresolve-dev-field-assistant` | `opsresolve.field_api.field_assistant.handler` |
| `lambdas/availability-slots` | `opsresolve-dev-availability-slots` | `lambda_function.lambda_handler` |

The four `field-*` zips each bundle the whole `opsresolve` package (`common/`, `engines/`, `runtime/`, `field_api/`), so the
files repeat across folders. `field-read` and `field-actions` carry the newer `field_actions.py`/`field_read.py`;
`field-uploads` and `field-assistant` carry older copies of those two files (their own handlers are identical).

## Local change not yet deployed (2026-10-13)

- `field-read/.../field_read.py`: `GET /mobile-web-app/jobs/{jobId}` now returns `partRequests[]` (the technician's
  `PART_REQUEST:<partId>` approvals with status PENDING/APPROVED/REJECTED, newest first). Needs `dynamodb:Scan` on
  `opsresolve-dev-approvals` for `opsresolve-dev-field-read-role` (`infra/mobile/policy-field-read.json`); without it the
  list is empty and a warning is logged. The `field-actions` copy of `field_read.py` is unused by that Lambda and was not edited.
