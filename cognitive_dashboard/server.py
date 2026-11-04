#!/usr/bin/env python3
"""Token-protected LAN and Tailnet viewer for PaLLi metadata traces."""
from __future__ import annotations
import argparse
import ipaddress
import json
import math
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import shutil
import socket
import subprocess
import sys
from urllib.parse import parse_qs, urlparse, urlsplit

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
sys.path.insert(0,str(ROOT))
from palli_config.server import dashboard_addresses
TRACE_ROOT=(Path(os.environ.get('PALLI_TEST_DATA_ROOT') or ROOT/'data')/'cognitive_traces').resolve()
MAX_EVENTS=10000


# Limit requests to local interfaces, private IPv4 networks, and the
# Tailscale IPv4 range even though the socket listens on all IPv4 interfaces.
_ALLOWED_NETWORKS=tuple(ipaddress.ip_network(x) for x in (
    '127.0.0.0/8','10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','100.64.0.0/10'))


def trusted_peer(value: str) -> bool:
    try: address=ipaddress.ip_address(value)
    except ValueError: return False
    return address.version==4 and any(address in network for network in _ALLOWED_NETWORKS)


def _valid_host(value: str) -> bool:
    value=value.rstrip('.').lower()
    if not value or len(value)>253 or value=='0.0.0.0': return False
    try: return ipaddress.ip_address(value).version==4
    except ValueError:
        return all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',label) for label in value.split('.'))


def local_names() -> set[str]:
    """Discover local interface IPs and this node's MagicDNS name."""
    names={'127.0.0.1','localhost'}
    names.update(x for x in dashboard_addresses() if trusted_peer(x))
    host=socket.gethostname().rstrip('.').lower()
    if _valid_host(host): names.add(host)
    tailscale=shutil.which('tailscale')
    if tailscale:
        try:
            result=subprocess.run([tailscale,'status','--self','--json'],capture_output=True,text=True,timeout=2,check=False)
            name=str((json.loads(result.stdout or '{}').get('Self') or {}).get('DNSName') or '').rstrip('.').lower()
            if name and _valid_host(name): names.add(name)
        except (OSError,ValueError,subprocess.TimeoutExpired):
            pass
    return names


def authority(value: str, expected_port: int) -> str | None:
    """Return a normalized hostname only for a valid authority at our port."""
    if not value or any(c.isspace() for c in value): return None
    try:
        parsed=urlsplit('http://'+value)
        if parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment: return None
        host=(parsed.hostname or '').rstrip('.').lower()
        port=parsed.port or 80
    except ValueError: return None
    return host if port==expected_port and _valid_host(host) else None


def _reverse_lines(path: Path):
    """Stream only newline-terminated lines backwards with bounded memory."""
    with path.open('rb') as f:
        f.seek(0,os.SEEK_END);pos=f.tell()
        if pos:
            f.seek(pos-1)
            if f.read(1)!=b'\n':
                # Ignore the trailing partial record, including a syntactically
                # complete JSON object that has not yet received its newline.
                while pos:
                    size=min(256*1024,pos);pos-=size;f.seek(pos);block=f.read(size)
                    idx=block.rfind(b'\n')
                    if idx>=0:
                        pos+=idx+1;break
                else: return
        buffer=b''
        while pos:
            size=min(256*1024,pos);pos-=size;f.seek(pos)
            block=f.read(size)+buffer
            lines=block.split(b'\n');buffer=lines.pop(0)
            for line in reversed(lines): yield line
        if buffer: yield buffer


def load_events(trace_dir: Path, limit: int = MAX_EVENTS, before: float | None = None):
    """Read recent or older pages from owned files; ignore partial lines."""
    if trace_dir != TRACE_ROOT and TRACE_ROOT not in trace_dir.parents:
        raise ValueError('trace directory must be inside the cognitive trace root')
    limit=max(1,min(limit,MAX_EVENTS));out=[]
    if not trace_dir.is_dir(): return []
    for path in reversed(sorted(trace_dir.glob('events-????-??-??.jsonl'))):
        if path.is_symlink() or not path.is_file(): continue
        for line in _reverse_lines(path):
            if not line or len(line)>4096: continue
            try: row=json.loads(line)
            except (ValueError,UnicodeDecodeError): continue
            if not isinstance(row,dict) or row.get('v')!=1: continue
            try: timestamp=float(row.get('ts',0))
            except (TypeError,ValueError): continue
            if not math.isfinite(timestamp): continue
            if before is not None and timestamp>=before: continue
            out.append(row)
            if len(out)>=limit: return list(reversed(out))
    return list(reversed(out))


def conversation_database():
    """Resolve the same configured SQLite path, without loading app services."""
    if os.environ.get('PALLI_TEST_DATA_ROOT'):
        return Path(os.environ.get('DATABASE_PATH') or Path(os.environ['PALLI_TEST_DATA_ROOT'])/'palli-local.sqlite3').resolve()
    from dotenv import dotenv_values
    setting=os.environ.get('DATABASE_PATH','')
    for name in ('.env','.env.local'):
        values=dotenv_values(ROOT/name)
        if values.get('DATABASE_PATH') is not None: setting=values['DATABASE_PATH']
    path=Path(setting or 'data/palli-local.sqlite3')
    return (path if path.is_absolute() else ROOT/path).resolve()


def conversation_max_id(path):
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2) as db:
        db.execute('PRAGMA query_only=ON')
        return int(db.execute('SELECT COALESCE(MAX(id),0) FROM conversations').fetchone()[0])


def conversations_since(path, baseline):
    # Read committed records only; no conversation contents are copied to trace files.
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2) as db:
        db.execute('PRAGMA query_only=ON')
        rows=db.execute('SELECT id,role,username,content,timestamp,platform FROM conversations WHERE id>? ORDER BY id DESC LIMIT 30',(baseline,)).fetchall()
    keys=('id','role','username','content','timestamp','platform')
    return [dict(zip(keys,row)) for row in reversed(rows)]


class Handler(BaseHTTPRequestHandler):
    server_version='CognitiveObservatory/1.0'
    def log_message(self, fmt, *args):
        # Never log query strings, which include the initial access token.
        return
    def headers_safe(self, content_type, status=200):
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
    def reject(self,code=403):
        self.headers_safe('text/plain; charset=utf-8',code); self.end_headers(); self.wfile.write(b'Access denied')
    def do_GET(self):
        if not trusted_peer(self.client_address[0]): return self.reject()
        allowed=getattr(self.server,'allowed_hosts',{'127.0.0.1','localhost'})
        host=authority(self.headers.get('Host',''),self.server.server_port)
        if host not in allowed: return self.reject()
        origin=self.headers.get('Origin')
        if origin:
            try:
                parsed_origin=urlsplit(origin)
                if parsed_origin.scheme!='http' or parsed_origin.path or parsed_origin.query or parsed_origin.fragment:
                    return self.reject()
                origin_host=authority(parsed_origin.netloc,self.server.server_port)
            except ValueError: return self.reject()
            if origin_host!=host: return self.reject()
        parsed=urlparse(self.path)
        qs=parse_qs(parsed.query)
        if parsed.path=='/' and secrets.compare_digest(qs.get('token',[''])[0],self.server.token):
            self.send_response(303); self.send_header('Location','/')
            self.send_header('Set-Cookie',f'observatory={self.server.token}; HttpOnly; SameSite=Strict; Path=/')
            self.send_header('Cache-Control','no-store'); self.end_headers(); return
        cookie=SimpleCookie()
        try: cookie.load(self.headers.get('Cookie',''))
        except Exception: return self.reject()
        supplied=cookie['observatory'].value if 'observatory' in cookie else ''
        if not secrets.compare_digest(supplied,self.server.token): return self.reject()
        if parsed.path=='/api/events':
            try: limit=max(1,min(int(qs.get('limit',[MAX_EVENTS])[0]),MAX_EVENTS))
            except ValueError: return self.reject(400)
            try: before=float(qs['before'][0]) if 'before' in qs else None
            except ValueError: return self.reject(400)
            if before is not None and not math.isfinite(before): return self.reject(400)
            events=load_events(self.server.trace_dir,limit,before)
            payload=json.dumps({'events':events,'limit':limit,'has_more':len(events)==limit},separators=(',',':')).encode()
            self.headers_safe('application/json; charset=utf-8');self.end_headers();self.wfile.write(payload);return
        if parsed.path=='/api/conversations':
            try:
                with self.server.conversation_lock:
                    if self.server.conversation_baseline is None:
                        self.server.conversation_baseline=conversation_max_id(self.server.conversation_path)
                    messages=conversations_since(self.server.conversation_path,self.server.conversation_baseline)
            except (OSError,sqlite3.Error):
                self.headers_safe('application/json; charset=utf-8',503);self.end_headers();self.wfile.write(b'{"error":"conversation feed unavailable"}');return
            body=json.dumps({'messages':messages},separators=(',',':')).encode()
            self.headers_safe('application/json; charset=utf-8');self.end_headers();self.wfile.write(body);return
        files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/flow.js':('flow.js','text/javascript; charset=utf-8'),'/styles.css':('styles.css','text/css; charset=utf-8'),'/flow.css':('flow.css','text/css; charset=utf-8')}
        if parsed.path not in files: return self.reject(404)
        name,ctype=files[parsed.path]; body=(HERE/name).read_bytes()
        self.headers_safe(ctype);self.end_headers();self.wfile.write(body)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--port',type=int,default=8765)
    ap.add_argument('--bind',default='0.0.0.0',help='IPv4 bind address (default: all interfaces)')
    ap.add_argument('--allow-host',action='append',default=[],help='additional trusted local DNS name; repeatable')
    ap.add_argument('--trace-dir',type=Path,default=TRACE_ROOT)
    args=ap.parse_args(); trace_dir=args.trace_dir.resolve()
    if trace_dir!=TRACE_ROOT and TRACE_ROOT not in trace_dir.parents: ap.error('trace directory must be inside the cognitive trace root')
    names=local_names()
    for name in args.allow_host:
        if not _valid_host(name): ap.error('additional hosts must be valid IPv4 addresses or DNS names')
        names.add(name.rstrip('.').lower())
    if args.bind not in {'0.0.0.0','127.0.0.1'} and args.bind not in names:
        ap.error('bind must be 0.0.0.0, 127.0.0.1, or a discovered local IPv4 address')
    server=ThreadingHTTPServer((args.bind,args.port),Handler)
    server.trace_dir=trace_dir;server.token=secrets.token_urlsafe(32);server.allowed_hosts=names
    server.conversation_path=conversation_database();server.conversation_lock=threading.Lock()
    try: server.conversation_baseline=conversation_max_id(server.conversation_path)
    except (OSError,sqlite3.Error): server.conversation_baseline=None
    port=server.server_port
    if args.bind in {'0.0.0.0','127.0.0.1'}:
        print(f'Local dashboard: http://127.0.0.1:{port}/?token={server.token}',flush=True)
    if args.bind!='127.0.0.1':
        addresses=[n for n in sorted(names) if n not in {'127.0.0.1','localhost'}]
        for address in addresses:
            if args.bind!='0.0.0.0' and address!=args.bind: continue
            print(f'Network dashboard: http://{address}:{port}/?token={server.token}',flush=True)
        if not addresses: print('No LAN/Tailnet addresses discovered; use --allow-host to add a name.',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
if __name__=='__main__': main()
