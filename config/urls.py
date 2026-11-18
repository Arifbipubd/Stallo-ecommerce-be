from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
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
