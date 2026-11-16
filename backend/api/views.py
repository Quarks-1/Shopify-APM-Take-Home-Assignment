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
            ],
        }
    )
