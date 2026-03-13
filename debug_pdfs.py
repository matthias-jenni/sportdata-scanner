"""
Run this directly to inspect what the parsers extract from your PDFs.

Usage:
  python3 debug_pdfs.py <registrations.pdf> <schedule.pdf>
"""
import sys
import json
from utils.parse_registrations import extract_fighters, get_swiss_fighters
from utils.parse_schedule import extract_schedule

if len(sys.argv) != 3:
    print("Usage: python3 debug_pdfs.py <registrations.pdf> <schedule.pdf>")
    sys.exit(1)

reg_path   = sys.argv[1]
sched_path = sys.argv[2]

print("\n=== REGISTRATIONS (first 20 rows, all countries) ===")
all_fighters = extract_fighters(reg_path)
print(f"Total rows found: {len(all_fighters)}")
for f in all_fighters[:20]:
    print(f)

print("\n=== SWISS FIGHTERS ===")
swiss = get_swiss_fighters(reg_path)
print(f"Total Swiss found: {len(swiss)}")
for f in swiss:
    print(f)

print("\n=== SCHEDULE (first 30 entries) ===")
schedule = extract_schedule(sched_path)
print(f"Total schedule entries found: {len(schedule)}")
for s in schedule[:30]:
    print(f"  time={s['time']}-{s.get('time_end','')}  tatami={s['tatami']}  code={s['category_code']}  phase={s.get('phase','')}")
