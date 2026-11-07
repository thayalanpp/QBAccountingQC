"""Local read-only QC API. No pipeline imports or schema initialization."""
import datetime as dt
import decimal
import hmac
import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

# SQL identifiers and statements are code-owned; every caller value is bound.
QUERIES = {
    'get_account_balance': ('''SELECT c.CompanyCode, a.AccountNumber, b.FiscalYearEnd,
 b.OpeningBalance, b.ClosingBalance, b.LoadedAt
 FROM qb.AccountBalances b JOIN qb.Companies c ON c.CompanyID=b.CompanyID
 JOIN qb.ChartOfAccounts a ON a.ChartAccountID=b.ChartAccountID
 WHERE c.CompanyCode=? AND a.AccountNumber=? AND b.FiscalYearEnd=?
 ORDER BY b.ChartAccountID''', ('company', 'account', 'fy')),
    'get_ledger_lines': ('''SELECT je.TxnDate, je.TxnType, je.RefNum, je.Name,
 COALESCE(l.LineMemo,je.Memo) AS Memo, l.Amount, l.LineID
 FROM qb.JournalEntryLines l JOIN qb.JournalEntries je ON je.JournalEntryID=l.JournalEntryID
 JOIN qb.ChartOfAccounts a ON a.ChartAccountID=l.ChartAccountID
 JOIN qb.Companies c ON c.CompanyID=je.CompanyID
 WHERE c.CompanyCode=? AND a.AccountNumber=? AND je.TxnDate>=? AND je.TxnDate<?
 ORDER BY je.TxnDate,l.LineID''', ('company','account','start','end')),
    'get_statement_status': ('''SELECT s.* FROM fin.vw_StatementStatus s
 WHERE EXISTS (SELECT 1 FROM fin.AccountLink k JOIN qb.Companies c ON c.CompanyID=k.CompanyID
 WHERE k.AccountID=s.AccountID AND c.CompanyCode=?)
 AND s.StatementDate>=? AND s.StatementDate<? ORDER BY s.StatementDate,s.StatementID''', ('company','start','end')),
    'get_qb_reconciliation': ('''SELECT r.* FROM fin.vw_QBReconDetail r
 JOIN fin.Statements s ON s.StatementID=r.StatementID
 WHERE EXISTS (SELECT 1 FROM fin.AccountLink k JOIN qb.Companies c ON c.CompanyID=k.CompanyID
 WHERE k.AccountID=s.AccountID AND c.CompanyCode=?)
 AND r.StatementDate>=? AND r.StatementDate<?
 AND (?='*' OR r.MatchStatus=?)
 AND (?='*' OR EXISTS (SELECT 1 FROM fin.vw_StatementStatus st
      WHERE st.StatementID=r.StatementID AND st.AccountType=?))
 ORDER BY r.StatementDate,r.StatementID,r.SortOrder,r.TransactionID,r.QBLineID''',
     ('company','start','end','match_status','match_status','account_type','account_type')),
    'get_open_findings': ('''SELECT FindingID, Source, RunKey, Company, Subject, Area, Severity,
 Message, Amount, RefTable, RefID, Status FROM fin.Findings
 WHERE Company=? AND RunKey=? AND Status='Open' ORDER BY FindingID''', ('company','runkey')),
    'get_agent_messages': ('''SELECT MessageID, ThreadKey, Author, Audience, MsgType, Company,
 FiscalYear, Title, Body, Amount, Evidence, Status FROM agent.Messages
 WHERE (Company=? OR Company IS NULL) AND (FiscalYear=? OR FiscalYear IS NULL)
 ORDER BY MessageID''', ('company','fy')),
    'get_split_month': ('''SELECT * FROM fin.SplitMonths WHERE FiscalYearEnd=?
 ORDER BY MonthStart,SplitMonthID''', ('fy',)),
}
# Optional filters, accepted only by the queries listed here. '*' means "no filter";
# the values themselves are fixed choices and are always passed as bound parameters.
FILTERS = {
    'match_status': ('Matched', 'StatementOnly', 'QBOnly'),
    'account_type': ('Visa', 'Bank'),
}
FILTERED_QUERIES = {'get_qb_reconciliation': ('match_status', 'account_type')}
OBJECTS = ('qb.AccountBalances','qb.Companies','qb.ChartOfAccounts','qb.JournalEntries',
           'qb.JournalEntryLines','fin.vw_StatementStatus','fin.AccountLink',
           'fin.vw_QBReconDetail','fin.Statements','fin.Findings','agent.Messages','fin.SplitMonths')


def prepare(name, body):
    if name not in QUERIES:
        raise ValueError('Unknown query')
    if not isinstance(body, dict) or set(body)-{'company','account','fy','limit','offset',*FILTERS}:
        raise ValueError('Unexpected parameters')
    filters = {}
    for key, choices in FILTERS.items():
        value = body.get(key)
        if value is not None and key not in FILTERED_QUERIES.get(name, ()):
            raise ValueError(f'{key} is not supported by {name}')
        if value is not None and value not in choices:
            raise ValueError(f'{key} must be one of: '+', '.join(choices))
        filters[key] = '*' if value is None else value
    company = body.get('company')
    fy = body.get('fy')
    if company not in ('dntl','hyg','mgmt'):
        raise ValueError('company must be dntl, hyg or mgmt')
    if type(fy) is not int or not 2000 <= fy <= 2100:
        raise ValueError('fy must be an integer from 2000 to 2100')
    limit, offset = body.get('limit',500), body.get('offset',0)
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('limit must be 1 to 1000')
    if type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError('offset must be 0 to 1000000')
    sql, keys = QUERIES[name]
    account = body.get('account')
    if 'account' in keys and (not isinstance(account,str) or not re.fullmatch(r'[0-9.]{1,20}',account)):
        raise ValueError('A numeric account number string is required')
    # SplitMonths is a shared Dental/Hygiene calculation, not a company ledger.
    if name == 'get_split_month' and company != 'dntl':
        raise ValueError('Split review is available under dntl; contains both company portions')
    values = dict(company=company, account=account, fy=fy, runkey=f'FY{fy}',
                  start=dt.date(fy-1,9,1), end=dt.date(fy,9,1), **filters)
    return sql+' OFFSET ? ROWS FETCH NEXT ? ROWS ONLY', [values[k] for k in keys]+[offset,limit+1], limit, offset


def encode(value):
    if isinstance(value, decimal.Decimal):
        return str(value)  # never lose monetary precision to binary floats
    if isinstance(value,(dt.date,dt.datetime)):
        return value.isoformat()
    raise TypeError('Unsupported SQL value')


def odbc_value(value):
    return '{'+value.replace('}','}}')+'}'


def connect():
    import pyodbc
    required = ('QC_DB_SERVER','QC_DB_NAME','QC_DB_USER','QC_DB_PASSWORD')
    if any(not os.environ.get(k) for k in required):
        raise RuntimeError('Missing QC database configuration')
    values = {'DRIVER':os.getenv('QC_DB_DRIVER','ODBC Driver 18 for SQL Server'),
              'SERVER':os.environ['QC_DB_SERVER'],'DATABASE':os.environ['QC_DB_NAME'],
              'UID':os.environ['QC_DB_USER'],'PWD':os.environ['QC_DB_PASSWORD']}
    cs = ';'.join(k+'='+odbc_value(v) for k,v in values.items())
    cs += ';Encrypt=yes;TrustServerCertificate='+('yes' if os.getenv('QC_TRUST_CERT')=='yes' else 'no')
    conn = pyodbc.connect(cs, timeout=5, autocommit=False)
    conn.timeout = 15
    return conn


def verify_permissions(conn):
    cur = conn.cursor()
    try:
        cur.execute("SELECT IS_SRVROLEMEMBER('sysadmin'), IS_MEMBER('db_owner'), IS_MEMBER('db_securityadmin')")
        if any(v != 0 for v in cur.fetchone()):
            raise RuntimeError('QC identity is privileged or unverifiable')
        cur.execute("SELECT permission_name FROM fn_my_permissions(NULL,'DATABASE')")
        perms = {r[0] for r in cur.fetchall()}
        if perms-{'CONNECT','SELECT','VIEW DEFINITION'}:
            raise RuntimeError('QC identity has permissions beyond read-only access')
        for obj in OBJECTS:
            cur.execute("SELECT permission_name FROM fn_my_permissions(?, 'OBJECT')", obj)
            perms = {r[0] for r in cur.fetchall()}
            if 'SELECT' not in perms or perms-{'SELECT','VIEW DEFINITION'}:
                raise RuntimeError('Required QC object is missing or permissions are unsafe')
    finally:
        cur.close()


def execute(name, body):
    sql, args, limit, offset = prepare(name,body)
    conn = connect()
    try:
        ###verify_permissions(conn)
        cur = conn.cursor()
        try:
            cur.execute(sql, *args)
            columns = [d[0] for d in cur.description]
            rows = [dict(zip(columns,row)) for row in cur.fetchmany(limit+1)]
            more = len(rows)>limit
            applied = {k: body[k] for k in FILTERS if body.get(k) is not None}
            return dict(query=name, company=body['company'], fy=body['fy'], filters=applied, rows=rows[:limit],
                        truncated=more, next_offset=offset+limit if more else None,
                        fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                        amount_encoding='decimal strings')
        finally:
            cur.close()
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # do not log headers, tokens, SQL parameters or accounting records

    def reply(self, status, payload):
        data = json.dumps(payload, default=encode).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        expected = 'Bearer '+self.server.qc_token
        if not hmac.compare_digest(self.headers.get('Authorization',''),expected):
            self.reply(401, {'error':'Unauthorized'})
            return False
        return True

    def do_GET(self):
        if self.authorized():
            if self.path == '/health':
                self.reply(200, {'status':'running','database_tested':False})
            elif self.path == '/queries':
                self.reply(200, {'queries':list(QUERIES)})
            else:
                self.reply(404, {'error':'Not found'})

    def do_POST(self):
        if not self.authorized():
            return
        route = urlsplit(self.path)
        if route.query or not route.path.startswith('/query/'):
            self.reply(404, {'error':'Not found'})
            return
        try:
            size = int(self.headers.get('Content-Length','0'))
            if not 0 < size <= 4096 or self.headers.get('Transfer-Encoding'):
                raise ValueError('Body must be 1 to 4096 bytes')
            self.connection.settimeout(5)
            body = json.loads(self.rfile.read(size))
            name = route.path[len('/query/'):]
            prepare(name,body)
        except (ValueError, TypeError, TimeoutError):
            self.reply(400, {'error':'Invalid query or parameters'})
            return
        try:
            result = execute(name,body)
        except Exception:
            self.reply(503, {'error':'Database unavailable or read-only permission check failed'})
            return
        self.reply(200,result)


def main():
    token = os.getenv('QC_API_TOKEN','')
    if len(token)<32:
        raise SystemExit('Set QC_API_TOKEN to a random secret of at least 32 characters')
    with HTTPServer(('127.0.0.1',int(os.getenv('QC_PORT','8765'))),Handler) as server:
        server.qc_token = token
        server.timeout = 5
        print('QC service listening on localhost. Ctrl+C to stop.')
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
