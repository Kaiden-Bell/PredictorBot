import os, re, time, requests, pandas as pd
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import quote_plus, urlencode
from bs4 import BeautifulSoup

LP_BASE = "https://liquipedia.net/"
LP_RL = f"{LP_BASE}/rocketleague"
BC_API = "https://ballchasing.com/api"

HEADERS = {
    "User-Agent": "RL-PredictorBot/1.0 (https://example.com)",
    "Accept-Language": "en-US,en;q=0.9",
}

def _soup(url, session):
    sess = session or requests.Session()
    r = sess.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


# Step 1.) Fetch LP H2H

def buildH2H(t1, t2):
    params = {
        "Headtohead[team1]": t1,
        "Headtohead[team2]": t2,
        "RunQuery": "Run",
        "pfRunQueryFormName": "Head2head"
    }

    return f"{LP_RL}/Special:RunQuery/Head2Head?{urlencode(params)}"

def parseH2H(t1, t2):
    # Return a list of key terms from past series (date, event, match link, score)
    url = buildH2H(t1, t2)
    s = _soup(url)
    rows = []

    for tr in s.select("table tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        
        a = tr.select_one("a[href*='/rocketleague/']")
        if not a:
            continue
        href = a.get("href")
        if not href:
            continue
        ml = href if href.startwith("http") else (LP_BASE + href)
        date = (tds[0].get_text(" ", strip=True) if tds else " ")[:32]
        score = tr.get_text(" ", strip=True)
        rows.append({"date": date, "match link": ml, "score": score})
    return rows

BC_ID_RE = re.compile(r"(?:ballchasing\.com/(?:replay|group)/)([A-Za-z0-9-]+)")

def extractBallchasing(url, session):
    s = _soup(url, session=session)
    out = []

    for a in s.select("a[href*='ballchasing.com']"):
        href = a.get("href") or "" 
        m = BC_ID_RE.search(href)

        if m:
            rid = m.group(1)
            tt = "group" if "/group/" in href else "replay"
            out.append((tt, rid))

    return out

# Ballchasing API setup

class Ballchasing:
    def __init__(self, key, delay=0.35):
        self.key = key or os.getenv("BALLCHASING_API_KEY") or ""
        if not self.key:
            raise RuntimeError("set BALLCHASING API KEY env or pass key=...")
        self.sess = requests.Session()
        self.sess.headers.update({"Authorization": self.key, "Accept": "application/json"})
        self.delay = delay


    def __get(self, path, params=None):
        url = f"{BC_API}{path}"
        r = self.sess.get(url, params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(1.25)
            r = self.sess.get(url, params=params, timeout=30)
        r.raise_for_status()
        time.sleep(self.delay)
        return r.json()
    
    def getReplay(self, replayID):
        return self.__get(f"/replays/{replayID}")
    def getGroup(self, groupID):
        return self.__get(f"/groups/{groupID}")
    def listReplays(self, **params):
        return self.__get("/replays", params=params)
    
# Parse Rosters, Players, Stats

def playersInReplay(detail):
    out = []

    blue = (detail.get("blue") or {}).get("players") or []
    orange = (detail.get("orange") or {}).get("players") or []

    for pl in blue + orange:
        name = pl.get("name") or (pl.get("player") or {}).get("name")
        if name: out.append(name)

    return out

def extractStats(detail):
    rows = []
    for side in ("blue", "orange"):
        team = (detail.get(side) or {})
        for pl in team.get("players", []) or []:
            name = pl.get("name") or (pl.get("player") or {}).get("name")
            stats = (pl.get("stats") or {})
            core = stats.get("core") or {}
            demo = stats.get("demo") or {}
            goals = core.get("goals", 0)
            shots = core.get("shots", 0)
            saves = core.get("saves", 0)
            demos = demo.get("inflicted", 0)
            shotPct = (goals / shots) if shots else 0.0
            rows.append({
                "Player": name,
                "Goals": goals,
                "Shots": shots,
                "Shot %": shotPct,
                "Saves": saves,
                "Demos": demos,
                "Replay ID": detail.get("id"),
                "Date": detail.get("date")
            })

    return rows


def aggregatePlayers(rows):
    if not rows:
        return pd.DataFrame(columns=["Players", "Games", "Goals", "Shots", "Shot %", "Saves", "Demos"])
    df = pd.DataFrame(rows)
    g = df.groupby("Player", dropna=False).agg(
        games = ("replayID", "nunique"),
        goals = ("Goals", "sum"),
        shots = ("Shots", "sum"),
        saves = ("Saves", "sum"),
        demos = ("Demos", "sum"), 
    ).reset_index()
    g["Shot %"] = g.apply(lambda r: (r["Goals"]/r["Shots"]) if r["Shots"] else 0.0, axis=1)
    return g[["Player", "Games", "Goals", "Shots", "Shot %", "Saves", "Demos"]].sort_values(["Games", "Shot %"], ascending=[False, False])


def getH2HStats(t1, t2, r1, r2, bc: Ballchasing, limit: int=6, fallback: int=30):

    logs = []
    session = requests.Session()
    h2h = parseH2H(t1, t2)
    if not h2h: 
        logs.append("No H2H rows found on LP.")
        return pd.DataFrame(), logs

    h2h = h2h[:limit]
    replayIDs = set()

    for row in h2h:
        seriesURL = row["matchLink"]
        try:
            pairs = extractBallchasing(seriesURL, session=session)
        except Exception as e:
            logs.append(f"Failed to extract series {seriesURL} ({e})")
            pairs = []
        for kind, rid in pairs:
            if kind == "replay":
                replayIDs.add(rid)
            elif kind == "group":
                try:
                    g = bc.getGroup(rid)
                    for it in g.get("replays", []) or []:
                        if "id" in it:
                            replayIDs.add(it["id"])
                except Exception as e:
                    logs.append(f"Failed group fetch {rid}: {e}")
    
    if not replayIDs:
        logs.append("No Ballchasing links on pages! Attempting name-based")

        cutoff = 50

        by_name = []

        for name in set((r1 or []) + (r2 or [])):
            try:
                data = bc.listReplays(**{
                    "player-name": name,
                    "sort-by": "date",
                    "order": "desc",
                    "count": 25, "page": 0,
                })
                by_name.extend(data.get("list", []))
                time.sleep(0.2)
            except Exception as e:
                logs.append(f"BC list error for {name}: {e}")

            if len (by_name) > cutoff:
                break

        for it in by_name:
            try:
                d = bc.getReplay(it["id"])
            except Exception as e:
                logs.append(f"BC detail error {it.get('id')}: {e}")
                continue
                
            names = set([n.lower() for n in playersInReplay(d)])
            r1Hit = sum(1 for p in (r1 or []) if p and p.lower() in names)
            r2Hit = sum(1 for p in (r2 or []) if p and p.lower() in names)
            if r1Hit >= 2 and r2Hit >= 2:
                replayIDs.add(d["id"])

    perPlayerRows = []

    for rid in replayIDs:
        try:
            d = bc.getReplay(rid)
        except Exception as e:
            logs.append(f"Replay {rid} fetch failed: {e}")
            continue
        perPlayerRows.extend(extractStats(d))

    return aggregatePlayers(perPlayerRows), logs

