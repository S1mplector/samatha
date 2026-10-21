"""Image display and resizing, reusable across Samatha editor shells."""
import cairo
import math
from drawing import color, label, text_layout, PangoCairo


class ImageEditing:
    def layout_image(self,geom,available):
        block=geom['block'];path=self.store.root/self.book['id']/block['path']
        try:
            image=self.image_cache.get(str(path))
            if image is None:
                image=cairo.ImageSurface.create_from_png(str(path))
                self.image_cache.put(str(path),image,image.get_stride()*image.get_height())
            default=min(image.get_width(),320*image.get_width()/image.get_height())
            width=min(available,block.get('width',default))
            ratio=width/image.get_width()
            geom.update(image=image,image_path=str(path),iw=width,ih=image.get_height()*ratio,ratio=ratio)
            caption=block.get('caption','')
            key=('image-caption',caption,available)
            layout=self.paragraph_cache.get(key)
            if layout is None:
                layout=text_layout(cairo.Context(image),caption,11,width=available)
                self.paragraph_cache.put(key,layout,len(caption)+1)
            geom.update(caption_layout=layout,caption_height=max(18,layout.get_pixel_size()[1]))
            geom['h']=geom['ih']+geom['caption_height']+40
        except Exception:geom['h']=60

    def render_image(self,ctx,g,x,y):
        p=self.palette;block=g['block']
        # Scale a screenshot once at each display size / subpixel position.
        # Include the fractional origin so cached pixels stay aligned on scroll.
        ix,iy=math.floor(x),math.floor(y);fx,fy=x-ix,y-iy
        key=('scaled',g['image_path'],g['ratio'],fx,fy,p['bg'])
        width,height=max(1,math.ceil(g['iw']+fx)),max(1,math.ceil(g['ih']+fy))
        scaled=self.image_cache.get(key)
        if scaled is None and width*height*4<=self.image_cache.max_cost:
            scaled=cairo.ImageSurface(cairo.FORMAT_ARGB32,width,height)
            paint=cairo.Context(scaled);color(paint,p['bg']);paint.paint()
            paint.translate(fx,fy);paint.scale(g['ratio'],g['ratio'])
            paint.set_source_surface(g['image'],0,0);paint.paint()
            self.image_cache.put(key,scaled,scaled.get_stride()*scaled.get_height())
        if scaled is None:
            # Very tall images should not allocate an unbounded scaled surface.
            ctx.save();ctx.translate(x,y);ctx.scale(g['ratio'],g['ratio'])
            ctx.set_source_surface(g['image'],0,0);ctx.paint();ctx.restore()
        else:ctx.set_source_surface(scaled,ix,iy);ctx.paint()
        color(ctx,p['muted']);ctx.move_to(x,y+g['ih']+4);PangoCairo.show_layout(ctx,g['caption_layout'])
        if self.doc.cursor[0]!=g['index'] or self.overlay or self.prompt:return
        color(ctx,p['accent']);ctx.set_line_width(2);ctx.rectangle(x-1,y-1,g['iw']+2,g['ih']+2);ctx.stroke()
        # A corner handle changes only the displayed size; the original stays intact.
        ctx.rectangle(x+g['iw']-6,y+g['ih']-6,12,12);ctx.fill()
        cy=y+g['ih']+g['caption_height']+6
        if cy<self.body_top or cy+25>self.body_bottom:return
        self.button(ctx,'−',x,cy,28,25,lambda:self.resize_selected_image(-32))
        self.button(ctx,'+',x+34,cy,28,25,lambda:self.resize_selected_image(32))
        self.button(ctx,'Reset',x+68,cy,55,25,lambda:self.resize_selected_image(reset=True))
        self.button(ctx,'Annotate',x+129,cy,83,25,self.annotate_image)
        label(ctx,f'{round(g["iw"])} × {round(g["ih"])}',x+222,cy+5,11,p['muted'])

    def annotate_image(self):
        block=self.doc.blocks[self.doc.cursor[0]]
        if block['type']!='image':return
        def apply(value):
            if value!=block.get('caption',''):
                self.doc.checkpoint();block['caption']=value;self.doc.mark();self.save()
        self.request_prompt('Image annotation',block.get('caption',''),apply)
        self.focus_button=-1
        self.prompt.update(inline=True,location='footer',limit=240)

    def resize_selected_image(self,delta=0,reset=False):
        index=self.doc.cursor[0]
        g=next((g for g in self.geometry if g['index']==index and 'image' in g),None)
        if g is None:return
        image=g['image'];default=min(image.get_width(),320*image.get_width()/image.get_height())
        current=min(g['w'],self.doc.blocks[index].get('width',default))
        width=None if reset else max(16,min(4096,g['w'],current+delta))
        if self.doc.resize_image(index,width):self.render_needed=True

    def resize_image_drag(self,x,y):
        drag=self.image_drag
        if drag is None:return
        # Project pointer movement onto the image diagonal to preserve its aspect ratio.
        aspect=drag['aspect'];delta=((x-drag['x'])+(y-drag['y'])*aspect)/(1+aspect*aspect)
        width=max(16,min(4096,drag['max'],drag['width']+delta))
        if not drag['changed'] and round(width)==round(drag['width']):return
        if self.doc.resize_image(drag['index'],width,checkpoint=not drag['changed']):
            drag['changed']=True;self.render_needed=True
