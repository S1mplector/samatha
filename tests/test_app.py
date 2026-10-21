from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app import Notebook, Pomodoro
from model import BOLD, paragraph

class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.app=Notebook(Path(self.tmp.name)); self.app.render()
    def tearDown(self): self.app.store.lock.close(); self.tmp.cleanup()

    def test_escape_opens_and_closes_settings(self):
        self.app.key('escape'); self.assertEqual(self.app.overlay,'settings')
        self.app.key('escape'); self.assertIsNone(self.app.overlay)

    def test_kitty_modifier_preview_release_and_word_delete(self):
        a=self.app; a.doc.insert('hello world')
        a.feed(b'\x1b[57442;5u'); self.assertTrue(a.ctrl_held)
        self.assertIn('underline',a.markup(a.doc.blocks[0],0))
        a.feed(b'\x1b[127;5u'); self.assertEqual(a.doc.blocks[0]['text'],'hello ')
        a.feed(b'\x1b[57442;1:3u'); self.assertFalse(a.ctrl_held)

    def test_key_release_does_not_duplicate_text(self):
        a=self.app; a.feed(b'\x1b[97;1;97u\x1b[97;1:3u')
        self.assertEqual(a.doc.blocks[0]['text'],'a')

    def test_shifted_printable_keys(self):
        self.app.feed(b'\x1b[49;2;33u'); self.assertEqual(self.app.doc.blocks[0]['text'],'!')

    def test_split_utf8_and_bracketed_paste(self):
        a=self.app; raw='日本語'.encode()
        a.feed(raw[:4]); a.feed(raw[4:]); a.feed(b'\x1b[200~a\nb\x1b[201~')
        self.assertEqual([b['text'] for b in a.doc.blocks],['日本語a','b'])

    def test_keyboard_selection_format_and_undo(self):
        a=self.app; a.doc.insert('test'); a.key('left',shift=True); a.key('b',ctrl=True)
        self.assertEqual(a.doc.blocks[0]['styles'],[0,0,0,BOLD]); a.key('z',ctrl=True)
        self.assertEqual(a.doc.blocks[0]['styles'],[0]*4)

    def test_wrapped_cursor_and_mouse_hit_test(self):
        a=self.app; a.doc.insert('A long paragraph ' * 40); a.render(follow=True)
        g=a.geometry[0]; cx,cy,ch=g['caret']
        i,off=a.locate(cx,a.body_top+cy-a.scroll+ch/2)
        self.assertEqual(i,0); self.assertEqual(off,len(a.doc.blocks[0]['text']))

    def test_render_all_panels_and_narrow_window(self):
        for panel in ['settings','fonts','timer','library','image','help']:
            self.app.overlay=panel; self.assertGreater(self.app.render().get_width(),0)
        self.app.render(500,350)

    def test_timer_pauses_and_changes_phase(self):
        timer=Pomodoro()
        with patch('app.time.monotonic',return_value=100): timer.toggle(self.app.settings)
        with patch('app.time.monotonic',return_value=110): timer.toggle(self.app.settings)
        self.assertEqual(timer.remaining,1490); self.assertIsNone(timer.deadline)
        with patch('app.time.monotonic',return_value=200): timer.toggle(self.app.settings)
        with patch('app.time.monotonic',return_value=1700): self.assertTrue(timer.tick(self.app.settings))
        self.assertEqual(timer.phase,'Break'); self.assertIsNone(timer.deadline)

if __name__=='__main__': unittest.main()
