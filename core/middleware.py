from .models import AuditLog
class SecurityHeadersMiddleware:
    def __init__(self,get_response): self.get_response=get_response
    def __call__(self,request):
        response=self.get_response(request)
        response["Content-Security-Policy"]="default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self' https://api.openai.com https://graph.facebook.com"
        response["Permissions-Policy"]="camera=(), microphone=(), geolocation=()"
        return response
class AuditMiddleware:
    def __init__(self,get_response): self.get_response=get_response
    def __call__(self,request):
        response=self.get_response(request)
        if request.method in {"POST","PUT","PATCH","DELETE"} and request.user.is_authenticated:
            AuditLog.objects.create(user=request.user,event="http.mutation",path=request.path,method=request.method,ip_address=request.META.get("REMOTE_ADDR"),new_values={"status":response.status_code})
        return response

