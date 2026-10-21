#!/usr/bin/python3
"""Measure keystroke-to-complete-frame latency through a real PTY, on temporary notes.

This includes the event loop, rendering, PNG transport and PTY writes, but not GPU
presentation. --editor can point to an older checkout for before/after comparison.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import statistics
import struct
import subprocess
import sys
import tempfile
import termios
import time
import re
import base64
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from model import Document,paragraph
from rich_storage import write_rich


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--editor',type=Path,default=ROOT/'rich_editor.py')
    parser.add_argument('--iterations',type=int,default=30)
    parser.add_argument('--scroll',action='store_true',help='Measure screenshot scrolling instead of typing')
    args=parser.parse_args()
    for paragraphs,with_image in ((1,False),(300,False),(1,True)):
        if args.scroll and not with_image:continue
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'images').mkdir()
            doc=Document();doc.blocks[:]=[paragraph('A calm place to think and write. '*8) for _ in range(paragraphs)]
            if with_image:
                Image.effect_noise((1600,1000),70).convert('RGB').save(root/'images/noise.png')
                doc.blocks.append({'type':'image','path':'images/noise.png','width':800})
            if args.scroll:doc.blocks.extend(paragraph('More notes to scroll through. '*8) for _ in range(30))
            path=root/'Test.md';write_rich(path,doc.data)
            master,slave=pty.openpty()
            fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',54,160,1920,1080))
            env={**os.environ,'TERM':'xterm-kitty','KITTY_WINDOW_ID':'benchmark','SAMATHA_NOTES_DIR':temp,'TMPDIR':temp}
            process=subprocess.Popen(['/usr/bin/python3',str(args.editor.resolve()),str(path)],
                                     stdin=slave,stdout=slave,stderr=slave,env=env,start_new_session=True)
            os.close(slave)
            def frame():
                data=bytearray();deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    if select.select([master],[],[],.1)[0]:
                        data.extend(os.read(master,65536))
                        if b'\x1b[?2026l' in data:
                            # Consume and release local transfer files as Kitty does.
                            for encoded in re.findall(rb'\x1b_G[^;]*t=t[^;]*;([^\x1b]+)\x1b\\',data):
                                transfer=Path(os.fsdecode(base64.b64decode(encoded)))
                                if transfer.parent==root:transfer.read_bytes();transfer.unlink()
                            return len(data)
                    if process.poll() is not None:break
                raise RuntimeError('No complete frame: '+repr(bytes(data[-1000:])))
            try:
                frame()
                # Drain the initial status-clock update before timing input.
                while select.select([master],[],[],.15)[0]:os.read(master,65536)
                times=[];sizes=[]
                for i in range(args.iterations):
                    keys=(b'\x1b[<65;800;500M' if i%2==0 else b'\x1b[<64;800;500M') if args.scroll else b'\x1b[120u'
                    started=time.perf_counter();os.write(master,keys)
                    sizes.append(frame());times.append((time.perf_counter()-started)*1000)
                print(json.dumps({'paragraphs':paragraphs,'image':with_image,'iterations':len(times),
                                  'median_ms':round(statistics.median(times),2),
                                  'p95_ms':round(sorted(times)[min(len(times)-1,int(len(times)*.95))],2),
                                  'median_bytes':int(statistics.median(sizes))}),flush=True)
            finally:
                process.terminate()
                try:process.wait(timeout=3)
                except subprocess.TimeoutExpired:process.kill();process.wait()
                os.close(master)


if __name__=='__main__':main()
