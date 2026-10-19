"""Проверить клики во всех роликах: python3 scripts/promo/check_clicks.py [embed forms …]"""
import importlib
import sys

import engine

NAMES = sys.argv[1:] or ["embed", "forms", "interactive", "library", "logo", "webhook"]
bad = 0
for name in NAMES:
    v = importlib.import_module(f"video_{name}").build()
    problems = engine.check_clicks(v)
    print(f"{name}: кликов {len(v.clicks)}, промахов {len(problems)}")
    for p in problems:
        print("   ", *p)
    bad += len(problems)
sys.exit(1 if bad else 0)
