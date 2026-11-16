from django.contrib import admin

from .models import Object, SimilarItem, Space


class SimilarItemInline(admin.TabularInline):
    model = SimilarItem
    extra = 0


@admin.register(Space)
class SpaceAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'space_type',
        'x',
        'y',
        'z',
        'dimension_unit',
        'editing_grid_increment_inches',
        'weight_limit',
    )
    list_filter = ('space_type', 'dimension_unit')


@admin.register(Object)
class ObjectAdmin(admin.ModelAdmin):
    list_display = ('id', 'object_type', 'material', 'style', 'weight', 'price_limit')
    list_filter = ('object_type', 'material', 'style')
    filter_horizontal = ('spaces',)
    inlines = [SimilarItemInline]


@admin.register(SimilarItem)
class SimilarItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'parent_object', 'image')