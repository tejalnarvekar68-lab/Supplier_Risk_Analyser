from pydantic_settings import BaseSettings, SettingsConfigDict

'''
BaseSettings is a class that allows you to define application settings using Python attributes.

For example, in your Supplier Risk Analyser, you might need:
- Database URL
- API key
- Tavily API key
- Application environment (development or production)

'''


class Settings(BaseSettings):
    app_api_key: str = "replace-this-with-a-long-random-secret"
    database_url: str = "sqlite:///./supplier_risk.db"
    max_upload_mb: int = 15
    tavily_api_key: str | None = None
    research_enabled: bool = False
    log_level: str = "INFO"
    embedding_model:str = "replace-with-embedding-model"
    embedding_base_url:str = "replace-with-embedding-base-url"
    openrouter_api_key:str = "replace-with-openrouter-api-key"
    financial_extraction_model: str = "openai/gpt-4o-mini"
    #SettingsConfigDict - Controls how configuration is loaded; Ignores any other variable API available in the .env file
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

#Printing settings.app_Api_key gives the results
settings = Settings()


#Yes, you could use Pydantic's BaseModel, but BaseSettings is specifically designed for application configuration.
#BaseModel is used to Validate data; BaseSettings is used to Load and validate application settings
