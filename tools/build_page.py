"""Assembles ../index.html from tools/template.html + data/players.json + data/headshots.webp.

Run after tools/build_data.py:   python tools/build_page.py
"""
import base64, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')


def read(path, mode='r'):
    with open(os.path.join(ROOT, path), mode) as f:
        return f.read()


html = read('tools/template.html')
players = read('data/players.json')
sprite = base64.b64encode(read('data/headshots.webp', 'rb')).decode()

assert '__PLAYERS__' in html and '__SPRITE__' in html
html = html.replace('__PLAYERS__', players).replace('__SPRITE__', sprite)

with open(os.path.join(ROOT, 'index.html'), 'w') as f:
    f.write(html)
print('index.html', round(len(html) / 1024), 'KB')
