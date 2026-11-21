import os

import uvicorn


def main():
    uvicorn.run(
        "pcx_gateway.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "9210")),
        workers=1,
    )


if __name__ == "__main__":
    main()
