import http.client
import json
import threading
import unittest
from unittest.mock import patch
from http.server import HTTPServer
from qc_service.explorer_service import Handler
from qc_service import explorer

class HttpTests(unittest.TestCase):
    def setUp(self):
        self.s=HTTPServer(('127.0.0.1',0),Handler);self.s.qc_token='test-only-token-12345678901234567890'
        self.t=threading.Thread(target=self.s.serve_forever,daemon=True);self.t.start()
    def tearDown(self):
        self.s.shutdown();self.s.server_close();self.t.join()
    def post(self,token,body):
        c=http.client.HTTPConnection('127.0.0.1',self.s.server_port)
        c.request('POST','/query/list_tables',json.dumps(body),{'Authorization':'Bearer '+token})
        r=c.getresponse();status=r.status;data=json.loads(r.read());c.close();return status,data
    def test_missing_auth_never_calls_db(self):
        with patch.object(explorer,'execute') as run:
            self.assertEqual(self.post('bad',{})[0],401);run.assert_not_called()
    def test_privilege_error_is_visible(self):
        with patch.object(explorer,'execute',side_effect=explorer.PermissionError('Use a separate SELECT-only exploration login')):
            self.assertEqual(self.post(self.s.qc_token,{})[0],403)
    def test_db_error_redacted(self):
        with patch.object(explorer,'execute',side_effect=RuntimeError('secret password')):
            status,data=self.post(self.s.qc_token,{})
            self.assertEqual(status,503);self.assertNotIn('secret',str(data))
    def test_forwarding(self):
        with patch.object(explorer,'execute',return_value={'rows':[]}) as run:
            self.assertEqual(self.post(self.s.qc_token,{'limit':10}), (200,{'rows':[]}))
            run.assert_called_once_with('list_tables',{'limit':10})
