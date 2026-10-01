"""
Builds the player dataset for the Hoops Draft Room.

Inputs (../data):
  espn_all.json    ESPN Fantasy 2026-27 projections + 2025-26 stats, ESPN position eligibility, headshot ids
  adp.json         FantasyPros consensus ADP (Yahoo + ESPN) for 2026-27
  bbref2026.json   Basketball-Reference 2025-26 per-game (used only for FGA/FTA volume and age)

Outputs:
  ../data/players.json    compact player records embedded into index.html
  ../data/headshots.webp  sprite sheet of ESPN headshots (SPRITE_COLS tiles wide)

Run:  python tools/build_data.py     (needs Pillow for the sprite)
"""
import io, json, os, re, sys, unicodedata, concurrent.futures, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, '..', 'data')
TILE, SPRITE_COLS = 72, 20

TEAMS = {
    'Atlanta Hawks': 'ATL', 'Boston Celtics': 'BOS', 'Brooklyn Nets': 'BKN', 'Charlotte Hornets': 'CHA',
    'Chicago Bulls': 'CHI', 'Cleveland Cavaliers': 'CLE', 'Dallas Mavericks': 'DAL', 'Denver Nuggets': 'DEN',
    'Detroit Pistons': 'DET', 'Golden State Warriors': 'GSW', 'Houston Rockets': 'HOU', 'Indiana Pacers': 'IND',
    'LA Clippers': 'LAC', 'Los Angeles Lakers': 'LAL', 'Memphis Grizzlies': 'MEM', 'Miami Heat': 'MIA',
    'Milwaukee Bucks': 'MIL', 'Minnesota Timberwolves': 'MIN', 'New Orleans Pelicans': 'NOP',
    'New York Knicks': 'NYK', 'Oklahoma City Thunder': 'OKC', 'Orlando Magic': 'ORL',
    'Philadelphia 76ers': 'PHI', 'Phoenix Suns': 'PHX', 'Portland Trail Blazers': 'POR',
    'Sacramento Kings': 'SAC', 'San Antonio Spurs': 'SAS', 'Toronto Raptors': 'TOR', 'Utah Jazz': 'UTA',
    'Washington Wizards': 'WAS',
}


def norm(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    s = re.sub(r"[.'’]", '', s)
    s = re.sub(r'\b(jr|sr|ii|iii|iv|v)\b', '', s)
    s = re.sub(r'[^a-z ]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load(name):
    with open(os.path.join(DATA, name)) as f:
        return json.load(f)


def stat_row(r):
    """ESPN row -> dict; row = [label, GP, MIN, FG%, FT%, 3PM, REB, AST, A/TO, STL, BLK, TO, PTS]"""
    if not r:
        return None
    v = [num(x) for x in r[1:]]
    if v[0] is None or v[0] <= 0:
        return None
    return dict(gp=v[0], mn=v[1], fg=v[2], ft=v[3], tp=v[4], rb=v[5], as_=v[6], st=v[8], bk=v[9], to=v[10], pt=v[11])


def volumes(s, per_min_fga, per_min_fta):
    """Estimate FGA/FTA per game consistent with the projected PTS/3PM/FG%/FT%.
    pts = 2*FGM + 3PM + FTM, with FGM = fg%*FGA and FTM = ft%*FTA."""
    fga = per_min_fga * s['mn'] if per_min_fga else None
    fta = per_min_fta * s['mn'] if per_min_fta else None
    if fga is None or fta is None:
        a = (s['pt'] - s['tp']) / (2 * s['fg'] + 0.26 * s['ft'])
        fga, fta = a, 0.26 * a
    have = 2 * s['fg'] * fga + s['ft'] * fta
    k = (s['pt'] - s['tp']) / have if have > 0 else 1.0
    return fga * k, fta * k


def main():
    es, adp, bb = load('espn_all.json'), load('adp.json'), load('bbref2026.json')
    bbn = {norm(b['name']): b for b in bb}
    adpn = {norm(a['name']): a for a in adp}

    players = []
    for p in es:
        proj = stat_row(p['proj'])
        if not proj or proj['gp'] < 15 or proj['mn'] < 8 or not p['id']:
            continue
        key = norm(p['name'])
        team = TEAMS.get(p['teamFull'], 'FA')
        pos = [x.strip() for x in p['pos'].split(',') if x.strip()]
        b = bbn.get(key)
        a = adpn.get(key)

        pmf = pmt = None
        if b and num(b['mp']) and num(b['g']) and num(b['fga']) is not None:
            pmf, pmt = num(b['fga']) / num(b['mp']), num(b['fta']) / num(b['mp'])
        fga, fta = volumes(proj, pmf, pmt)

        last = stat_row(p['last'])
        L = None
        if last and b:
            L = [round(last['gp']), last['mn'], last['pt'], last['tp'], last['rb'], last['as_'], last['st'],
                 last['bk'], last['to'], num(b['fg']), num(b['fga']), num(b['ft']), num(b['fta'])]

        status = ''
        ex = p.get('extra', '')
        if re.search(r'\bOut\b', ex): status = 'OUT'
        elif 'Day-To-Day' in ex: status = 'DTD'

        players.append(dict(
            id=p['id'], n=p['name'], t=team, p=pos,
            age=(int(b['age']) + 1) if b and b['age'].isdigit() else None,
            rk=last is None, inj=status,
            adp=num(a['avg']) if a else None,
            gp=round(proj['gp']), mn=proj['mn'], fg=proj['fg'], ft=proj['ft'], tp=proj['tp'], rb=proj['rb'],
            as_=proj['as_'], st=proj['st'], bk=proj['bk'], to=proj['to'], pt=proj['pt'],
            fga=round(fga, 2), fta=round(fta, 2), L=L,
        ))

    # ESPN's list is ordered by its own ranking; keep that order as the tie-breaker id
    for i, p in enumerate(players):
        p['esp'] = i + 1

    # ---- headshots -> one sprite ----
    try:
        from PIL import Image
    except ImportError:
        print('Pillow missing: skipping sprite'); Image = None

    def fetch(pid):
        url = f'https://a.espncdn.com/combiner/i?img=/i/headshots/nba/players/full/{pid}.png&w={TILE*2}&h={TILE*2}&scale=crop'
        for _ in range(3):
            try:
                with urllib.request.urlopen(url, timeout=20) as r:
                    return pid, r.read()
            except Exception:
                pass
        return pid, None

    if Image:
        with concurrent.futures.ThreadPoolExecutor(8) as ex:
            got = dict(ex.map(fetch, [p['id'] for p in players]))
        tiles = [p for p in players if got.get(p['id'])]
        rows = (len(tiles) + SPRITE_COLS - 1) // SPRITE_COLS
        sheet = Image.new('RGBA', (SPRITE_COLS * TILE, rows * TILE), (0, 0, 0, 0))
        for i, p in enumerate(tiles):
            im = Image.open(io.BytesIO(got[p['id']])).convert('RGBA').resize((TILE, TILE), Image.LANCZOS)
            sheet.paste(im, ((i % SPRITE_COLS) * TILE, (i // SPRITE_COLS) * TILE))
            p['hs'] = i
        for p in players:
            p.setdefault('hs', -1)
        sheet.save(os.path.join(DATA, 'headshots.webp'), 'WEBP', quality=82, method=6)
        print('headshots:', len(tiles), 'of', len(players), '| sheet', sheet.size,
              os.path.getsize(os.path.join(DATA, 'headshots.webp')) // 1024, 'KB')

    with open(os.path.join(DATA, 'players.json'), 'w') as f:
        json.dump(players, f, separators=(',', ':'))
    print('players:', len(players), '| with ADP:', sum(1 for p in players if p['adp']),
          '| rookies:', sum(1 for p in players if p['rk']))


if __name__ == '__main__':
    main()
