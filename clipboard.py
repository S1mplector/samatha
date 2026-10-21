"""Desktop clipboard bridge. GTK is used only for clipboard access, never windows."""
import base64
import json
import sys
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Gdk','4.0')
from gi.repository import Gdk, GLib, Gio, Gtk

def read_clipboard(image_only=False):
    Gtk.init()
    display=Gdk.Display.get_default()
    if not display: raise RuntimeError('No desktop clipboard available')
    clipboard=display.get_clipboard()
    loop=GLib.MainLoop(); result={}
    chunks=[]; total=0
    def read_chunk(stream,res,mime):
        nonlocal total
        try:
            data=stream.read_bytes_finish(res).get_data()
            if data:
                chunks.append(data); total+=len(data)
                if total>25_000_000: raise ValueError('Clipboard content is too large')
                stream.read_bytes_async(65536,GLib.PRIORITY_DEFAULT,None,read_chunk,mime)
                return
            raw=b''.join(chunks)
            if mime.startswith('image/'):
                result.update(type='image',data=base64.b64encode(raw).decode())
            else: result.update(type='html' if mime=='text/html' else 'text',data=raw.decode('utf-8','replace'))
        except Exception as e: result.update(error=str(e))
        loop.quit()
    def done_read(obj,res):
        try:
            stream,mime=obj.read_finish(res)
            stream.read_bytes_async(65536,GLib.PRIORITY_DEFAULT,None,read_chunk,mime)
        except Exception as e:
            result.update(error='No image on the clipboard. Copy an image, then press Ctrl+G.' if image_only else str(e)); loop.quit()
    formats=['image/png','image/jpeg','image/webp','image/bmp']
    if not image_only:formats+=['text/html','text/plain;charset=utf-8','text/plain']
    clipboard.read_async(formats,GLib.PRIORITY_DEFAULT,None,done_read)
    GLib.timeout_add_seconds(4,lambda:(result.update(error='Clipboard did not respond'),loop.quit(),False)[-1])
    loop.run(); return result

def own_clipboard(data):
    Gtk.init(); clip=Gdk.Display.get_default().get_clipboard()
    providers=[Gdk.ContentProvider.new_for_bytes('text/plain;charset=utf-8',GLib.Bytes.new(data['text'].encode()))]
    if data.get('html'): providers.append(Gdk.ContentProvider.new_for_bytes('text/html',GLib.Bytes.new(data['html'].encode())))
    if data.get('image'): providers=[Gdk.ContentProvider.new_for_bytes('image/png',GLib.Bytes.new(base64.b64decode(data['image'])))]
    provider=Gdk.ContentProvider.new_union(providers)
    clip.set_content(provider)
    loop=GLib.MainLoop()
    clip.connect('changed',lambda c: loop.quit() if not c.is_local() else None)
    loop.run()

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='write': own_clipboard(json.load(sys.stdin))
    else:
        try: print(json.dumps(read_clipboard(image_only='--image' in sys.argv[1:])))
        except Exception as e: print(json.dumps({'error':str(e)}))
