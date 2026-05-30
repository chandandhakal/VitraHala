import os
import json
import yt_dlp
from flask import Flask, request, jsonify, render_template, redirect
from flask_cors import CORS

template_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'templates'))
app = Flask(__name__, template_folder=template_dir)
CORS(app)


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

        # 'best[ext=mp4]/best' avoids ffmpeg-merge formats; gives us a processed info dict
        # with all sibling formats still accessible via info['formats']
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': 'best[ext=mp4]/best',
            'extractor_args': {
                'youtube': {
                    'player_client': ['tv_embedded', 'web_creator', 'android', 'ios', 'web'],
                }
            },
        }

        # Use a cookies file if provided (e.g. exported YouTube cookies for auth)
        cookies_file = os.environ.get('YOUTUBE_COOKIES_FILE')
        if cookies_file and os.path.isfile(cookies_file):
            ydl_opts['cookiefile'] = cookies_file

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = []
        seen_heights = set()
        seen_audio = False

        all_formats = info.get('formats') or ([info] if info.get('url') else [])

        for fmt in reversed(all_formats):
            vcodec = fmt.get('vcodec', 'none')
            acodec = fmt.get('acodec', 'none')
            ext = fmt.get('ext', 'mp4')
            height = fmt.get('height')
            fmt_url = fmt.get('url', '')

            if not fmt_url:
                continue

            if vcodec != 'none' and height and height not in seen_heights:
                seen_heights.add(height)
                filesize = fmt.get('filesize') or fmt.get('filesize_approx')
                formats.append({
                    'format_id': fmt['format_id'],
                    'label': f'{height}p',
                    'type': 'video',
                    'ext': ext if ext in ('mp4', 'webm', 'mov') else 'mp4',
                    'height': height,
                    'filesize': filesize,
                    'has_audio': acodec != 'none',
                })

            elif vcodec == 'none' and acodec != 'none' and not seen_audio:
                seen_audio = True
                filesize = fmt.get('filesize') or fmt.get('filesize_approx')
                formats.append({
                    'format_id': fmt['format_id'],
                    'label': 'Audio Only',
                    'type': 'audio',
                    'ext': 'm4a' if ext in ('m4a', 'mp4') else 'mp3',
                    'height': 0,
                    'filesize': filesize,
                    'has_audio': True,
                })

        formats.sort(key=lambda x: x['height'], reverse=True)

        # Fallback if no formats parsed
        if not formats:
            formats.append({
                'format_id': 'best',
                'label': 'Best Available',
                'type': 'video',
                'ext': 'mp4',
                'height': 0,
                'filesize': None,
                'has_audio': True,
            })

        return jsonify({
            'title': info.get('title', 'Unknown Video'),
            'thumbnail': info.get('thumbnail'),
            'duration': info.get('duration'),
            'platform': info.get('extractor_key', '').lower(),
            'uploader': info.get('uploader'),
            'formats': formats,
        })

    except yt_dlp.utils.DownloadError as e:
        msg = str(e).replace('ERROR: ', '')
        return jsonify({'error': msg}), 400
    except Exception as e:
        return jsonify({'error': f'Failed to fetch video info: {str(e)}'}), 500


@app.route('/api/download-url', methods=['POST'])
def get_download_url():
    try:
        data = request.get_json()
        url = (data or {}).get('url', '').strip()
        format_id = (data or {}).get('format_id', 'best')

        if not url:
            return jsonify({'error': 'URL is required'}), 400

        # Use format that doesn't need ffmpeg merging (single-file)
        if format_id == 'best':
            fmt_selector = 'best[ext=mp4]/best'
        else:
            fmt_selector = f'{format_id}/best[ext=mp4]/best'

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': fmt_selector,
            'extractor_args': {
                'youtube': {
                    'player_client': ['tv_embedded', 'web_creator', 'android', 'ios', 'web'],
                }
            },
        }

        cookies_file = os.environ.get('YOUTUBE_COOKIES_FILE')
        if cookies_file and os.path.isfile(cookies_file):
            ydl_opts['cookiefile'] = cookies_file

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        direct_url = info.get('url')

        # Try to find URL in formats if not at top level
        if not direct_url:
            for fmt in info.get('formats', []):
                if fmt.get('format_id') == format_id and fmt.get('url'):
                    direct_url = fmt['url']
                    break

        if not direct_url:
            return jsonify({'error': 'Could not extract direct download URL'}), 400

        title = info.get('title', 'video')
        # Sanitize filename
        safe_title = ''.join(c for c in title if c.isalnum() or c in ' -_()[]').strip()[:80]
        ext = info.get('ext', 'mp4')

        return jsonify({
            'url': direct_url,
            'filename': f'{safe_title}.{ext}',
            'ext': ext,
        })

    except yt_dlp.utils.DownloadError as e:
        msg = str(e).replace('ERROR: ', '')
        return jsonify({'error': msg}), 400
    except Exception as e:
        return jsonify({'error': f'Failed to get download URL: {str(e)}'}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)
