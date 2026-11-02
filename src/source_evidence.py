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


def fetch_job_details(record):
    """Use EURES' real detail response, otherwise the full public HTML page.

    Structured required/desired fields are kept separate; availableLanguages
    never becomes a spoken-language requirement.
    """
    if record.get('source') != 'eures':
        return fetch_public_text(record.get('url', '')), {}
    source_id = record.get('source_id', '')
    if not source_id or not all(c.isalnum() or c in '_=-' for c in source_id):
        raise ValueError('Invalid EURES source identifier')
    endpoint = 'https://europa.eu/eures/api/jv-searchengine/public/jv/id/' + source_id
    time.sleep(2)
    with requests.get(endpoint, timeout=(5,15), allow_redirects=False, stream=True) as resp:
        resp.raise_for_status()
        if resp.status_code != 200 or 'application/json' not in resp.headers.get('Content-Type',''):
            raise ValueError('EURES detail unavailable')
        data = bytearray()
        for chunk in resp.iter_content(65536):
            data.extend(chunk)
            if len(data) > 1500000:
                raise ValueError('EURES detail too large')
    import json
    payload = json.loads(data)
    profiles = payload.get('jvProfiles') or {}
    if not profiles:
        raise ValueError('No EURES detail profile')
    profile = profiles.get('en') or next(iter(profiles.values()))
    text = ' '.join([profile.get('title') or '', BeautifulSoup(profile.get('description') or '', 'html.parser').get_text(' ',strip=True)])
    # Extra fields are not flattened into description or accidentally interpreted
    # as positive sponsorship; the extractor handles their semantics separately.
    return text, profile
