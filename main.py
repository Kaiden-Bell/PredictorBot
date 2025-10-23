import pandas as pd
from scrapers import scrape_playoffs, getH2HStats, Ballchasing
from dotenv import load_dotenv
import os

load_dotenv()

def main():
    url = input("Enter the Tournament you wish to scrape: ").strip()
    df = scrape_playoffs(url)
    print(df.head())

    mask = df["team1"].notna() & df["team2"].notna()
    if not mask.any():
        print("No concrete matchups yet."); return
    row = df[mask].iloc[0]


    t1, t2 = row["team1"], row["team2"]
    r1, r2 = row["team1_players"], row["team2_players"]

    bc = Ballchasing()  # requires BALLCHASING_API_KEY in env
    stats, logs = getH2HStats(t1, t2, r1, r2, bc)
    print(stats)
    if logs:
        print("Notes:", "; ".join(logs[:6]))

if __name__ == "__main__":
    main()
