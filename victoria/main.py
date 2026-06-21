from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from victoria.config import settings
from victoria.core.memory import MemoryStore
from victoria.core.llm_router import LLMRouter
from victoria.core.conversation import ConversationManager
from victoria.core.semantic_memory import SemanticMemory
from victoria.tools import load_all_tools
from victoria.tools.registry import registry as tool_registry
from victoria.interfaces.api import router as api_router

STATIC_DIR = Path(__file__).parent / "static"

load_all_tools()

app = FastAPI(
    title="Victoria AI",
    description="Your personal AI assistant — brilliant, British, and never boring.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

memory = MemoryStore(db_path=settings.db_path)
semantic_memory = SemanticMemory(db_path=settings.chromadb_path)
llm_router = LLMRouter()
manager = ConversationManager(
    memory=memory,
    router=llm_router,
    tool_registry=tool_registry,
    semantic_memory=semantic_memory,
)

app.include_router(api_router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "name": settings.app_name,
        "tools": len(tool_registry),
        "semantic_memory": semantic_memory.available,
    }


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")
