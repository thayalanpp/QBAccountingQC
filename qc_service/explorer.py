"""Catalog-backed SQL Server exploration. No schema initialization or data writes."""
import datetime as dt
import os
import re
from contextlib import contextmanager
import sqlglot
from sqlglot import exp
from .service import odbc_value

class RequestError(ValueError): pass
class PermissionError(RuntimeError): pass

NAMES = ('list_tables', 'describe_table', 'read_table', 'query_readonly')
IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
SCHEMAS = tuple(x.strip() for x in os.getenv('QC_EXP_SCHEMAS','fin,qb,agent,dbo').split(',') if x.strip())
if not SCHEMAS or any(not IDENT.fullmatch(s) for s in SCHEMAS):
    raise RuntimeError('QC_EXP_SCHEMAS must be a comma-separated list of simple schema names')


def quote(value):
    if not isinstance(value,str) or not IDENT.fullmatch(value):
        raise RequestError('Use simple schema, object and column names')
    return '['+value+']'


def pagination(body):
    limit,offset=body.get('limit',200),body.get('offset',0)
    if type(limit) is not int or not 1<=limit<=1000: raise RequestError('limit must be 1..1000')
    if type(offset) is not int or not 0<=offset<=100000: raise RequestError('offset must be 0..100000')
    return limit,offset


def rows(cur):
    cols=[d[0] for d in cur.description]
    if len(set(cols))!=len(cols): raise RequestError('Result columns must have unique aliases')
    return [dict(zip(cols,r)) for r in cur.fetchall()]


def catalog(cur):
    cur.execute("""SELECT o.object_id AS ObjectID,s.name AS SchemaName,o.name AS ObjectName,
      o.type AS ObjectType FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id
      WHERE o.type IN ('U','V') AND o.is_ms_shipped=0
      AND HAS_PERMS_BY_NAME(QUOTENAME(s.name)+'.'+QUOTENAME(o.name),'OBJECT','SELECT')=1
      ORDER BY s.name,o.name""")
    return {(r['SchemaName'].lower(),r['ObjectName'].lower()):r for r in rows(cur)
            if r['SchemaName'] in SCHEMAS and IDENT.fullmatch(r['ObjectName'])}


def resolve(cat,schema,table):
    quote(schema);quote(table)
    obj=cat.get((schema.lower(),table.lower()))
    if obj is None: raise RequestError('Object is not an accessible table/view in an enabled schema')
    return obj


def columns(cur,obj):
    cur.execute("""SELECT c.name AS ColumnName,ty.name AS DataType,c.max_length AS MaxLengthBytes,
      c.precision AS NumericPrecision,c.scale AS NumericScale,c.is_nullable AS IsNullable,
      c.is_identity AS IsIdentity,c.is_computed AS IsComputed,
      dc.definition AS DefaultDefinition,c.column_id AS Ordinal
      FROM sys.columns c JOIN sys.types ty ON ty.user_type_id=c.user_type_id
      LEFT JOIN sys.default_constraints dc ON dc.object_id=c.default_object_id
      WHERE c.object_id=? ORDER BY c.column_id""",obj['ObjectID'])
    return rows(cur)


def keys(cur,obj):
    cur.execute("""SELECT i.name AS IndexName,i.is_primary_key AS IsPrimaryKey,
      i.is_unique AS IsUnique,c.name AS ColumnName,ic.key_ordinal AS KeyOrdinal
      FROM sys.indexes i JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id
      JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id
      WHERE i.object_id=? AND (i.is_primary_key=1 OR i.is_unique=1)
      AND ic.key_ordinal>0 ORDER BY i.is_primary_key DESC,i.index_id,ic.key_ordinal""",obj['ObjectID'])
    return rows(cur)


def relationships(cur,obj):
    cur.execute("""SELECT fk.name AS ForeignKeyName,ps.name AS FromSchema,po.name AS FromTable,
      pc.name AS FromColumn,rs.name AS ToSchema,ro.name AS ToTable,rc.name AS ToColumn,
      fkc.constraint_column_id AS Ordinal
      FROM sys.foreign_keys fk JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id=fk.object_id
      JOIN sys.objects po ON po.object_id=fkc.parent_object_id JOIN sys.schemas ps ON ps.schema_id=po.schema_id
      JOIN sys.columns pc ON pc.object_id=po.object_id AND pc.column_id=fkc.parent_column_id
      JOIN sys.objects ro ON ro.object_id=fkc.referenced_object_id JOIN sys.schemas rs ON rs.schema_id=ro.schema_id
      JOIN sys.columns rc ON rc.object_id=ro.object_id AND rc.column_id=fkc.referenced_column_id
      WHERE po.object_id=? OR ro.object_id=? ORDER BY fk.name,fkc.constraint_column_id""",obj['ObjectID'],obj['ObjectID'])
    return rows(cur)


def verify_reader(cur):
    cur.execute("SELECT IS_SRVROLEMEMBER('sysadmin'),IS_MEMBER('db_owner'),IS_MEMBER('db_securityadmin')")
    if any(x!=0 for x in cur.fetchone()): raise PermissionError('Use a separate SELECT-only exploration login')
    # SQL Server 2022 splits VIEW DEFINITION into finer view-only rights that come with it;
    # they read metadata only (no data change, no permission change), so they are allowed.
    view_only = {'VIEW SECURITY DEFINITION','VIEW PERFORMANCE DEFINITION',
                 'VIEW ANY COLUMN ENCRYPTION KEY DEFINITION','VIEW ANY COLUMN MASTER KEY DEFINITION'}
    for scope,allowed in [('SERVER',{'CONNECT SQL','VIEW ANY DATABASE'}),
                          ('DATABASE',{'CONNECT','SELECT','VIEW DEFINITION'}|view_only)]:
        cur.execute("SELECT permission_name FROM fn_my_permissions(NULL,?)",scope)
        extra={r[0] for r in cur.fetchall()}-allowed
        if extra:
            raise PermissionError(f'Exploration login has elevated {scope} permissions: {", ".join(sorted(extra))}')
    # Check object-level grants too; database-level checks alone miss direct grants.
    cur.execute("""SELECT DISTINCT p.permission_name FROM sys.objects o
      JOIN sys.schemas s ON s.schema_id=o.schema_id
      CROSS APPLY sys.fn_my_permissions(QUOTENAME(s.name)+'.'+QUOTENAME(o.name),'OBJECT') p
      WHERE o.is_ms_shipped=0""")
    extra={r[0] for r in cur.fetchall()}-{'SELECT','VIEW DEFINITION'}
    if extra:
        raise PermissionError(f'Exploration login has object permissions beyond SELECT: {", ".join(sorted(extra))}')


@contextmanager
def connection():
    import pyodbc
    user=os.getenv('QC_EXP_DB_USER') or os.getenv('QC_DB_USER')
    password=os.getenv('QC_EXP_DB_PASSWORD') or os.getenv('QC_DB_PASSWORD')
    vals={'DRIVER':os.getenv('QC_DB_DRIVER','ODBC Driver 18 for SQL Server'),
          'SERVER':os.getenv('QC_DB_SERVER'),'DATABASE':os.getenv('QC_DB_NAME'),
          'UID':user,'PWD':password}
    if not all(vals.values()): raise RuntimeError('Missing QC database configuration')
    cs=';'.join(k+'='+odbc_value(v) for k,v in vals.items())
    cs+=';Encrypt=yes;TrustServerCertificate='+('yes' if os.getenv('QC_TRUST_CERT')=='yes' else 'no')
    conn=pyodbc.connect(cs,timeout=5,autocommit=False)
    conn.timeout=15
    try:
        permission_cursor=conn.cursor()
        try: verify_reader(permission_cursor)
        finally: permission_cursor.close()
        yield conn
    finally:
        try: conn.rollback()
        finally: conn.close()


def validate_sql(sql,cat):
    if not isinstance(sql,str) or not 1<=len(sql)<=20000: raise RequestError('SQL must be 1..20000 characters')
    try: parsed=sqlglot.parse(sql,read='tsql')
    except sqlglot.errors.ParseError: raise RequestError('Cannot parse SQL as T-SQL') from None
    if len(parsed)!=1 or not isinstance(parsed[0],(exp.Select,exp.Union,exp.Intersect,exp.Except)):
        raise RequestError('Exactly one SELECT query is allowed')
    tree=parsed[0]
    forbidden={'into','insert','update','delete','merge','create','drop','alter','command','execute',
               'transaction','commit','rollback','grant','revoke','use','set','lock','hint',
               'withtablehint','queryoption','nextvaluefor','parameter','sessionparameter'}
    if any(n.key.lower() in forbidden for n in tree.walk()): raise RequestError('Unsupported or non-read-only SQL construct')
    # No user-defined/dotted functions, file/network readers, or table-valued functions.
    allowed_anon={'DATEFROMPARTS','EOMONTH','STRING_AGG','ISNULL','TRY_CONVERT','CONVERT','DATEDIFF','DATEADD'}
    bad_words={'OPENROWSET','OPENQUERY','OPENDATASOURCE','NEXT VALUE FOR','FOR XML','FOR JSON'}
    canonical=tree.sql(dialect='tsql')
    if any(w in canonical.upper() for w in bad_words): raise RequestError('External or unsupported SQL construct')
    if any(isinstance(n,exp.Dot) and isinstance(n.expression,exp.Func) for n in tree.walk()):
        raise RequestError('User-defined functions are not allowed')
    for fn in tree.find_all(exp.Anonymous):
        if fn.name.upper() not in allowed_anon: raise RequestError('Unsupported function: '+fn.name)
    ctes={c.alias.lower() for c in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if table.catalog: raise RequestError('Cross-database/server references are not allowed')
        if not isinstance(table.this,exp.Identifier): raise RequestError('Table functions are not allowed')
        if not table.db and table.name.lower() in ctes: continue
        if not table.db: raise RequestError('Use schema-qualified table names')
        resolve(cat,table.db,table.name)
    # Regenerate checked AST; never execute unchecked original text.
    return canonical


def read_sql(cur,sql,args,limit,offset):
    # ROWCOUNT limits rows sent by SQL Server. Timeout limits query time, not plan cost.
    cur.execute('SET ROWCOUNT '+str(offset+limit+1))
    try:
        cur.execute(sql,*args)
        names=[d[0] for d in cur.description]
        if len(set(names))!=len(names): raise RequestError('Give every result column a unique alias')
        if offset: cur.fetchmany(offset)
        raw=cur.fetchmany(limit+1)
        more=len(raw)>limit
        return {'columns':names,'rows':[dict(zip(names,r)) for r in raw[:limit]],
                'truncated':more,'next_offset':offset+limit if more else None}
    finally: cur.execute('SET ROWCOUNT 0')


def build_read(body,obj,cols,key_rows):
    known={c['ColumnName'] for c in cols}
    selected=body.get('columns') or [c['ColumnName'] for c in cols]
    if not isinstance(selected,list) or not selected or len(selected)>200 or any(c not in known for c in selected):
        raise RequestError('columns must name existing columns (maximum 200)')
    if len(set(selected))!=len(selected): raise RequestError('Duplicate columns')
    sql='SELECT '+','.join(quote(c) for c in selected)+' FROM '+quote(obj['SchemaName'])+'.'+quote(obj['ObjectName'])
    filters=body.get('filters',[])
    if not isinstance(filters,list) or len(filters)>20: raise RequestError('At most 20 AND filters')
    clauses=[];args=[]
    for f in filters:
        if not isinstance(f,dict) or set(f)-{'column','op','value'}: raise RequestError('Invalid filter')
        c=f.get('column');op=f.get('op');value=f.get('value')
        if c not in known: raise RequestError('Unknown filter column')
        if op in ('is_null','not_null'): clauses.append(quote(c)+(' IS NULL' if op=='is_null' else ' IS NOT NULL'))
        elif op in ('eq','ne','lt','le','gt','ge','like'):
            if value is None or not isinstance(value,(str,int,float,bool)): raise RequestError('Filter requires a scalar value')
            clauses.append(quote(c)+{'eq':' = ?','ne':' <> ?','lt':' < ?','le':' <= ?','gt':' > ?','ge':' >= ?','like':' LIKE ?'}[op]);args.append(value)
        else: raise RequestError('Unsupported filter operator')
    if clauses: sql+=' WHERE '+' AND '.join(clauses)
    primary=[k['ColumnName'] for k in key_rows if k['IsPrimaryKey']]
    order=body.get('order_by') or primary
    if not isinstance(order,list) or not order or any(c not in known for c in order):
        raise RequestError('Provide order_by columns for views/tables without a primary key')
    order=list(dict.fromkeys(order+primary))
    sql+=' ORDER BY '+','.join(quote(c) for c in order)
    return sql,args,order


def execute(name,body):
    if name not in NAMES or not isinstance(body,dict): raise RequestError('Unknown tool or invalid body')
    allowed={'list_tables':{'schema','limit','offset'},'describe_table':{'schema','table'},
             'read_table':{'schema','table','columns','filters','order_by','limit','offset'},
             'query_readonly':{'sql','limit','offset'}}[name]
    if set(body)-allowed: raise RequestError('Unexpected parameter')
    limit,offset=pagination(body)
    with connection() as conn:
        cur=conn.cursor()
        try:
            cat=catalog(cur)
            if name=='list_tables':
                objects=list(cat.values())
                if body.get('schema'): objects=[o for o in objects if o['SchemaName']==body['schema']]
                result={'rows':objects[offset:offset+limit],'truncated':len(objects)>offset+limit,
                        'next_offset':offset+limit if len(objects)>offset+limit else None,
                        'enabled_schemas':SCHEMAS}
            elif name=='query_readonly':
                sql=validate_sql(body.get('sql'),cat)
                result=read_sql(cur,sql,[],limit,offset)
                result['pagination_note']='Use ORDER BY with a unique tie-breaker; keep source loads paused between pages.'
            else:
                obj=resolve(cat,body.get('schema'),body.get('table'))
                cols=columns(cur,obj);ks=keys(cur,obj)
                if name=='describe_table':
                    result={'object':obj,'columns':cols,'keys':ks,'relationships':relationships(cur,obj)}
                else:
                    sql,args,order=build_read(body,obj,cols,ks)
                    result=read_sql(cur,sql,args,limit,offset)
                    result['order_by']=order
                    result['pagination_note']='Primary key appended when present; otherwise caller must choose a unique order. No snapshot across calls.'
            result.update(tool=name,fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                          amount_encoding='decimal strings',scope='All enabled schemas; caller must specify company and FY where needed')
            return result
        finally: cur.close()
