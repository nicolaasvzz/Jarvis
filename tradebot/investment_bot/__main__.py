from .cli import main

# Guarded: tuning runs backtests in worker processes, which re-import this module.
if __name__ == "__main__":
    main()
