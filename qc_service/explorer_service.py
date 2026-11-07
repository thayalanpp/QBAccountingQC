"""Listener with existing seven queries plus four exploration tools."""
import json
import os
from http.server import HTTPServer
from urllib.parse import urlsplit
from . import service, explorer

class Handler(service.Handler):
    def do_GET(self):
        if self.path=='/queries':
            if self.authorized(): self.reply(200,{'queries':list(service.QUERIES)+list(explorer.NAMES)})
        else: super().do_GET()

    def do_POST(self):
        route=urlsplit(self.path)
        name=route.path.removeprefix('/query/')
        if name not in explorer.NAMES:
            return super().do_POST()
        if not self.authorized(): return
        try:
            if route.query or not route.path.startswith('/query/'): raise explorer.RequestError('Invalid route')
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=32768 or self.headers.get('Transfer-Encoding'): raise explorer.RequestError('Body must be 1..32768 bytes')
            self.connection.settimeout(5)
            body=json.loads(self.rfile.read(size))
            result=explorer.execute(name,body)
            # Reuse exact-decimal encoding, but bound serialized response size.
            if len(json.dumps(result,default=service.encode).encode())>4*1024*1024:
                raise explorer.RequestError('Response exceeds 4 MiB; select fewer columns or lower limit')
        except (ValueError,TypeError,TimeoutError) as exc:
            self.reply(400,{'error':str(exc)[:300]});return
        except explorer.PermissionError as exc:
            self.reply(403,{'error':str(exc)});return
        except Exception:
            self.reply(503,{'error':'Exploration SQL connection/query failed. Run explorer.execute locally to see the database error.'});return
        self.reply(200,result)


def main():
    token=os.getenv('QC_API_TOKEN','')
    if len(token)<32: raise SystemExit('Set QC_API_TOKEN to at least 32 characters')
    with HTTPServer(('127.0.0.1',int(os.getenv('QC_PORT','8765'))),Handler) as server:
        server.qc_token=token
        print('QC listener with database exploration running on localhost. Ctrl+C to stop.')
        try: server.serve_forever()
        except KeyboardInterrupt: pass

if __name__=='__main__': main()
