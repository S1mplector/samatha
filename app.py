#!/usr/bin/python3
"""Notebook: a rich writing surface carried by Kitty's terminal graphics protocol."""
from __future__ import annotations
import argparse
import base64
import copy
import datetime as dt
import fcntl
import hashlib
import html
import io
import json
import math
import os
from pathlib import Path
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time
import tty
import unicodedata
import uuid
import cairo
from drawing import color, rounded, text_layout, label, Pango, PangoCairo
from image_blocks import ImageEditing
from text_blocks import TextLayout
from render_cache import RenderCache
from terminal_graphics import KittyRenderer, terminal_mode
from editor_commands import default_commands
from PIL import Image, ImageOps
from model import Document, Store, DEFAULT_SETTINGS, paragraph, length, RichParser, to_html, previous_word, BOLD, ITALIC, UNDERLINE, HIGHLIGHT

HERE=Path(__file__).resolve().parent
THEMES=json.loads((HERE/'tui/themes.json').read_text())
PALETTES={p['name']:p for p in THEMES.values()}
PALETTES['Paper']=PALETTES['Light']
PALETTES['Night']=PALETTES['Dark']


class Pomodoro:
    def __init__(self): self.phase='Focus'; self.remaining=0; self.deadline=None; self.started=False
    def toggle(self,settings):
        if self.deadline:
            self.remaining=max(0,self.deadline-time.monotonic()); self.deadline=None
        else:
            if not self.started: self.remaining=settings['focus_minutes']*60; self.started=True
            self.deadline=time.monotonic()+self.remaining
    def reset(self): self.__init__()
    def seconds(self): return max(0,math.ceil(self.deadline-time.monotonic())) if self.deadline else int(self.remaining)
    def tick(self,settings):
        if self.deadline and time.monotonic()>=self.deadline:
            self.phase='Break' if self.phase=='Focus' else 'Focus'
            self.remaining=settings['break_minutes' if self.phase=='Break' else 'focus_minutes']*60
            self.deadline=None; return True
        return False

class Notebook(ImageEditing,TextLayout):
    def __init__(self,root,store=None):
        self.store=store if store is not None else Store(root)
        self.book=next((b for b in self.store.library['notebooks'] if b['id']==self.store.library.get('active_book')),self.store.library['notebooks'][0])
        self.book['settings']={**DEFAULT_SETTINGS,**self.book.get('settings',{})}
        notes=self.book['notes']; note=next((n for n in notes if n['id']==self.store.library.get('active_note')),notes[0])
        self.doc=self.store.load(self.book,note['id'])
        self.scroll=0; self.overlay=None; self.prompt=None; self.message=''; self.message_until=0
        self.buttons=[]; self.geometry=[]; self.width=1100; self.height=800
        self.timer=Pomodoro(); self.running=True; self.drag=False
        self.commands=default_commands()
        self.fonts=['DejaVu Sans','DejaVu Serif','Liberation Sans','Liberation Serif','DejaVu Sans Mono']
        self.font_index=0; self.cursor_visible=True; self.last_saved=time.time()
        self.clip_process=None; self.clip_owner=None; self.buffer=b''; self.pasting=False; self.paste_data=b''
        self.render_needed=True; self.last_clock=''; self.error=None
        self.image_cache=RenderCache(32,64*1024*1024); self.paragraph_cache=RenderCache(); self.terminal=KittyRenderer(); self.image_drag=None; self.body_top=188; self.body_bottom=760; self.content_height=0
        self.goal_x=None; self.last_hit=None; self.ctrl_held=False; self.focus_button=-1
        self.book_offset=0; self.last_durable=time.monotonic(); self.last_save_attempt=0
        if self.store.recovered: self.notify(' '.join(self.store.recovered),30)

    @property
    def settings(self): return self.book['settings']
    @property
    def palette(self): return PALETTES[self.settings['theme']]

    def notify(self,msg,seconds=4):
        self.message=msg; self.message_until=time.monotonic()+seconds; self.render_needed=True

    def save(self):
        try:
            self.store.save(self.book,self.doc); self.last_saved=time.time(); self.last_durable=time.monotonic(); self.error=None
            return True
        except Exception as e:
            self.error='Could not save: '+str(e); self.notify(self.error,30); return False

    def request_prompt(self,title,initial,callback):
        self.prompt={'title':title,'value':initial,'callback':callback,'replace':True}; self.render_needed=True

    def button(self,ctx,text,x,y,w,h,action,active=False,quiet=False):
        p=self.palette
        if active or not quiet:
            color(ctx,p['tint'] if active else p['panel']); rounded(ctx,x,y,w,h,7); ctx.fill()
        lay=text_layout(ctx,text,13,bold=active)
        tw,th=lay.get_pixel_size()
        color(ctx,p['accent'] if active else p['text']); ctx.move_to(x+(w-tw)/2,y+(h-th)/2); PangoCairo.show_layout(ctx,lay)
        self.buttons.append((x,y,w,h,action))

    def layout_document(self,ctx):
        available=max(180,min(800,self.width-120))
        left=(self.width-available)/2
        y=0; result=[]
        for i,block in enumerate(self.doc.blocks):
            kind=block['type']; geom={'index':i,'x':left,'y':y,'w':available,'block':block}
            if kind=='text':
                size=self.settings['font_size']*{'h1':1.65,'h2':1.3}.get(block['kind'],1)
                indent=24 if block['kind']=='bullet' else 0
                self.layout_text(ctx,geom,size,indent)
            elif kind=='divider': geom['h']=30
            else:
                self.layout_image(geom,available)
            result.append(geom); y+=geom['h']+12
        self.geometry=result; self.content_height=y

    def ensure_cursor(self):
        if not self.geometry: return
        g=self.geometry[self.doc.cursor[0]]
        top=g.get('caret',(0,g['y'],0))[1]
        bottom=top+g.get('caret',(0,0,g['h']))[2]
        viewport=self.body_bottom-self.body_top
        if top<self.scroll: self.scroll=max(0,top-16)
        elif bottom>self.scroll+viewport: self.scroll=bottom-viewport+24
        self.scroll=max(0,min(self.scroll,max(0,self.content_height-viewport+40)))

    def render(self,width=None,height=None,follow=False,diagnostic=True):
        if width: self.width=width
        if height: self.height=height
        self.body_bottom=self.height-46
        p=self.palette; w,h=self.width,self.height
        surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,w,h); ctx=cairo.Context(surface)
        color(ctx,p['bg']); ctx.paint(); self.buttons=[]
        if w<760 or h<420:
            label(ctx,'Enlarge the terminal.',30,40,22,p['text'],True)
            label(ctx,'',30,85,14,p['muted'],width=w-60)
            label(ctx,'Ctrl+S saves · Ctrl+Q closes',30,130,14,p['muted'])
            return surface
        # Fixed dashboard.
        color(ctx,p['panel']); ctx.rectangle(0,0,w,70); ctx.fill()
        label(ctx,'SAMATHA',26,17,10,p['muted'],True)
        self.button(ctx,self.book['name'][:28]+'  ▾',18,32,210,30,lambda:self.open_overlay('library'),quiet=True)
        stamp=dt.datetime.now().strftime('%a, %d %b   %H:%M')
        label(ctx,stamp,w-490,29,13,p['muted'])
        secs=self.timer.seconds()
        timertext=f'{self.timer.phase}  {secs//60:02}:{secs%60:02}' if self.timer.started else '◷  Focus timer'
        self.button(ctx,timertext,w-270,21,146,32,lambda:self.open_overlay('timer'),active=bool(self.timer.deadline))
        self.button(ctx,'Settings',w-112,21,92,32,lambda:self.open_overlay('settings'),quiet=True)
        # Title and formatting toolbar.
        title=self.doc.data['title']
        label(ctx,title[:55],30,91,20,p['text'],True,width=w-265)
        self.buttons.append((24,82,w-270,41,lambda:self.rename_note()))
        state='Save failed' if self.error else ('Unsaved' if self.doc.dirty else 'Saved')
        if not self.settings['autosave']: state+=' · autosave off'
        label(ctx,state,w-210,98,12,p['accent'] if not self.error else 'c94d42')
        actions=[('B',40,lambda:self.format(BOLD),bool(self.doc.typing_style&BOLD)),('I',36,lambda:self.format(ITALIC),bool(self.doc.typing_style&ITALIC)),('U',36,lambda:self.format(UNDERLINE),bool(self.doc.typing_style&UNDERLINE)),('Highlight',84,lambda:self.format(HIGHLIGHT),bool(self.doc.typing_style&HIGHLIGHT)),('Body',57,lambda:self.set_kind('body'),False),('H1',38,lambda:self.set_kind('h1'),False),('H2',38,lambda:self.set_kind('h2'),False),('• List',61,lambda:self.set_kind('bullet'),False),('Divider',75,self.divider,False),('Image',65,self.image_menu,False)]
        x=22
        for title,bw,fn,active in actions:
            self.button(ctx,title,x,137,bw,30,fn,active,quiet=not active); x+=bw+4
        color(ctx,p['line']); ctx.rectangle(24,178,w-48,1); ctx.fill()
        self.layout_document(ctx)
        if follow: self.ensure_cursor()
        viewport=self.body_bottom-self.body_top
        self.scroll=min(self.scroll,max(0,self.content_height-viewport+40))
        ctx.save(); ctx.rectangle(0,self.body_top,w,viewport); ctx.clip()
        for g in self.geometry:
            x,y=g['x'],self.body_top+g['y']-self.scroll; block=g['block']; i=g['index']
            if y+g['h']<self.body_top or y>self.body_bottom: continue
            if block['type']=='text':
                if block['kind']=='bullet': label(ctx,'•',x,y,g['size'],p['accent'])
                color(ctx,p['text']); ctx.move_to(x+g['indent'],y); PangoCairo.show_layout(ctx,g['layout'])
                if 'caret' in g and not self.overlay and not self.prompt:
                    cx,cy,ch=g['caret']; color(ctx,p['accent']); ctx.rectangle(cx,self.body_top+cy-self.scroll,1.5,ch); ctx.fill()
                if not block['text'] and len(self.doc.blocks)==1:
                    label(ctx,'',x+3,y,self.settings['font_size'],p['muted'],font=self.settings['font'])
            elif block['type']=='divider':
                color(ctx,p['line']); ctx.rectangle(x,y+14,g['w'],1); ctx.fill()
            elif 'image' in g:
                self.render_image(ctx,g,x,y)
            else: label(ctx,'Image unavailable',x,y+10,14,p['muted'])
            if block['type']!='text' and self.doc.cursor[0]==i:
                color(ctx,p['accent']); ctx.set_line_width(2); ctx.rectangle(x-5,y-3,g.get('iw',g['w'])+10,g['h']+4); ctx.stroke()
        ctx.restore()
        if self.content_height>viewport:
            barh=max(24,viewport*viewport/self.content_height)
            bary=self.body_top+(viewport-barh)*self.scroll/max(1,self.content_height-viewport+40)
            color(ctx,p['line']); rounded(ctx,w-12,bary,4,barh,2); ctx.fill()
        color(ctx,p['line']); ctx.rectangle(24,h-35,w-48,1); ctx.fill()
        words=sum(len(b.get('text','').split()) for b in self.doc.blocks)
        hint=self.message if time.monotonic()<self.message_until else ('Backspace deletes the underlined word · release Ctrl to cancel' if self.ctrl_held else 'Ctrl+B bold   Ctrl+V paste   Ctrl+N new page   Esc settings   F1 help')
        label(ctx,hint[:115],25,h-25,11,p['muted'],width=w-140)
        label(ctx,f'{words} words',w-100,h-25,11,p['muted'])
        if self.overlay: self.render_overlay(ctx)
        if self.prompt: self.render_prompt(ctx)
        if 0<=self.focus_button<len(self.buttons) and (self.overlay or self.prompt):
            bx,by,bw,bh,_=self.buttons[self.focus_button]
            color(ctx,p['accent']); ctx.set_line_width(2); rounded(ctx,bx-2,by-2,bw+4,bh+4,8); ctx.stroke()
        return surface

    def open_overlay(self,name):
        self.overlay=name; self.prompt=None; self.focus_button=-1; self.render_needed=True

    def panel(self,ctx,title,width=490,height=450):
        self.buttons=[]
        p=self.palette; color(ctx,'000000',.25); ctx.paint()
        x=(self.width-width)/2; y=max(25,(self.height-height)/2)
        color(ctx,p['white']); rounded(ctx,x,y,width,height,14); ctx.fill()
        label(ctx,title,x+26,y+24,23,p['text'],True)
        self.button(ctx,'×',x+width-48,y+19,30,30,self.close_overlay,quiet=True)
        return x,y,width,height

    def close_overlay(self): self.overlay=None; self.render_needed=True

    def setting(self,key,value):
        self.settings[key]=value
        self.store.save_index(); self.render_needed=True

    def render_overlay(self,ctx):
        p=self.palette
        if self.overlay=='settings':
            x,y,w,h=self.panel(ctx,'Settings',520,490)
            label(ctx,'Settings for '+self.book['name'],x+26,y+61,12,p['muted'])
            label(ctx,'Notebook font',x+26,y+107,14,p['text'])
            self.button(ctx,self.settings['font']+'  ▾',x+214,y+99,276,33,lambda:self.open_overlay('fonts'))
            label(ctx,'Text size',x+26,y+156,14,p['text'])
            self.button(ctx,'−',x+322,y+149,38,32,lambda:self.setting('font_size',max(12,self.settings['font_size']-1)))
            label(ctx,str(self.settings['font_size']),x+377,y+156,14,p['text'])
            self.button(ctx,'+',x+421,y+149,38,32,lambda:self.setting('font_size',min(36,self.settings['font_size']+1)))
            label(ctx,'Appearance',x+26,y+204,14,p['text'])
            self.button(ctx,self.settings['theme']+'  ▾',x+275,y+196,184,33,lambda:self.open_overlay('themes'))
            label(ctx,'Autosave',x+26,y+253,14,p['text'])
            self.button(ctx,'On' if self.settings['autosave'] else 'Off',x+389,y+245,70,33,lambda:self.setting('autosave',not self.settings['autosave']),self.settings['autosave'])
            label(ctx,'Save after',x+26,y+301,14,p['text'])
            self.button(ctx,str(self.settings['autosave_seconds'])+' seconds',x+325,y+293,134,33,lambda:self.setting('autosave_seconds',{1:3,3:5,5:1}[self.settings['autosave_seconds']]))
            label(ctx,'Line spacing',x+26,y+349,14,p['text'])
            self.button(ctx,'Relaxed' if self.settings['line_spacing']>1.3 else 'Compact',x+325,y+341,134,33,lambda:self.setting('line_spacing',1.2 if self.settings['line_spacing']>1.3 else 1.45))

        elif self.overlay=='themes':
            x,y,w,h=self.panel(ctx,'Theme',440,450)
            for i,palette in enumerate(THEMES.values()):
                name=palette['name']
                self.button(ctx,name,x+26,y+78+i*47,w-52,38,lambda n=name:self.choose_theme(n),self.settings['theme']==name)
        elif self.overlay=='fonts':
            x,y,w,h=self.panel(ctx,'Notebook font',480,390)
            label(ctx,'Applies to every page in this notebook.',x+26,y+66,12,p['muted'])
            for i,font in enumerate(self.fonts):
                yy=y+106+i*47
                self.button(ctx,font,x+26,yy,w-52,38,lambda f=font:self.choose_font(f),font==self.settings['font'])
        elif self.overlay=='timer':
            x,y,w,h=self.panel(ctx,'Focus timer',480,365)
            sec=self.timer.seconds() if self.timer.started else self.settings['focus_minutes']*60
            label(ctx,f'{sec//60:02}:{sec%60:02}',x+145,y+80,56,p['accent'])
            label(ctx,self.timer.phase+(' · paused' if self.timer.started and not self.timer.deadline else ''),x+177,y+153,14,p['muted'])
            self.button(ctx,'Pause' if self.timer.deadline else 'Start',x+110,y+195,120,39,self.toggle_timer,True)
            self.button(ctx,'Reset',x+246,y+195,120,39,self.reset_timer)
            label(ctx,'Focus',x+28,y+261,13,p['muted'])
            self.button(ctx,str(self.settings['focus_minutes'])+' min',x+82,y+252,103,32,lambda:self.number_setting('focus_minutes','Focus minutes',1,180))
            label(ctx,'Break',x+255,y+261,13,p['muted'])
            self.button(ctx,str(self.settings['break_minutes'])+' min',x+314,y+252,103,32,lambda:self.number_setting('break_minutes','Break minutes',1,60))
            label(ctx,'',x+28,y+316,11,p['muted'])
        elif self.overlay=='library':
            x,y,w,h=self.panel(ctx,'Notebooks',600,min(self.height-70,580))
            self.button(ctx,'+ Notebook',x+25,y+69,137,32,self.new_book)
            self.button(ctx,'+ Page',x+185,y+69,98,32,self.new_note)
            self.button(ctx,'Rename notebook',x+306,y+69,175,32,self.rename_book)
            limit=max(1,int((h-169)/39))
            for i,book in enumerate(self.store.library['notebooks'][self.book_offset:self.book_offset+limit]):
                self.button(ctx,book['name'][:20],x+25,y+121+i*39,170,33,lambda b=book:self.switch_book(b),book['id']==self.book['id'])
            label(ctx,self.book['name'].upper()[:25],x+223,y+120,11,p['muted'],True)
            notes=self.book['notes']; start=max(0,self.font_index)
            for i,note in enumerate(notes[start:start+limit]):
                self.button(ctx,note['title'][:34],x+216,y+145+i*39,w-244,33,lambda n=note:self.switch_note(n),note['id']==self.doc.data['id'])
            if len(notes)>limit:
                self.button(ctx,'↑',x+w-95,y+h-39,30,28,lambda:self.page_library(-limit))
                self.button(ctx,'↓',x+w-59,y+h-39,30,28,lambda:self.page_library(limit))
            if len(self.store.library['notebooks'])>limit:
                self.button(ctx,'↑',x+90,y+h-39,30,28,lambda:self.page_books(-limit))
                self.button(ctx,'↓',x+128,y+h-39,30,28,lambda:self.page_books(limit))
        elif self.overlay=='image':
            x,y,w,h=self.panel(ctx,'Add an image',420,245)
            label(ctx,'',x+26,y+68,13,p['muted'])
            self.button(ctx,'Paste clipboard image',x+26,y+111,w-52,39,self.paste)
            self.button(ctx,'Choose an image by path',x+26,y+164,w-52,39,self.image_path)
        elif self.overlay=='help':
            x,y,w,h=self.panel(ctx,'Shortcuts',580,610)
            rows=[('Ctrl+B / I / U','Bold, italic, underline'),('Ctrl+Shift+H','Highlight'),('Shift+arrows / drag','Select text'),('Ctrl+A / C / X / V','Select all, copy, cut, paste'),('Ctrl+Z / Ctrl+Y','Undo / redo'),('Ctrl+Backspace','Delete the previous word'),('Ctrl+N / Ctrl+Shift+N','New note / notebook'),('Ctrl+G / Ctrl+T','Paste image / color theme'),('Ctrl+S / Ctrl+Q','Save / save and close'),('Ctrl+, / Ctrl+P','Settings / timer')]
            if hasattr(self,'change_font_size'): rows.append(('Ctrl++ / − / 0','Font size up / down / reset'))
            rows.extend([('Ctrl+R','Rename note'),('Alt+← / → / 0','Image smaller / larger / reset'),('Ctrl+Shift+G','Image file path')])
            for i,(keys,desc) in enumerate(rows):
                label(ctx,keys,x+25,y+84+i*32,13,p['accent'],True)
                label(ctx,desc,x+240,y+84+i*32,13,p['text'])
            label(ctx,'Select an image and drag its corner to resize.\nCopy an image in another app, then press Ctrl+V here.',x+25,y+84+len(rows)*32+12,12,p['muted'],width=w-50)

    def render_prompt(self,ctx):
        p=self.palette; x,y,w,h=self.panel(ctx,self.prompt['title'],500,215)
        color(ctx,p['panel']); rounded(ctx,x+25,y+78,w-50,45); ctx.fill()
        label(ctx,self.prompt['value'][-55:]+'│',x+37,y+89,16,p['text'])
        self.button(ctx,'Cancel',x+w-225,y+151,85,36,self.cancel_prompt)
        self.button(ctx,'Done',x+w-125,y+151,100,36,self.submit_prompt,True)

    def choose_theme(self,name): self.setting('theme',name); self.overlay=None; self.render_needed=True
    def choose_font(self,font): self.setting('font',font); self.overlay='settings'
    def toggle_timer(self): self.timer.toggle(self.settings); self.render_needed=True
    def reset_timer(self): self.timer.reset(); self.render_needed=True
    def number_setting(self,key,title,minimum,maximum):
        def apply(value):
            try: self.setting(key,max(minimum,min(maximum,int(value))))
            except ValueError: self.notify('Please enter a number')
        self.request_prompt(title,str(self.settings[key]),apply)
    def cancel_prompt(self): self.prompt=None; self.render_needed=True
    def submit_prompt(self):
        prompt=self.prompt; value=prompt['value'].strip()
        if value:
            self.prompt=None; prompt['callback'](value); self.render_needed=True
    def rename_note(self):
        def apply(value):
            self.doc.checkpoint(); self.doc.data['title']=value[:120]; self.doc.mark(); self.save()
        self.request_prompt('Page title',self.doc.data['title'],apply)
    def rename_book(self):
        def apply(value): self.book['name']=value[:60]; self.store.save_index()
        self.request_prompt('Notebook name',self.book['name'],apply)
    def new_note(self):
        def apply(value):
            if not self.save(): return
            self.doc=self.store.new_note(self.book,value[:120]); self.scroll=0; self.overlay=None; self.save()
        self.request_prompt('New page','Untitled',apply)
    def new_book(self):
        def apply(value):
            if not self.save(): return
            self.book=self.store.new_notebook(value[:60]); self.doc=self.store.load(self.book,self.book['notes'][0]['id']); self.scroll=0; self.font_index=0; self.overlay=None; self.save()
        self.request_prompt('New notebook','',apply)
    def switch_book(self,book):
        if not self.save(): return
        self.book=book; self.book['settings']={**DEFAULT_SETTINGS,**book['settings']}
        self.doc=self.store.load(book,book['notes'][0]['id']); self.scroll=0; self.font_index=0; self.save(); self.render_needed=True
    def switch_note(self,note):
        if not self.save(): return
        self.doc=self.store.load(self.book,note['id']); self.scroll=0; self.overlay=None; self.save(); self.render_needed=True
    def page_library(self,delta): self.font_index=max(0,min(len(self.book['notes'])-1,self.font_index+delta)); self.render_needed=True
    def page_books(self,delta): self.book_offset=max(0,min(len(self.store.library['notebooks'])-1,self.book_offset+delta)); self.render_needed=True
    def format(self,bit): self.doc.format(bit); self.render_needed=True
    def set_kind(self,kind): self.doc.set_kind(kind); self.render_needed=True
    def divider(self): self.doc.add_block({'type':'divider'}); self.render_needed=True
    def image_menu(self): self.paste_image()
    def paste_image(self): self.paste(image_only=True)
    def image_path(self):
        self.overlay=None
        self.request_prompt('Image file path','',lambda value:self.import_image(Path(value.strip('"\'')).expanduser()))

    def import_image(self,source):
        try:
            with Image.open(source) as im:
                im=ImageOps.exif_transpose(im).convert('RGBA')
                im.thumbnail((4096,4096))
                data=io.BytesIO(); im.save(data,format='PNG'); raw=data.getvalue()
            digest=hashlib.sha256(raw).hexdigest()
            rel=Path('images')/(digest+'.png'); path=self.store.root/self.book['id']/rel
            path.parent.mkdir(parents=True,exist_ok=True)
            if not path.exists():
                tmp=path.with_suffix('.tmp'); tmp.write_bytes(raw); os.replace(tmp,path)
            caption=source.name if isinstance(source,Path) else 'Pasted image'
            self.doc.add_block({'type':'image','path':str(rel),'caption':caption})
            self.overlay=None; self.notify('Image added to your page')
        except Exception as e: self.notify('Could not add image: '+str(e),8)

    def paste(self,image_only=False):
        if self.clip_process: return
        self.overlay=None
        self.clip_image_only=image_only
        command=['/usr/bin/python3',str(HERE/'clipboard.py')]
        if image_only:command.append('--image')
        self.clip_process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        os.set_blocking(self.clip_process.stdout.fileno(),False)
        os.set_blocking(self.clip_process.stderr.fileno(),False)
        self.clip_output=bytearray(); self.clip_errors=bytearray(); self.clip_started=time.monotonic()
        self.notify('Reading clipboard…')

    def finish_paste(self):
        if not self.clip_process: return
        # Drain while the child runs: an image can exceed the OS pipe capacity.
        for pipe,dest in [(self.clip_process.stdout,self.clip_output),(self.clip_process.stderr,self.clip_errors)]:
            while True:
                try: data=os.read(pipe.fileno(),65536)
                except BlockingIOError: break
                if not data: break
                dest.extend(data)
        if self.clip_process.poll() is None:
            if time.monotonic()-self.clip_started>8 or len(self.clip_output)>50_000_000:
                self.clip_process.kill(); self.notify('Clipboard content is too large or did not respond',8)
            return
        out=bytes(self.clip_output); self.clip_process.stdout.close(); self.clip_process.stderr.close(); self.clip_process=None
        try:
            result=json.loads(out)
            if 'error' in result: raise ValueError(result['error'])
            if self.clip_image_only and result['type']!='image':
                self.notify('No image on the clipboard. Copy an image, then press Ctrl+G.',6)
                return
            if result['type']=='image': self.import_image(io.BytesIO(base64.b64decode(result['data'])))
            elif result['type']=='html':
                if self.prompt: self.key('text',text=re.sub('<[^>]*>','',result['data']))
                else:
                    parser=RichParser(); parser.feed(result['data']); self.doc.insert_blocks(parser.result()); self.notify('Formatted text pasted')
            elif self.prompt: self.key('text',text=result['data'])
            else: self.doc.insert(result['data']); self.notify('Text pasted')
        except Exception as e: self.notify('Clipboard unavailable: '+str(e),6)
        self.render_needed=True

    def copy(self,cut=False):
        blocks=self.doc.selected_blocks()
        if not blocks: self.notify('Select some text first'); return
        payload={'text':self.doc.plain_selection(),'html':to_html(blocks)}
        if self.clip_owner and self.clip_owner.poll() is None: self.clip_owner.terminate()
        self.clip_owner=subprocess.Popen(['/usr/bin/python3',str(HERE/'clipboard.py'),'write'],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        self.clip_owner.stdin.write(json.dumps(payload).encode()); self.clip_owner.stdin.close()
        if cut: self.doc.delete()
        self.notify('Cut' if cut else 'Copied')

    def locate(self,x,y):
        yy=y-self.body_top+self.scroll
        if not self.geometry: return (0,0)
        geom=min(self.geometry,key=lambda g:0 if g['y']<=yy<=g['y']+g['h'] else min(abs(yy-g['y']),abs(yy-g['y']-g['h'])))
        if geom['block']['type']!='text': return (geom['index'],0 if x<geom['x']+geom.get('iw',geom['w'])/2 else 1)
        layout=geom['layout']
        inside,index,trailing=layout.xy_to_index(int((x-geom['x']-geom['indent'])*Pango.SCALE),int((yy-geom['y'])*Pango.SCALE))
        value=geom['block']['text']; offset=len(value.encode()[:index].decode('utf-8','ignore'))+trailing
        return (geom['index'],min(len(value),max(0,offset)))

    def mouse(self,button,x,y,release=False):
        if self.image_drag:
            if release or button&32:self.resize_image_drag(x,y)
            if release:self.image_drag=None;self.drag=False
            return
        if button&64:
            if not self.overlay and not self.prompt:
                self.scroll=max(0,self.scroll+(-85 if button&1==0 else 85)); self.render_needed=True
            return
        if release: self.drag=False; return
        if button&32:
            if self.drag:
                self.doc.cursor=self.locate(x,y); self.render_needed=True
            return
        if button&3: return
        for bx,by,bw,bh,fn in reversed(self.buttons):
            if bx<=x<bx+bw and by<=y<by+bh:
                fn(); self.render_needed=True; return
        if self.overlay or self.prompt: return
        if self.body_top<=y<self.body_bottom:
            for g in self.geometry:
                if 'image' not in g:continue
                top=self.body_top+g['y']-self.scroll
                corner=abs(x-g['x']-g['iw'])<=10 and abs(y-top-g['ih'])<=10
                if corner or g['x']<=x<=g['x']+g['iw'] and top<=y<=top+g['ih']:
                    self.doc.cursor=(g['index'],0);self.doc.anchor=None;self.drag=False;self.goal_x=None
                    if corner:
                        self.image_drag={'index':g['index'],'x':x,'y':y,'width':g['iw'],'max':g['w'],'aspect':g['ih']/g['iw'],'changed':False}
                    self.render_needed=True;return
            self.doc.cursor=self.locate(x,y); self.doc.anchor=self.doc.cursor; self.drag=True
            now=time.monotonic()
            if self.last_hit and now-self.last_hit[0]<.35 and abs(x-self.last_hit[1])<5 and abs(y-self.last_hit[2])<5:
                i,off=self.doc.cursor; block=self.doc.blocks[i]
                if block['type']=='text':
                    value=block['text']; start=end=min(off,len(value))
                    while start>0 and not value[start-1].isspace(): start-=1
                    while end<len(value) and not value[end].isspace(): end+=1
                    self.doc.anchor=(i,start); self.doc.cursor=(i,end); self.drag=False
            self.last_hit=(now,x,y)
            self.doc.typing_style=0; self.render_needed=True

    def move(self,key,shift=False,ctrl=False):
        doc=self.doc; old=doc.cursor; i,off=old; block=doc.blocks[i]
        if shift and doc.anchor is None: doc.anchor=old
        if not shift: doc.anchor=None
        if key in ('left','right'):
            step=-1 if key=='left' else 1
            if step<0:
                if off: off-=1
                elif i: i-=1; off=length(doc.blocks[i])
            else:
                if off<length(block): off+=1
                elif i<len(doc.blocks)-1: i+=1; off=0
            # Do not leave the cursor inside a combining sequence.
            if doc.blocks[i]['type']=='text':
                value=doc.blocks[i]['text']
                while 0<off<len(value) and unicodedata.combining(value[off]): off+=step
                if ctrl:
                    while 0<off<len(value) and not value[off if step>0 else off-1].isspace(): off+=step
            self.goal_x=None
        elif key in ('home','end'):
            if ctrl: i=0 if key=='home' else len(doc.blocks)-1
            off=0 if key=='home' else length(doc.blocks[i]); self.goal_x=None
        elif key in ('up','down','pageup','pagedown'):
            direction=-1 if key in ('up','pageup') else 1
            g=self.geometry[i] if i<len(self.geometry) else None
            if g and 'caret' in g:
                cx,cy,ch=g['caret']; self.goal_x=cx if self.goal_x is None else self.goal_x
                delta=(self.body_bottom-self.body_top-30) if key in ('pageup','pagedown') else ch
                targety=cy+(delta+3 if direction>0 else -max(3,delta/2))
                i,off=self.locate(self.goal_x,self.body_top+targety-self.scroll)
                if (i,off)==old and (direction<0 and i>0 or direction>0 and i<len(doc.blocks)-1):
                    i+=direction; off=min(off,length(doc.blocks[i]))
            else:
                i=max(0,min(len(doc.blocks)-1,i+direction)); off=min(off,length(doc.blocks[i]))
        doc.cursor=(i,off)
        if not shift:
            b=doc.blocks[i]; doc.typing_style=b['styles'][off-1] if b['type']=='text' and off else 0
        self.render_needed=True

    def key(self,key,shift=False,ctrl=False,alt=False,text=None):
        self.image_drag=None
        if key=='tab' and (self.overlay or self.prompt):
            self.focus_button=(self.focus_button+(-1 if shift else 1))%max(1,len(self.buttons)); self.render_needed=True; return
        if key=='enter' and 0<=self.focus_button<len(self.buttons) and (self.overlay or self.prompt):
            self.buttons[self.focus_button][4](); self.focus_button=-1; self.render_needed=True; return
        if self.prompt:
            if key=='escape': self.cancel_prompt()
            elif key=='enter': self.submit_prompt()
            elif key=='backspace': self.prompt['value']=self.prompt['value'][:-1]; self.prompt['replace']=False
            elif ctrl and key=='a': self.prompt['replace']=True
            elif ctrl and key=='v': self.paste()
            elif text and not ctrl and not alt:
                self.prompt['value']=('' if self.prompt['replace'] else self.prompt['value'])+text.replace('\n',' ')
                self.prompt['replace']=False
            self.render_needed=True; return
        if ctrl and key=='q':
            if self.save(): self.running=False
            return
        if key=='escape':
            if self.overlay: self.close_overlay()
            else: self.open_overlay('settings')
            self.render_needed=True; return
        if self.overlay: return
        if self.commands.dispatch(self,key,ctrl,alt,shift):return
        if key=='f1': self.open_overlay('help'); return
        if key in ('left','right','up','down','home','end','pageup','pagedown'): self.move(key,shift,ctrl)
        elif key=='backspace': self.doc.delete(); self.render_needed=True
        elif key=='delete': self.doc.delete(False); self.render_needed=True
        elif key=='enter': self.doc.insert('\n'); self.render_needed=True
        elif key=='tab': self.doc.insert('    '); self.render_needed=True
        elif text and not ctrl and not alt: self.doc.insert(text); self.render_needed=True

    def feed(self,data):
        self.buffer+=data
        while self.buffer:
            if self.pasting:
                end=self.buffer.find(b'\x1b[201~')
                if end<0: return
                raw=self.buffer[:end].decode('utf-8','replace'); self.buffer=self.buffer[end+6:]; self.pasting=False
                if self.prompt:
                    self.key('text',text=raw)
                else: self.doc.insert(raw); self.render_needed=True
                continue
            if self.buffer.startswith(b'\x1b[200~'):
                self.buffer=self.buffer[6:]; self.pasting=True; continue
            m=re.match(rb'\x1b\[<(\d+);(\d+);(\d+)([mM])',self.buffer)
            if m:
                button,x,y=map(int,m.group(1,2,3)); self.mouse(button,x-1,y-1,m[4]==b'm'); self.buffer=self.buffer[m.end():]; continue
            m=re.match(rb'\x1b\[([0-9:;]*)([A-Za-z~])',self.buffer)
            if m:
                values=m[1].decode().split(';'); final=m[2].decode(); first=int(values[0].split(':')[0] or 0); mod=int(values[1].split(':')[0]) if len(values)>1 and values[1] else 1
                event=int(values[1].split(':')[1]) if len(values)>1 and ':' in values[1] else 1
                self.buffer=self.buffer[m.end():]
                if final=='u' and first in (57442,57448):
                    self.ctrl_held=event!=3; self.render_needed=True; continue
                if event==3: continue
                shift,alt,ctrl=bool((mod-1)&1),bool((mod-1)&2),bool((mod-1)&4)
                key=None; text=None
                if final=='u':
                    key={27:'escape',13:'enter',9:'tab',127:'backspace',57364:'f1'}.get(first)
                    if key is None and 32<=first<57344:
                        text=''.join(chr(int(v)) for v in values[2].split(':')) if len(values)>2 and values[2] else chr(first)
                        if len(values)<=2 and shift and not ctrl: text=text.upper()
                        key=chr(first).lower()
                elif final in 'ABCDHF': key={'A':'up','B':'down','C':'right','D':'left','H':'home','F':'end'}[final]
                elif final=='~': key={1:'home',2:'insert',3:'delete',4:'end',5:'pageup',6:'pagedown',11:'f1'}.get(first)
                if key: self.key(key,shift,ctrl,alt,text)
                continue
            if self.buffer.startswith(b'\x1bOP'): self.buffer=self.buffer[3:]; self.key('f1'); continue
            if self.buffer.startswith(b'\x1b'):
                if len(self.buffer)==1: return
                if self.buffer[1:2] in (b'[',b'O',b'_'): return
                self.buffer=self.buffer[1:]; self.key('escape'); continue
            byte=self.buffer[0]
            if byte<32 or byte==127:
                self.buffer=self.buffer[1:]
                if byte in (10,13): self.key('enter')
                elif byte==9: self.key('tab')
                elif byte in (8,127): self.key('backspace')
                elif byte: self.key(chr(byte+96),ctrl=True)
                continue
            # Consume a run of printable UTF-8, retaining incomplete final characters.
            end=next((i for i,b in enumerate(self.buffer) if b<32 or b==127),len(self.buffer))
            chunk=self.buffer[:end]
            try: text=chunk.decode('utf-8')
            except UnicodeDecodeError as e:
                if e.reason=='unexpected end of data':
                    end=e.start
                    if end==0: return
                    text=chunk[:end].decode('utf-8','replace')
                else: text=chunk.decode('utf-8','replace')
            self.buffer=self.buffer[end:]; self.key('text',text=text)

    def terminal_size(self):
        rows,cols,pw,ph=struct.unpack('HHHH',fcntl.ioctl(sys.stdout.fileno(),termios.TIOCGWINSZ,b'\0'*8))
        return pw or cols*10,ph or rows*22

    def transmit(self,surface):
        self.terminal.transmit(surface,size=(self.width,self.height))

    def run(self):
        fd=sys.stdin.fileno(); old=termios.tcgetattr(fd)
        shared=os.environ.get('SAMATHA_SHARED_SCREEN')=='1'
        def stop(*_): self.running=False
        signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGHUP,stop)
        try:
            tty.setraw(fd)
            # Pixel mouse coordinates + Kitty disambiguated key events + bracketed paste.
            sys.stdout.write(terminal_mode('editor')+('' if shared else '\x1b[?1049h')+'\x1b[?25l\x1b[?2004h\x1b[?1002h\x1b[?1006h\x1b[?1016h\x1b[>27u')
            sys.stdout.flush()
            # Paint before waiting for input; the browser frame remains visible
            # during startup and is replaced by this complete synchronized frame.
            self.transmit(self.render(*self.terminal_size(),follow=True,diagnostic=False))
            self.render_needed=False
            last_input=time.monotonic(); follow=True
            while self.running:
                ready,_,_=select.select([fd],[],[],.1)
                if ready:
                    data=os.read(fd,65536)
                    if not data: break
                    ismouse=data.startswith(b'\x1b[<')
                    self.feed(data); last_input=time.monotonic(); follow=not ismouse
                elif self.buffer==b'\x1b' and time.monotonic()-last_input>.05:
                    self.buffer=b''; self.key('escape')
                self.finish_paste()
                if self.timer.tick(self.settings):
                    sys.stdout.write('\a'); sys.stdout.flush(); self.notify(self.timer.phase+' complete.',8)
                    if subprocess.call(['which','notify-send'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)==0:
                        subprocess.Popen(['notify-send','Samatha',self.timer.phase+' is ready.'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                clock=dt.datetime.now().strftime('%H:%M')+str(self.timer.seconds())
                if clock!=self.last_clock: self.render_needed=True; self.last_clock=clock
                now=time.monotonic()
                if self.doc.dirty and self.settings['autosave'] and (now-self.doc.changed_at>=self.settings['autosave_seconds'] or now-self.last_durable>=5) and now-self.last_save_attempt>=1:
                    self.last_save_attempt=now; self.save(); self.render_needed=True
                size=self.terminal_size()
                if size!=(self.width,self.height): self.render_needed=True
                if self.render_needed:
                    self.transmit(self.render(*size,follow=follow,diagnostic=False)); self.render_needed=False; follow=False
            self.save()
        finally:
            if self.doc.dirty:
                try: self.save()
                except Exception: pass
            cleanup='\x1b[<u\x1b[?1016l\x1b[?1006l\x1b[?1002l\x1b[?2004l'
            if not shared:cleanup='\x1b_Ga=d,d=I,i=42,q=2\x1b\\'+cleanup+'\x1b[?25h\x1b[?1049l'
            sys.stdout.write(cleanup+terminal_mode('browser' if shared else ''))
            sys.stdout.flush(); termios.tcsetattr(fd,termios.TCSADRAIN,old)

def main():
    parser=argparse.ArgumentParser(description='Samatha')
    parser.add_argument('--data-dir',type=Path,default=Path.home()/'.local/share/samatha')
    parser.add_argument('--render',type=Path,help='Write a PNG preview without opening a terminal')
    args=parser.parse_args()
    try:
        app=Notebook(args.data_dir)
        if args.render: app.render().write_to_png(str(args.render))
        else:
            if not sys.stdin.isatty(): raise RuntimeError('Open Notebook using its launcher or run it inside Kitty.')
            if not os.environ.get('KITTY_WINDOW_ID'): raise RuntimeError('Notebook needs Kitty for inline images. Use the Notebook launcher.')
            app.run()
    except Exception as e:
        print('Samatha:',e,file=sys.stderr); return 1
    return 0

if __name__=='__main__': raise SystemExit(main())
