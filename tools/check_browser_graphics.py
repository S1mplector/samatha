"""Drive a real Samatha PTY into Kitty's headless parser; verify browse images.
Run: kitty +runpy "import runpy; runpy.run_path('tools/check_browser_graphics.py')"
"""
import fcntl
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import tempfile
import termios
import time
from kitty.fast_data_types import Screen

ROOT=Path(__file__).resolve().parents[1]
IMAGE=0x534100

def parse(screen,data):
    while data:
        count=screen.test_commit_write_buffer(data,screen.test_create_write_buffer())
        assert count
        screen.test_parse_written_data();data=data[count:]

with tempfile.TemporaryDirectory() as temp:
    folder=Path(temp)
    subprocess.run(['/usr/bin/python3','-c',r'''
import sys
from pathlib import Path
from PIL import Image
p=Path(sys.argv[1]);(p/'images').mkdir()
Image.new('RGB',(640,480),'purple').save(p/'images/demo.png')
(p/'Note.md').write_text('# Image note\n\n![Useful observation](images/demo.png)\n\n---\n\n'+('More context\n\n'*40))
''',temp],check=True)
    master,slave=pty.openpty();fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',40,120,1200,800))
    env={**os.environ,'KITTY_WINDOW_ID':'graphics-check','TERM':'xterm-kitty','TMPDIR':temp}
    env.pop('SAMATHA_EDITOR',None)
    process=subprocess.Popen([str(ROOT/'samatha'),'--notes-dir',temp],stdin=slave,stdout=slave,stderr=slave,env=env,start_new_session=True)
    os.close(slave);screen=Screen(None,40,120);transcript=bytearray()
    def wait(predicate):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            if select.select([master],[],[],.05)[0]:
                try:data=os.read(master,65536)
                except OSError:
                    process.wait(timeout=1)
                    if predicate():return
                    break
                transcript.extend(data);parse(screen,data)
            if predicate():return
            if process.poll() is not None:break
        raise AssertionError('Missing browser graphics state: '+repr(bytes(transcript[-500:])))
    try:
        wait(lambda:screen.grman.image_for_client_id(IMAGE) is not None)
        image=screen.grman.image_for_client_id(IMAGE)
        assert image['width']==640 and image['height']==480
        assert b'U=1' in transcript and '\U0010eeee'.encode() in transcript
        original=image['internal_id'];start=len(transcript)
        os.write(master,b'\x1b[<65;90;20M')
        wait(lambda:len(transcript)>start)
        assert screen.grman.image_for_client_id(IMAGE)['internal_id']==original,'Scroll re-uploaded the image'
        os.write(master,b'\r');wait(lambda:screen.grman.image_for_client_id(42) is not None)
        start=len(transcript);os.write(master,b'\x11')
        wait(lambda:len(transcript)>start and screen.grman.image_for_client_id(42) is None and screen.grman.image_for_client_id(IMAGE) is not None)
        os.write(master,b'q')
        wait(lambda:process.poll() is not None)
        assert process.returncode==0
        assert screen.grman.image_for_client_id(IMAGE) is None,'Browse images were not released'
        assert transcript.count(b'\x1b[?1049h')==1 and transcript.count(b'\x1b[?1049l')==1
        print('PASS: inline browse image, scrolling without re-upload, edit/return persistence and terminal cleanup.')
    finally:
        if process.poll() is None:process.terminate();process.wait(timeout=3)
        os.close(master)
