import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from model import Document, Store, paragraph, BOLD, ITALIC, UNDERLINE, RichParser, atomic_json, validate_document, previous_word

class EditingTests(unittest.TestCase):
    def test_markdown_rule_on_enter_is_undoable(self):
        d=Document();d.insert('---');d.newline()
        self.assertEqual(d.blocks,[{'type':'divider'},paragraph()]);self.assertEqual(d.cursor,(1,0))
        d.undo();self.assertEqual(d.blocks,[paragraph('---')]);d.undo(True)
        d.insert('After');self.assertEqual(d.blocks[1]['text'],'After')
        for text in ('before ---','--','----'):
            d=Document();d.insert(text);d.newline();self.assertEqual(d.blocks[0]['type'],'text')

    def test_undo_snapshots_isolate_styles_and_extension_metadata(self):
        d=Document();d.insert('abc');d.data['extension']={'tags':['original']}
        d.blocks[0]['extension']={'nested':['original']}
        before=copy.deepcopy(d.data);d.checkpoint()
        d.blocks[0]['styles'][0]=BOLD;d.data['extension']['tags'].append('changed')
        d.blocks[0]['extension']['nested'].append('changed')
        after=copy.deepcopy(d.data);d.undo();self.assertEqual(d.data,before)
        d.undo(True);self.assertEqual(d.data,after)
        d.undo();d.insert('new');self.assertEqual(d.redo_stack,[])

    def test_typing_style_and_selection(self):
        d=Document(); d.insert('one two'); d.anchor=(0,4); d.cursor=(0,7); d.format(BOLD)
        self.assertEqual(d.blocks[0]['styles'],[0]*4+[BOLD]*3)
        d.format(ITALIC); self.assertEqual(d.blocks[0]['styles'][4:], [3]*3)
        d.insert('three'); self.assertEqual(d.blocks[0]['text'],'one three')
        validate_document(d.data)

    def test_multiline_paste_at_middle_retains_suffix_style(self):
        d=Document(); d.typing_style=BOLD; d.insert('abcd'); d.cursor=(0,2); d.typing_style=ITALIC
        d.insert('X\nY'); self.assertEqual([b['text'] for b in d.blocks],['abX','Ycd'])
        self.assertEqual(d.blocks[1]['styles'],[ITALIC,BOLD,BOLD]); self.assertEqual(d.cursor,(1,1))

    def test_range_delete_merges_paragraphs(self):
        d=Document(); d.insert('first\nmiddle\nlast'); d.anchor=(0,2); d.cursor=(2,2); d.delete()
        self.assertEqual(d.blocks[0]['text'],'fist'); self.assertEqual(d.cursor,(0,2)); self.assertEqual(len(d.blocks),1)
        d.undo(); self.assertEqual(len(d.blocks),3)
        d.undo(True); self.assertEqual(d.blocks[0]['text'],'fist')

    def test_delete_empty_and_paragraph_boundary(self):
        d=Document(); d.delete(); d.delete(False); self.assertEqual(d.blocks,[paragraph()])
        d.insert('one\ntwo'); d.cursor=(1,0); d.delete(); self.assertEqual(d.blocks[0]['text'],'onetwo')

    def test_word_delete_is_undoable(self):
        d=Document(); d.insert('hello world  '); d.delete_word(); self.assertEqual(d.blocks[0]['text'],'hello ')
        d.undo(); self.assertEqual(d.blocks[0]['text'],'hello world  ')
        self.assertEqual(previous_word('hello,',6),(5,6))

    def test_combining_accent_backspace(self):
        d=Document(); d.insert('cafe\u0301'); d.delete(); self.assertEqual(d.blocks[0]['text'],'caf')

    def test_image_insertion_splits_and_is_undoable(self):
        d=Document(); d.insert('hello world'); d.cursor=(0,5)
        d.add_block({'type':'image','path':'images/example.png','caption':'Example'})
        self.assertEqual([b['type'] for b in d.blocks],['text','image','text'])
        self.assertEqual(d.blocks[2]['text'],' world'); validate_document(d.data)
        d.undo(); self.assertEqual(d.blocks[0]['text'],'hello world')

    def test_image_delete_preserves_following_text(self):
        d=Document(); d.add_block({'type':'image','path':'images/x.png'}); d.insert('after')
        d.anchor=(1,0); d.cursor=(1,1); d.delete(); self.assertEqual(d.blocks[-1]['text'],'after')
        self.assertNotIn('image',[b['type'] for b in d.blocks])

    def test_paste_rich_text_preserves_format(self):
        p=RichParser(); p.feed('<h1>Title</h1><p>Hello <b>bold</b> <em>italic</em></p><script>bad()</script>')
        blocks=p.result(); self.assertEqual(blocks[0]['kind'],'h1')
        self.assertEqual(blocks[1]['text'],'Hello bold italic'); self.assertEqual(blocks[1]['styles'][6:10],[1]*4)
        d=Document(); d.insert_blocks(blocks); validate_document(d.data)

    def test_rich_paste_into_selection(self):
        d=Document(); d.insert('hello world'); d.anchor=(0,6); d.cursor=(0,11)
        d.insert_blocks([paragraph('earth',[BOLD]*5)])
        self.assertEqual(d.blocks[0]['text'],'hello earth'); self.assertEqual(d.blocks[0]['styles'][-5:],[BOLD]*5)

    def test_control_characters_not_saved(self):
        d=Document(); d.insert('a\x00\x1b\x7fb\r\nc'); self.assertEqual([b['text'] for b in d.blocks],['ab','c'])

    def test_invalid_image_path_rejected(self):
        d=Document(); d.blocks.append({'type':'image','path':'../../private.png'})
        with self.assertRaises(ValueError): validate_document(d.data)

class PersistenceTests(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def test_atomic_failure_keeps_previous_file(self):
        path=self.root/'page.json'; atomic_json(path,{'text':'safe'})
        with patch('model.os.replace',side_effect=OSError('disk full')):
            with self.assertRaises(OSError): atomic_json(path,{'text':'new'})
        self.assertEqual(json.loads(path.read_text()),{'text':'safe'})
        self.assertEqual(len(list(self.root.iterdir())),1)

    def test_save_reload_and_backup(self):
        s=Store(self.root); b=s.library['notebooks'][0]; d=s.load(b,b['notes'][0]['id'])
        d.insert('Important thoughts'); s.save(b,d)
        self.assertFalse(d.dirty); self.assertEqual(s.load(b,d.data['id']).blocks[0]['text'],'Important thoughts')
        self.assertTrue(list((self.root/'backups').rglob('*.json')))
        s.lock.close()

    def test_second_instance_cannot_write(self):
        s=Store(self.root)
        with self.assertRaises(RuntimeError): Store(self.root)
        s.lock.close()

    def test_damaged_index_recovers_without_losing_original(self):
        s=Store(self.root); s.lock.close(); (self.root/'library.json').write_text('{broken')
        recovered=Store(self.root); self.assertTrue(recovered.recovered)
        self.assertTrue(list((self.root/'recovery').glob('*.json'))); recovered.lock.close()

    def test_damaged_page_uses_valid_backup(self):
        s=Store(self.root); b=s.library['notebooks'][0]; d=s.load(b,b['notes'][0]['id']); d.insert('safe'); s.save(b,d)
        s.last_backup.clear(); d.insert(' newer'); s.save(b,d)
        (self.root/b['id']/(d.data['id']+'.json')).write_text('broken')
        restored=s.load(b,d.data['id']); self.assertEqual(restored.blocks[0]['text'],'safe')
        self.assertTrue(s.recovered); s.lock.close()

    def test_failed_save_does_not_clear_dirty_flag(self):
        s=Store(self.root); b=s.library['notebooks'][0]; d=s.load(b,b['notes'][0]['id']); d.insert('valuable')
        with patch('model.atomic_json',side_effect=OSError('disk full')):
            with self.assertRaises(OSError): s.save(b,d)
        self.assertTrue(d.dirty); s.lock.close()

if __name__=='__main__': unittest.main()
