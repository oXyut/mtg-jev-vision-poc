"""Pure OpenAI Decisions document/answer adapters, checked 2026-10-24.

Reference: https://developers.openai.com/api/reference/resources/decisions/methods/create
Native image parts preserve supplied bytes. No credential or HTTP code is used.
"""
import base64
import io

from PIL import Image

from .geometry import probability
from .prompt_factorial import instructions

ENDPOINT = 'https://api.openai.com/v1/decisions'
MODEL = 'gpt-6-luna'
UNCACHED_INPUT_USD_PER_MILLION = .10


class UnsupportedTask(ValueError):
    pass


def require_task(task):
    if task not in ('presence', 'crop_verification'):
        raise UnsupportedTask('Native Decisions supports predicates/finite choices/scores, not bbox or open-name output')


def verification_document(image_bytes, condition, task='presence'):
    """Build a private request document without sending it.

    Whole-frame presence and externally supplied crop verification are separate
    diagnostics. Neither produces detections or arbitrary card names.
    """
    require_task(task)
    profile = instructions(condition)
    if not isinstance(image_bytes, bytes) or not 0 < len(image_bytes) <= 16 * 1024 * 1024:
        raise ValueError('Image bytes required, at most 16 MiB (local limit)')
    with Image.open(io.BytesIO(image_bytes)) as image:
        if image.format not in ('JPEG', 'PNG') or getattr(image, 'is_animated', False) or image.getexif():
            raise ValueError('Requires EXIF-free still JPEG/PNG')
        mime = 'image/jpeg' if image.format == 'JPEG' else 'image/png'
        image.verify()
    target = 'trading card' if condition.startswith('generic_') else 'Magic: The Gathering card'
    text = f'Is any {target} printing visible in photo?'
    if condition.endswith('_explicit'):
        from .prompt_factorial import ELIGIBILITY
        text += ' ' + ELIGIBILITY
    questions = [{'type': 'predicate', 'name': 'present',
                  'instructions': text if task == 'presence' else profile['crop_present']}]
    if task == 'crop_verification':
        questions.append({'type': 'predicate', 'name': 'single', 'instructions': profile['crop_single']})
    return {'model': MODEL, 'input': [{'role': 'user', 'content': [
        {'type': 'input_image', 'image_url': f'data:{mime};base64,' + base64.b64encode(image_bytes).decode(),
         'detail': 'original'}]}], 'questions': questions,
        # Explicit mode without breakpoints disables caching per the reference.
        'prompt_cache_options': {'mode': 'explicit'}}


def read_verification(response, task='presence'):
    """Parse native predicate/refusal answers; refuse Jev or invented Box shapes."""
    require_task(task)
    try:
        if response['model'] != MODEL:
            raise ValueError()
        answers = response['answers']
        expected = ['present'] if task == 'presence' else ['present', 'single']
        if not isinstance(answers, list) or len(answers) != len(expected):
            raise ValueError()
        result = {}
        for name, answer in zip(expected, answers):
            if answer.get('name') != name or answer.get('type') not in ('predicate', 'refusal'):
                raise ValueError()
            result[name] = {'status': 'refused' if answer['type'] == 'refusal' else 'answered',
                            'probability': None if answer['type'] == 'refusal' else probability(answer['probability'])}
        counts = read_usage(response)
        # Preserve cache counters for later accounting. Never price cache writes
        # or an unknown processing tier as uncached input or zero spend.
        return {'answers': result, 'usage': counts}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError('Invalid Decisions verification response; contents omitted') from None


def read_usage(response, strict=False):
    """Read usage independently so a malformed answer cannot erase known spend.

    The live transport requires the complete documented usage object. The pure
    adapter continues to accept the original offline interchange subset.
    """
    try:
        if response['model'] != MODEL:
            raise ValueError()
        usage = response['usage']
        counts = {key: usage[key] for key in ('input_tokens', 'output_tokens')}
        counts.update({key: usage['input_tokens_details'][key]
                       for key in ('cached_tokens', 'cache_write_tokens')})
        if any(type(value) is not int or value < 0 for value in counts.values()):
            raise ValueError()
        if counts['cached_tokens'] + counts['cache_write_tokens'] > counts['input_tokens']:
            raise ValueError()
        if strict:
            total = usage['total_tokens']
            reasoning = usage['output_tokens_details']['reasoning_tokens']
            if (type(total) is not int or total != counts['input_tokens'] + counts['output_tokens']
                    or type(reasoning) is not int or not 0 <= reasoning <= counts['output_tokens']):
                raise ValueError()
        return counts
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError('Invalid Decisions usage; contents omitted') from None
