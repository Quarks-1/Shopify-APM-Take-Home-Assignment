import django
from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_safe


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
                    'id': 'state',
                    'method': 'GET',
                    'path': '/api/state/',
                    'description': 'Full snapshot of spaces and objects',
                    'status': 'operational',
                },
                {
                    'id': 'state-import',
                    'method': 'POST',
                    'path': '/api/state/import/',
                    'description': 'One-time import of a frontend AppState (spaces and objects)',
                    'status': 'operational',
                },
                {
                    'id': 'spaces-list',
                    'method': 'GET',
                    'path': '/api/spaces/',
                    'description': 'List spaces',
                    'status': 'operational',
                },
                {
                    'id': 'spaces-create',
                    'method': 'POST',
                    'path': '/api/spaces/',
                    'description': 'Create a space',
                    'status': 'operational',
                },
                {
                    'id': 'space-get',
                    'method': 'GET',
                    'path': '/api/spaces/<id>/',
                    'description': 'Fetch a space',
                    'status': 'operational',
                },
                {
                    'id': 'space-update',
                    'method': 'PATCH',
                    'path': '/api/spaces/<id>/',
                    'description': 'Partially update a space',
                    'status': 'operational',
                },
                {
                    'id': 'space-delete',
                    'method': 'DELETE',
                    'path': '/api/spaces/<id>/',
                    'description': 'Delete a space and its placements',
                    'status': 'operational',
                },
                {
                    'id': 'placement-put',
                    'method': 'PUT',
                    'path': '/api/spaces/<id>/placements/<object_id>/',
                    'description': 'Create or move a placement',
                    'status': 'operational',
                },
                {
                    'id': 'placement-delete',
                    'method': 'DELETE',
                    'path': '/api/spaces/<id>/placements/<object_id>/',
                    'description': 'Remove a placement',
                    'status': 'operational',
                },
                {
                    'id': 'objects-list',
                    'method': 'GET',
                    'path': '/api/objects/',
                    'description': 'List objects',
                    'status': 'operational',
                },
                {
                    'id': 'objects-create',
                    'method': 'POST',
                    'path': '/api/objects/',
                    'description': 'Create an object',
                    'status': 'operational',
                },
                {
                    'id': 'object-get',
                    'method': 'GET',
                    'path': '/api/objects/<id>/',
                    'description': 'Fetch an object',
                    'status': 'operational',
                },
                {
                    'id': 'object-update',
                    'method': 'PATCH',
                    'path': '/api/objects/<id>/',
                    'description': 'Partially update an object',
                    'status': 'operational',
                },
                {
                    'id': 'object-delete',
                    'method': 'DELETE',
                    'path': '/api/objects/<id>/',
                    'description': 'Delete an object and its placements',
                    'status': 'operational',
                },
            ],
        }
    )
