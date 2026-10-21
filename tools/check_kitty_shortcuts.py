"""Validate mode routing with Kitty's own parser and key matcher, without a GUI.

Run: kitty +runpy "import runpy; runpy.run_path('tools/check_kitty_shortcuts.py')"
"""
from pathlib import Path
from types import MethodType, SimpleNamespace

from kitty.config import load_config
from kitty.keys import Mappings
from kitty.options.utils import parse_key_action
from kitty.window import Window


class HeadlessMappings(Mappings):
    def __init__(self):
        self.window=SimpleNamespace(user_vars={})
        self.window.matches=MethodType(Window.matches,self.window)

    def get_active_window(self):return self.window

    def match_windows(self,query):
        # These bindings use one var expression, evaluated by Kitty itself.
        field,value=query.split(':',1)
        return [self.window] if Window.matches_query(self.window,field,value) else []


root=Path(__file__).resolve().parents[1]
bad=[]
options=load_config(str(root/'assets/kitty-shortcuts.conf'),accumulate_bad_lines=bad)
assert not bad,bad
defaults=load_config('/dev/null').keyboard_modes[''].keymap
matcher=HeadlessMappings()
checked=0
for key,candidates in options.keyboard_modes[''].keymap.items():
    if not any('var:samatha=' in candidate.options.when_focus_on for candidate in candidates):continue
    for mode in ('browser','editor',''):
        matcher.window.user_vars={'samatha':mode} if mode else {}
        selected=matcher.matching_key_actions(candidates)
        if mode=='':
            assert selected==matcher.matching_key_actions(defaults.get(key,[])),f'Shell shortcut changed: {key}'
            continue
        assert len(selected)==1,(key,mode,selected)
        assert selected[0].options.when_focus_on=='var:samatha='+mode
        action=parse_key_action(selected[0].definition)
        if mode=='browser':
            assert action.func=='change_font_size',action
            assert selected[0].definition.startswith('change_font_size current ')
        else:
            assert action.func=='send_text' and action.args[0]=='all',action
            assert action.args[1] in (b'\x1b[43;5u',b'\x1b[61;5u',b'\x1b[45;5u',b'\x1b[48;5u'),action
    checked+=1
assert checked==9,checked
print('PASS: all 9 font shortcuts route correctly in browse/edit modes and preserve shell bindings.')
