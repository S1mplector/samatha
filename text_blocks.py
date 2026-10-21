"""Styled paragraph layout with bounded caching and live caret placement."""
import html
from drawing import text_layout, Pango
from model import previous_word, BOLD, ITALIC, UNDERLINE, HIGHLIGHT


class TextLayout:
    def layout_text(self,ctx,geom,size,indent):
        block=geom['block'];index=geom['index'];text=block['text']
        selection=self.doc.selection();selected=None
        if selection and selection[0][0]<=index<=selection[1][0]:
            selected=(selection[0][1] if index==selection[0][0] else 0,
                      selection[1][1] if index==selection[1][0] else len(text))
        preview=previous_word(text,self.doc.cursor[1]) if self.ctrl_held and not selection and self.doc.cursor[0]==index else None
        width=geom['w']-indent;bold=block['kind'] in ('h1','h2')
        key=(text,tuple(block['styles']),selected,preview,size,width,bold,
             self.settings['font'],self.settings['line_spacing'],self.settings['theme'])
        layout=self.paragraph_cache.get(key)
        if layout is None:
            layout=text_layout(ctx,self.markup(block,index),size,self.settings['font'],bold,width,True)
            layout.set_line_spacing(self.settings['line_spacing'])
            self.paragraph_cache.put(key,layout,max(1,len(text)))
        _,height=layout.get_pixel_size()
        geom.update(layout=layout,h=max(height,size*1.4),indent=indent,size=size)
        if self.doc.cursor[0]==index:
            byte=len(text[:self.doc.cursor[1]].encode());strong,_=layout.get_cursor_pos(byte)
            geom['caret']=(geom['x']+indent+strong.x/Pango.SCALE,
                           geom['y']+strong.y/Pango.SCALE,max(size*1.3,strong.height/Pango.SCALE))

    def markup(self,block,index):
        selected=self.doc.selection(); runs=[]; start=0; text=block['text']; styles=block['styles']
        def key(pos):
            sel=bool(selected and selected[0]<=(index,pos)<selected[1])
            preview=False
            if self.ctrl_held and not selected and index==self.doc.cursor[0]:
                start,end=previous_word(text,self.doc.cursor[1]); preview=start<=pos<end
            return styles[pos],sel,preview
        while start<len(text):
            style,sel,preview=key(start); end=start+1
            while end<len(text) and key(end)==(style,sel,preview): end+=1
            attrs=[]
            if style&BOLD: attrs.append('weight="bold"')
            if style&ITALIC: attrs.append('style="italic"')
            if style&UNDERLINE: attrs.append('underline="single"')
            if sel: attrs.append(f'background="#{self.palette["select"]}"')
            elif preview: attrs.append(f'background="#{self.palette["tint"]}" underline="single"')
            elif style&HIGHLIGHT: attrs.append(f'background="#{self.palette["highlight"]}"')
            runs.append('<span '+' '.join(attrs)+'>'+html.escape(text[start:end])+'</span>')
            start=end
        return ''.join(runs)
