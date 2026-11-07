import unittest
from qc_service.explorer import validate_sql,build_read,pagination,RequestError,verify_reader,PermissionError,read_sql
from unittest.mock import Mock
CAT={('fin','transactions'):{'SchemaName':'fin','ObjectName':'Transactions','ObjectID':1},
     ('fin','statements'):{'SchemaName':'fin','ObjectName':'Statements','ObjectID':2}}
class Validation(unittest.TestCase):
    def test_select_and_join(self):
        for sql in ['SELECT SUM(Amount) AS Total FROM fin.Transactions',
                    'SELECT t.Amount FROM fin.Transactions t JOIN fin.Statements s ON s.StatementID=t.StatementID',
                    'WITH x AS (SELECT Amount FROM fin.Transactions) SELECT SUM(Amount) AS Total FROM x',
                    'SELECT Amount FROM fin.Transactions UNION ALL SELECT Amount FROM fin.Transactions',
                    "SELECT 'DELETE FROM x' AS TextValue"]:
            self.assertTrue(validate_sql(sql,CAT))
    def test_rejected_constructs(self):
        for sql in ['DELETE FROM fin.Transactions','SELECT * INTO fin.New FROM fin.Transactions',
                    'SELECT 1; DELETE FROM fin.Transactions',
                    'SELECT * FROM other.fin.Transactions','SELECT * FROM dbo.Unknown',
                    'SELECT * FROM Transactions','EXEC xp_cmdshell \'whoami\'',
                    "SELECT * FROM OPENROWSET('x','y','z')",'SELECT dbo.evil(1) AS Value',
                    'SELECT NEXT VALUE FOR dbo.MySequence AS Value','SELECT * FROM fin.Transactions WITH (UPDLOCK)',
                    'UPDATE fin.Transactions SET Amount=0',
                    'WITH x AS (SELECT 1 AS a) DELETE FROM fin.Transactions']:
            with self.subTest(sql=sql),self.assertRaises(RequestError): validate_sql(sql,CAT)
    def test_filter_values_bound(self):
        obj=CAT[('fin','transactions')]
        cols=[{'ColumnName':'Amount'},{'ColumnName':'TransactionID'}]
        sql,args,order=build_read({'filters':[{'column':'Amount','op':'eq','value':"0'; DELETE FROM t--"}]},obj,cols,
                                [{'ColumnName':'TransactionID','IsPrimaryKey':True}])
        self.assertNotIn('DELETE',sql);self.assertIn('?',sql);self.assertEqual(len(args),1)
    def test_injected_identifier(self):
        with self.assertRaises(RequestError): build_read({'columns':['Amount]; DROP TABLE x--']},CAT[('fin','transactions')],[{'ColumnName':'Amount'}],[])
    def test_no_key_requires_order(self):
        with self.assertRaises(RequestError): build_read({},CAT[('fin','transactions')],[{'ColumnName':'Amount'}],[])
    def test_bounds(self):
        for body in [{'limit':1001},{'limit':True},{'offset':-1},{'offset':100001}]:
            with self.assertRaises(RequestError): pagination(body)
    def test_privileged_identity(self):
        cur=Mock();cur.fetchone.return_value=(1,0,0)
        with self.assertRaises(PermissionError): verify_reader(cur)
    def test_object_write_grant(self):
        cur=Mock();cur.fetchone.return_value=(0,0,0)
        cur.fetchall.side_effect=[[('CONNECT SQL',)],[('CONNECT',)],[('SELECT',),('UPDATE',)]]
        with self.assertRaises(PermissionError): verify_reader(cur)
    def test_page_sentinel_and_reset(self):
        cur=Mock();cur.description=[('Amount',)];cur.fetchmany.side_effect=[[(1,),(2,),(3,)]]
        result=read_sql(cur,'SELECT Amount FROM fin.Transactions',[],2,0)
        self.assertEqual(len(result['rows']),2);self.assertTrue(result['truncated']);self.assertEqual(result['next_offset'],2)
        self.assertEqual(cur.execute.call_args.args,('SET ROWCOUNT 0',))
    def test_duplicate_alias_rejected(self):
        cur=Mock();cur.description=[('Amount',),('Amount',)]
        with self.assertRaises(RequestError): read_sql(cur,'SELECT 1,2',[],2,0)

if __name__=='__main__': unittest.main()
