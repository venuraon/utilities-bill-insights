"""Inline the trusted email template's small static CSS subset, never model HTML.

This supports the class/tag descendant selectors used in email.css. Responsive
rules remain in the viewer; standalone emails also carry inline base styles.
SVG viewBox casing and every calculated chart attribute are preserved verbatim.
"""
import html
import re
from html.parser import HTMLParser
from pathlib import Path

BASE = '''
.sample-note{padding:10px 18px;margin:0 auto 12px;background:#f3ebda;color:#7a5b2c;font:11px Arial,sans-serif;max-width:740px}
.email-sheet{font-family:Arial,sans-serif;color:#21392f;background:#ffffff;border:1px solid #e2e7dd;border-radius:8px;overflow:hidden;margin:0 auto;line-height:1.6}
.mail-meta{background:#fafbf7;border-bottom:1px solid #e2e7dd;display:flex;flex-direction:column;color:#737d72}
.mail-meta strong{font-weight:500;color:#21392f}
.eyebrow{font-size:10px;letter-spacing:1.6px;font-weight:700;color:#737d72}
'''


def inline_email_styles(source):
    css = BASE + (Path(__file__).resolve().parents[1] / 'web' / 'email.css').read_text().split('@media')[0]
    rules = [(selector.strip(), body.strip().replace('var(--line)', '#e2e7dd'))
             for selectors, body in re.findall(r'([^{}]+)\{([^{}]*)\}', css)
             for selector in selectors.split(',')]

    def simple_matches(selector, item):
        parts = selector.split('.')
        return (not parts[0] or item[0] == parts[0]) and all(c in item[1] for c in parts[1:])

    def matches(selector, path):
        tokens = selector.replace('>', ' ').split()
        if not tokens or not simple_matches(tokens[-1], path[-1]):
            return False
        index = len(path) - 2
        for token in reversed(tokens[:-1]):
            while index >= 0 and not simple_matches(token, path[index]):
                index -= 1
            if index < 0:
                return False
            index -= 1
        return True

    class Inliner(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.path, self.output = [], []

        def start(self, tag, attrs, closed=False):
            item = (tag, dict(attrs).get('class', '').split())
            path = self.path + [item]
            styles = [body for selector, body in rules if matches(selector, path)]
            raw = self.get_starttag_text()
            if styles:
                existing = dict(attrs).get('style', '')
                value = ';'.join([*styles, existing])
                raw = re.sub(r'\sstyle="[^"]*"', '', raw)
                end = '/>' if raw.endswith('/>') else '>'
                raw = raw[:-len(end)] + f' style="{html.escape(value, quote=True)}"' + end
            self.output.append(raw)
            if not closed and tag not in {'br', 'hr', 'img', 'meta', 'input', 'link'}:
                self.path.append(item)

        def handle_starttag(self, tag, attrs):
            self.start(tag, attrs)

        def handle_startendtag(self, tag, attrs):
            self.start(tag, attrs, closed=True)

        def handle_endtag(self, tag):
            self.output.append(f'</{tag}>')
            if self.path:
                self.path.pop()

        def handle_data(self, data):
            self.output.append(data)

        def handle_entityref(self, name):
            self.output.append(f'&{name};')

        def handle_charref(self, name):
            self.output.append(f'&#{name};')

    parser = Inliner()
    parser.feed(source)
    return ''.join(parser.output)
