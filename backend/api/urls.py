from django.urls import path

from . import views

app_name = 'api'

urlpatterns = [
    path('health/', views.health_check, name='health'),
    path('overview/', views.workspace_overview, name='overview'),
    path('spaces/', views.space_list, name='space-list'),
    path('spaces/<int:space_id>/objects/', views.space_objects, name='space-objects'),
    path('objects/<int:object_id>/', views.update_object, name='object-update'),
]
