#!/usr/bin/env python3
"""Add one privacy-safe, analyst-coded outcome to an existing trace.

This does not read or classify response text. Review a local artifact first;
record the scoring protocol in the investigation notes.
"""
import argparse, os, re, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cognitive_dashboard.server import TRACE_ROOT, load_events
from core import cognitive_trace as ct


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--trace',required=True)
    p.add_argument('--outcome',required=True,choices=['paper','digital','mixed','yes','no','unclear','other'])
    a=p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',a.run) or not re.fullmatch(r'[a-f0-9]{32}',a.trace): p.error('invalid run or trace identifier')
    # Search retained pages so older trace IDs can be annotated too.
    before=None; matches=[]
    while True:
        page=load_events(TRACE_ROOT, before=before)
        matches.extend(e for e in page if e['run']==a.run and e['trace_id']==a.trace)
        if len(page)<10000: break
        next_before=min(e['ts'] for e in page)
        if before is not None and next_before>=before: break
        before=next_before
    if not matches: p.error('trace not found in that run')
    if any(e['action']=='manual_code' for e in matches): p.error('this trace already has a manual outcome; retain its original coding')
    os.environ['PALLI_COGNITIVE_TRACE']='1';os.environ['PALLI_COGNITIVE_TRACE_RUN']=a.run
    token=ct._trace.set(a.trace)
    try: ct.emit('behavior','assessment','manual_code',evidence='analyst_coded',outcome=a.outcome)
    finally: ct._trace.reset(token)
    if not any(e['run']==a.run and e['trace_id']==a.trace and e['action']=='manual_code' and e.get('outcome')==a.outcome for e in load_events(TRACE_ROOT)):
        raise SystemExit('Annotation was not recorded; check the trace size limit and collector logs.')
    print('Added analyst-coded outcome; source text was not copied.')
if __name__=='__main__': main()
