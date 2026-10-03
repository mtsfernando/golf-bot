"""
Inspect 18Birdies JSON Export Structure
Usage:
    python scripts/inspect_birdies_json.py [path_to_file.json]

If no file path is provided, it inspects the latest JSON file in data/18birdies/.
"""

import sys
import json
import glob
from pathlib import Path

def inspect_json(file_path: str):
    path = Path(file_path)
    if not path.exists():
        print(f"❌ File not found: {path}")
        return

    print(f"\n🔍 Inspecting 18Birdies Export: {path.name} ({path.stat().st_size / 1024:.1f} KB)\n" + "="*60)
    
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 1. Root structure
    if isinstance(data, list):
        print(f"Root Type: List of {len(data)} items")
        if data:
            print(f"Sample Item Type: {type(data[0]).__name__}")
            if isinstance(data[0], dict):
                print(f"Keys in first item: {list(data[0].keys())[:15]}")
    elif isinstance(data, dict):
        print(f"Root Type: Dictionary with keys:\n  -> {list(data.keys())}\n")
        
        if "myData" in data:
            my_data = data["myData"]
            user = my_data.get("accountData", {})
            clubs = my_data.get("clubData", {}).get("playedClubs", [])
            rounds = my_data.get("activityData", {}).get("rounds", [])
            print(f"✅ Verified 18Birdies myData Archive!")
            print(f" - Golfer Name: {user.get('userName')} ({user.get('email')})")
            print(f" - Clubs Played ({len(clubs)}): {[c.get('name') for c in clubs[:5]]}")
            print(f" - Total Rounds Exported: {len(rounds)}")
            if rounds:
                sample = rounds[0]
                print(f"\nSample Round ID: {sample.get('id')}")
                print(f"Strokes: {sample.get('strokes')} | Score to Par: +{sample.get('score')}")
                print(f"Holes ({len(sample.get('holeStrokes', []))}): {sample.get('holeStrokes')}")
                print(f"Stats: {sample.get('stats')}")
        else:
            for key in ["rounds", "activities", "scorecards", "games", "history"]:
                if key in data:
                    val = data[key]
                    print(f"Found key '{key}': count={len(val) if isinstance(val, list) else 'N/A'}")

    print("\n" + "="*60)
    print("💡 Share this output or the top-level keys to lock in the exact 18Birdies parser mapping!")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = sys.argv[1]
    else:
        # Find latest file in data/18birdies/
        files = glob.glob("data/18birdies/*.json")
        if files:
            target = max(files, key=lambda f: Path(f).stat().st_mtime)
        else:
            print("No JSON files found in data/18birdies/ yet.")
            print("Usage: python scripts/inspect_birdies_json.py <path_to_exported_file.json>")
            sys.exit(0)
    inspect_json(target)
