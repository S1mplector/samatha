<p align="center"><img src="assets/samatha.svg" alt="Samatha Dharma wheel" width="80"></p>

# Samatha

Samatha is a fast visual notebook for your terminal. Paste a screenshot, keep an annotation beside it, and move between browsing and writing in the same window. Rich text, seven themes, and a quiet folder tree keep your observations close to their context.

Select an image and press **Alt+Enter** or click **Annotate** to add a short observation in the footer. Annotations stay with the image, appear in browsing and editing, and support undo. **Tab** in the browser hides the sidebar for a focused view.

For Linux, install Go 1.21 or newer and the system Python dependencies. On Ubuntu/Debian:

```sh
sudo apt install golang-go kitty python3-cairo python3-gi python3-gi-cairo python3-pil gir1.2-gtk-4.0 fonts-dejavu-core
git clone https://github.com/S1mplector/samatha.git
cd samatha
./build.sh
/usr/bin/python3 install.py
samatha
```

The installer adds `samatha` and `Samatha` to `~/.local/bin`, a desktop entry with the Dharma wheel icon, and a scoped shortcut include in Kitty's configuration. Keep the checkout in place and ensure `~/.local/bin` is in your shell's `PATH`. Run in Kitty for rich text and images; other terminals use your configured text editor. The compiled executable is built locally and is not stored in the repository.

Run `Samatha` or `samatha` from any directory to open it in your current terminal. Select a note and press **Enter** for rich editing in the same terminal panes, including bold, italic, underline, highlight, images, formatted clipboard paste, undo/redo, Ctrl-held word deletion preview, font settings, autosave, and focus timer. **Ctrl+Q** saves and returns to the TUI. **Esc** also returns to browsing. **Ctrl+,** opens settings; **Ctrl+P** opens the timer; **Ctrl+G** pastes a clipboard image directly, with no popup. **Ctrl+Shift+G** adds an image by path using an inline footer field. **Ctrl+V** still pastes images or formatted text.

Clipboard images are read through GTK's native image API, including screenshot textures and encoded raster formats. On Wayland desktops with XWayland available, the helper connects through the XWayland clipboard bridge: a Wayland helper without a focused window cannot receive clipboard offers. This choice is limited to the helper process, which opens no windows. `/usr/bin/python3 clipboard.py --diagnose` reports its connection without reading clipboard contents. Tests use private GTK clipboard objects so they do not replace your desktop clipboard.

In the browser: **j/k** or arrows navigate, **h/l** collapse/expand, **n** creates a note, **N** creates a notebook at the library root, **f** creates a subfolder, **r** or **F2** renames the selected note or folder, **Enter** edits a note or renames a selected folder, **Backspace** archives, **u** restores the last archive in the current session, **Tab** toggles focus mode, **[ / ]** adjust the sidebar, **t** changes the theme, and **?** shows help. Page Up/Down scrolls the preview. **q** quits.

Renaming uses an inline field and keeps the panes in place. **Enter** confirms, **Esc** cancels, and **Ctrl+U** clears the library's rename field. Note titles change in the Markdown preview and rich document while filenames stay stable. While editing, click the title or press **Ctrl+R** to rename it in place; **Ctrl+A** replaces the current title.

Select an image in the editor and drag its bottom-right corner to resize it, or use the **−**, **+**, and **Reset** controls beneath it. **Alt+← / Alt+→** makes the selected image smaller/larger; **Alt+0** restores its automatic size. Resizing preserves proportions and the original image file, is undoable with **Ctrl+Z**, and is saved in the rich document. Images fit narrower windows and return to their saved size when space allows.

Pasted images and their note references are saved immediately. Local Kitty sessions show images inline while browsing, with normal viewport scrolling and clipping; other terminals and remote sessions retain Markdown image links. The internal `images/` attachment folder stays out of the note tree. Type **`---` on its own line, then Enter** to create an undoable line separator.

Existing JSON notebooks are copied once into `~/.local/share/samatha/notes/`. The original notebooks remain intact. Every rich note has a hidden `.NAME.md.samatha.json` companion containing the full rich document; images live in the folder's `images/` directory. Keep these files together when copying a notebook. The Markdown file is a readable preview. If an external editor changes it, the rich editor preserves those edits as a separate `-external-…md` copy before restoring the rich version. Rich-save backups are in `.samatha-backups/`; archived notes and rich companions are in `.archive/`.

**Ctrl+T** changes themes while editing; Settings → Theme lists all seven themes. The selected theme is shared with the browser and remembered. **Ctrl+N** creates a note in the current notebook; **Ctrl+Shift+N** creates a new notebook.

**Ctrl++** (or **Ctrl+=**) increases font size; **Ctrl+-** decreases it; **Ctrl+0** resets it. In Kitty, these shortcuts zoom the current terminal window while browsing, and change the note text while editing. Editing sizes from 12 to 36 are remembered per notebook and reset to 14. They also work in Settings, where the **− / +** buttons provide a mouse alternative. The mappings use [Kitty's conditional shortcuts](https://sw.kovidgoyal.net/kitty/mapping/#conditional-mappings-depending-on-the-state-of-the-focused-window) and apply only while Samatha is running. After installing, open a fresh Kitty window or press **Ctrl+Shift+F5** to reload your existing Kitty configuration. An older desktop-launched Samatha window needs to be closed and reopened once to load its updated launch configuration.

`samatha --legacy` opens the original interface and its original data. Terminal commands stay in the current terminal by default; `--here` can force this behavior, and `--window` explicitly opens a separate Kitty window. The desktop launcher uses `--window`. Kitty is needed for rich text and images; other terminals use a conventional text editor. `SAMATHA_EDITOR='nano' samatha` explicitly selects a conventional text editor. Optional settings go in `~/.config/samatha/config.yaml`: `editor`, `notes_dir`, `sidebar_width`, and `theme` (`light`, `dark`, `nord`, `dracula`, `catppuccin`, `solarized`, or `rose-pine`).

Build with Go 1.21 or newer: `./build.sh` (or `GO=/path/to/go ./build.sh`). Tests: `cd tui && go test ./...`, plus `/usr/bin/python3 -m unittest discover -s tests` from this directory. The rich editor uses the existing system Python packages for Cairo, GTK/Pango, and Pillow.

Development: `model.py` owns document mutations, validation and undo; `rich_storage.py` owns durable storage and Markdown previews. `text_blocks.py` and `image_blocks.py` provide shared rendering and image interactions for both editor shells, using the drawing primitives in `drawing.py`. `terminal_graphics.py` handles Kitty output separately from document rendering. Library prompts live in `tui/prompts.go`; colors remain in `tui/themes.json`.

`tui/handoff.go` keeps the same terminal screen visible when entering the built-in editor and retains the editor's last frame until the browser is ready. It preserves the terminal interface so startup and live resizing use the whole window. `SAMATHA_SHARED_SCREEN` coordinates screen ownership; standalone and external editors keep their normal terminal lifecycle. `/usr/bin/python3 tools/smoke_terminal.py --theme dark` verifies full-window startup, live resizing, resizing while editing, repeated transitions, and startup-error recovery without screen clears or intermediate switches to the shell.

With Kitty installed, validate shortcut parsing and routing using its own matcher: `kitty +runpy "import runpy; runpy.run_path('tools/check_kitty_shortcuts.py')"`. No GUI window is opened by this check.

Verify inline browser images, scrolling, editor transitions, and image cleanup through a real PTY and Kitty's parser with `kitty +runpy "import runpy; runpy.run_path('tools/check_browser_graphics.py')"`.

Add editing actions through `editor_commands.py` without changing the input parser. Commands run only in the editing context, outside prompts and settings. For example, after constructing an editor:

```python
from editor_commands import Command

editor.commands.register(Command(
    'insert-marker', 'm', lambda app: app.doc.insert('TODO: '),
    ctrl=True, alt=True,
))
```

Duplicate shortcuts raise an error unless `replace=True` is explicitly supplied. Mutations should use `Document` methods so undo and autosave work; direct document changes must call `checkpoint()` before modifying and `mark()` afterward. New block types also need validation, storage export/import, layout, rendering, and editing tests.

Paragraph layouts use a bounded cache keyed by content, formatting, selection, font, theme and width. Decoded and scaled images share a 64 MiB cache budget. Kitty updates only changed shell rows and replaces changed scanlines in a single image's root frame (`a=f,r=1`), so typing beside a screenshot does not recompress and resend it. Resizing and repositioning replace the complete image. Undo snapshots copy integer style arrays directly; unchanged saves avoid rewriting documents, settings and timer state.

Large local editor updates use private temporary pixel files that Kitty deletes after reading, avoiding PNG compression and large PTY writes during scrolling. Small updates and remote sessions use the direct PNG transport. Measure screenshot scrolling with `/usr/bin/python3 tools/benchmark_typing.py --scroll`.

Run `/usr/bin/python3 tools/benchmark.py` for rendering timings, or `/usr/bin/python3 tools/benchmark_typing.py` to measure keystroke-to-complete-frame latency through a real PTY on disposable short, long, and screenshot-heavy notes. The PTY benchmark includes input handling, drawing, compression, and transport, but excludes GPU presentation. Use `--editor /path/to/another/checkout/rich_editor.py` for comparisons. Verify output pixel-for-pixel through Kitty's own graphics parser with `kitty +runpy "import runpy; runpy.run_path('tools/check_kitty_graphics.py')"`; this also checks that partial updates keep one image and release it on cleanup, without opening a window.

The app icon is the editable, single-color eight-spoked Dharma wheel in `assets/samatha.svg`.
