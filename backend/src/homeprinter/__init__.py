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
        # /api when a reverse proxy serves the API under that prefix, so /docs works there.
        root_path=os.environ.get("ROOT_PATH", ""),
    )
