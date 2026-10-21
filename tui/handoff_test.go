package main

import (
	"bytes"
	"io"
	"path/filepath"
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"
)

func TestHandoffRetainsFrameUntilCompleteBrowserRepaint(t *testing.T) {
	var output bytes.Buffer
	screen := &screenHandoff{out: &output}
	write := func(value string) {
		t.Helper()
		if _, err := io.WriteString(screen, value); err != nil {
			t.Fatal(err)
		}
	}
	write("original browser frame")
	screen.Hold()
	for _, value := range []string{ansi.EraseEntireLine, "\r", ansi.DisableAltScreenBuffer, ansi.EnableAltScreenBuffer, ansi.EraseEntireScreen, "stale frame"} {
		write(value)
	}
	if output.String() != "original browser frame" {
		t.Fatal("handoff exposed terminal resets")
	}
	screen.Resume()
	write("partial frame queued before repaint")
	if output.String() != "original browser frame" {
		t.Fatal("partial frame released editor too early")
	}
	write(ansi.EraseEntireScreen)
	write(ansi.CursorOrigin)
	if output.String() != "original browser frame" {
		t.Fatal("screen cleared before repaint")
	}
	frame := "\x1b[48;2;25;25;35mfull browser frame"
	write(frame)
	result := strings.TrimPrefix(output.String(), "original browser frame")
	if !strings.HasPrefix(result, "\x1b[?2026h") || !strings.HasSuffix(result, "\x1b[?2026l") {
		t.Fatal("repaint was not atomic")
	}
	if !strings.Contains(result, "a=d,d=I,i=42") || !strings.Contains(result, frame) {
		t.Fatal("graphic not replaced by browser")
	}
	if strings.Contains(result, ansi.EraseEntireScreen) || strings.Contains(result, ansi.DisableAltScreenBuffer) {
		t.Fatal("repaint cleared the terminal")
	}
	output.Reset()
	write(ansi.DisableAltScreenBuffer)
	if output.String() != ansi.DisableAltScreenBuffer {
		t.Fatal("normal exit cannot restore shell")
	}
}

func TestHandoffCleanupRestoresShellAfterFailure(t *testing.T) {
	var output bytes.Buffer
	screen := &screenHandoff{out: &output}
	screen.Hold()
	screen.Close()
	if !strings.Contains(output.String(), ansi.DisableAltScreenBuffer) || !strings.Contains(output.String(), "\x1b[?25h") {
		t.Fatal("terminal not restored after failed handoff")
	}
}

func TestOnlyBuiltInEditorUsesSharedScreen(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("SAMATHA_APP_DIR", dir)
	if !builtInEditor([]string{"/usr/bin/python3", filepath.Join(dir, "rich_editor.py")}) {
		t.Fatal("built-in editor not recognized")
	}
	if builtInEditor([]string{"nano"}) || builtInEditor([]string{"python3", filepath.Join(t.TempDir(), "rich_editor.py")}) {
		t.Fatal("external editor intercepted")
	}
}
