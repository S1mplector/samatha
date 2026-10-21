#!/usr/bin/python3
"""Reproducible editor timings using disposable notes, never the user's library."""
import argparse
import io
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from model import Document, paragraph
from rich_editor import RichNotebook
from rich_storage import write_rich


def measure(action,count):
    samples=[]
    for _ in range(count):
        started=time.perf_counter();action();samples.append((time.perf_counter()-started)*1000)
    return round(statistics.median(samples),3)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--paragraphs',type=int,default=300)
    parser.add_argument('--iterations',type=int,default=12)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory() as temp:
        path=Path(temp)/'Benchmark.md';doc=Document()
        doc.blocks[:]=[paragraph(f'{i}: '+('A calm place to think and write. '*8)) for i in range(args.paragraphs)]
        write_rich(path,doc.data);app=RichNotebook(path)
        try:
            app.render(1200,800)
            render=lambda:app.render(1200,800)
            results={'paragraphs':args.paragraphs,'iterations':args.iterations,
                     'warm_render_ms':measure(render,args.iterations)}
            def edit():app.doc.insert('x');render()
            results['edit_and_render_ms']=measure(edit,args.iterations)
            surface=render()
            output=io.TextIOWrapper(io.BytesIO())
            with patch('sys.stdout',output):
                app.transmit(surface)
                output.buffer.seek(0);output.buffer.truncate()
                results['unchanged_transmit_ms']=measure(lambda:app.transmit(surface),args.iterations)
                results['unchanged_transmit_bytes']=len(output.buffer.getvalue())//args.iterations
            output.close()
            print(json.dumps(results,indent=2))
        finally:app.store.lock.close()


if __name__=='__main__':main()
