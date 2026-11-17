"""Bounded monitor heartbeat transport; never forward secrets on redirects."""
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def ping(url, healthy):
    target = url.rstrip('/') + ('' if healthy else '/fail')
    request = Request(target, data=b'', method='POST')
    with build_opener(NoRedirect).open(request, timeout=5) as response:
        if response.status != 200 or response.read(128).strip() != b'OK':
            raise RuntimeError('Heartbeat not acknowledged')
