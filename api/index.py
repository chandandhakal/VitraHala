import os
import re
import json
import math
import urllib.request
import urllib.parse
import yt_dlp
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

template_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'templates'))
app = Flask(__name__, template_folder=template_dir)
CORS(app)

_COBALT_FORMATS = [
    {'format_id': 'cobalt_max',   'label': 'Max',        'type': 'video', 'ext': 'mp4', 'height': 9999, 'filesize': None, 'has_audio': True},
    {'format_id': 'cobalt_1080',  'label': '1080p',      'type': 'video', 'ext': 'mp4', 'height': 1080, 'filesize': None, 'has_audio': True},
    {'format_id': 'cobalt_720',   'label': '720p',       'type': 'video', 'ext': 'mp4', 'height': 720,  'filesize': None, 'has_audio': True},
    {'format_id': 'cobalt_360',   'label': '360p',       'type': 'video', 'ext': 'mp4', 'height': 360,  'filesize': None, 'has_audio': True},
    {'format_id': 'cobalt_audio', 'label': 'Audio Only', 'type': 'audio', 'ext': 'mp3', 'height': 0,    'filesize': None, 'has_audio': True},
]

# Every platform a Cobalt instance can process — Cobalt is the fallback for all of them.
_COBALT_PLATFORMS = {
    'youtube', 'tiktok', 'twitter', 'instagram', 'reddit', 'facebook',
    'vimeo', 'twitch', 'soundcloud', 'snapchat', 'streamable', 'tumblr',
    'vk', 'bilibili', 'loom', 'bluesky', 'ok', 'rutube', 'xiaohongshu',
    'pinterest', 'dailymotion',
}

# Platforms that block datacenter IPs — go to Cobalt directly instead of wasting
# time on a yt-dlp attempt that will fail on Vercel.
_COBALT_FIRST = {'youtube', 'facebook', 'instagram', 'snapchat', 'xiaohongshu'}

_AUDIO_ONLY_PLATFORMS = {'soundcloud'}


def _fmt_quality(format_id):
    """Map any of our format ids (cobalt_*, ydl_*, tw_*, ...) to a Cobalt quality/mode pair."""
    if format_id.endswith('audio'):
        return 'max', 'audio'
    for q in ('1080', '720', '360'):
        if format_id.endswith(q):
            return q, 'auto'
    return 'max', 'auto'

_COMBINED = 'best[vcodec!=none][acodec!=none][ext=mp4]/best[vcodec!=none][acodec!=none]/best'
_YDL_FORMAT_SELECTORS = {
    'ydl_max':   _COMBINED,
    'ydl_1080':  f'best[height<=1080][vcodec!=none][acodec!=none][ext=mp4]/best[width<=1080][vcodec!=none][acodec!=none][ext=mp4]/{_COMBINED}',
    'ydl_720':   f'best[height<=720][vcodec!=none][acodec!=none][ext=mp4]/best[width<=720][vcodec!=none][acodec!=none][ext=mp4]/{_COMBINED}',
    'ydl_360':   f'best[height<=360][vcodec!=none][acodec!=none][ext=mp4]/best[width<=360][vcodec!=none][acodec!=none][ext=mp4]/{_COMBINED}',
    'ydl_audio': 'bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio',
    'best':      _COMBINED,
}


def _ydl_selector_for(format_id):
    """yt-dlp format selector for any of our format ids (ydl_*, cobalt_*, tw_*, ...)."""
    if format_id in _YDL_FORMAT_SELECTORS:
        return _YDL_FORMAT_SELECTORS[format_id]
    quality, mode = _fmt_quality(format_id)
    if mode == 'audio':
        return _YDL_FORMAT_SELECTORS['ydl_audio']
    return _YDL_FORMAT_SELECTORS.get(f'ydl_{quality}', _YDL_FORMAT_SELECTORS['best'])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _http_get(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _http_post(url, body, headers=None, timeout=20):
    req = urllib.request.Request(url, data=body, headers=headers or {}, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _safe_title(s, maxlen=80):
    return re.sub(r'[^\w\s\-()]', '', s or 'video').strip()[:maxlen]


def _cobalt_api_url():
    return os.environ.get('COBALT_API_URL', '').rstrip('/')


def _detect_platform(url):
    u = url.lower()
    pairs = [
        ('youtube.com',      'youtube'),
        ('youtu.be',         'youtube'),
        ('tiktok.com',       'tiktok'),
        ('vm.tiktok.com',    'tiktok'),
        ('twitter.com',      'twitter'),
        ('x.com',            'twitter'),
        ('instagram.com',    'instagram'),
        ('instagr.am',       'instagram'),
        ('reddit.com',       'reddit'),
        ('redd.it',          'reddit'),
        ('dailymotion.com',  'dailymotion'),
        ('dai.ly',           'dailymotion'),
        ('pinterest.com',    'pinterest'),
        ('pinterest.co.',    'pinterest'),
        ('pin.it',           'pinterest'),
        ('facebook.com',     'facebook'),
        ('fb.watch',         'facebook'),
        ('fb.com',           'facebook'),
        ('vimeo.com',        'vimeo'),
        ('twitch.tv',        'twitch'),
        ('soundcloud.com',   'soundcloud'),
        ('snapchat.com',     'snapchat'),
        ('streamable.com',   'streamable'),
        ('tumblr.com',       'tumblr'),
        ('vkvideo.ru',       'vk'),
        ('vk.com',           'vk'),
        ('bilibili.com',     'bilibili'),
        ('b23.tv',           'bilibili'),
        ('loom.com',         'loom'),
        ('bsky.app',         'bluesky'),
        ('ok.ru',            'ok'),
        ('rutube.ru',        'rutube'),
        ('xiaohongshu.com',  'xiaohongshu'),
        ('xhslink.com',      'xiaohongshu'),
        ('threads.net',      'threads'),
        ('threads.com',      'threads'),
        ('linkedin.com',     'linkedin'),
        ('rumble.com',       'rumble'),
        ('9gag.com',         '9gag'),
        ('imgur.com',        'imgur'),
        ('likee.video',      'likee'),
    ]
    for domain, key in pairs:
        if domain in u:
            return key
    return None


def _friendly_error(raw):
    msg = raw.replace('ERROR: ', '').strip()
    checks = [
        ('Account authentication is required', 'Login required — this platform needs you to be signed in.'),
        ('cookies',                             'Login required — please provide cookies for this platform.'),
        ('login required',                      'Login required — please sign in on the platform first.'),
        ('IP address is blocked',               'Your IP is blocked by this platform. Try using a VPN.'),
        ('Access forbidden',                    'Access denied by this platform.'),
        ('HTTP Error 403',                      'Access denied by this platform (403 Forbidden).'),
        ('HTTP Error 404',                      'Video not found — the URL may be private or deleted.'),
        ('Video unavailable',                   'This video is unavailable or has been removed.'),
        ('Private video',                       'This video is private.'),
        ('No video could be found',             'No video found in this URL.'),
        ('no video',                            'No video found in this URL.'),
        ('does not exist',                      'Video not found — it may have been deleted.'),
        ('Cannot parse data',                   'Unable to read data from this platform — login may be required.'),
        ('Unable to download JSON metadata',    'Could not fetch video metadata — the URL may be private or removed.'),
        ('Unsupported URL',                     'This URL is not supported.'),
        ('status code 0',                       'Could not reach this platform — it may be region-blocked.'),
        ('Bad guest token',                     'Twitter/X requires authentication.'),
        ('Error(s) while querying API',         'Twitter/X API error — try again or set TWITTER_BEARER_TOKEN.'),
        ('not available',                       'This video is not available in your region or has been removed.'),
        ('empty media response',                'Instagram requires login. Set INSTAGRAM_SESSIONID env var.'),
        ('[instagram]',                         'Instagram requires login. Set the INSTAGRAM_SESSIONID env var.'),
        ('[pinterest]',                         'Pinterest video not accessible. Try a direct video pin URL.'),
        ('No video formats found',              'No video found — this URL may be an image, not a video.'),
        ('WinError 10054',                      'Connection was reset by the server — the platform may be blocking automated access.'),
        ('Connection reset',                    'Connection was reset by the server — the platform may be blocking automated access.'),
        ('getaddrinfo failed',                  'Could not connect to this platform — check your internet connection.'),
    ]
    for needle, friendly in checks:
        if needle.lower() in msg.lower():
            return friendly
    if '; please report' in msg:
        msg = msg[:msg.index('; please report')]
    if 'See  https' in msg:
        msg = msg[:msg.index('See  https')].strip(' .')
    return msg


# ---------------------------------------------------------------------------
# YouTube / Cobalt
# ---------------------------------------------------------------------------

def _youtube_oembed(url):
    api = f'https://www.youtube.com/oembed?url={urllib.parse.quote(url)}&format=json'
    return _http_get(api, {'User-Agent': 'Mozilla/5.0'}, timeout=10)


def _cobalt_download(url, quality='max', mode='auto'):
    base = _cobalt_api_url()
    payload = json.dumps({'url': url, 'videoQuality': quality,
                          'downloadMode': mode, 'filenameStyle': 'pretty'}).encode()
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    api_key = os.environ.get('COBALT_API_KEY', '')
    if api_key:
        headers['Authorization'] = f'Api-Key {api_key}'
    req = urllib.request.Request(f'{base}/', data=payload, headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _cobalt_error_text(code):
    nice = {
        'error.api.link.invalid':                'This URL is not supported or is invalid.',
        'error.api.link.unsupported':            'This URL is not supported.',
        'error.api.fetch.fail':                  'Could not fetch this video — it may be private or deleted.',
        'error.api.fetch.empty':                 'No media found at this URL.',
        'error.api.content.too.long':            'This video is too long to process.',
        'error.api.service.disabled':            'This platform is disabled on the download server.',
        'error.api.content.video.unavailable':   'This video is unavailable — it may be private, deleted, or region-locked.',
        'error.api.content.video.private':       'This video is private.',
        'error.api.content.video.age':           'This video is age-restricted.',
        'error.api.content.post.unavailable':    'This post is unavailable — it may be private or deleted.',
        'error.api.content.post.private':        'This post is private.',
        'error.api.rate_exceeded':               'Too many requests — wait a moment and try again.',
    }
    if code in nice:
        return nice[code]
    return (code or 'Unknown download error').replace('error.api.', '').replace('.', ' ')


def _cobalt_resolve(url, format_id):
    """Run a URL through Cobalt and return {url, filename, ext} or raise ValueError."""
    quality, mode = _fmt_quality(format_id)
    result = _cobalt_download(url, quality=quality, mode=mode)
    status = result.get('status')
    ext = 'mp3' if mode == 'audio' else 'mp4'
    if status in ('tunnel', 'redirect'):
        return {'url': result['url'],
                'filename': result.get('filename', f'video.{ext}'),
                'ext': ext}
    if status == 'picker':
        items = result.get('items') or result.get('picker') or []
        videos = [i for i in items if i.get('type') in ('video', 'gif')] or items
        if videos:
            return {'url': videos[0]['url'],
                    'filename': videos[0].get('filename', 'video.mp4'),
                    'ext': 'mp4'}
        raise ValueError('No video found in this post (it may contain only images).')
    raise ValueError(_cobalt_error_text((result.get('error') or {}).get('code', '')))


def _noembed(url):
    """Best-effort title/thumbnail/author via noembed.com — never raises."""
    try:
        d = _http_get('https://noembed.com/embed?url=' + urllib.parse.quote(url),
                      {'User-Agent': 'Mozilla/5.0'}, timeout=8)
        if d.get('error'):
            return None, None, None
        return d.get('title'), d.get('thumbnail_url'), d.get('author_name')
    except Exception:
        return None, None, None


def _cobalt_generic_info(url, platform):
    """Info for any Cobalt-supported platform: validate the link with a real
    Cobalt request, then fill in metadata best-effort."""
    audio_only = platform in _AUDIO_ONLY_PLATFORMS
    probe = _cobalt_download(url, quality='max', mode='audio' if audio_only else 'auto')
    if probe.get('status') not in ('tunnel', 'redirect', 'picker'):
        raise ValueError(_cobalt_error_text((probe.get('error') or {}).get('code', '')))

    title, thumb, author = _noembed(url)
    name = (platform or 'video').title()
    formats = [f for f in _COBALT_FORMATS if f['type'] == 'audio'] if audio_only else _COBALT_FORMATS
    return {'title': title or probe.get('filename') or f'{name} Video',
            'thumbnail': thumb,
            'duration': None,
            'platform': platform,
            'uploader': author,
            'formats': formats}


# ---------------------------------------------------------------------------
# TikTok — via tikwm.com public API (no auth needed)
# ---------------------------------------------------------------------------

_TIKWM_BASE = 'https://www.tikwm.com'


def _tikwm_abs(u):
    """Convert tikwm.com relative path to absolute URL."""
    if u and u.startswith('/'):
        return _TIKWM_BASE + u
    return u or None


def _tikwm_fetch(url):
    body = urllib.parse.urlencode(
        {'url': url, 'count': 12, 'cursor': 0, 'web': 1, 'hd': 1}
    ).encode()
    data = _http_post('https://www.tikwm.com/api/', body, {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Referer': 'https://www.tikwm.com/',
        'Origin': 'https://www.tikwm.com',
    }, timeout=25)
    if data.get('code') != 0:
        msg = data.get('msg', 'TikTok error')
        if 'parsing' in msg.lower() or 'check url' in msg.lower():
            raise ValueError('TikTok video not found. Make sure the video is public and the URL is valid.')
        raise ValueError(f'TikTok: {msg}')
    return data['data']


def _tiktok_info(url):
    d = _tikwm_fetch(url)
    return {
        'title':     d.get('title') or 'TikTok Video',
        'thumbnail': _tikwm_abs(d.get('cover')),
        'duration':  d.get('duration'),
        'platform':  'tiktok',
        'uploader':  (d.get('author') or {}).get('nickname'),
        'formats': [
            {'format_id': 'tikwm_hd',    'label': 'HD',         'type': 'video', 'ext': 'mp4', 'height': 1080, 'filesize': None, 'has_audio': True},
            {'format_id': 'tikwm_sd',    'label': 'SD',         'type': 'video', 'ext': 'mp4', 'height': 480,  'filesize': None, 'has_audio': True},
            {'format_id': 'tikwm_audio', 'label': 'Audio Only', 'type': 'audio', 'ext': 'mp3', 'height': 0,    'filesize': None, 'has_audio': True},
        ],
    }


def _tiktok_download_url(url, format_id):
    d = _tikwm_fetch(url)
    url_map = {
        'tikwm_hd':    _tikwm_abs(d.get('hdplay') or d.get('play')),
        'tikwm_sd':    _tikwm_abs(d.get('play')),
        'tikwm_audio': _tikwm_abs(d.get('music')),
    }
    dl_url = url_map.get(format_id) or _tikwm_abs(d.get('hdplay') or d.get('play'))
    if not dl_url:
        raise ValueError('TikTok video URL unavailable — try again or use a shortened URL (vm.tiktok.com/...)')
    ext = 'mp3' if format_id == 'tikwm_audio' else 'mp4'
    return {'url': dl_url, 'filename': f"{_safe_title(d.get('title') or 'tiktok')}.{ext}", 'ext': ext}


# ---------------------------------------------------------------------------
# Twitter/X — via syndication API (no auth for public tweets)
# ---------------------------------------------------------------------------

def _twitter_token(tweet_id):
    """Replicate JS: (n/1e15*Math.PI).toString(36).replace('.','0').substr(0,7)"""
    n = abs(int(tweet_id) / 1e15 * math.pi)
    int_part = int(n)
    frac_part = n - int_part
    chars = '0123456789abcdefghijklmnopqrstuvwxyz'
    s = ''
    i = int_part
    while i:
        s = chars[i % 36] + s
        i //= 36
    s = (s or '0') + '.'
    for _ in range(10):
        frac_part *= 36
        d = int(frac_part)
        s += chars[d]
        frac_part -= d
    return s.replace('.', '0')[:7]


def _twitter_fetch(url):
    m = re.search(r'/status(?:es)?/(\d+)', url)
    if not m:
        raise ValueError('Could not extract tweet ID')
    tweet_id = m.group(1)
    token = _twitter_token(tweet_id)
    api = f'https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&lang=en&token={token}'
    return _http_get(api, {
        'User-Agent': 'Mozilla/5.0 (compatible; Twitterbot/1.0)',
        'Origin':  'https://platform.twitter.com',
        'Referer': 'https://platform.twitter.com/',
    }, timeout=15)


def _twitter_info(url):
    data = _twitter_fetch(url)
    variants = []
    thumbnail = None
    for media in data.get('mediaDetails', []):
        if media.get('type') in ('video', 'animated_gif'):
            thumbnail = media.get('media_url_https')
            mp4s = [v for v in media.get('video_info', {}).get('variants', [])
                    if v.get('content_type') == 'video/mp4']
            variants = sorted(mp4s, key=lambda v: v.get('bitrate', 0), reverse=True)
    if not variants:
        raise ValueError('No video found in this tweet')

    formats = []
    for i, v in enumerate(variants[:4]):
        qm = re.search(r'(\d+)x(\d+)', v['url'])
        height = int(qm.group(2)) if qm else max(1080 - i * 360, 240)
        label = f'{height}p' if qm else ('Best', 'Medium', 'Low', 'Lowest')[i]
        formats.append({'format_id': f'tw_{i}', 'label': label, 'type': 'video',
                        'ext': 'mp4', 'height': height, 'filesize': None, 'has_audio': True})
    formats.append({'format_id': 'tw_audio', 'label': 'Audio Only', 'type': 'audio',
                    'ext': 'mp4', 'height': 0, 'filesize': None, 'has_audio': True})
    return {
        'title':     (data.get('text') or 'Twitter/X Video')[:120],
        'thumbnail': thumbnail,
        'duration':  None,
        'platform':  'twitter',
        'uploader':  (data.get('user') or {}).get('name'),
        'formats':   formats,
        '_variants': [v['url'] for v in variants],
    }


def _twitter_download_url(url, format_id):
    data = _twitter_fetch(url)
    variants = []
    for media in data.get('mediaDetails', []):
        if media.get('type') in ('video', 'animated_gif'):
            mp4s = [v for v in media.get('video_info', {}).get('variants', [])
                    if v.get('content_type') == 'video/mp4']
            variants = sorted(mp4s, key=lambda v: v.get('bitrate', 0), reverse=True)
    if not variants:
        raise ValueError('No video found')
    if format_id == 'tw_audio':
        idx = len(variants) - 1  # lowest quality for audio-only extraction
    else:
        idx = int(format_id[3:]) if re.match(r'^tw_\d+$', format_id) else 0
    dl_url = variants[min(idx, len(variants) - 1)]['url']
    return {'url': dl_url, 'filename': 'twitter_video.mp4', 'ext': 'mp4'}


# ---------------------------------------------------------------------------
# Reddit — via public JSON API + v.redd.it CDN (no auth needed)
# ---------------------------------------------------------------------------

def _reddit_fetch(url):
    clean = re.sub(r'\?.*$', '', url.rstrip('/'))
    try:
        data = _http_get(clean + '/.json?raw_json=1&limit=1', {
            'User-Agent': 'Mozilla/5.0 (compatible; VideoFetcher/1.0)',
            'Accept': 'application/json',
        }, timeout=15)
    except Exception as e:
        msg = str(e)
        code = getattr(e, 'code', None)
        if code == 404 or '404' in msg:
            raise ValueError('Reddit post not found — the URL may be deleted or private.')
        # Cloudflare resets the connection (403/connection reset) for automated requests
        raise ValueError('Reddit is blocking automated access. Open the Reddit URL in your browser to watch the video.')
    post = data[0]['data']['children'][0]['data']

    def _get_rv(p):
        m = p.get('secure_media') or p.get('media') or {}
        return m.get('reddit_video')

    rv = _get_rv(post)
    if not rv:
        for xp in post.get('crosspost_parent_list', []):
            rv = _get_rv(xp)
            if rv:
                break
    return post, rv


def _reddit_info(url):
    post, rv = _reddit_fetch(url)
    if not rv:
        raise ValueError('No video in this Reddit post')

    thumb = post.get('thumbnail')
    if thumb in ('self', 'default', 'nsfw', '', None):
        try:
            thumb = post['preview']['images'][0]['source']['url']
        except Exception:
            thumb = None

    height = rv.get('height', 720)
    base = rv.get('fallback_url', '').split('?')[0].rsplit('/', 1)[0]

    formats = []
    for h in [1080, 720, 480, 360, 240]:
        if h <= height:
            formats.append({'format_id': f'reddit_{h}',
                            'label': 'Max' if h == height else f'{h}p',
                            'type': 'video', 'ext': 'mp4', 'height': h,
                            'filesize': None, 'has_audio': True})
    if not formats:
        formats = [{'format_id': 'reddit_best', 'label': 'Best', 'type': 'video',
                    'ext': 'mp4', 'height': height, 'filesize': None, 'has_audio': True}]
    formats.append({'format_id': 'reddit_audio', 'label': 'Audio Only', 'type': 'audio',
                    'ext': 'mp4', 'height': 0, 'filesize': None, 'has_audio': True})
    return {
        'title':     post.get('title', 'Reddit Video'),
        'thumbnail': thumb,
        'duration':  rv.get('duration'),
        'platform':  'reddit',
        'uploader':  f"r/{post.get('subreddit', '')}",
        'formats':   formats,
    }


def _reddit_download_url(url, format_id):
    post, rv = _reddit_fetch(url)
    if not rv:
        raise ValueError('No video in this Reddit post')

    base = rv.get('fallback_url', '').split('?')[0].rsplit('/', 1)[0]
    safe = _safe_title(post.get('title', 'reddit_video'))

    if format_id == 'reddit_audio':
        # Try DASH_audio first, fall back to audio
        for audio_path in ['DASH_audio.mp4', 'audio']:
            audio_url = f'{base}/{audio_path}'
            try:
                req = urllib.request.Request(audio_url, method='HEAD',
                                             headers={'User-Agent': 'Mozilla/5.0'})
                urllib.request.urlopen(req, timeout=5)
                return {'url': audio_url, 'filename': f'{safe}.mp4', 'ext': 'mp4'}
            except Exception:
                continue
        raise ValueError('No audio stream found for this Reddit video')

    h_match = re.search(r'reddit_(\d+)', format_id)
    h = h_match.group(1) if h_match else str(rv.get('height', 720))
    dl_url = f'{base}/DASH_{h}.mp4'
    return {'url': dl_url, 'filename': f'{safe}.mp4', 'ext': 'mp4'}


# ---------------------------------------------------------------------------
# Dailymotion — via player metadata API (bypasses geo-block on server IPs)
# ---------------------------------------------------------------------------

def _dm_fetch_player(vid_id):
    url = (f'https://www.dailymotion.com/player/metadata/video/{vid_id}'
           f'?embedder=https%3A%2F%2Fwww.dailymotion.com&locale=en_US&mstub=1')
    return _http_get(url, {
        'User-Agent': 'Mozilla/5.0',
        'Referer':    'https://www.dailymotion.com/',
        'Origin':     'https://www.dailymotion.com',
    }, timeout=20)


def _dailymotion_info(url):
    m = re.search(r'/video/([a-zA-Z0-9]+)', url)
    if not m:
        raise ValueError('Could not extract Dailymotion video ID')
    vid_id = m.group(1)

    meta = _http_get(
        f'https://api.dailymotion.com/video/{vid_id}?fields=title,thumbnail_url,duration',
        {'User-Agent': 'Mozilla/5.0'}, timeout=15)

    err = meta.get('error')
    if err:
        raise ValueError(f"Dailymotion: {err.get('message', 'video unavailable')}")

    player = _dm_fetch_player(vid_id)
    if player.get('error'):
        raise ValueError(f"Dailymotion: {player['error'].get('message', 'video unavailable')}")

    qualities = player.get('qualities', {})
    formats = []
    for qk in ['1080', '720', '480', '380', '240']:
        for stream in qualities.get(qk, []):
            su = stream.get('url', '')
            if stream.get('type') == 'video/mp4' or '.mp4' in su:
                formats.append({'format_id': f'dm_{qk}', 'label': f'{qk}p',
                                'type': 'video', 'ext': 'mp4', 'height': int(qk),
                                'filesize': None, 'has_audio': True})
                break

    if not formats:
        # HLS fallback — mark as mp4 but it's actually a stream URL
        for s in qualities.get('auto', []):
            if s.get('url'):
                formats = [{'format_id': 'dm_hls', 'label': 'Best', 'type': 'video',
                            'ext': 'mp4', 'height': 720, 'filesize': None, 'has_audio': True}]
                break

    if not formats:
        raise ValueError('No streams found — video may be private or region-locked')

    formats.append({'format_id': 'dm_audio', 'label': 'Audio Only', 'type': 'audio',
                    'ext': 'mp3', 'height': 0, 'filesize': None, 'has_audio': True})
    return {
        'title':     meta.get('title', 'Dailymotion Video'),
        'thumbnail': meta.get('thumbnail_url'),
        'duration':  meta.get('duration'),
        'platform':  'dailymotion',
        'uploader':  None,
        'formats':   formats,
    }


def _dailymotion_download_url(url, format_id):
    m = re.search(r'/video/([a-zA-Z0-9]+)', url)
    if not m:
        raise ValueError('No video ID')
    vid_id = m.group(1)
    player = _dm_fetch_player(vid_id)
    if player.get('error'):
        raise ValueError(player['error'].get('message', 'Dailymotion error'))
    qualities = player.get('qualities', {})

    meta = _http_get(f'https://api.dailymotion.com/video/{vid_id}?fields=title',
                     {'User-Agent': 'Mozilla/5.0'}, timeout=10)
    safe = _safe_title(meta.get('title', 'dailymotion'))

    qk = format_id.replace('dm_', '') if format_id.startswith('dm_') else 'auto'

    # audio: use lowest mp4 quality (browser will save as video, user extracts audio)
    if qk == 'audio':
        for quality_key in ['240', '380', '480', '720', '1080']:
            for stream in qualities.get(quality_key, []):
                if stream.get('url'):
                    return {'url': stream['url'], 'filename': f'{safe}.mp4', 'ext': 'mp4'}
        # fall through to HLS if no mp4

    # specific quality
    for stream in qualities.get(qk, []):
        su = stream.get('url', '')
        if su:
            ext = 'mp4' if (stream.get('type') == 'video/mp4' or '.mp4' in su) else 'mp4'
            return {'url': su, 'filename': f'{safe}.{ext}', 'ext': ext}

    # fallback: best mp4 quality
    for quality_key in ['1080', '720', '480', '380', '240']:
        for stream in qualities.get(quality_key, []):
            if stream.get('url'):
                return {'url': stream['url'], 'filename': f'{safe}.mp4', 'ext': 'mp4'}

    # last resort: HLS stream (browser/player can handle it)
    for stream in qualities.get('auto', []):
        if stream.get('url'):
            return {'url': stream['url'], 'filename': f'{safe}.mp4', 'ext': 'mp4'}

    raise ValueError('No downloadable URL found')


# ---------------------------------------------------------------------------
# Instagram — scrape og:video from public page (works for public reels/posts)
# ---------------------------------------------------------------------------

def _instagram_info(url):
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
                           'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 '
                           'Mobile/15E148 Safari/604.1'),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode('utf-8', errors='replace')
    except Exception:
        raise ValueError('Instagram requires login. Set the INSTAGRAM_SESSIONID env var to your session cookie.')

    def og(prop):
        for pat in [
            rf'<meta[^>]+property=["\']og:{prop}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:{prop}["\']',
        ]:
            hit = re.search(pat, html, re.IGNORECASE)
            if hit:
                return hit.group(1)
        return None

    video_url = og('video') or og('video:url') or og('video:secure_url')
    if not video_url:
        raise ValueError('Login required — Instagram requires you to be signed in. Set INSTAGRAM_SESSIONID env var.')

    return {
        'title':     og('title') or 'Instagram Video',
        'thumbnail': og('image') or og('image:url'),
        'duration':  None,
        'platform':  'instagram',
        'uploader':  None,
        'formats': [
            {'format_id': 'ig_best',  'label': 'Best',       'type': 'video', 'ext': 'mp4', 'height': 720, 'filesize': None, 'has_audio': True},
            {'format_id': 'ig_audio', 'label': 'Audio Only', 'type': 'audio', 'ext': 'mp4', 'height': 0,   'filesize': None, 'has_audio': True},
        ],
        '_video_url': video_url,
    }


def _instagram_download_url(url, format_id):
    info = _instagram_info(url)
    return {'url': info['_video_url'], 'filename': 'instagram_video.mp4', 'ext': 'mp4'}


# ---------------------------------------------------------------------------
# yt-dlp fallback
# ---------------------------------------------------------------------------

def _ydl_opts(fmt_selector, url=''):
    opts = {
        'quiet': True,
        'no_warnings': True,
        'format': fmt_selector,
        'extractor_retries': 3,
        'http_headers': {
            'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                           'AppleWebKit/537.36 (KHTML, like Gecko) '
                           'Chrome/125.0.0.0 Safari/537.36'),
            'Accept-Language': 'en-US,en;q=0.9',
        },
        'extractor_args': {
            'youtube': {'player_client': ['ios_downgraded', 'android_vr', 'web']},
        },
    }

    if 'twitter.com' in url or 'x.com' in url:
        bearer = os.environ.get('TWITTER_BEARER_TOKEN', '')
        opts['extractor_args']['twitter'] = (
            {'bearer_token': [bearer]} if bearer else {'legacy_api': ['1']}
        )

    if 'instagram.com' in url:
        sid = os.environ.get('INSTAGRAM_SESSIONID', '')
        if sid:
            opts['http_headers']['Cookie'] = f'sessionid={sid}'

    if 'facebook.com' in url or 'fb.watch' in url:
        fb = os.environ.get('FACEBOOK_COOKIE', '')
        if fb:
            opts['http_headers']['Cookie'] = fb

    if 'pinterest.com' in url or 'pin.it' in url:
        opts['format'] = 'best[ext=mp4]/best[vcodec!=none]/best'

    return opts


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/info', methods=['POST'])
def get_info():
    custom_error = None
    try:
        data = request.get_json()
        url = (data or {}).get('url', '').strip()
        if not url:
            return jsonify({'error': 'URL is required'}), 400

        platform = _detect_platform(url)
        cobalt_ok = bool(_cobalt_api_url()) and platform in _COBALT_PLATFORMS

        # YouTube: oEmbed metadata + Cobalt formats (yt-dlp is IP-blocked on Vercel)
        if platform == 'youtube' and _cobalt_api_url():
            try:
                meta = _youtube_oembed(url)
                return jsonify({'title': meta.get('title', 'YouTube Video'),
                                'thumbnail': meta.get('thumbnail_url'),
                                'duration': None, 'platform': 'youtube',
                                'uploader': meta.get('author_name'),
                                'formats': _COBALT_FORMATS})
            except Exception:
                pass

        # Custom platform handlers (fast, rich metadata, no Cobalt load)
        handlers = {
            'tiktok':      _tiktok_info,
            'twitter':     _twitter_info,
            'reddit':      _reddit_info,
            'dailymotion': _dailymotion_info,
            'instagram':   _instagram_info,
        }
        if platform in handlers:
            try:
                info = handlers[platform](url)
                return jsonify({k: v for k, v in info.items() if not k.startswith('_')})
            except Exception as e:
                # Preserve custom error when our message is more actionable than yt-dlp's.
                if platform in ('instagram', 'twitter', 'reddit'):
                    custom_error = str(e)

        # Cobalt for IP-blocked platforms, or when the custom handler just failed
        if cobalt_ok and (platform in _COBALT_FIRST or custom_error):
            try:
                return jsonify(_cobalt_generic_info(url, platform))
            except Exception:
                pass

        # yt-dlp for everything else
        try:
            with yt_dlp.YoutubeDL(_ydl_opts('best[ext=mp4]/best', url)) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception:
            # Last resort: Cobalt for any supported platform
            if cobalt_ok:
                try:
                    return jsonify(_cobalt_generic_info(url, platform))
                except Exception:
                    pass
            raise

        max_height = 0
        max_height_any = 0
        for fmt in info.get('formats') or ([info] if info.get('url') else []):
            h = fmt.get('height') or 0
            v = fmt.get('vcodec', 'none')
            a = fmt.get('acodec', 'none')
            has_video = v not in ('none', None)
            has_audio = a not in ('none', None)
            if has_video and has_audio and h > max_height:
                max_height = h
            if has_video and h > max_height_any:
                max_height_any = h
        if max_height == 0:
            max_height = max_height_any

        formats = [{'format_id': 'ydl_max', 'label': 'Max', 'type': 'video',
                    'ext': 'mp4', 'height': max_height or 9999, 'filesize': None, 'has_audio': True}]
        for h, fid in [(1080, 'ydl_1080'), (720, 'ydl_720'), (360, 'ydl_360')]:
            if max_height >= h:
                formats.append({'format_id': fid, 'label': f'{h}p', 'type': 'video',
                                'ext': 'mp4', 'height': h, 'filesize': None, 'has_audio': True})
        formats.append({'format_id': 'ydl_audio', 'label': 'Audio Only', 'type': 'audio',
                        'ext': 'm4a', 'height': 0, 'filesize': None, 'has_audio': True})

        return jsonify({'title':    info.get('title', 'Unknown Video'),
                        'thumbnail': info.get('thumbnail'),
                        'duration':  info.get('duration'),
                        'platform':  platform or info.get('extractor_key', '').lower(),
                        'uploader':  info.get('uploader'),
                        'formats':   formats})

    except yt_dlp.utils.DownloadError as e:
        return jsonify({'error': custom_error or _friendly_error(str(e))}), 400
    except Exception as e:
        return jsonify({'error': custom_error or f'Failed to fetch video info: {e}'}), 500


@app.route('/api/download-url', methods=['POST'])
def get_download_url():
    custom_error = None
    try:
        data = request.get_json()
        url       = (data or {}).get('url', '').strip()
        format_id = (data or {}).get('format_id', 'best')
        if not url:
            return jsonify({'error': 'URL is required'}), 400

        platform = _detect_platform(url)
        cobalt_ok = bool(_cobalt_api_url()) and platform in _COBALT_PLATFORMS

        # Custom handlers (direct CDN URLs — fast, no Cobalt load)
        dl_handlers = {
            'tiktok':      _tiktok_download_url,
            'twitter':     _twitter_download_url,
            'reddit':      _reddit_download_url,
            'dailymotion': _dailymotion_download_url,
            'instagram':   _instagram_download_url,
        }
        if platform in dl_handlers and (format_id.split('_')[0] in (
                'tikwm', 'tw', 'reddit', 'dm', 'ig') or format_id == 'dm_hls'):
            try:
                return jsonify(dl_handlers[platform](url, format_id))
            except Exception as e:
                if platform in ('instagram', 'twitter', 'reddit'):
                    custom_error = str(e)
                # fall through to Cobalt / yt-dlp

        # Cobalt: IP-blocked platforms (incl. YouTube), cobalt_* formats,
        # or any supported platform whose custom handler just failed
        if cobalt_ok and (platform in _COBALT_FIRST
                          or format_id.startswith('cobalt_') or custom_error):
            try:
                return jsonify(_cobalt_resolve(url, format_id))
            except Exception as e:
                if platform == 'youtube':
                    return jsonify({'error': str(e)}), 400
                custom_error = custom_error or str(e)
                # fall through to yt-dlp

        # yt-dlp fallback
        fmt_selector = _ydl_selector_for(format_id)
        try:
            with yt_dlp.YoutubeDL(_ydl_opts(fmt_selector, url)) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception:
            # Last resort: Cobalt for any supported platform
            if cobalt_ok:
                try:
                    return jsonify(_cobalt_resolve(url, format_id))
                except Exception:
                    pass
            raise

        direct_url = info.get('url')
        if not direct_url:
            fallback_url = None
            for fmt in reversed(info.get('formats', [])):
                u = fmt.get('url')
                if not u:
                    continue
                v = fmt.get('vcodec', 'none')
                a = fmt.get('acodec', 'none')
                if v not in ('none', None) and a not in ('none', None):
                    direct_url = u
                    break
                if fallback_url is None:
                    fallback_url = u
            if not direct_url:
                direct_url = fallback_url

        if not direct_url:
            return jsonify({'error': 'Could not extract direct download URL'}), 400

        safe = _safe_title(info.get('title', 'video'))
        ext  = info.get('ext', 'mp4')
        return jsonify({'url': direct_url, 'filename': f'{safe}.{ext}', 'ext': ext})

    except yt_dlp.utils.DownloadError as e:
        return jsonify({'error': custom_error or _friendly_error(str(e))}), 400
    except Exception as e:
        return jsonify({'error': custom_error or f'Failed to get download URL: {e}'}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)
