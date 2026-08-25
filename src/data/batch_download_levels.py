"""批量下载 level JSON（获取敌人路径 routes）。

用 tile JSON 的 levelId 从 ArknightsGameData CDN 下载。
"""

from __future__ import annotations

import json
import os
import time

MAA = r"C:\Users\slient\Downloads\MAA-v6.16.8-win-x64"
EXPERT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "expert_jobs")


def main():
    from src.data.stage_util import ensure_level_json_by_tile

    # Get all stages with expert jobs
    stages = set()
    for d in os.listdir(EXPERT_DIR):
        if os.path.isdir(os.path.join(EXPERT_DIR, d)):
            stages.add(d)

    print("Stages with expert jobs: %d" % len(stages))

    success = 0
    fail = 0
    skip = 0

    for i, sid in enumerate(sorted(stages)):
        result = ensure_level_json_by_tile(MAA, sid)
        if result:
            # Check if it has routes
            try:
                with open(result, encoding="utf-8") as f:
                    ld = json.load(f)
                routes = len(ld.get("routes", []))
                if routes > 0:
                    success += 1
                else:
                    skip += 1
            except:
                skip += 1
        else:
            fail += 1

        if (i + 1) % 20 == 0:
            print("[%d/%d] success=%d skip=%d fail=%d" % (i + 1, len(stages), success, skip, fail))
            time.sleep(0.5)  # Avoid rate limiting

    print()
    print("Done: success=%d skip=%d fail=%d (total=%d)" % (success, skip, fail, len(stages)))


if __name__ == "__main__":
    main()
