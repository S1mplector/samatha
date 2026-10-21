package main

import (
	"github.com/charmbracelet/x/ansi"
	"image"
	"image/color"
	"image/png"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestImagePreviewAndClippedScrolling(t *testing.T) {
	t.Setenv("KITTY_WINDOW_ID", "test")
	t.Setenv("SSH_CONNECTION", "")
	folder := t.TempDir()
	os.Mkdir(filepath.Join(folder, "images"), 0700)
	path := filepath.Join(folder, "images", "saved.png")
	file, _ := os.Create(path)
	img := image.NewRGBA(image.Rect(0, 0, 320, 240))
	img.Set(0, 0, color.RGBA{255, 0, 0, 255})
	png.Encode(file, img)
	file.Close()
	note := filepath.Join(folder, "Note.md")
	os.WriteFile(note, []byte("# Image note\n\n![A useful detail](images/saved.png)\n\n---\n\nAfter\n"), 0600)
	m, err := newModel(&Config{NotesDir: folder, Theme: "dark", SidebarWidth: 30})
	if err != nil {
		t.Fatal(err)
	}
	if len(m.notes) != 1 || m.notes[0].isDir {
		t.Fatal("attachment folder leaked into note tree")
	}
	m.resize(100, 20)
	frame := m.View()
	if !strings.Contains(frame, "t=f,") || !strings.Contains(frame, "U=1") || !strings.Contains(frame, "\U0010eeee") {
		t.Fatal("inline image missing")
	}
	if strings.Contains(ansi.Strip(frame), "images/saved.png") || strings.Contains(frame, "SIMG000") {
		t.Fatal("image rendered as a link/token")
	}
	upload := m.previewGraphics
	m.viewport.LineDown(5)
	if !strings.Contains(m.View(), "\U0010eeee") || m.previewGraphics != upload {
		t.Fatal("scrolling rebuilt or lost image")
	}
	m.previewText += strings.Repeat("\n\nMore context", 40) + "\n\n![Second image](images/saved.png)\n"
	m.renderPreview()
	m.viewport.GotoTop()
	if strings.Count(m.View(), "a=T,") != 1 {
		t.Fatal("offscreen image uploaded")
	}
	m.viewport.GotoBottom()
	if strings.Count(m.View(), "a=T,") != 1 || !strings.Contains(m.previewGraphics, "i=5456129,U=1") {
		t.Fatal("scrolling did not load the newly visible image")
	}
	m.resize(70, 20)
	if !strings.Contains(m.View(), "U=1") {
		t.Fatal("resize lost image")
	}
	t.Setenv("SSH_CONNECTION", "remote")
	m.renderPreview()
	m.viewport.GotoTop()
	if strings.Contains(m.View(), "U=1") || !strings.Contains(m.View(), "saved.png") {
		t.Fatal("remote fallback missing")
	}
}

func TestPlaceholderCellsHaveStableWidth(t *testing.T) {
	for _, line := range strings.Split(imageCells(previewImageBase, 40, 20), "\n") {
		if got := ansi.StringWidth(line); got != 42 {
			t.Fatalf("placeholder line width %d", got)
		}
	}
}
