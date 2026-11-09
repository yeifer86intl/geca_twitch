"""Twitch Helix API client: app token, paginación, límites y errores."""
import os
import random
import time
import requests

class TwitchAPI:
    def __init__(self, session=None):
        self.client_id = os.environ.get('TWITCH_CLIENT_ID','').strip()
        self.secret = os.environ.get('TWITCH_CLIENT_SECRET','').strip()
        if not self.client_id or not self.secret:
            raise RuntimeError('Configura TWITCH_CLIENT_ID y TWITCH_CLIENT_SECRET en Secrets/entorno')
        self.s = session or requests.Session()
        self.token = None
        self._auth()

    def _auth(self):
        r = self.s.post('https://id.twitch.tv/oauth2/token', data={
            'client_id':self.client_id, 'client_secret':self.secret,
            'grant_type':'client_credentials'}, timeout=30)
        r.raise_for_status()
        self.token = r.json()['access_token']

    def get(self, endpoint, params=None):
        url = 'https://api.twitch.tv/helix' + endpoint
        for attempt in range(7):
            try:
                r = self.s.get(url, params=params, headers={
                    'Client-Id': self.client_id,
                    'Authorization': 'Bearer ' + self.token}, timeout=45)
            except requests.RequestException:
                if attempt == 6: raise
                time.sleep(min(2**attempt + random.random(), 30))
                continue
            if r.status_code == 401 and attempt == 0:
                self._auth()
                continue
            if r.status_code == 429 or 500 <= r.status_code <= 599:
                if attempt == 6: r.raise_for_status()
                wait = r.headers.get('Ratelimit-Reset') if r.status_code == 429 else None
                try:
                    delay = max(1.0, min(float(wait) - time.time(), 65)) if wait else min(2**attempt+random.random(), 45)
                except ValueError:
                    delay = min(2**attempt+random.random(), 45)
                time.sleep(delay)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError('Se agotaron los reintentos de Helix')

    def pages(self, endpoint, params, max_rows=None):
        """Devuelve (filas, exhaustivo). False si se alcanzó el límite de filas."""
        rows, cursor, seen = [], None, set()
        while max_rows is None or len(rows) < max_rows:
            p = dict(params)
            p['first'] = 100 if max_rows is None else min(100, max_rows-len(rows))
            if cursor: p['after'] = cursor
            body = self.get(endpoint, p)
            batch = body.get('data', [])
            rows.extend(batch)
            new_cursor = body.get('pagination',{}).get('cursor')
            if not new_cursor or not batch:
                return rows, True
            if new_cursor in seen:
                raise RuntimeError(f'Cursor repetido en {endpoint}')
            seen.add(new_cursor)
            cursor = new_cursor
        return rows, False
