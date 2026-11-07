import datetime
import decimal
import http.client
import json
import threading
import unittest
from unittest.mock import patch
from http.server import HTTPServer
from qc_service import service as s

class QueryTests(unittest.TestCase):
    def test_every_query_is_select_and_bounded(self):
        for name in s.QUERIES:
            sql,args,limit,offset=s.prepare(name,dict(company='dntl',account='22200',fy=2025))
            self.assertTrue(sql.startswith('SELECT'))
            self.assertEqual(args[-2:],[0,501])
            self.assertIn('FETCH NEXT ? ROWS ONLY',sql)
            self.assertEqual(sql.count('?'),len(args))
    def test_fiscal_boundary(self):
        _,args,_,_=s.prepare('get_ledger_lines',dict(company='hyg',account='1010.25',fy=2025))
        self.assertEqual(args[2:4],[datetime.date(2024,9,1),datetime.date(2025,9,1)])
    def test_bad_requests(self):
        base=dict(company='dntl',account='22200',fy=2025)
        for change in [dict(sql='DELETE'),dict(company='evil'),dict(account="22200'; DELETE"),
                       dict(fy=True),dict(limit=1001),dict(offset=-1)]:
            with self.assertRaises(ValueError): s.prepare('get_ledger_lines',base|change)
        with self.assertRaises(ValueError): s.prepare('arbitrary_sql',base)
    def test_recon_filters_are_bound(self):
        base=dict(company='dntl',fy=2024)
        sql,args,_,_=s.prepare('get_qb_reconciliation',base)
        self.assertEqual(args[3:7],['*','*','*','*'])  # no filter by default
        sql,args,_,_=s.prepare('get_qb_reconciliation',base|dict(match_status='StatementOnly',account_type='Visa'))
        self.assertEqual(args[3:7],['StatementOnly','StatementOnly','Visa','Visa'])
        self.assertEqual(sql.count('?'),len(args))
        self.assertNotIn('StatementOnly',sql); self.assertNotIn('Visa',sql)
    def test_recon_filter_rejections(self):
        base=dict(company='dntl',fy=2024)
        for change in [dict(match_status='Matched; DROP'),dict(account_type='visa'),dict(match_status=1)]:
            with self.assertRaises(ValueError): s.prepare('get_qb_reconciliation',base|change)
        with self.assertRaises(ValueError):  # filters only where supported
            s.prepare('get_ledger_lines',base|dict(account='22200',match_status='Matched'))
    def test_exact_money(self):
        self.assertEqual(json.loads(json.dumps({'amount':decimal.Decimal('1600.96')},default=s.encode)),{'amount':'1600.96'})
    def test_odbc_escape(self):
        self.assertEqual(s.odbc_value('abc};PWD=evil'),'{abc}};PWD=evil}')
    def test_cleanup_on_permission_failure(self):
        from unittest.mock import Mock
        conn=Mock()
        with patch.object(s,'connect',return_value=conn),patch.object(s,'verify_permissions',side_effect=RuntimeError):
            with self.assertRaises(RuntimeError): s.execute('get_account_balance',dict(company='dntl',account='22200',fy=2025))
        conn.rollback.assert_called_once()
        conn.close.assert_called_once()
    def test_privileged_identity_rejected(self):
        from unittest.mock import Mock
        conn=Mock(); conn.cursor.return_value.fetchone.return_value=(1,0,0)
        with self.assertRaises(RuntimeError): s.verify_permissions(conn)

class HttpTests(unittest.TestCase):
    def setUp(self):
        self.server=HTTPServer(('127.0.0.1',0),s.Handler)
        self.server.qc_token='x'*40
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
    def request(self,path,body=None,token=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=2)
        headers={} if token is None else {'Authorization':'Bearer '+token}
        conn.request('POST' if body is not None else 'GET',path,body,headers)
        response=conn.getresponse(); data=json.loads(response.read()); conn.close()
        return response.status,data
    def test_auth(self):
        self.assertEqual(self.request('/queries')[0],401)
        self.assertEqual(self.request('/queries',token='wrong')[0],401)
        self.assertEqual(self.request('/queries',token='x'*40)[0],200)
    def test_reject_sql_without_connecting(self):
        with patch.object(s,'connect') as connect:
            status,_=self.request('/query/get_ledger_lines',json.dumps(dict(company='dntl',fy=2025,sql='DROP TABLE')), 'x'*40)
            self.assertEqual(status,400); connect.assert_not_called()
    def test_redacted_db_failure(self):
        with patch.object(s,'execute',side_effect=RuntimeError('secret password')):
            status,data=self.request('/query/get_account_balance',json.dumps(dict(company='dntl',fy=2025,account='22200')),'x'*40)
            self.assertEqual(status,503); self.assertNotIn('secret',str(data))

if __name__=='__main__': unittest.main()
