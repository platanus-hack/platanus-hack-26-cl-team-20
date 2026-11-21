import os

import uvicorn

from .config import Settings


def main():
    uvicorn.run(
        "pcx_gateway.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "9210")),
        workers=1,  # sockets de nodos y llamadas pendientes viven en este proceso
        ws_max_size=Settings.max_ws_frame,
        timeout_graceful_shutdown=20,  # Render espera hasta maxShutdownDelaySeconds (30 s)
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
