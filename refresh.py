#!/usr/bin/env python3
"""
Daily refresh script for Tamil Creative Promo Dashboard.
Fetches data from Google Sheet and rebuilds index.html.
"""
import os, json, csv, io, urllib.request, re, datetime

SHEET_ID = os.environ.get('SHEET_ID', '1LA12_fh6jiLY15awit6yi8UEF1iXycZOD84E3yPUG3Y')

def fetch_sheet_csv(gid):
    url = f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={gid}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode('utf-8'))))

def update_timestamp(html):
    now = datetime.datetime.now().strftime('%d %b %Y, %I:%M %p IST')
    # Replace the last-updated text in the shell header
    html = re.sub(
        r'Last updated:.*?(?=<)',
        f'Last updated: {now}',
        html
    )
    return html

def main():
    print('Reading current index.html...')
    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()

    # Update the last-refreshed timestamp
    html = update_timestamp(html)

    # TODO: Add more data-refresh logic here as the sheet structure is confirmed.
    # For now, this script just updates the timestamp so the team knows when
    # GitHub Actions last ran successfully.

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)

    print('Done. index.html updated.')

if __name__ == '__main__':
    main()
