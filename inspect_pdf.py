"""
Low-level PDF inspector – shows exactly what pdfplumber sees.
Usage:  python3 inspect_pdf.py <file.pdf>
"""
import sys
import pdfplumber

path = sys.argv[1]

with pdfplumber.open(path) as pdf:
    print(f"Pages: {len(pdf.pages)}\n")
    for i, page in enumerate(pdf.pages[:3]):   # first 3 pages only
        print(f"{'='*60}")
        print(f"PAGE {i+1}")
        print(f"{'='*60}")

        tables = page.extract_tables()
        print(f"  Tables found: {len(tables)}")
        for t_idx, table in enumerate(tables):
            print(f"  -- Table {t_idx+1} ({len(table)} rows) --")
            for row in table[:8]:
                print("   ", row)

        print(f"\n  Raw text (first 1500 chars):")
        text = page.extract_text() or ""
        print(text[:1500])
        print()
