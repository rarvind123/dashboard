#!/usr/bin/env python3
"""
Daily refresh script — Tamil Creative Promo Dashboard
Runs automatically via GitHub Actions at 9:00 AM IST.

What this updates:
  - Asset Pipeline tab  → pulls live status/dates from "Asset production pipeline" sheet
  - Script metrics      → rebuilds scripts array from "Script cracking status" sheet
  - Trending Powerstarts   → Claude generates 10 fresh entries from real Indian news RSS
  - Microdrama Powerstarts → Claude generates 10 fresh entries from current Indian platforms
  - Social Media Formats   → Claude generates 8 fresh viral Tamil reel formats
  - Meta Creative Intel → fetches Meta Graph API → computes Hook Rate, Thruplay, SES scores
  - newToday registry   → updated to today's date with all new IDs / titles
  - Timestamp           → last-updated header in the dashboard

Secrets required in GitHub Actions:
  - SHEET_ID            → Google Sheet ID
  - GOOGLE_CREDENTIALS  → Google service account JSON
  - ANTHROPIC_API_KEY   → Anthropic API key (for Claude content generation)
  - META_ACCESS_TOKEN   → Meta long-lived user/system access token
"""

import urllib.request, urllib.parse, csv, io, re, json, datetime, os
import xml.etree.ElementTree as ET

SHEET_ID          = os.environ.get('SHEET_ID', '1LA12_fh6jiLY15awit6yi8UEF1iXycZOD84E3yPUG3Y')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
META_ACCESS_TOKEN = os.environ.get('META_ACCESS_TOKEN', '')
IST               = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

def fetch_sheet(sheet_name):
    """Fetch a Google Sheet tab as a list of dicts."""
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

    header_idx = 0
    for i, row in enumerate(all_rows):
        if sum(1 for c in row if c.strip()) >= 4:
            header_idx = i
            break

    headers   = [h.strip() for h in all_rows[header_idx]]
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
    except Exception:
        return default


def to_secs(val):
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
    except Exception:
        return 0


def js_array(data):
    """Dump a Python list as a pretty JavaScript array literal (unquoted keys)."""
    lines = json.dumps(data, indent=2, ensure_ascii=False).splitlines()
    result = []
    for line in lines:
        m = re.match(r'^(\s+)"([a-zA-Z_][a-zA-Z0-9_]*)": (.*)', line)
        if m:
            result.append(f'{m.group(1)}{m.group(2)}: {m.group(3)}')
        else:
            result.append(line)
    return '\n'.join(result)


def replace_js_const(html, const_name, new_array_js):
    """Replace `const NAME = [...];` in the HTML."""
    pattern     = rf'const {re.escape(const_name)} = \[[\s\S]*?\];'
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
        rows  = fetch_sheet('Asset production pipeline')
        items = []
        for row in rows:
            show   = row.get('Show', '').strip()
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
#  SCRIPTS FULL REBUILD
#  (index-based parsing handles duplicate column headers)
# ─────────────────────────────────────────────

# Column indices in "Script cracking status"
_COL_SHOW       = 0
_COL_TYPE       = 1
_COL_SCRIPT     = 2
_COL_FORMAT     = 3
_COL_LEAD       = 4
_COL_NEXT_STEP  = 6
_COL_TEST_CPI   = 8
_COL_PLAYTIME   = 9
_COL_TEST_ACT   = 10
_COL_CPS_CPI    = 14
_COL_CPS_ACT    = 15

def fetch_sheet_raw(sheet_name):
    """Fetch a sheet as raw list-of-lists (handles duplicate column headers)."""
    url = (
        f'https://docs.google.com/spreadsheets/d/{SHEET_ID}'
        f'/gviz/tq?tqx=out:csv&sheet={urllib.parse.quote(sheet_name)}'
    )
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode('utf-8')
    return list(csv.reader(io.StringIO(text)))


def _extract_fire_tips(html):
    """Pull existing (script_name_upper → fire_text) from the scripts const."""
    tips = {}
    block = re.search(r'const scripts = \[([\s\S]*?)\];', html)
    if not block:
        return tips
    for m in re.finditer(
        r'script:"([^"]+)"[^}]*?fire:"((?:[^"\\]|\\.)*)"',
        block.group(1),
    ):
        tips[m.group(1).strip().upper()] = m.group(2)
    return tips


def update_scripts_metrics(html):
    print('Fetching Script cracking status (full rebuild)...')
    try:
        all_rows = fetch_sheet_raw('Script cracking status')

        # Find header row
        header_idx = 0
        for i, row in enumerate(all_rows):
            if sum(1 for c in row if c.strip()) >= 4:
                header_idx = i
                break
        data_rows = all_rows[header_idx + 1:]

        # Preserve manually written fire tips
        fire_tips = _extract_fire_tips(html)

        items = []
        for row in data_rows:
            row = row + [''] * max(0, 22 - len(row))   # pad short rows
            show   = row[_COL_SHOW].strip()
            script = row[_COL_SCRIPT].strip()
            if not show or not script:
                continue

            stype     = row[_COL_TYPE].strip()
            fmt       = row[_COL_FORMAT].strip()
            lead      = row[_COL_LEAD].strip()
            next_step = row[_COL_NEXT_STEP].strip()
            is_genai  = bool(re.search(r'gen.?ai', next_step, re.I))

            test_cpi  = to_int(row[_COL_TEST_CPI])
            playtime  = to_secs(row[_COL_PLAYTIME])
            test_act  = to_int(row[_COL_TEST_ACT])
            cps_cpi   = to_int(row[_COL_CPS_CPI])
            cps_act   = to_int(row[_COL_CPS_ACT])

            if cps_cpi > 0:
                status = 'Scaling Now'
                cps    = True
                cpi    = cps_cpi
                act    = cps_act
            elif is_genai:
                status = 'Scaling Next'
                cps    = False
                cpi    = test_cpi
                act    = test_act
            else:
                status = 'Observation'
                cps    = False
                cpi    = test_cpi
                act    = test_act

            item = {
                'show':       show,
                'script':     script,
                'type':       stype,
                'format':     fmt,
                'promoLead':  lead,
                'nextStep':   'Gen AI' if is_genai else '',
                'status':     status,
                'cpi':        cpi,
                'activation': act,
                'playtime':   playtime,
                'cps':        cps,
            }

            fire = fire_tips.get(script.strip().upper(), '')
            if fire:
                item['fire'] = fire

            items.append(item)

        html = replace_js_const(html, 'scripts', js_array(items))
        genai_count = sum(1 for it in items if it.get('nextStep') == 'Gen AI')
        print(f'  Scripts: {len(items)} items rebuilt · {genai_count} flagged ⚡ Gen AI')
    except Exception as e:
        print(f'  Scripts update FAILED: {e}')
        import traceback; traceback.print_exc()
    return html


# ─────────────────────────────────────────────
#  TIMESTAMP UPDATE
# ─────────────────────────────────────────────

def update_timestamp(html):
    now = datetime.datetime.now(IST)
    ts  = now.strftime('%-d %b %Y, %-I:%M %p IST')
    html = re.sub(r'Last updated:[^<\n]*', f'Last updated: {ts}', html)
    return html


# ─────────────────────────────────────────────
#  NEWS RSS FETCHER
# ─────────────────────────────────────────────

def fetch_news_headlines():
    """Fetch recent Indian news headlines from public RSS feeds."""
    feeds = [
        ('NDTV India',    'https://feeds.feedburner.com/ndtvnews-india-news'),
        ('TOI India',     'https://timesofindia.indiatimes.com/rssfeeds/296589292.cms'),
        ('HT India',      'https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml'),
        ('The Hindu',     'https://www.thehindu.com/news/national/?service=rss'),
        ('India Today',   'https://www.indiatoday.in/rss/1206614'),
    ]
    headlines = []
    for name, url in feeds:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as r:
                content = r.read().decode('utf-8', errors='replace')
            root  = ET.fromstring(content)
            count = 0
            for item in root.findall('.//item')[:12]:
                title = item.findtext('title', '').strip()
                desc  = re.sub(r'<[^>]+>', '', item.findtext('description', '')).strip()[:200]
                if title and len(title) > 15:
                    headlines.append(f"{title}. {desc}" if desc else title)
                    count += 1
            print(f'  {name}: {count} headlines')
        except Exception as e:
            print(f'  {name}: fetch failed ({e})')
    return headlines[:50]


# ─────────────────────────────────────────────
#  CLAUDE API
# ─────────────────────────────────────────────

def call_claude(system_prompt, user_prompt, max_tokens=4096):
    """Call Claude via OpenRouter (OpenAI-compatible) — no SDK dependency."""
    if not ANTHROPIC_API_KEY:
        print('  ANTHROPIC_API_KEY not set — skipping AI generation')
        return None
    payload = json.dumps({
        'model':      'anthropic/claude-haiku-4-5-20251001',
        'max_tokens': max_tokens,
        'messages':   [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user',   'content': user_prompt},
        ],
    }).encode()
    req = urllib.request.Request(
        'https://openrouter.ai/api/v1/chat/completions',
        data=payload,
        headers={
            'Authorization': f'Bearer {ANTHROPIC_API_KEY}',
            'Content-Type':  'application/json',
            'HTTP-Referer':  'https://rarvind123.github.io/dashboard/',
            'X-Title':       'Pocket FM Tamil Dashboard',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read())['choices'][0]['message']['content']
    except urllib.error.HTTPError as e:
        print(f'  OpenRouter HTTP {e.code}: {e.read().decode()[:300]}')
    except Exception as e:
        print(f'  OpenRouter error: {e}')
    return None


def extract_json(text):
    """Pull the first JSON array from a Claude response."""
    if not text:
        return None
    # Strip markdown fences
    text = re.sub(r'^```(?:json)?\n?', '', text.strip(), flags=re.MULTILINE)
    text = re.sub(r'\n?```\s*$', '', text.strip(), flags=re.MULTILINE)
    m = re.search(r'\[[\s\S]*\]', text)
    if not m:
        print('  No JSON array found in response')
        return None
    try:
        return json.loads(m.group())
    except json.JSONDecodeError as e:
        print(f'  JSON decode error: {e}')
        return None


# ─────────────────────────────────────────────
#  TRENDING POWERSTARTS
# ─────────────────────────────────────────────

_PS_SYSTEM = """You are a creative content strategist for Pocket FM Tamil audio dramas.
Generate "trending powerstart" entries — emotionally extreme real-world Indian stories formatted as cinematic promo hooks.

Return ONLY a valid JSON array. No commentary, no markdown, no explanation outside the array.

Each entry must have exactly these fields (all strings unless noted):
  id          – integer, sequential from 1
  headline    – punchy news headline, max 15 words
  emotion     – exactly one of: betrayal | sacrifice | love | shock | identity | desperation | redemption | greed
  emotionLabel – readable label e.g. "Betrayal" or "Sacrifice / Loss"
  what        – 2–3 sentences describing the real incident (location, people, what happened)
  angle       – cinematic scene in present tense: describe exactly what camera sees beat-by-beat; must end with <em>"tagline in double quotes."</em>
  hookOpener  – 1–2 lines of colloquial Tamil in Tamil script (not formal/literary)
  shows       – JSON array, subset of ["vvk","indrajith","trot"] matching the story tone
  fire        – 4–6 sentences of specific creative direction for a Pocket FM promo

Show guide: vvk = hero rise/revenge, indrajith = crime/betrayal/thriller, trot = sacrifice/love/family."""


def update_powerstarts(html, headlines):
    print('Generating trending powerstarts via Claude...')
    if not ANTHROPIC_API_KEY:
        print('  Skipped (no ANTHROPIC_API_KEY)')
        return html, []

    today = datetime.datetime.now(IST).strftime('%Y-%m-%d')
    hl    = '\n'.join(f'- {h}' for h in headlines) if headlines else '(no RSS headlines available)'

    user_prompt = f"""Today is {today}.

Recent Indian news headlines (past 7 days):
{hl}

Generate 10 trending powerstart entries.
Rules:
- Adapt the most emotionally extreme stories from the headlines above
- If headlines lack variety or emotional depth, supplement with your knowledge of real Indian news / viral incidents from this week
- Every story must feel current — dated around {today}
- Cover different emotion types across the 10 entries (no two entries with the same emotion)
- Tamil hookOpener must be authentic colloquial Tamil (Chennai / Tamil Nadu register)
- The angle field MUST describe a specific opening visual (not generic) and end with <em>"tagline"</em>
- Do NOT reuse these headlines already in the dashboard: ASHA heatwave worker, Gulf returnee with ₹500, 103-year-old voter, auto driver UPSC daughter, AI romance bot

Return ONLY the JSON array."""

    raw  = call_claude(_PS_SYSTEM, user_prompt, max_tokens=4096)
    data = extract_json(raw)

    if not data or not isinstance(data, list):
        print('  Powerstarts: no usable data — keeping existing content')
        return html, []

    for i, item in enumerate(data, 1):
        item['id'] = i

    html = replace_js_const(html, 'powerstarts', js_array(data))
    ids  = [item['id'] for item in data]
    print(f'  Powerstarts: {len(data)} entries updated')
    return html, ids


# ─────────────────────────────────────────────
#  MICRODRAMA POWERSTARTS
# ─────────────────────────────────────────────

_MD_SYSTEM = """You are a creative content analyst tracking trending Indian microdrama shows.
Generate microdrama powerstart entries covering the latest shows on Indian short-drama platforms.

Return ONLY a valid JSON array. No commentary, no markdown, no explanation outside the array.

Each entry must have exactly these fields:
  platform      – one of: kuku | tadka | flicktv | quicktv | reelsaga | fatafat | storytv
  platformLabel – full display name e.g. "Kuku TV" / "Tadka (JioHotstar)" / "Flick TV" / "QuickTV" / "ReelSaga" / "MX Fatafat" / "Story TV"
  title         – show title (can be real or plausible for the platform)
  subtitle      – one-sentence show premise
  genre         – e.g. "Revenge · Corporate" or "Romance · Identity"
  category      – one of: romance | revenge | return | dark
  hooks         – JSON array of 2–3 short hook labels e.g. ["Secret identity","Class tension"]
  powerstart    – 3–5 sentence cinematic episode-1 hook; must end with <em>"tagline in quotes."</em>
  why           – 3–4 sentences: why this hook / show / platform matters for Pocket FM Tamil teams
  fire          – 4–6 sentences: specific creative steal — what technique from this show can Pocket FM promo teams use directly

Platform notes: Kuku TV (Hindi social dramas), Tadka on JioHotstar (rural revenge), Flick TV (urban romance/corporate), QuickTV (thriller/mystery), ReelSaga (regional youth), MX Fatafat (free Bollywood cast), Story TV (women-first revenge/sacrifice)."""


def update_microdramas(html):
    print('Generating microdrama powerstarts via Claude...')
    if not ANTHROPIC_API_KEY:
        print('  Skipped (no ANTHROPIC_API_KEY)')
        return html, []

    today = datetime.datetime.now(IST).strftime('%Y-%m-%d')

    user_prompt = f"""Today is {today}.

Generate 10 microdrama powerstart entries covering shows currently trending on Indian microdrama platforms.

Rules:
- Cover at least 5 different platforms across the 10 entries
- Mix categories: include at least 2 romance, 3 revenge, 2 return/identity, 1 dark
- Each show's powerstart must describe a specific, vivid episode-1 opening — not a generic description
- The "why" field must explain what's strategically important about this show/format for Pocket FM Tamil promo teams
- The "fire" field must give a specific, immediately actionable creative steal
- Do NOT repeat these shows already in the dashboard: Waris Ki Ghar Wapsi, The Broken Billionaire, My Groom a Billionaire, Secret Billionaire, The Billionaire's Revenge, Mitti Ka Sher, Rukega Nahin Saala, Ek Anjaani Shaadi, The Final Boss, Happily Never After, Ghost in the Lift, Aamchi Crush, Ab Hoga Hisaab, Indian Institute of Zombies, Where Is My Child, Never Betray the Woman Who Built You, Dadi Detective Agency, DM For Destiny

Return ONLY the JSON array."""

    raw    = call_claude(_MD_SYSTEM, user_prompt, max_tokens=4096)
    data   = extract_json(raw)

    if not data or not isinstance(data, list):
        print('  Microdramas: no usable data — keeping existing content')
        return html, []

    html   = replace_js_const(html, 'microdramas', js_array(data))
    titles = [item.get('title', '') for item in data]
    print(f'  Microdramas: {len(data)} entries updated')
    return html, titles


# ─────────────────────────────────────────────
#  SOCIAL MEDIA POWERSTARTS
# ─────────────────────────────────────────────

_SM_SYSTEM = """You are a viral content strategist specialising in Tamil social media (Instagram Reels, YouTube Shorts, Facebook).
Generate "social media powerstart" entries — proven viral reel/short formats for Tamil Nadu audiences that Pocket FM promo teams can steal directly.

Return ONLY a valid JSON array. No commentary, no markdown, no explanation outside the array.

Each entry must have exactly these fields:
  id         – integer, sequential from 1
  name       – format name, 3–6 words, punchy e.g. "The Deadpan Narrator"
  platform   – one of: instagram | youtube | facebook
  emotion    – one of: drama | emotion | comedy
  whyViral   – 2–3 sentences explaining why this specific format drives watch-time/shares in Tamil Nadu audiences
  hookLine   – the exact Tamil hook line or format trigger (quoted, can mix Tamil and English)
  stealThis  – 3–4 sentences: specific instruction for how a Pocket FM Tamil promo editor steals this format today
  shows      – JSON array, subset of ["vvk","indrajith","trot","pks","kipi","cult"] — which Pocket FM shows fit
  emoji      – single emoji that best represents this format

Show guide: vvk=VVK, indrajith=Indrajith, trot=TROT, pks=PKS, kipi=KIPI, cult=Cult."""


def update_social_formats(html):
    print('Generating social media powerstarts via Claude...')
    if not ANTHROPIC_API_KEY:
        print('  Skipped (no ANTHROPIC_API_KEY)')
        return html

    today = datetime.datetime.now(IST).strftime('%Y-%m-%d')

    user_prompt = f"""Today is {today}.

Generate 8 social media powerstart entries for Tamil Nadu audiences.

Rules:
- Cover all three platforms: at least 3 Instagram, 2 YouTube, 2 Facebook (remaining your choice)
- Mix emotions: at least 2 drama, 2 emotion, 2 comedy
- Each format must be specific to how Tamil audiences currently consume content (May 2026 trends)
- hookLine should feel like something a real Tamil creator or narrator would say — mix of Tamil and English is fine
- stealThis must give a concrete, immediate action: "take [X scene], do [Y edit], post with [Z hook]"
- Do NOT repeat these formats already in the dashboard: The Deadpan Narrator, Silent Face Close-Up, Before Marriage / After Marriage, Middle Class Pain Moment, Auto Uncle Philosophy, Amma Scrolling, The Unannounced Return, One Dialogue Zero Context, Neenga Yaaru Same Person 2 Years Later, The Twist Title Card

Return ONLY the JSON array."""

    raw  = call_claude(_SM_SYSTEM, user_prompt, max_tokens=3000)
    data = extract_json(raw)

    if not data or not isinstance(data, list):
        print('  Social formats: no usable data — keeping existing content')
        return html

    for i, item in enumerate(data, 1):
        item['id'] = i

    html = replace_js_const(html, 'socialFormats', js_array(data))
    print(f'  Social formats: {len(data)} entries updated')
    return html


# ─────────────────────────────────────────────
#  newToday REGISTRY
# ─────────────────────────────────────────────

def update_newtoday(html, ps_ids, md_titles):
    """Stamp today's date and mark every new powerstart / microdrama as 'New Today'."""
    today      = datetime.datetime.now(IST).strftime('%Y-%m-%d')
    ids_js     = json.dumps(ps_ids)
    titles_js  = json.dumps(md_titles, ensure_ascii=False)
    replacement = (
        f"const newToday = {{\n"
        f"  date: '{today}',\n"
        f"  powerstartIds: {ids_js},\n"
        f"  microdramaTitles: {titles_js}\n"
        f"}};"
    )
    updated, count = re.subn(
        r'const newToday = \{[\s\S]*?\};',
        replacement,
        html,
        count=1,
    )
    if count == 0:
        print('  WARNING: Could not find newToday in HTML')
    else:
        print(f'  newToday: updated to {today}')
    return updated


# ─────────────────────────────────────────────
#  META CREATIVE INTEL
# ─────────────────────────────────────────────

def update_meta_analytics(html):
    print('Fetching Meta Ads analytics...')
    if not META_ACCESS_TOKEN:
        print('  META_ACCESS_TOKEN not set — skipping')
        return html
    try:
        import meta_analytics
        meta_analytics.META_TOKEN = META_ACCESS_TOKEN
        ads, benchmarks, summary = meta_analytics.generate_analytics_data('last_30d')
        if not ads:
            return html

        html = replace_js_const(html, 'metaAds', js_array(ads))

        bench_js   = json.dumps(benchmarks,  ensure_ascii=False)
        summary_js = json.dumps(summary,     ensure_ascii=False)

        html = re.sub(r'const metaBenchmarks\s*=\s*\{[^;]*\};',
                      f'const metaBenchmarks = {bench_js};', html)
        html = re.sub(r'const metaSummary\s*=\s*\{[^;]*\};',
                      f'const metaSummary = {summary_js};', html)

        print(f'  Meta: {len(ads)} ads · ₹{summary["totalSpend"]:,.0f} spend · '
              f'Hook {summary["avgHook"]}% · Thruplay {summary["avgThruplay"]}%')
    except Exception as e:
        print(f'  Meta analytics FAILED: {e}')
        import traceback; traceback.print_exc()
    return html


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    print('=== Tamil Creative Dashboard Refresh ===')
    print(f'Started at: {datetime.datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")}')
    print()

    with open('index.html', 'r', encoding='utf-8') as f:
        html = f.read()
    original_len = len(html)

    # Google Sheets sections
    html = update_pipeline(html)
    html = update_scripts_metrics(html)
    print()

    # Meta Creative Intel
    html = update_meta_analytics(html)
    print()

    # AI-generated content sections
    print('Fetching news headlines for powerstarts...')
    headlines    = fetch_news_headlines()
    print(f'  Total headlines: {len(headlines)}')
    print()

    html, ps_ids    = update_powerstarts(html, headlines)
    html, md_titles = update_microdramas(html)
    html            = update_social_formats(html)
    print()

    # Registry + timestamp
    html = update_newtoday(html, ps_ids, md_titles)
    html = update_timestamp(html)

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)

    print()
    print(f'Done. HTML size: {original_len:,} → {len(html):,} chars')
    print('index.html saved.')


if __name__ == '__main__':
    main()
