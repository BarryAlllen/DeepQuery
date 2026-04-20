import uvicorn

from deepquery.config.settings import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "deepquery.api.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=settings.env == "dev",
    )


if __name__ == "__main__":
    main()
