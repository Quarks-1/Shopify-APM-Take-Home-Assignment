"""Product votes: thumbs up/down on the products shown for an object."""
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .models import Candidate, ProductVote, SpaceObject
from .serializers import ValidationFailed, _is_number, vote_product_snapshot, votes_by_object
from .state_views import _body, _json_view, _not_found


def _votes_response(object_id):
    return JsonResponse(
        {'objectId': object_id, 'votes': votes_by_object([object_id]).get(object_id, {})}
    )


def _parse(request, obj):
    """Validate `{candidateId, vote}` and return (candidate, vote or None)."""
    data, err = _body(request)
    if err:
        raise ValidationFailed({'$': 'invalid JSON'})
    if not isinstance(data, dict):
        raise ValidationFailed({'$': 'must be an object'})
    errors = {k: 'unknown key' for k in data if k not in ('candidateId', 'vote')}
    cid = data.get('candidateId')
    if not _is_number(cid) or int(cid) != cid:
        errors['candidateId'] = 'is required and must be an integer'
    if 'vote' not in data or data['vote'] not in ('up', 'down', None):
        errors['vote'] = 'is required and must be "up", "down" or null'
    if errors:
        raise ValidationFailed(errors)
    cand = Candidate.objects.filter(pk=int(cid), run__object_id=obj.pk).first()
    if cand is None:
        raise ValidationFailed({'candidateId': 'not a candidate of this object'})
    return cand, data['vote']


@_json_view
@require_http_methods(['GET', 'PUT'])
def object_votes(request, object_id):
    obj = SpaceObject.objects.filter(pk=object_id).first()
    if obj is None:
        return _not_found()
    if request.method == 'PUT':
        cand, vote = _parse(request, obj)
        if vote is None:
            ProductVote.objects.filter(object=obj, product_gid=cand.product_gid).delete()
        else:
            ProductVote.objects.update_or_create(
                object=obj,
                product_gid=cand.product_gid,
                defaults={
                    'vote': vote,
                    'candidate': cand,
                    'product': vote_product_snapshot(cand),
                },
            )
    return _votes_response(obj.pk)
