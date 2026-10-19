"""Run and checkpoint independent stages; preserve diagnostics even when publication is blocked."""
from __future__ import annotations
import argparse
import datetime as dt
import os
import subprocess
import sys
import config
import store


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full',action='store_true')
    parser.add_argument('--collect-only',action='store_true',help='Collect and audit without spending model quota')
    args=parser.parse_args()
    full = args.full or dt.datetime.now(dt.timezone.utc).weekday() == 6
    stages=[['voters.py','--audit-history'],['update.py']+(['--full'] if full else [])]
    if not args.collect_only:
        stages.append(['score.py'])
    stages += [['audit.py'],['coverage.py'],['build_index.py']]
    results=[]
    for stage in stages:
        result=subprocess.run([sys.executable,*stage],cwd=config.ROOT)
        results.append({'command':stage,'exit_code':result.returncode})
        store.atomic_json(config.DATA/'pipeline_run.json',{'updated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
                          'stages':results,'success':all(r['exit_code']==0 for r in results)})
    success=all(r['exit_code']==0 for r in results)
    if success:
        result=subprocess.run([sys.executable,'chart.py'],cwd=config.ROOT)
        success=result.returncode==0
    return 0 if success else 2


if __name__=='__main__':
    raise SystemExit(main())
