import io
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
        self.assertIn(b'a=T',send(('shell','Unsaved')))
        self.assertIn(b'\x1b[1;39H\x1b_Ga=T',send(('shell','Unsaved'),row=1))


if __name__=='__main__':unittest.main()
