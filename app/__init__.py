import os

# python-telegram-bot: opt in to timedelta return types early (our code handles both)
os.environ.setdefault("PTB_TIMEDELTA", "1")
