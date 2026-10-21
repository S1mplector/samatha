"""Lossless rich documents with Markdown previews for the terminal browser."""
from __future__ import annotations
import copy
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid
import unicodedata
from model import Document, DEFAULT_SETTINGS, paragraph, validate_document, atomic_json, BOLD, ITALIC, UNDERLINE, HIGHLIGHT


def sidecar(path):
    path=Path(path)
    return path.with_name('.'+path.name+'.samatha.json')


def atomic_text(path,text):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as out:
            out.write(text); out.flush(); os.fsync(out.fileno())
        os.replace(tmp,path)
        dfd=os.open(path.parent,os.O_RDONLY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def escape(text):
    return re.sub(r'([\\`*_{}\[\]<>#~])',r'\\\1',text)


def markdown(data):
    lines=['# '+data['title'],'']
    for block in data['blocks']:
        if block['type']=='divider': lines+=['---','']; continue
        if block['type']=='image':
            lines+=['!['+escape(block.get('caption','Image'))+']('+block['path']+')','']; continue
        runs=[]; start=0
        while start<len(block['text']):
            style=block['styles'][start]; end=start+1
            while end<len(block['text']) and block['styles'][end]==style: end+=1
            part=escape(block['text'][start:end])
            # Keep surrounding whitespace outside Markdown emphasis delimiters.
            leading=part[:len(part)-len(part.lstrip())]; trailing=part[len(part.rstrip()):]
            core=part.strip()
            if core:
                if style&BOLD: core='**'+core+'**'
                if style&ITALIC: core='*'+core+'*'
                if style&UNDERLINE: core='<u>'+core+'</u>'
                if style&HIGHLIGHT: core='<mark>'+core+'</mark>'
                part=leading+core+trailing
            runs.append(part); start=end
        prefix={'h1':'# ','h2':'## ','bullet':'- '}.get(block['kind'],'')
        lines += [prefix+''.join(runs),'']
    return '\n'.join(lines).rstrip()+'\n'


def load_markdown(path):
    """Import ordinary Markdown without interpreting it as terminal commands."""
    text=Path(path).read_text(); title=Path(path).stem; blocks=[]; have_title=False
    for line in text.splitlines():
        if not have_title and line.startswith('# '): title=line[2:].strip(); have_title=True; continue
        if line in ('---','***','___'): blocks.append({'type':'divider'}); continue
        image=re.fullmatch(r'!\[(.*?)\]\((images/[^)]+)\)',line)
        if image:
            rel=Path(image[2])
            if len(rel.parts)==2 and '..' not in rel.parts:
                blocks.append({'type':'image','path':image[2],'caption':image[1]}); continue
        kind='body'
        for prefix,k in [('## ','h2'),('# ','h1'),('- ','bullet'),('* ','bullet')]:
            if line.startswith(prefix):kind=k;line=line[len(prefix):];break
        if line or blocks: blocks.append(paragraph(line,kind=kind))
    while len(blocks)>1 and blocks[-1].get('text')=='':blocks.pop()
    return {'id':uuid.uuid4().hex,'title':title,'blocks':blocks or [paragraph()]}


def read_rich(path):
    path=Path(path); rich=sidecar(path)
    if rich.exists():
        # Never silently flatten a damaged rich document into its Markdown preview.
        value=json.loads(rich.read_text()); validate_document(value['document'])
        return value['document'],value.get('markdown_sha256')
    return load_markdown(path),None


def write_rich(path,data):
    """Commit the full-fidelity source first; a preview can always be regenerated."""
    validate_document(data); preview=markdown(data)
    atomic_json(sidecar(path),{'version':1,'document':data,'markdown_sha256':hashlib.sha256(preview.encode()).hexdigest()})
    atomic_text(path,preview)


def safe_name(name):
    name=re.sub(r'[\x00-\x1f/\\]','-',name).strip().lstrip('.')[:100]
    return name or 'Untitled'


def unique_path(parent,name,suffix=''):
    path=parent/(safe_name(name)+suffix); n=2
    while path.exists():path=parent/(safe_name(name)+' '+str(n)+suffix); n+=1
    return path


def import_library(root):
    """One atomic, repeatable import; the original library is never modified."""
    root=Path(root); notes=root/'notes'
    if notes.exists(): return notes
    root.mkdir(parents=True,exist_ok=True)
    lock=open(root/'.lock','a')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if notes.exists():return notes
        stage=Path(tempfile.mkdtemp(prefix='.notes-import-',dir=root))
        try:
            library=root/'library.json'
            if library.exists():
                from model import Store
                data=Store.read_library(library)
                for book in data['notebooks']:
                    folder=unique_path(stage,book['name']);folder.mkdir()
                    atomic_json(folder/'.samatha-settings.json',{**DEFAULT_SETTINGS,**book.get('settings',{})})
                    for note in book['notes']:
                        source=root/book['id']/(note['id']+'.json')
                        document=validate_document(json.loads(source.read_text()))
                        for block in document['blocks']:
                            if block['type']=='image':
                                image=root/book['id']/block['path']
                                if not image.is_file():raise ValueError('Missing image: '+str(image))
                                dest=folder/block['path'];dest.parent.mkdir(exist_ok=True);shutil.copy2(image,dest)
                        write_rich(unique_path(folder,document['title'],'.md'),document)
            atomic_json(stage/'.samatha-import.json',{'version':1,'imported_at':time.time(),'source':str(root)})
            os.replace(stage,notes)
        finally:
            if stage.exists():shutil.rmtree(stage)
    finally:lock.close()
    return notes


class RichStore:
    """Adapter for the existing rich editor, backed by Markdown plus rich sidecars."""
    def __init__(self,path):
        self.path=Path(path).resolve();self.root=self.path.parent
        self.lock=open(self.root/'.rich-editor.lock','a')
        try:fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close();raise RuntimeError('A rich editor is already open in this folder.')
        try:
            self.recovered=[];self.last_backup={}
            data,digest=read_rich(self.path)
            self.current_digest=hashlib.sha256(self.path.read_bytes()).hexdigest()
            self.needs_save=not sidecar(self.path).exists() or digest!=self.current_digest
            # External edits are kept as a separate copy, never discarded or used to flatten rich data.
            if digest and digest!=self.current_digest:
                conflict=self.path.with_name(self.path.stem+'-external-'+str(time.time_ns())+'.md')
                shutil.copy2(self.path,conflict)
                self.recovered.append('Kept external Markdown changes in '+conflict.name+'. Rich formatting restored.')
            self.document=Document(data)
            settings=copy.deepcopy(DEFAULT_SETTINGS)
            self.settings_path=self.root/'.samatha-settings.json'
            if self.settings_path.exists():settings.update(json.loads(self.settings_path.read_text()))
            self.saved_settings=copy.deepcopy(settings) if self.settings_path.exists() else None
            self.book={'id':'.','name':self.root.name,'settings':settings,'notes':[{'id':data['id'],'title':data['title']}]}
            self.library={'notebooks':[self.book],'active_book':'.','active_note':data['id']}
        except Exception:
            self.lock.close()
            raise

    def load(self,book,note_id):return self.document
    def save_index(self):
        settings=self.book['settings']
        if settings!=self.saved_settings:
            atomic_json(self.settings_path,settings);self.saved_settings=copy.deepcopy(settings)
    def save(self,book,doc):
        actual=hashlib.sha256(self.path.read_bytes()).hexdigest() if self.path.exists() else None
        if actual!=self.current_digest:raise RuntimeError('The Markdown file changed outside Samatha. Reopen it to preserve both versions.')
        rich=sidecar(self.path);now=time.time()
        if not doc.dirty and not self.needs_save and rich.exists():
            self.save_index();return
        if rich.exists() and now-self.last_backup.get(str(rich),0)>60:
            dest=self.root/'.samatha-backups'/doc.data['id'];dest.mkdir(parents=True,exist_ok=True)
            shutil.copy2(rich,dest/(str(time.time_ns())+'.json'))
            self.last_backup[str(rich)]=now
        write_rich(self.path,doc.data)
        self.current_digest=hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.book['notes'][0]['title']=doc.data['title'];self.save_index();doc.dirty=False;self.needs_save=False

    def new_note(self,book,title='Untitled'):
        data=Document().data;data['title']=title
        self.path=unique_path(self.root,title,'.md');write_rich(self.path,data)
        self.current_digest=hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.needs_save=False
        self.document=Document(data);book['notes']=[{'id':data['id'],'title':title}]
        return self.document


def rename_title(path,title):
    """Change the displayed title, keeping the filename, rich content and links."""
    title=title.strip()
    if not title or len(title)>120 or any(unicodedata.category(c)=='Cc' for c in title):
        raise ValueError('Use a title of 1–120 characters without control characters.')
    store=RichStore(path)
    try:
        if sidecar(store.path).exists():
            store.document.data['title']=title
            store.document.mark()
            store.save(store.book,store.document)
        else:
            # Ordinary Markdown must not be re-imported/flattened just to rename it.
            raw=store.path.read_bytes().decode('utf-8')
            heading=re.search(r'^# [^\r\n]*',raw,re.MULTILINE)
            if heading:
                preview=raw[:heading.start()]+'# '+title+raw[heading.end():]
            else:
                newline='\r\n' if '\r\n' in raw else '\n'
                preview='# '+title+newline+newline+raw
            atomic_text(store.path,preview)
        return store.recovered
    finally:
        store.lock.close()


if __name__=='__main__':
    import argparse
    import sys
    parser=argparse.ArgumentParser(description='Update Samatha note titles without opening an editor.')
    parser.add_argument('--rename',type=Path,required=True)
    parser.add_argument('title')
    args=parser.parse_args()
    try:
        for message in rename_title(args.rename,args.title):print(message)
    except Exception as error:
        print(str(error),file=sys.stderr)
        raise SystemExit(1)
