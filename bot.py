import asyncio
import contextlib
import os
import signal
import sys
import time

import config
from config import IS_ALPHA
from database.manager import (
    init_databases,
    ECONOMY_DB_PATH,
    MODERATOR_DB_PATH,
    BACKUP_FOLDER_ID,
    backup_all_dbs_to_gdrive_env,
    restore_all_dbs_from_gdrive_env,
)
from logging_modules.custom_logger import get_logger

log = get_logger()

# Import the bot instance and necessary tasks from main.py
from main import bot, global_blacklist_check, kill_all_tasks

async def graceful_shutdown():
    log.info("Shutdown signal received — performing cleanup...")

    # Prevent new interactions (optional but good practice)
    bot._is_shutting_down = True

    # Let ongoing tasks wrap up
    await asyncio.sleep(1)

    # Final backup
    if not IS_ALPHA:
        try:
            log.info("Performing final database backup before shutdown...")
            await asyncio.wait_for(
                backup_all_dbs_to_gdrive_env(
                    [
                        (ECONOMY_DB_PATH, "economy.db"),
                        (MODERATOR_DB_PATH, "moderator.db")
                    ],
                    BACKUP_FOLDER_ID
                ),
                timeout=25  # must complete before Railway kills us
            )
            log.info("Backup completed successfully.")
        except asyncio.TimeoutError:
            log.critical("Backup timed out — Railway may have killed us mid-upload.")
        except Exception as e:
            log.critical(f"Backup failed: {e}")
    else:
        log.warning("Skipping final backup as this is an alpha version.")

    # Close shared HTTP session
    if getattr(bot, "http_session", None) and not bot.http_session.closed:
        await bot.http_session.close()
        log.network("Closed shared HTTP session")
        
    # Close bot connections
    await kill_all_tasks()
    with contextlib.suppress(Exception):
        await bot.close()

    log.info("Shutdown complete.")
    log.info("Flurazide says: Goodbye!")

async def main():
    # Attempt restore and surface any problem
    try:
        restored_ok = await restore_all_dbs_from_gdrive_env(BACKUP_FOLDER_ID,
            {
                "economy.db": ECONOMY_DB_PATH,
                "moderator.db": MODERATOR_DB_PATH,
            }
        )
        if restored_ok is False:
            log.warning("Drive restore returned False (no files restored or an error occurred).")
    except Exception:
        log.exception("Exception while restoring databases from Drive")

    # Initialize database tables
    await init_databases()

    async with bot:
        bot.tree.interaction_check = global_blacklist_check

        shutdown_signal = asyncio.get_event_loop().create_future()

        def _signal_handler():
            if not shutdown_signal.done():
                shutdown_signal.set_result(True)

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                # Windows: loop.add_signal_handler usually isn't implemented for SIGTERM
                log.warning(f"Cannot register signal handler for {sig!r}; falling back to default behaviour.")
            except Exception as e:
                log.exception(f"Failed to register signal handler for {sig!r}: {e}")

        bot_task = asyncio.create_task(bot.start(config.BOT_TOKEN))

        try:
            # Wait for our shutdown future, or bot failure
            done, pending = await asyncio.wait(
                [shutdown_signal, bot_task], 
                return_when=asyncio.FIRST_COMPLETED
            )
            if bot_task in done:
                exc = bot_task.exception()
                if exc:
                    log.critical(f"bot.start() crashed: {exc}", exc_info=exc)
                else:
                    log.warning("bot.start() task finished unexpectedly without exception.")
        except asyncio.CancelledError:
            log.info("Shutdown future was cancelled; initiating cleanup.")
        except KeyboardInterrupt:
            log.info("KeyboardInterrupt received; initiating cleanup.")
        finally:
            if not bot_task.done():
                bot_task.cancel()
                try:
                    await bot_task
                except asyncio.CancelledError:
                    pass
            try:
                await graceful_shutdown()
            except Exception as e:
                log.exception(f"Error during graceful shutdown: {e}", exc_info=True)
                sys.exit(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # User pressed Ctrl+C during the very last stage of closing the loop
        pass
    except Exception as e:
        log.critical(f"Fatal crash as {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Use a hard exit to avoid "Exception ignored while joining a thread" 
        # which happens on Windows when you double-ctrl-c
        os._exit(0)
