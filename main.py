import multiprocessing

from photo_report_app.ui import run


if __name__ == "__main__":
    multiprocessing.freeze_support()
    run()
