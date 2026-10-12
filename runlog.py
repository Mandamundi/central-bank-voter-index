"""Persistent collector logs and run reports, including interrupted runs."""
from __future__ import annotations

from contextlib import contextmanager
import datetime as dt
import json
import logging
import os
from pathlib import Path
import sys

import config

logger = logging.getLogger('fed.collector')
_paths = None
verbose = False


class _Tee:
    def __init__(self, terminal, file):
        self.terminal, self.file = terminal, file

    def write(self, text):
        self.file.write(text)
        self.file.flush()
        return self.terminal.write(text)

    def flush(self):
        self.file.flush()
        self.terminal.flush()

    def __getattr__(self, name):
        return getattr(self.terminal, name)


@contextmanager
def session(detailed=False):
    global _paths, verbose
    if _paths is not None:
        yield _paths
        return
    directory = config.DATA / 'runs'
    directory.mkdir(parents=True, exist_ok=True)
    identifier = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + f'-{os.getpid()}'
    _paths = {'log': directory / f'{identifier}.log', 'report': directory / f'{identifier}.json'}
    verbose = detailed
    handler = logging.FileHandler(_paths['log'], encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(handler)
    old_level, old_propagate = logger.level, logger.propagate
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    stdout, stderr = sys.stdout, sys.stderr
    with _paths['log'].open('a', encoding='utf-8', buffering=1) as file:
        sys.stdout, sys.stderr = _Tee(stdout, file), _Tee(stderr, file)
        try:
            print(f"Saved log: {_paths['log']}", flush=True)
            yield _paths
        except Exception:
            logger.exception('Collector stage failed')
            raise
        finally:
            sys.stdout, sys.stderr = stdout, stderr
            logger.removeHandler(handler)
            handler.close()
            logger.setLevel(old_level)
            logger.propagate = old_propagate
            _paths = None
            verbose = False


def detail(message):
    if _paths is None or verbose:
        print(message, flush=True)
    else:
        logger.info(message)


def write_report(report: dict):
    report['log'] = str(_paths['log'])
    report['report'] = str(_paths['report'])
    content = json.dumps(report, indent=2, ensure_ascii=False) + '\n'
    for target in (_paths['report'], config.DATA / 'last_run.json'):
        temporary = target.with_suffix('.json.tmp')
        temporary.write_text(content, encoding='utf-8')
        temporary.replace(target)
