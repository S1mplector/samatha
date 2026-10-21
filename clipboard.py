"""Desktop clipboard bridge. GTK is used only for clipboard access, never windows."""
import base64
import json
import os
import sys

# A helper without a focused surface cannot receive a Wayland clipboard offer.
# Use the desktop's XWayland clipboard bridge, without creating any window.
# This must run before importing Gtk: PyGObject can initialize it on import.
# Scope the choice to this helper process, leaving the terminal/app untouched.
if __name__=='__main__' and os.environ.get('WAYLAND_DISPLAY') and os.environ.get('DISPLAY'):
    os.environ['GDK_BACKEND']='x11'

import gi
gi.require_version('Gtk','4.0')
gi.require_version('Gdk','4.0')
from gi.repository import Gdk, GLib, Gio, Gtk

MAX_BYTES=25_000_000
IMAGE_FORMATS=['image/png','image/jpeg','image/webp','image/bmp','image/tiff','image/gif','image/x-bmp']


def clipboard_error(error):
    if isinstance(error,GLib.Error):
        if error.matches(Gio.io_error_quark(),Gio.IOErrorEnum.PERMISSION_DENIED):
            return 'Your desktop denied clipboard access. Copy the image again and retry.'
        return 'Your desktop could not provide the clipboard contents. Copy again and retry.'
    return str(error)


def read_clipboard(image_only=False,*,clipboard=None,timeout_ms=4000):
    if clipboard is None:
        Gtk.init()
        display=Gdk.Display.get_default()
        if not display: raise RuntimeError('No desktop clipboard available')
        clipboard=display.get_clipboard()
    loop=GLib.MainLoop(); result={}
    chunks=[]; total=0; stream=None
    cancelled=Gio.Cancellable()
    def finish(**value):
        if result:return
        result.update(value);loop.quit()
    def image_result(raw):
        if not raw:raise ValueError('Clipboard image is empty')
        if len(raw)>MAX_BYTES:raise ValueError('Clipboard content is too large')
        finish(type='image',data=base64.b64encode(raw).decode())
    def read_chunk(stream,res,mime):
        nonlocal total
        if result:return
        try:
            data=stream.read_bytes_finish(res).get_data()
            if data:
                chunks.append(data); total+=len(data)
                if total>MAX_BYTES: raise ValueError('Clipboard content is too large')
                stream.read_bytes_async(65536,GLib.PRIORITY_DEFAULT,cancelled,read_chunk,mime)
                return
            raw=b''.join(chunks)
            if mime.startswith('image/'):
                image_result(raw)
            else: finish(type='html' if mime=='text/html' else 'text',data=raw.decode('utf-8','replace'))
        except Exception as e: finish(error=clipboard_error(e),detail=str(e))
    def done_read(obj,res):
        nonlocal stream
        if result:return
        try:
            stream,mime=obj.read_finish(res)
            stream.read_bytes_async(65536,GLib.PRIORITY_DEFAULT,cancelled,read_chunk,mime)
        except Exception as e:
            unavailable=isinstance(e,GLib.Error) and e.matches(Gio.io_error_quark(),Gio.IOErrorEnum.NOT_SUPPORTED)
            # X11 sometimes reports unsupported formats as a generic FAILED
            # error. Only call it empty when the advertised formats confirm it.
            if image_only and hasattr(clipboard,'get_formats'):
                offered=clipboard.get_formats()
                types=offered.get_gtypes();mimes=offered.get_mime_types()
                if types or mimes:
                    unavailable=unavailable or not (any(m.startswith('image/') for m in mimes) or any(t.is_a(Gdk.Texture.__gtype__) for t in types))
            finish(error='No image on the clipboard. Copy an image, then press Ctrl+G.' if image_only and unavailable else clipboard_error(e),detail=str(e))
    def done_texture(obj,res):
        if result:return
        try:
            texture=obj.read_texture_finish(res)
        except GLib.Error:
            texture=None
        if texture is not None:
            try:image_result(texture.save_to_png_bytes().get_data())
            except Exception as e:finish(error=clipboard_error(e),detail=str(e))
            return
        # Some applications expose encoded bytes rather than a native texture.
        # Keep those formats and formatted/text paste as compatible fallbacks.
        formats=list(IMAGE_FORMATS)
        if not image_only:formats+=['text/html','text/plain;charset=utf-8','text/plain']
        clipboard.read_async(formats,GLib.PRIORITY_DEFAULT,cancelled,done_read)
    def timed_out():
        finish(error='Clipboard did not respond');return False
    # Screenshot tools can offer GdkTexture/GdkMemoryTexture instead of one of
    # the raw MIME types. GTK negotiates and converts those to a PNG for us.
    clipboard.read_texture_async(cancelled,done_texture)
    timeout=GLib.timeout_add(timeout_ms,timed_out)
    try:
        loop.run()
        return result
    finally:
        cancelled.cancel()
        if GLib.MainContext.default().find_source_by_id(timeout):GLib.source_remove(timeout)
        if stream is not None:
            try:stream.close(None)
            except GLib.Error:pass

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
    elif '--diagnose' in sys.argv[1:]:
        # Report the connection used by a fresh helper without reading content.
        Gtk.init();display=Gdk.Display.get_default()
        print(json.dumps({'backend':display.__gtype__.name if display else None}))
    else:
        try: print(json.dumps(read_clipboard(image_only='--image' in sys.argv[1:])))
        except Exception as e: print(json.dumps({'error':clipboard_error(e),'detail':str(e)}))
