from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn  # 新增

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "LangGraph Backend is running!"}

@app.post("/chat")
async def chat(query: str):
    return {"reply": f"Received: {query}"}

# 新增：让绿色三角形运行按钮生效的启动代码
if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)