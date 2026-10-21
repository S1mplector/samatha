"""Kitty transport: diff terminal rows and upload graphics only when pixels change."""
import base64
import io
import sys
import os
import tempfile
import cairo
from PIL import Image


def terminal_mode(mode):
    """Scope Kitty's font shortcuts to Samatha without changing shell bindings."""
    value='='+base64.b64encode(mode.encode()).decode() if mode else ''
    return '\x1b]1337;SetUserVar=samatha'+value+'\x07'


class KittyRenderer:
    def __init__(self,local_files=None):
        self.last_rows=None;self.last_image=None;self.last_size=None;self.last_pixels=None
        self.local_files=(bool(os.environ.get('KITTY_WINDOW_ID')) and not os.environ.get('SSH_CONNECTION')
                          if local_files is None else local_files)
        self.pending_files=[]

    def encode(self,band):
        """Large local frames use Kitty's temporary-file transport, avoiding PNG
        compression and megabytes of PTY traffic while scrolling screenshots.
        Kitty deletes each private file after reading it. Remote sessions use PNG.
        """
        width,height=band.get_width(),band.get_height()
        if self.local_files and width*height*4>256*1024:
            self.pending_files=[path for path in self.pending_files if os.path.exists(path)]
            # Bound outstanding files if the receiving terminal stops consuming.
            if len(self.pending_files)<8:
                path=None
                try:
                    mode,raw=('RGBa','BGRa') if band.get_format()==cairo.FORMAT_ARGB32 else ('RGB','BGRX')
                    image=Image.frombytes(mode,(width,height),bytes(band.get_data()),'raw',raw,band.get_stride(),1).convert('RGBA')
                    fd,path=tempfile.mkstemp(prefix='samatha-tty-graphics-protocol-')
                    with os.fdopen(fd,'wb') as out:out.write(image.tobytes())
                    self.pending_files.append(path)
                    return f'f=32,t=t,s={width},v={height}',base64.b64encode(os.fsencode(path))
                except OSError:
                    if path and os.path.exists(path):os.unlink(path)
        stream=io.BytesIO();band.write_to_png(stream)
        return 'f=100',base64.b64encode(stream.getvalue())

    def transmit(self,surface,lines=(),row=1,col=1,size=None,base_style=''):
        # Keep one image placement and replace only changed scanlines in its root
        # frame (Kitty a=f,r=1). Typing must not recompress unchanged screenshots.
        surface.flush()
        width,height,stride=surface.get_width(),surface.get_height(),surface.get_stride()
        signature=(row,col,width,height,stride,surface.get_format())
        pixels=bytes(surface.get_data())
        replace=signature!=self.last_image or size!=self.last_size
        image_changed=replace or pixels!=self.last_pixels
        previous=self.last_rows if size==self.last_size else None
        changed=[(i,line) for i,line in enumerate(lines) if previous is None or i>=len(previous) or previous[i]!=line]
        if not image_changed and not changed:return
        parts=[b'\x1b[?2026h']
        if replace:parts.append(b'\x1b_Ga=d,d=I,i=42,q=2\x1b\\')
        for i,line in changed:
            parts.append((f'\x1b[{i+1};1H'+base_style+line).encode())
        if changed:parts.append(b'\x1b[0m')
        if image_changed:
            top,bottom=0,height
            if not replace:
                # Native byte comparisons are cheap, including on image-heavy pages.
                while pixels[top*stride:(top+1)*stride]==self.last_pixels[top*stride:(top+1)*stride]:top+=1
                while pixels[(bottom-1)*stride:bottom*stride]==self.last_pixels[(bottom-1)*stride:bottom*stride]:bottom-=1
            band=cairo.ImageSurface.create_for_data(surface.get_data()[top*stride:bottom*stride],
                                                   surface.get_format(),width,bottom-top,stride)
            encoding,payload=self.encode(band)
            if replace:parts.append(f'\x1b[{row};{col}H'.encode())
            action=f'a=T,{encoding},i=42,p=1,q=2,C=1' if replace else f'a=f,{encoding},i=42,r=1,X=1,x=0,y={top},q=2'
            for offset in range(0,len(payload),4096):
                more=int(offset+4096<len(payload))
                head=f'{action},m={more}' if offset==0 else f'a={"T" if replace else "f"},m={more},q=2'
                parts.extend((b'\x1b_G',head.encode(),b';',payload[offset:offset+4096],b'\x1b\\'))
        parts.append(b'\x1b[?2026l')
        sys.stdout.buffer.write(b''.join(parts));sys.stdout.buffer.flush()
        self.last_rows=tuple(lines);self.last_image=signature;self.last_size=size;self.last_pixels=pixels
