#!/usr/bin/env python3
"""
Meta Ads Analytics Layer — Pocket FM Tamil Creative Intelligence
Fetches raw metrics from Meta Graph API and computes custom KPIs.

Custom metrics:
  hookRate        = 3s_video_plays / impressions × 100
  thruplRate      = thruplay_watched / impressions × 100
  completionRate  = p100_watched / impressions × 100
  actPerMille     = activations / impressions × 1000
  normCpi         = cpc × (benchmark_ctr / actual_ctr)   [proxy for creative quality]
  ses             = weighted(CTR 30%, Hook 25%, Thruplay 25%, CPM-efficiency 20%) vs benchmarks

Benchmarks auto-recomputed daily as rolling account averages.
"""

import os, re, json, urllib.request, urllib.parse, datetime

META_TOKEN    = os.environ.get('META_ACCESS_TOKEN', '')
GRAPH_VERSION = 'v20.0'
GRAPH_BASE    = f'https://graph.facebook.com/{GRAPH_VERSION}'

# MCP-enabled accounts — account_id → (display_name, language_filter or None)
# language_filter: if set, only include ads whose parsed language matches (case-insensitive)
ACCOUNT_MAP = {
    '1305834253717811': ('India Regional', 'Tamil'),
    '977245951121888':  ('South Testing',  'Tamil'),
}

# Fallback benchmarks — overwritten daily with live account averages
_DEFAULT_BENCHMARKS = {
    'ctr':      1.26,
    'hook':     24.7,
    'thruplay': 8.7,
    'cpm':      81.14,
}

AD_FIELDS = ','.join([
    'id', 'name', 'spend', 'impressions', 'reach', 'clicks',
    'ctr', 'cpc', 'cpm', 'frequency', 'objective',
    '3_second_video_plays',
    'video_thruplay_watched_actions',
    'video_p100_watched_actions',
    'video_p75_watched_actions',
    'video_p25_watched_actions',
    'cost_per_thruplay',
    'results',
    'cost_per_result',
])


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

def pnum(s):
    """Strip ₹, commas, %, whitespace → float."""
    if s is None or str(s).strip() in ('', '-', 'N/A'):
        return 0.0
    try:
        return float(re.sub(r'[^\d.]', '', str(s)))
    except Exception:
        return 0.0


def nested_val(obj):
    """Extract scalar from Meta's nested results/cost_per_result structure."""
    try:
        return float(obj['value'][0]['values'][0]['value'])
    except Exception:
        return 0.0


def parse_ad_name(name):
    """
    Parse Pocket FM's ad naming convention:
    date_country || platform || OS || campaign_type || audience ||
    language || show | local_name || budget || format || … || creative_label
    """
    parts = [p.strip() for p in name.split('||')]
    get   = lambda i: parts[i] if len(parts) > i else ''

    show_raw   = get(6).split('|')[0].strip()
    language   = get(5)
    fmt_tag    = get(8)            # FALSE / LA / Gen AI
    camp_type  = get(3)            # Scaling-CPS / Testing / etc.
    creative   = get(len(parts) - 1)

    # creative format: Script_Type_Lead_Gender
    cr         = creative.split('_')
    script     = cr[0].strip() if cr else creative
    cr_type    = cr[1].strip() if len(cr) > 1 else ''
    lead       = cr[2].strip() if len(cr) > 2 else ''

    return {
        'show':        show_raw,
        'language':    language,
        'campaignType': camp_type,
        'formatTag':   fmt_tag,
        'script':      script,
        'creativeType': cr_type,
        'lead':        lead,
    }


# ─────────────────────────────────────────────
#  CUSTOM METRIC COMPUTATION
# ─────────────────────────────────────────────

def compute_ses(ctr, hook, thruplay, cpm, benchmarks):
    """
    Script Efficiency Score (0–100).
    Each dimension scored relative to account benchmark, then weighted.
    """
    def vs(val, bench, higher_better=True, cap=150):
        if bench <= 0:
            return 50.0
        r = val / bench * 100.0
        return min(r if higher_better else max(0, 200 - r), cap)

    ctr_s  = vs(ctr,      benchmarks['ctr'],      True)
    hook_s = vs(hook,     benchmarks['hook'],      True)
    thru_s = vs(thruplay, benchmarks['thruplay'],  True)
    cpm_s  = vs(cpm,      benchmarks['cpm'],       False)   # lower CPM = better

    raw = ctr_s * 0.30 + hook_s * 0.25 + thru_s * 0.25 + cpm_s * 0.20
    return round(min(raw, 100.0), 1)


def process_ad(e, account_name, benchmarks):
    """Apply business logic to one raw Meta ad dict."""
    imp   = pnum(e.get('impressions'))
    if imp == 0:
        return None

    sp3   = pnum(e.get('3_second_video_plays'))
    thru  = pnum(e.get('video_thruplay_watched_actions'))
    p100  = pnum(e.get('video_p100_watched_actions'))
    p75   = pnum(e.get('video_p75_watched_actions'))
    ctr   = pnum(e.get('ctr'))
    cpm   = pnum(e.get('cpm'))
    cpc   = pnum(e.get('cpc'))
    spend = pnum(e.get('amount_spent') or e.get('spend'))
    freq  = pnum(e.get('frequency'))
    acts  = nested_val(e.get('results', {}))

    hook_rate   = round(sp3  / imp * 100, 1)
    thru_rate   = round(thru / imp * 100, 1)
    comp_rate   = round(p100 / imp * 100, 2)
    apm         = round(acts / imp * 1000, 3)
    norm_cpi    = round(cpc * (benchmarks['ctr'] / ctr), 2) if ctr > 0 else 0
    ses         = compute_ses(ctr, hook_rate, thru_rate, cpm, benchmarks)

    meta = parse_ad_name(e.get('name', ''))

    return {
        'id':           e.get('id', ''),
        'account':      account_name,
        'show':         meta['show'],
        'language':     meta['language'],
        'campaignType': meta['campaignType'],
        'script':       meta['script'],
        'format':       meta['formatTag'],
        'lead':         meta['lead'],
        # Raw
        'spend':        round(spend, 0),
        'impressions':  int(imp),
        'ctr':          round(ctr, 2),
        'cpm':          round(cpm, 2),
        'cpc':          round(cpc, 2),
        'frequency':    round(freq, 2),
        'activations':  int(acts),
        # Custom KPIs
        'hookRate':       hook_rate,
        'thruplRate':     thru_rate,
        'completionRate': comp_rate,
        'actPerMille':    apm,
        'normCpi':        norm_cpi,
        'ses':            ses,
        'fatigue':        freq > 2.5,
    }


def recompute_benchmarks(ads):
    """Live rolling benchmarks from current ad cohort."""
    def avg(lst):
        filt = [x for x in lst if x > 0]
        return round(sum(filt) / len(filt), 2) if filt else 0.0

    return {
        'ctr':      avg([a['ctr']      for a in ads]),
        'hook':     avg([a['hookRate'] for a in ads]),
        'thruplay': avg([a['thruplRate'] for a in ads]),
        'cpm':      avg([a['cpm']      for a in ads]),
    }


# ─────────────────────────────────────────────
#  META GRAPH API FETCH
# ─────────────────────────────────────────────

def fetch_ads_graph(account_id, date_preset='last_30d'):
    """Fetch ad-level insights from Meta Graph API with cursor pagination."""
    if not META_TOKEN:
        return []

    params = urllib.parse.urlencode({
        'level':        'ad',
        'fields':       AD_FIELDS,
        'date_preset':  date_preset,
        'limit':        100,
        'sort':         'spend_descending',
        'access_token': META_TOKEN,
    })
    url = f'{GRAPH_BASE}/act_{account_id}/insights?{params}'
    all_ads = []

    while url:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                resp = json.loads(r.read())
        except Exception as e:
            print(f'    Graph API error ({account_id}): {e}')
            break

        if 'error' in resp:
            print(f'    Meta API error: {resp["error"].get("message", resp["error"])}')
            break

        all_ads.extend(resp.get('data', []))
        url = resp.get('paging', {}).get('next')  # follow pagination

    return all_ads


# ─────────────────────────────────────────────
#  MAIN ENTRY POINT
# ─────────────────────────────────────────────

def generate_analytics_data(date_preset='last_30d'):
    """
    Fetch + process all MCP-enabled accounts.
    Returns (ads_list, benchmarks_dict, summary_dict).
    """
    if not META_TOKEN:
        print('  META_ACCESS_TOKEN not set — skipping Meta analytics')
        return [], _DEFAULT_BENCHMARKS, {}

    # First pass: fetch raw data
    raw_all = []
    for acc_id, (acc_name, lang_filter) in ACCOUNT_MAP.items():
        print(f'  Fetching {acc_name}{" (Tamil only)" if lang_filter else ""}...')
        raw = fetch_ads_graph(acc_id, date_preset)
        print(f'    {len(raw)} raw ads received')
        # Apply language filter if specified (match against parsed ad name)
        if lang_filter:
            raw = [e for e in raw
                   if lang_filter.lower() in parse_ad_name(e.get('name', ''))['language'].lower()]
            print(f'    {len(raw)} ads after {lang_filter} filter')
        raw_all.append((acc_name, raw))

    if not any(r for _, r in raw_all):
        print('  No data received from Meta')
        return [], _DEFAULT_BENCHMARKS, {}

    # First-pass process with default benchmarks to compute live ones
    temp = []
    for acc_name, raw in raw_all:
        for e in raw:
            result = process_ad(e, acc_name, _DEFAULT_BENCHMARKS)
            if result:
                temp.append(result)

    if not temp:
        return [], _DEFAULT_BENCHMARKS, {}

    # Recompute live benchmarks from actual data
    live_benchmarks = recompute_benchmarks(temp)

    # Second pass: re-score SES against live benchmarks
    ads = []
    for acc_name, raw in raw_all:
        for e in raw:
            result = process_ad(e, acc_name, live_benchmarks)
            if result:
                ads.append(result)

    ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    summary = {
        'totalSpend':  round(sum(a['spend'] for a in ads), 0),
        'totalAds':    len(ads),
        'avgCtr':      live_benchmarks['ctr'],
        'avgHook':     live_benchmarks['hook'],
        'avgThruplay': live_benchmarks['thruplay'],
        'avgCpm':      live_benchmarks['cpm'],
        'datePreset':  date_preset,
        'fetchedAt':   datetime.datetime.now(ist).strftime('%d %b %Y, %I:%M %p IST'),
    }

    print(f'  Meta: {len(ads)} ads processed · ₹{summary["totalSpend"]:,.0f} total spend')
    return ads, live_benchmarks, summary
