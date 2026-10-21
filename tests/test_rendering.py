import io
import math
import base64
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import cairo
from editor_commands import Command
from model import paragraph, BOLD
from render_cache import RenderCache
from rich_editor import RichNotebook
from terminal_graphics import KittyRenderer


class RenderingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        path=Path(self.tmp.name)/'Test.md';path.write_text('# Test\n')
        self.app=RichNotebook(path);self.addCleanup(self.app.store.lock.close)
        self.app.doc.blocks[:]=[paragraph('A paragraph with styles and wrapping. '*10),paragraph('日本語 and café')]

    def test_cached_and_fresh_frames_match_after_every_visual_change(self):
        app=self.app
        def compare():
            cached=bytes(app.render(1000,700).get_data())
            app.paragraph_cache.clear()
            self.assertEqual(cached,bytes(app.render().get_data()))
        compare()
        app.doc.anchor=(0,4);app.doc.cursor=(0,18);compare()
        app.format(BOLD);compare()
        app.doc.anchor=None;app.ctrl_held=True;compare()
        app.ctrl_held=False;app.change_font_size(4);compare()
        app.cycle_theme();compare()
        app.doc.undo();compare()
        app.key('tab');compare()

    def test_clean_save_does_not_rewrite_notes_settings_or_timer(self):
        app=self.app;app.doc.mark();self.assertTrue(app.save())
        paths=[app.store.path,app.store.settings_path,app.timer_path,
               app.store.path.with_name('.'+app.store.path.name+'.samatha.json')]
        times=[p.stat().st_mtime_ns for p in paths]
        self.assertTrue(app.save());self.assertEqual(times,[p.stat().st_mtime_ns for p in paths])
        app.doc.insert('edited');self.assertTrue(app.save())
        self.assertNotEqual(times[0],paths[0].stat().st_mtime_ns)

    def test_command_extension_and_conflict_detection(self):
        app=self.app
        app.commands.register(Command('insert-marker','m',lambda editor:editor.doc.insert('Marker'),ctrl=True,alt=True))
        app.key('m',ctrl=True,alt=True)
        self.assertTrue(app.doc.blocks[0]['text'].startswith('Marker'))
        with self.assertRaises(ValueError):
            app.commands.register(Command('conflict','b',lambda editor:None))
        app.key('z',ctrl=True);self.assertFalse(app.doc.blocks[0]['text'].startswith('Marker'))
        app.key('z',ctrl=True,shift=True);self.assertTrue(app.doc.blocks[0]['text'].startswith('Marker'))

    def test_render_cache_obeys_memory_budget_and_keeps_recent_items(self):
        cache=RenderCache(3,10)
        cache.put('a','first',4);cache.put('b','second',4)
        self.assertEqual(cache.get('a'),'first')
        cache.put('c','third',4)
        self.assertIsNone(cache.get('b'));self.assertEqual(cache.get('a'),'first')
        cache.put('large','oversized',20)
        self.assertLessEqual(cache.cost,10);self.assertIsNone(cache.get('large'))

    def test_transport_diffs_footer_reuploads_changed_pixels_and_repositions(self):
        renderer=KittyRenderer();surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,30,30)
        output=io.TextIOWrapper(io.BytesIO());self.addCleanup(output.close)
        def send(lines=('shell','Saved'),row=4):
            output.buffer.seek(0);output.buffer.truncate()
            with patch('sys.stdout',output):renderer.transmit(surface,lines,row,39,size=(120,36))
            return output.buffer.getvalue()
        self.assertIn(b'a=T',send());self.assertEqual(send(),b'')
        footer=send(('shell','Unsaved'))
        self.assertIn(b'\x1b[2;1HUnsaved',footer);self.assertNotIn(b'a=T',footer)
        ctx=cairo.Context(surface);ctx.set_source_rgb(1,0,0);ctx.paint()
        self.assertIn(b'a=f',send(('shell','Unsaved')))
        self.assertIn(b'\x1b[1;39H\x1b_Ga=T',send(('shell','Unsaved'),row=1))

    def test_scaled_image_cache_matches_direct_cairo_render(self):
        from PIL import Image, ImageChops
        from drawing import color
        app=self.app;folder=app.store.root/app.book['id']/'images';folder.mkdir(exist_ok=True)
        Image.effect_noise((237,193),65).convert('RGB').save(folder/'cache.png')
        block={'type':'image','path':'images/cache.png'}
        for width,x,y in [(113,5,7),(321,5.25,7.75),(113,5.25,7.75)]:
            block['width']=width
            g={'index':1,'block':block,'w':400};app.layout_image(g,400)
            expected=cairo.ImageSurface(cairo.FORMAT_ARGB32,450,400)
            ctx=cairo.Context(expected);color(ctx,app.palette['bg']);ctx.paint()
            ctx.save();ctx.translate(x,y);ctx.scale(g['ratio'],g['ratio']);ctx.set_source_surface(g['image'],0,0);ctx.paint();ctx.restore()
            for _ in range(2):
                actual=cairo.ImageSurface(cairo.FORMAT_ARGB32,450,400)
                ctx=cairo.Context(actual);color(ctx,app.palette['bg']);ctx.paint();app.render_image(ctx,g,x,y)
                # Compare only image pixels, excluding the caption and handles.
                bottom=math.floor(y+g['ih'])
                images=[Image.frombuffer('RGBA',(450,400),s.get_data(),'raw','BGRA',s.get_stride(),1)
                        for s in (expected,actual)]
                delta=ImageChops.difference(*images).crop((0,0,450,bottom))
                # Cairo's fixed-point sampling can round by two channel levels
                # when the destination origin changes; geometry must not shift.
                self.assertLessEqual(max(high for low,high in delta.getextrema()),2)

    def test_partial_update_sends_only_changed_scanlines_and_erases_old_pixels(self):
        from PIL import Image
        renderer=KittyRenderer();surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,30,300)
        output=io.TextIOWrapper(io.BytesIO());self.addCleanup(output.close)
        def send():
            output.buffer.seek(0);output.buffer.truncate()
            with patch('sys.stdout',output):renderer.transmit(surface)
            return output.buffer.getvalue()
        ctx=cairo.Context(surface);ctx.set_source_rgb(0,0,0);ctx.paint();send()
        for red in (1,0):
            ctx.set_source_rgb(red,0,0);ctx.rectangle(5,170,2,1);ctx.fill()
            data=send();self.assertIn(b'a=f,f=100,i=42,r=1,X=1,x=0,y=170',data)
            self.assertNotIn(b'a=d',data)
            chunks=re.findall(rb'\x1b_G[^;]+;(.*?)\x1b\\',data,re.S)
            with Image.open(io.BytesIO(base64.b64decode(b''.join(chunks)))) as patch_image:
                self.assertEqual(patch_image.size,(30,1))
                self.assertEqual(patch_image.convert('RGB').getpixel((5,0)),(red*255,0,0))
        self.assertEqual(send(),b'')


if __name__=='__main__':unittest.main()
