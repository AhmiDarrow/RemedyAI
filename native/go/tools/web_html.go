package tools

import (
	"fmt"
	"html"
	"regexp"
	"strings"

	"golang.org/x/text/encoding/htmlindex"
)

// HTML to markdown extract, ported from src/remedy/core/html_extract.py. The
// module has no golang.org/x/net, so a small tolerant tokenizer stands in for
// Python's html.parser.HTMLParser: it never fails, and anything it cannot read
// as markup is text.

var webDropTags = map[string]bool{
	"script": true, "style": true, "noscript": true, "svg": true, "canvas": true,
	"iframe": true, "template": true, "form": true, "nav": true, "footer": true,
	"aside": true, "header": true, "button": true, "select": true, "textarea": true,
}

var webHeadingTags = map[string]string{
	"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######",
}

var webJSHints = []string{
	"enable javascript", "enable js", "requires javascript", "turn on javascript",
	"please enable cookies", "checking your browser", "just a moment", "cf-browser-verification",
}

var (
	webWSRe       = regexp.MustCompile(`[ \t\f\v]+`)
	webNLRe       = regexp.MustCompile(`\n{3,}`)
	webHTMLHeadRe = regexp.MustCompile(`(?is)^\s*(<!doctype\s+html|<html\b)`)
	webTagRe      = regexp.MustCompile(`<[^>]+>`)
)

// decodeCharset is bytes.decode(charset, errors="replace"). Unknown labels
// fall back to UTF-8 instead of failing the fetch.
func decodeCharset(raw []byte, charset string) string {
	cs := strings.ToLower(strings.TrimSpace(charset))
	if cs == "" || cs == "utf-8" || cs == "utf8" {
		return decodeReplace(raw)
	}
	enc, err := htmlindex.Get(cs)
	if err != nil {
		return decodeReplace(raw)
	}
	out, err := enc.NewDecoder().Bytes(raw)
	if err != nil {
		return decodeReplace(raw)
	}
	return decodeReplace(out)
}

func looksLikeHTML(raw []byte) bool {
	sample := raw
	if len(sample) > 800 {
		sample = sample[:800]
	}
	s := strings.ToValidUTF8(string(sample), "")
	low := strings.ToLower(s)
	if len(low) > 800 {
		low = low[:800]
	}
	return webHTMLHeadRe.MatchString(s) || strings.Contains(low, "<body")
}

// stripTags is _strip_tags: tags to spaces, unescape, collapse whitespace.
func stripTags(fragment string) string {
	t := html.UnescapeString(webTagRe.ReplaceAllString(fragment, " "))
	return strings.Join(strings.Fields(t), " ")
}

type htmlAttr struct{ key, val string }

// htmlHandler receives the tokenizer's events, like HTMLParser's handle_*.
type htmlHandler interface {
	start(tag string, attrs []htmlAttr)
	end(tag string)
	data(text string)
}

func isASCIILetter(c byte) bool { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') }

// tokenizeHTML walks s and reports tags and text. Comments, doctypes and
// processing instructions are dropped; script/style bodies are raw text;
// <x/> is a start and an end; an unterminated tag at EOF is text.
func tokenizeHTML(s string, h htmlHandler) {
	var text strings.Builder
	flush := func() {
		if text.Len() > 0 {
			h.data(html.UnescapeString(text.String()))
			text.Reset()
		}
	}
	i, n := 0, len(s)
	for i < n {
		lt := strings.IndexByte(s[i:], '<')
		if lt < 0 {
			text.WriteString(s[i:])
			break
		}
		text.WriteString(s[i : i+lt])
		i += lt
		rest := s[i:]
		switch {
		case strings.HasPrefix(rest, "<!--"):
			flush()
			if j := strings.Index(rest[4:], "-->"); j >= 0 {
				i += 4 + j + 3
			} else {
				i = n
			}
		case strings.HasPrefix(rest, "<!") || strings.HasPrefix(rest, "<?"):
			flush()
			if j := strings.IndexByte(rest, '>'); j >= 0 {
				i += j + 1
			} else {
				i = n
			}
		case strings.HasPrefix(rest, "</"):
			if len(rest) > 2 && isASCIILetter(rest[2]) {
				j := strings.IndexByte(rest, '>')
				if j < 0 {
					text.WriteString(rest)
					i = n
					continue
				}
				flush()
				name := rest[2:j]
				if k := strings.IndexAny(name, " \t\n\r\f/"); k >= 0 {
					name = name[:k]
				}
				h.end(strings.ToLower(name))
				i += j + 1
			} else if j := strings.IndexByte(rest, '>'); j >= 0 {
				// "</>" or "</ 3": bogus comment, dropped.
				flush()
				i += j + 1
			} else {
				text.WriteString(rest)
				i = n
			}
		case len(rest) > 1 && isASCIILetter(rest[1]):
			name, attrs, selfClose, size := parseStartTag(rest)
			if size < 0 {
				text.WriteString(rest)
				i = n
				continue
			}
			flush()
			i += size
			h.start(name, attrs)
			if selfClose {
				h.end(name)
				continue
			}
			if name == "script" || name == "style" {
				low := strings.ToLower(s[i:])
				j := strings.Index(low, "</"+name)
				if j < 0 {
					h.data(s[i:])
					i = n
					continue
				}
				if j > 0 {
					h.data(s[i : i+j])
				}
				i += j
			}
		default:
			text.WriteByte('<')
			i++
		}
	}
	flush()
}

// parseStartTag reads "<name attrs...>" at the start of s. size is the
// bytes consumed, or -1 when there is no closing '>'.
func parseStartTag(s string) (string, []htmlAttr, bool, int) {
	i := 1
	for i < len(s) && !strings.ContainsRune(" \t\n\r\f/>\x00", rune(s[i])) {
		i++
	}
	name := strings.ToLower(s[1:i])
	var attrs []htmlAttr
	selfClose := false
	for i < len(s) {
		c := s[i]
		switch {
		case c == '>':
			return name, attrs, selfClose, i + 1
		case c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f':
			i++
		case c == '/':
			selfClose = i+1 < len(s) && s[i+1] == '>'
			i++
		default:
			selfClose = false
			k := i
			for i < len(s) && !strings.ContainsRune(" \t\n\r\f/>=", rune(s[i])) {
				i++
			}
			key := strings.ToLower(s[k:i])
			val := ""
			for i < len(s) && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r' || s[i] == '\f') {
				i++
			}
			if i < len(s) && s[i] == '=' {
				i++
				for i < len(s) && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r' || s[i] == '\f') {
					i++
				}
				if i < len(s) && (s[i] == '"' || s[i] == '\'') {
					q := s[i]
					j := strings.IndexByte(s[i+1:], q)
					if j < 0 {
						return "", nil, false, -1
					}
					val = s[i+1 : i+1+j]
					i += j + 2
				} else {
					k := i
					for i < len(s) && !strings.ContainsRune(" \t\n\r\f>", rune(s[i])) {
						i++
					}
					val = s[k:i]
				}
			}
			if key != "" {
				attrs = append(attrs, htmlAttr{key: key, val: html.UnescapeString(val)})
			}
		}
	}
	return "", nil, false, -1
}

// mdExtractor is _ExtractParser.
type mdExtractor struct {
	skip, inPre, inCode, inA, inTitle int
	aHref                             string
	aText                             []string
	parts                             []string
	title                             string
	titleBits                         []string
}

func (p *mdExtractor) start(t string, attrs []htmlAttr) {
	if webDropTags[t] {
		p.skip++
		return
	}
	if p.skip > 0 {
		return
	}
	switch {
	case t == "title":
		p.inTitle++
	case t == "br":
		p.parts = append(p.parts, "\n")
	case t == "hr":
		p.parts = append(p.parts, "\n---\n")
	case webHeadingTags[t] != "":
		p.parts = append(p.parts, "\n"+webHeadingTags[t]+" ")
	case t == "li":
		p.parts = append(p.parts, "\n- ")
	case t == "blockquote":
		p.parts = append(p.parts, "\n> ")
	case t == "pre":
		p.inPre++
		p.parts = append(p.parts, "\n```\n")
	case t == "code" && p.inPre == 0:
		p.inCode++
		p.parts = append(p.parts, "`")
	case t == "a":
		href := ""
		for _, a := range attrs {
			if a.key == "href" && a.val != "" {
				href = strings.TrimSpace(a.val)
				break
			}
		}
		p.inA++
		p.aHref = href
		p.aText = nil
	case t == "p":
		p.parts = append(p.parts, "\n\n")
	}
}

func (p *mdExtractor) end(t string) {
	if webDropTags[t] {
		if p.skip > 0 {
			p.skip--
		}
		return
	}
	if p.skip > 0 {
		return
	}
	switch {
	case t == "title":
		if p.inTitle > 0 {
			p.inTitle--
		}
		if p.title == "" {
			p.title = strings.TrimSpace(strings.Join(p.titleBits, ""))
		}
	case webHeadingTags[t] != "" || t == "p" || t == "li" || t == "blockquote" || t == "tr":
		p.parts = append(p.parts, "\n")
	case t == "pre" && p.inPre > 0:
		p.inPre--
		p.parts = append(p.parts, "\n```\n")
	case t == "code" && p.inCode > 0:
		p.inCode--
		p.parts = append(p.parts, "`")
	case t == "a" && p.inA > 0:
		p.inA--
		label := strings.TrimSpace(strings.Join(p.aText, ""))
		href := p.aHref
		if label != "" && href != "" && (strings.HasPrefix(href, "http://") || strings.HasPrefix(href, "https://") || strings.HasPrefix(href, "/")) {
			p.parts = append(p.parts, "["+label+"]("+href+")")
		} else if label != "" {
			p.parts = append(p.parts, label)
		}
		p.aHref = ""
		p.aText = nil
	}
}

func (p *mdExtractor) data(d string) {
	switch {
	case p.skip > 0:
	case p.inTitle > 0:
		p.titleBits = append(p.titleBits, d)
	case p.inA > 0:
		p.aText = append(p.aText, d)
	case p.inPre > 0:
		p.parts = append(p.parts, d)
	default:
		if text := webWSRe.ReplaceAllString(d, " "); strings.TrimSpace(text) != "" {
			p.parts = append(p.parts, text)
		}
	}
}

func (p *mdExtractor) text() string {
	raw := strings.Join(p.parts, "")
	raw = strings.ReplaceAll(strings.ReplaceAll(raw, "\r\n", "\n"), "\r", "\n")
	return strings.TrimSpace(webNLRe.ReplaceAllString(raw, "\n\n"))
}

type htmlExtract struct {
	title    string
	markdown string
	jsShell  bool
}

func webTruncMarker(maxChars int) string {
	return fmt.Sprintf("\n\n…[truncated at %d chars]", maxChars)
}

// htmlToMarkdown is html_to_markdown. Lengths count characters (runes), as
// Python does.
func htmlToMarkdown(src string, maxChars int) htmlExtract {
	p := &mdExtractor{}
	tokenizeHTML(src, p)
	md := p.text()
	if p.title != "" && !strings.HasPrefix(strings.ToLower(md), "# ") {
		md = strings.TrimSpace("# " + p.title + "\n\n" + md)
	}
	js := looksJSShell(md, src)
	if r := []rune(md); len(r) > maxChars {
		md = string(r[:maxChars]) + webTruncMarker(maxChars)
	}
	return htmlExtract{title: p.title, markdown: md, jsShell: js}
}

func looksJSShell(extracted, src string) bool {
	body := strings.ToLower(strings.TrimSpace(extracted))
	for _, h := range webJSHints {
		if strings.Contains(body, h) {
			return true
		}
	}
	head := []rune(strings.ToLower(src))
	if len(head) > 4000 {
		head = head[:4000]
	}
	bodyLen := len([]rune(body))
	for _, h := range webJSHints {
		if strings.Contains(string(head), h) && bodyLen < 400 {
			return true
		}
	}
	return len([]rune(src)) >= 4000 && bodyLen < 240
}
