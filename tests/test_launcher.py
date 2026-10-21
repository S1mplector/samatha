import os
import contextlib
import io
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def launch(self,tty,args=(),kitty=False):
        class Executed(Exception):pass
        with tempfile.TemporaryDirectory() as directory:
            env={**os.environ,'SAMATHA_EDITOR':'nano'}
            if kitty:env['KITTY_WINDOW_ID']='existing-window'
            else:env.pop('KITTY_WINDOW_ID',None)
            with patch.dict(os.environ,env,clear=True), \
                 patch.object(sys,'argv',[str(ROOT/'samatha'),'--notes-dir',directory,*args]), \
                 patch.object(sys.stdin,'isatty',return_value=tty), \
                 patch.object(sys.stdout,'isatty',return_value=tty), \
                 patch('shutil.which',return_value='/test/kitty') as find_kitty, \
                 patch('os.execv',side_effect=Executed) as execute:
                with self.assertRaises(Executed):runpy.run_path(str(ROOT/'samatha'),run_name='__main__')
                return execute.call_args.args,find_kitty.called

    def test_terminal_invocation_runs_tui_in_place_with_or_without_kitty(self):
        for kitty in (True,False):
            (program,args),opened_window=self.launch(True,kitty=kitty)
            self.assertEqual(program,str(ROOT/'bin/samatha-tui'))
            self.assertFalse(opened_window)

    def test_explicit_window_and_desktop_launch_use_window(self):
        for tty,args in [(True,('--window',)),(False,())]:
            (program,command),opened_window=self.launch(tty,args)
            self.assertEqual(program,'/test/kitty');self.assertTrue(opened_window)
            self.assertIn('--here',command);self.assertNotIn('--window',command)

    def test_explicit_here_never_creates_window(self):
        (program,args),opened_window=self.launch(False,('--here',))
        self.assertEqual(program,str(ROOT/'bin/samatha-tui'));self.assertFalse(opened_window)

    def test_installer_preserves_kitty_settings_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);config=home/'config/kitty/kitty.conf'
            config.parent.mkdir(parents=True)
            original='font_size 13\nmap ctrl+alt+x new_window\n'
            config.write_text(original)
            env={**os.environ,'XDG_CONFIG_HOME':str(home/'config')}
            env.pop('KITTY_CONFIG_DIRECTORY',None)
            with patch.object(Path,'home',return_value=home),patch.dict(os.environ,env,clear=True),contextlib.redirect_stdout(io.StringIO()):
                runpy.run_path(str(ROOT/'install.py'),run_name='__main__')
                first=config.read_text()
                runpy.run_path(str(ROOT/'install.py'),run_name='__main__')
            self.assertTrue(first.startswith(original))
            self.assertEqual(config.read_text(),first)
            self.assertEqual(first.count('include '),1)
            self.assertIn(str(ROOT/'assets/kitty-shortcuts.conf'),first)
            self.assertEqual((home/'.local/bin/Samatha').resolve(),ROOT/'samatha')


if __name__=='__main__':unittest.main()
