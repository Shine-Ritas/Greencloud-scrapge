# One process owns the scheduler. Threads handle the dashboard while a scrape runs.
bind = "0.0.0.0:8080"
workers = 1
threads = 4
worker_class = "gthread"
timeout = 90
graceful_timeout = 35
accesslog = "-"
errorlog = "-"
loglevel = "info"
preload_app = False
control_socket_disable = True
