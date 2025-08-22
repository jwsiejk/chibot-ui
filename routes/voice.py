from flask import Blueprint, request, jsonify, current_app
import base64
from services.tts_service import tts_bytes, tts_with_visemes

voice_bp = Blueprint('voice', __name__, url_prefix='/api/voice')

@voice_bp.route('/tts', methods=['POST'])
def tts():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get('text') or data.get('message') or '').strip()
    fmt = (data.get('format') or 'mp3').lower()
    voice_id = data.get('voice') or data.get('voice_id')
    try:
        audio = tts_bytes(text=text, format=fmt, voice_id=voice_id)
        b64 = base64.b64encode(audio or b'').decode('ascii')
        return jsonify({'ok': True, 'audio_b64': b64, 'format': fmt})
    except Exception as e:
        current_app.logger.exception('voice.tts failed')
        return jsonify({'ok': False, 'error': 'tts_failed', 'detail': str(e)}), 500

@voice_bp.route('/tts_with_visemes', methods=['POST'])
def tts_with_visemes_route():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get('text') or data.get('message') or '').strip()
    fmt = (data.get('format') or 'mp3').lower()
    voice_id = data.get('voice') or data.get('voice_id')
    try:
        audio, visemes = tts_with_visemes(text=text, format=fmt, voice_id=voice_id)
        b64 = base64.b64encode(audio or b'').decode('ascii')
        return jsonify({'ok': True, 'audio_b64': b64, 'format': fmt, 'visemes': visemes or []})
    except Exception as e:
        current_app.logger.exception('voice.tts_with_visemes failed')
        return jsonify({'ok': False, 'error': 'tts_visemes_failed', 'detail': str(e)}), 500