// Samatha's terminal interface, adapted from armandsauzay/note.
// The original project's MIT license is in LICENSE.note.
package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"time"
	"unicode"

	"github.com/charmbracelet/bubbles/textinput"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/glamour"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
	"github.com/muesli/termenv"
	"golang.org/x/term"
)

type Note struct {
	path, title string
	isDir       bool
	depth       int
}
type archiveEntry struct{ From, To string }

func richSidecar(path string) string {
	return filepath.Join(filepath.Dir(path), "."+filepath.Base(path)+".samatha.json")
}
func moveNote(from, to string) error {
	info, err := os.Stat(from)
	if err != nil {
		return err
	}
	if err = os.Rename(from, to); err != nil {
		return err
	}
	if !info.IsDir() {
		if _, sideErr := os.Stat(richSidecar(from)); sideErr == nil {
			if err = os.Rename(richSidecar(from), richSidecar(to)); err != nil {
				if rollbackErr := os.Rename(to, from); rollbackErr != nil {
					return fmt.Errorf("rich data move failed: %v; note remains at %s", err, to)
				}
				return err
			}
		}
	}
	return nil
}

type sessionState struct {
	Selected string
	Expanded map[string]bool
}
type editorDone struct{ err error }
type Model struct {
	config                                  *Config
	notes                                   []Note
	cursor, sidebarOffset                   int
	expanded                                map[string]bool
	viewport                                viewport.Model
	showSidebar                             bool
	width, height, sidebarOuter, bodyHeight int
	renderer                                *glamour.TermRenderer
	previewPath, previewText                string
	input                                   textinput.Model
	prompt, promptPath                      string
	message                                 string
	help                                    bool
	archives                                []archiveEntry
	screen                                  *screenHandoff
	editing                                 bool
}

func safeText(text string) string {
	return strings.Map(func(r rune) rune {
		if r == '\n' || r == '\t' || !unicode.IsControl(r) {
			return r
		}
		return -1
	}, text)
}
func labelText(s string) string {
	return strings.ReplaceAll(strings.ReplaceAll(safeText(s), "\n", " "), "\t", " ")
}
func extractTitle(content string) string {
	for _, line := range strings.Split(content, "\n") {
		if strings.HasPrefix(line, "# ") {
			return strings.TrimSpace(strings.TrimPrefix(line, "# "))
		}
	}
	return ""
}
func (m *Model) selectedPath() string {
	if m.cursor >= 0 && m.cursor < len(m.notes) {
		return m.notes[m.cursor].path
	}
	return ""
}
func (m *Model) refresh(selected string) error {
	var notes []Note
	var walk func(string, int) error
	walk = func(dir string, depth int) error {
		entries, err := os.ReadDir(dir)
		if err != nil {
			return err
		}
		// Keep the reference's alphabetical directory/file ordering.
		sort.Slice(entries, func(i, j int) bool { return strings.ToLower(entries[i].Name()) < strings.ToLower(entries[j].Name()) })
		for _, entry := range entries {
			if strings.HasPrefix(entry.Name(), ".") || entry.Type()&os.ModeSymlink != 0 {
				continue
			}
			path := filepath.Join(dir, entry.Name())
			if entry.IsDir() {
				notes = append(notes, Note{path: path, title: entry.Name(), isDir: true, depth: depth})
				if m.expanded[path] {
					if err := walk(path, depth+1); err != nil {
						return err
					}
				}
			} else if strings.EqualFold(filepath.Ext(path), ".md") {
				data, err := os.ReadFile(path)
				if err != nil {
					return err
				}
				title := extractTitle(string(data))
				if title == "" {
					title = strings.TrimSuffix(entry.Name(), filepath.Ext(path))
				}
				notes = append(notes, Note{path: path, title: title, depth: depth})
			}
		}
		return nil
	}
	if err := walk(m.config.NotesDir, 0); err != nil {
		return err
	}
	m.notes = notes
	for i, n := range notes {
		if n.path == selected {
			m.cursor = i
			break
		}
	}
	m.cursor = max(0, min(m.cursor, len(notes)-1))
	m.ensureVisible()
	return nil
}
func newModel(cfg *Config) (*Model, error) {
	if err := os.MkdirAll(cfg.NotesDir, 0700); err != nil {
		return nil, err
	}
	input := textinput.New()
	input.CharLimit = 120
	input.Prompt = "› "
	input.Cursor.Style = accent
	input.PromptStyle = accent
	m := &Model{config: cfg, expanded: map[string]bool{}, showSidebar: true, input: input, viewport: viewport.New(80, 24)}
	m.readTheme()
	var state sessionState
	if data, err := os.ReadFile(filepath.Join(cfg.NotesDir, ".samatha-state.json")); err == nil {
		_ = json.Unmarshal(data, &state)
	}
	if state.Expanded != nil {
		m.expanded = state.Expanded
	}
	if err := m.refresh(state.Selected); err != nil {
		return nil, err
	}
	// Open the first notebook on first launch so imported pages are immediately visible.
	if state.Selected == "" && len(m.notes) > 0 && m.notes[0].isDir {
		m.expanded[m.notes[0].path] = true
		if err := m.refresh(""); err != nil {
			return nil, err
		}
		if len(m.notes) > 1 && !m.notes[1].isDir {
			m.cursor = 1
		}
	}
	m.resize(120, 36)
	m.updatePreview()
	return m, nil
}
func (m *Model) saveState() error {
	data, err := json.MarshalIndent(sessionState{m.selectedPath(), m.expanded}, "", "  ")
	if err != nil {
		return err
	}
	tmp, err := os.CreateTemp(m.config.NotesDir, ".state-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if _, err = tmp.Write(data); err == nil {
		err = tmp.Sync()
	}
	closeErr := tmp.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	return os.Rename(tmp.Name(), filepath.Join(m.config.NotesDir, ".samatha-state.json"))
}
func (m *Model) resize(w, h int) {
	m.width = max(1, w)
	m.height = max(1, h)
	m.bodyHeight = max(3, h-4)
	m.sidebarOuter = 0
	if m.showSidebar && w >= 60 {
		m.sidebarOuter = min(m.config.SidebarWidth+6, w/2)
	}
	contentOuter := max(4, w-m.sidebarOuter)
	if m.sidebarOuter > 0 {
		contentOuter--
	}
	m.viewport.Width = max(1, contentOuter-2)
	m.viewport.Height = max(1, m.bodyHeight-2)
	m.input.Width = max(1, w-24)
	m.renderer, _ = glamour.NewTermRenderer(glamour.WithStyles(m.markdownStyle()), glamour.WithWordWrap(max(10, m.viewport.Width-4)))
	offset := m.viewport.YOffset
	m.renderPreview()
	m.viewport.SetYOffset(offset)
	m.ensureVisible()
}
func (m *Model) renderPreview() {
	content := safeText(m.previewText)
	if m.renderer != nil {
		if rendered, err := m.renderer.Render(content); err == nil {
			content = rendered
		}
	}
	m.viewport.SetContent(content)
}
func (m *Model) updatePreview() {
	if len(m.notes) == 0 {
		m.previewPath = ""
		m.previewText = "No notes found. Press 'n' to create one."
	} else {
		n := m.notes[m.cursor]
		if n.isDir {
			m.previewPath = ""
			m.previewText = "# " + n.title + "\n\n"
			entries, err := os.ReadDir(n.path)
			if err != nil {
				m.message = err.Error()
				return
			}
			count := 0
			for _, entry := range entries {
				if strings.HasPrefix(entry.Name(), ".") || entry.Type()&os.ModeSymlink != 0 {
					continue
				}
				if entry.IsDir() && entry.Name() != "images" {
					m.previewText += "- " + entry.Name() + "/\n"
					count++
				}
				if strings.EqualFold(filepath.Ext(entry.Name()), ".md") {
					title := strings.TrimSuffix(entry.Name(), filepath.Ext(entry.Name()))
					if content, err := os.ReadFile(filepath.Join(n.path, entry.Name())); err == nil {
						if t := extractTitle(string(content)); t != "" {
							title = t
						}
					}
					m.previewText += "- " + title + "\n"
					count++
				}
			}
			if count == 0 {
				m.previewText += "No notes"
			}
		} else {
			data, err := os.ReadFile(n.path)
			if err != nil {
				m.message = "Could not read note: " + err.Error()
				return
			}
			m.previewPath = n.path
			m.previewText = string(data)
		}
	}
	m.renderPreview()
	m.viewport.GotoTop()
}
func (m *Model) ensureVisible() {
	rows := max(1, m.bodyHeight-4)
	if m.cursor < m.sidebarOffset {
		m.sidebarOffset = m.cursor
	}
	if m.cursor >= m.sidebarOffset+rows {
		m.sidebarOffset = m.cursor - rows + 1
	}
	m.sidebarOffset = max(0, min(m.sidebarOffset, max(0, len(m.notes)-rows)))
}
func (m *Model) currentDirectory() string {
	if len(m.notes) == 0 {
		return m.config.NotesDir
	}
	n := m.notes[m.cursor]
	if n.isDir {
		return n.path
	}
	return filepath.Dir(n.path)
}
func (m *Model) reload(path string) {
	if err := m.refresh(path); err != nil {
		m.message = err.Error()
		return
	}
	m.updatePreview()
}
func (m *Model) newNote() {
	dir := m.currentDirectory()
	name := "note-" + time.Now().Format("2006-01-02-150405.000000000") + ".md"
	path := filepath.Join(dir, name)
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		m.message = err.Error()
		return
	}
	_, err = fmt.Fprint(file, "# New Note\n\n")
	if err == nil {
		err = file.Sync()
	}
	closeErr := file.Close()
	if err != nil {
		m.message = err.Error()
		return
	}
	if closeErr != nil {
		m.message = closeErr.Error()
		return
	}
	m.expanded[dir] = true
	m.reload(path)
	m.message = "Note created • enter to edit"
}
func (m *Model) archive() {
	path := m.selectedPath()
	if path == "" {
		return
	}
	dir := filepath.Join(m.config.NotesDir, ".archive")
	if err := os.MkdirAll(dir, 0700); err != nil {
		m.message = err.Error()
		return
	}
	dest := filepath.Join(dir, time.Now().Format("2006-01-02-150405.000000000")+"-"+filepath.Base(path))
	if err := moveNote(path, dest); err != nil {
		m.message = err.Error()
		return
	}
	m.archives = append(m.archives, archiveEntry{path, dest})
	m.cursor = max(0, m.cursor-1)
	m.reload("")
	m.message = "Archived • u to restore"
}
func (m *Model) undoArchive() {
	if len(m.archives) == 0 {
		m.message = "No archive to undo in this session."
		return
	}
	entry := m.archives[len(m.archives)-1]
	if _, err := os.Lstat(entry.From); !os.IsNotExist(err) {
		m.message = "Cannot restore: the original name is in use."
		return
	}
	if err := os.MkdirAll(filepath.Dir(entry.From), 0700); err != nil {
		m.message = err.Error()
		return
	}
	if err := moveNote(entry.To, entry.From); err != nil {
		m.message = err.Error()
		return
	}
	m.archives = m.archives[:len(m.archives)-1]
	m.expanded[filepath.Dir(entry.From)] = true
	m.reload(entry.From)
	m.message = "Restored"
}
func (m *Model) edit() tea.Cmd {
	if len(m.notes) == 0 {
		return nil
	}
	n := m.notes[m.cursor]
	if n.isDir {
		return m.startPrompt("rename", n.path, n.title)
	}
	args, err := m.config.editorArgs()
	if err != nil {
		m.message = err.Error()
		return nil
	}
	if err := m.saveState(); err != nil {
		m.message = err.Error()
		return nil
	}
	command := exec.Command(args[0], append(args[1:], n.path)...)
	command.Env = append(os.Environ(), "SAMATHA_THEME="+m.config.Theme, "SAMATHA_NOTES_DIR="+m.config.NotesDir, fmt.Sprintf("SAMATHA_SIDEBAR_WIDTH=%d", m.config.SidebarWidth))
	var diagnostics bytes.Buffer
	if m.screen != nil && builtInEditor(args) {
		m.screen.Hold()
		command.Env = append(command.Env, "SAMATHA_SHARED_SCREEN=1")
		// Keep stdout a real TTY for pixel dimensions and direct graphics output.
		command.Stdout = os.Stdout
		command.Stderr = &diagnostics
	} else if m.screen != nil {
		m.screen.SetMode("")
	}
	m.editing = true
	return tea.ExecProcess(command, func(err error) tea.Msg {
		if err != nil && diagnostics.Len() > 0 {
			err = fmt.Errorf("%s", strings.TrimSpace(diagnostics.String()))
		}
		return editorDone{err}
	})
}
func (m *Model) Init() tea.Cmd { return nil }
func (m *Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.resize(msg.Width, msg.Height)
		return m, nil
	case editorDone:
		m.editing = false
		m.readTheme()
		m.resize(m.width, m.height)
		selected := m.selectedPath()
		var state sessionState
		if data, err := os.ReadFile(filepath.Join(m.config.NotesDir, ".samatha-state.json")); err == nil {
			if json.Unmarshal(data, &state) == nil && state.Selected != "" {
				selected = state.Selected
			}
		}
		m.reload(selected)
		if msg.err != nil {
			m.message = "Editor: " + msg.err.Error()
		} else {
			m.message = ""
		}
		if m.screen != nil {
			m.screen.SetMode("browser")
			m.screen.Resume()
		}
		return m, tea.ClearScreen
	case tea.MouseMsg:
		if m.editing {
			return m, nil
		}
		if m.prompt != "" || m.help {
			return m, nil
		}
		if msg.Action == tea.MouseActionPress && msg.Button == tea.MouseButtonLeft && m.sidebarOuter > 0 && msg.X < m.sidebarOuter && msg.Y >= 4 && msg.Y < m.height-3 {
			i := m.sidebarOffset + msg.Y - 4
			if i < len(m.notes) {
				m.cursor = i
				m.ensureVisible()
				m.updatePreview()
			}
			return m, nil
		}
		var cmd tea.Cmd
		m.viewport, cmd = m.viewport.Update(msg)
		return m, cmd
	case tea.KeyMsg:
		if m.editing {
			return m, nil
		}
		key := msg.String()
		if m.prompt != "" {
			switch key {
			case "esc":
				m.prompt = ""
				m.input.Blur()
				m.message = ""
				return m, nil
			case "enter":
				m.confirmPrompt()
				return m, nil
			case "ctrl+c":
				m.prompt = ""
				m.input.Blur()
				return m, nil
			}
			var cmd tea.Cmd
			m.input, cmd = m.input.Update(msg)
			return m, cmd
		}
		if m.help {
			if key == "esc" || key == "?" || key == "q" {
				m.help = false
				return m, nil
			}
			if key != "ctrl+c" {
				return m, nil
			}
		}
		m.message = ""
		switch key {
		case "q", "ctrl+c", "ctrl+q":
			if err := m.saveState(); err != nil {
				m.message = "Could not save view state: " + err.Error()
				return m, nil
			}
			return m, tea.Quit
		case "t":
			m.cycleTheme()
			return m, nil
		case "?":
			m.help = true
			return m, nil
		case "tab":
			m.showSidebar = !m.showSidebar
			m.resize(m.width, m.height)
			return m, nil
		case "up", "k", "down", "j":
			if !m.showSidebar || m.sidebarOuter == 0 {
				if key == "up" || key == "k" {
					m.viewport.LineUp(1)
				} else {
					m.viewport.LineDown(1)
				}
			} else {
				if key == "up" || key == "k" {
					m.cursor = max(0, m.cursor-1)
				} else {
					m.cursor = min(max(0, len(m.notes)-1), m.cursor+1)
				}
				m.ensureVisible()
				m.updatePreview()
			}
			return m, nil
		case "home", "g":
			if !m.showSidebar {
				m.viewport.GotoTop()
			} else {
				m.cursor = 0
				m.ensureVisible()
				m.updatePreview()
			}
			return m, nil
		case "end", "G":
			if !m.showSidebar {
				m.viewport.GotoBottom()
			} else {
				m.cursor = max(0, len(m.notes)-1)
				m.ensureVisible()
				m.updatePreview()
			}
			return m, nil
		case "enter":
			return m, m.edit()
		case "n":
			m.newNote()
			return m, nil
		case "N":
			return m, m.startPrompt("folder", m.config.NotesDir, "")
		case "f":
			return m, m.startPrompt("folder", m.currentDirectory(), "New Folder")
		case "r", "f2":
			if len(m.notes) > 0 {
				kind := "title"
				if m.notes[m.cursor].isDir {
					kind = "rename"
				}
				return m, m.startPrompt(kind, m.selectedPath(), m.notes[m.cursor].title)
			}
			return m, nil
		case "right", "l":
			if len(m.notes) > 0 && m.notes[m.cursor].isDir {
				path := m.selectedPath()
				m.expanded[path] = true
				m.reload(path)
			}
			return m, nil
		case "left", "h":
			if len(m.notes) > 0 {
				path := m.selectedPath()
				if !m.notes[m.cursor].isDir {
					path = filepath.Dir(path)
				}
				if path != m.config.NotesDir {
					m.expanded[path] = false
					m.reload(path)
				}
			}
			return m, nil
		case "backspace", "delete":
			m.archive()
			return m, nil
		case "u":
			m.undoArchive()
			return m, nil
		case "R":
			m.reload(m.selectedPath())
			return m, nil
		case "[":
			m.config.SidebarWidth = max(12, m.config.SidebarWidth-2)
			m.resize(m.width, m.height)
			return m, nil
		case "]":
			m.config.SidebarWidth = min(60, m.config.SidebarWidth+2)
			m.resize(m.width, m.height)
			return m, nil
		}
	}
	var cmd tea.Cmd
	if m.prompt != "" {
		m.input, cmd = m.input.Update(msg)
		return m, cmd
	}
	m.viewport, cmd = m.viewport.Update(msg)
	return m, cmd
}
func (m *Model) sidebar() string {
	var lines []string
	rows := max(1, m.bodyHeight-4)
	for i := m.sidebarOffset; i < len(m.notes) && len(lines) < rows; i++ {
		n := m.notes[i]
		icon := "└─ "
		if n.isDir {
			if m.expanded[n.path] {
				icon = "▼ "
			} else {
				icon = "▶ "
			}
		} else if i+1 < len(m.notes) && m.notes[i+1].depth >= n.depth {
			icon = "├─ "
		}
		line := ansi.Truncate(strings.Repeat("  ", n.depth)+icon+labelText(n.title), max(1, m.sidebarOuter-6), "…")
		if i == m.cursor {
			line = accent.Render(line)
		}
		lines = append(lines, line)
	}
	return border.Width(m.sidebarOuter-2).Height(m.bodyHeight-2).Padding(1, 2).Render(strings.Join(lines, "\n"))
}
func (m *Model) footer() string {
	if m.prompt != "" {
		title := "Folder name: "
		if m.prompt == "title" {
			title = "Note title: "
		}
		if m.prompt == "folder" && m.promptPath == m.config.NotesDir {
			title = "Notebook name: "
		}
		hint := "Enter to confirm • Esc to cancel • Ctrl+U to clear"
		if m.message != "" {
			hint = labelText(m.message)
		}
		return "  " + accent.Render(title) + m.input.View() + "\n" + muted.Render("  "+ansi.Truncate(hint, max(1, m.width-4), "…"))
	}
	count := 0
	for _, n := range m.notes {
		if !n.isDir {
			count++
		}
	}
	status := fmt.Sprintf("%d notes", count)
	if len(m.notes) > 0 {
		status = labelText(m.notes[m.cursor].title) + " • " + status
	}
	if m.message != "" {
		status = labelText(m.message)
	}
	help := "↑/k,↓/j: up/down • h/l: expand • enter: edit • n: new note • N: new notebook • backspace: archive • tab: sidebar • t: theme • q: quit"
	if m.width < 126 {
		help = "↑/k ↓/j move • h/l expand • enter edit • n note • N notebook • ⌫ archive • tab focus • t theme • ? help • q quit"
	}
	if m.width < 94 {
		help = "j/k move • enter edit • r rename • n new • tab focus • ? help • q quit"
	}
	if m.width >= 94 {
		help = strings.Replace(help, "enter edit", "enter edit • r rename", 1)
		help = strings.Replace(help, "enter: edit", "enter: edit • r: rename", 1)
	}
	line := func(s string) string { return muted.Render("  " + ansi.Truncate(s, max(1, m.width-4), "…")) }
	return line(status) + "\n" + line(help)
}
func (m *Model) View() string {
	if m.width < 30 || m.height < 10 {
		return ansi.Truncate("samatha • enlarge terminal", m.width, "") + "\n" + ansi.Truncate("q quit • notes are safe", m.width, "")
	}
	var body string
	if m.help {
		content := "Keyboard shortcuts\n\n" +
			"j/k or ↑/↓     Select a note\nh/l or ←/→     Collapse / expand folders\n" +
			"enter          Edit note / rename folder\nr / F2         Rename note or folder\nn / N          New note / new notebook\n" +
			"f              New subfolder\nbackspace      Archive selected note or folder\nu              Undo last archive\n" +
			"tab            Toggle sidebar / focus mode\npgup / pgdown  Scroll preview\n" +
			"ctrl++ / - / 0 Font size up / down / reset (Kitty)\n" +
			"t              Color theme\n[ / ]          Adjust sidebar width\nR              Refresh notes\nq / ctrl+c     Quit\n\n" +
			"Editor: save and exit to return to Samatha.\n? or esc       Close help"
		var lines []string
		for _, line := range strings.Split(content, "\n") {
			lines = append(lines, ansi.Truncate(line, m.width-6, "…"))
		}
		if len(lines) > m.bodyHeight-4 {
			lines = lines[:m.bodyHeight-4]
		}
		body = border.Width(m.width-2).Height(m.bodyHeight-2).Padding(1, 2).Render(strings.Join(lines, "\n"))
	} else {
		content := border.Width(m.viewport.Width).Height(m.viewport.Height).Render(m.viewport.View())
		if m.sidebarOuter > 0 {
			body = lipgloss.JoinHorizontal(lipgloss.Top, m.sidebar(), " ", content)
		} else {
			body = content
		}
	}
	p := m.palette()
	frame := lipgloss.NewStyle().Background(lipgloss.Color("#" + p.Bg)).Foreground(lipgloss.Color("#" + p.Text)).Width(m.width).Height(m.height).Render(header(m.width) + "\n\n" + body + "\n" + m.footer())
	return m.themedFrame(frame)
}
func run() error {
	cfg, err := loadConfig()
	if err != nil {
		return err
	}
	notes := flag.String("notes-dir", "", "Markdown notes directory")
	data := flag.String("data-dir", "", "Samatha data directory")
	render := flag.Bool("render", false, "Print one terminal frame (no interactive session)")
	width := flag.Int("width", 120, "Width used with --render")
	height := flag.Int("height", 36, "Height used with --render")
	version := flag.Bool("version", false, "Print version")
	config := flag.Bool("config", false, "Print configuration location and settings")
	flag.Parse()
	if *version {
		fmt.Println("Samatha 2.0 • note-style TUI")
		return nil
	}
	if *data != "" {
		cfg.NotesDir = filepath.Join(*data, "notes")
	}
	if *notes != "" {
		cfg.NotesDir = *notes
	}
	cfg.NotesDir, err = filepath.Abs(cfg.NotesDir)
	if err != nil {
		return err
	}
	if *config {
		fmt.Printf("%s\n\nnotes_dir: %s\nsidebar_width: %d\ntheme: %s\neditor: %s\n", configPath(), cfg.NotesDir, cfg.SidebarWidth, cfg.Theme, cfg.Editor)
		return nil
	}
	if !*render && !term.IsTerminal(int(os.Stdin.Fd())) {
		return fmt.Errorf("open Samatha from the app launcher or run samatha --here in a terminal")
	}
	if err := os.MkdirAll(cfg.NotesDir, 0700); err != nil {
		return err
	}
	lock, err := os.OpenFile(filepath.Join(cfg.NotesDir, ".samatha.lock"), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return err
	}
	defer lock.Close()
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		return fmt.Errorf("Samatha is already open for these notes")
	}
	lipgloss.SetColorProfile(termenv.TrueColor)
	model, err := newModel(cfg)
	if err != nil {
		return err
	}
	if *render {
		model.resize(*width, *height)
		fmt.Print(model.View() + "\x1b[0m\n")
		return nil
	}
	// Size the first frame before rendering; subsequent changes arrive through
	// Bubble Tea's WindowSizeMsg, including resizes while the editor is active.
	if w, h, err := term.GetSize(int(os.Stdout.Fd())); err == nil && w > 0 && h > 0 {
		model.resize(w, h)
	}
	screen := &screenHandoff{out: os.Stdout, terminal: os.Stdout}
	model.screen = screen
	screen.SetMode("browser")
	defer screen.SetMode("")
	p := tea.NewProgram(model, tea.WithOutput(screen), tea.WithAltScreen(), tea.WithMouseCellMotion())
	defer fmt.Print("\x1b[0m")
	defer screen.Close()
	_, err = p.Run()
	return err
}
func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "Samatha:", err)
		os.Exit(1)
	}
}
