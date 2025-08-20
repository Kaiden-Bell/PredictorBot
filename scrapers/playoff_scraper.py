from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote
import time, re, requests
import pandas as pd


BASE = "https://liquidpedia.net/rocketleague/"
PLACEHOLDER = re.compile(r'\b(winner|loser)\s+of\b|^tbd$|^[-—]$', re.I)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; RL-PredictorBot/1.0)",
    "Accept-Language": "en-US,en;q=0.9",
}

def cleanTitle(s):
    if not s: return ""
    s = s.replace("\u200b", "").replace("\xa0", " ").strip()
    s = re.sub(r'\s+', ' ', s)
    return s


def getTeamName(op):
    name = op.get('aria-label')
    return cleanTitle(name) if name else None


def getTeamUrl(teamURL):
    teamURL = cleanTitle(teamURL).replace(' ', '_')
    slug = quote(teamURL, safe="()_'!-")
    return BASE + slug

def isPlaceholder(name):
    return not name or bool(PLACEHOLDER.search(name.strip()))

def fetchHTML(url, session=None):
    sess = session or requests.Session()
    r = sess.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")
    

def cleanPlayers(names):
    # filter obvious non-players and staff-y entries
    block = {'coach','twitter','country','substitute','manager','owner','analyst','staff'}
    out = []
    for n in names or []:
        n = cleanTitle(n)
        if not n or any(b in n.lower() for b in block) or len(n) > 40:
            continue
        out.append(n)
    # de-dupe preserving order
    seen, uniq = set(), []
    for n in out:
        if n not in seen:
            uniq.append(n); seen.add(n)
    return uniq

def extractRoster(team_soup):
    headers = team_soup.find_all(['h2','h3','h4'])
    pr_idx = None
    for i, h in enumerate(headers):
        txt = h.get_text(" ", strip=True).lower()
        if 'player roster' in txt:
            pr_idx = i
            break

    if pr_idx is not None:
        for j in range(pr_idx + 1, min(pr_idx + 8, len(headers))):
            t = headers[j].get_text(" ", strip=True).lower()
            if 'active' in t:
                players = []
                node = headers[j].find_next_sibling()
                hops = 0
                while node and hops < 12:
                    if node.name in ('h2','h3','h4'):
                        break
                    if hasattr(node, 'select'):
                        anchors = node.select('a[title]')
                        players.extend([a.get('title') or a.get_text(strip=True) for a in anchors])
                    node = node.find_next_sibling()
                    hops += 1
                players = cleanPlayers(players)
                players = [p for p in players if p.lower() not in {'eversax'}]  # sample coach filter; extend as needed
                if len(players) >= 3:
                    return players[:3]
                if players:
                    return players

    for sel in [
        '.roster-card .team-template-text a[title]',
        '.roster-card .ID a[title]',
        '.roster-card .player a[title]',
        '.roster .player a[title]',
        '.teamcard .team-template-text a[title]',
        '.infobox-cell-2 a[title]',
    ]:
        els = team_soup.select(sel)
        if els:
            names = cleanPlayers([e.get('title') or e.get_text(strip=True) for e in els])
            if names:
                return names[:3]

    els = team_soup.select('.mw-parser-output a[title]')
    names = cleanPlayers([e.get('title') for e in els])
    return names[:3]


def roundMap(bracket):
    mapping = {}
    columns = bracket.select('.brkts-column, .brkts-round, .brkts-round-wrapper') or [bracket]
    for col in columns:
        current = None
        for child in col.children:
            if not hasattr(child, 'get'):  # text nodes
                continue
            cls = child.get('class', [])
            if 'brkts-header' in cls:  # this is the label you found
                current = child.get_text(" ", strip=True)
            elif 'brkts-match' in cls:
                mapping[id(child)] = current
            else:
                inner = child.select('.brkts-match')
                for m in inner:
                    mapping[id(m)] = current
    return mapping

def nearestSect(n):
    hd = n.find_previous(['h2', 'h3', 'h4'])
    if not hd: return "Unknown"
    hl = hd.select_one('.mw-headline')
    return (hl.get_text(strip=True) if hl else hd.get_text(strip=True)) or "Unknown"


def scrape(URL):

    opts = Options()
    opts.add_argument("--headless=new")
    driver = webdriver.Chrome(options=opts)
    driver.get(URL)
    time.sleep(5)

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    driver.quit()

    rows = []
    for b in soup.find_all('div', class_='brkts-bracket'):
        section = nearestSect(b)
        if 'playoff' not in section.lower():
            continue

        rmap = roundMap(b)

        for m in b.find_all('div', class_='brkts-match'):
            ops = m.select('.brkts-opponent-entry')
            if len(ops) < 2:
                continue

            t1 = getTeamName(ops[0])
            t2 = getTeamName(ops[1])

            rows.append({
                'section': section,
                'round': rmap.get(id(m)) or "Unknown",
                'best_of': 7,
                'team1': t1, 'team2': t2,
                'team1_url': (None if isPlaceholder(t1) else getTeamUrl(t1)),
                'team2_url': (None if isPlaceholder(t2) else getTeamUrl(t2)),
            })

    sess = requests.Session()
    cache = {}
    for r in rows:
        for side in ('team1','team2'):
            url = r[side + '_url']
            if not url:
                r[side + '_players'] = []
                continue
            if url not in cache:
                try:
                    ts = fetchHTML(url, session=sess)
                    cache[url] = extractRoster(ts)
                    time.sleep(0.4)
                except Exception:
                    cache[url] = []
            r[side + '_players'] = cache[url]

    return pd.DataFrame(rows)


if __name__ == "__main__":
    URL = input("Enter the Tournament you wish to scrape: ")
    df = scrape(URL)
    print(df[['section','round','team1','team2','team1_players','team2_players']].head(20))
    df.to_csv("playoffs-scraped.csv", index=False)