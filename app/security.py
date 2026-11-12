#This code creates an API key authentication mechanism for your FastAPI application.
#In our Supplier Risk Analyser, it ensures that only users who provide the correct API key can access protected endpoints.


#Provides security-focused functions, including secure comparison of secret values.
import secrets
#Header -Extracts values from HTTP request headers.
#HTTPException - Raises HTTP errors such as 401 and 503.
from fastapi import Header, HTTPException
#Imports application configuration, including the API key stored in your .env file.
#Comes from config.py file
from app.config import settings

def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    configured = settings.app_api_key
    if not configured or configured == "replace-this-with-a-long-random-secret":
        raise HTTPException(status_code=503, detail="Server API key is not configured securely")
    if not x_api_key or not secrets.compare_digest(x_api_key, configured):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
