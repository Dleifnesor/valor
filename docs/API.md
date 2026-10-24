# VALOR web API

The web UI is a client of this API; scripts can use it the same way. For CI pipelines the `valor` CLI inside the
VALOR VM is usually simpler ([CLI.md](CLI.md)).

The source of truth for who may call what is the `ACCESS` table in
[`tests/test_security.py`](../tests/test_security.py): a new route fails the tests until it is added there. This
page follows that table.

## Conventions

- **Base URL**: `https://<valor-vm>/api/`. There is no OpenAPI page (it is turned off on purpose).
- **Sign-in** makes a session cookie (`__Host-valor`: HttpOnly, Secure, SameSite=Strict). Sessions have stages: after
  the password the session is in stage `mfa` (or `enroll` at the first sign-in), and only after the TOTP code is it
  `full`. Every route except sign-in and `/api/health` needs a `full` session.
- **CSRF**: `POST /api/auth/login` returns a `csrf` value; `/mfa` and `/enroll/confirm` start a new session and
  return a new one (so does `GET /api/auth/me`). Send the current value as the `X-CSRF-Token` header on every
  `POST`, `PUT`, `PATCH` and `DELETE`, including the MFA step.
- **State changes** must be same-origin (`Origin` / `Sec-Fetch-Site` are checked) and carry a JSON body
  (`Content-Type: application/json`). The one exception is the ISO upload, whose body is the file.
- **Errors** are JSON: `{"error": "<code>", "message": "...", ...}`, with `step`, `host`, `details`, `hint` and
  `completed_steps` when the engine reports them. Common statuses: 400 engine error, 401 not signed in or MFA
  missing, 403 wrong role or cross-origin, 409 busy or stale (`plan_changed`, `preview_changed`), 415 not JSON,
  422 invalid request (`details` lists the fields), 429 rate limited.
- **Jobs**: routes that change the cluster return `{"job": "<id>", ...}` at once. Poll `GET /api/jobs/<id>` for the
  state, the events and the result.
- **Audit**: every state change and every view of a secret is written to the audit log.

## Access levels

| Level | Meaning |
|---|---|
| public | No session needed. |
| stage | A session in the named sign-in stage (`mfa` or `enroll`). |
| session | Any signed-in session. |
| viewer / operator / admin | A full session with at least this role (admin > operator > viewer). |

## Routes

### Sign-in and account

| Method and path | Access | What it does |
|---|---|---|
| `POST /api/auth/login` | public | Username and password (local or LDAP). Rate-limited per client address and per account. |
| `POST /api/auth/mfa` | stage `mfa` | TOTP code or recovery code; the session becomes `full`. |
| `POST /api/auth/enroll/start` | stage `enroll` | A new TOTP secret and its QR code (first sign-in, or after an MFA reset). |
| `POST /api/auth/enroll/confirm` | stage `enroll` | Confirms the first code; returns the recovery codes. |
| `GET /api/auth/me` | session | The signed-in user, the session stage, the CSRF token, the version. |
| `POST /api/auth/logout` | session | Ends the session. |
| `POST /api/auth/password` | viewer | Change your own password (also allowed while a password change is required). |
| `POST /api/auth/recovery-codes` | viewer | New recovery codes (needs the current password). |
| `GET /api/notifications` | viewer | Your in-app notifications. |
| `POST /api/notifications/read` | viewer | Mark notifications read. |

### Health and cluster

| Method and path | Access | What it does |
|---|---|---|
| `GET /api/health` | public | Liveness only: `{"ok": true}`. |
| `GET /api/status` | viewer | The health checks and cluster facts shown on the dashboard. |
| `GET /api/catalog` | viewer | What specs can use on this cluster: OS templates, roles (with parameters), baselines, VLANs in use, reserved networks. |

### Ranges, plans and builds

| Method and path | Access | What it does |
|---|---|---|
| `GET /api/ranges` | viewer | Every range with its last build and verification. |
| `GET /api/ranges/{name}` | viewer | One range: spec (YAML and parsed), version, map, VMs, tests, last verification, history, journal. |
| `POST /api/specs/check` | operator | Validate a spec that is not saved (schema and cluster) and return its map. |
| `PUT /api/ranges/{name}/spec` | operator | Save a spec. |
| `DELETE /api/ranges/{name}/spec` | operator | Delete a spec (the range's VMs must be gone). |
| `POST /api/ranges/{name}/plan` | operator | The plan (or the draft's plan with `draft: true`) and its `plan_hash`. |
| `POST /api/ranges/{name}/apply` | operator | Build. Send the `plan_hash` you approved; `409 plan_changed` if the plan differs now. With `draft: true` the draft becomes the spec first. |
| `POST /api/ranges/{name}/verify` | operator | Run the verification again. |
| `POST /api/ranges/{name}/destroy` | operator | Delete the range's VMs (needs the range name as confirmation). |
| `GET /api/jobs` | viewer | Recent jobs. |
| `GET /api/jobs/{job_id}` | viewer | One job: state, events (`tail` limits them), result. |

### Drafts (editing a built range)

| Method and path | Access | What it does |
|---|---|---|
| `GET /api/ranges/{name}/draft` | viewer | The draft, what it changes, and its map. |
| `PUT /api/ranges/{name}/draft` | operator | Replace the draft with a full spec (YAML). |
| `POST /api/ranges/{name}/draft/ops` | operator | Map edits (add/remove VMs, services, segments, rules) applied to the draft. |
| `GET /api/ranges/{name}/draft/suggest-segment` | operator | A free VLAN and private network for a new segment. |
| `DELETE /api/ranges/{name}/draft` | operator | Discard the draft. |

### Using a range

| Method and path | Access | What it does |
|---|---|---|
| `GET /api/ranges/{name}/credentials` | operator | The range login (user names and password). Audited. |
| `POST /api/ranges/{name}/credentials/rotate` | operator | New range password, set on every VM (job). |
| `POST /api/ranges/{name}/power` | operator | `start`, `shutdown`, `reboot` or `stop`, for the range or some hosts (job). |
| `GET /api/ranges/{name}/snapshots` | viewer | Snapshots across the range's VMs. |
| `POST /api/ranges/{name}/snapshots` | operator | Take a snapshot of every VM (job). |
| `POST /api/ranges/{name}/snapshots/{snap}/rollback` | operator | Roll every VM back and verify (job). `delete_newer` where the storage needs it. |
| `DELETE /api/ranges/{name}/snapshots/{snap}` | operator | Delete a snapshot (job). |
| `POST /api/ranges/{name}/vms/{host}/console` | operator | A one-time console ticket (valid 60 s, bound to your session). Audited. |
| `WS /api/console/{sid}` | public* | The console websocket. *The ticket and the session cookie are checked in the handler. |
| `GET /api/ranges/{name}/wireguard` | viewer | WireGuard peers with their last handshake. |
| `GET /api/ranges/{name}/wireguard/peers/{peer}` | operator | A peer's config and QR code. Audited. |
| `POST /api/ranges/{name}/wireguard/peers/{peer}/rotate` | operator | New keys for one peer (job). |

### ISO library

| Method and path | Access | What it does |
|---|---|---|
| `GET /api/isos` | viewer | Media on the ISO storages VALOR may list. |
| `GET /api/isos/catalog` | viewer | The catalog of official media. |
| `POST /api/isos/download` | operator | Download a catalog entry, or a URL with an optional checksum, as a Proxmox task. |
| `GET /api/isos/tasks` | viewer | Running downloads and uploads. |
| `POST /api/isos/upload` | operator | Upload: the request body is the ISO; `filename`, `checksum` and `algorithm` are query parameters. |
| `DELETE /api/isos/{volid}` | admin | Delete a medium. |

### Blueprints

| Method and path | Access | What it does |
|---|---|---|
| `GET /api/blueprints` | viewer | Blueprints and their copies. |
| `GET /api/blueprints/{bp_id}` | viewer | One blueprint. |
| `PUT /api/blueprints/{bp_id}` | operator | Save a blueprint (a spec). |
| `POST /api/blueprints/from-range` | operator | A blueprint from a range's spec. |
| `DELETE /api/blueprints/{bp_id}` | operator | Delete a blueprint (its copies stay). |
| `POST /api/blueprints/{bp_id}/preview` | operator | The copies (names, VLANs, networks, plans) and their `deploy_hash`. |
| `POST /api/blueprints/{bp_id}/deploy` | operator | Write the previewed copies (and build them with `build: true`). `409 preview_changed` if they differ now. |

### Chats and AI

| Method and path | Access | What it does |
|---|---|---|
| `GET /api/builder` | operator | The AI providers chats may use and the default (no secrets). |
| `POST /api/builder/chat` | operator | One stateless chat-builder exchange (messages in, validated spec out). The UI uses the saved chats below. |
| `GET /api/chats` | viewer | Your chats (`scope=all` for admins), filtered by `kind` (`builder` / `range`) and `range`. |
| `POST /api/chats` | operator | A new chat (builder, or for a range). |
| `GET /api/chats/{chat_id}` | viewer | A chat with its messages (owner or admin). |
| `PATCH /api/chats/{chat_id}` | operator | Rename, choose the provider, `allow_all`. |
| `DELETE /api/chats/{chat_id}` | operator | Delete a chat. |
| `POST /api/chats/{chat_id}/messages` | operator | Send a message with a `mode` (`build`, `question`, `plan`, `code`); the answer runs in the background. |
| `GET /api/chats/{chat_id}/messages/{msg_id}` | viewer | Poll an answer: its live feed (`meta.steps`) and state. |
| `POST /api/chats/{chat_id}/messages/{msg_id}/decide` | operator | Approve or deny the waiting tool calls (and optionally allow all for this chat). |
| `POST /api/chats/{chat_id}/messages/{msg_id}/stop` | operator | Stop a running answer. |
| `POST /api/jobs/{job_id}/fix` | operator | Hand a failed build to the assistant (or open the chat already working on it). |
| `GET /api/jobs/{job_id}/fix` | viewer | The chats working on this build that you may open. |

### Administration

| Method and path | Access | What it does |
|---|---|---|
| `GET /api/users` | admin | Accounts. |
| `POST /api/users` | admin | Create an account (the user chooses a new password at the first sign-in). |
| `PATCH /api/users/{uid}` | admin | Change display name, email or role; disable or enable. |
| `POST /api/users/{uid}/unlock` | admin | Clear a lockout. |
| `POST /api/users/{uid}/reset-mfa` | admin | The user sets up MFA again at the next sign-in. |
| `POST /api/users/{uid}/reset-password` | admin | Set a temporary password. |
| `DELETE /api/users/{uid}` | admin | Delete an account. |
| `GET /api/audit` | admin | The audit log (`q` searches, `before` pages). |
| `GET`, `PUT /api/settings/notifications` | admin | Notification channels (secrets come back masked). |
| `POST /api/settings/notifications/test` | admin | Send a test message. |
| `GET`, `PUT /api/settings/ldap` | admin | LDAP / Active Directory settings. |
| `POST /api/settings/ldap/test` | admin | Test a user lookup. |
| `GET`, `PUT /api/settings/ai` | admin | AI providers, the default, budgets, failed-build handling. Keys are never returned. |
| `POST /api/settings/ai/test` | admin | Send a test prompt to a provider. |
| `GET /api/settings/ai/usage` | admin | AI tokens per user and day. |

## Example: build a range from a script

```bash
VALOR=https://valor.example.lan
jar=$(mktemp)
c() { curl -s --cacert valor-ca.crt -b "$jar" -c "$jar" -H 'Content-Type: application/json' "$@"; }

csrf=$(c -X POST "$VALOR/api/auth/login" -d '{"username": "ci", "password": "..."}' | jq -r .csrf)
csrf=$(c -H "X-CSRF-Token: $csrf" -X POST "$VALOR/api/auth/mfa" -d '{"code": "123456"}' | jq -r .csrf)
h=(-H "X-CSRF-Token: $csrf")

c "${h[@]}" -X PUT "$VALOR/api/ranges/web2tier/spec" --data-binary @<(jq -Rs '{yaml: .}' web2tier.yaml)
hash=$(c "${h[@]}" -X POST "$VALOR/api/ranges/web2tier/plan" -d '{}' | jq -r .plan_hash)
job=$(c "${h[@]}" -X POST "$VALOR/api/ranges/web2tier/apply" -d "{\"plan_hash\": \"$hash\"}" | jq -r .job)
c "$VALOR/api/jobs/$job" | jq '.state, .result.ok'
```

Every account needs MFA, so unattended pipelines are usually better served by the `valor` CLI inside the VALOR VM
([CLI.md](CLI.md#valor)).
