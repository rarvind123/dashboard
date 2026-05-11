#!/usr/bin/env python3
"""
Daily refresh script — Tamil Creative Promo Dashboard
Runs automatically via GitHub Actions at 9:00 AM IST.

What this updates:
  - Asset Pipeline tab  → pulls live status/dates from "Asset production pipeline" sheet
  - Script metrics      → updates CPI, activation, playtime from "Script cracking status" sheet
                          (fire tips and status labels are preserved from the HTML)
  - Timestamp           → last-updated header in the dashboard

What is NOT auto-updated (needs manual edits via Claude Code):
  - Powerstarts / Microdramas — web scraped, stub below
  - Social formats
  - Fire tips / analysis text
  - Script status (Scaling Now / Scaling Next / Observation)
"""

import urllib.request, urllib.parse, csv, io, re, json, datetime

SHEET_ID = '1LA12_fh6jiLY15awit6yi8UEF1iXycZOD84E3yPUG3Y'


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

def fetch_sheet(sheet_name):
    """
    Fetch a Google Sheet tab as a list of dicts.
    Auto-detects the real header row (first row with ≥4 non-empty cells)
    to handle sheets that have blank/merged top rows.
    """
    url = (
        f'https://docs.google.com/spreadsheets/d/{SHEET_ID}'
        f'/gviz/tq?tqx=out:csv&sheet={urllib.parse.quote(sheet_name)}'
    )
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode('utf-8')

    all_rows = list(csv.reader(io.StringIO(text)))
    if not all_rows:
        return []

    # Find first row that looks like a real header (≥4 filled cells)
    header_idx = 0
    for i, row in enumerate(all_rows):
        if sum(1 for c in row if c.strip()) >= 4:
            header_idx = i
            break

    headers = [h.strip() for h in all_rows[header_idx]]
    data_rows = all_rows[header_idx + 1:]
    result = []
    for row in data_rows:
        padded = row + [''] * (len(headers) - len(row))
        d = {headers[i]: padded[i].strip() for i in range(len(headers))}
        if any(d.values()):
            result.append(d)
    return result


def to_int(val, default=0):
    try:
        return int(float(re.sub(r'[^0-9.\-]', '', str(val)) or default))
    except:
        return default


def to_secs(val):
    """Convert '52 Sec', '1.03 Mins', '27 mins', '30 Sec' etc. → integer seconds."""
    val = str(val).strip()
    if not val:
        return 0
    try:
        if re.search(r'min', val, re.I):
            m = re.search(r'[\d.]+', val)
            return int(float(m.group()) * 60) if m else 0
        else:
            m = re.search(r'[\d.]+', val)
            return int(float(m.group())) if m else 0
    except:
        return 0


def js_array(data):
    """Dump a Python list as a pretty JavaScript array literal."""
    lines = json.dumps(data, indent=2, ensure_ascii=False).splitlines()
    # json.dumps uses " for keys — convert to JS unquoted keys for cleanliness
    result = []
    for line in lines:
        m = re.match(r'^(\s+)"([a-zA-Z_][a-zA-Z0-9_]*)": (.*)', line)
        if m:
            result.append(f'{m.group(1)}{m.group(2)}: {m.group(3)}')
        else:
            result.append(line)
    return '\n'.join(result)


def replace_js_const(html, const_name, new_array_js):
    """Replace `const NAME = [...];` in the HTML with new_array_js."""
    pattern = rf'const {re.escape(const_name)} = \[[\s\S]*?\];'
    replacement = f'const {const_name} = {new_array_js};'
    updated, count = re.subn(pattern, replacement, html, count=1)
    if count == 0:
        print(f'  WARNING: Could not find "const {const_name}" in HTML')
    return updated


# ─────────────────────────────────────────────
#  PIPELINE UPDATE
# ─────────────────────────────────────────────

def update_pipeline(html):
    print('Fetching Asset production pipeline...')
    try:
        rows = fetch_sheet('Asset production pipeline')
        items = []
        for row in rows:
            show = row.get('Show', '').strip()
            status = row.get('Status', '').strip()
            script = row.get('Script Name', '').strip()
            if not show or not script:
                continue
            items.append({
                'show':      show,
                'script':    script,
                'format':    row.get('Visual format', '').strip(),
                'duration':  row.get('Duration', '').strip(),
                'mode':      'inhouse' if 'in house' in row.get('Mode', '').lower() else 'vendor',
                'vendor':    row.get('Vendor name', '').strip(),
                'ppmDate':   row.get('PPM Date', '').strip(),
                'startDate': row.get('Start Date', '').strip(),
                'eta':       row.get('ETA', '').strip(),
                'status':    status if status else 'WIP',
                'notes':     row.get('Base CPI', row.get('Notes', '')).strip(),
            })
        html = replace_js_const(html, 'pipeline', js_array(items))
        print(f'  Pipeline: {len(items)} rows updated')
    except Exception as e:
        print(f'  Pipeline update FAILED: {e}')
    return html


# ─────────────────────────────────────────────
#  SCRIPTS METRICS UPDATE
#  (preserves fire tips and status labels)
# ─────────────────────────────────────────────

def update_scripts_metrics(html):
    print('Fetching Script cracking status...')
    try:
        rows = fetch_sheet('Script cracking status')

        # Build lookup: (SHOW_UPPER, SCRIPT_UPPER) → {cpi, activation, playtime}
        # We do NOT update status or fire tips — those are manually curated.
        lookup = {}
        for row in rows:
            show   = row.get('Show', '').strip()
            script = row.get('Script Name', '').strip()
            if not show or not script:
                continue

            # Handle both "Activation %" and "Activation" column names
            activation_raw = (
                row.get('Activation %', '') or
                row.get('Activation', '') or
                row.get('Activa', '')  # truncated header fallback
            )

            lookup[(show.upper(), script.upper())] = {
                'cpi':        to_int(row.get('CPI', 0)),
                'activation': to_int(activation_raw),
                'playtime':   to_secs(row.get('Avg Play Time', '0')),
            }

        updated = 0

        def patch_item(m):
            nonlocal updated
            item = m.group(0)
            show_m   = re.search(r'show:\s*"([^"]+)"', item)
            script_m = re.search(r'script:\s*"([^"]+)"', item)
            if not show_m or not script_m:
                return item
            key = (show_m.group(1).upper(), script_m.group(1).upper())
            if key not in lookup:
                return item
            d = lookup[key]
            item = re.sub(r'cpi:\s*\d+',        f'cpi:{d["cpi"]}',        item)
            item = re.sub(r'activation:\s*\d+',  f'activation:{d["activation"]}', item)
            item = re.sub(r'playtime:\s*\d+',    f'playtime:{d["playtime"]}', item)
            updated += 1
            return item

        # Match individual JS object literals inside the scripts array
        html = re.sub(r'\{\s*show:[^{}]+\}', patch_item, html)
        print(f'  Scripts: {updated} items updated (fire tips + status preserved)')
    except Exception as e:
        print(f'  Scripts update FAILED: {e}')
    return html


# ─────────────────────────────────────────────
#  TIMESTAMP UPDATE
# ─────────────────────────────────────────────

def update_timestamp(html):
    ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    now = datetime.datetime.now(ist)
    ts  = now.strftime('%-d %b %Y, %-I:%M %p IST')
    # Replace wherever the timestamp appears in the header
    html = re.sub(r'Last updated:[^<\n]*', f'Last updated: {ts}', html)
    return html


# ─────────────────────────────────────────────
#  POWERSTARTS STUB  (web scraping — TO BUILD)
# ─────────────────────────────────────────────

def update_powerstarts(html):
    """
    TODO: scrape trending powerstarts from the web and update the
    `const powerstarts = [...]` array.

    Source to confirm with Arvind — likely a Pocket FM internal page or
    analytics tool. Once the URL/source is known, add scraping logic here.
    """
    print('  Powerstarts: skipped (web scraping not yet implemented)')
    return html


def update_microdramas(html):
    """
    TODO: same as powerstarts — source URL needed.
    """
    print('  Microdramas: skipped (web scraping not yet implemented)')
    return html


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    print('=== Tamil Creative Dashboard Refresh ===')
    print(f'Started at: {datetime.datetime.now()}')
    print()

    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()
    original_len = len(html)

    html = update_pipeline(html)
    html = update_scripts_metrics(html)
    html = update_powerstarts(html)
    html = update_microdramas(html)
    html = update_timestamp(html)

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)

    print()
    print(f'Done. HTML size: {original_len:,} → {len(html):,} chars')
    print('index.html saved.')


if __name__ == '__main__':
    main()
