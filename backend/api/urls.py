from django.urls import path

from . import cart_views, render_views, search_views, state_views, views

app_name = 'api'

urlpatterns = [
    path('health/', views.health_check, name='health'),
    path('overview/', views.workspace_overview, name='overview'),
    path('state/', state_views.state_snapshot, name='state'),
    path('state/import/', state_views.state_import, name='state-import'),
    path('spaces/', state_views.spaces_collection, name='spaces'),
    path('spaces/<str:space_id>/', state_views.space_detail, name='space-detail'),
    path(
        'spaces/<str:space_id>/placements/<str:object_id>/',
        state_views.placement_detail,
        name='placement-detail',
    ),
    path('objects/', state_views.objects_collection, name='objects'),
    path('objects/<str:object_id>/', state_views.object_detail, name='object-detail'),
    path('objects/<str:object_id>/searches/', search_views.object_searches, name='object-searches'),
    path(
        'objects/<str:object_id>/searches/latest/',
        search_views.object_search_latest,
        name='object-search-latest',
    ),
    path('objects/<str:object_id>/select/', search_views.object_select, name='object-select'),
    path('searches/<uuid:run_id>/', search_views.search_detail, name='search-detail'),
    path('searches/<uuid:run_id>/cancel/', search_views.search_cancel, name='search-cancel'),
    path('spaces/<str:space_id>/renders/', render_views.space_renders, name='space-renders'),
    path(
        'spaces/<str:space_id>/renders/latest/',
        render_views.space_render_latest,
        name='space-render-latest',
    ),
    path('renders/<uuid:render_id>/', render_views.render_detail, name='render-detail'),
    path('renders/<uuid:render_id>/image/', render_views.render_image, name='render-image'),
    path('cart/', cart_views.cart_root, name='cart'),
    path('cart/lines/', cart_views.cart_lines, name='cart-lines'),
    path('cart/lines/<str:line_id>/', cart_views.cart_line_detail, name='cart-line-detail'),
    path('cart/stores/<str:domain>/sync/', cart_views.store_sync, name='cart-store-sync'),
    path(
        'cart/stores/<str:domain>/checkout/',
        cart_views.store_checkout,
        name='cart-store-checkout',
    ),
]
