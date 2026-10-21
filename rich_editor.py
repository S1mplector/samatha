#!/usr/bin/python3
"""Rich editing within Samatha's native terminal panes."""
import fcntl
import json
import os
from pathlib import Path
import struct
import sys
import termios
import time
import unicodedata
import cairo
from app import Notebook, THEMES
from drawing import label, color, text_layout, PangoCairo
from model import atomic_json
from rich_storage import RichStore, atomic_text, unique_path


def clip(text,width):
    out='';used=0
    for c in str(text).replace('\n',' ').replace('\t',' '):
        if unicodedata.category(c)=='Cc':continue
        size=2 if unicodedata.east_asian_width(c) in 'WF' else 0 if unicodedata.combining(c) else 1
        if used+size>width:break
        out+=c;used+=size
    return out+' '*(width-used)

def ansi_color(value,background=False):
    return '\x1b['+('48' if background else '38')+';2;'+';'.join(str(int(value[i:i+2],16)) for i in (0,2,4))+'m'

class RichNotebook(Notebook):
    def __init__(self,path):
        store=RichStore(path)
        super().__init__(store.root,store=store)
        self.notes_root=Path(os.environ.get('SAMATHA_NOTES_DIR',store.root))
        self.theme_path=self.notes_root/'.samatha-theme'
        theme=os.environ.get('SAMATHA_THEME','light')
        if self.theme_path.exists():theme=self.theme_path.read_text().strip()
        self.settings['theme']=THEMES.get(theme,THEMES['light'])['name']
        if not self.settings.get('tui_editor'):
            self.settings.update(font='DejaVu Sans Mono',font_size=14,line_spacing=1.25,tui_editor=True)
        self.show_sidebar=True;self.cols=120;self.rows=36;self.cw=10;self.ch=22
        self.sidebar_width=int(os.environ.get('SAMATHA_SIDEBAR_WIDTH','30'))+6
        self.tree=[];self.tree_offset=0;self.sidebar_outer=0;self.content_left=0
        self.read_tree()
        self.timer_path=store.root/'.samatha-timer.json'
        self.saved_timer=None
        try:
            state=json.loads(self.timer_path.read_text())
            self.timer.phase=state['phase'];self.timer.started=state['started'];self.timer.remaining=state['remaining']
            if state.get('deadline'):
                self.timer.deadline=time.monotonic()+state['deadline']-time.time();self.timer.tick(self.settings)
        except (OSError,ValueError,KeyError,TypeError):pass

    def read_tree(self):
        expanded={}
        try:expanded=json.loads((self.notes_root/'.samatha-state.json').read_text()).get('Expanded',{})
        except (OSError,ValueError):pass
        current=self.store.path
        for folder in current.parents:
            if folder==self.notes_root:break
            expanded[str(folder)]=True
        self.tree=[]
        def walk(folder,depth=0):
            for path in sorted(folder.iterdir(),key=lambda p:p.name.casefold()):
                if path.name.startswith('.') or path.is_symlink():continue
                if path.is_dir():
                    if path.name=='images':continue
                    self.tree.append((path,depth,True,path.name,expanded.get(str(path),False)))
                    if expanded.get(str(path)):walk(path,depth+1)
                elif path.suffix.lower()=='.md':
                    try:title=next((l[2:].strip() for l in path.read_text().splitlines() if l.startswith('# ')),path.stem)
                    except OSError:title=path.stem
                    self.tree.append((path,depth,False,title,False))
        walk(self.notes_root)

    def return_to_browser(self,path=None):
        if not self.save():return
        state_path=self.notes_root/'.samatha-state.json'
        try:
            state=json.loads(state_path.read_text()) if state_path.exists() else {}
            state['Selected']=str(path or self.store.path);atomic_json(state_path,state)
        except (OSError,ValueError) as e:self.notify(str(e));return
        self.running=False

    def open_overlay(self,name):
        if name=='library':self.return_to_browser()
        else:super().open_overlay(name)

    def setting(self,key,value):
        super().setting(key,value)
        if key=='theme':
            theme=next(k for k,p in THEMES.items() if p['name']==value)
            atomic_text(self.theme_path,theme)

    def new_book(self):
        def create(name):
            if not self.save():return
            folder=unique_path(self.notes_root,name);folder.mkdir()
            self.return_to_browser(folder)
        self.request_prompt('Notebook name','',create)

    def new_note(self):
        def create(title):
            if not self.save():return
            self.doc=self.store.new_note(self.book,title[:120]);self.scroll=0;self.overlay=None
            self.save();self.read_tree()
        self.request_prompt('Note title','Untitled',create)

    def rename_note(self):
        def apply(value):
            self.doc.checkpoint();self.doc.data['title']=value[:120];self.doc.mark()
            if self.save():self.read_tree()
        self.request_prompt('Note title',self.doc.data['title'],apply)
        self.focus_button=-1
        self.prompt['inline']=True

    def image_path(self):
        self.overlay=None
        self.request_prompt('Image file path','',lambda value:self.import_image(Path(value.strip('"\'')).expanduser()))
        self.focus_button=-1
        self.prompt.update(inline=True,location='footer',limit=4096)

    def render_prompt(self,ctx):
        if not self.prompt.get('inline'):return super().render_prompt(ctx)
        if self.prompt.get('location')=='footer':return
        p=self.palette;x=self.content_left+3*self.cw;y=4*self.ch
        width=max(1,self.width-x-3*self.cw);height=min(2*self.ch-4,max(self.ch,self.settings['font_size']*1.5))
        ctx.save();ctx.rectangle(x-3,y-2,width+6,height+4);ctx.clip()
        color(ctx,p['panel']);ctx.paint()
        value=self.prompt['value'];size=self.settings['font_size']
        while value and text_layout(ctx,value+'│',size,self.settings['font']).get_pixel_size()[0]>width:value=value[1:]
        label(ctx,value+'│',x,y,size,p['accent'],font=self.settings['font'])
        color(ctx,p['accent']);ctx.rectangle(x,y+height,width,1);ctx.fill();ctx.restore()

    def mouse(self,button,x,y,release=False):
        if self.prompt and self.prompt.get('inline'):return
        super().mouse(button,x,y,release)

    def cycle_theme(self):
        keys=list(THEMES);current=next((k for k,p in THEMES.items() if p['name']==self.settings['theme']),'light')
        self.setting('theme',THEMES[keys[(keys.index(current)+1)%len(keys)]]['name'])

    def change_font_size(self,delta=0,reset=False):
        size=14 if reset else max(12,min(36,self.settings['font_size']+delta))
        self.setting('font_size',size)
        self.goal_x=None
        self.notify(f'Font size {size}',2)

    def save(self):
        if not super().save():return False
        try:
            state=(self.timer.phase,self.timer.started,self.timer.remaining,self.timer.deadline)
            if state!=self.saved_timer:
                deadline=time.time()+self.timer.deadline-time.monotonic() if self.timer.deadline else None
                atomic_json(self.timer_path,{'phase':self.timer.phase,'started':self.timer.started,'remaining':self.timer.remaining,'deadline':deadline})
                self.saved_timer=state
        except OSError as e:self.notify(str(e))
        return True

    def key(self,key,shift=False,ctrl=False,alt=False,text=None):
        if self.prompt and self.prompt.get('inline'):
            if key=='tab':return
            if text:text=''.join(c for c in text if unicodedata.category(c)!='Cc')
            super().key(key,shift,ctrl,alt,text)
            if self.prompt:self.prompt['value']=self.prompt['value'][:self.prompt.get('limit',120)]
            return
        if ctrl and not alt and not self.prompt:
            if key in ('+','='):
                self.change_font_size(1);return
            if key in ('-','_'):
                self.change_font_size(-1);return
            if key=='0':
                self.change_font_size(reset=True);return
        if not self.overlay and not self.prompt:
            if key=='escape' or ctrl and key=='q':self.return_to_browser();return
            if ctrl and shift and key=='n':self.new_book();return
            if key=='tab' and not ctrl:
                self.show_sidebar=not self.show_sidebar;self.render_needed=True;return
            if ctrl and key=='t':self.cycle_theme();return
            if ctrl and key=='p':self.open_overlay('timer');return
        super().key(key,shift,ctrl,alt,text)

    def terminal_size(self):
        rows,cols,pw,ph=struct.unpack('HHHH',fcntl.ioctl(sys.stdout.fileno(),termios.TIOCGWINSZ,b'\0'*8))
        self.rows=max(1,rows);self.cols=max(1,cols)
        return pw or cols*10,ph or rows*22

    def layout_document(self,ctx):
        available=max(1,self.width-self.content_left-6*self.cw);left=self.content_left+3*self.cw
        y=0;result=[]
        for i,block in enumerate(self.doc.blocks):
            geom={'index':i,'x':left,'y':y,'w':available,'block':block}
            if block['type']=='text':
                size=self.settings['font_size']*{'h1':1.35,'h2':1.15}.get(block['kind'],1)
                indent=2*self.cw if block['kind']=='bullet' else 0
                self.layout_text(ctx,geom,size,indent)
            elif block['type']=='divider':geom['h']=self.ch
            else:
                self.layout_image(geom,available)
            result.append(geom);y+=geom['h']+self.ch*.5
        self.geometry=result;self.content_height=y

    def shell_lines(self):
        p=self.palette;cols=self.cols;rows=self.rows;side=self.sidebar_outer
        fg=ansi_color(p['text']);ac=ansi_color(p['accent']);muted=ansi_color(p['muted']);bg=ansi_color(p['bg'],True)
        content=cols-side-(1 if side else 0)
        lines=[bg+fg+clip('samatha'.center(cols),cols),bg+clip('',cols)]
        def edge(left,middle,right):return ac+left+middle+right+fg
        if side:lines.append(edge('╭','─'*(side-2),'╮')+' '+edge('╭','─'*(content-2),'╮'))
        else:lines.append(edge('╭','─'*(content-2),'╮'))
        selected=next((i for i,n in enumerate(self.tree) if n[0]==self.store.path),0)
        self.tree_offset=max(0,selected-max(1,rows-9)+1)
        for r in range(max(1,rows-6)):
            item='';index=self.tree_offset+r-1;style=fg
            if r>=1 and index<len(self.tree):
                path,depth,isdir,title,expanded=self.tree[index]
                icon=('▼ ' if expanded else '▶ ') if isdir else ('├─ ' if index+1<len(self.tree) and self.tree[index+1][1]>=depth else '└─ ')
                item='  '+'  '*depth+icon+title
                if path==self.store.path:style=ac
                if side:self.buttons.append((self.cw,(r+3)*self.ch,(side-2)*self.cw,self.ch,lambda x=path:self.return_to_browser(x)))
            left=edge('│','','')+style+clip(item,max(0,side-2))+edge('','','│')+' ' if side else ''
            lines.append(left+edge('│',' '*(content-2),'│'))
        if side:lines.append(edge('╰','─'*(side-2),'╯')+' '+edge('╰','─'*(content-2),'╯'))
        else:lines.append(edge('╰','─'*(content-2),'╯'))
        state='Unsaved' if self.doc.dirty else 'Saved'
        status=self.doc.data['title']+' • '+state
        if self.timer.started:
            sec=self.timer.seconds();status+=f' • {self.timer.phase} {sec//60:02}:{sec%60:02}'
        if time.monotonic()<self.message_until:status=self.message
        hint='ctrl+=/- size • ctrl+b/i/u style • ctrl+g paste image • ctrl+t theme • ctrl+, settings • esc browse'
        if self.cols<105:hint='ctrl+=/- size • ctrl+b/i/u style • F1 help • esc browse'
        if self.ctrl_held:hint='Backspace deletes highlighted word • release Ctrl to cancel'
        if self.doc.blocks[self.doc.cursor[0]]['type']=='image':hint='Alt+Enter annotate • Drag corner to resize • Alt+←/→ size • Alt+0 reset • Ctrl+Z undo'
        if self.prompt and self.prompt.get('inline'):
            hint='Rename note • Enter confirm • Esc cancel • Ctrl+A replace'
            if self.prompt.get('location')=='footer':
                hint='Image file path • Enter add • Esc cancel • Ctrl+A replace'
                annotation=self.prompt['title']=='Image annotation'
                if annotation:hint='Image annotation • Enter save • Esc cancel • Ctrl+A replace'
                visible=[];used=0
                for c in reversed(self.prompt['value']+'│'):
                    width=2 if unicodedata.east_asian_width(c) in 'WF' else 0 if unicodedata.combining(c) else 1
                    if used+width>max(1,cols-17):break
                    visible.append(c);used+=width
                status=('Annotation: › ' if annotation else 'Image path: › ')+''.join(reversed(visible))
        lines+=[muted+clip('  '+status,cols),muted+clip('  '+hint,cols)]
        return lines[:rows]

    def render(self,width=None,height=None,follow=False,diagnostic=True):
        if width:self.width=width
        if height:self.height=height
        self.cw=self.width/self.cols;self.ch=self.height/self.rows
        self.sidebar_outer=min(self.sidebar_width,self.cols//2) if self.show_sidebar and self.cols>=60 else 0
        self.content_left=(self.sidebar_outer+(1 if self.sidebar_outer else 0))*self.cw
        self.body_top=6*self.ch;self.body_bottom=(self.rows-3)*self.ch
        p=self.palette;surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,self.width,self.height);ctx=cairo.Context(surface)
        color(ctx,p['bg']);ctx.paint();self.buttons=[]
        # The shell itself is terminal text. Draw a matching shell for PNG diagnostics.
        lines=self.shell_lines()
        if diagnostic or self.overlay or self.prompt and not self.prompt.get('inline'):
            import re
            cw,_=text_layout(ctx,'M',self.ch*.70,'DejaVu Sans Mono').get_pixel_size()
            for row,line in enumerate(lines):
                layout=text_layout(ctx,re.sub(r'\x1b\[[0-9;]*m','',line),self.ch*.70,'DejaVu Sans Mono')
                ctx.save();ctx.translate(0,row*self.ch);ctx.scale(self.cw/max(1,cw),1)
                color(ctx,p['accent']);ctx.move_to(0,0);PangoCairo.show_layout(ctx,layout);ctx.restore()
        ctx.save();ctx.rectangle(self.content_left+self.cw,3*self.ch,self.width-self.content_left-2*self.cw,(self.rows-6)*self.ch);ctx.clip()
        color(ctx,p['bg']);ctx.paint()
        title=clip(self.doc.data['title'],max(1,int((self.width-self.content_left)/self.cw)-7)).rstrip()
        tx=self.content_left+3*self.cw;ty=4*self.ch
        lay=text_layout(ctx,title,self.settings['font_size'],self.settings['font'],True);tw,th=lay.get_pixel_size()
        if not (self.prompt and self.prompt.get('inline') and self.prompt.get('location')!='footer'):
            color(ctx,p['accent']);ctx.rectangle(tx-3,ty-1,tw+6,th+2);ctx.fill();label(ctx,title,tx,ty,self.settings['font_size'],p['bg'],True,font=self.settings['font'])
        self.buttons.append((tx,ty,max(30,tw),max(self.ch,th),self.rename_note))
        self.layout_document(ctx)
        if follow and not self.prompt:self.ensure_cursor()
        viewport=max(1,self.body_bottom-self.body_top);self.scroll=max(0,min(self.scroll,max(0,self.content_height-viewport+40)))
        ctx.rectangle(self.content_left+self.cw,self.body_top,self.width-self.content_left-2*self.cw,viewport);ctx.clip()
        for g in self.geometry:
            x,y=g['x'],self.body_top+g['y']-self.scroll;block=g['block'];i=g['index']
            if y+g['h']<self.body_top or y>self.body_bottom:continue
            if block['type']=='text':
                if block['kind']=='bullet':label(ctx,'•',x,y,g['size'],p['accent'])
                color(ctx,p['text']);ctx.move_to(x+g['indent'],y);PangoCairo.show_layout(ctx,g['layout'])
                if 'caret' in g and not self.overlay and not self.prompt:
                    cx,cy,ch=g['caret'];color(ctx,p['accent']);ctx.rectangle(cx,self.body_top+cy-self.scroll,1.5,ch);ctx.fill()
            elif block['type']=='divider':
                color(ctx,p['line']);ctx.rectangle(x,y+self.ch/2,g['w'],1);ctx.fill()
            elif 'image' in g:
                self.render_image(ctx,g,x,y)
            else:label(ctx,'Image unavailable',x,y,14,p['muted'])
        ctx.restore()
        if self.overlay:self.render_overlay(ctx)
        if self.prompt:self.render_prompt(ctx)
        return surface

    def transmit(self,surface):
        buttons=self.buttons[:];lines=self.shell_lines();self.buttons=buttons;p=self.palette
        if self.overlay or self.prompt and not self.prompt.get('inline'):x=y=0;w=self.width;h=self.height;row=col=1
        else:
            x=round(self.content_left+self.cw);y=round(3*self.ch)
            w=max(1,round(self.width-self.content_left-2*self.cw));h=max(1,round((self.rows-6)*self.ch))
            row=4;col=self.sidebar_outer+3 if self.sidebar_outer else 2
        crop=cairo.ImageSurface(cairo.FORMAT_ARGB32,w,h);ctx=cairo.Context(crop);ctx.set_source_surface(surface,-x,-y);ctx.paint()
        base=ansi_color(p['bg'],True)+ansi_color(p['text'])
        self.terminal.transmit(crop,lines,row,col,size=(self.cols,self.rows,self.width,self.height),base_style=base)

def main():
    if len(sys.argv)!=2:raise SystemExit('Usage: rich_editor.py NOTE.md')
    if not os.environ.get('KITTY_WINDOW_ID'):raise SystemExit('Open Samatha from its launcher for rich editing.')
    app=None
    try:app=RichNotebook(Path(sys.argv[1]));app.run()
    except Exception as e:print('Samatha:',e,file=sys.stderr);return 1
    finally:
        if app:app.store.lock.close()
    return 0
if __name__=='__main__':raise SystemExit(main())
