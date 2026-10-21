package main

// Kitty's Unicode placeholders travel with viewport text, so image clipping and
// scrolling use the same layout as paragraphs rather than a separate overlay.
import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"image"
	_ "image/png"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"golang.org/x/sys/unix"
)

const previewImageBase = 0x534100

type previewImage struct {
	token, upload string
	start, rows   int
}

var imageLine = regexp.MustCompile(`^!\[(.*?)\]\((images/[^)]+\.png)\)$`)

// The first 32 entries of Kitty's published rowcolumn-diacritics table.
// Only the first cell needs a row: subsequent columns inherit and increment.
var imageRows = []rune{0x0305, 0x030d, 0x030e, 0x0310, 0x0312, 0x033d, 0x033e, 0x033f, 0x0346, 0x034a, 0x034b, 0x034c, 0x0350, 0x0351, 0x0352, 0x0357, 0x035b, 0x0363, 0x0364, 0x0365, 0x0366, 0x0367, 0x0368, 0x0369, 0x036a, 0x036b, 0x036c, 0x036d, 0x036e, 0x036f, 0x0483, 0x0484}

func imageCells(id, columns, rows int) string {
	var lines []string
	for row := 0; row < rows; row++ {
		lines = append(lines, fmt.Sprintf("  \x1b[38;2;%d;%d;%dm\x1b[59m", id>>16&255, id>>8&255, id&255)+
			"\U0010eeee"+string(imageRows[row])+strings.Repeat("\U0010eeee", columns-1)+"\x1b[39m")
	}
	return strings.Join(lines, "\n")
}

func (m *Model) imagePreview(content string) (string, map[string]string) {
	replacements := map[string]string{}
	m.previewGraphics = ""
	m.previewImages = nil
	// File transfer is local to the terminal's machine. Other terminals retain
	// the ordinary Markdown image link rather than an invisible placeholder.
	if os.Getenv("KITTY_WINDOW_ID") == "" || os.Getenv("SSH_CONNECTION") != "" || m.previewPath == "" {
		return content, replacements
	}
	cw, ch := 10.0, 20.0
	if size, err := unix.IoctlGetWinsize(int(os.Stdout.Fd()), unix.TIOCGWINSZ); err == nil && size.Col > 0 && size.Row > 0 && size.Xpixel > 0 && size.Ypixel > 0 {
		cw = float64(size.Xpixel) / float64(size.Col)
		ch = float64(size.Ypixel) / float64(size.Row)
	}
	widths := map[string]int{}
	var rich struct {
		Document struct {
			Blocks []struct {
				Path  string
				Width int
			}
		}
	}
	if raw, err := os.ReadFile(richSidecar(m.previewPath)); err == nil && json.Unmarshal(raw, &rich) == nil {
		for _, block := range rich.Document.Blocks {
			if block.Width > 0 {
				widths[block.Path] = block.Width
			}
		}
	}
	folder := filepath.Dir(m.previewPath)
	lines := strings.Split(content, "\n")
	count := 0
	for i, line := range lines {
		match := imageLine.FindStringSubmatch(strings.TrimSpace(line))
		if match == nil {
			continue
		}
		rel := filepath.Clean(match[2])
		if filepath.Dir(rel) != "images" {
			continue
		}
		path := filepath.Join(folder, rel)
		real, err := filepath.EvalSymlinks(path)
		if err != nil || real != path {
			continue
		}
		file, err := os.Open(path)
		if err != nil {
			continue
		}
		config, format, err := image.DecodeConfig(file)
		file.Close()
		if err != nil || format != "png" || config.Width < 1 || config.Height < 1 || int64(config.Width)*int64(config.Height) > 4096*4096 {
			continue
		}
		width := math.Min(float64(config.Width), 320*float64(config.Width)/float64(config.Height))
		if saved := widths[rel]; saved > 0 {
			width = float64(saved)
		}
		width = math.Min(width, float64(max(1, m.viewport.Width-4))*cw)
		rows := max(1, int(math.Ceil(width*float64(config.Height)/float64(config.Width)/ch)))
		if rows > len(imageRows) {
			width *= float64(len(imageRows)) / float64(rows)
			rows = len(imageRows)
		}
		columns := max(1, min(m.viewport.Width-4, int(math.Ceil(width/cw))))
		id := previewImageBase + count
		token := fmt.Sprintf("SIMG%04d", count)
		replacements[token] = imageCells(id, columns, rows)
		if caption := strings.TrimSpace(match[1]); caption != "" && caption != "Pasted image" && caption != "Image" {
			if rendered, err := m.renderer.Render(caption); err == nil {
				replacements[token] += "\n" + strings.Trim(rendered, "\n")
			}
		}
		lines[i] = token
		count++
		// t=f sends only a filename; PNG bytes are read once by Kitty, never
		// re-encoded or sent through the PTY on each scroll event.
		upload := fmt.Sprintf("\x1b_Ga=T,f=100,t=f,i=%d,U=1,c=%d,r=%d,q=2;%s\x1b\\", id, columns, rows, base64.StdEncoding.EncodeToString([]byte(path)))
		m.previewImages = append(m.previewImages, &previewImage{token: token, upload: upload, start: -1, rows: rows})
	}
	m.previewImageCount = max(m.previewImageCount, count)
	return strings.Join(lines, "\n"), replacements
}

func (m *Model) visibleImageGraphics() string {
	var cleanup, uploads strings.Builder
	for i := 0; i < m.previewImageCount; i++ {
		fmt.Fprintf(&cleanup, "\x1b_Ga=d,d=I,i=%d,q=2\x1b\\", previewImageBase+i)
	}
	if !m.help {
		for _, image := range m.previewImages {
			if image.start >= 0 && image.start < m.viewport.YOffset+m.viewport.Height && image.start+image.rows > m.viewport.YOffset {
				uploads.WriteString(image.upload)
			}
		}
	}
	m.previewGraphics = cleanup.String() + uploads.String()
	if m.screen != nil {
		m.screen.SetImageCleanup(cleanup.String())
	}
	return m.previewGraphics
}

func (m *Model) replaceImageTokens(content string, replacements map[string]string) string {
	lines := strings.Split(content, "\n")
	lineNumber := 0
	for i, line := range lines {
		for token, cells := range replacements {
			if strings.Contains(line, token) {
				lines[i] = cells
				for _, image := range m.previewImages {
					if image.token == token {
						image.start = lineNumber
						break
					}
				}
				break
			}
		}
		lineNumber += strings.Count(lines[i], "\n") + 1
	}
	return strings.Join(lines, "\n")
}
