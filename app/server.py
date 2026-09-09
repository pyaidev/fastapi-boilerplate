from uvicorn_worker import UvicornWorker


class SecureUvicornWorker(UvicornWorker):
    # Preserve the real direct peer; only application-configured peers can supply X-Real-IP.
    CONFIG_KWARGS = {"loop": "auto", "http": "auto", "proxy_headers": False}
