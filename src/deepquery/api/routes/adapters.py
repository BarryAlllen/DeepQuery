from fastapi import APIRouter

from deepquery.cli_adapters import list_adapters

router = APIRouter()


@router.get("/adapters")
async def adapters() -> dict[str, list[str]]:
    return {"adapters": list_adapters()}
