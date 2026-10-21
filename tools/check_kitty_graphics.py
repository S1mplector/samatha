"""Verify full and partial editor frames with Kitty's real headless graphics parser.

Run: kitty +runpy "import runpy; runpy.run_path('tools/check_kitty_graphics.py')"
Uses disposable notes only; never opens or changes a user terminal window.
"""
import json
from pathlib import Path
import subprocess
import tempfile
from kitty.fast_data_types import Screen

root=Path(__file__).resolve().parents[1]
fixture=r'''
import io,json,sys
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from model import Document,paragraph
from rich_storage import write_rich
from rich_editor import RichNotebook
folder=Path(sys.argv[1]);(folder/'images').mkdir()
Image.effect_noise((1200,800),70).convert('RGB').save(folder/'images/test.png')
doc=Document();doc.blocks[:]=[paragraph('Text above an image. '*4),{'type':'image','path':'images/test.png','width':550},paragraph('Below')]
path=folder/'Test.md';write_rich(path,doc.data);app=RichNotebook(path)
app.cols=120;app.rows=40
out=io.TextIOWrapper(io.BytesIO());records=[]
def frame(name):
    out.buffer.seek(0);out.buffer.truncate()
    with patch('sys.stdout',out):app.transmit(app.render(1200,800,diagnostic=False))
    data=out.buffer.getvalue();(folder/(name+'.bin')).write_bytes(data)
    _,_,w,h,stride,fmt=app.terminal.last_image
    image=Image.frombuffer('RGBA',(w,h),app.terminal.last_pixels,'raw','BGRA',stride,1)
    (folder/(name+'.rgb')).write_bytes(image.convert('RGB').tobytes())
    records.append({'name':name,'width':w,'height':h,'bytes':len(data)})
try:
    frame('initial')
    for i in range(20):app.doc.insert('x');frame('type'+str(i))
    app.doc.delete();frame('delete');app.doc.undo();frame('undo');app.doc.undo(True);frame('redo')
    app.doc.anchor=(0,2);app.doc.cursor=(0,15);frame('selection')
    app.doc.anchor=None;app.change_font_size(3);frame('font')
    app.scroll=180;frame('scroll');app.cycle_theme();frame('theme')
    app.overlay='settings';frame('overlay');app.overlay=None;frame('close-overlay')
    app.cols=100;app.rows=36;frame('resize')
    frame('unchanged')
    app.notify('Saved');frame('footer')
    print(json.dumps(records))
finally:out.close();app.store.lock.close()
'''


def parse(screen,data):
    while data:
        buffer=screen.test_create_write_buffer()
        count=screen.test_commit_write_buffer(data,buffer)
        assert count,'Kitty parser stopped consuming output'
        screen.test_parse_written_data();data=data[count:]


with tempfile.TemporaryDirectory() as temp:
    folder=Path(temp)
    records=json.loads(subprocess.check_output(['/usr/bin/python3','-c',fixture,temp],cwd=root))
    screen=Screen(None,100,200)
    for record in records:
        name=record['name'];data=(folder/(name+'.bin')).read_bytes()
        parse(screen,data);image=screen.grman.image_for_client_id(42)
        assert image is not None,name
        assert (image['width'],image['height'])==(record['width'],record['height']),name
        assert image['data']==(folder/(name+'.rgb')).read_bytes(),name
        assert not image['extra_frames'],name
        assert image['refs.count']==1,name
        assert screen.grman.image_count==1,name
        if name.startswith('type'):
            assert b'a=f,' in data and b'a=T,' not in data,name
            assert record['bytes']<records[0]['bytes']//10,name
        if name=='unchanged':assert not data
    parse(screen,b'\x1b_Ga=d,d=I,i=42,q=2\x1b\\')
    assert screen.grman.image_count==0
    print(f"Verified {len(records)} frames pixel-for-pixel in Kitty; one image, no extra frames, cleanup complete.")
