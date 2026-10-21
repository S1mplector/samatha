import base64
import copy
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from rich_editor import RichNotebook
from rich_storage import read_rich


class InlineImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'Note.md'
        self.path.write_text('# Note\n\nExisting text\n')
        self.app=RichNotebook(self.path);self.addCleanup(self.app.store.lock.close)
        self.app.render(1200,800)

    def start_clipboard(self,payload,keys=b'\x07'):
        # Exercise real nonblocking pipes without reading or replacing the desktop clipboard.
        popen=subprocess.Popen;commands=[]
        def spawn(command,**kwargs):
            commands.append(command)
            return popen(['/usr/bin/python3','-c','print('+repr(json.dumps(payload))+')'],**kwargs)
        with patch('app.subprocess.Popen',side_effect=spawn):self.app.feed(keys)
        return commands[0]

    def finish_clipboard(self):
        self.app.clip_process.wait(timeout=3)
        self.app.finish_paste()

    def test_ctrl_g_pastes_image_without_modal_or_pane_changes_and_undoes(self):
        app=self.app;original=copy.deepcopy(app.doc.blocks)
        geometry=(app.sidebar_outer,app.content_left,app.body_top,app.body_bottom)
        image=io.BytesIO();Image.new('RGB',(30,20),'purple').save(image,format='PNG')
        command=self.start_clipboard({'type':'image','data':base64.b64encode(image.getvalue()).decode()})
        self.assertEqual(command[-1],'--image')
        self.assertIsNone(app.overlay);self.assertIsNone(app.prompt)
        app.render();self.assertEqual(geometry,(app.sidebar_outer,app.content_left,app.body_top,app.body_bottom))
        self.finish_clipboard();app.render()
        self.assertIsNone(app.overlay);self.assertIsNone(app.prompt)
        self.assertEqual(geometry,(app.sidebar_outer,app.content_left,app.body_top,app.body_bottom))
        self.assertEqual(sum(b['type']=='image' for b in app.doc.blocks),1)
        # The clipboard operation itself commits both the image and reference.
        saved,_=read_rich(self.path)
        block=next(b for b in saved['blocks'] if b['type']=='image')
        self.assertTrue((self.path.parent/block['path']).is_file())
        self.assertFalse(app.doc.dirty)
        app.key('z',ctrl=True);self.assertEqual(app.doc.blocks,original)

    def test_annotation_stays_inline_and_survives_reopen(self):
        app=self.app;image=io.BytesIO();Image.new('RGB',(30,20),'purple').save(image,format='PNG');image.seek(0)
        app.import_image(image)
        index=next(i for i,b in enumerate(app.doc.blocks) if b['type']=='image');app.doc.cursor=(index,0)
        app.key('enter',alt=True);self.assertTrue(app.prompt['inline']);self.assertIsNone(app.overlay)
        app.feed(b'Keep this detail in view\r')
        saved,_=read_rich(self.path)
        self.assertEqual(saved['blocks'][index]['caption'],'Keep this detail in view')
        app.doc.undo();self.assertEqual(app.doc.blocks[index]['caption'],'Pasted image')
        app.store.lock.close();reopened=RichNotebook(self.path)
        try:self.assertEqual(reopened.doc.blocks[index]['caption'],'Keep this detail in view')
        finally:reopened.store.lock.close()

    def test_ctrl_g_does_not_insert_text_when_clipboard_has_no_image(self):
        app=self.app;original=copy.deepcopy(app.doc.data)
        self.start_clipboard({'type':'text','data':'Not an image'})
        self.finish_clipboard()
        self.assertEqual(app.doc.data,original)
        self.assertIsNone(app.overlay);self.assertIsNone(app.prompt)
        self.assertIn('No image on the clipboard',app.message)
        command=self.start_clipboard({'type':'text','data':'Pasted text'},keys=b'\x16')
        self.assertNotIn('--image',command)
        self.finish_clipboard();self.assertIn('Pasted text',app.doc.blocks[0]['text'])

    def test_ctrl_shift_g_uses_footer_for_file_path_and_can_cancel(self):
        app=self.app;original=copy.deepcopy(app.doc.data)
        source=self.root/'an image.png';Image.new('RGB',(30,20),'blue').save(source)
        app.feed(b'\x1b[103;6u');app.render()
        self.assertIsNone(app.overlay)
        self.assertTrue(app.prompt['inline']);self.assertEqual(app.prompt['location'],'footer')
        self.assertIn('Image path:',app.shell_lines()[-2])
        app.key('escape');self.assertEqual(app.doc.data,original)
        app.feed(b'\x1b[103;6u');app.feed(str(source).encode()+b'\r')
        self.assertIsNone(app.prompt);self.assertIsNone(app.overlay)
        self.assertEqual(app.doc.data['title'],'Note')
        self.assertTrue(any(b['type']=='image' for b in app.doc.blocks))


if __name__=='__main__':unittest.main()
