import os
import re
import json
import urllib.request
import urllib.parse
import yt_dlp
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

template_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'templates'))
app = Flask(__name__, template_folder=template_dir)
CORS(app)

_YT_RE = re.compile(r'(?:https?://)?(?:www\.)?(?:youtube\.com|youtu\.be)/')

# Standard quality options served when Cobalt handles YouTube
_COBALT_FORMATS = [
    {'format_id': 'cobalt_max',  'label': 'Max',        'type': 'video', 'ext': 'mp4', 'height': 9999, 'filesize': None, 'has_audio': True},
    {'format_id': 'cobalt_1080', 'label': '1080p',      'type': 'video', 'ext': 'mp4', 'height': 1080, 'filesize': None, 'has_audio': True},
    {'format_id': 'cobalt_720',  'label': '720p',       'type': 'video', 'ext': 'mp4', 'height': 720,  'filesize': None, 'has_audio': True},
    {'format_id': 'cobalt_360',  'label': '360p',       'type': 'video', 'ext': 'mp4', 'height': 360,  'filesize': None, 'has_audio': True},
    {'format_id': 'cobalt_audio','label': 'Audio Only', 'type': 'audio', 'ext': 'mp3', 'height': 0,    'filesize': None, 'has_audio': True},
]

_COBALT_QUALITY_MAP = {
    'cobalt_max':   ('max',  'auto'),
    'cobalt_1080':  ('1080', 'auto'),
    'cobalt_720':   ('720',  'auto'),
    'cobalt_360':   ('360',  'auto'),
    'cobalt_audio': ('max',  'audio'),
    'best':         ('max',  'auto'),
}

# Combined (audio+video) format selectors keyed by synthetic format_id.
# Each falls back progressively so portrait/unusual-resolution videos still work.
_COMBINED = 'best[vcodec!=none][acodec!=none][ext=mp4]/best[vcodec!=none][acodec!=none]/best'
_YDL_FORMAT_SELECTORS = {
    'ydl_max':   _COMBINED,
    'ydl_1080':  f'best[height<=1080][vcodec!=none][acodec!=none][ext=mp4]/best[width<=1080][vcodec!=none][acodec!=none][ext=mp4]/{_COMBINED}',
    'ydl_720':   f'best[height<=720][vcodec!=none][acodec!=none][ext=mp4]/best[width<=720][vcodec!=none][acodec!=none][ext=mp4]/{_COMBINED}',
    'ydl_360':   f'best[height<=360][vcodec!=none][acodec!=none][ext=mp4]/best[width<=360][vcodec!=none][acodec!=none][ext=mp4]/{_COMBINED}',
    'ydl_audio': 'bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio',
    'best':      _COMBINED,
}


def _is_youtube(url):
    return bool(_YT_RE.match(url))


def _cobalt_api_url():
    return os.environ.get('COBALT_API_URL', '').rstrip('/')


def _youtube_oembed(url):
    """Fetch title/thumbnail from YouTube oEmbed — no bot detection."""
    api = f'https://www.youtube.com/oembed?url={urllib.parse.quote(url)}&format=json'
    req = urllib.request.Request(api, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _cobalt_download(url, quality='max', mode='auto'):
    """Call the self-hosted Cobalt API and return its response dict."""
    base = _cobalt_api_url()
    payload = json.dumps({
        'url': url,
        'videoQuality': quality,
        'downloadMode': mode,
        'filenameStyle': 'pretty',
    }).encode()

    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    api_key = os.environ.get('COBALT_API_KEY', '')
    if api_key:
        headers['Authorization'] = f'Api-Key {api_key}'

    req = urllib.request.Request(f'{base}/', data=payload, headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _ydl_opts(fmt_selector):
    return {
        'quiet': True,
        'no_warnings': True,
        'format': fmt_selector,
        'extractor_args': {
            'youtube': {
                # ios_downgraded is the current least-blocked unauthenticated client
                'player_client': ['ios_downgraded', 'android_vr', 'web'],
            }
        },
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/info', methods=['POST'])
def get_info():
    try:
        data = request.get_json()
        url = (data or {}).get('url', '').strip()

        if not url:
            return jsonify({'error': 'URL is required'}), 400

        # YouTube + Cobalt configured → use oEmbed for metadata, no bot risk
        if _is_youtube(url) and _cobalt_api_url():
            try:
                meta = _youtube_oembed(url)
                return jsonify({
                    'title': meta.get('title', 'YouTube Video'),
                    'thumbnail': meta.get('thumbnail_url'),
                    'duration': None,
                    'platform': 'youtube',
                    'uploader': meta.get('author_name'),
                    'formats': _COBALT_FORMATS,
                })
            except Exception:
                pass  # fall through to yt-dlp

        # All other platforms (and YouTube fallback) — use yt-dlp
        with yt_dlp.YoutubeDL(_ydl_opts('best[ext=mp4]/best')) as ydl:
            info = ydl.extract_info(url, download=False)

        # Find the max height available (combined video+audio streams only)
        max_height = 0
        for fmt in info.get('formats') or ([info] if info.get('url') else []):
            h = fmt.get('height') or 0
            v = fmt.get('vcodec', 'none')
            a = fmt.get('acodec', 'none')
            if v not in ('none', None) and a not in ('none', None) and h > max_height:
                max_height = h

        # Build fixed quality options based on what's actually available
        formats = [{'format_id': 'ydl_max', 'label': 'Max', 'type': 'video',
                    'ext': 'mp4', 'height': max_height or 9999, 'filesize': None, 'has_audio': True}]
        for h, fid in [(1080, 'ydl_1080'), (720, 'ydl_720'), (360, 'ydl_360')]:
            if max_height >= h:
                formats.append({'format_id': fid, 'label': f'{h}p', 'type': 'video',
                                'ext': 'mp4', 'height': h, 'filesize': None, 'has_audio': True})
        formats.append({'format_id': 'ydl_audio', 'label': 'Audio Only', 'type': 'audio',
                        'ext': 'm4a', 'height': 0, 'filesize': None, 'has_audio': True})

        return jsonify({
            'title':    info.get('title', 'Unknown Video'),
            'thumbnail':info.get('thumbnail'),
            'duration': info.get('duration'),
            'platform': info.get('extractor_key', '').lower(),
            'uploader': info.get('uploader'),
            'formats':  formats,
        })

    except yt_dlp.utils.DownloadError as e:
        return jsonify({'error': str(e).replace('ERROR: ', '')}), 400
    except Exception as e:
        return jsonify({'error': f'Failed to fetch video info: {e}'}), 500


@app.route('/api/download-url', methods=['POST'])
def get_download_url():
    try:
        data = request.get_json()
        url       = (data or {}).get('url', '').strip()
        format_id = (data or {}).get('format_id', 'best')

        if not url:
            return jsonify({'error': 'URL is required'}), 400

        # YouTube + Cobalt configured → let Cobalt handle the download
        if _is_youtube(url) and _cobalt_api_url():
            quality, mode = _COBALT_QUALITY_MAP.get(format_id, ('max', 'auto'))
            result = _cobalt_download(url, quality=quality, mode=mode)

            status = result.get('status')
            if status in ('tunnel', 'redirect'):
                ext = 'mp3' if mode == 'audio' else 'mp4'
                return jsonify({
                    'url':      result['url'],
                    'filename': result.get('filename', f'video.{ext}'),
                    'ext':      ext,
                })
            elif status == 'picker':
                # Cobalt returned multiple items (e.g. playlist) — take the first
                items = result.get('items', [])
                if items:
                    return jsonify({
                        'url':      items[0]['url'],
                        'filename': items[0].get('filename', 'video.mp4'),
                        'ext':      'mp4',
                    })
            err_code = result.get('error', {}).get('code', 'unknown cobalt error')
            return jsonify({'error': err_code}), 400

        # Non-YouTube (or no Cobalt) — use yt-dlp with height-based combined selectors
        fmt_selector = _YDL_FORMAT_SELECTORS.get(format_id, _YDL_FORMAT_SELECTORS['best'])

        with yt_dlp.YoutubeDL(_ydl_opts(fmt_selector)) as ydl:
            info = ydl.extract_info(url, download=False)

        direct_url = info.get('url')
        if not direct_url:
            for fmt in info.get('formats', []):
                if fmt.get('url'):
                    direct_url = fmt['url']
                    break

        if not direct_url:
            return jsonify({'error': 'Could not extract direct download URL'}), 400

        safe_title = ''.join(c for c in info.get('title', 'video')
                             if c.isalnum() or c in ' -_()[]').strip()[:80]
        ext = info.get('ext', 'mp4')
        return jsonify({'url': direct_url, 'filename': f'{safe_title}.{ext}', 'ext': ext})

    except yt_dlp.utils.DownloadError as e:
        return jsonify({'error': str(e).replace('ERROR: ', '')}), 400
    except Exception as e:
        return jsonify({'error': f'Failed to get download URL: {e}'}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)
