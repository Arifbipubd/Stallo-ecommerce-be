from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path, re_path
from django.views.static import serve
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView


def index(request):
    return JsonResponse({
        'service': 'Stallo API',
        'status': 'ok',
        'docs': '/api/docs/',
        'endpoints': {
            'health': '/api/health/',
            'menu': '/api/menu/',
            'outlets': '/api/outlets/',
            'orders (POST)': '/api/orders/',
            'admin': '/admin/',
        },
    })


urlpatterns = [
    path('', index),
    path('admin/', admin.site.urls),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    path('api/', include('core.urls')),
]

if not settings.USE_S3:
    # No bucket configured (local work): serve uploaded pictures from this server's disk.
    urlpatterns += [re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT})]
