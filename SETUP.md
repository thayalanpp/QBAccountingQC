# Database exploration addon for QBAccountingQC

Adds list_tables, describe_table, read_table and query_readonly. Existing service.py,
mcp_adapter.py, startup script and seven tools are preserved. Extract into
C:\NM\QBAccountingQC, merging qc_service/, tests/ and sql/ folders.

## 1. Install dependencies in the QC project's venv

```powershell
python -m pip install -r qc_service/requirements-explorer.txt
python -m unittest discover -s tests -p 'test_explorer*.py'
```

## 2. Configure a dedicated exploration identity

The old named queries may use your existing QC_DB_USER and QC_DB_PASSWORD.
The four general tools need a SELECT-only identity. Do not disable this check:
SQL parsing is an extra restriction, not a substitute for SQL Server permissions.

In SSMS create SQL login qb_qc_explorer with a new password, no server roles other
than public. Then run sql/explorer_reader.sql as administrator. The SQL file
creates the database user, grants SELECT/metadata access, and denies writes.
It does not alter accounting records. Do not paste the password into chat.
This login reads all database tables; the API exposes fin,qb,agent,dbo by default.
Additional schemas require QC_EXP_SCHEMAS in the listener environment.

Alternatively an existing SELECT-only identity such as qb_agent_sql can be used
through QC_EXP_DB_USER / QC_EXP_DB_PASSWORD when launching manually.
Permission checks reject sysadmin, db_owner, other elevated effective permissions,
and object-level write/execute grants. If checks fail, inspect the login's grants;
there is no permission-bypass switch for general tools.

## 3. Create a separate tunnel profile (same existing tunnel)

Stop the old QC listener and old tunnel before starting the new pair. Do not run
two profiles against the same tunnel concurrently. In C:\NM\QBAccountingQC:

```powershell
.\tunnel-client\tunnel-client.exe init --sample sample_mcp_stdio_local --profile qb-qc-explorer --tunnel-id tunnel_6acc1d2c51e08191b142a1bc0a9d2720 --mcp-command "C:/NM/QBAccountingQC/.venv/Scripts/python.exe C:/NM/QBAccountingQC/qc_service/explorer_mcp.py"
```

This uses the new explorer_mcp.py entry point, which imports your existing adapter
and adds the four tools. The original qb-qc profile is kept for rollback.

## 4. Start

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Start-QBAccountingQC-Explorer.ps1
```

The script prompts for the exploration SQL password, existing QC SQL password and
OpenAI runtime API key. It creates a fresh matching QC token, tests both SQL
paths, starts explorer_service on localhost:8765, then runs qb-qc-explorer.
Keep it running; Ctrl+C ends the tunnel and cleans up its listener.
Check http://127.0.0.1:8080/ui for ready. No credentials are saved to disk.
The existing saved profile must be initialized first in step 3.

Manual listener entry point: python -m qc_service.explorer_service
Manual tunnel: .\tunnel-client\tunnel-client.exe run --profile qb-qc-explorer
They must inherit the same QC_API_TOKEN.

Refresh/reconnect the existing ChatGPT custom MCP connection if it caches the
old seven-tool list. Verify it now discovers 11 tools. No new tunnel ID is needed.
Use explorer_mcp.py for other local MCP clients too.

## Usage

list_tables: optional schema, limit (1..1000, default 200), offset (0..100000).
Only accessible user tables/views in enabled schemas are returned. SQL permissions
and metadata visibility affect discovery; results are not a DBA-wide inventory.

Example describe_table arguments:
```json
{"schema":"fin","table":"Transactions"}
```
Columns include SQL type, size in bytes, precision/scale, nullability, default,
identity/computed flags. Keys report primary and unique indexes. Relationships
report declared inbound/outbound foreign keys; views and unenforced logical
relationships may have no declared keys.

Example read_table arguments:
```json
{"schema":"fin","table":"Transactions","columns":["TransactionID","AccountID","TransactionDate","Amount"],"filters":[{"column":"AccountID","op":"eq","value":4},{"column":"TransactionDate","op":"ge","value":"2024-09-01"},{"column":"TransactionDate","op":"lt","value":"2025-09-01"}],"order_by":["TransactionID"],"limit":200,"offset":0}
```
AccountID 4 is an example: discover/verify actual account metadata before analysis.
Filters are ANDed; operators eq,ne,lt,le,gt,ge,like,is_null,not_null. Scalar values
are bound with ODBC parameters. Identifiers must match accessible catalog columns.
Order is ascending. A primary key is appended when present. Views/no-PK tables
require caller-supplied ordering with a unique tie-breaker.

Example query_readonly arguments:
```json
{"sql":"SELECT AccountID, COUNT(*) AS RowCount, SUM(Amount) AS Total FROM fin.Transactions GROUP BY AccountID ORDER BY AccountID","limit":200,"offset":0}
```
Supports one parsed T-SQL SELECT, including CTEs, joins, aggregates and unions.
Table names must be schema-qualified. Use unique aliases for all result columns.
Rejects writes, SELECT INTO, multiple statements, external/cross-database references,
UDFs/table functions, locking hints and unsupported constructs. It is intentionally
conservative: valid SQL may be rejected rather than executed without validation.
For unsupported SQL, revise the query or add a reviewed named tool.

Money is returned as decimal strings and dates as ISO strings. Follow next_offset
until null; truncated indicates more rows, not a complete total. Queries use a
15-second SQL timeout, maximum 1000 returned rows, and a 4-MiB response limit.
ROWCOUNT bounds rows emitted, not intermediate work or SQL Server memory usage.
Separate pages are not a shared snapshot. Pause source reloads while paging.

The general tools do NOT automatically filter by company, FY, travel exclusions
or accounting materiality. Specify those explicitly. Discovery provides schema,
not accounting interpretation. The owner-wide token can access all enabled
companies. Future company agents need authorization in the runner or separate
company-scoped views/identities; a prompt alone is not an access boundary.

## Rollback

Stop the new tunnel/listener. Start the original Start-QBAccountingQC.ps1 or
original listener and qb-qc tunnel profile. Existing source was not overwritten.
The new SQL login can remain unused; remove it separately if desired.

## Validation performed

14 local tests cover SQL rejection, CTE/joins, bound filter values, identifiers,
pagination, elevated identity rejection, HTTP auth/error redaction and forwarding.
An actual stdio MCP client test found 11 tools and called all four new tools
against a mocked HTTP backend. No live SQL Server catalog query or Windows
PowerShell launcher was executed here. Those checks occur on your PC at startup.
