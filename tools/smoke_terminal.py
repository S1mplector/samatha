#!/usr/bin/python3
"""Drive the built launcher through a real PTY with an isolated test notebook."""
import fcntl
import argparse
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from model import Document, paragraph
from rich_storage import sidecar, write_rich
from terminal_graphics import terminal_mode


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--theme',default='dark',choices=json.loads((ROOT/'tui/themes.json').read_text()))
    args=parser.parse_args()
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp);notes=root/'notes';notes.mkdir();(notes/'images').mkdir()
        (notes/'.samatha-theme').write_text(args.theme)
        Image.new('RGB',(420,240),'purple').save(notes/'images/example.png')
        path=notes/'Stable.md';doc=Document();doc.data['title']='Original'
        doc.blocks[:]=[paragraph('Body'),{'type':'image','path':'images/example.png'},paragraph('After')]
        write_rich(path,doc.data)
        master,slave=pty.openpty()
        # Deliberately differ from the model's 120x36 fallback dimensions.
        fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',47,141,1410,940))
        env={**os.environ,'TERM':'xterm-kitty','KITTY_WINDOW_ID':'smoke',
             'XDG_CONFIG_HOME':str(root/'config'),'XDG_DATA_HOME':str(root/'data'),'TMPDIR':temp}
        env.pop('SAMATHA_EDITOR',None)
        process=subprocess.Popen([str(ROOT/'samatha'),'--notes-dir',str(notes)],
                                 stdin=slave,stdout=slave,stderr=slave,env=env,start_new_session=True)
        os.close(slave)
        pending=bytearray();transcript=bytearray()
        def receive():
            data=os.read(master,65536);pending.extend(data);transcript.extend(data)
        def wait_for(needle):
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                found=pending.find(needle)
                if found>=0:
                    del pending[:found+len(needle)];return
                if select.select([master],[],[],.1)[0]:
                    try:receive()
                    except OSError:break
                if process.poll() is not None:break
            raise AssertionError(f'Missing terminal state {needle!r}; exit={process.poll()}')
        def send(keys):os.write(master,keys)
        def resize(columns,rows):
            fcntl.ioctl(master,termios.TIOCSWINSZ,struct.pack('HHHH',rows,columns,columns*10,rows*20))
            os.kill(process.pid,signal.SIGWINCH)
        def expect_frame(columns,rows,start):
            # Bubble Tea ends each repaint by positioning the cursor on its
            # last rendered row. Inspect actual pane width as well as height.
            for _ in range(3):
                wait_for(f'\x1b[{rows};0H'.encode())
                raw=bytes(transcript[start:])
                plain=re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]',b'',raw).decode('utf-8',errors='replace')
                borders=[line for line in plain.splitlines() if '╭' in line]
                if borders and len(borders[-1])==columns:
                    assert b'enter edit' in raw or b'enter: edit' in raw,'Footer missing from resized frame'
                    return
                # A resize can land while the previous frame is being flushed.
                # Wait for the complete replacement, not that stale frame.
            raise AssertionError(f'No full {columns}x{rows} frame: {borders!r}')
        def wait_exit(timeout=5):
            deadline=time.monotonic()+timeout
            while process.poll() is None and time.monotonic()<deadline:
                if select.select([master],[],[],.1)[0]:
                    try:receive()
                    except OSError:break
            return process.wait(timeout=max(.1,deadline-time.monotonic()))
        try:
            wait_for(b'Original')
            expect_frame(141,47,0)
            for columns,rows in [(100,28),(154,53)]:
                start=len(transcript);resize(columns,rows)
                expect_frame(columns,rows,start)
            send(b'r');wait_for(b'Note title:')
            send(b'\x15Library title\r');wait_for(b'Renamed')
            assert path.read_text().startswith('# Library title\n')
            send(b'\r');wait_for(b'a=T')
            # Match the exact bytes emitted by the installed Kitty mappings.
            send(b'\x1b[61;5u');wait_for(b'Font size 15')
            send(b'\x1b[43;5u');wait_for(b'Font size 16')
            send(b'\x1b[45;5u');wait_for(b'Font size 15')
            send(b'\x1b[48;5u');wait_for(b'Font size 14')
            # Resizing while the child owns the TTY must reach the browser too.
            resize(132,44)
            # Move over "Body" to the image, then enlarge it using the real CSI key decoder.
            send(b'\x1b[C'*5+b'\x1b[1;3C')
            wait_for(b'Drag corner to resize')
            send(b'\x1b[13;3u');wait_for(b'Image annotation')
            send(b'Screenshot detail\r')
            send(b'\x12');wait_for(b'Rename note')
            send(b'Editor title\r');wait_for(b'Editor title')
            start=len(transcript)
            send(b'\x11')
            expect_frame(132,44,start)
            saved=json.loads(sidecar(path).read_text())['document']
            assert saved['title']=='Editor title',saved['title']
            assert saved['blocks'][1]['width']==452,saved['blocks'][1]
            assert saved['blocks'][1]['caption']=='Screenshot detail',saved['blocks'][1]
            # Reopening an unchanged note must also force a complete browser
            # repaint on return; a cached frame must not leave graphics behind.
            send(b'\r');wait_for(b'a=T')
            start=len(transcript);send(b'\x11')
            expect_frame(132,44,start)
            # Startup errors stay in the browser without exposing the shell.
            sidecar(path).write_text('{broken')
            send(b'\r');wait_for(b'Editor:')
            send(b'q');wait_exit()
            assert process.returncode==0
            assert transcript.count(b'\x1b[?1049h')==1,'Editor switched to another screen'
            assert transcript.count(b'\x1b[?1049l')==1,'Transition exposed the shell screen'
            assert transcript.count(b'\x1b[2J')==1,'Transition cleared to the terminal background'
            modes=re.findall(rb'\x1b\]1337;SetUserVar=samatha[^\x07]*\x07',transcript)
            assert modes[0]==terminal_mode('browser').encode()
            assert terminal_mode('editor').encode() in modes
            assert modes[-1]==terminal_mode('').encode(),'Font mappings leaked into the shell'
            print(f'PASS ({args.theme}): full-window startup, live resize, resize during editing, rename, image resize, durable save, repeated no-clear transitions, startup-error recovery and quit.')
        finally:
            if process.poll() is None:
                process.terminate()
                try:wait_exit()
                except subprocess.TimeoutExpired:process.kill();process.wait()
            os.close(master)


if __name__=='__main__':main()
