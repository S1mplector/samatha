package main

import (
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
	"github.com/muesli/termenv"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"
)

func testModel(t *testing.T) *Model {
	t.Helper()
	cfg := &Config{NotesDir: t.TempDir(), SidebarWidth: 30, Theme: "light"}
	appDir, err := filepath.Abs("..")
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("SAMATHA_APP_DIR", appDir)
	m, err := newModel(cfg)
	if err != nil {
		t.Fatal(err)
	}
	return m
}

func TestInlineRenameKeepsPanesAndUpdatesTitle(t *testing.T) {
	m := testModel(t)
	m.newNote()
	path := m.selectedPath()
	original := "# New Note\n\n**Keep this formatting**\n\n" + strings.Repeat("Paragraph\n\n", 40)
	if err := os.WriteFile(path, []byte(original), 0600); err != nil {
		t.Fatal(err)
	}
	m.reload(path)
	m.viewport.LineDown(8)
	before := strings.Split(m.View(), "\n")
	m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("r")})
	if m.prompt != "title" {
		t.Fatal("note rename did not open")
	}
	after := strings.Split(m.View(), "\n")
	if strings.Join(before[:len(before)-2], "\n") != strings.Join(after[:len(after)-2], "\n") {
		t.Fatal("rename changed the panes or preview")
	}
	if !strings.Contains(ansi.Strip(m.View()), "Note title:") {
		t.Fatal("inline field missing")
	}
	m.input.SetValue("Renamed / 日本語")
	m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	if m.prompt != "" || m.notes[m.cursor].title != "Renamed / 日本語" {
		t.Fatal(m.message)
	}
	data, err := os.ReadFile(path)
	if err != nil || string(data) != strings.Replace(original, "# New Note", "# Renamed / 日本語", 1) {
		t.Fatal("Markdown changed", err)
	}
	if m.viewport.YOffset != 8 {
		t.Fatal("preview scrolled during rename")
	}
	m.Update(tea.KeyMsg{Type: tea.KeyF2})
	m.input.SetValue("Cancel this")
	m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	data, _ = os.ReadFile(path)
	if strings.Contains(string(data), "Cancel this") {
		t.Fatal("cancel saved title")
	}
}

func TestInlinePromptsFitWithoutChangingPaneSize(t *testing.T) {
	m := testModel(t)
	m.newNote()
	for _, size := range [][2]int{{140, 40}, {80, 24}, {60, 20}, {40, 12}, {30, 10}} {
		for _, sidebar := range []bool{true, false} {
			m.showSidebar = sidebar
			m.resize(size[0], size[1])
			for _, kind := range []string{"title", "rename", "folder"} {
				m.startPrompt(kind, m.config.NotesDir, strings.Repeat("日本語", 40))
				frame := m.View()
				if lipgloss.Width(frame) > size[0] || lipgloss.Height(frame) > size[1] {
					t.Fatalf("%v %s overflow: %dx%d", size, kind, lipgloss.Width(frame), lipgloss.Height(frame))
				}
			}
		}
	}
}
func TestLayoutFitsTerminal(t *testing.T) {
	m := testModel(t)
	m.newNote()
	for _, size := range [][2]int{{154, 53}, {141, 47}, {140, 40}, {120, 36}, {80, 24}, {60, 20}, {40, 12}, {20, 6}} {
		for _, mode := range []string{"normal", "focus", "help", "prompt"} {
			m.showSidebar = mode != "focus"
			m.help = mode == "help"
			m.prompt = ""
			if mode == "prompt" {
				m.startPrompt("folder", m.config.NotesDir, "New Folder")
			}
			m.resize(size[0], size[1])
			v := m.View()
			if lipgloss.Width(v) > size[0] || lipgloss.Height(v) > size[1] {
				t.Errorf("%v %s got %dx%d", size, mode, lipgloss.Width(v), lipgloss.Height(v))
			}
			if mode == "normal" && size[0] >= 30 && size[1] >= 10 && (lipgloss.Width(v) != size[0] || lipgloss.Height(v) != size[1]) {
				t.Errorf("%v browser did not fill terminal: %dx%d", size, lipgloss.Width(v), lipgloss.Height(v))
			}
		}
	}
}
func TestCreateArchiveRestoreRichNote(t *testing.T) {
	m := testModel(t)
	m.newNote()
	path := m.selectedPath()
	os.WriteFile(richSidecar(path), []byte("rich content"), 0600)
	m.archive()
	if len(m.notes) != 0 {
		t.Fatal("note not archived")
	}
	entry := m.archives[0]
	if _, err := os.Stat(richSidecar(entry.To)); err != nil {
		t.Fatal(err)
	}
	m.undoArchive()
	data, err := os.ReadFile(richSidecar(path))
	if err != nil || string(data) != "rich content" {
		t.Fatal("rich content lost", err)
	}
}
func TestFolderAndResizeNavigation(t *testing.T) {
	m := testModel(t)
	m.startPrompt("folder", m.config.NotesDir, "Notebook")
	m.confirmPrompt()
	m.newNote()
	path := m.selectedPath()
	m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("h")})
	if len(m.notes) != 1 {
		t.Fatal("folder not collapsed")
	}
	m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("l")})
	if len(m.notes) != 2 || m.notes[1].path != path {
		t.Fatal("folder did not expand")
	}
	m.startPrompt("rename", m.notes[0].path, "../escape")
	m.confirmPrompt()
	if m.prompt == "" {
		t.Fatal("unsafe name accepted")
	}
}
func TestLongLibraryScrollAndFocus(t *testing.T) {
	m := testModel(t)
	for i := 0; i < 50; i++ {
		m.newNote()
	}
	m.resize(80, 24)
	if m.sidebarOffset == 0 {
		t.Fatal("selection not scrolled into view")
	}
	before := m.viewport.Width
	m.Update(tea.KeyMsg{Type: tea.KeyTab})
	if m.viewport.Width <= before {
		t.Fatal("focus did not resize preview")
	}
}
func TestEditorArguments(t *testing.T) {
	args, err := splitCommand("'/path with spaces/editor' --wait \"two words\"")
	if err != nil || len(args) != 3 || args[0] != "/path with spaces/editor" || args[2] != "two words" {
		t.Fatal(args, err)
	}
	if _, err = splitCommand("editor '"); err == nil {
		t.Fatal("bad quote accepted")
	}
}
func TestNoHiddenFilesAndSymlinkTraversal(t *testing.T) {
	m := testModel(t)
	os.WriteFile(filepath.Join(m.config.NotesDir, ".hidden.md"), []byte("secret"), 0600)
	os.Symlink(t.TempDir(), filepath.Join(m.config.NotesDir, "linked"))
	m.reload("")
	if len(m.notes) != 0 {
		t.Fatal("read hidden or linked files")
	}
	if strings.Contains(safeText("\x1b[31mtext"), "\x1b") {
		t.Fatal("terminal escape retained")
	}
}

func TestNewNotebookIsAtLibraryRoot(t *testing.T) {
	m := testModel(t)
	m.startPrompt("folder", m.config.NotesDir, "Existing")
	m.confirmPrompt()
	m.newNote()
	m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("N")})
	if m.promptPath != m.config.NotesDir {
		t.Fatal("notebook nested in current folder")
	}
	m.input.SetValue("Research")
	m.confirmPrompt()
	folder := m.selectedPath()
	if filepath.Dir(folder) != m.config.NotesDir {
		t.Fatal(folder)
	}
	m.newNote()
	if filepath.Dir(m.selectedPath()) != folder {
		t.Fatal("note created outside notebook")
	}
}
func TestThemesPersistAndFit(t *testing.T) {
	m := testModel(t)
	m.newNote()
	m.resize(120, 36)
	for range themeOrder {
		m.cycleTheme()
		v := m.View()
		if strings.Contains(v, "✍") {
			t.Fatal("hand emoji remains")
		}
		if lipgloss.Width(v) > 120 || lipgloss.Height(v) > 36 {
			t.Fatal("theme changes layout")
		}
		saved, err := os.ReadFile(filepath.Join(m.config.NotesDir, ".samatha-theme"))
		if err != nil || string(saved) != m.config.Theme {
			t.Fatal("theme not saved")
		}
		restored, err := newModel(&Config{NotesDir: m.config.NotesDir, SidebarWidth: 30, Theme: "light"})
		if err != nil || restored.config.Theme != m.config.Theme {
			t.Fatal("theme not restored")
		}
	}
}

// Replay SGR background changes as a terminal would. Every painted cell,
// including whitespace following nested resets, must have an explicit background.
func assertOpaqueFrame(t *testing.T, frame string) {
	t.Helper()
	pattern := regexp.MustCompile("\x1b\\[[0-9;]*m")
	positions := pattern.FindAllStringIndex(frame, -1)
	background := "default"
	offset := 0
	check := func(text string) {
		if strings.Trim(text, "\r\n") != "" && background == "default" {
			t.Fatalf("terminal-default background paints %q", text)
		}
	}
	for _, position := range positions {
		check(frame[offset:position[0]])
		codes := strings.Split(frame[position[0]+2:position[1]-1], ";")
		for i := 0; i < len(codes); i++ {
			code, _ := strconv.Atoi(codes[i])
			switch {
			case code == 0 || code == 49:
				background = "default"
			case code == 38 || code == 48 || code == 58:
				if i+1 >= len(codes) {
					continue
				}
				count := 0
				if codes[i+1] == "2" {
					count = 4
				} else if codes[i+1] == "5" {
					count = 2
				}
				if code == 48 {
					background = strings.Join(codes[i:min(i+count+1, len(codes))], ";")
				}
				i += count
			case code >= 40 && code <= 47 || code >= 100 && code <= 107:
				background = codes[i]
			}
		}
		offset = position[1]
	}
	check(frame[offset:])
}
func TestThemePaintsWhitespaceAfterMarkdownAndHeaderResets(t *testing.T) {
	lipgloss.SetColorProfile(termenv.TrueColor)
	m := testModel(t)
	m.newNote()
	for _, theme := range themeOrder {
		m.config.Theme = theme
		m.applyTheme()
		for _, sidebar := range []bool{true, false} {
			m.showSidebar = sidebar
			m.resize(120, 30)
			for _, mode := range []string{"normal", "help", "prompt"} {
				t.Run(theme+"/"+strconv.FormatBool(sidebar)+"/"+mode, func(t *testing.T) {
					m.prompt = ""
					m.help = mode == "help"
					if mode == "prompt" {
						m.startPrompt("folder", m.config.NotesDir, "Notebook")
					}
					assertOpaqueFrame(t, m.View())
				})
			}
		}
	}
	// RGB zero/39/49 components must never be interpreted as SGR reset commands.
	explicit := "\x1b[48;2;0;49;39mcolored\x1b[49m blank\x1b[m reset"
	fixed := m.themedFrame(explicit)
	if !strings.Contains(fixed, "\x1b[48;2;0;49;39mcolored") {
		t.Fatal("explicit background changed")
	}
	assertOpaqueFrame(t, fixed)
}
