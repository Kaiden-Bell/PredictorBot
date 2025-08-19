from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
import time
import pandas as pd

def getTeamName(op):
    aria = op.get('aria-label')
    if aria:
        return aria.strip()

    el = op.select_one('.team-template-text a[title]')
    if el:
        return el.get('title').strip()

    el = op.select_one('.team-template-image a[title]')
    if el:
        return el.get('title').strip()

    el = op.select_one('.name.hidden-xs, .name.visible-xs, .brkts-opponent-name, .team-template-name, .name')
    if el and el.get_text(strip=True):
        return el.get_text(strip=True)

    el = op.find('a')
    if el and (el.get('title') or el.get_text(strip=True)):
        return (el.get('title') or el.get_text(strip=True)).strip()

    txt = op.get_text(" ", strip=True)
    return txt or None

def get_nearest_section_name(node):
    heading = node.find_previous(['h2', 'h3', 'h4'])
    if not heading:
        return "Unknown Section"
    hl = heading.select_one('.mw-headline')
    return (hl.get_text(strip=True) if hl else heading.get_text(strip=True)) or "Unknown Section"

def get_round_labels_by_column(bracket):
    mapping = {}
    round_wrappers = bracket.select('.brkts-round, .brkts-column, .brkts-round-wrapper')
    for rw in round_wrappers:
        label_el = rw.select_one('.brkts-round-label, .brkts-matchlist-header, .brkts-roundheader')
        label = label_el.get_text(" ", strip=True) if label_el else None
        for m in rw.select('.brkts-match'):
            mapping[id(m)] = label
    return mapping

def infer_best_of(section):
    s = section.lower()
    if "group" in s:
        return 5
    if "playoff" in s:
        return 7
    return None  # unknown bucket (rare), you can default to 5/7 if you want

def getMatchData():
    url = "https://liquipedia.net/rocketleague/Rocket_League_Championship_Series/2025/Last_Chance_Qualifier/Europe"

    options = Options()
    options.add_argument("--headless=new")
    driver = webdriver.Chrome(options=options)
    driver.get(url)
    time.sleep(3)

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    driver.quit()

    rows = []
    brackets = soup.find_all('div', class_='brkts-bracket')
    print(f"Found {len(brackets)} total bracket containers")

    for b in brackets:
        section = get_nearest_section_name(b)  # e.g., "Group A", "Group B", "Playoffs"
        best_of = infer_best_of(section)
        round_map = get_round_labels_by_column(b)

        matches = b.find_all('div', class_='brkts-match')
        print(f"[{section}] matches: {len(matches)}")

        for m in matches:
            opponents = m.select('.brkts-opponent-entry')
            if len(opponents) < 2:
                continue

            t1 = getTeamName(opponents[0])
            t2 = getTeamName(opponents[1])
            rnd = round_map.get(id(m)) or "Unknown Round"

            rows.append({
                "section": section,
                "round": rnd,
                "best_of": best_of,
                "team1": t1,
                "team2": t2,
            })

    return rows

if __name__ == "__main__":
    data = getMatchData()
    df = pd.DataFrame(data)
    print(df.head(20))

    # Examples of easy splits:
    groups = df[df['section'].str.contains('Group', case=False, na=False)]
    playoffs = df[df['section'].str.contains('Playoff', case=False, na=False)]

    groups.to_csv("liquipedia_groups.csv", index=False)
    playoffs.to_csv("liquipedia_playoffs.csv", index=False)
    df.to_csv("liquipedia_all.csv", index=False)
