from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed


class SoftTokenAuthentication(TokenAuthentication):
    """
    Like TokenAuthentication, but a bad/expired token just means "anonymous" instead of a 401.
    Used on checkout so a stale token in the browser never blocks a guest order.
    """

    def authenticate(self, request):
        try:
            return super().authenticate(request)
        except AuthenticationFailed:
            return None
