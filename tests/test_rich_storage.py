import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from model import Document, Store, BOLD, ITALIC, UNDERLINE, HIGHLIGHT, paragraph
from rich_storage import RichStore, import_library, sidecar, write_rich, rename_title
from rich_editor import RichNotebook

class RichIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()

    def test_import_preserves_original_and_all_rich_content(self):
        store=Store(self.root);book=store.library['notebooks'][0]
        doc=store.load(book,book['notes'][0]['id'])
        doc.blocks[:]=[paragraph('all styles',[15]*10),{'type':'image','path':'images/sample.png','caption':'Photo'}]
        images=self.root/book['id']/'images';images.mkdir();(images/'sample.png').write_bytes(b'image')
        store.save(book,doc);store.lock.close()
        originals={p:p.read_bytes() for p in self.root.rglob('*.json')}
        notes=import_library(self.root)
        path=next(notes.rglob('*.md'));adapter=RichStore(path)
        self.assertEqual(adapter.document.data,doc.data)
        self.assertEqual((path.parent/'images/sample.png').read_bytes(),b'image')
        adapter.lock.close()
        self.assertEqual(import_library(self.root),notes)
        self.assertEqual(len(list(notes.rglob('*.md'))),1)
        for p,raw in originals.items():self.assertEqual(p.read_bytes(),raw)

    def test_format_image_ctrl_delete_save_reopen(self):
        path=self.root/'Example.md';path.write_text('# Example\n\n')
        app=RichNotebook(path)
        app.doc.insert('hello world');app.doc.anchor=(0,6);app.doc.cursor=(0,11);app.doc.format(BOLD|ITALIC|UNDERLINE|HIGHLIGHT)
        app.doc.anchor=None
        app.feed(b'\x1b[57442;5u')
        self.assertTrue(app.ctrl_held);self.assertIn('underline',app.markup(app.doc.blocks[0],0))
        app.feed(b'\x1b[127;5u');self.assertEqual(app.doc.blocks[0]['text'],'hello ')
        app.doc.undo();self.assertEqual(app.doc.blocks[0]['styles'][6:],[15]*5)
        app.feed(b'\x1b[57442;1:3u');self.assertFalse(app.ctrl_held)
        from PIL import Image
        image=self.root/'source.png';Image.new('RGB',(20,20),'purple').save(image)
        app.import_image(image);expected=copy.deepcopy(app.doc.blocks)
        self.assertTrue(app.save());app.store.lock.close()
        reopened=RichNotebook(path)
        self.assertEqual(reopened.doc.blocks,expected)
        self.assertTrue(any(b['type']=='image' for b in reopened.doc.blocks))
        reopened.render();reopened.store.lock.close()

    def test_external_edits_preserved_separately(self):
        path=self.root/'Note.md';doc=Document();doc.insert('rich');write_rich(path,doc.data)
        path.write_text('# External edit\nImportant external content\n')
        store=RichStore(path)
        self.assertTrue(store.recovered)
        self.assertIn('Important external',next(self.root.glob('*-external-*.md')).read_text())
        self.assertEqual(store.document.blocks[0]['text'],'rich');store.lock.close()

    def test_failed_import_is_atomic_and_retryable(self):
        store=Store(self.root);store.lock.close()
        with patch('rich_storage.write_rich',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):import_library(self.root)
        self.assertFalse((self.root/'notes').exists())
        self.assertFalse(list(self.root.glob('.notes-import-*')))
        import_library(self.root);self.assertTrue((self.root/'notes').exists())

    def test_damaged_rich_sidecar_is_not_flattened(self):
        path=self.root/'Note.md';path.write_text('# Original\n')
        sidecar(path).write_text('{broken')
        with self.assertRaises(ValueError):RichStore(path)
        self.assertEqual(sidecar(path).read_text(),'{broken')


    def test_editor_uses_panes_and_theme_persists(self):
        path=self.root/'Test.md';path.write_text('# Test\n\n')
        app=RichNotebook(path);app.render(1200,800)
        shell='\n'.join(app.shell_lines())
        self.assertIn('╭',shell);self.assertIn('samatha',shell)
        self.assertNotIn('✍',shell);self.assertNotIn('Start writing',shell)
        self.assertGreater(app.content_left,0)
        app.cycle_theme();chosen=app.settings['theme'];app.save();app.store.lock.close()
        reopened=RichNotebook(path)
        self.assertEqual(reopened.settings['theme'],chosen)
        reopened.key('tab');reopened.render();self.assertEqual(reopened.content_left,0)
        reopened.store.lock.close()

    def test_notebook_and_note_creation(self):
        path=self.root/'First.md';path.write_text('# First\n')
        app=RichNotebook(path);app.new_book();app.prompt['value']='Research';app.submit_prompt()
        self.assertTrue((self.root/'Research').is_dir())
        self.assertFalse(app.running);app.store.lock.close()
        app=RichNotebook(path);app.new_note();app.prompt['value']='Second';app.submit_prompt()
        self.assertEqual(app.store.path.parent,self.root)
        self.assertTrue((self.root/'Second.md').exists());app.store.lock.close()


    def test_font_shortcuts_decode_reflow_and_persist(self):
        path=self.root/'Test.md';path.write_text('# Test\n\n')
        app=RichNotebook(path);app.doc.insert('A paragraph that wraps. '*50)
        app.render(1000,650,follow=True);height=app.content_height
        original=copy.deepcopy(app.doc.data)
        # Actual Kitty CSI-u press, repeat, and release events; release must not zoom twice.
        app.feed(b'\x1b[61;6u\x1b[61;6:2u\x1b[61;6:3u')
        self.assertEqual(app.settings['font_size'],16)
        app.render(follow=True);self.assertGreater(app.content_height,height)
        app.feed(b'\x1b[43;5u');self.assertEqual(app.settings['font_size'],17)
        app.feed(b'\x1b[45;5u');self.assertEqual(app.settings['font_size'],16)
        self.assertEqual(app.doc.data,original)
        app.store.lock.close()
        reopened=RichNotebook(path);self.assertEqual(reopened.settings['font_size'],16)
        reopened.feed(b'\x1b[48;5u');self.assertEqual(reopened.settings['font_size'],14)
        reopened.overlay='settings';reopened.feed(b'\x1b[61;5u')
        self.assertEqual(reopened.settings['font_size'],15)
        reopened.settings['font_size']=36;reopened.feed(b'\x1b[61;5u')
        self.assertEqual(reopened.settings['font_size'],36)
        reopened.settings['font_size']=12;reopened.feed(b'\x1b[45;5u')
        self.assertEqual(reopened.settings['font_size'],12)
        reopened.store.lock.close()

    def test_library_rename_preserves_plain_markdown_bytes_and_filename(self):
        path=self.root/'stable-name.md'
        original=b'---\r\ncustom: metadata\r\n---\r\n# Before\r\n\r\n**bold** <u>underline</u>\r\n```go\r\nfmt.Println("example")\r\n```\r\n'
        path.write_bytes(original)
        rename_title(path,'After / 日本語')
        self.assertEqual(path.read_bytes(),original.replace(b'# Before','# After / 日本語'.encode()))
        self.assertFalse(sidecar(path).exists())

    def test_library_rename_keeps_rich_content_hash_and_backup(self):
        path=self.root/'stable-name.md';doc=Document();doc.data['title']='Before'
        doc.blocks[:]=[paragraph('formatted',[15]*9),{'type':'image','path':'images/example.png','width':250}]
        write_rich(path,doc.data);expected=copy.deepcopy(doc.data);expected['title']='After'
        rename_title(path,'After')
        reopened=RichStore(path);self.addCleanup(reopened.lock.close)
        self.assertEqual(reopened.document.data,expected)
        self.assertFalse(reopened.recovered)
        self.assertTrue(list((self.root/'.samatha-backups').rglob('*.json')))
        before=path.read_bytes()
        with self.assertRaises(RuntimeError):rename_title(path,'Locked')
        self.assertEqual(path.read_bytes(),before)

    def test_editor_title_prompt_stays_inside_panes_and_cancel_is_safe(self):
        path=self.root/'Test.md';path.write_text('# Test\n\n')
        app=RichNotebook(path);self.addCleanup(app.store.lock.close)
        app.doc.insert('A paragraph. '*500);app.render(1200,800,follow=True)
        before=(app.sidebar_outer,app.content_left,app.scroll)
        app.key('r',ctrl=True);app.render(follow=True)
        self.assertTrue(app.prompt['inline'])
        self.assertEqual((app.sidebar_outer,app.content_left,app.scroll),before)
        # The terminal shell stays text; only the existing content pane is transmitted.
        class Output:
            def __init__(self):self.buffer=__import__('io').BytesIO()
        output=Output()
        with patch('rich_editor.sys.stdout',output):app.transmit(app.render())
        self.assertTrue(b'\x1b[4;39H\x1b_Ga=T' in output.buffer.getvalue())
        self.assertFalse(b'\x1b[1;1H\x1b_Ga=T' in output.buffer.getvalue())
        app.key('text',text='Cancelled');app.key('escape')
        self.assertEqual(app.doc.data['title'],'Test')
        app.key('r',ctrl=True);app.key('text',text='Renamed');app.key('enter')
        self.assertEqual(app.doc.data['title'],'Renamed')
        self.assertIn('Renamed',[n[3] for n in app.tree])
        self.assertTrue(path.read_text().startswith('# Renamed\n'))

    def test_image_drag_is_proportional_one_undo_and_persists(self):
        from PIL import Image
        path=self.root/'Test.md';path.write_text('# Test\n\n')
        source=self.root/'source.png';Image.new('RGB',(400,240),'purple').save(source)
        app=RichNotebook(path);self.addCleanup(app.store.lock.close)
        app.import_image(source);app.render(1200,800)
        g=next(g for g in app.geometry if 'image' in g);index=g['index']
        original=copy.deepcopy(app.doc.blocks);undo_count=len(app.doc.undo_stack)
        original_image=(self.root/g['block']['path']).read_bytes()
        x=g['x']+g['iw'];y=app.body_top+g['y']-app.scroll+g['ih']
        def mouse(button,x,y,release=False):
            app.feed(f'\x1b[<{button};{round(x)+1};{round(y)+1}{"m" if release else "M"}'.encode())
        mouse(0,x,y)
        mouse(32,x+80,y+48);app.render()
        mouse(32,x+120,y+72);app.render()
        mouse(0,x+160,y+96,True);app.render()
        self.assertIsNone(app.image_drag)
        self.assertEqual(app.doc.blocks[index]['width'],560)
        self.assertEqual(len(app.doc.undo_stack),undo_count+1)
        g=app.geometry[index];self.assertAlmostEqual(g['iw']/g['ih'],400/240)
        self.assertEqual((self.root/g['block']['path']).read_bytes(),original_image)
        app.key('z',ctrl=True);self.assertEqual(app.doc.blocks,original)
        app.key('y',ctrl=True);self.assertEqual(app.doc.blocks[index]['width'],560)
        app.save();app.store.lock.close()
        reopened=RichNotebook(path);self.addCleanup(reopened.store.lock.close)
        reopened.render(1200,800);self.assertEqual(reopened.geometry[index]['iw'],560)
        reopened.render(600,800);self.assertLessEqual(reopened.geometry[index]['iw'],reopened.geometry[index]['w'])
        self.assertEqual(reopened.doc.blocks[index]['width'],560)
        reopened.render(1200,800);reopened.doc.cursor=(index,0)
        reopened.feed(b'\x1b[1;3D');self.assertEqual(reopened.doc.blocks[index]['width'],528)
        reopened.render();reopened.feed(b'\x1b[48;3u')
        self.assertNotIn('width',reopened.doc.blocks[index])
        reopened.render();self.assertEqual(reopened.geometry[index]['iw'],400)

    def test_invalid_image_dimensions_are_rejected(self):
        from model import validate_document
        doc=Document();doc.blocks.append({'type':'image','path':'images/sample.png'})
        for width in (0,-1,4097,True,'100',float('nan')):
            doc.blocks[-1]['width']=width
            with self.assertRaises(ValueError):validate_document(doc.data)

if __name__=='__main__':unittest.main()
