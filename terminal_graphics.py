"""Kitty transport: diff terminal rows and upload graphics only when pixels change."""
import base64
import hashlib
import io
import sys


def terminal_mode(mode):
    """Scope Kitty's font shortcuts to Samatha without changing shell bindings."""
    value='='+base64.b64encode(mode.encode()).decode() if mode else ''
    return '\x1b]1337;SetUserVar=samatha'+value+'\x07'


class KittyRenderer:
    def __init__(self):
        self.last_rows=None;self.last_image=None;self.last_size=None

    def transmit(self,surface,lines=(),row=1,col=1,size=None,base_style=''):
        # Hashing pixels is substantially cheaper than PNG encoding and retransmission.
        surface.flush()
        signature=(row,col,surface.get_width(),surface.get_height(),
                   hashlib.blake2b(surface.get_data(),digest_size=16).digest())
        image_changed=signature!=self.last_image or size!=self.last_size
        previous=self.last_rows if size==self.last_size else None
        changed=[(i,line) for i,line in enumerate(lines) if previous is None or i>=len(previous) or previous[i]!=line]
        if not image_changed and not changed:return
        parts=[b'\x1b[?2026h']
        if image_changed:parts.append(b'\x1b_Ga=d,d=I,i=42,q=2\x1b\\')
        for i,line in changed:
            parts.append((f'\x1b[{i+1};1H'+base_style+line).encode())
        if changed:parts.append(b'\x1b[0m')
        if image_changed:
            stream=io.BytesIO();surface.write_to_png(stream)
            payload=base64.b64encode(stream.getvalue())
            parts.append(f'\x1b[{row};{col}H'.encode())
            for offset in range(0,len(payload),4096):
                more=int(offset+4096<len(payload))
                head=f'a=T,f=100,i=42,p=1,q=2,C=1,m={more}' if offset==0 else f'm={more},q=2'
                parts.extend((b'\x1b_G',head.encode(),b';',payload[offset:offset+4096],b'\x1b\\'))
        parts.append(b'\x1b[?2026l')
        sys.stdout.buffer.write(b''.join(parts));sys.stdout.buffer.flush()
        self.last_rows=tuple(lines);self.last_image=signature;self.last_size=size
