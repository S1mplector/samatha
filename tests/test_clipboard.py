"""Exercise GTK's real image negotiation on private, in-process clipboards.

These clipboard objects are not the desktop selection; tests preserve whatever
the user has copied and never create a window.
"""
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from PIL import Image
from clipboard import Gdk, Gio, GLib, Gtk, read_clipboard


class ClipboardBackendTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('WAYLAND_DISPLAY') and os.environ.get('DISPLAY'),'Needs a Wayland desktop with XWayland')
    def test_fresh_helper_uses_windowless_bridge_before_gtk_initializes(self):
        helper=Path(__file__).resolve().parents[1]/'clipboard.py'
        for inherited in (None,'wayland'):
            with self.subTest(GDK_BACKEND=inherited):
                env=dict(os.environ)
                env.pop('GDK_BACKEND',None)
                if inherited:env['GDK_BACKEND']=inherited
                output=subprocess.run(['/usr/bin/python3',str(helper),'--diagnose'],env=env,
                                      capture_output=True,text=True,check=True,timeout=5)
                self.assertEqual(json.loads(output.stdout)['backend'],'GdkX11Display')


class ClipboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.init_check():raise unittest.SkipTest('GTK display unavailable')
        cls.display=Gdk.Display.get_default()
        if cls.display is None:raise unittest.SkipTest('GTK display unavailable')

    def setUp(self):
        self.clipboard=Gdk.Clipboard(display=self.display)

    def read(self,image_only=True):
        return read_clipboard(image_only,clipboard=self.clipboard)

    def encoded(self,format='PNG'):
        stream=io.BytesIO();Image.new('RGB',(32,24),'purple').save(stream,format=format)
        return stream.getvalue()

    def provider(self,mime,data):
        return Gdk.ContentProvider.new_for_bytes(mime,GLib.Bytes.new(data))

    def assert_image(self,result):
        self.assertEqual(result.get('type'),'image',result)
        with Image.open(io.BytesIO(base64.b64decode(result['data']))) as image:
            self.assertEqual(image.size,(32,24))

    def test_native_screenshot_texture_with_no_mime_types(self):
        texture=Gdk.Texture.new_from_bytes(GLib.Bytes.new(self.encoded()))
        self.clipboard.set_content(Gdk.ContentProvider.new_for_value(texture))
        self.assertEqual(self.clipboard.get_formats().get_mime_types(),[])
        self.assert_image(self.read())

    def test_encoded_screenshot_and_other_raster_formats(self):
        for mime,format in [('image/png','PNG'),('image/jpeg','JPEG'),('image/tiff','TIFF'),('image/gif','GIF')]:
            with self.subTest(mime=mime):
                self.clipboard.set_content(self.provider(mime,self.encoded(format)))
                self.assert_image(self.read())

    def test_image_wins_when_clipboard_also_contains_text(self):
        self.clipboard.set_content(Gdk.ContentProvider.new_union([
            self.provider('text/plain',b'Image description'),
            self.provider('image/png',self.encoded()),
        ]))
        self.assert_image(self.read(image_only=False))

    def test_formatted_and_plain_text_still_paste(self):
        for mime,type,data in [('text/html','html','<b>Formatting</b>'),('text/plain','text','Plain text')]:
            self.clipboard.set_content(self.provider(mime,data.encode()))
            self.assertEqual(self.read(image_only=False),{'type':type,'data':data})

    def test_text_is_not_reported_as_an_image(self):
        self.clipboard.set_content(self.provider('text/plain',b'Only text'))
        self.assertIn('No image on the clipboard',self.read()['error'])

    def test_image_size_limit_remains_enforced(self):
        self.clipboard.set_content(self.provider('image/png',self.encoded()))
        with patch('clipboard.MAX_BYTES',20):
            self.assertIn('too large',self.read()['error'])

    def test_timeout_cancels_an_unresponsive_source(self):
        class Unresponsive:
            def read_texture_async(self,cancelled,callback):self.cancelled=cancelled
        clipboard=Unresponsive()
        result=read_clipboard(True,clipboard=clipboard,timeout_ms=10)
        self.assertEqual(result,{'error':'Clipboard did not respond'})
        self.assertTrue(clipboard.cancelled.is_cancelled())

    def test_transfer_error_is_not_mislabeled_as_an_empty_clipboard(self):
        class Denied:
            def read_texture_async(self,cancelled,callback):
                GLib.idle_add(lambda:(callback(self,None),False)[1])
            def read_texture_finish(self,result):
                raise GLib.Error.new_literal(Gio.io_error_quark(),'Cannot convert image',Gio.IOErrorEnum.NOT_SUPPORTED)
            def read_async(self,formats,priority,cancelled,callback):
                GLib.idle_add(lambda:(callback(self,None),False)[1])
            def read_finish(self,result):
                raise GLib.Error.new_literal(Gio.io_error_quark(),'Clipboard access denied',Gio.IOErrorEnum.PERMISSION_DENIED)
        result=read_clipboard(True,clipboard=Denied())
        self.assertIn('denied clipboard access',result['error'])
        self.assertNotIn('g-io-error-quark',result['error'])
        self.assertIn('Clipboard access denied',result['detail'])
        self.assertNotIn('No image',result['error'])


if __name__=='__main__':unittest.main()
