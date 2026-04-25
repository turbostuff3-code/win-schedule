#!/usr/bin/env python3
"""
森崎ウィン スケジュール自動取得スクリプト
公式サイトから出演情報を取得して events.json を更新します
"""

import json, re, sys, os, urllib.parse
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.error import URLError
from html.parser import HTMLParser

EVENTS_FILE = os.path.join(os.path.dirname(__file__), 'events.json')
LOG_FILE    = os.path.join(os.path.dirname(__file__), 'scraper.log')

# ─────────────────────────────────────────────
# Twitter / X 設定
# ─────────────────────────────────────────────
TW_BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
TW_ACCOUNTS = {
    "win_morisaki_": "1231818350977679360",   # 本人アカウント
    "staff_win":     None,                     # スタッフ（ID未取得）
}
# スケジュール関連キーワード（これを含むツイートのみ取り込む）
SCHEDULE_KEYWORDS = [
    '出演', '放送', '配信', '公開', '上映', 'ライブ', 'コンサート', '舞台',
    'ミュージカル', 'ラジオ', 'テレビ', 'TV', 'ドラマ', '映画', 'イベント',
    'フェス', '収録', 'レギュラー', '番組', '舞台挨拶', '握手', 'サイン会',
    'LIVE', 'CONCERT', 'TOUR', 'STAGE', 'RADIO', 'DRAMA', 'MOVIE',
    '月', '日', '時', '開場', '開演', '会場', 'チケット',
]

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; WinScheduleScraper/1.0)'}

# ─────────────────────────────────────────────
# カテゴリ判定
# ─────────────────────────────────────────────
def guess_category(text):
    t = text.lower()
    if any(k in t for k in ['ライブ','コンサート','tour','live','session','fes','フェス','武道館','zepp','billboard']):
        return 'stage'
    if any(k in t for k in ['舞台','ミュージカル','theatrical','theater','劇場']):
        return 'stage'
    if any(k in t for k in ['fm','radio','ラジオ','nhk-fm']):
        return 'radio'
    if any(k in t for k in ['映画','cinema','film','公開','ロードショー']):
        return 'movie'
    if any(k in t for k in ['イベント','握手','サイン','トーク','舞台挨拶','premiere']):
        return 'event'
    if any(k in t for k in ['tv','テレビ','ドラマ','番組','放送','nhk','フジ','tbs','日テレ','テレ朝','wowow','dmm','配信','zip','めざまし','mステ','cdtv']):
        return 'tv'
    if any(k in t for k in ['雑誌','magazine','anan','non-no','インタビュー','掲載','web']):
        return 'other'
    return 'other'

# ─────────────────────────────────────────────
# 日付パーサー
# ─────────────────────────────────────────────
DATE_PATTERNS = [
    r'(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日',
    r'(\d{4})[./\-](\d{1,2})[./\-](\d{1,2})',
    r'(\d{4})\s*\.?\s*(\d{1,2})\s*\.?\s*(\d{1,2})',
]

def extract_dates(text):
    """テキストから最初の日付(とあれば終了日)を返す (start, end|None)"""
    found = []
    for pat in DATE_PATTERNS:
        for m in re.finditer(pat, text):
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 2020 <= y <= 2030 and 1 <= mo <= 12 and 1 <= d <= 31:
                found.append(f'{y:04d}-{mo:02d}-{d:02d}')
    found = list(dict.fromkeys(found))  # 重複除去・順序保持
    if not found:
        return None, None
    return found[0], found[1] if len(found) > 1 else None

# ─────────────────────────────────────────────
# HTMLから全テキストブロックを抽出する汎用パーサー
# ─────────────────────────────────────────────
class BlockParser(HTMLParser):
    """h2/h3/h4/p/li タグのテキストをブロックごとに収集"""
    TARGET = {'h2','h3','h4','h5','p','li','dd','dt','span','a'}

    def __init__(self):
        super().__init__()
        self.blocks = []
        self._cur_tag = None
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag in self.TARGET:
            self._cur_tag = tag
            self._buf = []

    def handle_endtag(self, tag):
        if tag == self._cur_tag and self._buf:
            text = ' '.join(''.join(self._buf).split()).strip()
            if text:
                self.blocks.append((tag, text))
            self._cur_tag = None
            self._buf = []

    def handle_data(self, data):
        if self._cur_tag:
            self._buf.append(data)

def fetch_html(url):
    try:
        req = Request(url, headers=HEADERS)
        with urlopen(req, timeout=15) as r:
            raw = r.read()
            enc = r.headers.get_content_charset() or 'utf-8'
            return raw.decode(enc, errors='replace')
    except Exception as e:
        log(f'  [fetch error] {url}: {e}')
        return ''

# ─────────────────────────────────────────────
# スターダスト公式ページ
# ─────────────────────────────────────────────
def scrape_stardust():
    log('Stardust を取得中...')
    html = fetch_html('https://www.stardust.co.jp/talent/section1/morisakiwin/')
    if not html:
        return []

    parser = BlockParser()
    parser.feed(html)

    results = []
    blocks = parser.blocks
    i = 0
    while i < len(blocks):
        tag, text = blocks[i]
        # 日付を含むブロックを起点にする
        start, end = extract_dates(text)
        if start:
            # タイトルは前後ブロックから推測
            title = ''
            for j in range(max(0,i-3), i+1):
                if blocks[j][0] in ('h2','h3','h4'):
                    title = blocks[j][1]
            if not title:
                title = text[:60]

            # 会場・メモをまとめる
            notes = []
            for j in range(i, min(i+5, len(blocks))):
                notes.append(blocks[j][1])
            note_text = ' / '.join(dict.fromkeys(notes))

            if title and len(title) > 3:
                results.append({
                    'title': title,
                    'dateStart': start,
                    'dateEnd': end,
                    'venue': '',
                    'note': note_text[:200],
                    'category': guess_category(title + ' ' + note_text),
                    '_source': 'stardust',
                })
        i += 1
    log(f'  → {len(results)} 件候補')
    return results

# ─────────────────────────────────────────────
# コロムビア MEDIA ページ
# ─────────────────────────────────────────────
def scrape_columbia_media():
    log('Columbia Media を取得中...')
    html = fetch_html('https://columbia.jp/morisakiwin/media.html')
    if not html:
        return []

    parser = BlockParser()
    parser.feed(html)
    blocks = parser.blocks

    results = []
    i = 0
    while i < len(blocks):
        tag, text = blocks[i]
        if tag in ('h3', 'h4') and len(text) > 4:
            # タイトルっぽいブロック
            title = text
            note_parts = []
            date_start = date_end = None
            venue = ''

            # 直後のブロックから日付・詳細を収集
            for j in range(i+1, min(i+8, len(blocks))):
                t2 = blocks[j][1]
                if not date_start:
                    s, e = extract_dates(t2)
                    if s:
                        date_start, date_end = s, e
                if any(k in t2 for k in ['局','FM','TV','テレビ','NHK','フジ','TBS','WOWOW','DMM',
                                           'ラジオ','radio','Radio']):
                    venue = t2[:60]
                note_parts.append(t2)
                if blocks[j][0] in ('h2','h3') and j > i:
                    break

            if date_start:
                results.append({
                    'title': title,
                    'dateStart': date_start,
                    'dateEnd': date_end,
                    'venue': venue,
                    'note': ' / '.join(dict.fromkeys(note_parts))[:200],
                    'category': guess_category(title + ' ' + venue),
                    '_source': 'columbia_media',
                })
        i += 1
    log(f'  → {len(results)} 件候補')
    return results

# ─────────────────────────────────────────────
# コロムビア LIVE ページ
# ─────────────────────────────────────────────
def scrape_columbia_live():
    log('Columbia Live を取得中...')
    html = fetch_html('https://columbia.jp/morisakiwin/live.html')
    if not html:
        return []

    parser = BlockParser()
    parser.feed(html)
    blocks = parser.blocks

    results = []
    i = 0
    while i < len(blocks):
        tag, text = blocks[i]
        if tag in ('h2','h3','h4') and len(text) > 4:
            title = text
            date_start = date_end = None
            venue = ''
            note_parts = []

            for j in range(i+1, min(i+10, len(blocks))):
                t2 = blocks[j][1]
                if not date_start:
                    s, e = extract_dates(t2)
                    if s:
                        date_start, date_end = s, e
                if not venue and any(k in t2 for k in ['ホール','劇場','アリーナ','武道館','ドーム',
                                                        'Zepp','Billboard','会場','ライブ','フェス']):
                    venue = t2[:60]
                note_parts.append(t2)
                if blocks[j][0] in ('h2','h3') and j > i:
                    break

            if date_start:
                results.append({
                    'title': title,
                    'dateStart': date_start,
                    'dateEnd': date_end,
                    'venue': venue,
                    'note': ' / '.join(dict.fromkeys(note_parts))[:200],
                    'category': guess_category(title + ' ' + venue),
                    '_source': 'columbia_live',
                })
        i += 1
    log(f'  → {len(results)} 件候補')
    return results

# ─────────────────────────────────────────────
# 重複チェック・マージ
# ─────────────────────────────────────────────
def normalize_title(t):
    return re.sub(r'[\s　「」『』【】〔〕()（）!！?？・。、]', '', t).lower()

def is_duplicate(ev, existing):
    nt = normalize_title(ev['title'])
    for e in existing:
        if e.get('dateStart') == ev['dateStart'] and normalize_title(e.get('title','')) == nt:
            return True
        # タイトルが80%以上一致して日付が近い場合も重複とみなす
        if normalize_title(e.get('title','')) == nt:
            return True
    return False

def filter_valid(items):
    """今日以降の予定かつタイトルが意味のあるものだけ残す"""
    today = datetime.now().strftime('%Y-%m-%d')
    valid = []
    skip_titles = {'お問い合わせ','contact','プロフィール','ヒストリー','会社概要',
                   'menu','home','top','news','discography'}
    for item in items:
        if item['dateStart'] < '2026-01-01':
            continue
        nt = normalize_title(item['title'])
        if nt in skip_titles or len(item['title']) < 4:
            continue
        valid.append(item)
    return valid

# ─────────────────────────────────────────────
# ログ
# ─────────────────────────────────────────────
def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except:
        pass

# ─────────────────────────────────────────────
# Twitter / X
# ─────────────────────────────────────────────
def tw_get_guest_token():
    """Twitterのゲストトークンを取得"""
    req = Request(
        "https://api.twitter.com/1.1/guest/activate.json",
        data=b"",
        method="POST",
        headers={"Authorization": f"Bearer {TW_BEARER}",
                 "User-Agent": "Mozilla/5.0"},
    )
    with urlopen(req, timeout=10) as r:
        return json.load(r)["guest_token"]

def tw_get_user_id(screen_name, guest_token):
    """スクリーンネームからユーザーIDを取得"""
    variables = urllib.parse.quote(json.dumps({
        "screen_name": screen_name,
        "withSafetyModeUserFields": True,
    }))
    features = urllib.parse.quote(json.dumps({
        "hidden_profile_likes_enabled": True,
        "responsive_web_graphql_exclude_directive_enabled": True,
    }))
    url = (f"https://api.twitter.com/graphql/sLVLhk0bGj3MVFEKTdax1w/UserByScreenName"
           f"?variables={variables}&features={features}")
    req = Request(url, headers={
        "Authorization": f"Bearer {TW_BEARER}",
        "x-guest-token": guest_token,
        "User-Agent": "Mozilla/5.0",
    })
    with urlopen(req, timeout=10) as r:
        d = json.load(r)
    return d["data"]["user"]["result"]["rest_id"]

def tw_get_tweets(user_id, guest_token, count=40):
    """ユーザーIDからツイート一覧を取得"""
    variables = urllib.parse.quote(json.dumps({
        "userId": user_id,
        "count": count,
        "includePromotedContent": False,
        "withQuickPromoteEligibilityTweetFields": True,
        "withVoice": True,
        "withV2Timeline": True,
    }))
    features = urllib.parse.quote(json.dumps({
        "rweb_lists_timeline_redesign_enabled": True,
        "responsive_web_graphql_exclude_directive_enabled": True,
        "verified_phone_label_enabled": False,
        "creator_subscriptions_tweet_preview_api_enabled": True,
        "responsive_web_graphql_timeline_navigation_enabled": True,
        "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
        "tweetypie_unmention_optimization_enabled": True,
        "responsive_web_edit_tweet_api_enabled": True,
        "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
        "view_counts_everywhere_api_enabled": True,
        "longform_notetweets_consumption_enabled": True,
        "responsive_web_twitter_article_tweet_consumption_enabled": False,
        "tweet_awards_web_tipping_enabled": False,
        "freedom_of_speech_not_reach_fetch_enabled": True,
        "standardized_nudges_misinfo": True,
        "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": False,
        "longform_notetweets_rich_text_read_enabled": True,
        "longform_notetweets_inline_media_enabled": True,
        "responsive_web_enhance_cards_enabled": False,
    }))
    url = (f"https://api.twitter.com/graphql/V7H0Ap3_Hh2FyS75OCDO3Q/UserTweets"
           f"?variables={variables}&features={features}")
    req = Request(url, headers={
        "Authorization": f"Bearer {TW_BEARER}",
        "x-guest-token": guest_token,
        "User-Agent": "Mozilla/5.0",
    })
    with urlopen(req, timeout=15) as r:
        d = json.load(r)

    tweets = []
    instructions = (d.get("data", {}).get("user", {})
                     .get("result", {}).get("timeline_v2", {})
                     .get("timeline", {}).get("instructions", []))
    for inst in instructions:
        for entry in inst.get("entries", []):
            try:
                result = entry["content"]["itemContent"]["tweet_results"]["result"]
                legacy = result.get("legacy", {})
                text = legacy.get("full_text", "")
                created = legacy.get("created_at", "")
                tweet_id = legacy.get("id_str", "")
                if text and not text.startswith("RT @"):
                    tweets.append({"text": text, "created_at": created, "id": tweet_id})
            except (KeyError, TypeError):
                pass
    return tweets

def tw_parse_created(created_at_str):
    """Twitter日付文字列 → YYYY-MM-DD"""
    try:
        # "Fri Apr 25 10:00:00 +0000 2026"
        dt = datetime.strptime(created_at_str, "%a %b %d %H:%M:%S +0000 %Y")
        # 日本時間に変換（+9時間）
        from datetime import timedelta
        dt = dt + timedelta(hours=9)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None

def is_schedule_tweet(text):
    """スケジュール関連ツイートかどうか判定"""
    return any(kw in text for kw in SCHEDULE_KEYWORDS)

def extract_tweet_title(text):
    """ツイートから予定タイトルを生成"""
    # URLを除去
    text = re.sub(r'https?://\S+', '', text).strip()
    # ハッシュタグ・メンションを除去
    text = re.sub(r'[#＃@＠]\S+', '', text).strip()
    # 改行を空白に
    text = re.sub(r'\s+', ' ', text).strip()
    # 先頭80文字を使う
    return text[:80].strip()

def scrape_twitter():
    """Twitter/Xから森崎ウィン関連のスケジュールツイートを取得"""
    log("Twitter/X を取得中...")
    results = []
    try:
        guest_token = tw_get_guest_token()
        log(f"  ゲストトークン取得: {guest_token[:10]}...")
    except Exception as e:
        log(f"  [エラー] ゲストトークン取得失敗: {e}")
        return []

    for screen_name, user_id in TW_ACCOUNTS.items():
        log(f"  @{screen_name} を取得中...")
        try:
            # ユーザーIDが未設定の場合は取得
            if not user_id:
                user_id = tw_get_user_id(screen_name, guest_token)
                log(f"    user_id: {user_id}")

            tweets = tw_get_tweets(user_id, guest_token)
            log(f"    {len(tweets)} ツイート取得")

            for tw in tweets:
                text = tw["text"]
                if not is_schedule_tweet(text):
                    continue

                # ツイート投稿日をベースの日付にする
                post_date = tw_parse_created(tw["created_at"])
                if not post_date:
                    continue

                # ツイート内の日付を優先して使う
                start, end = extract_dates(text)
                date_start = start or post_date
                date_end   = end

                title = extract_tweet_title(text)
                if not title or len(title) < 5:
                    continue

                results.append({
                    "title": title,
                    "dateStart": date_start,
                    "dateEnd": date_end,
                    "venue": "",
                    "note": f"@{screen_name} より: {re.sub(r'https?://S+', '', text).strip()[:200]}",
                    "category": guess_category(text),
                    "_source": f"twitter_{screen_name}",
                    "_tweet_id": tw["id"],
                })

        except Exception as e:
            log(f"  [エラー] @{screen_name}: {e}")

    log(f"  → Twitter合計 {len(results)} 件候補")
    return results


# ─────────────────────────────────────────────
# メイン
# ─────────────────────────────────────────────
def main():
    log('=== 森崎ウィン スケジュール取得開始 ===')

    # 既存データ読み込み
    existing = []
    if os.path.exists(EVENTS_FILE):
        try:
            with open(EVENTS_FILE, encoding='utf-8') as f:
                data = json.load(f)
                existing = data.get('events', [])
            log(f'既存データ: {len(existing)} 件')
        except Exception as e:
            log(f'既存データ読み込みエラー: {e}')

    # スクレイピング
    scraped = []
    scraped += scrape_stardust()
    scraped += scrape_columbia_media()
    scraped += scrape_columbia_live()
    scraped += scrape_twitter()

    # フィルタ・重複除去
    scraped = filter_valid(scraped)
    new_items = [ev for ev in scraped if not is_duplicate(ev, existing)]

    # ID付与
    max_id = max((e.get('id', 0) for e in existing), default=0)
    for i, ev in enumerate(new_items, 1):
        ev['id'] = max_id + i
        ev.pop('_source', None)

    # マージ（既存 + 新規）
    merged = existing + new_items
    merged.sort(key=lambda e: e.get('dateStart', ''))

    # 保存
    out = {
        'updatedAt': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'events': merged,
    }
    with open(EVENTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    log(f'新規追加: {len(new_items)} 件 / 合計: {len(merged)} 件')
    log(f'保存先: {EVENTS_FILE}')
    if new_items:
        log('--- 追加された予定 ---')
        for ev in new_items:
            log(f'  [{ev["dateStart"]}] {ev["title"]}')
    log('=== 完了 ===')
    return len(new_items)

if __name__ == '__main__':
    n = main()
    sys.exit(0)
