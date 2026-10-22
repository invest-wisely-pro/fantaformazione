#!/usr/bin/env python3
"""Aggiorna dati.json per Fantaformazione, senza chiavi né registrazioni.

Fonte: understat.com, che pubblica liberamente i dati della Serie A (calendario, risultati,
statistiche dei giocatori per stagione e partita per partita). Da questi lo script ricava:
  - classifica, gol ed expected goals fatti e subiti di ogni squadra;
  - calendario diviso in giornate e giornata in corso;
  - per ogni giocatore: ruolo, squadra, presenze, minuti, gol, assist, cartellini;
  - per ogni partita giocata: minuti e fantavoto stimato di ciascun giocatore;
  - squalifiche probabili (espulsioni e somma di ammonizioni) e titolari rimasti fuori
    nell'ultima partita, per la giornata in corso.

Uso:  python tools/aggiorna_dati.py dati.json
Solo libreria standard di Python 3.9+.
"""
import codecs
import datetime
import gzip
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request

BASE = 'https://understat.com/'
LEGA = 'Serie_A'
PAUSA = 1.5               # secondi tra due richieste, per non pesare sul sito
MAX_PARTITE_PER_GIRO = 400
VERSIONE_DATI = 2         # cambia quando si aggiungono statistiche: le partite vengono rilette
SOGLIE_AMMONIZIONI = (5, 10, 14, 17)   # squalifica alla 5ª, 10ª, 14ª, 17ª e poi a ogni ammonizione

CANON = [('internazionale', 'Inter'), ('inter', 'Inter'), ('milan', 'Milan'), ('juventus', 'Juventus'),
         ('napoli', 'Napoli'), ('roma', 'Roma'), ('lazio', 'Lazio'), ('atalanta', 'Atalanta'),
         ('bologna', 'Bologna'), ('fiorentina', 'Fiorentina'), ('torino', 'Torino'), ('genoa', 'Genoa'),
         ('udinese', 'Udinese'), ('lecce', 'Lecce'), ('cagliari', 'Cagliari'), ('parma', 'Parma'),
         ('como', 'Como'), ('sassuolo', 'Sassuolo'), ('venezia', 'Venezia'), ('monza', 'Monza'),
         ('frosinone', 'Frosinone'), ('verona', 'Verona'), ('empoli', 'Empoli'), ('cremonese', 'Cremonese'),
         ('pisa', 'Pisa'), ('salernitana', 'Salernitana'), ('sampdoria', 'Sampdoria'), ('spezia', 'Spezia'),
         ('palermo', 'Palermo'), ('bari', 'Bari'), ('pescara', 'Pescara'), ('catanzaro', 'Catanzaro'),
         ('modena', 'Modena'), ('cesena', 'Cesena'), ('brescia', 'Brescia'), ('reggiana', 'Reggiana'),
         ('sudtirol', 'Südtirol'), ('carrarese', 'Carrarese'), ('juve stabia', 'Juve Stabia'),
         ('avellino', 'Avellino'), ('padova', 'Padova'), ('entella', 'Entella'), ('mantova', 'Mantova'),
         ('spal', 'SPAL'), ('benevento', 'Benevento'), ('crotone', 'Crotone')]


TRASL = str.maketrans({'ø': 'o', 'Ø': 'o', 'æ': 'ae', 'Æ': 'ae', 'œ': 'oe', 'ß': 'ss', 'ł': 'l', 'Ł': 'l',
                       'đ': 'd', 'Đ': 'd', 'ı': 'i', 'þ': 'th'})


def norm(s):
    s = unicodedata.normalize('NFD', str(s or '').translate(TRASL))
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn').lower()
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', s)).strip()


def squadra(nome):
    n = norm(nome)
    for k, v in CANON:
        if k in n:
            return v
    return str(nome or '').strip()


def ruolo(pos):
    p = (str(pos or '').split() or [''])[0].upper()
    if p.startswith('GK'):
        return 'P'
    return {'D': 'D', 'M': 'C', 'F': 'A'}.get(p[:1], 'C')


def f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def i(x, d=0):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return d


def stagione_corrente(oggi=None):
    oggi = oggi or datetime.date.today()
    return oggi.year if oggi.month >= 7 else oggi.year - 1


def log(*a):
    print(*a, flush=True)


class Fonte(Exception):
    pass


# ------------------------------------------------------------------ richieste

_ultima = [0.0]


def scarica(url, ajax=True):
    wait = PAUSA - (time.time() - _ultima[0])
    if wait > 0:
        time.sleep(wait)
    h = {'User-Agent': 'Mozilla/5.0 (compatible; Fantaformazione/1.0; uso personale)',
         'Accept-Encoding': 'gzip', 'Accept': 'application/json, text/html'}
    if ajax:
        h['X-Requested-With'] = 'XMLHttpRequest'
    for tentativo in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=40) as r:
                _ultima[0] = time.time()
                body = r.read()
                if r.headers.get('Content-Encoding') == 'gzip' or body[:2] == b'\x1f\x8b':
                    body = gzip.decompress(body)
                return body.decode('utf-8')
        except urllib.error.HTTPError as e:
            _ultima[0] = time.time()
            if e.code in (429, 500, 502, 503) and tentativo < 2:
                time.sleep(20 * (tentativo + 1))
                continue
            raise Fonte(f'Understat ha risposto {e.code} a {url}')
        except (urllib.error.URLError, TimeoutError) as e:
            if tentativo < 2:
                time.sleep(10)
                continue
            raise Fonte(f'Understat non raggiungibile ({e})')


def json_da_html(html, var):
    m = re.search(var + r"\s*=\s*JSON\.parse\('(.*?)'\)", html, re.S)
    if not m:
        return None
    return json.loads(codecs.escape_decode(m.group(1).encode())[0].decode('utf-8'))


def dati_lega(stag):
    try:
        d = json.loads(scarica(f'{BASE}getLeagueData/{LEGA}/{stag}'))
        if isinstance(d, dict) and d.get('dates'):
            return d
    except (Fonte, ValueError) as e:
        log('  dati della lega in formato diretto non disponibili:', e)
    html = scarica(f'{BASE}league/{LEGA}/{stag}', ajax=False)
    d = {'dates': json_da_html(html, 'datesData'), 'teams': json_da_html(html, 'teamsData'),
         'players': json_da_html(html, 'playersData')}
    if not d['dates']:
        raise Fonte('Understat: non trovo il calendario della Serie A (il sito potrebbe aver cambiato formato)')
    return d


def formazioni(mid):
    try:
        d = json.loads(scarica(f'{BASE}getMatchData/{mid}'))
        if isinstance(d, dict) and d.get('rosters'):
            return d['rosters']
    except (Fonte, ValueError):
        pass
    r = json_da_html(scarica(f'{BASE}match/{mid}', ajax=False), 'rostersData')
    if not r:
        raise Fonte(f'Understat: formazioni della partita {mid} non trovate')
    return r


# ------------------------------------------------------------------ giornate

def giornate_per_vincoli(partite):
    """Assegna ogni partita alla sua giornata: ogni squadra gioca una sola volta per giornata.

    1. Se una partita è la k-esima per entrambe le squadre, in ordine di data, è della giornata k.
    2. Quelle con una data lontana dalle altre partite della loro giornata vengono rimesse in coda.
    3. Le partite rimaste (rinvii, recuperi, anticipi) vanno nella giornata in cui entrambe
       le squadre sono libere, scegliendo la più vicina per data quando ce n'è più d'una."""
    squadre = sorted({p['h'] for p in partite} | {p['a'] for p in partite})
    n_g = 2 * (len(squadre) - 1) if len(squadre) > 1 else 1
    per_sq = {t: sorted([p for p in partite if t in (p['h'], p['a'])], key=lambda p: p['data']) for t in squadre}
    idx = {}
    for t, L in per_sq.items():
        for k, p in enumerate(L, 1):
            idx[(p['id'], t)] = k
    g_di = {}
    for p in partite:
        kh, ka = idx[(p['id'], p['h'])], idx[(p['id'], p['a'])]
        if kh == ka and kh <= n_g:
            g_di[p['id']] = kh

    def mediane():
        md = {}
        for g in range(1, n_g + 1):
            ds = sorted(p['data'] for p in partite if g_di.get(p['id']) == g)
            if ds:
                md[g] = ds[len(ds) // 2]
        return md
    md = mediane()
    for p in partite:
        g = g_di.get(p['id'])
        if g and abs((p['data'] - md[g]).days) > 3:
            del g_di[p['id']]
    md = mediane()
    # stima la data delle giornate senza riferimenti, interpolando tra le vicine
    note = sorted(md)
    for g in range(1, n_g + 1):
        if g not in md and note:
            prima = [x for x in note if x < g]
            dopo = [x for x in note if x > g]
            if prima and dopo:
                a_, b_ = prima[-1], dopo[0]
                md[g] = md[a_] + (md[b_] - md[a_]) * ((g - a_) / (b_ - a_))
            else:
                ref = prima[-1] if prima else dopo[0]
                md[g] = md[ref] + datetime.timedelta(days=7 * (g - ref))
    occ = {g: set() for g in range(1, n_g + 1)}
    for p in partite:
        g = g_di.get(p['id'])
        if g:
            occ[g].update((p['h'], p['a']))
    resto = [p for p in partite if p['id'] not in g_di]
    while resto:
        def candidati(p):
            return [g for g in range(1, n_g + 1) if p['h'] not in occ[g] and p['a'] not in occ[g]]
        resto.sort(key=lambda p: (len(candidati(p)) or 99, p['data']))
        p = resto.pop(0)
        c = candidati(p) or list(range(1, n_g + 1))
        g = min(c, key=lambda g: abs((p['data'] - md[g]).total_seconds()))
        g_di[p['id']] = g
        occ[g].update((p['h'], p['a']))
    return g_di


def giornate_per_date(partite):
    """Raggruppa per finestre di date; i recuperi isolati tornano dove entrambe le squadre mancano."""
    ps = sorted(partite, key=lambda p: p['data'])
    finestre, cur = [], None
    for p in ps:
        if cur and (p['h'] in cur['sq'] or p['a'] in cur['sq'] or (p['data'] - cur['inizio']).days > 5):
            cur = None
        if not cur:
            cur = {'inizio': p['data'], 'sq': set(), 'p': []}
            finestre.append(cur)
        cur['p'].append(p)
        cur['sq'].update((p['h'], p['a']))
    giornate, orfane = [], []
    for w in finestre:
        (giornate.append(w['p']) if len(w['p']) >= 5 else orfane.extend(w['p']))
    for p in orfane:
        for g in giornate:
            if len(g) < 10 and all(p['h'] not in (x['h'], x['a']) and p['a'] not in (x['h'], x['a']) for x in g):
                g.append(p)
                break
        else:
            giornate.append([p])
    return {p['id']: n for n, g in enumerate(giornate, 1) for p in g}


def penalita(partite, g_di):
    """Quanto una divisione in giornate è incoerente: 0 è perfetta."""
    if any(p['id'] not in g_di for p in partite):
        return float('inf')
    n_sq = len({p['h'] for p in partite} | {p['a'] for p in partite})
    per_g = {}
    for p in partite:
        per_g.setdefault(g_di[p['id']], []).append(p)
    pen = abs(len(per_g) - 2 * (n_sq - 1)) * 10
    for L in per_g.values():
        squadre = [t for p in L for t in (p['h'], p['a'])]
        pen += abs(len(L) - n_sq // 2) * 3 + (len(squadre) - len(set(squadre))) * 5
        ds = sorted(p['data'] for p in L)
        med = ds[len(ds) // 2]
        pen += sum(max(0, abs((d - med).days) - 3) for d in ds) / 10
    return pen


def dividi_in_giornate(partite, salvate=None):
    """Sceglie la divisione più coerente tra quella già salvata e i due metodi."""
    cand = []
    if salvate:
        s = {p['id']: salvate.get(str(p['id'])) for p in partite}
        if all(s.values()):
            cand.append(('salvata', s))
    cand += [('vincoli', giornate_per_vincoli(partite)), ('date', giornate_per_date(partite))]
    nome, g_di = min(cand, key=lambda c: penalita(partite, c[1]))  # a parità vince la salvata
    n = max(g_di.values())
    giornate = [[] for _ in range(n)]
    for p in partite:
        p['g'] = g_di[p['id']]
        giornate[p['g'] - 1].append(p)
    log(f'  giornate: metodo {nome}, coerenza {penalita(partite, g_di):.1f} (0 = perfetta)')
    return giornate


# ------------------------------------------------------------------ voti

def fantavoto(r, ru, fatti, subiti):
    """Fantavoto stimato da statistiche oggettive (non è il voto dei quotidiani)."""
    minuti, gol, ass = i(r.get('time')), i(r.get('goals')), i(r.get('assists'))
    amm, esp, aut = i(r.get('yellow_card')), i(r.get('red_card')), i(r.get('own_goals'))
    if minuti < 15 and not (gol or ass or esp or aut):
        return None
    v = 6.0 + (0.25 if fatti > subiti else -0.25 if fatti < subiti else 0)
    inv = f(r.get('xG')) + f(r.get('xA'))
    if ru == 'P':
        v += 0.5 if subiti == 0 else -0.5 if subiti >= 3 else 0
    elif ru == 'D':
        v += (0.25 if subiti == 0 else -0.25 if subiti >= 3 else 0) + min(0.5, 0.5 * inv)
    else:
        v += min(0.75, 0.75 * inv)
    v += (0.5 if gol else 0) + (0.25 if ass else 0) + (0.25 if i(r.get('key_passes')) >= 3 else 0) - (1 if esp else 0)
    v = max(4.0, min(8.5, round(v * 2) / 2))
    bonus = 3 * gol + ass - 0.5 * amm - esp - 2 * aut
    if ru == 'P' and minuti >= 45:
        bonus -= subiti
    return round(v + bonus, 1)


# ------------------------------------------------------------------ costruzione

def costruisci(stag, prec):
    lg = dati_lega(stag)
    partite = []
    for x in lg['dates']:
        try:
            data = datetime.datetime.strptime(x['datetime'][:16], '%Y-%m-%d %H:%M')
        except (KeyError, ValueError):
            continue
        fatto = x.get('isResult') in (True, 'true', 1, '1')
        gl = x.get('goals') or {}
        partite.append({'id': i(x['id']), 'data': data, 'fatto': fatto,
                        'h': squadra(x['h']['title']), 'a': squadra(x['a']['title']),
                        'gh': i(gl.get('h')) if fatto else None, 'ga': i(gl.get('a')) if fatto else None})
    giornate = dividi_in_giornate(partite, (prec.get('meta') or {}).get('giornataDi'))
    cal = {str(n): [[p['h'], p['a'], p['gh'], p['ga'], p['data'].strftime('%Y-%m-%dT%H:%M')]
                    for p in sorted(g, key=lambda p: p['data'])] for n, g in enumerate(giornate, 1)}
    aperte = [n for n, g in enumerate(giornate, 1) if sum(1 for p in g if not p['fatto']) > len(g) / 2]
    corrente = aperte[0] if aperte else len(giornate)

    # classifica, dalle partite giocate
    tab = {}
    for p in partite:
        for t in (p['h'], p['a']):
            tab.setdefault(t, {'n': t, 'g': 0, 'pt': 0, 'gf': 0, 'gs': 0, 'xgf': 0.0, 'xgs': 0.0})
    vuoto = lambda: {'g': 0, 'gf': 0, 'gs': 0, 'xgf': 0.0, 'xgs': 0.0}
    def somma(acc, h):
        acc['g'] += 1
        acc['gf'] += i(h.get('scored'))
        acc['gs'] += i(h.get('missed'))
        acc['xgf'] = round(acc['xgf'] + f(h.get('xG')), 2)
        acc['xgs'] = round(acc['xgs'] + f(h.get('xGA')), 2)
    for t in (lg.get('teams') or {}).values():
        n = squadra(t.get('title'))
        if n not in tab:
            continue
        storia = sorted(t.get('history') or [], key=lambda h: str(h.get('date')))
        casa, fuori, forma = vuoto(), vuoto(), vuoto()
        for h in storia:
            tab[n]['xgf'] += f(h.get('xG'))
            tab[n]['xgs'] += f(h.get('xGA'))
            somma(casa if h.get('h_a') == 'h' else fuori, h)
        for h in storia[-6:]:
            somma(forma, h)
        tab[n].update({'casa': casa, 'fuori': fuori, 'forma': forma})
    for p in partite:
        if not p['fatto']:
            continue
        for t, fa, su in ((p['h'], p['gh'], p['ga']), (p['a'], p['ga'], p['gh'])):
            r = tab[t]
            r['g'] += 1
            r['gf'] += fa
            r['gs'] += su
            r['pt'] += 3 if fa > su else 1 if fa == su else 0
    sq = sorted(tab.values(), key=lambda r: (-r['pt'], -(r['gf'] - r['gs']), -r['gf'], r['n']))
    for pos, r in enumerate(sq, 1):
        r['pos'] = pos
        r['xgf'], r['xgs'] = round(r['xgf'], 1), round(r['xgs'], 1)

    # giocatori
    gioc = {}
    for x in lg.get('players') or []:
        squadre = [s for s in str(x.get('team_title') or '').split(',') if s]
        gioc[str(x['id'])] = {'id': i(x['id']), 'n': x.get('player_name', ''), 'c': x.get('player_name', ''),
                              'r': ruolo(x.get('position')), 's': squadra(squadre[-1] if squadre else ''),
                              'pres': i(x.get('games')), 'min': i(x.get('time')), 'gol': i(x.get('goals')),
                              'ass': i(x.get('assists')), 'amm': i(x.get('yellow_cards')),
                              'esp': i(x.get('red_cards')), 'xg': round(f(x.get('xG')), 2),
                              'xa': round(f(x.get('xA')), 2), 'pxg': round(f(x.get('xG')) - f(x.get('npxG')), 2),
                              'tiri': i(x.get('shots')), 'kp': i(x.get('key_passes')), 'fm': None, 'mv': None}

    # partite giocate: minuti, cartellini e fantavoto stimato di ogni giocatore
    voti = {g: dict(v) for g, v in (prec.get('voti') or {}).items()}
    lette = {int(k): v for k, v in ((prec.get('meta') or {}).get('lette') or {}).items()}
    if (prec.get('meta') or {}).get('versione') != VERSIONE_DATI:
        voti, lette = {}, {}
    per_id = {p['id']: p for p in partite}
    # se un rinvio ha cambiato la giornata di una partita già letta, sposta i suoi voti
    for mid, gvec in list(lette.items()):
        p = per_id.get(mid)
        if p and p['g'] != gvec and str(gvec) in voti:
            for pid in list(voti[str(gvec)]):
                if (gioc.get(pid) or {}).get('s') in (p['h'], p['a']):
                    voti.setdefault(str(p['g']), {})[pid] = voti[str(gvec)].pop(pid)
            lette[mid] = p['g']
    da_leggere = [p for p in sorted(partite, key=lambda p: p['data']) if p['fatto'] and p['id'] not in lette]
    letti = 0
    for p in da_leggere[:MAX_PARTITE_PER_GIRO]:
        try:
            ro = formazioni(p['id'])
        except Fonte as e:
            log('  ', e)
            continue
        g = str(p['g'])
        for lato, casa in (('h', 1), ('a', 0)):
            fatti, subiti = (p['gh'], p['ga']) if casa else (p['ga'], p['gh'])
            for r in (ro.get(lato) or {}).values():
                pid = str(r.get('player_id'))
                ru = (gioc.get(pid) or {}).get('r') or ruolo(r.get('position'))
                titolare = 0 if str(r.get('position') or '').strip().lower() == 'sub' else 1
                voti.setdefault(g, {})[pid] = [fantavoto(r, ru, fatti, subiti), i(r.get('time')), casa,
                                               i(r.get('yellow_card')), i(r.get('red_card')), titolare,
                                               round(f(r.get('xG')), 2), round(f(r.get('xA')), 2),
                                               i(r.get('shots')), i(r.get('key_passes'))]
        lette[p['id']] = p['g']
        letti += 1

    # fantamedia stimata
    acc = {}
    for vs in voti.values():
        for pid, v in vs.items():
            if v[0] is not None:
                acc.setdefault(pid, []).append(v[0])
    for pid, a in acc.items():
        if pid in gioc:
            gioc[pid]['fm'] = round(sum(a) / len(a), 2)

    ind = indisponibili(partite, voti, gioc, corrente)
    out = {'calendario': cal, 'giornataCorrente': corrente, 'squadre': sq, 'giocatori': list(gioc.values()),
           'voti': voti, 'indisponibili': ind,
           'meta': {'versione': VERSIONE_DATI, 'giornataDi': {str(p['id']): p['g'] for p in partite}, 'lette': {str(k): v for k, v in sorted(lette.items())},
                    'partiteDaLeggere': max(0, len(da_leggere) - letti)}}
    log(f'  Understat: {len(partite)} partite in {len(giornate)} giornate, {len(gioc)} giocatori, '
        f'{letti} partite lette ora, {len(ind)} indisponibili per la giornata {corrente}')
    return out


def indisponibili(partite, voti, gioc, corrente):
    """Squalifiche probabili e titolari rimasti fuori nell'ultima partita della propria squadra."""
    ind = {}
    giocate = sorted([p for p in partite if p['fatto'] and p['g'] < corrente], key=lambda p: p['data'])
    storia = {}
    for p in giocate:
        storia.setdefault(p['h'], []).append(p)
        storia.setdefault(p['a'], []).append(p)
    amm_tot, prima = {}, {}
    for p in giocate:
        for pid, v in (voti.get(str(p['g'])) or {}).items():
            if (gioc.get(pid) or {}).get('s') not in (p['h'], p['a']):
                continue
            prima[(pid, p['id'])] = amm_tot.get(pid, 0)
            amm_tot[pid] = amm_tot.get(pid, 0) + (v[3] if len(v) > 3 else 0)

    def soglia(a, b):
        return any(a < s <= b for s in SOGLIE_AMMONIZIONI) or (b > 17 and b > a)

    def squalificato_dopo(pid, p):
        v = (voti.get(str(p['g'])) or {}).get(pid)
        if not v or len(v) < 5:
            return None
        if v[4]:
            return 'espulso nell’ultima partita, squalifica probabile'
        a0 = prima.get((pid, p['id']), 0)
        if v[3] and soglia(a0, a0 + v[3]):
            return f'{a0 + v[3]}ª ammonizione, squalifica probabile'
        return None

    for pid, gi in gioc.items():
        st = storia.get(gi['s']) or []
        if not st:
            continue
        ultima = st[-1]
        if (voti.get(str(ultima['g'])) or {}).get(pid):
            m = squalificato_dopo(pid, ultima)
            if m:
                ind[pid] = {'t': 'squ', 'm': m}
            continue
        # partite consecutive saltate fino all'ultima
        fuori = 0
        while fuori < len(st) and not (voti.get(str(st[-1 - fuori]['g'])) or {}).get(pid):
            fuori += 1
        prima_assenza = st[:len(st) - fuori]
        prec4 = prima_assenza[-4:]
        titolare = sum(1 for p in prec4 if ((voti.get(str(p['g'])) or {}).get(pid) or [0, 0])[1] >= 45)
        if len(prec4) < 3 or titolare < 3:
            continue
        if fuori == 1 and len(st) >= 2 and squalificato_dopo(pid, st[-2]):
            continue  # stava scontando la squalifica
        if fuori == 1:
            ind[pid] = {'t': 'dub', 'm': 'titolare fuori nell’ultima partita: infortunio o turno di riposo'}
        elif fuori == 2:
            ind[pid] = {'t': 'dub', 'm': 'titolare fuori nelle ultime 2 partite: infortunio o scelta tecnica'}
        else:
            ind[pid] = {'t': 'pan', 'm': f'fuori da {fuori} partite: infortunio o scelta tecnica'}
    return ind


def main():
    dest = sys.argv[1] if len(sys.argv) > 1 else 'dati.json'
    try:
        prec = json.load(open(dest, encoding='utf-8'))
    except (OSError, ValueError):
        prec = {}
    stag = stagione_corrente()
    log(f'Serie A {stag}/{str(stag + 1)[2:]}, fonte understat.com')
    try:
        nuovo = costruisci(stag, prec)
    except Fonte as e:
        log('Aggiornamento non riuscito:', e)
        if prec:  # tiene i dati di prima e segnala il problema nell'app
            prec['problemi'] = [str(e)]
            with open(dest, 'w', encoding='utf-8') as fh:
                json.dump(prec, fh, ensure_ascii=False, separators=(',', ':'))
            return 0
        return 1
    nuovo.update({'stagione': stag, 'fonti': ['understat.com'], 'problemi': []})
    confronta = lambda d: json.dumps({k: v for k, v in d.items() if k != 'aggiornato'}, sort_keys=True)
    if prec and confronta(prec) == confronta(nuovo):
        log('Nessuna novità.')
        return 0
    nuovo['aggiornato'] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    with open(dest, 'w', encoding='utf-8') as fh:
        json.dump(nuovo, fh, ensure_ascii=False, separators=(',', ':'))
    log(f'Scritto {dest}: giornata {nuovo["giornataCorrente"]}, {len(nuovo["squadre"])} squadre, '
        f'{len(nuovo["giocatori"])} giocatori')
    return 0


if __name__ == '__main__':
    sys.exit(main())
