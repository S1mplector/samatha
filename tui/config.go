package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	yaml "gopkg.in/yaml.v3"
)

type Config struct {
	NotesDir     string `yaml:"notes_dir"`
	Editor       string `yaml:"editor"`
	SidebarWidth int    `yaml:"sidebar_width"`
	Theme        string `yaml:"theme"`
}

func configPath() string {
	dir, err := os.UserConfigDir()
	if err != nil {
		dir = "."
	}
	return filepath.Join(dir, "samatha", "config.yaml")
}

func defaultDataDir() string {
	if dir := os.Getenv("XDG_DATA_HOME"); filepath.IsAbs(dir) {
		return filepath.Join(dir, "samatha")
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".local", "share", "samatha")
}

func loadConfig() (*Config, error) {
	cfg := &Config{NotesDir: filepath.Join(defaultDataDir(), "notes"), SidebarWidth: 30, Theme: "light"}
	data, err := os.ReadFile(configPath())
	if err != nil && !os.IsNotExist(err) {
		return nil, err
	}
	if err == nil {
		if err = yaml.Unmarshal(data, cfg); err != nil {
			return nil, fmt.Errorf("read config: %w", err)
		}
	}
	if cfg.SidebarWidth < 12 {
		cfg.SidebarWidth = 12
	}
	if cfg.SidebarWidth > 60 {
		cfg.SidebarWidth = 60
	}
	if _, ok := themes[cfg.Theme]; !ok {
		return nil, fmt.Errorf("unknown color theme")
	}
	return cfg, nil
}

// Parse editor arguments without invoking a shell.
func splitCommand(command string) ([]string, error) {
	var result []string
	var word strings.Builder
	var quote rune
	escaped, started := false, false
	for _, c := range command {
		if escaped {
			word.WriteRune(c)
			escaped = false
			started = true
			continue
		}
		if c == '\\' && quote != '\'' {
			escaped = true
			started = true
			continue
		}
		if quote != 0 {
			if c == quote {
				quote = 0
			} else {
				word.WriteRune(c)
			}
			continue
		}
		if c == '\'' || c == '"' {
			quote = c
			started = true
			continue
		}
		if c == ' ' || c == '\t' {
			if started {
				result = append(result, word.String())
				word.Reset()
				started = false
			}
			continue
		}
		word.WriteRune(c)
		started = true
	}
	if escaped || quote != 0 {
		return nil, fmt.Errorf("unclosed quote or escape in editor command")
	}
	if started {
		result = append(result, word.String())
	}
	if len(result) == 0 || result[0] == "" {
		return nil, fmt.Errorf("empty editor command")
	}
	return result, nil
}

func (c *Config) editorArgs() ([]string, error) {
	for _, candidate := range []string{c.Editor, os.Getenv("SAMATHA_EDITOR"), os.Getenv("VISUAL"), os.Getenv("EDITOR")} {
		if candidate != "" {
			return splitCommand(candidate)
		}
	}
	for _, candidate := range []string{"nano", "nvim", "vim", "vi"} {
		if path, err := exec.LookPath(candidate); err == nil {
			return []string{path}, nil
		}
	}
	return nil, fmt.Errorf("no editor found; set SAMATHA_EDITOR or install nano")
}
