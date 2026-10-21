import logging
import os


def main() -> None:
    import uvicorn

    # Our own warnings (rejected requests, failed jobs, retries) next to uvicorn's access log.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")
    uvicorn.run(
        "homeprinter.app:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
    )
