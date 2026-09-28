import uvicorn


def serve():
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
