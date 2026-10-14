import json
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from detector import LawDetector


class LawLink(BaseModel):
    law_id: int | None = None
    article: str | None = None
    point_article: str | None = None
    subpoint_article: str | None = None


class LinksResponse(BaseModel):
    links: list[LawLink]


class TextRequest(BaseModel):
    text: str = Field(max_length=100_000)


@asynccontextmanager
async def lifespan(app: FastAPI):
    path = Path(__file__).with_name("law_aliases.json")
    app.state.detector = LawDetector(json.loads(path.read_text(encoding="utf-8")))
    yield


app = FastAPI(title="Law Links Service", version="1.0.0", lifespan=lifespan)


@app.post("/detect", response_model=LinksResponse)
def get_law_links(data: TextRequest, request: Request):
    try:
        return {"links": request.app.state.detector.detect(data.text)}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/health")
def health_check():
    return {"status": "healthy"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8978)
