#!/usr/bin/python3
"""Install launchers for this checkout. Does not modify system packages."""
from pathlib import Path
import os
import re
import sys

root=Path(__file__).resolve().parent
commands=[Path.home()/'.local/bin'/name for name in ('samatha','Samatha')]
for binary in commands:
    if (binary.exists() or binary.is_symlink()) and binary.resolve()!=root/'samatha':
        raise SystemExit(f'{binary} already exists and belongs to another installation. Left untouched.')
for binary in commands:
    binary.parent.mkdir(parents=True,exist_ok=True)
    if not binary.exists() and not binary.is_symlink():binary.symlink_to(root/'samatha')
(root/'samatha').chmod(0o755)
desktop=Path.home()/'.local/share/applications/samatha.desktop'
desktop.parent.mkdir(parents=True,exist_ok=True)
# Desktop Exec quoting follows the desktop-entry specification, not shell syntax.
path=str(root/'samatha').replace('\\','\\\\').replace('"','\\"').replace('`','\\`').replace('$','\\$')
desktop.write_text('[Desktop Entry]\nType=Application\nName=Samatha\nComment=Notes\nExec="'+path+'" --window\nIcon='+str(root/'assets/samatha.svg')+'\nTerminal=false\nCategories=Office;Utility;\nStartupWMClass=Samatha\n')
# Conditional mappings also work when Samatha is opened in an existing Kitty.
# Keep other terminal preferences intact and replace only our managed include.
config_home=Path(os.environ.get('XDG_CONFIG_HOME',Path.home()/'.config'))
kitty_config=Path(os.environ.get('KITTY_CONFIG_DIRECTORY',config_home/'kitty'))/'kitty.conf'
existing=kitty_config.read_text() if kitty_config.exists() else ''
block='# BEGIN SAMATHA SHORTCUTS\ninclude '+str(root/'assets/kitty-shortcuts.conf')+'\n# END SAMATHA SHORTCUTS'
pattern=r'(?m)^# BEGIN SAMATHA SHORTCUTS\n.*?^# END SAMATHA SHORTCUTS'
updated=re.sub(pattern,lambda _:block,existing,flags=re.S) if re.search(pattern,existing,re.S) else existing+('' if not existing or existing.endswith('\n') else '\n')+block+'\n'
if updated!=existing:
    kitty_config.parent.mkdir(parents=True,exist_ok=True)
    kitty_config.write_text(updated)
print('Installed. Open Samatha from your app launcher, or run: Samatha (or samatha)')
print('In an already-open Kitty terminal, press Ctrl+Shift+F5 once to reload the font shortcuts.')
