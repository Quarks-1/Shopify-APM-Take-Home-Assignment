"""Endpoint for parsing a natural-language object description with Haiku."""
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .models import Space
from .object_parser import parser, prompts
from .serializers import ValidationFailed, _check_unknown, _dims, _id
from .state_views import _body, _json_view


def _validate_parse_request(data):
    if not isinstance(data, dict):
        raise ValidationFailed({'$': 'must be an object'})
    errors = {}
    _check_unknown(data, {'prompt', 'spaceIds', 'drawnDims'}, '', errors)

    raw_prompt = data.get('prompt')
    if not isinstance(raw_prompt, str):
        errors['prompt'] = 'must be a string'
        cleaned_prompt = ''
    else:
        cleaned_prompt = parser.clean_prompt(raw_prompt)
        if not cleaned_prompt:
            errors['prompt'] = 'must not be empty'

    space_ids = []
    if 'spaceIds' in data and data['spaceIds'] is not None:
        if not isinstance(data['spaceIds'], list):
            errors['spaceIds'] = 'must be a list of strings'
        else:
            seen = set()
            for i, sid in enumerate(data['spaceIds'][:20]):
                valid_id = _id(sid, f'spaceIds[{i}]', errors)
                if valid_id and valid_id not in seen:
                    seen.add(valid_id)
                    space_ids.append(valid_id)

    drawn_dims = None
    if 'drawnDims' in data and data['drawnDims'] is not None:
        drawn_dims = _dims(data['drawnDims'], 'drawnDims', errors, require_y=False)

    if errors:
        raise ValidationFailed(errors)
    if len(raw_prompt.strip()) > prompts.PROMPT_MAX_LEN:
        raise ValidationFailed({'prompt': f'must be at most {prompts.PROMPT_MAX_LEN} characters'})
    return cleaned_prompt, space_ids, drawn_dims


@_json_view
@require_http_methods(['POST'])
def parse_object_prompt(request):
    data, err = _body(request)
    if err:
        return err
    prompt_text, space_ids, drawn_dims = _validate_parse_request(data)
    spaces_by_id = {s.id: s for s in Space.objects.filter(pk__in=space_ids)}
    spaces = [spaces_by_id[sid] for sid in space_ids if sid in spaces_by_id]
    result = parser.parse_prompt(prompt_text, spaces=spaces, drawn_dims=drawn_dims)
    return JsonResponse(result)
