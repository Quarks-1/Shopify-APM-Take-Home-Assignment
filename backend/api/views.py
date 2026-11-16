import json

import django
from django.conf import settings
from django.db import connection
from django.db.models import Count
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_safe

from .editing import serialize_object, serialize_space, update_object_from_payload
from .models import Object, Space


@require_safe
def health_check(_request):
    connection.ensure_connection()
    return JsonResponse(
        {
            'status': 'ok',
            'service': 'shopify-apm-backend',
            'framework': f'Django {django.get_version()}',
            'database': 'connected',
            'debug': settings.DEBUG,
            'timestamp': timezone.now().isoformat(),
        }
    )


@require_safe
def workspace_overview(_request):
    return JsonResponse(
        {
            'project': 'Shopify APM Workspace',
            'environment': 'development' if settings.DEBUG else 'production',
            'frontend': 'React + Vite + TypeScript + BoardUI',
            'backend': f'Django {django.get_version()}',
            'endpoints': [
                {
                    'id': 'health',
                    'method': 'GET',
                    'path': '/api/health/',
                    'description': 'Backend service and SQLite connectivity check',
                    'status': 'operational',
                },
                {
                    'id': 'overview',
                    'method': 'GET',
                    'path': '/api/overview/',
                    'description': 'Starter workspace metadata and registered API routes',
                    'status': 'operational',
                },
                {
                    'id': 'admin',
                    'method': 'GET',
                    'path': '/admin/',
                    'description': 'Django administration interface',
                    'status': 'operational',
                },
                {
                    'id': 'spaces',
                    'method': 'GET',
                    'path': '/api/spaces/',
                    'description': 'Spaces available in the editor',
                    'status': 'operational',
                },
                {
                    'id': 'space-objects',
                    'method': 'GET',
                    'path': '/api/spaces/<id>/objects/',
                    'description': 'Objects placed in one space',
                    'status': 'operational',
                },
                {
                    'id': 'object-update',
                    'method': 'PATCH',
                    'path': '/api/objects/<id>/',
                    'description': 'Update one object and its attributes',
                    'status': 'operational',
                },
            ],
        }
    )


@require_GET
def space_list(_request):
    spaces = Space.objects.annotate(object_count=Count('child_objects')).order_by('id')
    return JsonResponse(
        {
            'spaces': [
                serialize_space(space, object_count=space.object_count) for space in spaces
            ],
        }
    )


@require_GET
def space_objects(_request, space_id):
    try:
        space = Space.objects.get(pk=space_id)
    except Space.DoesNotExist:
        return JsonResponse({'error': 'Space not found.'}, status=404)
    objects = (
        Object.objects.filter(spaces=space)
        .prefetch_related('similar_items', 'spaces')
        .order_by('id')
    )
    parents = Space.objects.order_by('id')
    return JsonResponse(
        {
            'space': serialize_space(space),
            'objects': [serialize_object(obj) for obj in objects],
            'spaces': [serialize_space(item) for item in parents],
        }
    )


@csrf_exempt
@require_http_methods(['PATCH'])
def update_object(request, object_id):
    try:
        obj = Object.objects.prefetch_related('similar_items', 'spaces').get(pk=object_id)
    except Object.DoesNotExist:
        return JsonResponse({'error': 'Object not found.'}, status=404)
    try:
        payload = json.loads(request.body.decode() or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON.'}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({'error': 'Expected a JSON object.'}, status=400)
    errors = update_object_from_payload(obj, payload)
    if errors:
        return JsonResponse({'errors': errors}, status=400)
    obj = Object.objects.prefetch_related('similar_items', 'spaces').get(pk=obj.pk)
    return JsonResponse(serialize_object(obj))
