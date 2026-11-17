import base64
import json
import logging
import socket
import time
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)


class ProviderNotConfigured(Exception):
    pass


class ProviderError(Exception):
    pass


def _b64(data):
    return base64.b64encode(data).decode('ascii')


def _short(text, limit=200):
    text = ' '.join(str(text).split())
    return text[:limit]


def parse_gemini_response(data):
    """Extract (image_bytes, mime) from a generateContent response dict."""
    if not isinstance(data, dict):
        raise ProviderError('unexpected response')
    candidates = data.get('candidates') or []
    if not candidates:
        block = (data.get('promptFeedback') or {}).get('blockReason')
        raise ProviderError(f'no candidates returned (blocked: {block})' if block
                            else 'no candidates returned')
    candidate = candidates[0] or {}
    parts = (candidate.get('content') or {}).get('parts') or []

    found = None
    texts = []
    for part in parts:
        if not isinstance(part, dict) or part.get('thought') is True:
            continue
        blob = part.get('inlineData') or part.get('inline_data')
        if blob and blob.get('data'):
            found = blob
        elif part.get('text'):
            texts.append(part['text'])

    if found is None:
        reason = (data.get('promptFeedback') or {}).get('blockReason') or candidate.get('finishReason')
        if reason:
            raise ProviderError(f'no image returned ({reason})')
        if texts:
            raise ProviderError(f'no image returned: {_short(texts[-1])}')
        raise ProviderError('no image returned')

    try:
        image = base64.b64decode(found['data'], validate=False)
    except (ValueError, TypeError) as exc:
        raise ProviderError('image data could not be decoded') from exc
    mime = found.get('mimeType') or found.get('mime_type') or 'image/png'
    return image, mime


def _gemini_error_message(http_error):
    try:
        payload = json.loads(http_error.read().decode('utf-8', 'replace'))
        message = (payload.get('error') or {}).get('message')
        if message:
            return _short(message, 300)
    except (ValueError, AttributeError):
        pass
    return _short(http_error.reason or 'request failed', 300)


def _generate_gemini(prompt, guide_png, photos, aspect):
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        raise ProviderNotConfigured('GEMINI_API_KEY is not set')

    parts = [
        {'text': prompt},
        {'inline_data': {'mime_type': 'image/png', 'data': _b64(guide_png)}},
    ]
    for photo, mime in photos:
        parts.append({'inline_data': {'mime_type': mime, 'data': _b64(photo)}})
    body = {
        'contents': [{'role': 'user', 'parts': parts}],
        'generationConfig': {
            'responseModalities': ['IMAGE'],
            'imageConfig': {
                'aspectRatio': aspect,
                'imageSize': settings.IMAGINE_IMAGE_SIZE,
            },
        },
    }
    url = f'{settings.IMAGINE_API_BASE.rstrip("/")}/models/{settings.IMAGINE_MODEL}:generateContent'
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode('utf-8'),
        headers={'x-goog-api-key': api_key, 'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.IMAGINE_TIMEOUT) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise ProviderError(f'Gemini HTTP {exc.code}: {_gemini_error_message(exc)}') from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
        reason = getattr(exc, 'reason', exc)
        raise ProviderError(f'Gemini request failed: {_short(reason, 200)}') from None

    try:
        data = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        raise ProviderError('Gemini returned invalid JSON') from None
    return parse_gemini_response(data)


def generate(prompt, guide_png, photos, aspect):
    """Return (image_bytes, mime, provider_name, model_name)."""
    provider = settings.IMAGINE_PROVIDER
    if provider == 'fake':
        time.sleep(settings.IMAGINE_FAKE_DELAY)
        return guide_png, 'image/png', 'fake', 'fake'
    if provider == 'gemini':
        image, mime = _generate_gemini(prompt, guide_png, photos, aspect)
        return image, mime, 'gemini', settings.IMAGINE_MODEL
    raise ProviderError(f'unknown IMAGINE_PROVIDER: {provider}')
