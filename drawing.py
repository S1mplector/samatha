"""Shared Cairo/Pango drawing primitives; no document or storage dependencies."""
import math
import gi
gi.require_version('Pango','1.0')
gi.require_version('PangoCairo','1.0')
from gi.repository import Pango, PangoCairo

def color(ctx,value,alpha=1):
    ctx.set_source_rgba(*(int(value[i:i+2],16)/255 for i in (0,2,4)),alpha)

def rounded(ctx,x,y,w,h,r=8):
    r=min(r,w/2,h/2)
    ctx.new_sub_path()
    for cx,cy,start in [(x+w-r,y+r,-math.pi/2),(x+w-r,y+h-r,0),(x+r,y+h-r,math.pi/2),(x+r,y+r,math.pi)]:
        ctx.arc(cx,cy,r,start,start+math.pi/2)
    ctx.close_path()

def text_layout(ctx,text,size=14,font='DejaVu Sans',bold=False,width=None,markup=False):
    layout=PangoCairo.create_layout(ctx)
    desc=Pango.FontDescription()
    desc.set_family(font); desc.set_absolute_size(size*Pango.SCALE)
    if bold: desc.set_weight(Pango.Weight.BOLD)
    layout.set_font_description(desc)
    if width is not None:
        layout.set_width(max(1,int(width))*Pango.SCALE); layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    if markup: layout.set_markup(text,-1)
    else: layout.set_text(text,-1)
    return layout

def label(ctx,text,x,y,size=14,ink='282e2c',bold=False,width=None,font='DejaVu Sans'):
    layout=text_layout(ctx,text,size,font,bold,width)
    color(ctx,ink); ctx.move_to(x,y); PangoCairo.show_layout(ctx,layout)
    return layout.get_pixel_size()
