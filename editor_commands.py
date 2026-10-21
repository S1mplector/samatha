"""Editor commands and shortcut registration, independent of the terminal shell."""
from dataclasses import dataclass
from typing import Callable
from model import BOLD, ITALIC, UNDERLINE, HIGHLIGHT, length


@dataclass(frozen=True)
class Command:
    name: str
    key: str
    action: Callable
    ctrl: bool = True
    alt: bool = False
    shift: bool | None = None
    enabled: Callable = lambda editor: True


class CommandRegistry:
    def __init__(self):self.bindings={}

    def register(self,command,replace=False):
        chord=(command.key,command.ctrl,command.alt,command.shift)
        if chord in self.bindings and not replace:
            raise ValueError(f'Shortcut already registered: {chord}')
        self.bindings[chord]=command

    def dispatch(self,editor,key,ctrl=False,alt=False,shift=False):
        command=self.bindings.get((key,ctrl,alt,shift)) or self.bindings.get((key,ctrl,alt,None))
        if command is None or not command.enabled(editor):return False
        command.action(editor);editor.render_needed=True
        return True


def select_all(editor):
    editor.doc.anchor=(0,0)
    editor.doc.cursor=(len(editor.doc.blocks)-1,length(editor.doc.blocks[-1]))


def default_commands():
    registry=CommandRegistry()
    for name,key,action in [
        ('bold','b',lambda e:e.format(BOLD)),('italic','i',lambda e:e.format(ITALIC)),
        ('underline','u',lambda e:e.format(UNDERLINE)),('highlight','h',lambda e:e.format(HIGHLIGHT)),
        ('save','s',lambda e:e.notify('Saved') if e.save() else None),
        ('new-note','n',lambda e:e.new_note()),('library','o',lambda e:e.open_overlay('library')),
        ('rename','r',lambda e:e.rename_note()),('divider','d',lambda e:e.divider()),
        ('paste-image','g',lambda e:e.paste_image()),
        ('paste','v',lambda e:e.paste()),('copy','c',lambda e:e.copy()),('cut','x',lambda e:e.copy(True)),
        ('undo','z',lambda e:e.doc.undo()),('redo','y',lambda e:e.doc.undo(True)),
        ('settings',',',lambda e:e.open_overlay('settings')),
        ('delete-word','backspace',lambda e:e.doc.delete_word()),('select-all','a',select_all),
    ]:registry.register(Command(name,key,action))
    registry.register(Command('redo-shift','z',lambda e:e.doc.undo(True),shift=True))
    registry.register(Command('image-file','g',lambda e:e.image_path(),shift=True))
    for name,key,delta,reset in [('smaller-image','left',-32,False),('larger-image','right',32,False),('reset-image','0',0,True)]:
        registry.register(Command(name,key,lambda e,d=delta,r=reset:e.resize_selected_image(d,reset=r),
                                  ctrl=False,alt=True,enabled=lambda e:e.doc.blocks[e.doc.cursor[0]]['type']=='image'))
    return registry
