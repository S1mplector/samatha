// Inline library prompts; storage updates never take over the terminal.
package main

import (
	tea "github.com/charmbracelet/bubbletea"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

func (m *Model) startPrompt(kind, path, value string) tea.Cmd {
	m.prompt = kind
	m.promptPath = path
	m.message = ""
	m.input.SetValue(value)
	m.input.CursorEnd()
	return m.input.Focus()
}
func validName(value string) bool {
	return value != "" && value != "." && value != ".." && !strings.HasPrefix(value, ".") &&
		!strings.ContainsAny(value, "/\\\x00\n\r\t") && safeText(value) == value
}
func (m *Model) confirmPrompt() {
	value := strings.TrimSpace(m.input.Value())
	if m.prompt == "title" {
		if value == "" || labelText(value) != value {
			m.message = "Enter a title without control characters."
			return
		}
		appDir := os.Getenv("SAMATHA_APP_DIR")
		if appDir == "" {
			executable, err := os.Executable()
			if err != nil {
				m.message = err.Error()
				return
			}
			appDir = filepath.Join(filepath.Dir(executable), "..")
		}
		// A storage-only helper: never hand the terminal over to an editor.
		command := exec.Command("/usr/bin/python3", filepath.Join(appDir, "rich_storage.py"), "--rename", m.promptPath, "--", value)
		output, err := command.CombinedOutput()
		if err != nil {
			m.message = "Could not rename: " + strings.TrimSpace(string(output))
			if len(output) == 0 {
				m.message += err.Error()
			}
			return
		}
		offset := m.viewport.YOffset
		m.prompt = ""
		m.input.Blur()
		m.reload(m.promptPath)
		m.viewport.SetYOffset(offset)
		m.message = "Renamed"
		if len(output) > 0 {
			m.message += " • " + strings.TrimSpace(string(output))
		}
		return
	}
	if !validName(value) {
		m.message = "Use a name without slashes, control characters, or a leading dot."
		return
	}
	parent := m.promptPath
	if m.prompt == "rename" {
		parent = filepath.Dir(m.promptPath)
	}
	path := filepath.Join(parent, value)
	if m.prompt == "rename" && path == m.promptPath {
		m.prompt = ""
		m.input.Blur()
		return
	}
	if _, err := os.Lstat(path); !os.IsNotExist(err) {
		m.message = "That name already exists or is unavailable."
		return
	}
	var err error
	if m.prompt == "folder" {
		err = os.Mkdir(path, 0700)
	} else {
		err = os.Rename(m.promptPath, path)
	}
	if err != nil {
		m.message = err.Error()
		return
	}
	if m.prompt == "rename" {
		for old, expanded := range m.expanded {
			if old == m.promptPath || strings.HasPrefix(old, m.promptPath+string(os.PathSeparator)) {
				m.expanded[path+strings.TrimPrefix(old, m.promptPath)] = expanded
				delete(m.expanded, old)
			}
		}
		if m.previewPath == m.promptPath || strings.HasPrefix(m.previewPath, m.promptPath+string(os.PathSeparator)) {
			m.previewPath = path + strings.TrimPrefix(m.previewPath, m.promptPath)
		}
	}
	m.expanded[parent] = true
	m.prompt = ""
	m.input.Blur()
	m.message = ""
	m.reload(path)
}
