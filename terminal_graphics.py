"""Kitty transport: diff terminal rows and upload graphics only when pixels change."""
import base64
import io
import sys
import cairo


def terminal_mode(mode):
    """Scope Kitty's font shortcuts to Samatha without changing shell bindings."""
    value='='+base64.b64encode(mode.encode()).decode() if mode else ''
    return '\x1b]1337;SetUserVar=samatha'+value+'\x07'


class KittyRenderer:
    def __init__(self):
        self.last_rows=None;self.last_image=None;self.last_size=None;self.last_pixels=None

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
            stream=io.BytesIO();band.write_to_png(stream)
            payload=base64.b64encode(stream.getvalue())
            if replace:parts.append(f'\x1b[{row};{col}H'.encode())
            action='a=T,f=100,i=42,p=1,q=2,C=1' if replace else f'a=f,f=100,i=42,r=1,X=1,x=0,y={top},q=2'
            for offset in range(0,len(payload),4096):
                more=int(offset+4096<len(payload))
                head=f'{action},m={more}' if offset==0 else f'a={"T" if replace else "f"},m={more},q=2'
                parts.extend((b'\x1b_G',head.encode(),b';',payload[offset:offset+4096],b'\x1b\\'))
        parts.append(b'\x1b[?2026l')
        sys.stdout.buffer.write(b''.join(parts));sys.stdout.buffer.flush()
        self.last_rows=tuple(lines);self.last_image=signature;self.last_size=size;self.last_pixels=pixels
