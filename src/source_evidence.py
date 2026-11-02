"""Bounded read of public job details. No proxies, private hosts or redirects."""
import ipaddress
import socket
import time
from urllib.parse import urlsplit
import requests
from bs4 import BeautifulSoup


def fetch_public_text(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.port not in (None, 443)):
        raise ValueError('Unsupported job source URL')
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('Job source is not public')
    # One bounded request, no redirect chasing, no challenges or rate-limit retries.
    time.sleep(2)
    with requests.get(url, timeout=(5, 15), allow_redirects=False, stream=True,
                      headers={'User-Agent': 'EU-Job-Requirements-Checker/1.0'}) as resp:
        resp.raise_for_status()
        if resp.status_code != 200 or 'text/html' not in resp.headers.get('Content-Type', ''):
            raise ValueError('Job detail is unavailable')
        data = bytearray()
        for chunk in resp.iter_content(65536):
            data.extend(chunk)
            if len(data) > 1500000:
                raise ValueError('Job detail too large')
    soup = BeautifulSoup(bytes(data), 'html.parser')
    for node in soup(['script', 'style', 'noscript']):
        node.decompose()
    return soup.get_text(' ', strip=True)
