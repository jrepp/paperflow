"""Bounded public source retrieval for visible curation candidates."""
from html.parser import HTMLParser
import ipaddress
import socket
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

from paperflow_domain import ContentRevision


def public_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Content requires a public HTTP(S) URL')
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError('Content address is not public')


class PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url):
    public_url(url)
    request = Request(url, headers={'User-Agent': 'Paperflow/0.1 (research content extraction)'})
    with build_opener(PublicRedirect()).open(request, timeout=20) as response:
        body = response.read(8 * 1024 * 1024 + 1)
    if len(body) > 8 * 1024 * 1024:
        raise ValueError('Source exceeds content size limit')
    return body


class ArticleText(HTMLParser):
    """Keep semantic article/main content, excluding navigation and executable text."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.found = False

    def handle_starttag(self, tag, attrs):
        if tag in ('br', 'hr', 'img', 'meta', 'link', 'input', 'source', 'wbr', 'area', 'base', 'embed', 'param', 'col'):
            if tag == 'br':
                self.parts.append('\n')
            return
        self.stack.append(tag)
        if tag in ('article', 'main'):
            self.found = True
        if tag in ('p', 'div', 'section', 'li', 'h1', 'h2', 'h3', 'h4'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in self.stack:
            self.stack = self.stack[:len(self.stack) - 1 - self.stack[::-1].index(tag)]
        if tag in ('p', 'div', 'section', 'li', 'h1', 'h2', 'h3', 'h4'):
            self.parts.append('\n')

    def handle_data(self, data):
        if any(t in self.stack for t in ('article', 'main')) and not any(t in self.stack for t in ('script', 'style', 'nav', 'footer', 'header', 'aside')):
            self.parts.append(data)


def source_revision(item, url, *, get=fetch):
    """Prefer arXiv paper HTML; a missing/unreadable source leaves the original intact."""
    parsed = urlsplit(url)
    if parsed.hostname in ('arxiv.org', 'www.arxiv.org') and parsed.path.startswith('/abs/'):
        url = 'https://arxiv.org/html/' + parsed.path.removeprefix('/abs/')
    try:
        body = get(url)
    except (OSError, ValueError):
        if parsed.hostname not in ('arxiv.org', 'www.arxiv.org') or not parsed.path.startswith('/abs/'):
            raise
        url = 'https://arxiv.org/pdf/' + parsed.path.removeprefix('/abs/')
        body = get(url)
    if body.startswith(b'%PDF-'):
        import pymupdf
        with pymupdf.open(stream=body, filetype='pdf') as doc:
            if len(doc) > 100:
                raise ValueError('Paper exceeds extraction page limit')
            text = '\n\n'.join(page.get_text() for page in doc)
        basis = 'full_text'
    else:
        parser = ArticleText()
        parser.feed(body.decode('utf-8', errors='replace'))
        text = '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())
        # Semantic article markup does not establish complete/paywall-free text.
        basis = 'excerpt'
        if urlsplit(url).hostname == 'arxiv.org' and 'ltx_document' in body.decode('utf-8', errors='replace'):
            basis = 'full_text'
    if len(text) < 500 or len(text) > 400000:
        raise ValueError('Source has insufficient or excessive readable text')
    return ContentRevision.from_markdown(item_id=item.id, markdown=text,
        processor_version='public-content-v1', evidence_basis=basis)
