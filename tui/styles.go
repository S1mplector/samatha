// Layout adapted from armandsauzay/note (see LICENSE.note).
package main

import (
	_ "embed"
	"encoding/json"
	"github.com/charmbracelet/glamour"
	glamansi "github.com/charmbracelet/glamour/ansi"
	"github.com/charmbracelet/lipgloss"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

//go:embed themes.json
var themeJSON []byte

type Palette struct {
	Name, Bg, Text, Muted, Accent, Panel string
	Dark                                 bool
}

var themeOrder = []string{"light", "dark", "nord", "dracula", "catppuccin", "solarized", "rose-pine"}
var themes = func() map[string]Palette { var p map[string]Palette; _ = json.Unmarshal(themeJSON, &p); return p }()
var accent = lipgloss.NewStyle()
var muted = lipgloss.NewStyle()
var border = lipgloss.NewStyle()

func (m *Model) palette() Palette {
	p, ok := themes[m.config.Theme]
	if !ok {
		return themes["light"]
	}
	return p
}
func (m *Model) applyTheme() {
	p := m.palette()
	bg := lipgloss.Color("#" + p.Bg)
	accent = lipgloss.NewStyle().Foreground(lipgloss.Color("#" + p.Accent)).Background(bg)
	muted = lipgloss.NewStyle().Foreground(lipgloss.Color("#" + p.Muted)).Background(bg)
	border = lipgloss.NewStyle().Border(lipgloss.RoundedBorder()).BorderForeground(lipgloss.Color("#" + p.Accent)).BorderBackground(bg).Background(bg).Foreground(lipgloss.Color("#" + p.Text))
	m.input.Cursor.Style = accent
	m.input.PromptStyle = accent
	m.input.TextStyle = lipgloss.NewStyle().Foreground(lipgloss.Color("#" + p.Text)).Background(bg)
}
func (m *Model) readTheme() {
	if data, err := os.ReadFile(filepath.Join(m.config.NotesDir, ".samatha-theme")); err == nil {
		key := strings.TrimSpace(string(data))
		if _, ok := themes[key]; ok {
			m.config.Theme = key
		}
	}
	m.applyTheme()
}
func (m *Model) cycleTheme() {
	index := 0
	for i, key := range themeOrder {
		if key == m.config.Theme {
			index = i
			break
		}
	}
	key := themeOrder[(index+1)%len(themeOrder)]
	if err := os.WriteFile(filepath.Join(m.config.NotesDir, ".samatha-theme"), []byte(key), 0600); err != nil {
		m.message = err.Error()
		return
	}
	m.config.Theme = key
	m.applyTheme()
	m.resize(m.width, m.height)
	m.message = m.palette().Name
}
func (m *Model) markdownStyle() glamansi.StyleConfig {
	p := m.palette()
	style := glamour.LightStyleConfig
	if p.Dark {
		style = glamour.DarkStyleConfig
	}
	fg, bg, ac := "#"+p.Text, "#"+p.Bg, "#"+p.Accent
	style.Document.Color = &fg
	style.Document.BackgroundColor = &bg
	style.Heading.Color = &ac
	style.H1.Color = &bg
	style.H1.BackgroundColor = &ac
	style.H2.Color = &ac
	style.H3.Color = &ac
	style.Link.Color = &ac
	style.LinkText.Color = &ac
	style.Code.Color = &ac
	panel := "#" + p.Panel
	style.Code.BackgroundColor = &panel
	return style
}
func header(width int) string {
	return lipgloss.NewStyle().Width(width).Align(lipgloss.Center).Bold(true).Render(accent.Render("samatha"))
}

var sgrSequence = regexp.MustCompile(`\x1b\[[0-9;]*m`)

// Nested Markdown and Lip Gloss styles reset to the terminal's default colors.
// Rebase those resets to the theme so padding and blank lines stay opaque.
// Explicit heading/code colors and non-color attributes remain unchanged.
func (m *Model) themedFrame(frame string) string {
	p := m.palette()
	color := func(prefix, hex string) string {
		parts := []string{prefix, "2"}
		for i := 0; i < 6; i += 2 {
			n, _ := strconv.ParseUint(hex[i:i+2], 16, 8)
			parts = append(parts, strconv.Itoa(int(n)))
		}
		return strings.Join(parts, ";")
	}
	fg, bg := color("38", p.Text), color("48", p.Bg)
	base := "\x1b[" + fg + ";" + bg + "m"
	frame = sgrSequence.ReplaceAllStringFunc(frame, func(sequence string) string {
		codes := strings.Split(sequence[2:len(sequence)-1], ";")
		var result []string
		for i := 0; i < len(codes); i++ {
			switch codes[i] {
			case "", "0":
				result = append(result, "0", fg, bg)
			case "39":
				result = append(result, fg)
			case "49":
				result = append(result, bg)
			case "38", "48", "58":
				// Zeros inside RGB values are color components, not resets.
				end := i
				if i+1 < len(codes) {
					if codes[i+1] == "2" {
						end = min(i+4, len(codes)-1)
					}
					if codes[i+1] == "5" {
						end = min(i+2, len(codes)-1)
					}
				}
				result = append(result, codes[i:end+1]...)
				i = end
			default:
				result = append(result, codes[i])
			}
		}
		return "\x1b[" + strings.Join(result, ";") + "m"
	})
	return base + strings.ReplaceAll(frame, "\n", "\n"+base)
}
