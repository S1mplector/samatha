package main

import (
	"encoding/base64"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"github.com/charmbracelet/x/ansi"
	"github.com/charmbracelet/x/term"
)

// screenHandoff lets Bubble Tea release input without exposing the shell screen.
// The built-in editor writes directly to the same TTY and owns the visible frame
// until the browser has a complete replacement ready. External editors retain
// Bubble Tea's normal terminal lifecycle.
type screenHandoff struct {
	out                            io.Writer
	terminal                       *os.File
	mu                             sync.Mutex
	held, waiting, resumeRequested bool
}

// Bubble Tea requires the complete term.File interface to detect terminal size
// and subscribe to resize events; Fd alone leaves it at the fallback dimensions.
var _ term.File = (*screenHandoff)(nil)

func (s *screenHandoff) Fd() uintptr                   { return s.terminal.Fd() }
func (s *screenHandoff) Read(data []byte) (int, error) { return s.terminal.Read(data) }

func (s *screenHandoff) SetMode(mode string) {
	if os.Getenv("KITTY_WINDOW_ID") == "" {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	value := ""
	if mode != "" {
		value = "=" + base64.StdEncoding.EncodeToString([]byte(mode))
	}
	_, _ = io.WriteString(s.out, "\x1b]1337;SetUserVar=samatha"+value+"\x07")
}

func (s *screenHandoff) Hold() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.held = true
	s.waiting = false
	s.resumeRequested = false
}

func (s *screenHandoff) Resume() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.held {
		s.resumeRequested = true
	}
}

func (s *screenHandoff) Write(data []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.held {
		return s.out.Write(data)
	}
	// Arm only when ClearScreen executes under the renderer's lock, immediately
	// before it invalidates its previous frame. A queued partial render cannot
	// release the retained editor image ahead of the full repaint.
	if s.resumeRequested && string(data) == ansi.EraseEntireScreen {
		s.waiting, s.resumeRequested = true, false
		return len(data), nil
	}
	// Suppress release/restore controls, clears, and stale browser renders.
	if !s.waiting || strings.TrimSpace(ansi.Strip(string(data))) == "" {
		return len(data), nil
	}
	// ClearScreen forces Bubble Tea to repaint every row; its separate clear is
	// suppressed above. Remove the editor graphic only inside that complete frame.
	frame := "\x1b[?2026h\x1b_Ga=d,d=I,i=42,q=2\x1b\\" +
		"\x1b[?1016l\x1b[?1002h\x1b[?1006h\x1b[?2004h\x1b[?25l\x1b[H" +
		string(data) + "\x1b[?2026l"
	if _, err := io.WriteString(s.out, frame); err != nil {
		return 0, err
	}
	s.held, s.waiting = false, false
	return len(data), nil
}

func (s *screenHandoff) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	// Also restore the user's screen if the program fails during a handoff.
	if s.held {
		_, err := io.WriteString(s.out, "\x1b_Ga=d,d=I,i=42,q=2\x1b\\\x1b[?1016l\x1b[?1002l\x1b[?1006l\x1b[?2004l\x1b[?25h\x1b[?1049l")
		return err
	}
	// The descriptor belongs to the caller (usually stdout), not this wrapper.
	return nil
}

func builtInEditor(args []string) bool {
	dir := os.Getenv("SAMATHA_APP_DIR")
	if dir == "" {
		executable, err := os.Executable()
		if err != nil {
			return false
		}
		dir = filepath.Join(filepath.Dir(executable), "..")
	}
	want, err := filepath.Abs(filepath.Join(dir, "rich_editor.py"))
	if err != nil {
		return false
	}
	for _, arg := range args {
		path, err := filepath.Abs(arg)
		if err == nil && path == want {
			return true
		}
	}
	return false
}
