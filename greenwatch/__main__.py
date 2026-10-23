import logging
from .web import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
create_app().run(host="127.0.0.1", port=8080, threaded=True, use_reloader=False)
