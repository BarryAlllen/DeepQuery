from deepquery.config.settings import get_settings
from deepquery.services.query_service import QueryService


def get_query_service() -> QueryService:
    return QueryService(get_settings())
