#!/usr/bin/env python3
"""Resolve a trace ID against this installation, with optional private text."""
from __future__ import annotations
import argparse, json, os, re, sqlite3, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core import cognitive_trace as ct


def checked(path: Path, root: Path) -> Path:
    p=path.resolve()
    if root not in p.parents: raise ValueError('resolved path leaves the selected run')
    return p


def main():
    ap=argparse.ArgumentParser(description='Resolve a local ID; private text is hidden unless explicitly requested.')
    ap.add_argument('--data-dir',type=Path,help='data directory within this installation (default: data/)')
    ap.add_argument('--database',type=Path,help='database within this installation data directory')
    ap.add_argument('--entity',required=True,help='pseudonymous ID copied from the observatory')
    ap.add_argument('--show-text',action='store_true',help='print potentially private state text locally')
    a=ap.parse_args()
    if not re.fullmatch(r'(stake|fact|quest|fear|inner|daydream|moment|night):[0-9a-f]{16}',a.entity): ap.error('unsupported entity ID')
    root=Path(os.environ.get('PALLI_TEST_DATA_ROOT') or ROOT/'data').resolve()
    data=(a.data_dir or root).resolve()
    if (data!=root and root not in data.parents) or not data.is_dir(): ap.error('data directory must be inside this installation data root')
    key=ct.trace_dir()/'.trace-key'
    if not key.is_file(): ap.error('trace key unavailable; identifier cannot be resolved')
    ns=a.entity.split(':',1)[0];found=[]
    def keep(raw,text,status=''):
        if ct.entity(ns,raw)==a.entity:
            safe_status=str(status) if re.fullmatch(r'[a-z_-]{1,40}',str(status)) else ('other' if status else '')
            record={'entity':a.entity,'version':ct.entity('version',text) if text else '', 'status':safe_status}
            if a.show_text: record['text']=str(text)
            found.append(record)
    if ns in ('stake','fact','inner'):
        db=checked(a.database or data/'palli-local.sqlite3',root)
        if not db.is_file(): ap.error('no copied main database in this run')
        con=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)
        try:
            query={'stake':'SELECT id, stake, status FROM stakes', 'fact':'SELECT id, content, status FROM user_memory','inner':'SELECT entry_id, text_content, source_kind FROM monologue_memory_fts'}[ns]
            for raw,text,status in con.execute(query): keep(raw,text or '',status or '')
        finally: con.close()
    elif ns in ('quest','fear'):
        path=checked(data/('quests/quests.jsonl' if ns=='quest' else 'fears/fears.jsonl'),root)
        state={}
        if path.is_file():
            for line in path.read_text(encoding='utf-8').splitlines():
                try:e=json.loads(line)
                except ValueError:continue
                rid=e.get('id');op=e.get('op')
                if not rid:continue
                if op=='plant' and rid not in state:state[rid]={'text':e.get('text',''),'status':e.get('status','held' if ns=='fear' else 'alive')}
                elif rid in state and op=='text':state[rid]['text']=e.get('text','')
                elif rid in state and op in ('status','release','face'):state[rid]['status']=e.get('status',op)
        for raw,item in state.items():keep(raw,item['text'],item['status'])
    else:
        folder={'daydream':'daydream','moment':'present_moment','night':'night_cycle'}[ns]
        directory=checked(data/folder,root)
        if directory.is_dir():
            for path in sorted(directory.glob('*.jsonl')):
                path=checked(path,root)
                for line in path.read_text(encoding='utf-8').splitlines():
                    try:item=json.loads(line)
                    except ValueError:continue
                    text=str(item.get('text') or '').strip()
                    if text:keep(text,text)
    if not found:
        print('No matching current record in this installation. It may have changed, been removed, or use a different trace key.')
        return 1
    print(json.dumps(found,indent=2,ensure_ascii=False))
    return 0
if __name__=='__main__':raise SystemExit(main())
