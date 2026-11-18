from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(['GET'])
def health(request):
    """Simple liveness check the storefront (or a monitor) can call."""
    return Response({'status': 'ok', 'service': 'stallo-backend'})
