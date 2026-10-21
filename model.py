"""Lossless note editing and atomic local storage; no terminal dependencies."""
from __future__ import annotations
import copy
import datetime as dt
import fcntl
import html
import json
import os
from pathlib import Path
import tempfile
import time
import uuid
import unicodedata
from html.parser import HTMLParser

BOLD, ITALIC, UNDERLINE, HIGHLIGHT = 1, 2, 4, 8

def paragraph(text='', styles=None, kind='body'):
    return {'type': 'text', 'text': text, 'styles': list(styles) if styles is not None else [0]*len(text), 'kind': kind}

def length(block):
    return len(block['text']) if block['type'] == 'text' else 1

def clean_text(text):
    return ''.join(c for c in text.replace('\r\n', '\n').replace('\r', '\n') if c in '\n\t' or ord(c) >= 32 and ord(c) != 127).replace('\t', '    ')

def previous_word(text, offset):
    end=offset
    while offset>0 and text[offset-1].isspace(): offset-=1
    if offset:
        word=text[offset-1].isalnum() or text[offset-1]=='_'
        while offset>0 and not text[offset-1].isspace() and (text[offset-1].isalnum() or text[offset-1]=='_')==word: offset-=1
    return offset,end

class Document:
    def __init__(self, data=None):
        self.data = data or {'id': uuid.uuid4().hex, 'title': 'Untitled', 'blocks': [paragraph()]}
        self.cursor = (0, 0)
        self.anchor = None
        self.typing_style = 0
        self.undo_stack = []
        self.redo_stack = []
        self.dirty = False
        self.changed_at = 0

    @property
    def blocks(self): return self.data['blocks']

    def mark(self):
        self.dirty = True
        self.changed_at = time.monotonic()

    def snapshot(self):
        # Style runs contain immutable integer flags. Copy their list directly
        # instead of recursively visiting every character in every paragraph.
        data={key:copy.deepcopy(value) for key,value in self.data.items() if key!='blocks'}
        data['blocks']=[{key:value.copy() if key=='styles' else copy.deepcopy(value)
                         for key,value in block.items()} for block in self.blocks]
        return data

    def checkpoint(self):
        self.undo_stack.append((self.snapshot(), self.cursor, self.anchor))
        self.undo_stack = self.undo_stack[-150:]
        self.redo_stack.clear()

    def undo(self, redo=False):
        source, target = (self.redo_stack, self.undo_stack) if redo else (self.undo_stack, self.redo_stack)
        if source:
            target.append((self.snapshot(), self.cursor, self.anchor))
            self.data, self.cursor, self.anchor = source.pop()
            self.mark()

    def selection(self):
        if self.anchor is None or self.anchor == self.cursor: return None
        return tuple(sorted((self.anchor, self.cursor)))

    def _delete_selection(self):
        selected = self.selection()
        if not selected: return False
        (a, x), (b, y) = selected
        first, last = self.blocks[a], self.blocks[b]
        if a == b and first['type'] == 'text':
            first['text'] = first['text'][:x] + first['text'][y:]
            first['styles'][x:y] = []
        else:
            prefix = first['text'][:x] if first['type'] == 'text' else ''
            suffix = last['text'][y:] if last['type'] == 'text' else ''
            styles = (first['styles'][:x] if first['type'] == 'text' else []) + (last['styles'][y:] if last['type'] == 'text' else [])
            replacement = []
            if first['type'] != 'text' and x == 1: replacement.append(copy.deepcopy(first))
            replacement.append(paragraph(prefix+suffix, styles, first.get('kind', 'body')))
            pos = a+len(replacement)-1
            if last['type'] != 'text' and y == 0: replacement.append(copy.deepcopy(last))
            self.blocks[a:b+1] = replacement
            a = pos
            x = len(prefix)
        self.cursor, self.anchor = (a, x), None
        return True

    def insert(self, text):
        text = clean_text(text)
        if not text: return
        self.checkpoint()
        self._delete_selection()
        i, offset = self.cursor
        block = self.blocks[i]
        if block['type'] != 'text':
            i += offset
            self.blocks.insert(i, paragraph())
            offset = 0
            block = self.blocks[i]
        parts = text.split('\n')
        before, after = block['text'][:offset], block['text'][offset:]
        bs, ats = block['styles'][:offset], block['styles'][offset:]
        if len(parts) == 1:
            block['text'] = before + text + after
            block['styles'] = bs + [self.typing_style]*len(text) + ats
            self.cursor = (i, offset+len(text))
        else:
            new = [paragraph(before+parts[0], bs+[self.typing_style]*len(parts[0]), block['kind'])]
            new += [paragraph(p, [self.typing_style]*len(p), 'bullet' if block['kind']=='bullet' else 'body') for p in parts[1:]]
            end = len(new[-1]['text'])
            new[-1]['text'] += after
            new[-1]['styles'] += ats
            self.blocks[i:i+1] = new
            self.cursor = (i+len(new)-1, end)
        self.mark()

    def add_block(self, block):
        self.checkpoint()
        self._delete_selection()
        i, off = self.cursor
        current = self.blocks[i]
        if current['type'] == 'text':
            head = paragraph(current['text'][:off], current['styles'][:off], current['kind'])
            tail = paragraph(current['text'][off:], current['styles'][off:])
            self.blocks[i:i+1] = [head, block, tail]
            self.cursor = (i+2, 0)
        else:
            self.blocks[i+off:i+off] = [block, paragraph()]
            self.cursor = (i+off+1, 0)
        self.anchor = None
        self.mark()

    def resize_image(self,index,width=None,checkpoint=True):
        block=self.blocks[index]
        if block['type']!='image':return False
        if width is not None:
            width=max(1,min(4096,round(width)))
        if block.get('width')==width:return False
        if checkpoint:self.checkpoint()
        if width is None:block.pop('width',None)
        else:block['width']=width
        self.mark()
        return True

    def insert_blocks(self, blocks):
        if not blocks: return
        self.checkpoint()
        self._delete_selection()
        i, off = self.cursor
        current = self.blocks[i]
        blocks = copy.deepcopy(blocks)
        if current['type'] == 'text':
            before = paragraph(current['text'][:off], current['styles'][:off], current['kind'])
            after = paragraph(current['text'][off:], current['styles'][off:], current['kind'])
            if blocks[0]['type'] == 'text':
                blocks[0]['text'] = before['text'] + blocks[0]['text']
                blocks[0]['styles'] = before['styles'] + blocks[0]['styles']
            else: blocks.insert(0, before)
            if blocks[-1]['type'] == 'text':
                end = len(blocks[-1]['text'])
                blocks[-1]['text'] += after['text']
                blocks[-1]['styles'] += after['styles']
            else:
                blocks.append(after); end = 0
            self.blocks[i:i+1] = blocks
        else:
            i += off
            if blocks[-1]['type'] != 'text': blocks.append(paragraph())
            end = len(blocks[-1]['text'])
            self.blocks[i:i] = blocks
        self.cursor, self.anchor = (i+len(blocks)-1, end), None
        self.mark()

    def delete(self, backwards=True):
        i, off = self.cursor
        if not self.selection():
            if backwards:
                if off:
                    start=off-1
                    if block_text := self.blocks[i].get('text'):
                        while start>0 and (unicodedata.combining(block_text[start]) or block_text[start] in ('\ufe0f','\ufe0e')): start-=1
                    target = (i, start)
                elif i: target = (i-1, length(self.blocks[i-1]))
                else: return
            else:
                if off < length(self.blocks[i]): target = (i, off+1)
                elif i < len(self.blocks)-1: target = (i+1, 0)
                else: return
            self.anchor = target
        self.checkpoint()
        self._delete_selection()
        self.mark()

    def delete_word(self):
        if self.selection(): self.delete(); return
        i,off=self.cursor; block=self.blocks[i]
        if block['type']!='text' or not off: self.delete(); return
        start,_=previous_word(block['text'],off)
        self.anchor=(i,start); self.delete()

    def format(self, bit):
        selected = self.selection()
        if not selected:
            self.typing_style ^= bit
            return
        self.checkpoint()
        (a,x),(b,y) = selected
        entries = []
        for i in range(a,b+1):
            block = self.blocks[i]
            if block['type'] != 'text': continue
            entries.extend((block,j) for j in range(x if i==a else 0, y if i==b else length(block)))
        remove = bool(entries) and all(block['styles'][j] & bit for block,j in entries)
        for block,j in entries:
            block['styles'][j] = block['styles'][j] & ~bit if remove else block['styles'][j] | bit
        self.mark()

    def set_kind(self, kind):
        self.checkpoint()
        selected = self.selection()
        a,b = (selected[0][0],selected[1][0]) if selected else (self.cursor[0],self.cursor[0])
        for block in self.blocks[a:b+1]:
            if block['type']=='text': block['kind'] = kind
        self.mark()

    def selected_blocks(self):
        selected = self.selection()
        if not selected: return []
        (a,x),(b,y) = selected
        result = copy.deepcopy(self.blocks[a:b+1])
        if result[-1]['type']=='text':
            result[-1]['text'] = result[-1]['text'][:y]; result[-1]['styles'] = result[-1]['styles'][:y]
        elif y==0: result.pop()
        if result and result[0]['type']=='text':
            result[0]['text'] = result[0]['text'][x:]; result[0]['styles'] = result[0]['styles'][x:]
        elif result and x==1: result.pop(0)
        return result

    def plain_selection(self):
        return '\n'.join(b.get('text', '[Image]' if b['type']=='image' else '────────') for b in self.selected_blocks())

class RichParser(HTMLParser):
    """Import common clipboard HTML without executing or fetching anything."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = [paragraph()]
        self.stack = []
        self.style = 0
        self.ignored = 0

    def boundary(self, kind='body'):
        if self.blocks[-1]['text']: self.blocks.append(paragraph(kind=kind))
        else: self.blocks[-1]['kind'] = kind

    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'): self.ignored += 1
        self.stack.append((tag,self.style))
        attrs = dict(attrs); css=attrs.get('style','').lower().replace(' ','')
        if tag in ('b','strong') or 'font-weight:bold' in css or 'font-weight:700' in css: self.style |= BOLD
        if tag in ('i','em') or 'font-style:italic' in css: self.style |= ITALIC
        if tag=='u' or 'text-decoration:underline' in css: self.style |= UNDERLINE
        if tag=='mark': self.style |= HIGHLIGHT
        if tag in ('p','div','h1','h2','h3','li'):
            self.boundary({'h1':'h1','h2':'h2','h3':'h2','li':'bullet'}.get(tag,'body'))
        if tag=='br': self.blocks.append(paragraph())

    def handle_endtag(self, tag):
        if tag in ('script','style'): self.ignored=max(0,self.ignored-1)
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:
                self.style = self.stack[i][1]; self.stack=self.stack[:i]; break
        if tag in ('p','div','h1','h2','h3','li') and self.blocks[-1]['text']: self.blocks.append(paragraph())

    def handle_data(self, data):
        if self.ignored: return
        data = clean_text(data).replace('\n',' ')
        if not self.blocks[-1]['text'] and not data.strip(): return
        self.blocks[-1]['text'] += data
        self.blocks[-1]['styles'] += [self.style]*len(data)

    def result(self):
        while len(self.blocks)>1 and not self.blocks[-1]['text']: self.blocks.pop()
        return self.blocks

def to_html(blocks):
    output=[]
    for block in blocks:
        if block['type']=='divider': output.append('<hr>'); continue
        if block['type']!='text': continue
        tag={'h1':'h1','h2':'h2','bullet':'li'}.get(block['kind'],'p')
        runs=[]
        for char,style in zip(block['text'],block['styles']):
            value=html.escape(char)
            for bit,t in [(BOLD,'strong'),(ITALIC,'em'),(UNDERLINE,'u'),(HIGHLIGHT,'mark')]:
                if style & bit: value=f'<{t}>{value}</{t}>'
            runs.append(value)
        output.append(f'<{tag}>{"".join(runs)}</{tag}>')
    return ''.join(output)

DEFAULT_SETTINGS={'font':'DejaVu Sans','font_size':19,'theme':'Paper','autosave':True,'autosave_seconds':1,'focus_minutes':25,'break_minutes':5,'line_spacing':1.45}

def atomic_json(path, data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.'+path.name)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(data,f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
        dfd=os.open(path.parent,os.O_RDONLY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

class Store:
    def __init__(self, root):
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True)
        self.lock=open(self.root/'.lock','a')
        try: fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError('Samatha is already open. Use the existing window.')
        self.index_path=self.root/'library.json'
        self.recovered=[]
        if self.index_path.exists():
            try:
                self.library=self.read_library(self.index_path)
            except (ValueError,KeyError,TypeError):
                backup=self.index_path.with_suffix('.backup.json')
                self.library=self.read_library(backup)
                self.preserve_corrupt(self.index_path)
                atomic_json(self.index_path,self.library)
                self.recovered.append('Recovered the notebook list from its backup.')
        else:
            self.library={'version':1,'notebooks':[]}
            self.new_notebook('Personal')
        self.last_backup={}

    @staticmethod
    def read_library(path):
        data=json.loads(path.read_text())
        if data.get('version')!=1 or not isinstance(data.get('notebooks'),list) or not data['notebooks']: raise ValueError('Invalid notebook library')
        for b in data['notebooks']:
            if not isinstance(b['name'],str) or not b['notes'] or not valid_id(b['id']): raise ValueError('Invalid notebook')
            for n in b['notes']:
                if not valid_id(n['id']) or not isinstance(n['title'],str): raise ValueError('Invalid page index')
        return data

    def preserve_corrupt(self,path):
        recovery=self.root/'recovery'; recovery.mkdir(exist_ok=True)
        dest=recovery/(path.stem+'-'+str(time.time_ns())+'.json')
        dest.write_bytes(path.read_bytes())

    def save_index(self):
        # A matching index backup is committed only after the primary is durable.
        atomic_json(self.index_path,self.library)
        atomic_json(self.index_path.with_suffix('.backup.json'),self.library)

    def new_notebook(self,name):
        book={'id':uuid.uuid4().hex,'name':name,'settings':copy.deepcopy(DEFAULT_SETTINGS),'notes':[]}
        self.library['notebooks'].append(book)
        self.new_note(book,'First page')
        self.save_index(); return book

    def new_note(self,book,title='Untitled'):
        doc=Document(); doc.data['title']=title
        book['notes'].append({'id':doc.data['id'],'title':title})
        atomic_json(self.root/book['id']/(doc.data['id']+'.json'),doc.data)
        self.save_index(); return doc

    def load(self,book,note_id):
        path=self.root/book['id']/(note_id+'.json')
        try: data=validate_document(json.loads(path.read_text()))
        except (ValueError,KeyError,TypeError,FileNotFoundError):
            backup=self.root/'backups'/book['id']/note_id
            for candidate in sorted(backup.glob('*.json'),reverse=True):
                try: data=validate_document(json.loads(candidate.read_text()))
                except (ValueError,KeyError,TypeError): continue
                if path.exists(): self.preserve_corrupt(path)
                atomic_json(path,data)
                self.recovered.append('Recovered '+data['title']+' from its latest valid backup.'); break
            else: raise ValueError('This page is damaged and has no valid backup. Its files have been left untouched: '+str(path))
        return Document(data)

    def save(self,book,doc):
        path=self.root/book['id']/(doc.data['id']+'.json')
        now=time.time()
        if path.exists() and now-self.last_backup.get(str(path),0)>60:
            backup=self.root/'backups'/book['id']/doc.data['id']; backup.mkdir(parents=True,exist_ok=True)
            atomic_json(backup/(str(time.time_ns())+'.json'),json.loads(path.read_text()))
            for p in sorted(backup.glob('*.json'))[:-20]: p.unlink()
            self.last_backup[str(path)]=now
        doc.data['updated']=dt.datetime.now().astimezone().isoformat()
        atomic_json(path,doc.data)
        for note in book['notes']:
            if note['id']==doc.data['id']: note['title']=doc.data['title']
        self.library['active_book']=book['id']; self.library['active_note']=doc.data['id']
        self.save_index(); doc.dirty=False

def valid_id(value):
    return isinstance(value,str) and len(value)==32 and all(c in '0123456789abcdef' for c in value)

def validate_document(data):
    if not valid_id(data['id']) or not isinstance(data['title'],str) or not isinstance(data['blocks'],list) or not data['blocks']: raise ValueError('Invalid page')
    for block in data['blocks']:
        kind=block['type']
        if kind=='text':
            if not isinstance(block['text'],str) or len(block['styles'])!=len(block['text']) or not all(isinstance(s,int) and 0<=s<16 for s in block['styles']): raise ValueError('Invalid styled text')
            if block['kind'] not in ('body','h1','h2','bullet'): raise ValueError('Invalid paragraph')
        elif kind=='image':
            path=Path(block['path'])
            if path.is_absolute() or '..' in path.parts or len(path.parts)!=2 or path.parts[0]!='images': raise ValueError('Invalid image path')
            if 'width' in block and (type(block['width']) is not int or not 1<=block['width']<=4096): raise ValueError('Invalid image width')
        elif kind!='divider': raise ValueError('Invalid block')
    return data
