# QBAccounting local QC service

A localhost HTTP reader for the existing database. It never calls pipeline helpers,
initializes schema, posts messages, approves entries, or executes caller SQL.
All request values use ODBC bound parameters. SQL SELECT permissions enforce the
read-only boundary; ApplicationIntent alone would not enforce it.

## Windows setup

1. In SSMS, create a **new SQL authentication login** `qb_qc_reader`, using a unique
   strong password and no server roles. SQL Server must allow SQL authentication.
   Run `sql/qc_readonly_setup.sql` as administrator against QBAccounting.
   The verification must show CanRead=1 and all write columns=0.
   Do not assign db_owner, db_datawriter, or other roles to this identity.
2. Open PowerShell in the repository. Install Microsoft ODBC Driver 18 for SQL
   Server if missing, then install the small separate dependency set:

   ```powershell
   python -m pip install -r qc_service/requirements.txt
   $env:QC_DB_SERVER = 'NilashaAI'
   $env:QC_DB_NAME = 'QBAccounting'
   $env:QC_DB_USER = 'qb_qc_reader'
   $secret = Read-Host 'QC SQL password' -AsSecureString
   $env:QC_DB_PASSWORD = [System.Net.NetworkCredential]::new('', $secret).Password
   $env:QC_API_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
   # Local SQL Server often uses a self-signed certificate.
   # Only for your local server, if certificate validation fails:
   $env:QC_TRUST_CERT = 'yes'
   python -m qc_service.service
   ```

   Uses only QC_* variables, never your existing pipeline .env. Keep this terminal
   running. Do not paste secrets into chat, GitHub, or tracked files. If saving a
   local startup script, use a *.local.ps1 filename (git-ignored).
3. In a second PowerShell window set the **same** QC_API_TOKEN securely (for
   example from your password manager), then test:

   ```powershell
   $token = Read-Host 'QC API token' -AsSecureString
   $headers = @{ Authorization = 'Bearer ' + [System.Net.NetworkCredential]::new('', $token).Password }
   Invoke-RestMethod http://127.0.0.1:8765/queries -Headers $headers
   $body = @{company='dntl';account='22200';fy=2025;limit=100;offset=0} | ConvertTo-Json
   Invoke-RestMethod http://127.0.0.1:8765/query/get_account_balance -Method Post -Headers $headers -ContentType 'application/json' -Body $body
   ```

   The generated API token exists in the first terminal's environment. To transfer
   it locally you can copy `$env:QC_API_TOKEN` into a password manager; keep it out
   of shared screenshots and command history. `/health` confirms HTTP only. The
   first successful query confirms SQL access and permission checks. A 503 means
   configuration, authentication, permissions, missing schema objects, or SQL
   availability needs attention; raw database errors are deliberately withheld.

## Operations

POST `/query/<name>` with JSON company (`dntl`, `hyg`, `mgmt`), fy (year ending
Aug 31), optional limit (1–1000, default 500), offset (default 0). Account-based
operations also require account as a string, preserving `1010.25`.

| Name | Scope |
|---|---|
| get_account_balance | GL opening/closing checkpoints for company/account/FY |
| get_ledger_lines | Company/account ledger, Sep 1 through Aug 31 |
| get_statement_status | Linked company statements whose statement date falls in FY |
| get_qb_reconciliation | Linked company reconciliation rows by statement date |
| get_open_findings | Open company findings with RunKey FYyyyy |
| get_agent_messages | Company and shared messages, including undated messages |
| get_split_month | Shared Dental/Hygiene split calculation, requested as dntl |

`get_qb_reconciliation` also takes two optional filters (omit either one for all rows):

| Filter | Values |
|---|---|
| match_status | `Matched`, `StatementOnly` (on the statement, not in QB), `QBOnly` (in QB, not on a statement) |
| account_type | `Visa`, `Bank` |

Example: unposted card lines for Dental FY2024 =
`{"company":"dntl","fy":2024,"match_status":"StatementOnly","account_type":"Visa"}`.
Filter values are fixed choices sent as bound parameters; the account type comes
from `fin.vw_StatementStatus`, so no new SQL grant is needed.

Decimals return as strings; dates use ISO format. `truncated=true` and
`next_offset` indicate another page. Request pages while pipeline reloads are
paused; separate requests do not share a database snapshot. Empty results do not
prove completeness: statements need AccountLink, findings need the FY RunKey,
and AccountBalances must be refreshed using load_gl_balances.py after QB reload.
This service does not recompute or claim to verify the 22200 roll-forward yet;
use balances plus ledger evidence. No financial records are included in source.

## Tests and connectivity

Run `python -m unittest discover -s tests -p test_qc_service.py`.
Unit/HTTP tests use mocks and do not connect to your PC's SQL Server.
Startup and every query require a dedicated unprivileged identity; connection
and query timeouts are bounded, and all query connections are rolled back/closed.

This is a small serialized **local** API, not an internet production server.
It binds only to 127.0.0.1 and supports Bearer authentication without CORS.
It does not make your PC reachable from ChatGPT. The next stage needs a supported
connector/MCP adapter and authenticated HTTPS transport or a local Codex client.
Do not forward the SQL Server port or publish this HTTP listener as-is. Until an
adapter is configured, local agents can use it, and QC results can be exported
and supplied to this chat. This version does not isolate companies by token;
the QC token can request all three companies. Company-specific agent identities
and authorization belong in the next phase.
